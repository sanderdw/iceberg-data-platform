"""Point the portal sign-in clients and linked users at new origins after an address switch.

Keycloak skips the realm import once the realm exists, so changed origins never reach
the iceberg-admin and iceberg-users clients. Linked users also carry the issuer they
signed in with; after an issuer change the user portal refuses them until it matches.

Run inside the keycloak-bootstrap image, which has the Keycloak administrator and
Polaris credentials. Pipe this file in so nothing needs to be mounted:

    docker compose run --rm --no-deps -T --entrypoint /app/.venv/bin/python keycloak-bootstrap - \\
        --admin-origins https://ADMIN.trycloudflare.com http://localhost:3000 \\
        --user-origins https://USERS.trycloudflare.com http://localhost:3002 \\
        --old-issuer http://localhost:8080/realms/iceberg < sync_origins.py

The new issuer is OIDC_ISSUER from the current .env. Running it twice changes nothing.
"""

import argparse
import os
from urllib.parse import urlsplit

import httpx

ISSUER_PROPERTY = "portal.oidc-issuer"


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path.strip("/")
            or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError(f"Expected an origin such as https://host:3000, got {value}")
    if parsed.scheme == "http" and parsed.hostname != "localhost":
        raise argparse.ArgumentTypeError(f"Plain HTTP only works on localhost: {value}")
    return f"{parsed.scheme}://{parsed.netloc}"


def update_client(kc, prefix, client_id, origins):
    """Allow sign-in, CORS and sign-out redirects for exactly these origins."""
    response = kc.get(prefix + "/clients", params={"clientId": client_id})
    response.raise_for_status()
    client, = response.json()
    client["redirectUris"] = [o + "/auth/callback" for o in origins]
    client["webOrigins"] = list(origins)
    client["attributes"] = {**client.get("attributes", {}),
                            "post.logout.redirect.uris": "##".join(o + "/" for o in origins)}
    kc.put(prefix + "/clients/" + client["id"], json=client).raise_for_status()
    print(f"{client_id}: sign-in allowed from {', '.join(origins)}")


def migrate_issuer(provider, old, new, enc):
    """Move users linked under the old issuer to the new one; other users are untouched."""
    old, new = old.rstrip("/"), new.rstrip("/")
    changed = []
    if old == new:
        return changed
    for principal in provider.management("/principals")["principals"]:
        properties = principal.get("properties", {})
        if properties.get(ISSUER_PROPERTY, "").rstrip("/") == old:
            provider.update_properties("/principals/" + enc(principal["name"]),
                                       {**properties, ISSUER_PROPERTY: new})
            changed.append(principal["name"])
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-origins", nargs="+", type=origin, required=True,
                        help="Administration portal origins; the first is PORTAL_ORIGIN")
    parser.add_argument("--user-origins", nargs="+", type=origin, required=True,
                        help="User portal origins; the first is USER_ORIGIN")
    parser.add_argument("--old-issuer", required=True, help="OIDC_ISSUER before the change")
    args = parser.parse_args()
    if args.admin_origins[0] != os.environ["OIDC_ORIGIN"].rstrip("/"):
        parser.error("The first --admin-origins value must equal PORTAL_ORIGIN in .env. "
                     "Restart the stack after editing .env, then run this again.")

    from server.polaris import PolarisProvider, enc
    from server.storage import RustFSStorage

    base = os.environ["OIDC_INTERNAL_ISSUER"].rsplit("/realms/", 1)[0]
    with httpx.Client(base_url=base, timeout=30, trust_env=False) as kc:
        response = kc.post("/realms/master/protocol/openid-connect/token", data={
            "grant_type": "password", "client_id": "admin-cli", "username": "admin",
            "password": os.environ["KEYCLOAK_ADMIN_PASSWORD"],
        })
        response.raise_for_status()
        kc.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        prefix = "/admin/realms/iceberg"
        update_client(kc, prefix, "iceberg-admin", args.admin_origins)
        update_client(kc, prefix, "iceberg-users", args.user_origins)

    provider = PolarisProvider(os.environ, RustFSStorage(os.environ))
    try:
        changed = migrate_issuer(provider, args.old_issuer, os.environ["OIDC_ISSUER"], enc)
    finally:
        provider.close()
    print(f"Linked users moved to {os.environ['OIDC_ISSUER']}: {', '.join(changed) or 'none needed'}")


if __name__ == "__main__":
    main()
