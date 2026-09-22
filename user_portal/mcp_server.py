"""User MCP tools: catalog reads and administrator-scoped database management."""

import time
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from server.mcp_auth import call, current_access, mcp_server, run_locked
from server.models import DatabaseId, DatabaseInput, DatabaseRename, Environment, ServiceError, TeamId
from server.validation import validation_message

from .directory import UserSession, validate_namespace
from .preview import run_preview

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
given snapshot. These catalog tools read with the user's Polaris grants and never run SQL.
Only a current Administrator or Database + bucket administrator of a team may create,
rename or delete its databases. create_database takes the owning team id and environment;
rename_database and delete_database take the database id. Deletion immediately destroys
tables, views, stored files, the bucket and data shares, including in Production. It requires
confirm_name equal to the current display name and can be retried if cleanup stops partway.
Ask the user before a destructive call."""

Namespace = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=256)]],
    Field(max_length=100, description="Namespace path as a list of parts, for example ['analytics']."),
]
Name = Annotated[str, Field(min_length=1, max_length=256)]
Limit = Annotated[int, Field(ge=1, le=100, description="Rows to return, at most 100.")]
Snapshot = Annotated[
    str | None, Field(pattern=r"^[0-9]{1,19}$", description="Snapshot id from describe_table; current when omitted.")
]
DatabaseName = Annotated[str, Field(description="3 to 48 lowercase letters, digits, hyphens or underscores, starting with a letter.")]
Description = Annotated[str, Field(description="Up to 280 characters.")]


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
        oidc_subject=access.subject, oidc_issuer=access.claims["iss"],
    )


def create_mcp(directory, oidc, *, lock, previews):
    mcp = mcp_server("iceberg-workspaces", oidc, title="Iceberg Workspaces", instructions=INSTRUCTIONS)

    async def query(fn, *args):
        # Directory reads share the gateway lock like every /api request.
        return await run_locked(lock, fn, *args)

    async def resolve(database=None, *, allow_deleting=False):
        access = current_access()
        if not access.claims["polaris"]["principal_name"]:
            raise ToolError("Your Keycloak account is not linked to a platform user. Ask an administrator.")
        session = session_for(access)
        # Re-read the identity link, memberships and databases from Polaris on every call.
        profile = await query(directory.profile, session)
        if database is not None:
            available = profile["databases"] + (profile["deletingDatabases"] if allow_deleting else [])
            record = next((d for d in available if d["id"] == database), None)
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
        """The caller's teams with their role per team, and the databases those teams own."""
        _, profile = await resolve()
        return {
            "user": profile["user"],
            "teams": [{"id": t["id"], "name": t["name"], "role": t["role"]} for t in profile["teams"]],
            "databases": [
                {k: d[k] for k in ("id", "name", "team", "environment", "description", "status")}
                for d in profile["databases"] + profile["deletingDatabases"]
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
        session, _ = await resolve(database)
        result = await query(directory.contents, session, database, parts)
        return {"database": database, "namespace": parts, "namespaces": result["namespaces"]}

    @mcp.tool(annotations=READ_ONLY)
    async def list_tables(database: DatabaseId, namespace: Namespace) -> dict[str, Any]:
        """Tables and views in one namespace."""
        parts = namespace_parts(namespace)
        if not parts:
            raise ToolError("Select a namespace.")
        session, _ = await resolve(database)
        result = await query(directory.contents, session, database, parts)
        return {"database": database, "namespace": parts, "tables": result["tables"], "views": result["views"]}

    @mcp.tool(annotations=READ_ONLY)
    async def describe_table(database: DatabaseId, namespace: Namespace, table: Name) -> dict[str, Any]:
        """Schema, partitioning, sort order, branches and tags and the latest snapshots of a table."""
        parts = namespace_parts(namespace, table)
        if not parts:
            raise ToolError("Select a namespace.")
        session, _ = await resolve(database)
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
        session, _ = await resolve(database)
        details = await query(directory.details, session, database, parts, "view", view)
        keep = ("name", "namespace", "uuid", "formatVersion", "location", "columns", "properties", "currentVersionId")
        return {**{k: details[k] for k in keep}, "versions": details["versions"][:5]}

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
            session, _ = await resolve(database)
            prepared = await query(directory.preview_request, session, database, parts, table, snapshot_id, limit)
            # The sandboxed subprocess runs without the lock, like the portal preview.
            result = await call(run_preview, prepared)
            # Revocation while reading discards the result, as in the portal preview.
            await query(directory.details, session, database, parts, "table", table)
            return result

    return mcp
