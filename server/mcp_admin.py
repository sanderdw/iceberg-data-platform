"""Platform administration as MCP tools: the admin API's operations for platform administrators."""

from functools import partial
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from .identity import CreateIdentity, IdentityMembership, LinkIdentity
from .mcp_auth import call, current_access, mcp_server, run_locked
from .models import (
    DatabaseId,
    DatabaseInput,
    DatabaseRename,
    Environment,
    Membership,
    Memberships,
    ShareId,
    TeamId,
    TeamInput,
    identifier_part,
)
from .validation import validation_message

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
CREATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
UPDATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
MOVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)
INSTRUCTIONS = """Iceberg Workspace Administration manages the teams, databases, users and data shares of
the platform; every tool requires the Keycloak platform-admin role. Start with get_overview or the
list_* tools. Objects have opaque ids (team-..., db-..., portal-..., share-...) and display names;
every other tool takes the id. Database names repeat across environments (development, acceptance,
production) and are unique per team and environment. Roles per team are reader, writer and admin;
bucket-admin (direct S3 access) is not available to Keycloak users. create_user creates a Keycloak
account and returns a temporary password exactly once: pass it to the person over a secure channel
and never store or repeat it. delete_team, delete_database, delete_user and revoke_share are
irreversible. delete_database destroys every table, view, storage object and data share of that
database and requires confirm_name; delete_user revokes platform access but keeps the Keycloak
account. Ask the administrator before any destructive call. browse_catalog lists namespaces,
tables and views by name only."""

# Free-text parameters stay unconstrained here and are validated by the API's own models,
# whose messages name the field without echoing the submitted value.
UserId = Annotated[str, Field(pattern=r"^portal-[a-f0-9]{32}$", description="User id from list_users.")]
ObjectName = Annotated[
    str, Field(description="3 to 48 lowercase letters, digits, hyphens or underscores, starting with a letter.")
]
Description = Annotated[str, Field(description="Up to 280 characters.")]
Username = Annotated[str, Field(description="Exact Keycloak username.")]
Namespace = Annotated[list[str], Field(description="Namespace path as a list of parts, for example ['analytics'].")]
MembershipList = Annotated[list[Membership], Field(description="One entry per team with the role there.")]
IdentityMemberships = Annotated[
    list[IdentityMembership], Field(description="One entry per team: reader, writer or admin.")
]


def validated(model, path, **fields):
    """Apply the API's own input rules; messages name the field and never echo values."""
    try:
        return model(**fields)
    except ValidationError as exc:
        raise ToolError(validation_message(exc.errors(), path)) from None


def catalog_parts(database, namespace):
    try:
        identifier_part(database)
        parts = [identifier_part(part) for part in namespace]
    except ValueError:
        raise ToolError("Invalid database or namespace.") from None
    if not database or len(database) > 256 or len(parts) > 100 or any(not p or len(p) > 256 for p in parts):
        raise ToolError("Invalid database or namespace.")
    return parts


def create_admin_mcp(provider, oidc, *, lock, user_management, overview, users, update_user):
    mcp = mcp_server("iceberg-admin", oidc, title="Iceberg Workspace Administration", instructions=INSTRUCTIONS)

    def require_admin():
        # The role is read from the verified token on every call, as the portal does on renewal.
        if "platform-admin" not in current_access().claims["roles"]:
            raise ToolError("Platform administrator access is required.")

    async def query(fn, *args):
        require_admin()
        return await run_locked(lock, fn, *args)

    @mcp.tool(annotations=READ_ONLY)
    async def get_overview() -> dict[str, Any]:
        """Provider health with every team, database, user and data share."""
        return await query(overview)

    @mcp.tool(annotations=READ_ONLY)
    async def list_teams() -> dict[str, Any]:
        """All teams with their stable ids."""
        return {"teams": await query(provider.list_teams)}

    @mcp.tool(annotations=CREATE)
    async def create_team(name: ObjectName, description: Description = "") -> dict[str, Any]:
        """Create a team; the name must be lowercase letters, digits, hyphens or underscores."""
        data = validated(TeamInput, "/api/teams", name=name, description=description)
        return await query(provider.save_team, data)

    @mcp.tool(annotations=UPDATE)
    async def update_team(team: TeamId, name: ObjectName, description: Description = "") -> dict[str, Any]:
        """Rename or describe a team; its id and memberships stay the same."""
        data = validated(TeamInput, "/api/teams", name=name, description=description)
        return await query(provider.save_team, data, team)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_team(team: TeamId) -> dict[str, Any]:
        """Delete a team without databases; refused while it is any user's last team."""
        await query(provider.delete_team, team)
        return {"deleted": True, "team": team}

    @mcp.tool(annotations=READ_ONLY)
    async def list_databases() -> dict[str, Any]:
        """All databases with their id, name, team, environment and status."""
        return {"databases": await query(provider.list_databases)}

    @mcp.tool(annotations=CREATE)
    async def create_database(
        name: ObjectName, team: TeamId, environment: Environment = "development", description: Description = ""
    ) -> dict[str, Any]:
        """Create an Iceberg database (catalog and bucket) owned by a team in one environment."""
        data = validated(
            DatabaseInput, "/api/databases", name=name, team=team, environment=environment, description=description
        )
        return await query(provider.create_database, data)

    @mcp.tool(annotations=UPDATE)
    async def rename_database(database: DatabaseId, name: ObjectName) -> dict[str, Any]:
        """Change a database's display name; its catalog id, bucket and grants stay the same."""
        data = validated(DatabaseRename, "/api/databases", name=name)
        return await query(provider.rename_database, database, data.name)

    @mcp.tool(annotations=MOVE)
    async def move_database(database: DatabaseId, team: TeamId) -> dict[str, Any]:
        """Move a database to another team; members of the old team lose access at once."""
        return await query(provider.move_database, database, team)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_database(database: DatabaseId, confirm_name: str) -> dict[str, Any]:
        """Irreversibly delete a database with all tables, views, stored data and data shares.

        confirm_name must equal the database's display name from list_databases; nothing is
        deleted otherwise. Call again with the same arguments to resume an interrupted deletion.
        """
        require_admin()
        async with lock:
            databases = await call(provider.list_databases)
            record = next((d for d in databases if d["id"] == database), None)
            if record is None:
                raise ToolError("Database not found. Nothing was deleted.")
            if record["name"] != confirm_name:
                raise ToolError("confirm_name does not match the database's display name. Nothing was deleted.")
            await call(partial(provider.delete_database, database, expected_name=confirm_name))
        return {"deleted": True, "database": database, "name": record["name"], "environment": record["environment"]}

    @mcp.tool(annotations=READ_ONLY)
    async def get_database_connection(database: DatabaseId) -> dict[str, Any]:
        """Iceberg REST connection settings of a database; credentials are never included."""
        return await query(provider.connection, database)

    @mcp.tool(annotations=READ_ONLY)
    async def browse_catalog(database: str | None = None, namespace: Namespace = []) -> dict[str, Any]:  # noqa: B006
        """List every Polaris catalog, or the namespaces, tables and views inside one, by name only."""
        if database is None:
            return {"databases": await query(provider.explorer_databases)}
        return await query(provider.explorer_contents, database, catalog_parts(database, namespace))

    @mcp.tool(annotations=READ_ONLY)
    async def list_users() -> dict[str, Any]:
        """All platform users with their memberships and identity status."""
        return {"users": await query(users)}

    @mcp.tool(annotations=UPDATE)
    async def update_user_access(user: UserId, memberships: MembershipList) -> dict[str, Any]:
        """Replace a user's teams and the role per team."""
        data = validated(Memberships, "/api/users", memberships=[m.model_dump() for m in memberships])
        return await query(update_user, user, data)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_user(user: UserId) -> dict[str, Any]:
        """Revoke a user's platform access and data grants; a linked Keycloak account remains."""
        if user_management:
            await query(user_management.revoke, provider, user)
        else:
            await query(provider.delete_user, user)
        return {"deleted": True, "user": user}

    @mcp.tool(annotations=READ_ONLY)
    async def list_shares() -> dict[str, Any]:
        """Every data share with its database, objects, expiry and creator."""
        return {"shares": await query(provider.list_shares)}

    @mcp.tool(annotations=DESTRUCTIVE)
    async def revoke_share(share: ShareId) -> dict[str, Any]:
        """End an external party's access immediately."""
        await query(provider.delete_share, share)
        return {"revoked": True, "share": share}

    if user_management:

        @mcp.tool(annotations=READ_ONLY)
        async def find_accounts(username: Username) -> dict[str, Any]:
            """Keycloak accounts with exactly this username and whether each is already linked."""
            if not username.strip() or len(username) > 254:
                raise ToolError("Username is required.")
            return {"accounts": await query(user_management.accounts, username)}

        @mcp.tool(annotations=CREATE)
        async def create_user(
            name: ObjectName, memberships: IdentityMemberships, email: str, first_name: str, last_name: str
        ) -> dict[str, Any]:
            """Create a Keycloak account and a platform user with teams and roles.

            The result's identity.temporaryPassword is shown exactly once and stored nowhere:
            hand it to the person over a secure channel; they change it at first sign-in.
            """
            data = validated(
                CreateIdentity, "/api/identity/users", name=name,
                memberships=[m.model_dump() for m in memberships],
                email=email, first_name=first_name, last_name=last_name,
            )
            return await query(user_management.create, provider, data, "new")

        @mcp.tool(annotations=CREATE)
        async def link_user(username: Username, name: ObjectName, memberships: IdentityMemberships) -> dict[str, Any]:
            """Give an existing Keycloak account platform access as a new user; its password stays."""
            if not username.strip() or len(username) > 254:
                raise ToolError("Username is required.")
            accounts = await query(user_management.accounts, username)
            if len(accounts) != 1:
                raise ToolError("No single Keycloak account has exactly that username. Use find_accounts.")
            if accounts[0]["linked"]:
                raise ToolError("This Keycloak account is already linked to a platform user.")
            data = validated(
                LinkIdentity, "/api/identity/links", name=name,
                memberships=[m.model_dump() for m in memberships], subject=accounts[0]["id"],
            )
            return await query(user_management.create, provider, data, "link")

        @mcp.tool(annotations=UPDATE)
        async def retry_user_setup(user: UserId) -> dict[str, Any]:
            """Finish an interrupted account setup; a new account may get another one-time password."""
            return await query(user_management.resume, provider, user)

    return mcp
