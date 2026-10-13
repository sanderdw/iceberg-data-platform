import json
from types import SimpleNamespace

import pytest

from dbt_portal.client import iceberg_dbt as cli
from tests.test_api import create, enable


@pytest.fixture
def terminal(stack, tmp_path, monkeypatch):
    """The CLI against the test API, signed in as bob (a writer), in an empty folder."""
    monkeypatch.chdir(tmp_path)
    api = cli.Api(SimpleNamespace(token=lambda: "bob"), origin="http://testserver", http=stack.client)
    id = create(stack)["id"]
    enable(stack)

    def run(*argv):
        args = cli.parser().parse_args(argv)
        handlers = {"projects": cli.cmd_projects, "clone": cli.cmd_clone, "pull": cli.cmd_pull,
                    "status": cli.cmd_status, "push": cli.cmd_push, "show": cli.cmd_show}
        return handlers.get(args.command, cli.cmd_run)(api, args)

    return SimpleNamespace(run=run, id=id, root=tmp_path / "shop")


def test_the_portal_serves_the_cli_with_its_addresses(stack):
    response = stack.client.get("/iceberg_dbt.py")
    assert response.headers["content-disposition"] == 'attachment; filename="iceberg_dbt.py"'
    assert f"ORIGIN = {stack.settings.origin!r}" in response.text
    assert f"CLIENT_ID = {stack.settings.mcp_client_id!r}" in response.text
    assert "secret" not in response.text.lower().split('"""')[0]


def test_clone_build_and_show_run_main_on_the_platform(terminal, stack, monkeypatch, capsys):
    terminal.run("clone", "shop")
    state = json.loads((terminal.root / cli.STATE).read_text())
    assert state["ref"] == "main" and "dbt_project.yml" in state["files"]
    monkeypatch.chdir(terminal.root)
    assert terminal.run("build", "--select", "stg_regions+") == 0
    run = stack.services.store.runs(terminal.id)[0]
    assert (run["ref"], run["command"], run["selector"], run["environment"]) == ("main", "build", "stg_regions+",
                                                                                "development")
    err = capsys.readouterr().err
    assert "Running build on main in development" in err and f"ref=main&run={run['id']}" in err
    assert terminal.run("show", "--select", "stg_regions", "--output", "json") == 0
    assert json.loads(capsys.readouterr().out) == {"show": [{"region_id": 1}]}


def test_local_edits_reach_the_platform_only_through_a_branch(terminal, stack, monkeypatch, capsys):
    terminal.run("clone", "shop")
    monkeypatch.chdir(terminal.root)
    (terminal.root / "models").mkdir(exist_ok=True)
    (terminal.root / "models" / "orders.sql").write_text("select 1 as id\n")
    (terminal.root / "target").mkdir()
    (terminal.root / "target" / "manifest.json").write_text("{}")  # local output is never pushed
    terminal.run("status")
    assert "added     models/orders.sql" in capsys.readouterr().out
    terminal.run("build")
    assert "1 local change(s) are not on the platform; this runs main" in capsys.readouterr().err
    with pytest.raises(cli.Failure, match="main changes only through a merge"):
        terminal.run("push", "--branch", "main")
    terminal.run("push", "--branch", "orders", "-m", "Add orders")
    assert "build --ref orders" in capsys.readouterr().out
    files = stack.call("GET", f"/projects/{terminal.id}/files?ref=orders", "bob").json()["files"]
    assert {f["path"] for f in files} >= {"models/orders.sql"} and "target/manifest.json" not in str(files)
    terminal.run("status")
    assert "No local changes." in capsys.readouterr().out
    terminal.run("build", "--ref", "orders")
    assert stack.services.store.runs(terminal.id)[0]["ref"] == "orders"


def test_pull_keeps_local_edits(terminal, stack, monkeypatch, capsys):
    terminal.run("clone", "shop")
    monkeypatch.chdir(terminal.root)
    (terminal.root / "README.md").write_text("mine\n")
    stack.call("POST", f"/projects/{terminal.id}/branches", "bob", json={"name": "docs"})
    stack.call("POST", f"/projects/{terminal.id}/commits", "bob",
               json={"branch": "docs", "files": {"README.md": "theirs\n", "NOTES.md": "new\n"}, "message": "x"})
    terminal.run("pull", "--ref", "docs")
    assert "kept your version of README.md" in capsys.readouterr().out
    assert (terminal.root / "README.md").read_text() == "mine\n" and (terminal.root / "NOTES.md").exists()


def test_outside_a_project_folder_it_says_how_to_start(terminal):
    with pytest.raises(cli.Failure, match=r"iceberg_dbt\.py clone"):
        terminal.run("build")


def test_show_keeps_one_line_per_row():
    assert cli.cell("Dit is\r\n- een samentelling") == "Dit is - een samentelling"
    assert cli.cell("x" * 50) == "x" * 39 + "…"
