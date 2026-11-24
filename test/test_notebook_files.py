"""A team's notebook files through its marimo server (user_portal/notebook_files.py), against a fake marimo."""

import json
import time
from email.parser import BytesParser
from email.policy import default
from urllib.parse import parse_qs

import httpx
import pytest

from server.models import ServiceError
from user_portal.notebook.seed import seed_workspace
from user_portal.notebook_files import EXAMPLE_TITLES, EXAMPLES, TEMPLATE, NotebookFiles, enabled, examples
from user_portal.runtime import Workspace

TOKEN = "gateway-token"


def form(request):
    """The fields of a URL-encoded or multipart form, as marimo's request.form() reads them."""
    kind = request.headers["content-type"]
    if kind.startswith("application/x-www-form-urlencoded"):
        return {k: v[0].encode() for k, v in parse_qs(request.content.decode()).items()}
    message = BytesParser(policy=default).parsebytes(b"Content-Type: " + kind.encode() + b"\r\n\r\n" + request.content)
    return {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
            for part in message.iter_parts()}


class FakeMarimo:
    """marimo's page (with its server token) and file API over an in-memory /work."""

    def __init__(self):
        self.files = {"/work/workspace.py": b"x", "/work/notes.txt": b"x", "/work/.marimo.toml": b"x"}
        self.dirs = {"/work"}
        self.server_token = "server-1"
        self.modified = {}

    def handler(self, request):
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        path = request.url.path
        if request.method == "GET" and path == "/workspaces/w1/":
            return httpx.Response(200, text=f'<marimo-server-token data-token="{self.server_token}" hidden>')
        if request.headers.get("marimo-server-token") != self.server_token:
            return httpx.Response(401, json={"error": "Missing server token"})
        if path.endswith("/api/files/list_files"):
            folder = json.loads(request.content)["path"]
            children = [p for p in [*self.files, *self.dirs] if p.rsplit("/", 1)[0] == folder]
            return httpx.Response(200, json={"files": [
                {"name": p.rsplit("/", 1)[1], "isDirectory": p in self.dirs,
                 "isMarimoFile": p.endswith(".py"), "lastModified": self.modified.get(p, 1)} for p in children]})
        if path.endswith("/api/files/create"):
            fields = form(request)
            target = f"{fields['path'].decode()}/{fields['name'].decode()}"
            if fields["type"] == b"directory":
                self.dirs.add(target)
            else:
                self.files[target] = fields["file"]
                self.modified[target] = time.time()
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404)


@pytest.fixture
def marimo():
    fake = FakeMarimo()
    workspace = Workspace("w1", "s1", "u1", "team", "db", "key", None, None, "http://notebook:2718", TOKEN)
    files = NotebookFiles({}, httpx.Client(transport=httpx.MockTransport(fake.handler)))
    return fake, workspace, files


def test_the_list_shows_the_teams_notebooks_and_the_examples(marimo):
    _, workspace, files = marimo
    listing = files.files(workspace)
    assert [n["path"] for n in listing["notebooks"]] == ["workspace.py"]
    assert listing["notebooks"][0]["url"] == "/workspaces/w1/?file=workspace.py"
    assert [e["id"] for e in listing["examples"]] == list(EXAMPLE_TITLES)
    assert listing["allFilesUrl"] == "/workspaces/w1/"


def test_a_new_notebook_starts_from_the_template_and_never_replaces_a_file(marimo):
    fake, workspace, files = marimo
    created = files.create(workspace, "Sales Forecast")
    assert created == {"path": "sales_forecast.py", "url": "/workspaces/w1/?file=sales_forecast.py"}
    assert fake.files["/work/sales_forecast.py"] == TEMPLATE.read_bytes()
    with pytest.raises(ServiceError) as exists:
        files.create(workspace, "sales_forecast")
    assert exists.value.status == 409
    for bad in ("../x", "a/b", "", "x" * 70):
        with pytest.raises(ServiceError):
            files.create(workspace, bad)
    # The newest file comes first.
    assert files.notebooks(workspace)[0]["path"] == "sales_forecast.py"


def test_an_example_is_copied_once_with_its_data_and_keeps_the_teams_edits(marimo):
    fake, workspace, files = marimo
    copy = files.copy_example(workspace, "06_duckdb_flights_write.py")
    assert copy["url"] == "/workspaces/w1/?file=examples/06_duckdb_flights_write.py"
    assert "/work/examples" in fake.dirs
    for name in ("06_duckdb_flights_write.py", "flights.yaml", "flights.product.yaml", "OSSIE-NOTICE.txt"):
        assert fake.files[f"/work/examples/{name}"] == (EXAMPLES / name).read_bytes()
    # The copy is the team's own: opening the example again keeps its edits.
    fake.files["/work/examples/06_duckdb_flights_write.py"] = b"# our changes"
    fake.files["/work/examples/flights.yaml"] = b"# our changes"
    files.copy_example(workspace, "06_duckdb_flights_write.py")
    assert fake.files["/work/examples/06_duckdb_flights_write.py"] == b"# our changes"
    assert fake.files["/work/examples/flights.yaml"] == b"# our changes"
    assert "examples/06_duckdb_flights_write.py" in {n["path"] for n in files.notebooks(workspace)}
    with pytest.raises(ServiceError) as unknown:
        files.copy_example(workspace, "../../etc/passwd")
    assert unknown.value.status == 404


def test_the_platform_can_turn_the_examples_off(marimo):
    fake, workspace, _ = marimo
    off = NotebookFiles({"NOTEBOOK_EXAMPLES": "false"}, httpx.Client(transport=httpx.MockTransport(fake.handler)))
    assert off.files(workspace)["examples"] == [] and examples({"NOTEBOOK_EXAMPLES": "false"}) == []
    # The portal shows this switch to the page, which then leaves the examples out of its text.
    assert enabled({}) and enabled({"NOTEBOOK_EXAMPLES": "true"}) and not enabled({"NOTEBOOK_EXAMPLES": "false"})
    with pytest.raises(ServiceError):
        off.copy_example(workspace, "01_pyiceberg_neighborhood.py")


def test_a_restarted_marimo_gets_its_new_server_token(marimo):
    fake, workspace, files = marimo
    files.notebooks(workspace)
    fake.server_token = "server-2"
    assert [n["path"] for n in files.notebooks(workspace)] == ["workspace.py"]
    assert workspace.server_token == "server-2"


def test_a_team_folder_gets_only_the_starter_notebooks(tmp_path):
    seed_workspace(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shared_workspace.py", "workspace.py"]
    (tmp_path / "workspace.py").write_text("# our changes")
    seed_workspace(tmp_path)
    assert (tmp_path / "workspace.py").read_text() == "# our changes"
