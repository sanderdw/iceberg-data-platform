"""A team's notebook files, through its marimo server: list them, start a new one, copy an example.

The gateway reaches each marimo server with the bearer token it already uses for the proxy, so this
works the same for Docker and Kubernetes runtimes, and only ever inside the team's shared folder
(/work). marimo's file API also wants its server token, which marimo puts in its page.

Examples ship with the platform and are no longer copied into every team folder. Opening one copies
it, with the data files the examples read beside them, into examples/ in the team folder; from then
on the copy is the team's own. NOTEBOOK_EXAMPLES=false turns them off for the whole platform.
"""

import re
from pathlib import Path
from urllib.parse import quote

import httpx

from server.models import ServiceError

NOTEBOOK = Path(__file__).parent / "notebook"
EXAMPLES = NOTEBOOK / "examples"
TEMPLATE = NOTEBOOK / "template.py"
WORK = "/work"
EXAMPLE_FOLDER = "examples"
# The examples in the order the Getting started guide follows.
EXAMPLE_TITLES = {
    "01_pyiceberg_neighborhood.py": "01 · Neighborhood data with PyIceberg",
    "02_duckdb_visualization.py": "02 · Visualize with DuckDB",
    "03_duckdb_iceberg.py": "03 · Native DuckDB on Iceberg",
    "04_duckdb_iceberg_v3_write.py": "04 · Write Iceberg v3 with DuckDB",
    "05_duckdb_iceberg_v3_read.py": "05 · Read Iceberg v3 with DuckDB",
    "06_duckdb_flights_write.py": "06 · Write an AI-ready flights product",
    "07_duckdb_flights_read.py": "07 · Read an AI-ready flights product",
    "08_dlt_ingest.py": "08 · Load data with dlt",
    "09_polars_grid_forecast_write.py": "09 · Write an AI-ready grid forecast with Polars",
    "10_polars_grid_forecast_read.py": "10 · Read an AI-ready grid forecast with Polars",
    "11_duckdb_iceberg_explained.py": "11 · How Apache Iceberg works",
}
# Data files the examples read beside themselves.
COMPANIONS = (".yaml", ".txt")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
SERVER_TOKEN = re.compile(r'<marimo-server-token[^>]*data-token="([^"]+)"')


def enabled(env):
    return str(env.get("NOTEBOOK_EXAMPLES", "true")).strip().lower() not in ("0", "false", "no", "off")


def examples(env):
    """The examples a team can open, or none when the platform turns them off."""
    if not enabled(env):
        return []
    return [{"id": name, "title": title} for name, title in EXAMPLE_TITLES.items() if (EXAMPLES / name).is_file()]


class NotebookFiles:
    def __init__(self, env, http=None):
        self.env = env
        self.http = http

    def client(self, workspace):
        return self.http or httpx.Client(timeout=15, trust_env=False)

    def headers(self, workspace, client, refresh=False):
        if refresh or not workspace.server_token:
            page = client.get(workspace.upstream + workspace.url,
                              headers={"Authorization": f"Bearer {workspace.token}"})
            found = SERVER_TOKEN.search(page.text) if page.status_code == 200 else None
            if not found:
                raise ServiceError(503, "The notebook is still starting. Try again in a moment.")
            workspace.server_token = found[1]
        return {"Authorization": f"Bearer {workspace.token}", "Marimo-Server-Token": workspace.server_token}

    def call(self, workspace, action, **kwargs):
        """POST to marimo's file API; renews the server token once, as marimo makes a new one on restart."""
        client = self.client(workspace)
        try:
            for refresh in (False, True):
                response = client.post(f"{workspace.upstream}{workspace.url}api/files/{action}",
                                       headers=self.headers(workspace, client, refresh), **kwargs)
                if response.status_code not in (401, 403):
                    break
            if response.status_code != 200:
                raise ServiceError(502, "The notebook did not answer. Try again in a moment.")
            body = response.json()
        except httpx.HTTPError:
            raise ServiceError(502, "The notebook did not answer. Try again in a moment.") from None
        finally:
            if client is not self.http:
                client.close()
        if body.get("success") is False:
            raise ServiceError(502, "The notebook could not save the file.")
        return body

    def listing(self, workspace, folder=None):
        return self.call(workspace, "list_files", json={"path": folder or WORK}).get("files", [])

    def upload(self, workspace, folder, name, content):
        self.call(workspace, "create", data={"path": folder, "type": "file", "name": name},
                  files={"file": (name, content, "text/x-python")})

    def url(self, workspace, path):
        return f"{workspace.url}?file={quote(path, safe='/')}"

    def notebooks(self, workspace):
        """The team's notebooks: marimo files in the folder and in examples/, newest first."""
        found = []
        root = self.listing(workspace)
        for item in root:
            if item.get("isMarimoFile") and not item["name"].startswith("."):
                found.append((item["name"], item))
        if any(i.get("isDirectory") and i["name"] == EXAMPLE_FOLDER for i in root):
            for item in self.listing(workspace, f"{WORK}/{EXAMPLE_FOLDER}"):
                if item.get("isMarimoFile"):
                    found.append((f"{EXAMPLE_FOLDER}/{item['name']}", item))
        found.sort(key=lambda pair: pair[1].get("lastModified") or 0, reverse=True)
        return [{"path": path, "url": self.url(workspace, path), "modified": item.get("lastModified")}
                for path, item in found]

    def files(self, workspace):
        return {"notebooks": self.notebooks(workspace), "examples": examples(self.env),
                "allFilesUrl": workspace.url}

    def create(self, workspace, name):
        """A new notebook from the starter template; never replaces a file."""
        name = (name or "").strip().lower().removesuffix(".py").replace(" ", "_")
        if not NAME.match(name):
            raise ServiceError(422, "Use 1 to 63 lowercase letters, digits, - and _ for the name.")
        filename = name + ".py"
        if any(item["name"] == filename for item in self.listing(workspace)):
            raise ServiceError(409, f"{filename} already exists in your team's folder.")
        self.upload(workspace, WORK, filename, TEMPLATE.read_bytes())
        return {"path": filename, "url": self.url(workspace, filename)}

    def copy_example(self, workspace, example):
        """The team's copy of an example in examples/, made on first use with the data files beside it."""
        if example not in {e["id"] for e in examples(self.env)}:
            raise ServiceError(404, "No such example on this platform.")
        if not any(i.get("isDirectory") and i["name"] == EXAMPLE_FOLDER for i in self.listing(workspace)):
            self.call(workspace, "create", data={"path": WORK, "type": "directory", "name": EXAMPLE_FOLDER})
        folder = f"{WORK}/{EXAMPLE_FOLDER}"
        present = {item["name"] for item in self.listing(workspace, folder)}
        wanted = [example, *sorted(p.name for p in EXAMPLES.iterdir() if p.suffix in COMPANIONS)]
        for name in wanted:
            if name not in present:
                self.upload(workspace, folder, name, (EXAMPLES / name).read_bytes())
        path = f"{EXAMPLE_FOLDER}/{example}"
        return {"path": path, "url": self.url(workspace, path)}
