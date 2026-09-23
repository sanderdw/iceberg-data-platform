# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx>=0.28",
#     "pyiceberg[pyarrow]>=0.12",
#     "duckdb>=1.5",
# ]
# ///
"""Connect your own tools to the Iceberg platform as yourself.

    uv run iceberg_connect.py login              # sign in once in your browser
    uv run iceberg_connect.py duckdb DATABASE    # SQL for the DuckDB CLI or DBeaver
    uv run iceberg_connect.py token              # a fresh access token
    uv run iceberg_connect.py logout             # revoke and forget the sign-in

From Python, next to this file:

    from iceberg_connect import catalog, duckdb_connection
    catalog("db-...").list_namespaces()

Your Keycloak account and your Polaris grants decide what you can read and write.
The refresh token is stored in ~/.config/iceberg-platform with owner-only permissions.
"""

import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import webbrowser
from pathlib import Path

import httpx

# Defaults match a local installation; the user portal fills in these values when you download this file.
ISSUER = "http://localhost:8080/realms/iceberg"
CATALOG_URI = "http://localhost:8181/api/catalog"
CLIENT_ID = "iceberg-cli"

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
LOGIN_AGAIN = "Sign in again with: uv run iceberg_connect.py login"


class LoginRequired(RuntimeError):
    pass


def credentials_path(issuer=ISSUER):
    # One file per platform, so several installations do not overwrite each other.
    name = hashlib.sha256(issuer.encode()).hexdigest()[:16] + ".json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "iceberg-platform" / name


def _write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A unique owner-only file per write, so concurrent refreshes never share a temporary file.
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump(data, file)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _expiry(token):
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"]
    except (IndexError, KeyError, ValueError):
        return 0


class Session:
    """A signed-in user: the stored refresh token and the current access token."""

    def __init__(self, issuer=ISSUER, client_id=CLIENT_ID, path=None, http=None):
        self.issuer, self.client_id = issuer.rstrip("/"), client_id
        self.path = path or credentials_path(self.issuer)
        self.http = http or httpx.Client(timeout=30)
        self._access_token = None

    def _endpoint(self, name):
        return f"{self.issuer}/protocol/openid-connect/{name}"

    def _store(self, issued):
        self._access_token = issued["access_token"]
        if "refresh_token" in issued:
            _write_private(self.path, {"issuer": self.issuer, "refresh_token": issued["refresh_token"]})

    def login(self, out=sys.stdout, sleep=time.sleep, open_browser=webbrowser.open):
        """Device authorization grant: approve in any browser, no callback port needed."""
        response = self.http.post(self._endpoint("auth/device"), data={
            "client_id": self.client_id, "scope": "openid offline_access"})
        response.raise_for_status()
        device = response.json()
        url = device.get("verification_uri_complete") or device["verification_uri"]
        print(f"Open {url} and confirm the code {device['user_code']}.", file=out, flush=True)
        with contextlib.suppress(Exception):  # A missing browser is fine; the URL is printed.
            open_browser(url)
        interval = device.get("interval", 5)
        deadline = time.monotonic() + device.get("expires_in", 600)
        while time.monotonic() < deadline:
            sleep(interval)
            response = self.http.post(self._endpoint("token"), data={
                "grant_type": DEVICE_GRANT, "client_id": self.client_id, "device_code": device["device_code"]})
            if response.status_code == 200:
                self._store(response.json())
                print("Signed in.", file=out)
                return
            error = response.json().get("error")
            if error == "slow_down":
                interval += 5
            elif error != "authorization_pending":
                raise LoginRequired("Sign-in was denied or expired. " + LOGIN_AGAIN)
        raise LoginRequired("Sign-in expired. " + LOGIN_AGAIN)

    def access_token(self):
        """The current access token, refreshed a minute before it expires."""
        if self._access_token and _expiry(self._access_token) > time.time() + 60:
            return self._access_token
        try:
            stored = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raise LoginRequired("You are not signed in. " + LOGIN_AGAIN) from None
        response = self.http.post(self._endpoint("token"), data={
            "grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": stored["refresh_token"]})
        if response.status_code in (400, 401):
            raise LoginRequired("Your sign-in has expired or was revoked. " + LOGIN_AGAIN)
        response.raise_for_status()
        self._store(response.json())
        return self._access_token

    def logout(self):
        try:
            stored = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        response = self.http.post(self._endpoint("revoke"), data={
            "client_id": self.client_id, "token": stored["refresh_token"], "token_type_hint": "refresh_token"})
        response.raise_for_status()  # Keep the token when Keycloak did not revoke it, so logout can be retried.
        self.path.unlink(missing_ok=True)
        self._access_token = None


_default = None


def session():
    global _default
    if _default is None:
        _default = Session()
    return _default


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def duckdb_sql(warehouse, token, alias="lakehouse", write=False, catalog_uri=CATALOG_URI):
    """Statements that attach one database, one per line so DBeaver can take each as a bootstrap query."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
        raise ValueError("The alias must be a plain SQL identifier.")
    return "\n".join([
        "INSTALL httpfs;", "LOAD httpfs;", "INSTALL iceberg;", "LOAD iceberg;",
        f"CREATE OR REPLACE SECRET {alias}_token (TYPE iceberg, TOKEN {sql_literal(token)});",
        f"DETACH DATABASE IF EXISTS {alias};",
        (f"ATTACH {sql_literal(warehouse)} AS {alias} (TYPE iceberg, ENDPOINT {sql_literal(catalog_uri)}, "
         f"SECRET {alias}_token, ACCESS_DELEGATION_MODE 'vended_credentials', "
         f"SUPPORT_NESTED_NAMESPACES true{'' if write else ', READ_ONLY'});"),
    ]) + "\n"


def duckdb_connection(warehouse, alias="lakehouse", write=False):
    """A DuckDB connection with the database attached. The token lasts an hour; connect again to renew."""
    import duckdb

    connection = duckdb.connect()
    try:
        for statement in duckdb_sql(warehouse, session().access_token(), alias, write).splitlines():
            connection.execute(statement)
    except duckdb.Error:
        connection.close()
        # DuckDB errors can repeat the CREATE SECRET statement, which contains the token.
        raise RuntimeError(f"Could not attach {warehouse}. Check the name and your access.") from None
    return connection


try:
    from pyiceberg.catalog.rest.auth import AuthManager, AuthManagerFactory
except ImportError:  # DuckDB-only use does not need PyIceberg.
    AuthManager = None

if AuthManager is not None:
    class RefreshingAuthManager(AuthManager):
        def auth_header(self):
            return f"Bearer {session().access_token()}"

    AuthManagerFactory.register("iceberg-platform", RefreshingAuthManager)


def catalog(warehouse, **properties):
    """A PyIceberg catalog for one database. It renews its token as long as the sign-in lasts."""
    from pyiceberg.catalog import load_catalog

    return load_catalog(warehouse, **{
        "type": "rest", "uri": CATALOG_URI, "warehouse": warehouse, "auth": {"type": "iceberg-platform"},
        # Polaris hands out short-lived storage credentials for each table you may access.
        "header.X-Iceberg-Access-Delegation": "vended-credentials", **properties,
    })


def main(argv=None):
    parser = argparse.ArgumentParser(description="Connect your own tools to the Iceberg platform.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="sign in with your browser")
    commands.add_parser("token", help="print a fresh access token")
    duckdb_command = commands.add_parser("duckdb", help="print SQL that attaches a database in DuckDB")
    duckdb_command.add_argument("warehouse", help="catalog name of the database, for example db-...")
    duckdb_command.add_argument("--as", dest="alias", default="lakehouse", help="name to attach it as")
    duckdb_command.add_argument("--write", action="store_true", help="attach writable (default: read-only)")
    commands.add_parser("logout", help="revoke and forget your sign-in")
    args = parser.parse_args(argv)
    try:
        if args.command == "login":
            session().login()
        elif args.command == "token":
            print(session().access_token())
        elif args.command == "duckdb":
            print(duckdb_sql(args.warehouse, session().access_token(), args.alias, args.write), end="")
        else:
            session().logout()
            print("Signed out.")
    except (LoginRequired, ValueError) as exc:
        sys.exit(str(exc))
    except httpx.HTTPStatusError as exc:
        sys.exit(f"Keycloak refused the request with status {exc.response.status_code}.")
    except httpx.TransportError:
        sys.exit(f"Could not reach {ISSUER}. Is the platform running?")


if __name__ == "__main__":
    main()
