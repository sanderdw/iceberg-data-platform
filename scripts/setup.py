"""Generate configuration for a new local Keycloak installation."""

import argparse
import json
import os
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


MCP_CLIENT_ID = "iceberg-mcp"
CLI_CLIENT_ID = "iceberg-cli"
DEFAULT_ROLES = "default-roles-iceberg"
# Extension tokens carry this audience, never Polaris's: the bridge accepts them, Polaris does not.
BRIDGE_AUDIENCE = "iceberg-bridge"
BRIDGE_URL = "http://bridge:3005"
PLATFORM_NETWORK = "iceberg-platform_default"
INTERNAL_ISSUER = "http://keycloak:8080/realms/iceberg"
EXTENSION_ID = re.compile(r"^[a-z][a-z0-9]{1,23}$")


def mapper(name, kind, config):
    return {"name": name, "protocol": "openid-connect", "protocolMapper": kind, "config": config}


def principal_mappers():
    """The platform user a token belongs to, from the Keycloak account link."""
    return [mapper(attribute, "oidc-usermodel-attribute-mapper", {
        "user.attribute": attribute, "claim.name": "polaris." + claim,
        "jsonType.label": kind, "access.token.claim": "true", "id.token.claim": "false",
    }) for attribute, claim, kind in [("polaris_id", "principal_id", "long"),
                                      ("polaris_name", "principal_name", "String")]]


def bridge_mappers():
    """Access-token claims for extension clients: the bridge audience and the user link, not Polaris's audience."""
    return [mapper("bridge-audience", "oidc-audience-mapper", {
        "included.custom.audience": BRIDGE_AUDIENCE, "access.token.claim": "true", "id.token.claim": "false",
    }), *principal_mappers()]


def polaris_mappers():
    """Access-token claims that let Polaris accept a Keycloak user token."""
    mappers = [mapper("polaris-audience", "oidc-audience-mapper", {
        "included.custom.audience": "polaris", "access.token.claim": "true", "id.token.claim": "false",
    }), *principal_mappers()]
    mappers.append(mapper("polaris-roles", "oidc-hardcoded-claim-mapper", {
        "claim.name": "polaris.roles", "claim.value": '["PRINCIPAL_ROLE:ALL"]',
        "jsonType.label": "JSON", "access.token.claim": "true", "id.token.claim": "false",
    }))
    return mappers


def mcp_client(callback_port):
    """Public client for MCP clients such as Codex, GitHub Copilot and Claude Code: PKCE, loopback redirects.

    Agents choose their own callback: Claude Code listens on this fixed port, VS Code on
    127.0.0.1:33418 or a random port, others on their own path. Keycloak retries an unmatched
    http loopback redirect without its port, so the two wildcard entries accept any local
    port and path (RFC 8252 section 7.3) and never a remote host. The bootstrap service
    reuses this definition to upgrade existing realms.
    """
    return {
        "clientId": MCP_CLIENT_ID, "name": "MCP clients", "enabled": True, "protocol": "openid-connect",
        "publicClient": True, "standardFlowEnabled": True, "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False,
        "redirectUris": [f"http://localhost:{int(callback_port)}/callback", "http://localhost/*", "http://127.0.0.1/*"],
        "webOrigins": [],
        "attributes": {"pkce.code.challenge.method": "S256"},
        "defaultClientScopes": ["basic", "profile", "roles"],
        # MCP clients request offline_access for a refresh token that outlives the browser SSO session.
        "optionalClientScopes": ["offline_access"], "protocolMappers": polaris_mappers(),
    }


def cli_client():
    """Public client for tools on the user's own machine: device login, no redirect.

    DuckDB and DBeaver hold a static bearer token, so this client alone issues
    tokens for one hour instead of the realm's 15 minutes. The bootstrap service
    reuses this definition to upgrade existing realms.
    """
    return {
        "clientId": CLI_CLIENT_ID, "name": "Tools on your computer", "enabled": True,
        "protocol": "openid-connect", "publicClient": True, "standardFlowEnabled": False,
        "implicitFlowEnabled": False, "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False,
        "redirectUris": [], "webOrigins": [],
        "attributes": {"oauth2.device.authorization.grant.enabled": "true", "access.token.lifespan": "3600"},
        "defaultClientScopes": ["basic", "profile", "roles"],
        # A refresh token that outlives the browser SSO session, like MCP clients.
        "optionalClientScopes": ["offline_access"], "protocolMappers": polaris_mappers(),
    }


def extension_client(extension, origin, secret):
    """Confidential client of an extension stack: user sign-in to its UI and its own service account.

    The bootstrap service creates it from PLATFORM_EXTENSIONS; the realm import never contains it.
    """
    return {
        "clientId": f"ext-{extension}", "name": f"Extension: {extension}", "enabled": True,
        "protocol": "openid-connect", "publicClient": False, "secret": secret,
        "standardFlowEnabled": True, "implicitFlowEnabled": False, "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": True,
        "redirectUris": [origin + "/auth/callback"], "webOrigins": [origin],
        "attributes": {"pkce.code.challenge.method": "S256", "post.logout.redirect.uris": origin + "/"},
        "defaultClientScopes": ["basic", "profile", "roles"], "optionalClientScopes": [],
        "protocolMappers": bridge_mappers(),
    }


def extension_mcp_client(extension, callback_port):
    """Public PKCE client through which AI agents use an extension's MCP server, like `mcp_client`."""
    return {
        **mcp_client(callback_port), "clientId": f"ext-{extension}-mcp", "name": f"Extension MCP: {extension}",
        "protocolMappers": bridge_mappers(),
    }


def parse_pairs(value):
    pairs = {}
    for item in filter(None, (part.strip() for part in (value or "").split(","))):
        key, _, rest = item.partition("=")
        pairs[key.strip()] = rest.strip()
    return pairs


def format_pairs(pairs):
    return ",".join(f"{key}={value}" for key, value in pairs.items())


def realm(env):
    clients = []
    for name, origin, secret in [("iceberg-admin", env["PORTAL_ORIGIN"], "PORTAL"),
                                 ("iceberg-users", env["USER_ORIGIN"], "USERS")]:
        clients.append({
            "clientId": name, "enabled": True, "protocol": "openid-connect", "publicClient": False,
            "secret": "${OIDC_" + secret + "_SECRET}", "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False,
            "redirectUris": [origin + "/auth/callback"],
            "webOrigins": [origin],
            "attributes": {"pkce.code.challenge.method": "S256",
                           "post.logout.redirect.uris": origin + "/"},
            "defaultClientScopes": ["basic", "profile", "roles"], "protocolMappers": polaris_mappers(),
        })
    clients.append(mcp_client(env["MCP_CALLBACK_PORT"]))
    clients.append(cli_client())
    users = [{
        "username": env["PLATFORM_ADMIN_USERNAME"], "enabled": True,
        "firstName": "Platform", "lastName": "Administrator",
        "email": "platform-admin@example.test", "emailVerified": True,
        "requiredActions": ["UPDATE_PASSWORD"],
        "credentials": [{"type": "password", "value": "${PLATFORM_ADMIN_PASSWORD}", "temporary": True}],
        # Imported users do not receive the realm default roles, which include offline_access.
        "realmRoles": [DEFAULT_ROLES],
        "clientRoles": {"iceberg-admin": ["platform-admin"]},
    }]
    return {
        "realm": "iceberg", "enabled": True, "registrationAllowed": False,
        "resetPasswordAllowed": False, "rememberMe": False, "accessTokenLifespan": 900,
        "ssoSessionIdleTimeout": 1800, "ssoSessionMaxLifespan": 28800,
        "roles": {"client": {"iceberg-admin": [{"name": "platform-admin"}]}},
        "clients": clients, "users": users,
    }


def read_env(path):
    # Read literal assignments generated by this tool; do not expand secret values.
    values = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
    return values


def set_env(config, updates):
    """Replace or append keys in an .env file without touching any other line."""
    lines = config.read_text().splitlines()
    seen = set()
    for index, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if key in updates and not key.startswith("#"):
            lines[index] = f"{key}={updates[key]}"
            seen.add(key)
    missing = [key for key in updates if key not in seen]
    if missing:
        lines += ["", "# Extension stacks (scripts.setup --extension)"] + [f"{k}={updates[k]}" for k in missing]
    config.write_text("\n".join(lines) + "\n")
    config.chmod(0o600)


def register_extension(root, extension, origin, handshake):
    """Register an extension stack and write the handshake file it starts with (contracts/bridge/v1/handshake.md)."""
    if not EXTENSION_ID.match(extension):
        raise SystemExit("An extension id is 2–24 lowercase letters or digits, starting with a letter.")
    if not re.match(r"^https?://[^/@?#]+/?$", origin):
        raise SystemExit("The extension origin is a scheme and host, such as http://localhost:3004.")
    config = root / ".env"
    values = read_env(config)
    registered = parse_pairs(values.get("PLATFORM_EXTENSIONS"))
    secrets_ = parse_pairs(values.get("PLATFORM_EXTENSION_SECRETS"))
    registered[extension] = origin.rstrip("/")
    secrets_.setdefault(extension, secrets.token_hex(24))
    set_env(config, {"PLATFORM_EXTENSIONS": format_pairs(registered),
                     "PLATFORM_EXTENSION_SECRETS": format_pairs(secrets_)})
    target = Path(handshake)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{key}={value}\n" for key, value in {
        "BRIDGE_CONTRACT": "1",
        "BRIDGE_URL": BRIDGE_URL,
        "BRIDGE_EXTENSION_ID": extension,
        "BRIDGE_CLIENT_ID": f"ext-{extension}",
        "BRIDGE_CLIENT_SECRET": secrets_[extension],
        "BRIDGE_MCP_CLIENT_ID": f"ext-{extension}-mcp",
        "OIDC_ISSUER": values["OIDC_ISSUER"],
        "OIDC_INTERNAL_ISSUER": INTERNAL_ISSUER,
        "EXTENSION_ORIGIN": registered[extension],
        "MCP_CALLBACK_PORT": values.get("MCP_CALLBACK_PORT", "3010"),
        "PLATFORM_NETWORK": PLATFORM_NETWORK,
    }.items())
    if target.exists():
        target.unlink()
    with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as file:
        file.write(content)
    print(f"Extension {extension} registered. Recreate the platform services to apply it:")
    print("  docker compose up -d --wait && docker compose -f compose.users.yaml up -d --wait users")
    print(f"The extension reads its bridge credentials from {target}.")


def remove_extension(root, extension):
    config = root / ".env"
    values = read_env(config)
    registered = parse_pairs(values.get("PLATFORM_EXTENSIONS"))
    secrets_ = parse_pairs(values.get("PLATFORM_EXTENSION_SECRETS"))
    registered.pop(extension, None)
    secrets_.pop(extension, None)
    set_env(config, {"PLATFORM_EXTENSIONS": format_pairs(registered),
                     "PLATFORM_EXTENSION_SECRETS": format_pairs(secrets_)})
    print(f"Extension {extension} removed. Recreating the platform services disables its Keycloak clients.")


def setup(root=ROOT, demo=False):
    config = root / ".env"
    template = (root / ".env.example").read_text()
    content = re.sub(r"replace-with-generated-(password|secret)", lambda _: secrets.token_hex(24), template)
    if not config.exists():
        with os.fdopen(os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as file:
            file.write(content)
    values = read_env(config)
    additions = {}

    def default(key, value):
        if key not in values:
            additions[key] = values[key] = value

    for key in ("KEYCLOAK_ADMIN_PASSWORD", "OIDC_PORTAL_SECRET", "OIDC_USERS_SECRET",
                "OIDC_MANAGEMENT_SECRET", "PLATFORM_ADMIN_PASSWORD"):
        default(key, secrets.token_hex(24))
    for key, value in {
        "PLATFORM_ADMIN_USERNAME": "platform-admin", "KEYCLOAK_PORT": "8080",
        "PORTAL_ORIGIN": f"http://localhost:{values.get('PORT', '3000')}",
        "USER_ORIGIN": f"http://localhost:{values.get('USER_PORT', '3002')}",
        "KEYCLOAK_ORIGIN": f"http://localhost:{values.get('KEYCLOAK_PORT', '8080')}",
        "MCP_CALLBACK_PORT": "3010",
    }.items():
        default(key, value)
    default("OIDC_ISSUER", values["KEYCLOAK_ORIGIN"] + "/realms/iceberg")
    default("OIDC_INTERNAL_ISSUER", values["OIDC_ISSUER"])
    default("KEYCLOAK_IMPORT_FILE", "./.local/keycloak/iceberg-realm.json")
    if demo:
        for account in ("admin", "writer", "reader", "outsider"):
            default(f"DEMO_{account.upper()}_PASSWORD", secrets.token_hex(24))
    if additions:
        with config.open("a") as file:
            file.write("\n# Keycloak identity and access management\n")
            file.writelines(f"{key}={value}\n" for key, value in additions.items())
    config.chmod(0o600)
    target = root / values["KEYCLOAK_IMPORT_FILE"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(realm(values), indent=2) + "\n")
    print("Keycloak configuration ready in .env.")
    print("Start compose.yaml (platform), then compose.users.yaml (workspaces).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Generate credentials for optional test fixtures")
    parser.add_argument("--extension", help="Register an extension stack, such as dbt")
    parser.add_argument("--origin", help="The extension's browser origin, such as http://localhost:3004")
    parser.add_argument("--handshake", help="Where to write the extension's bridge credentials (mode 0600)")
    parser.add_argument("--remove-extension", metavar="ID", help="Unregister an extension stack")
    args = parser.parse_args()
    setup(demo=args.demo)
    if args.extension:
        if not args.origin or not args.handshake:
            parser.error("--extension requires --origin and --handshake")
        register_extension(ROOT, args.extension, args.origin, args.handshake)
    if args.remove_extension:
        remove_extension(ROOT, args.remove_extension)


if __name__ == "__main__":
    main()
elif __name__ == "<run_path>":
    setup()
