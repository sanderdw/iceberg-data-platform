"""Live check of tools on the user's computer: device login, PyIceberg, DuckDB SQL and denied access.

Requires `scripts.setup --demo` and `keycloak-bootstrap --demo`. Downloads the helper from the
user portal, signs in by scripting Keycloak's device verification page and creates and removes
only its own namespace. No token is printed or passed on a command line.
"""

import asyncio
import base64
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from io import StringIO
from pathlib import Path
from urllib.parse import urljoin

import duckdb
import httpx
import pyarrow as pa
from dotenv import load_dotenv
from pyiceberg.exceptions import ForbiddenError, UnauthorizedError

from scripts.mcp_smoke import sign_in, tools_as


def approve(url, username, password):
    """Sign in and grant access on the device verification pages, as the user's browser would."""
    with httpx.Client(timeout=30, follow_redirects=False) as browser:
        # Python's cookie jar does not replay cookies for localhost with a port; send them by hand.
        cookies = lambda: {"Cookie": "; ".join(f"{c.name}={c.value}" for c in browser.cookies.jar)}
        response = browser.get(url)
        for _ in range(8):
            if response.is_redirect:
                response = browser.get(urljoin(str(response.url), response.headers["location"]), headers=cookies())
                continue
            response.raise_for_status()
            form = re.search(r'<form[^>]*action="([^"]+)"', response.text)
            if not form:
                break
            # Keep hidden fields such as the consent code; the device grant always asks for consent.
            fields = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', response.text))
            if 'name="password"' in response.text:
                fields.update(username=username, password=password)
            else:
                assert 'name="accept"' in response.text, "Unexpected Keycloak page during device sign-in"
                fields["accept"] = "Yes"
            action = urljoin(str(response.url), html.unescape(form.group(1)))
            response = browser.post(action, headers=cookies(), data=fields)
        assert response.status_code == 200 and not re.search(r"<form", response.text), "Device sign-in not confirmed"
    return True


def load_helper(users_url, directory):
    response = httpx.get(users_url + "/iceberg_connect.py", timeout=15)
    response.raise_for_status()
    assert response.headers["content-disposition"] == 'attachment; filename="iceberg_connect.py"'
    path = directory / "iceberg_connect.py"
    path.write_text(response.text)
    spec = importlib.util.spec_from_file_location("iceberg_connect", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["iceberg_connect"] = module
    spec.loader.exec_module(module)
    return module, path


def log_in(helper, username, password):
    # A new session per account; it stores its sign-in where the CLI looks for it.
    helper._default = helper.Session()
    approved = []

    def browser(url):
        # The helper ignores browser errors, so record the outcome and stop polling on failure.
        try:
            approved.append(approve(url, username, password))
        except (AssertionError, httpx.HTTPError) as exc:
            approved.append(exc)

    def wait(seconds):
        if isinstance(approved[0], Exception):
            raise approved[0]
        time.sleep(1)

    helper.session().login(out=StringIO(), sleep=wait, open_browser=browser)
    assert helper.credentials_path(helper.ISSUER).stat().st_mode & 0o777 == 0o600


def main():
    load_dotenv()
    issuer, users_url = os.environ["OIDC_ISSUER"].rstrip("/"), os.environ["USER_ORIGIN"].rstrip("/")
    passwords = {a: os.environ[f"DEMO_{a.upper()}_PASSWORD"] for a in ("writer", "reader", "outsider")}
    namespace = "connect_smoke_" + str(int(time.time()))

    # The database ID comes from the MCP tools, as a user would read it in the portal.
    port = os.environ.get("MCP_CALLBACK_PORT", "3010")
    mcp_token = sign_in(issuer, "iceberg-mcp", f"http://localhost:{port}/callback", "demo-writer", passwords["writer"])
    _, (databases,) = asyncio.run(tools_as(users_url + "/mcp", mcp_token, [("list_databases", {})]))
    database = next(d["id"] for d in databases.structured_content["databases"] if d["name"] == "demo-demo")

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        os.environ["XDG_CONFIG_HOME"] = str(directory / "config")
        helper, script = load_helper(users_url, directory)
        assert helper.ISSUER == issuer
        print("PASS: the portal serves the helper with this platform's issuer and catalog")

        log_in(helper, "demo-writer", passwords["writer"])
        claims = json.loads(base64.urlsafe_b64decode(helper.session().access_token().split(".")[1] + "=="))
        assert claims["exp"] - claims["iat"] == 3600 and claims["azp"] == helper.CLIENT_ID, claims["exp"] - claims["iat"]
        catalog = helper.catalog(database)
        catalog.create_namespace(namespace)
        try:
            rows = pa.table({"id": [1, 2, 3], "event": ["deploy", "build", "release"]})
            catalog.create_table((namespace, "events"), schema=rows.schema).append(rows)
            print("PASS: device login as writer; PyIceberg published a table with vended credentials")

            log_in(helper, "demo-reader", passwords["reader"])
            # The CLI path: this SQL is what the DuckDB CLI and DBeaver run.
            sql = subprocess.run([sys.executable, str(script), "duckdb", database],
                                 check=True, capture_output=True, text=True).stdout
            connection = duckdb.connect()
            for statement in sql.splitlines():
                connection.execute(statement)
            count = connection.execute(f'SELECT count(*) FROM lakehouse."{namespace}".events').fetchone()[0]
            assert count == 3, count
            try:
                connection.execute(f"INSERT INTO lakehouse.\"{namespace}\".events VALUES (4, 'denied')")
                raise AssertionError("A read-only attach accepted a write")
            except duckdb.Error:
                pass
            connection.close()
            connection = helper.duckdb_connection(database)
            assert connection.execute(f'SELECT max(id) FROM lakehouse."{namespace}".events').fetchone()[0] == 3
            connection.close()
            print("PASS: reader attached the database from the CLI's SQL and from Python; writes are refused")

            log_in(helper, "demo-outsider", passwords["outsider"])
            try:
                helper.catalog(database).list_namespaces()
                raise AssertionError("An unlinked account read the catalog")
            except (ForbiddenError, UnauthorizedError):
                pass
            print("PASS: an account without platform access is refused by Polaris")
        finally:
            log_in(helper, "demo-writer", passwords["writer"])
            catalog = helper.catalog(database)
            if catalog.table_exists((namespace, "events")):
                catalog.drop_table((namespace, "events"))
            catalog.drop_namespace(namespace)
            helper.session().logout()
            assert not helper.credentials_path(helper.ISSUER).exists()
            print("PASS: cleaned up and signed out")


if __name__ == "__main__":
    main()
