"""User MCP tools: catalog reads and administrator-scoped database management."""

import time
from datetime import UTC, datetime
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from server.mcp_auth import call, current_access, mcp_server, run_locked
from server.models import (
    DatabaseId,
    DatabaseInput,
    DatabaseRename,
    Environment,
    ServiceError,
    ShareInput,
    ShareObject,
    ShareUpdate,
    TeamId,
)
from server.validation import validation_message

from .directory import UserSession, validate_namespace
from .preview import run_preview
from .semantic.compiler import Dimension, Filter, Order, QuerySpec
from .semantic.compiler import Name as MetricName

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
CREATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
UPDATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)
INSTRUCTIONS = """Iceberg Workspaces exposes the signed-in user's Apache Iceberg databases.
Start with list_databases: it returns the user's teams with their role per team and the
databases available through them, each with an opaque id (db-...), a display name, an
owning team and an environment (development, acceptance or production). Catalog browsing,
rename and delete tools take the database id, never its name; names repeat across environments. Namespaces are
lists of path parts such as ["analytics", "nested"]; tables and views are addressed by
database id, namespace list and name. Use list_namespaces to walk the tree, list_tables for
the tables and views in one namespace, describe_table and describe_view for schemas,
snapshots and view SQL, and preview_rows for up to 100 rendered rows at the current or a
given snapshot. list_semantic_models and describe_semantic_model return the Apache Ossie
semantic models stored next to the tables: datasets that map to tables, fields, relationships
and metrics with their SQL expressions, plus what each metric can be split by. Answer business
questions with query_semantic_model: name metrics, dimensions (DATASET.field, optional time grain)
and filters; it compiles the model's agreed SQL and reads with the user's own grants. State the
metric definition, filters and period with every answer, and use preview_rows only to look at raw
rows. All tools read with the user's Polaris grants; none runs free-form SQL.
Writers create a semantic model with publish_semantic_model. To replace one, read it with
describe_semantic_model first and pass its entityVersion; a newer version is refused. Ask the
user before replacing a model or calling delete_semantic_model, which needs confirm_name.
Only a current Administrator or Database + bucket administrator of a team may create,
rename or delete its databases. create_database takes the owning team id and environment;
rename_database and delete_database take the database id. Deletion immediately destroys
tables, views, stored files, the bucket and data shares, including in Production. It requires
confirm_name equal to the current display name and can be retried if cleanup stops partway.
Ask the user before a destructive call. list_databases also returns sharedDatabases: databases
of other teams shared with one of the user's teams. They are read-only, the catalog tools show
only their shared tables, views and semantic models, and they cannot be renamed or deleted.
Data shares give another team, an external party or both read access to selected tables,
views and semantic models of a database. list_shares shows a database's outgoing shares and
list_received_shares the shares received by the user's teams. Only a team administrator may
create_share, update_share, rotate_share_credential or delete_share, and list_share_teams
names the teams that can receive one. Views and semantic models share only their definition:
the recipient reads the underlying tables with the same credential, so select those tables
too. By default create_share and update_share add the tables that selected semantic models
read and report them in addedTables. create_share of an external share and
rotate_share_credential return a client secret once: hand it only to the intended recipient
and never store it in notebooks, files or chat history. Rotation invalidates the old secret
and delete_share revokes all access immediately, so ask the user first."""

Namespace = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=256)]],
    Field(max_length=100, description="Namespace path as a list of parts, for example ['analytics']."),
]
Name = Annotated[str, Field(min_length=1, max_length=256)]
ModelName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,256}$", description="Semantic model name: letters, digits, - and _.")]
QUERY_ROWS = 200
Limit = Annotated[int, Field(ge=1, le=100, description="Rows to return, at most 100.")]
Snapshot = Annotated[
    str | None, Field(pattern=r"^[0-9]{1,19}$", description="Snapshot id from describe_table; current when omitted.")
]
DatabaseName = Annotated[str, Field(description="3 to 48 lowercase letters, digits, hyphens or underscores, starting with a letter.")]
Description = Annotated[str, Field(description="Up to 280 characters.")]
ShareId = Annotated[str, Field(pattern=r"^share-[a-f0-9]{32}$", description="Share id (share-...) from list_shares.")]
ShareObjects = Annotated[
    list[ShareObject],
    Field(min_length=1, max_length=50, description="Tables, views and semantic models to share; include at least one table."),
]
Recipient = Annotated[str, Field(description="Who receives the share, for your records; up to 120 characters.")]
Expiry = Annotated[datetime | None, Field(description="ISO 8601 time at which access ends; never when omitted.")]
ModelTables = Annotated[
    bool, Field(description="Also select the tables that selected semantic models read, as the portal does.")
]


def validated(model, path, **fields):
    try:
        return model(**fields)
    except ValidationError as exc:
        raise ToolError(validation_message(exc.errors(), path)) from None


def session_for(access):
    """A per-request session; the token is the session and is never stored by the gateway."""
    until = time.monotonic() + max(0.0, access.expires_at - time.time())
    return UserSession(
        "mcp:" + access.subject, access.claims["polaris"]["principal_name"], "", "", "",
        access.token, until, until, "",
        oidc_subject=access.subject, oidc_issuer=access.claims["iss"], all_teams=True,
    )


def create_mcp(directory, oidc, *, lock, previews):
    mcp = mcp_server("iceberg-workspaces", oidc, title="Iceberg Workspaces", instructions=INSTRUCTIONS)

    async def query(fn, *args):
        # Directory reads share the gateway lock like every /api request.
        return await run_locked(lock, fn, *args)

    async def resolve(database=None, *, allow_deleting=False, shared=False):
        access = current_access()
        if not access.claims["polaris"]["principal_name"]:
            raise ToolError("Your Keycloak account is not linked to a platform user. Ask an administrator.")
        session = session_for(access)
        # Re-read the identity link, memberships and databases from Polaris on every call.
        profile = await query(directory.profile, session)
        if database is not None:
            available = profile["databases"] + (profile["deletingDatabases"] if allow_deleting else [])
            record = next((d for d in available if d["id"] == database), None)
            received = next((d for d in profile["sharedDatabases"] if d["id"] == database), None)
            if not record and received and not shared:
                raise ToolError("This database is shared with your team read-only. Only its owning team can change it.")
            if not record and received:
                # The recipient team selects the shared objects, as in the portal's Catalog.
                session.team, session.environment = received["sharedWithTeam"], received["environment"]
                return session, profile
            if not record:
                raise ToolError("This database is not available to you. Call list_databases first.")
            session.team, session.environment = record["team"], record["environment"]
        return session, profile

    def namespace_parts(namespace, *names):
        try:
            validate_namespace(namespace)
            validate_namespace(list(names))
        except ServiceError as exc:
            raise ToolError(str(exc)) from None
        return list(namespace)

    @mcp.tool(annotations=READ_ONLY)
    async def list_databases() -> dict[str, Any]:
        """The caller's teams with their role per team, the databases those teams own and read-only shared databases."""
        _, profile = await resolve()
        return {
            "user": profile["user"],
            "teams": [{"id": t["id"], "name": t["name"], "role": t["role"]} for t in profile["teams"]],
            "databases": [
                {k: d[k] for k in ("id", "name", "team", "environment", "description", "status")}
                for d in profile["databases"] + profile["deletingDatabases"]
            ],
            "sharedDatabases": [
                {**{k: d[k] for k in ("id", "name", "team", "ownerTeamName", "sharedWithTeam", "environment", "description")},
                 "shared": True, "readOnly": True}
                for d in profile["sharedDatabases"]
            ],
        }

    @mcp.tool(annotations=CREATE)
    async def create_database(
        team: TeamId, name: DatabaseName, environment: Environment = "development", description: Description = ""
    ) -> dict[str, Any]:
        """Create an Iceberg catalog and dedicated bucket for a team you administer."""
        data = validated(DatabaseInput, "/api/databases", team=team, name=name, environment=environment, description=description)
        session, _ = await resolve()
        session.team, session.environment = data.team, data.environment
        return await query(directory.create_database, session, data.name, data.description)

    @mcp.tool(annotations=UPDATE)
    async def rename_database(database: DatabaseId, name: DatabaseName) -> dict[str, Any]:
        """Rename a database of a team you administer; its id, bucket and grants stay the same."""
        data = validated(DatabaseRename, "/api/databases", name=name)
        session, _ = await resolve(database)
        return await query(directory.rename_database, session, database, data.name)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_database(database: DatabaseId, confirm_name: str) -> dict[str, Any]:
        """Permanently delete a database and its data; exact display name required, including on retry."""
        session, _ = await resolve(database, allow_deleting=True)
        return await query(directory.delete_database, session, database, confirm_name)

    @mcp.tool(annotations=READ_ONLY)
    async def list_namespaces(database: DatabaseId, namespace: Namespace = []) -> dict[str, Any]:  # noqa: B006
        """Child namespaces of a database or of one namespace; omit namespace for the top level."""
        parts = namespace_parts(namespace)
        session, _ = await resolve(database, shared=True)
        result = await query(directory.contents, session, database, parts)
        return {"database": database, "namespace": parts, "namespaces": result["namespaces"]}

    @mcp.tool(annotations=READ_ONLY)
    async def list_tables(database: DatabaseId, namespace: Namespace) -> dict[str, Any]:
        """Tables and views in one namespace."""
        parts = namespace_parts(namespace)
        if not parts:
            raise ToolError("Select a namespace.")
        session, _ = await resolve(database, shared=True)
        result = await query(directory.contents, session, database, parts)
        return {"database": database, "namespace": parts, "tables": result["tables"], "views": result["views"]}

    @mcp.tool(annotations=READ_ONLY)
    async def describe_table(database: DatabaseId, namespace: Namespace, table: Name) -> dict[str, Any]:
        """Schema, partitioning, sort order, branches and tags and the latest snapshots of a table."""
        parts = namespace_parts(namespace, table)
        if not parts:
            raise ToolError("Select a namespace.")
        session, _ = await resolve(database, shared=True)
        details = await query(directory.details, session, database, parts, "table", table)
        keep = (
            "name", "namespace", "uuid", "formatVersion", "location", "columns", "properties",
            "currentSnapshotId", "updatedAt", "refs", "partitionSpecs", "defaultSpecId",
            "sortOrders", "defaultSortOrderId",
        )
        return {
            **{k: details[k] for k in keep},
            "snapshotCount": len(details["snapshots"]),
            "snapshots": details["snapshots"][:10],
            "history": details["history"][:10],
        }

    @mcp.tool(annotations=READ_ONLY)
    async def describe_view(database: DatabaseId, namespace: Namespace, view: Name) -> dict[str, Any]:
        """Schema and the SQL of the latest versions of a view."""
        parts = namespace_parts(namespace, view)
        if not parts:
            raise ToolError("Select a namespace.")
        session, _ = await resolve(database, shared=True)
        details = await query(directory.details, session, database, parts, "view", view)
        keep = ("name", "namespace", "uuid", "formatVersion", "location", "columns", "properties", "currentVersionId")
        return {**{k: details[k] for k in keep}, "versions": details["versions"][:5]}

    @mcp.tool(annotations=READ_ONLY)
    async def list_semantic_models(database: DatabaseId, namespace: Namespace) -> dict[str, Any]:
        """Names of the semantic models in one namespace; empty when the feature is off or not granted."""
        parts = namespace_parts(namespace)
        if not parts:
            raise ToolError("Select a namespace.")
        session, _ = await resolve(database, shared=True)
        models = await query(directory.list_semantic_models, session, database, parts)
        return {"database": database, "namespace": parts, "semanticModels": models}

    @mcp.tool(annotations=READ_ONLY)
    async def describe_semantic_model(
        database: DatabaseId, namespace: Namespace, model: Name, include_definition: bool = False
    ) -> dict[str, Any]:
        """Datasets (with their source tables and fields), relationships and metrics of a semantic model,
        and what query_semantic_model can split each metric by.

        include_definition also returns the document exactly as Polaris stores it.
        """
        parts = namespace_parts(namespace, model)
        if not parts:
            raise ToolError("Select a namespace.")
        session, _ = await resolve(database, shared=True)
        details = await query(directory.details, session, database, parts, "semantic-model", model)
        keep = ("name", "namespace", "specVersion", "entityVersion", "models") + (("definition",) if include_definition else ())
        guide = await query(directory.semantic_query_guide, session, database, parts, model)
        return {**{k: details[k] for k in keep}, "queryable": guide}

    @mcp.tool(annotations=READ_ONLY)
    async def preview_rows(
        database: DatabaseId, namespace: Namespace, table: Name, limit: Limit = 20, snapshot_id: Snapshot = None
    ) -> dict[str, Any]:
        """Up to 100 rows of a table rendered as text, at the current or a given snapshot."""
        parts = namespace_parts(namespace, table)
        if not parts:
            raise ToolError("Select a namespace.")
        if previews.locked():
            raise ToolError("Preview slots are busy. Try again shortly.")
        async with previews:
            session, _ = await resolve(database, shared=True)
            prepared = await query(directory.preview_request, session, database, parts, table, snapshot_id, limit)
            # The sandboxed subprocess runs without the lock, like the portal preview.
            result = await call(run_preview, prepared)
            # Revocation while reading discards the result, as in the portal preview.
            await query(directory.details, session, database, parts, "table", table)
            return result

    @mcp.tool(annotations=READ_ONLY)
    async def query_semantic_model(
        database: DatabaseId, namespace: Namespace, model: Name,
        metrics: Annotated[list[MetricName], Field(min_length=1, max_length=5)],
        dimensions: Annotated[list[Dimension], Field(max_length=5)] = [],  # noqa: B006
        filters: Annotated[list[Filter], Field(max_length=10)] = [],  # noqa: B006
        order_by: Annotated[list[Order], Field(max_length=5)] = [],  # noqa: B006
        limit: Annotated[int, Field(ge=1, le=QUERY_ROWS)] = 100,
    ) -> dict[str, Any]:
        """Answer a business question with a semantic model's agreed metrics, split by dimensions
        (DATASET.field, optional time grain and via) and filtered, read with your own permissions."""
        parts = namespace_parts(namespace, model)
        if not parts:
            raise ToolError("Select a namespace.")
        try:
            spec = QuerySpec(metrics=metrics, dimensions=dimensions, filters=filters, order_by=order_by, limit=limit)
        except ValidationError as exc:
            raise ToolError(validation_message(exc.errors(), "query")) from None
        if previews.locked():
            raise ToolError("Query slots are busy. Try again shortly.")
        async with previews:
            session, _ = await resolve(database, shared=True)
            _, compiled, job = await query(directory.semantic_query_job, session, database, parts, model, spec,
                                           QUERY_ROWS)
            out = await call(run_preview, job, "user_portal.semantic.worker", "Query")
            # Revocation while reading discards the result, as for previews.
            await query(directory.load_semantic_model, session, database, parts, model)
        committed = (out.get("snapshots", {}).get(compiled.metrics[0]["dataset"]) or {}).get("committedAt")
        return {
            "model": {"database": database, "namespace": parts, "name": model},
            "columns": compiled.columns, "rows": out["rows"], "rowCount": len(out["rows"]),
            "truncated": out["truncated"], "metrics": compiled.metrics, "joinPaths": compiled.join_paths,
            "sql": compiled.pretty, "params": compiled.params,
            "dataAsOf": datetime.fromtimestamp(committed / 1000, UTC).isoformat(timespec="seconds") if committed else None,
        }

    @mcp.tool(annotations=UPDATE)
    async def publish_semantic_model(
        database: DatabaseId, namespace: Namespace, name: ModelName,
        model: Annotated[dict[str, Any], Field(description="One Apache Ossie semantic model, or a document that holds one.")],
        entity_version: Annotated[int | None, Field(description="entityVersion from describe_semantic_model; "
                                                                "required to replace an existing model.")] = None,
    ) -> dict[str, Any]:
        """Create a semantic model in a namespace, or replace the version you read (Writer role)."""
        parts = namespace_parts(namespace, name)
        if not parts:
            raise ToolError("Select a namespace.")
        session, _ = await resolve(database)
        return await query(directory.publish_semantic_model, session, database, parts, name, model, entity_version)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_semantic_model(
        database: DatabaseId, namespace: Namespace, name: ModelName, confirm_name: str
    ) -> dict[str, Any]:
        """Permanently delete a semantic model (Writer role); confirm_name must equal its name."""
        parts = namespace_parts(namespace, name)
        if not parts:
            raise ToolError("Select a namespace.")
        session, _ = await resolve(database)
        return await query(directory.delete_semantic_model, session, database, parts, name, confirm_name)

    async def owned_share(database, share):
        """A session for the share's owning database; the share must belong to it."""
        session, _ = await resolve(database)
        principal = await query(directory.share, session, share)
        if principal["properties"]["portal.database"] != database:
            raise ToolError("This share belongs to another database. Call list_shares first.")
        return session

    @mcp.tool(annotations=READ_ONLY)
    async def list_share_teams(database: DatabaseId) -> dict[str, Any]:
        """Teams that can receive a share of this database: every team other than its owner."""
        session, _ = await resolve(database)
        return {"database": database, "teams": await query(directory.share_teams, session)}

    @mcp.tool(annotations=READ_ONLY)
    async def list_shares(database: DatabaseId) -> dict[str, Any]:
        """Outgoing data shares of a database, with the objects each one grants; never secrets."""
        session, _ = await resolve(database)
        return await query(directory.outgoing_shares, session, database)

    @mcp.tool(annotations=READ_ONLY)
    async def list_received_shares() -> dict[str, Any]:
        """Data shares received by any of your teams, in every environment, with their objects."""
        session, _ = await resolve()
        return {"shares": await query(directory.received_shares, session)}

    @mcp.tool(annotations=CREATE)
    async def create_share(
        database: DatabaseId,
        name: Name,
        objects: ShareObjects,
        external: bool = True,
        recipient_team: TeamId | None = None,
        recipient: Recipient = "",
        description: Description = "",
        expires_at: Expiry = None,
        include_model_tables: ModelTables = True,
    ) -> dict[str, Any]:
        """Share selected objects of a database you administer with another team, externally, or both.

        An external share returns its connection details and client secret once.
        """
        data = validated(
            ShareInput, "/api/shares", database=database, name=name, objects=[o.model_dump() for o in objects],
            external=external, recipientTeam=recipient_team, recipient=recipient, description=description,
            expiresAt=expires_at,
        )
        session, _ = await resolve(database)
        return await query(lambda: directory.create_share(session, data, include_model_tables=include_model_tables))

    @mcp.tool(annotations=UPDATE)
    async def update_share(
        database: DatabaseId,
        share: ShareId,
        objects: ShareObjects | None = None,
        recipient: Recipient | None = None,
        description: Description | None = None,
        expires_at: Expiry = None,
        remove_expiry: bool = False,
        include_model_tables: ModelTables = True,
    ) -> dict[str, Any]:
        """Change a share's objects, recipient, description or expiry; omitted fields stay as they are."""
        if expires_at and remove_expiry:
            raise ToolError("Give expires_at or remove_expiry, not both.")
        fields = {"recipient": recipient, "description": description, "expiresAt": expires_at}
        fields = {k: v for k, v in fields.items() if v is not None}
        if objects is not None:
            fields["objects"] = [o.model_dump() for o in objects]
        if remove_expiry:
            fields["expiresAt"] = None
        if not fields:
            raise ToolError("Nothing to change.")
        data = validated(ShareUpdate, "/api/shares", **fields)
        session = await owned_share(database, share)
        return await query(lambda: directory.update_share(session, share, data, include_model_tables=include_model_tables))

    @mcp.tool(annotations=DESTRUCTIVE)
    async def rotate_share_credential(database: DatabaseId, share: ShareId) -> dict[str, Any]:
        """Issue a new client secret for an external share; the old secret stops working at once."""
        session = await owned_share(database, share)
        return await query(directory.rotate_share, session, share)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_share(database: DatabaseId, share: ShareId, confirm_name: str) -> dict[str, Any]:
        """Revoke a share: all access ends immediately. confirm_name must equal the share's name."""
        session = await owned_share(database, share)
        return await query(directory.delete_share, session, share, confirm_name)

    return mcp
