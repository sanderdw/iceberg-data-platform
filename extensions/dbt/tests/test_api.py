import json
import time

import httpx

from dbt_portal.api import OPERATIONS
from dbt_portal.bridge_client import in_range
from dbt_portal.cleanup import Cleaner
from dbt_portal.config import ROOT, contract_range
from tests.conftest import OTHER, TEAM


def enable(stack, environment="development"):
    assert stack.call("POST", f"/teams/{TEAM}/environments/{environment}").status_code == 200


def create(stack, user="bob", name="shop"):
    response = stack.call("POST", "/projects", user, json={"team": TEAM, "name": name, "default_database": "sales"})
    assert response.status_code == 201, response.text
    return response.json()


def test_whoami_and_environments(stack):
    body = stack.call("GET", "/me", "carol").json()
    assert body["user"]["name"] == "carol" and body["extension"] == "dbt"
    assert stack.call("GET", "/me", "nobody").json()["code"] == "unauthorized"
    denied = stack.call("POST", f"/teams/{TEAM}/environments/development", "bob")
    assert (denied.status_code, denied.json()["code"]) == (403, "forbidden_role")
    enable(stack)
    environments = stack.call("GET", f"/teams/{TEAM}/environments", "carol").json()
    assert [(e["environment"], e["enabled"]) for e in environments] == [
        ("development", True), ("acceptance", False), ("production", False)]
    assert environments[0]["databases"] == ["sales", "raw-data"]
    assert stack.call("GET", f"/teams/{OTHER}/environments", "carol").json()["code"] == "not_a_member"


def test_project_creation_starts_from_an_empty_project(stack):
    assert stack.call("POST", "/projects", "carol", json={"team": TEAM, "name": "shop"}).json()["code"] == \
        "forbidden_role"
    assert stack.call("POST", "/projects", "bob", json={"team": TEAM, "name": "Shop"}).json()["code"] == "invalid_name"
    wrong = stack.call("POST", "/projects", "bob", json={"team": TEAM, "name": "shop", "default_database": "nope"})
    assert wrong.json()["code"] == "unknown_database" and "sales" in wrong.json()["detail"]["databases"]
    project = create(stack)
    assert project["default_database"] == "sales"
    assert stack.call("POST", "/projects", "bob", json={"team": TEAM, "name": "shop"}).json()["code"] == \
        "project_exists"
    assert stack.call("POST", "/projects", "bob", json={"team": TEAM, "name": "other"}).json()[
        "default_database"] == "raw-data"
    files = stack.call("GET", f"/projects/{project['id']}/files", "carol").json()
    paths = {f["path"] for f in files["files"]}
    # No example pipeline: only the project file, its README and the namespace macro.
    assert paths == {"dbt_project.yml", "README.md", "macros/generate_schema_name.sql"}
    config = stack.call("GET", f"/projects/{project['id']}/files/dbt_project.yml", "carol").json()["content"]
    assert "name: shop" in config and "+catalog_name: sales" in config and "{{" not in config
    # Other teams do not even see it.
    assert stack.call("GET", f"/projects/{project['id']}", "dave").json()["code"] == "unknown_project"
    assert [p["name"] for p in stack.call("GET", "/projects", "dave").json()] == []


def test_branches_commits_and_protected_main(stack):
    id = create(stack)["id"]
    blocked = stack.call("POST", f"/projects/{id}/commits", "bob",
                         json={"branch": "main", "files": {"a.sql": "select 1"}, "message": "x"})
    assert blocked.json()["code"] == "protected_branch"
    assert stack.call("POST", f"/projects/{id}/branches", "carol", json={"name": "feature"}).json()["code"] == \
        "forbidden_role"
    head = stack.call("POST", f"/projects/{id}/branches", "bob", json={"name": "feature"}).json()["revision"]
    commit = stack.call("POST", f"/projects/{id}/commits", "bob", json={
        "branch": "feature", "message": "Add orders", "expected_revision": head,
        "files": {"models/marts/orders.sql": "select 1 as id", "README.md": None}})
    assert commit.status_code == 201, commit.text
    stale = stack.call("POST", f"/projects/{id}/commits", "bob", json={
        "branch": "feature", "message": "Again", "expected_revision": head, "files": {"x.sql": "select 1"}})
    assert stale.json()["code"] == "stale_branch"
    reserved = stack.call("POST", f"/projects/{id}/commits", "bob", json={
        "branch": "feature", "message": "creds", "files": {"profiles.yml": "x: 1"}})
    assert reserved.json()["code"] == "reserved_path"
    escape = stack.call("POST", f"/projects/{id}/commits", "bob", json={
        "branch": "feature", "message": "x", "files": {"../x.sql": "select 1"}})
    assert escape.json()["code"] == "invalid_path"
    diff = stack.call("GET", f"/projects/{id}/diff", "carol", params={"head": "feature"}).json()
    assert {f["path"] for f in diff["files"]} == {"models/marts/orders.sql", "README.md"}
    assert stack.call("POST", f"/projects/{id}/merge", "bob", json={"branch": "feature"}).json()["code"] == \
        "forbidden_role"
    merged = stack.call("POST", f"/projects/{id}/merge", "alice", json={"branch": "feature"}).json()
    assert merged["merged"] is True
    history = stack.call("GET", f"/projects/{id}/history", "carol").json()
    assert history[0]["subject"] == "Add orders" and history[0]["author"] == "bob (via agent)"


def test_runs_use_the_right_token_and_follow_promotion_rules(stack):
    id = create(stack)["id"]
    assert stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build"}).json()["code"] == \
        "environment_not_enabled"
    enable(stack)
    enable(stack, "production")
    denied = stack.call("POST", f"/projects/{id}/runs", "carol", json={"command": "build"})
    assert denied.json()["code"] == "forbidden_role"
    run = stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "select": "stg_regions+",
                                                                   "wait": 30}).json()
    assert run["status"] == "success", run
    assert (run["triggered_by"], run["agent"], run["access"]) == ("bob", "ext-dbt-mcp", "write")
    started = stack.launcher.started[run["id"]]
    assert started["timeout"] == 2700
    assert started["token"] == "token-write"
    assert started["job"]["command"] == ["build", "--select", "stg_regions+"]
    assert started["job"]["forward"] == {"listen": 9000, "target": "rustfs:9000"}
    assert started["job"]["publish"]["catalogs"] == {"raw_data": "db-" + "3" * 32, "sales": "db-" + "1" * 32}
    # A reader may test and compile, with a read token only.
    test = stack.call("POST", f"/projects/{id}/runs", "carol", json={"command": "test", "wait": 30}).json()
    assert stack.launcher.started[test["id"]]["token"] == "token-read"
    assert stack.launcher.started[test["id"]]["job"]["publish"] is None
    # Production only builds main.
    branch = stack.call("POST", f"/projects/{id}/branches", "bob", json={"name": "wip"}).json()
    stack.call("POST", f"/projects/{id}/commits", "bob", json={"branch": "wip", "message": "wip",
                                                              "files": {"models/x.sql": "select 1"}})
    blocked = stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "environment": "production",
                                                                       "ref": "wip"})
    assert blocked.json()["code"] == "main_required", branch
    assert stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "environment": "production",
                                                                    "ref": "main"}).status_code == 202
    detail = stack.call("GET", f"/runs/{run['id']}", "carol").json()
    assert {r["node"]: r["status"] for r in detail["results"]}["model.shop.stg_regions"] == "success"
    assert stack.call("GET", f"/runs/{run['id']}/logs", "carol").json()["lines"] == ["Finished 'build' successfully"]
    assert stack.call("GET", f"/runs/{run['id']}", "dave").json()["code"] == "unknown_project"


def test_staged_run_contains_generated_profiles_and_platform_macros(stack):
    id = create(stack)["id"]
    enable(stack)
    run = stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "wait": 30}).json()
    staged = stack.launcher.started[run["id"]]["staged"]
    names = set(staged.getnames())
    assert {"project/catalogs.yml", "profiles/profiles.yml", "project/macros/_iceberg_platform/replace_table.sql",
            "job.json", "out"} <= names
    # A new project defines generate_schema_name, so the platform's is not added twice.
    assert "project/macros/_iceberg_platform/generate_schema_name.sql" not in names
    catalogs = staged.extractfile("project/catalogs.yml").read().decode()
    assert "name: raw_data" in catalogs and "warehouse: db-" + "3" * 32 in catalogs
    assert "http://polaris-control-plane:8181/api/catalog" in catalogs
    profiles = staged.extractfile("profiles/profiles.yml").read().decode()
    assert "env_var('DBT_ENV_SECRET_POLARIS_TOKEN')" in profiles and "token-" not in profiles
    assert all(m.uid == 10001 for m in staged.getmembers())


def test_pipeline_graph_and_lineage(stack):
    id = create(stack)["id"]
    enable(stack)
    empty = stack.call("GET", f"/projects/{id}/pipeline", "carol").json()
    assert empty["nodes"] == [] and "Run build" in empty["hint"]
    stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "wait": 30})
    pipeline = stack.call("GET", f"/projects/{id}/pipeline", "carol").json()
    nodes = {n["id"]: n for n in pipeline["nodes"]}
    assert set(nodes) == {"seed.shop.regions", "model.shop.stg_regions", "model.shop.regions_per_country",
                          "source.shop.raw.events"}
    staging = nodes["model.shop.stg_regions"]
    assert (staging["status"], staging["tests"]["pass"], staging["databaseId"]) == ("success", 1, "db-" + "1" * 32)
    assert nodes["model.shop.regions_per_country"]["status"] == "never_run"
    assert "sql" not in staging
    assert {"from": "model.shop.stg_regions", "to": "model.shop.regions_per_country"} in pipeline["edges"]
    assert not any(e["from"].startswith("macro.") for e in pipeline["edges"])
    detail = stack.call("GET", f"/projects/{id}/nodes/model.shop.stg_regions", "carol").json()
    assert detail["columns"][0] == {"name": "region_id", "type": "INTEGER", "description": "Stable id", "index": 0}
    assert detail["testResults"][0]["status"] == "pass" and detail["compiledSql"]
    lineage = stack.call("GET", f"/projects/{id}/lineage/model.shop.regions_per_country", "carol").json()
    assert {n["id"] for n in lineage["upstream"]} == {"model.shop.stg_regions", "seed.shop.regions",
                                                       "source.shop.raw.events"}


def test_pipeline_of_one_branch(stack):
    id = create(stack)["id"]
    enable(stack)
    stack.call("POST", f"/projects/{id}/branches", "bob", json={"name": "wip"})
    stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "ref": "wip", "wait": 30})
    # Without ref the latest run of any branch draws the graph; with ref only that branch's runs count.
    assert stack.call("GET", f"/projects/{id}/pipeline", "carol").json()["nodes"]
    wip = stack.call("GET", f"/projects/{id}/pipeline?ref=wip", "carol").json()
    assert wip["ref"] == "wip" and {r["ref"] for r in wip["runs"]} == {"wip"}
    assert {n["id"]: n["status"] for n in wip["nodes"]}["model.shop.stg_regions"] == "success"
    main = stack.call("GET", f"/projects/{id}/pipeline?ref=main", "carol").json()
    assert main["nodes"] == [] and main["runs"] == [] and "on main" in main["hint"]
    node = stack.call("GET", f"/projects/{id}/nodes/model.shop.stg_regions?ref=main", "carol").json()
    assert node["code"] == "no_pipeline"
    node = stack.call("GET", f"/projects/{id}/nodes/model.shop.stg_regions?ref=wip", "carol").json()
    assert node["status"] == "success"


class FakeCatalog:
    """Iceberg REST of the sales database: one table of this project, one since written by another
    producer; a third was dropped. raw-data holds none of them."""

    def __init__(self, project):
        url = f"http://localhost:3004/#/projects/{project}/pipeline?environment=development"
        self.properties = {
            "stg_regions": {"comment": "Regions", "iceberg-data-platform.producer": "dbt",
                            "iceberg-data-platform.producer.url": url, "iceberg-data-platform.lineage.inputs": "[]",
                            "iceberg-data-platform.quality.status": "pass"},
            "regions": {"iceberg-data-platform.producer.url": "http://notebook/#x"},
        }
        self.commits = []

    def handler(self, request):
        assert request.headers["authorization"] == "Bearer token-write"
        if request.url.path.endswith("/v1/config"):
            return httpx.Response(200, json={"overrides": {"prefix": request.url.params["warehouse"]}})
        name = request.url.path.rsplit("/", 1)[-1]
        if name not in self.properties or "/v1/db-1111" not in request.url.path:  # all in sales
            return httpx.Response(404, json={"error": {"message": "no table"}})
        if request.method == "POST":
            self.commits.append((name, json.loads(request.content)))
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"metadata": {"table-uuid": "u-" + name, "properties": self.properties[name]}})


def delete(stack, user, id, **body):
    return stack.call("DELETE", f"/projects/{id}", user, json={"confirm_name": "shop", **body}).json()


def test_delete_forgets_the_project_and_its_table_properties(stack):
    id = create(stack)["id"]
    enable(stack)
    stack.launcher.published = {"tables": ["sales.stg_regions", "sales.regions", "sales.gone"], "errors": []}
    stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "wait": 30})
    catalog = FakeCatalog(id)
    stack.services.cleaner = Cleaner(httpx.AsyncClient(transport=httpx.MockTransport(catalog.handler)))
    assert delete(stack, "bob", id)["code"] == "forbidden_role"
    assert stack.call("DELETE", f"/projects/{id}", "alice", json={"confirm_name": "x"}).json()["code"] == \
        "confirmation_required"
    result = delete(stack, "alice", id)
    assert result["deleted"] and result["tables"]["failed"] == []
    assert {t["table"] for t in result["tables"]["cleaned"]} == {"sales.stg_regions"}
    # Only this project's dbt keys go; the table, its comment and other producers' tables stay.
    [(name, commit)] = catalog.commits
    assert name == "stg_regions" and commit["requirements"] == [{"type": "assert-table-uuid", "uuid": "u-stg_regions"}]
    assert commit["updates"] == [{"action": "remove-properties", "removals": [
        "iceberg-data-platform.lineage.inputs", "iceberg-data-platform.producer",
        "iceberg-data-platform.producer.url", "iceberg-data-platform.quality.status"]}]
    assert stack.call("GET", f"/projects/{id}", "alice").json()["code"] == "unknown_project"
    assert stack.services.store.runs(id) == []


def test_delete_stops_when_table_properties_stay_unless_forced(stack):
    id = create(stack)["id"]
    enable(stack)
    stack.launcher.published = {"tables": ["sales.stg_regions"], "errors": []}
    stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "wait": 30})
    stack.bridge.automation = []  # development was disabled since: no token to clean with
    refused = delete(stack, "alice", id)
    assert refused["code"] == "cleanup_failed"
    assert refused["detail"]["failed"] == [{"environment": "development", "table": "sales.stg_regions",
                                            "error": "environment_not_enabled"}]
    assert stack.call("GET", f"/projects/{id}", "alice").json()["name"] == "shop"
    forced = delete(stack, "alice", id, force=True)
    assert forced["deleted"] and len(forced["tables"]["failed"]) == 1


def test_preview_and_compile(stack):
    id = create(stack)["id"]
    enable(stack)
    preview = stack.call("POST", f"/projects/{id}/preview", "carol", json={"model": "stg_regions"}).json()
    assert preview["rows"] == [{"region_id": 1}]
    run = stack.launcher.started[preview["run"]]
    assert run["token"] == "token-read" and run["job"]["command"][:3] == ["show", "--select", "stg_regions"]
    compiled = stack.call("POST", f"/projects/{id}/compile", "carol", json={"select": "stg_regions"}).json()
    assert "model.shop.stg_regions" in compiled["compiled"]
    inline = stack.call("POST", f"/projects/{id}/compile", "carol", json={"sql": "select * from {{ ref('x') }}"}).json()
    assert stack.launcher.started[inline["run"]]["job"]["command"][:2] == ["compile", "--inline"]


def test_schedules_follow_roles_and_run(stack):
    id = create(stack)["id"]
    enable(stack)
    enable(stack, "production")
    assert stack.call("POST", f"/projects/{id}/schedules", "bob", json={
        "environment": "production", "cron": "0 6 * * *"}).json()["code"] == "forbidden_role"
    assert stack.call("POST", f"/projects/{id}/schedules", "bob", json={
        "environment": "development", "cron": "every day"}).json()["code"] == "invalid_cron"
    schedule = stack.call("POST", f"/projects/{id}/schedules", "bob", json={
        "environment": "development", "cron": "*/5 * * * *", "select": "tag:daily"}).json()
    assert schedule["next_run_at"] > 0
    stack.services.store.update_schedule(schedule["id"], next_run_at=0)
    from dbt_portal.scheduler import Scheduler

    started = stack.client.portal.call(Scheduler(stack.services).tick)
    assert len(started) == 1
    run = stack.services.store.run(started[0])
    assert (run["triggered_by"], run["schedule"], run["selector"]) == (f"schedule {schedule['id']}",
                                                                      schedule["id"], "tag:daily")
    assert stack.call("DELETE", f"/schedules/{schedule['id']}", "carol").json()["code"] == "forbidden_role"
    assert stack.call("DELETE", f"/schedules/{schedule['id']}", "bob").json()["deleted"]


def test_browser_writes_need_the_api_header(stack):
    response = stack.client.post("/api/v1/projects", json={"team": TEAM, "name": "shop"})
    assert (response.status_code, response.json()["code"]) == (403, "csrf")
    response = stack.client.post("/api/v1/projects", json={"team": TEAM, "name": "shop"},
                                 headers={"X-Iceberg-Dbt": "1"})
    assert response.json()["code"] == "unauthorized"


def test_every_operation_has_an_mcp_tool_and_the_contract_is_current(stack):
    schema = stack.app.openapi()
    operations = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert operations == set(OPERATIONS)
    mcp = stack.client.portal.call(_tools, stack.app)
    assert set(OPERATIONS.values()) == mcp
    contract = ROOT / "contracts" / "dbt-api" / "v1" / "openapi.yaml"
    assert json.loads(contract.read_text()) == json.loads(json.dumps(schema)), (
        "Regenerate extensions/dbt/contracts/dbt-api/v1/openapi.yaml with `uv run python -m scripts.api_contract`")


async def _tools(app):
    from dbt_portal.mcp_server import create_mcp

    services = app.state.services
    mcp = create_mcp(services.settings, services, None)
    return {tool.name for tool in await mcp.list_tools()}


def test_supports_the_platforms_bridge_contract():
    # The platform serves Bridge contract 0.1.x (contracts/bridge/COMPATIBILITY.md in the core).
    supported = contract_range()
    assert in_range("0.1.0", supported) and in_range("0.1.4", supported)
    assert not in_range("0.2.0", supported) and not in_range("1.0.0", supported)


def test_the_viewer_revalidates_its_files(stack):
    for path in ("/", "/app.js", "/style.css"):
        assert stack.client.get(path).headers["cache-control"] == "no-cache"
    assert stack.client.get("/fonts/doto.ttf").headers["cache-control"].startswith("public")


def test_the_pipeline_timeline_leaves_out_previews(stack):
    id = create(stack)["id"]
    enable(stack)
    stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "wait": 30})
    stack.call("POST", f"/projects/{id}/preview", "carol", json={"model": "stg_regions"})
    stack.call("POST", f"/projects/{id}/compile", "carol", json={"select": "stg_regions"})
    pipeline = stack.call("GET", f"/projects/{id}/pipeline", "carol").json()
    assert [r["command"] for r in pipeline["runs"]] == ["build"]
    runs = stack.call("GET", f"/projects/{id}/runs", "carol").json()
    assert {r["command"] for r in runs} == {"build", "show", "compile"}


def wait_for(stack, id, test):
    for _ in range(300):
        run = stack.call("GET", f"/runs/{id}", "carol").json()
        if test(run):
            return run
        time.sleep(0.01)
    raise AssertionError(f"{id} stayed {run['status']}")


def test_stopping_a_running_build_kills_it_and_marks_it_cancelled(stack):
    id = create(stack)["id"]
    enable(stack)
    stack.launcher.hold = True
    run = stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build"}).json()
    wait_for(stack, run["id"], lambda r: r["id"] in stack.launcher.started)
    assert stack.call("POST", f"/runs/{run['id']}/cancel", "carol").json()["code"] == "forbidden_role"
    assert stack.call("POST", f"/runs/{run['id']}/cancel", "bob").json()["cancelled"]
    done = wait_for(stack, run["id"], lambda r: r["status"] not in ("queued", "running"))
    assert done["status"] == "cancelled" and stack.launcher.killed == [run["id"]]


def test_a_stopped_build_that_waits_never_starts(stack):
    id = create(stack)["id"]
    enable(stack)
    stack.launcher.hold = True
    first = stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build"}).json()
    wait_for(stack, first["id"], lambda r: r["id"] in stack.launcher.started)
    # One build per project and environment at a time: the second waits for the first.
    second = stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build"}).json()
    stack.call("POST", f"/runs/{second['id']}/cancel", "bob")
    assert wait_for(stack, second["id"], lambda r: r["status"] != "queued")["status"] == "cancelled"
    stack.call("POST", f"/runs/{first['id']}/cancel", "bob")
    wait_for(stack, first["id"], lambda r: r["status"] == "cancelled")
    assert second["id"] not in stack.launcher.started


def test_a_build_stopped_while_the_launcher_starts_it_is_stopped_once_it_runs(stack):
    id = create(stack)["id"]
    enable(stack)
    stack.launcher.hold = True
    # The stop arrives while the launcher is still starting the container.
    stack.launcher.on_start = lambda run: stack.services.runs.stopping.add(run)
    run = stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build"}).json()
    done = wait_for(stack, run["id"], lambda r: r["status"] not in ("queued", "running"))
    assert done["status"] == "cancelled" and stack.launcher.killed == [run["id"]]


def test_where_dbt_is_enabled_is_visible_before_the_first_run(stack):
    def enabled():
        found = stack.call("GET", "/me", "carol").json()["enabledEnvironments"]
        return next(e["environments"] for e in found if e["team"] == TEAM)

    assert enabled() == []
    project = create(stack)
    assert "enable_environment" in project["hint"]
    enable(stack)
    assert enabled() == ["development"]
    second = stack.call("POST", "/projects", "bob", json={"team": TEAM, "name": "second"}).json()
    assert "hint" not in second


def test_development_is_shared_and_the_viewer_points_at_what_built_it(stack):
    id = create(stack)["id"]
    enable(stack)
    stack.call("POST", f"/projects/{id}/branches", "bob", json={"name": "wip"})
    wip = stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "ref": "wip", "wait": 30}).json()
    assert "Development is shared" in wip["hint"]
    # main has nothing to draw yet, so the viewer names the branches that do.
    main = stack.call("GET", f"/projects/{id}/pipeline?ref=main", "carol").json()
    assert main["refsWithPipeline"] == ["wip"] and "wip" in main["hint"]
    # A merge says main still needs a build before the viewer shows it.
    merged = stack.call("POST", f"/projects/{id}/merge", "alice", json={"branch": "wip"}).json()
    assert "Build main in development" in merged["hint"]
    built = stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "wait": 30}).json()
    assert "hint" not in built
    main = stack.call("GET", f"/projects/{id}/pipeline?ref=main", "carol").json()
    assert main["overwrittenBy"] is None
    assert {n["id"]: n["status"] for n in main["nodes"]}["source.shop.raw.events"] == "external"
    # A later build of the branch replaces main's tables, and main's pipeline says so.
    stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "ref": "wip", "wait": 30})
    main = stack.call("GET", f"/projects/{id}/pipeline?ref=main", "carol").json()
    assert main["overwrittenBy"]["ref"] == "wip"
    assert "hint" not in stack.call("POST", f"/projects/{id}/merge", "alice", json={"branch": "wip"}).json()
