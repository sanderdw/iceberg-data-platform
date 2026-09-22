"""Configure realm provisioning permissions; demo data requires --demo explicitly."""

import argparse
import os

import httpx

# The installer ships scripts/setup.py on its own, so the client definition lives there.
from scripts.setup import MCP_CLIENT_ID, mcp_client
from server.models import DatabaseInput, TeamInput, UserInput
from server.polaris import PolarisProvider, enc
from server.storage import RustFSStorage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Create optional demo users and data")
    args = parser.parse_args()
    issuer = os.environ["OIDC_ISSUER"]
    demo_prefix = "demo"
    if args.demo and not all(os.environ.get(f"DEMO_{a}_PASSWORD") for a in ("ADMIN", "WRITER", "READER", "OUTSIDER")):
        parser.error("Run scripts.setup --demo before provisioning demo data")
    provider = PolarisProvider(os.environ, RustFSStorage(os.environ))
    try:
        with httpx.Client(base_url=os.environ["OIDC_INTERNAL_ISSUER"].rsplit("/realms/", 1)[0], timeout=30, trust_env=False) as kc:
            response = kc.post("/realms/master/protocol/openid-connect/token", data={
                "grant_type": "password", "client_id": "admin-cli", "username": "admin",
                "password": os.environ["KEYCLOAK_ADMIN_PASSWORD"],
            })
            response.raise_for_status()
            kc.headers["Authorization"] = "Bearer " + response.json()["access_token"]
            prefix = "/admin/realms/iceberg"
            # A dedicated realm service account provisions users. The portal never
            # receives the master-realm administrator password.
            response = kc.get(prefix + "/clients", params={"clientId": "iceberg-provisioner"})
            response.raise_for_status()
            client_config = {"clientId": "iceberg-provisioner", "enabled": True,
                             "publicClient": False, "serviceAccountsEnabled": True,
                             "standardFlowEnabled": False, "directAccessGrantsEnabled": False,
                             "secret": os.environ["OIDC_MANAGEMENT_SECRET"]}
            if response.json():
                client_id = response.json()[0]["id"]
                kc.put(prefix + "/clients/" + client_id, json=client_config).raise_for_status()
            else:
                kc.post(prefix + "/clients", json=client_config).raise_for_status()
                response = kc.get(prefix + "/clients", params={"clientId": "iceberg-provisioner"})
                response.raise_for_status()
                client_id = response.json()[0]["id"]
            # MCP clients sign in through a public PKCE client; existing realms skip the
            # import, so make sure it exists with the current callback port and mappers.
            mcp_config = mcp_client(os.environ.get("MCP_CALLBACK_PORT", "3010"))
            mappers = mcp_config.pop("protocolMappers")
            response = kc.get(prefix + "/clients", params={"clientId": MCP_CLIENT_ID})
            response.raise_for_status()
            if response.json():
                mcp_id = response.json()[0]["id"]
                # An update ignores protocolMappers; they are reconciled by name below.
                kc.put(prefix + "/clients/" + mcp_id, json=mcp_config).raise_for_status()
            else:
                kc.post(prefix + "/clients", json={**mcp_config, "protocolMappers": mappers}).raise_for_status()
                response = kc.get(prefix + "/clients", params={"clientId": MCP_CLIENT_ID})
                response.raise_for_status()
                mcp_id = response.json()[0]["id"]
            response = kc.get(prefix + "/clients/" + mcp_id + "/protocol-mappers/models")
            response.raise_for_status()
            present = {m["name"] for m in response.json()}
            for m in mappers:
                if m["name"] not in present:
                    kc.post(prefix + "/clients/" + mcp_id + "/protocol-mappers/models", json=m).raise_for_status()
            response = kc.get(prefix + "/client-scopes")
            response.raise_for_status()
            scopes = {scope["name"]: scope["id"] for scope in response.json()}
            for name in mcp_config["optionalClientScopes"]:
                kc.put(prefix + "/clients/" + mcp_id + "/optional-client-scopes/" + scopes[name]).raise_for_status()
            response = kc.get(prefix + "/clients/" + client_id + "/service-account-user")
            response.raise_for_status()
            service_user = response.json()["id"]
            response = kc.get(prefix + "/clients", params={"clientId": "realm-management"})
            response.raise_for_status()
            management_id = response.json()[0]["id"]
            for role_name in ("manage-users", "view-users", "query-users", "view-clients"):
                response = kc.get(prefix + "/clients/" + management_id + "/roles/" + role_name)
                response.raise_for_status()
                kc.post(prefix + "/users/" + service_user + "/role-mappings/clients/" + management_id,
                        json=[response.json()]).raise_for_status()
            response = kc.get(prefix + "/client-scopes")
            response.raise_for_status()
            basic = next(scope["id"] for scope in response.json() if scope["name"] == "basic")
            response = kc.get(prefix + "/clients")
            response.raise_for_status()
            for client in response.json():
                if client["clientId"] in ("iceberg-admin", "iceberg-users", MCP_CLIENT_ID):
                    kc.put(prefix + "/clients/" + client["id"] + "/default-client-scopes/" + basic).raise_for_status()
            response = kc.get(prefix + "/users/profile")
            response.raise_for_status()
            profile = response.json()
            # Users cannot edit their principal binding through the account console.
            for name in ("polaris_id", "polaris_name", "iceberg_provisioning_id"):
                profile["attributes"] = [a for a in profile["attributes"] if a["name"] != name]
                profile["attributes"].append({"name": name, "permissions": {"view": ["admin"], "edit": ["admin"]}})
            kc.put(prefix + "/users/profile", json=profile).raise_for_status()

            if not args.demo:
                print("Keycloak account provisioning configured. Manage users in the administration portal.")
                return
            for account in ("admin", "writer", "reader", "outsider"):
                name = demo_prefix + "-" + account
                response = kc.get(prefix + "/users", params={"username": name, "exact": "true"})
                response.raise_for_status()
                if not response.json():
                    user = {"username": name, "enabled": True, "emailVerified": True,
                            "firstName": "Demo", "lastName": account.title(),
                            "email": name + "@example.test",
                            "credentials": [{"type": "password", "temporary": False,
                                             "value": os.environ[f"DEMO_{account.upper()}_PASSWORD"]}]}
                    kc.post(prefix + "/users", json=user).raise_for_status()
                if account == "admin":
                    response = kc.get(prefix + "/users", params={"username": name, "exact": "true"})
                    response.raise_for_status()
                    subject = response.json()[0]["id"]
                    response = kc.get(prefix + "/clients", params={"clientId": "iceberg-admin"})
                    response.raise_for_status()
                    admin_client = response.json()[0]["id"]
                    response = kc.get(prefix + "/clients/" + admin_client + "/roles/platform-admin")
                    response.raise_for_status()
                    kc.post(prefix + "/users/" + subject + "/role-mappings/clients/" + admin_client,
                            json=[response.json()]).raise_for_status()

            teams = {t["name"]: t for t in provider.list_teams()}
            for name in (demo_prefix + "-team", demo_prefix + "-private"):
                if name not in teams:
                    teams[name] = provider.save_team(TeamInput(name=name))
            databases = provider.list_databases()
            for name, team in ((demo_prefix + "-demo", demo_prefix + "-team"), (demo_prefix + "-private", demo_prefix + "-private")):
                if not any(d["name"] == name for d in databases):
                    provider.create_database(DatabaseInput(name=name, team=teams[team]["id"]))
            users = {u["name"]: u for u in provider.list_users()}
            for account, role in (("admin", "reader"), ("writer", "writer"), ("reader", "reader")):
                name = demo_prefix + "-" + account
                if name not in users:
                    result = provider.create_user(UserInput(name=name, memberships=[{"team": teams[demo_prefix + "-team"]["id"], "role": role}]))
                    users[name] = result["user"]  # Generated Polaris client secret is deliberately discarded.
                response = kc.get(prefix + "/users", params={"username": name, "exact": "true"})
                response.raise_for_status()
                user, = response.json()
                path = "/principals/" + enc(users[name]["id"])
                principal = provider.require(path)
                properties = principal["properties"]
                if properties.get("portal.oidc-subject") not in (None, user["id"]):
                    raise RuntimeError("Existing user is linked to another subject; refusing to rebind")
                provider.update_properties(path, {**properties, "portal.oidc-subject": user["id"],
                                                   "portal.oidc-issuer": issuer})
                user["attributes"] = {**user.get("attributes", {}),
                                      "polaris_id": ["0"],
                                      "polaris_name": [principal["name"]]}
                kc.put(prefix + "/users/" + user["id"], json=user).raise_for_status()
        print("Optional demo accounts and data ready. Credentials are in the selected environment file.")
    finally:
        provider.close()


if __name__ == "__main__":
    main()
