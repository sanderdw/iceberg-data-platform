import json
import re
import time
from datetime import datetime

import httpx
import pytest

from dbt_portal.api import OPERATIONS
from dbt_portal.bridge_client import in_range
from dbt_portal.cleanup import Cleaner
from dbt_portal.config import ROOT, contract_range
from dbt_portal.errors import DbtError
from tests.conftest import DATABASES, OTHER, TEAM, ToolRefused

TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
RUN_FIELDS = {"id", "project", "environment", "status", "triggeredBy", "agent", "scheduled", "schedule", "command",
              "select", "ref", "revision", "createdAt", "startedAt", "finishedAt", "exitCode", "summary", "error"}
SCHEDULE_FIELDS = {"id", "project", "environment", "cron", "command", "select", "ref", "enabled", "nextRunAt",
                   "lastRun", "createdBy", "createdAt"}
PROJECT_FIELDS = {"id", "team", "name", "description", "defaultDatabase", "createdAt", "createdBy", "role", "url"}


def seconds(timestamp):
    assert TIMESTAMP.match(timestamp), timestamp
    return datetime.fromisoformat(timestamp).timestamp()


def enable(stack, environment="development"):
    assert stack.call("POST", f"/teams/{TEAM}/environments/{environment}").status_code == 200


def create(stack, user="bob", name="shop"):
    response = stack.call("POST", "/projects", user, json={"team": TEAM, "name": name, "defaultDatabase": "sales"})
    assert response.status_code == 201, response.text
    return response.json()


def build(stack, id, user="bob", **body):
    """A run that is waited for, as the API answers it: 202 and the run in the shape of get_run."""
    response = stack.call("POST", f"/projects/{id}/runs", user, json={"wait": 30, **body})
    assert response.status_code == 202, response.text
    return response.json()


def test_whoami_and_environments(stack):
    body = stack.call("GET", "/me", "carol").json()
    assert body["user"] == {"id": "portal-" + "carol".ljust(32, "0"), "name": "carol"} and body["extension"] == "dbt"
    # Exactly the shared keys: what the Bridge knows beyond them (sharedDatabases) is not passed on.
    assert set(body) == {"user", "memberships", "databases", "extension", "origin", "environments",
                         "enabledEnvironments", "userPortalUrl", "apps"}
    assert body["memberships"] == [{"team": TEAM, "teamName": TEAM[:9], "role": "reader"}]
    assert body["databases"][0] == {"id": DATABASES["sales", "development"], "name": "sales", "team": TEAM,
                                    "environment": "development", "status": "ready"}
    assert body["environments"] == ["development", "acceptance", "production"]
    assert body["apps"] == {"workspace": [{"id": "portal", "title": "User portal", "url": "http://localhost:3002"}],
                            "extensions": []}
    assert stack.call("GET", "/me", "nobody").json()["code"] == "unauthorized"
    denied = stack.call("POST", f"/teams/{TEAM}/environments/development", "bob")
    assert (denied.status_code, denied.json()["code"]) == (403, "forbidden_role")
    enabled = stack.call("POST", f"/teams/{TEAM}/environments/development")
    development = {"team": TEAM, "environment": "development", "enabled": True, "enabledBy": "alice",
                   "databases": [{"id": DATABASES["sales", "development"], "name": "sales"},
                                 {"id": DATABASES["raw-data", "development"], "name": "raw-data"}]}
    # Enabling answers 200 with the environment, never the service account behind it.
    assert enabled.status_code == 200 and enabled.json() == development
    environments = stack.call("GET", f"/teams/{TEAM}/environments", "carol").json()
    assert list(environments) == ["environments"]
    assert environments["environments"] == [
        development,
        {"team": TEAM, "environment": "acceptance", "enabled": False, "enabledBy": None, "databases": []},
        {"team": TEAM, "environment": "production", "enabled": False, "enabledBy": None,
         "databases": [{"id": DATABASES["sales", "production"], "name": "sales"}]}]
    assert stack.call("GET", f"/teams/{OTHER}/environments", "carol").json()["code"] == "not_a_member"
    disabled = stack.call("DELETE", f"/teams/{TEAM}/environments/development")
    assert disabled.status_code == 200 and disabled.json() == {**development, "enabled": False, "enabledBy": None}
    again = stack.call("DELETE", f"/teams/{TEAM}/environments/development")
    assert (again.status_code, again.json()["code"]) == (409, "environment_not_enabled")


def test_project_creation_starts_from_an_empty_project(stack):
    assert stack.call("POST", "/projects", "carol", json={"team": TEAM, "name": "shop"}).json()["code"] == \
        "forbidden_role"
    assert stack.call("POST", "/projects", "bob", json={"team": TEAM, "name": "Shop"}).json()["code"] == "invalid_name"
    wrong = stack.call("POST", "/projects", "bob", json={"team": TEAM, "name": "shop", "defaultDatabase": "nope"})
    assert (wrong.status_code, wrong.json()["code"]) == (422, "invalid_database")
    assert "sales" in wrong.json()["detail"]["databases"]
    old = stack.call("POST", "/projects", "bob", json={"team": TEAM, "name": "shop", "default_database": "sales"})
    assert (old.status_code, old.json()["code"]) == (422, "invalid_request")
    project = create(stack)
    assert set(project) == PROJECT_FIELDS | {"hint"}
    assert (project["defaultDatabase"], project["createdBy"], project["role"]) == ("sales", "bob", "writer")
    assert project["url"] == f"http://localhost:3004/#/projects/{project['id']}/pipeline"
    assert time.time() - seconds(project["createdAt"]) < 60
    taken = stack.call("POST", "/projects", "bob", json={"team": TEAM, "name": "shop"})
    assert (taken.status_code, taken.json()["code"]) == (409, "name_taken")
    assert stack.call("POST", "/projects", "bob", json={"team": TEAM, "name": "other"}).json()[
        "defaultDatabase"] == "raw-data"
    listed = stack.call("GET", "/projects", "carol").json()
    assert list(listed) == ["projects"] and [p["name"] for p in listed["projects"]] == ["other", "shop"]
    assert set(listed["projects"][1]) == PROJECT_FIELDS and listed["projects"][1]["role"] == "reader"
    files = stack.call("GET", f"/projects/{project['id']}/files", "carol").json()
    paths = {f["path"] for f in files["files"]}
    # No example pipeline: only the project file, its README and the namespace macro.
    assert paths == {"dbt_project.yml", "README.md", "macros/generate_schema_name.sql"}
    config = stack.call("GET", f"/projects/{project['id']}/files/dbt_project.yml", "carol").json()["content"]
    assert "name: shop" in config and "+catalog_name: sales" in config and "{{" not in config
    # Other teams do not even see it.
    assert stack.call("GET", f"/projects/{project['id']}", "dave").json()["code"] == "unknown_project"
    assert stack.call("GET", "/projects", "dave").json() == {"projects": []}


def test_branches_commits_and_protected_main(stack):
    id = create(stack)["id"]
    blocked = stack.call("POST", f"/projects/{id}/commits", "bob",
                         json={"branch": "main", "files": {"a.sql": "select 1"}, "message": "x"})
    assert blocked.json()["code"] == "protected_branch"
    assert stack.call("POST", f"/projects/{id}/branches", "carol", json={"name": "feature"}).json()["code"] == \
        "forbidden_role"
    head = stack.call("POST", f"/projects/{id}/branches", "bob", json={"name": "feature"}).json()["revision"]
    commit = stack.call("POST", f"/projects/{id}/commits", "bob", json={
        "branch": "feature", "message": "Add orders", "expectedRevision": head,
        "files": {"models/marts/orders.sql": "select 1 as id", "README.md": None}})
    assert commit.status_code == 201, commit.text
    stale = stack.call("POST", f"/projects/{id}/commits", "bob", json={
        "branch": "feature", "message": "Again", "expectedRevision": head, "files": {"x.sql": "select 1"}})
    assert (stale.status_code, stale.json()["code"]) == (409, "revision_conflict")
    assert stale.json()["detail"] == {"head": commit.json()["revision"]}
    old = stack.call("POST", f"/projects/{id}/commits", "bob", json={
        "branch": "feature", "message": "Again", "expected_revision": head, "files": {"x.sql": "select 1"}})
    assert (old.status_code, old.json()["code"]) == (422, "invalid_request")
    twice = stack.call("POST", f"/projects/{id}/branches", "bob", json={"name": "feature"})
    assert (twice.status_code, twice.json()["code"]) == (409, "name_taken")
    branches = stack.call("GET", f"/projects/{id}/branches", "carol").json()
    assert list(branches) == ["branches"] and [b["name"] for b in branches["branches"]] == ["main", "feature"]
    assert branches["branches"][1] == {"name": "feature", "revision": commit.json()["revision"],
                                       "subject": "Add orders", "committedAt": branches["branches"][1]["committedAt"]}
    assert time.time() - seconds(branches["branches"][1]["committedAt"]) < 60
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
    assert list(history) == ["commits"]
    history = history["commits"]
    assert history[0]["subject"] == "Merge feature into main"
    assert set(history[0]) == {"revision", "author", "subject", "committedAt"} and seconds(history[0]["committedAt"])
    assert {"subject": "Add orders", "author": "bob (via agent)"}.items() <= next(
        h for h in history if h["subject"] == "Add orders").items()
    assert len(stack.call("GET", f"/projects/{id}/history?limit=1", "carol").json()["commits"]) == 1
    assert stack.call("GET", f"/projects/{id}/history?limit=201", "carol").json()["code"] == "invalid_request"
    protected = stack.call("DELETE", f"/projects/{id}/branches/main", "bob")
    assert (protected.status_code, protected.json()["code"]) == (403, "protected_branch")
    assert stack.call("DELETE", f"/projects/{id}/branches/feature", "bob").json() == {
        "deleted": True, "branch": "feature"}


def test_runs_use_the_right_token_and_follow_promotion_rules(stack):
    id = create(stack)["id"]
    assert stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build"}).json()["code"] == \
        "environment_not_enabled"
    enable(stack)
    enable(stack, "production")
    denied = stack.call("POST", f"/projects/{id}/runs", "carol", json={"command": "build"})
    assert denied.json()["code"] == "forbidden_role"
    old = stack.call("POST", f"/projects/{id}/runs", "bob", json={"full_refresh": True})
    assert (old.status_code, old.json()["code"]) == (422, "invalid_request")
    assert stack.call("POST", f"/projects/{id}/runs", "bob", json={"wait": 601}).json()["code"] == "invalid_request"
    run = build(stack, id, command="build", select="stg_regions+", fullRefresh=True)
    assert run["status"] == "success", run
    # The run a start returns is the run get_run returns: the same keys, with its results.
    assert set(run) == RUN_FIELDS | {"results"}
    assert (run["project"], run["triggeredBy"], run["agent"]) == (id, "bob", "ext-dbt-mcp")
    assert (run["scheduled"], run["schedule"], run["error"], run["exitCode"]) == (False, None, None, 0)
    assert (run["command"], run["select"], run["ref"]) == ("build", "stg_regions+", "main")
    assert seconds(run["createdAt"]) <= seconds(run["startedAt"]) <= seconds(run["finishedAt"])
    # Neither the account the run acted as nor the kind of token it got is shown.
    assert "principal" not in run["summary"] and "access" not in run
    assert run["summary"]["counts"] == {"success": 1, "pass": 1} and run["summary"]["catalogs"]
    assert {"node": "model.shop.stg_regions", "status": "success", "executionTime": 0.2, "message": "",
            "rowsAffected": 3} in run["results"]
    assert stack.services.store.run(run["id"])["access"] == "write"
    started = stack.launcher.started[run["id"]]
    assert started["timeout"] == 2700
    assert started["token"] == "token-write"
    assert started["job"]["command"] == ["build", "--select", "stg_regions+", "--full-refresh"]
    assert started["job"]["forward"] == {"listen": 9000, "target": "rustfs:9000"}
    assert started["job"]["publish"]["catalogs"] == {"raw_data": "db-" + "3" * 32, "sales": "db-" + "1" * 32}
    # A reader may test and compile, with a read token only.
    test = build(stack, id, "carol", command="test")
    assert stack.launcher.started[test["id"]]["token"] == "token-read"
    assert stack.launcher.started[test["id"]]["job"]["publish"] is None
    # Production only builds main.
    branch = stack.call("POST", f"/projects/{id}/branches", "bob", json={"name": "wip"}).json()
    stack.call("POST", f"/projects/{id}/commits", "bob", json={"branch": "wip", "message": "wip",
                                                              "files": {"models/x.sql": "select 1"}})
    blocked = stack.call("POST", f"/projects/{id}/runs", "bob", json={"command": "build", "environment": "production",
                                                                       "ref": "wip"})
    assert blocked.json()["code"] == "main_required", branch
    # Without a body: a build of main in development. Without wait the answer does not wait for the run.
    started = stack.call("POST", f"/projects/{id}/runs", "bob")
    assert started.status_code == 202 and set(started.json()) == RUN_FIELDS | {"results"}
    assert (started.json()["command"], started.json()["environment"], started.json()["ref"]) == (
        "build", "development", "main")
    production = stack.call("POST", f"/projects/{id}/runs", "bob", json={
        "command": "build", "environment": "production", "ref": "main"})
    assert production.status_code == 202 and production.json()["status"] in ("queued", "running", "success")
    assert production.json()["results"] == [] or production.json()["status"] == "success"
    detail = stack.call("GET", f"/runs/{run['id']}", "carol").json()
    assert detail == run
    assert stack.call("GET", f"/runs/{run['id']}?wait=5", "carol").json() == run
    assert stack.call("GET", f"/runs/{run['id']}?wait=601", "carol").json()["code"] == "invalid_request"
    assert stack.call("GET", f"/runs/{run['id']}/logs", "carol").json() == {
        "run": run["id"], "status": "success", "lines": ["Finished 'build' successfully"]}
    assert stack.call("GET", f"/runs/{run['id']}/logs?limit=5001", "carol").json()["code"] == "invalid_request"
    # A run of another team is unknown, as its project is.
    for path in (f"/runs/{run['id']}", f"/runs/{run['id']}/logs"):
        other = stack.call("GET", path, "dave")
        assert (other.status_code, other.json()["code"]) == (404, "unknown_run")
    finished = stack.call("POST", f"/runs/{run['id']}/cancel", "bob")
    assert (finished.status_code, finished.json()["code"]) == (409, "run_finished")


def test_runs_are_listed_newest_first_without_results(stack):
    id = create(stack)["id"]
    enable(stack)
    first = build(stack, id, command="build")
    second = build(stack, id, "carol", command="test")
    listed = stack.call("GET", f"/projects/{id}/runs", "carol").json()
    assert list(listed) == ["runs"] and [r["id"] for r in listed["runs"]] == [second["id"], first["id"]]
    assert all(set(r) == RUN_FIELDS for r in listed["runs"])
    assert listed["runs"][1] == {k: v for k, v in first.items() if k != "results"}
    assert [r["id"] for r in stack.call("GET", f"/projects/{id}/runs?limit=1", "carol").json()["runs"]] == [
        second["id"]]
    assert stack.call("GET", f"/projects/{id}/runs?environment=production", "carol").json() == {"runs": []}
    for limit in (0, 201):
        refused = stack.call("GET", f"/projects/{id}/runs?limit={limit}", "carol")
        assert (refused.status_code, refused.json()["code"]) == (422, "invalid_request")
    # The project shows the newest run per environment, its environments and its schedules in the shared shapes.
    project = stack.call("GET", f"/projects/{id}", "carol").json()
    assert set(project) == PROJECT_FIELDS | {"branches", "environments", "latestRuns", "schedules"}
    assert project["latestRuns"] == {"development": listed["runs"][0]}
    assert project["environments"] == stack.call("GET", f"/teams/{TEAM}/environments", "carol").json()["environments"]
    assert project["branches"] == stack.call("GET", f"/projects/{id}/branches", "carol").json()["branches"]
    assert project["schedules"] == []


def test_staged_run_contains_generated_profiles_and_platform_macros(stack):
    id = create(stack)["id"]
    enable(stack)
    run = build(stack, id, command="build")
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
    run = build(stack, id, command="build")
    pipeline = stack.call("GET", f"/projects/{id}/pipeline", "carol").json()
    # The timeline's runs are runs as list_runs shows them.
    assert pipeline["runs"] == stack.call("GET", f"/projects/{id}/runs", "carol").json()["runs"]
    assert pipeline["structureRun"] == run["id"] and pipeline["revision"] == run["revision"]
    nodes = {n["id"]: n for n in pipeline["nodes"]}
    assert set(nodes) == {"seed.shop.regions", "model.shop.stg_regions", "model.shop.regions_per_country",
                          "source.shop.raw.events"}
    staging = nodes["model.shop.stg_regions"]
    assert (staging["status"], staging["tests"]["pass"], staging["databaseId"]) == ("success", 1, "db-" + "1" * 32)
    assert staging["lastRun"] == run["id"] and seconds(staging["lastRunAt"]) == seconds(run["finishedAt"])
    assert nodes["model.shop.regions_per_country"]["status"] == "never_run"
    assert nodes["model.shop.regions_per_country"]["lastRunAt"] is None
    assert "sql" not in staging
    assert {"from": "model.shop.stg_regions", "to": "model.shop.regions_per_country"} in pipeline["edges"]
    assert not any(e["from"].startswith("macro.") for e in pipeline["edges"])
    detail = stack.call("GET", f"/projects/{id}/nodes/model.shop.stg_regions", "carol").json()
    assert detail["columns"][0] == {"name": "region_id", "type": "INTEGER", "description": "Stable id", "index": 0}
    assert detail["testResults"][0]["status"] == "pass" and detail["compiledSql"]
    assert seconds(detail["lastRunAt"]) == seconds(run["finishedAt"])
    unknown = stack.call("GET", f"/projects/{id}/nodes/model.shop.nope", "carol")
    assert (unknown.status_code, unknown.json()["code"]) == (404, "unknown_node")
    lineage = stack.call("GET", f"/projects/{id}/lineage/model.shop.regions_per_country", "carol").json()
    assert {n["id"] for n in lineage["upstream"]} == {"model.shop.stg_regions", "seed.shop.regions",
                                                       "source.shop.raw.events"}
    assert stack.call("GET", f"/projects/{id}/lineage/model.shop.stg_regions?depth=21", "carol").json()["code"] == \
        "invalid_request"


def test_pipeline_of_one_branch(stack):
    id = create(stack)["id"]
    enable(stack)
    stack.call("POST", f"/projects/{id}/branches", "bob", json={"name": "wip"})
    build(stack, id, command="build", ref="wip")
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


def delete(stack, user, id, **query):
    return stack.call("DELETE", f"/projects/{id}", user, params={"confirmName": "shop", **query}).json()


def test_delete_forgets_the_project_and_its_table_properties(stack):
    id = create(stack)["id"]
    enable(stack)
    stack.launcher.published = {"tables": ["sales.stg_regions", "sales.regions", "sales.gone"], "errors": []}
    build(stack, id, command="build")
    catalog = FakeCatalog(id)
    stack.services.cleaner = Cleaner(httpx.AsyncClient(transport=httpx.MockTransport(catalog.handler)))
    assert delete(stack, "bob", id)["code"] == "forbidden_role"
    wrong = stack.call("DELETE", f"/projects/{id}", "alice", params={"confirmName": "x"})
    assert (wrong.status_code, wrong.json()["code"]) == (422, "confirmation_mismatch")
    missing = stack.call("DELETE", f"/projects/{id}", "alice")
    assert (missing.status_code, missing.json()["code"]) == (422, "invalid_request")
    # The name in a JSON body, as before, is no confirmation.
    body = stack.call("DELETE", f"/projects/{id}", "alice", json={"confirm_name": "shop"})
    assert (body.status_code, body.json()["code"]) == (422, "invalid_request")
    assert stack.call("GET", f"/projects/{id}", "alice").json()["name"] == "shop"
    result = delete(stack, "alice", id)
    assert set(result) == {"deleted", "project", "tables"} and result["project"] == id
    assert result["deleted"] is True and result["tables"]["failed"] == []
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
    build(stack, id, command="build")
    stack.bridge.automation = []  # development was disabled since: no token to clean with
    refused = delete(stack, "alice", id)
    assert refused["code"] == "cleanup_failed"
    assert refused["detail"]["failed"] == [{"environment": "development", "table": "sales.stg_regions",
                                            "error": "environment_not_enabled"}]
    assert stack.call("GET", f"/projects/{id}", "alice").json()["name"] == "shop"
    forced = delete(stack, "alice", id, force="true")
    assert forced["deleted"] and len(forced["tables"]["failed"]) == 1


def test_a_project_with_a_run_going_is_not_deleted(stack):
    id = create(stack)["id"]
    enable(stack)
    stack.launcher.hold = True
    run = stack.call("POST", f"/projects/{id}/runs", "bob").json()
    refused = stack.call("DELETE", f"/projects/{id}", "alice", params={"confirmName": "shop"})
    assert (refused.status_code, refused.json()["code"]) == (409, "run_in_progress")
    assert stack.call("POST", f"/runs/{run['id']}/cancel", "bob").status_code == 200
    wait_for(stack, run["id"], lambda r: r["status"] == "cancelled")


def test_preview_and_compile(stack):
    id = create(stack)["id"]
    enable(stack)
    preview = stack.call("POST", f"/projects/{id}/preview", "carol", json={"model": "stg_regions"}).json()
    assert preview["rows"] == [{"region_id": 1}]
    run = stack.launcher.started[preview["run"]]
    assert run["token"] == "token-read" and run["job"]["command"][:3] == ["show", "--select", "stg_regions"]
    compiled = stack.call("POST", f"/projects/{id}/compile", "carol", json={"select": "stg_regions"}).json()
    assert "model.shop.stg_regions" in compiled["compiled"]
    assert set(compiled) == {"run", "status", "compiled", "output", "error"}
    assert compiled["status"] == "success" and compiled["error"] is None
    inline = stack.call("POST", f"/projects/{id}/compile", "carol", json={"sql": "select * from {{ ref('x') }}"}).json()
    assert stack.launcher.started[inline["run"]]["job"]["command"][:2] == ["compile", "--inline"]
    assert stack.call("POST", f"/projects/{id}/preview", "carol", json={
        "model": "stg_regions", "limit": 101}).json()["code"] == "invalid_request"


def test_schedules_follow_roles_and_run(stack):
    id = create(stack)["id"]
    enable(stack)
    enable(stack, "production")
    assert stack.call("POST", f"/projects/{id}/schedules", "bob", json={
        "environment": "production", "cron": "0 6 * * *"}).json()["code"] == "forbidden_role"
    assert stack.call("POST", f"/projects/{id}/schedules", "bob", json={
        "environment": "development", "cron": "every day"}).json()["code"] == "invalid_cron"
    # At most every 5 minutes.
    often = stack.call("POST", f"/projects/{id}/schedules", "bob", json={
        "environment": "development", "cron": "*/4 * * * *"})
    assert (often.status_code, often.json()["code"]) == (422, "invalid_cron")
    created = stack.call("POST", f"/projects/{id}/schedules", "bob", json={
        "environment": "development", "cron": "*/5 * * * *", "select": "tag:daily"})
    schedule = created.json()
    assert created.status_code == 201 and set(schedule) == SCHEDULE_FIELDS
    assert schedule == {**schedule, "project": id, "environment": "development", "cron": "*/5 * * * *",
                        "command": "build", "select": "tag:daily", "ref": "main", "enabled": True, "lastRun": None,
                        "createdBy": "bob"}
    assert 0 < seconds(schedule["nextRunAt"]) - time.time() <= 300 and seconds(schedule["createdAt"])
    assert stack.call("GET", f"/projects/{id}/schedules", "carol").json() == {"schedules": [schedule]}
    assert stack.call("GET", f"/projects/{id}", "carol").json()["schedules"] == [schedule]
    stack.services.store.update_schedule(schedule["id"], next_run_at=0)
    from dbt_portal.scheduler import Scheduler

    # The app's own scheduler ticks too: either of the two starts the due run, and at worst both do.
    stack.client.portal.call(Scheduler(stack.services).tick)
    scheduled = [r["id"] for r in stack.call("GET", f"/projects/{id}/runs", "carol").json()["runs"] if r["scheduled"]]
    assert 1 <= len(scheduled) <= 2
    run = wait_for(stack, scheduled[-1], lambda r: r["status"] == "success")
    # A scheduled run says so, and names its schedule; nobody triggered it.
    assert (run["triggeredBy"], run["scheduled"], run["schedule"], run["agent"], run["select"]) == (
        "schedule", True, schedule["id"], None, "tag:daily")
    assert stack.services.store.run(run["id"])["triggered_by"] == f"schedule {schedule['id']}"
    last = stack.call("GET", f"/projects/{id}/schedules", "carol").json()["schedules"][0]["lastRun"]
    assert last in scheduled
    path = f"/schedules/{schedule['id']}"
    # A change merges: what is left out stays.
    paused = stack.call("PATCH", path, "bob", json={"enabled": False})
    assert paused.status_code == 200
    assert paused.json() == {**schedule, "enabled": False, "nextRunAt": None, "lastRun": last}
    changed = stack.call("PATCH", path, "bob", json={"cron": "0 6 * * *", "select": "", "enabled": True}).json()
    assert (changed["cron"], changed["select"], changed["enabled"], changed["command"], changed["ref"]) == (
        "0 6 * * *", "", True, "build", "main")
    assert seconds(changed["nextRunAt"]) > time.time() and changed["createdAt"] == schedule["createdAt"]
    assert stack.call("PATCH", path, "bob", json={"cron": "* * * * *"}).json()["code"] == "invalid_cron"
    # A schedule saved before the minimum existed keeps its cron until someone changes it: it can still be paused.
    stack.services.store.update_schedule(schedule["id"], cron="* * * * *")
    legacy = stack.call("PATCH", path, "bob", json={"enabled": False})
    assert legacy.status_code == 200 and (legacy.json()["cron"], legacy.json()["enabled"]) == ("* * * * *", False)
    assert stack.call("PATCH", path, "bob", json={"cron": "*/2 * * * *"}).json()["code"] == "invalid_cron"
    changed = stack.call("PATCH", path, "bob", json={"cron": "0 6 * * *", "enabled": True}).json()
    assert stack.call("PATCH", path, "bob", json={"environment": "production"}).json()["code"] == "forbidden_role"
    assert stack.call("PATCH", path, "carol", json={"enabled": False}).json()["code"] == "forbidden_role"
    assert stack.call("PATCH", path, "bob", json={"ref": "nope"}).json()["code"] == "unknown_ref"
    assert stack.call("GET", f"/projects/{id}/schedules", "carol").json() == {"schedules": [changed]}
    # The old address and method are gone.
    gone = stack.call("PUT", f"/projects/{id}/schedules/{schedule['id']}", "bob", json={
        "environment": "development", "cron": "0 6 * * *"})
    assert (gone.status_code, gone.json()["code"]) == (404, "not_found")
    # A schedule that does not exist and one of another team are the same to the caller.
    for user, address in (("bob", "/schedules/sch-" + "0" * 24), ("dave", path)):
        unknown = stack.call("PATCH", address, user, json={"enabled": False})
        assert (unknown.status_code, unknown.json()["code"]) == (404, "unknown_schedule")
    assert stack.call("DELETE", path, "dave").json()["code"] == "unknown_schedule"
    assert stack.call("DELETE", path, "carol").json()["code"] == "forbidden_role"
    assert stack.call("DELETE", path, "bob").json() == {"deleted": True, "schedule": schedule["id"]}
    assert stack.call("DELETE", path, "bob").json()["code"] == "unknown_schedule"


def test_browser_writes_need_the_api_header(stack):
    response = stack.client.post("/api/v1/projects", json={"team": TEAM, "name": "shop"})
    assert (response.status_code, response.json()["code"]) == (403, "csrf")
    response = stack.client.post("/api/v1/projects", json={"team": TEAM, "name": "shop"},
                                 headers={"X-Iceberg-Dbt": "1"})
    assert response.json()["code"] == "unauthorized"


def test_every_error_has_the_same_body(stack, monkeypatch):
    def refused(response, status, code):
        body = response.json()
        assert response.status_code == status and body["code"] == code, response.text
        assert isinstance(body["error"], str) and body["error"] and set(body) <= {"error", "code", "detail"}

    refused(stack.call("GET", "/nothing-here"), 404, "not_found")
    refused(stack.client.get("/nothing/here"), 404, "not_found")
    refused(stack.call("PUT", "/me"), 405, "method_not_allowed")
    refused(stack.call("GET", "/me", "mallory"), 401, "unauthorized")
    refused(stack.call("GET", "/projects/prj-" + "0" * 24), 404, "unknown_project")
    refused(stack.call("GET", "/projects/not-an-id"), 422, "invalid_request")
    refused(stack.call("GET", "/runs/run-" + "0" * 24), 404, "unknown_run")
    refused(stack.call("POST", "/runs/run-" + "0" * 24 + "/cancel"), 404, "unknown_run")
    refused(stack.call("DELETE", "/schedules/sch-" + "0" * 24), 404, "unknown_schedule")
    id = create(stack)["id"]
    refused(stack.call("GET", f"/projects/{id}/runs?environment=test"), 422, "invalid_request")
    refused(stack.call("GET", f"/projects/{id}/files?ref=nope"), 404, "unknown_ref")
    refused(stack.call("GET", f"/projects/{id}/files/nope.sql"), 404, "unknown_file")
    refused(stack.call("POST", f"/projects/{id}/runs", json={"select": "x;y"}), 422, "invalid_selector")
    refused(stack.call("POST", f"/projects/{id}/runs"), 409, "environment_not_enabled")

    async def broken(caller):
        raise RuntimeError("boom")
    monkeypatch.setattr(stack.services, "whoami", broken)
    refused(stack.call("GET", "/me"), 500, "internal_error")
    assert DbtError(418, "x").code == "internal_error" and DbtError(405, "x").code == "method_not_allowed"
    assert DbtError(500, "x").code == "internal_error" and DbtError(504, "x").code == "timeout"


def test_tools_return_what_the_api_returns(stack):
    """An MCP tool gives the REST operation's body, unchanged and without a wrapper of its own."""
    enable(stack)
    project = stack.tool("create_project", "bob", team=TEAM, name="shop", default_database="sales")
    id = project["id"]
    assert project["defaultDatabase"] == "sales" and "default_database" not in project
    stack.tool("create_branch", "bob", project=id, name="wip")
    head = stack.tool("list_files", "bob", project=id, ref="wip")["revision"]
    commit = stack.tool("write_files", "bob", project=id, branch="wip", message="Add x",
                        files={"models/x.sql": "select 1"}, expected_revision=head)
    assert commit["branch"] == "wip" and commit["revision"] != head
    with pytest.raises(ToolRefused, match=r"\[revision_conflict\]"):
        stack.tool("write_files", "bob", project=id, branch="wip", message="Again",
                   files={"models/y.sql": "select 1"}, expected_revision=head)
    with pytest.raises(ToolRefused, match=r"\[name_taken\]"):
        stack.tool("create_branch", "bob", project=id, name="wip")
    with pytest.raises(ToolRefused, match=r"\[name_taken\]"):
        stack.tool("create_project", "bob", team=TEAM, name="shop")
    with pytest.raises(ToolRefused, match=r"\[invalid_database\]"):
        stack.tool("create_project", "bob", team=TEAM, name="other", default_database="nope")
    run = stack.tool("run_project", "bob", project=id, ref="wip", select="stg_regions+", full_refresh=True, wait=30)
    assert run["status"] == "success" and set(run) == RUN_FIELDS | {"results", "hint"}
    assert stack.launcher.started[run["id"]]["job"]["command"][-1] == "--full-refresh"
    # Without wait the tool answers at once, with the run as it is then.
    assert set(stack.tool("run_project", "carol", project=id, command="test")) == RUN_FIELDS | {"results"}
    schedule = stack.tool("create_schedule", "bob", project=id, environment="development", cron="0 6 * * *",
                          select="tag:daily")
    assert set(schedule) == SCHEDULE_FIELDS and schedule["select"] == "tag:daily"
    changed = stack.tool("update_schedule", "bob", schedule=schedule["id"], enabled=False)
    assert changed == {**schedule, "enabled": False, "nextRunAt": None}
    assert stack.tool("update_schedule", "bob", schedule=schedule["id"], cron="30 6 * * *", enabled=True) == {
        **schedule, "cron": "30 6 * * *", "nextRunAt": stack.tool("list_schedules", project=id)["schedules"][0][
            "nextRunAt"]}
    with pytest.raises(ToolRefused, match=r"\[invalid_cron\]"):
        stack.tool("create_schedule", "bob", project=id, environment="development", cron="* * * * *")
    hint = {"hint": run["hint"]}
    node = "model.shop.stg_regions"
    for tool, arguments, path in (
            ("whoami", {}, "/me"),
            ("list_environments", {"team": TEAM}, f"/teams/{TEAM}/environments"),
            ("list_projects", {}, "/projects"),
            ("get_project", {"project": id}, f"/projects/{id}"),
            ("list_files", {"project": id, "ref": "wip"}, f"/projects/{id}/files?ref=wip"),
            ("read_file", {"project": id, "path": "models/x.sql", "ref": "wip"},
             f"/projects/{id}/files/models/x.sql?ref=wip"),
            ("list_branches", {"project": id}, f"/projects/{id}/branches"),
            ("get_history", {"project": id, "ref": "wip", "limit": 1}, f"/projects/{id}/history?ref=wip&limit=1"),
            ("get_diff", {"project": id, "head": "wip"}, f"/projects/{id}/diff?head=wip"),
            ("list_runs", {"project": id}, f"/projects/{id}/runs"),
            ("list_runs", {"project": id, "environment": "development", "limit": 1},
             f"/projects/{id}/runs?environment=development&limit=1"),
            ("get_run", {"run": run["id"]}, f"/runs/{run['id']}"),
            ("get_run", {"run": run["id"], "wait": 5}, f"/runs/{run['id']}?wait=5"),
            ("get_run_logs", {"run": run["id"], "limit": 1}, f"/runs/{run['id']}/logs?limit=1"),
            ("list_schedules", {"project": id}, f"/projects/{id}/schedules"),
            ("get_pipeline_graph", {"project": id}, f"/projects/{id}/pipeline"),
            ("get_pipeline_graph", {"project": id, "ref": "wip"}, f"/projects/{id}/pipeline?ref=wip"),
            ("get_node", {"project": id, "node": node}, f"/projects/{id}/nodes/{node}"),
            ("get_lineage", {"project": id, "node": node, "depth": 1}, f"/projects/{id}/lineage/{node}?depth=1")):
        wait_for(stack, stack.services.store.runs(id)[0]["id"], lambda r: r["status"] == "success")
        assert stack.tool(tool, "carol", **arguments) == stack.call("GET", path, "carol").json(), tool
    assert list(stack.tool("list_projects")) == ["projects"] and list(stack.tool("list_runs", project=id)) == ["runs"]
    assert list(stack.tool("list_branches", project=id)) == ["branches"]
    assert list(stack.tool("get_history", project=id)) == ["commits"]
    assert list(stack.tool("list_schedules", project=id)) == ["schedules"]
    assert list(stack.tool("list_environments", team=TEAM)) == ["environments"]
    # The hint a start gives belongs to that answer; the run itself is the same.
    assert stack.tool("get_run", run=run["id"]) == {k: v for k, v in run.items() if k not in hint}
    assert stack.tool("get_run_logs", run=run["id"]) == {"run": run["id"], "status": "success",
                                                         "lines": ["Finished 'build' successfully"]}
    compiled = stack.tool("compile_project", "carol", project=id, select="stg_regions")
    assert compiled["status"] == "success" and node in compiled["compiled"] and compiled["error"] is None
    assert stack.tool("preview_model", "carol", project=id, model="stg_regions", limit=5)["rows"] == [
        {"region_id": 1}]
    with pytest.raises(ToolRefused, match=r"\[run_finished\]"):
        stack.tool("cancel_run", "bob", run=run["id"])
    with pytest.raises(ToolRefused, match=r"\[unknown_run\]"):
        stack.tool("get_run", "dave", run=run["id"])
    stack.launcher.hold = True
    held = stack.tool("run_project", "bob", project=id)
    assert stack.tool("cancel_run", "bob", run=held["id"]) == {"run": held["id"], "cancelling": True}
    with pytest.raises(ToolRefused, match=r"\[run_in_progress\]|\[confirmation_mismatch\]"):
        stack.tool("delete_project", project=id, confirm_name="nope")
    wait_for(stack, held["id"], lambda r: r["status"] == "cancelled")
    stack.launcher.hold = False
    merged = stack.tool("merge_to_main", project=id, branch="wip", revision=commit["revision"])
    assert merged["merged"] is True
    assert stack.tool("delete_branch", "bob", project=id, name="wip") == {"deleted": True, "branch": "wip"}
    assert stack.tool("delete_schedule", "bob", schedule=schedule["id"]) == {"deleted": True,
                                                                             "schedule": schedule["id"]}
    with pytest.raises(ToolRefused, match=r"\[unknown_schedule\]"):
        stack.tool("delete_schedule", "bob", schedule=schedule["id"])
    with pytest.raises(ToolRefused, match=r"\[confirmation_mismatch\]"):
        stack.tool("delete_project", project=id, confirm_name="nope")
    deleted = stack.tool("delete_project", project=id, confirm_name="shop", force=True)
    assert deleted["deleted"] is True and deleted["project"] == id and set(deleted["tables"]) == {"cleaned", "failed"}
    environment = stack.tool("disable_environment", team=TEAM, environment="development")
    assert environment["enabled"] is False and environment["enabledBy"] is None and environment["team"] == TEAM
    with pytest.raises(ToolRefused, match=r"\[environment_not_enabled\]"):
        stack.tool("disable_environment", team=TEAM, environment="development")
    assert stack.tool("enable_environment", team=TEAM, environment="development") == {
        **environment, "enabled": True, "enabledBy": "alice"}


def test_tool_parameters_and_annotations(stack):
    tools = stack.tools()
    assert len(tools) == 31
    # The names these tools had before are gone.
    assert not {"run", "diff", "compile", "get_pipeline", "get_model", "set_schedule"} & set(tools)

    def parameters(tool):
        return tools[tool]["inputSchema"]["properties"]

    def hints(tool):
        a = tools[tool]["annotations"]
        return a["readOnlyHint"], a["destructiveHint"], a["idempotentHint"], a["openWorldHint"]

    for tool in ("run_project", "get_run"):
        wait = parameters(tool)["wait"]
        assert (wait["default"], wait["minimum"], wait["maximum"]) == (0, 0, 600), tool
    limit = parameters("get_run_logs")["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (500, 1, 5000)
    assert "tail" not in parameters("get_run_logs")
    limit = parameters("list_runs")["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (20, 1, 200)
    limit = parameters("get_history")["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (30, 1, 200)
    assert set(parameters("delete_project")) == {"project", "confirm_name", "force"}
    assert set(parameters("create_schedule")) == {"project", "environment", "cron", "command", "select", "ref",
                                                  "enabled"}
    assert set(tools["create_schedule"]["inputSchema"]["required"]) == {"project", "environment", "cron"}
    assert set(parameters("update_schedule")) == {"schedule", "environment", "cron", "command", "select", "ref",
                                                  "enabled"}
    assert tools["update_schedule"]["inputSchema"]["required"] == ["schedule"]
    assert {"full_refresh", "select", "ref", "command", "environment"} <= set(parameters("run_project"))
    assert "expected_revision" in parameters("write_files") and "default_database" in parameters("create_project")
    assert set(parameters("get_node")) == {"project", "node", "environment", "ref"}
    assert "model" in parameters("preview_model")
    # A resource is named by its noun, never "id".
    assert not [name for name in tools if "id" in parameters(name)]
    read = (True, False, True, False)
    for tool in ("whoami", "list_environments", "list_projects", "get_project", "list_files", "read_file",
                 "list_branches", "get_history", "get_diff", "list_runs", "get_run", "get_run_logs",
                 "list_schedules", "get_pipeline_graph", "get_node", "get_lineage"):
        assert hints(tool) == read, tool
    for tool in ("delete_project", "delete_branch", "delete_schedule", "disable_environment", "merge_to_main"):
        assert hints(tool) == (False, True, False, False), tool
    # A compile and a preview write no table, but each leaves a run record; stopping a run deletes nothing.
    for tool in ("compile_project", "preview_model", "cancel_run", "run_project", "enable_environment",
                 "create_project", "write_files", "create_branch", "create_schedule", "update_schedule"):
        assert hints(tool) == (False, False, False, False), tool
    instructions = stack.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        headers={"Authorization": "Bearer alice", "Accept": "application/json, text/event-stream"}).json()[
        "result"]["instructions"]
    for name in ("run_project", "compile_project", "get_pipeline_graph", "get_node", "create_schedule"):
        assert name in instructions, name
    assert not re.search(r"\b(get_pipeline|get_model|set_schedule|compile|diff)\b", instructions)


def test_the_contract_describes_errors_and_the_shared_shapes(stack):
    schema = stack.app.openapi()
    schemas = schema["components"]["schemas"]
    assert {"Error", "Me", "Environment", "EnvironmentList", "Run", "RunList", "RunLogs", "CancelResult",
            "Schedule", "ScheduleList", "Deleted", "ProjectList", "BranchList", "CommitList"} <= set(schemas)
    assert "HTTPValidationError" not in schemas
    assert set(schemas["Error"]["properties"]) == {"error", "code", "detail"}
    assert schemas["Error"]["required"] == ["error", "code"]
    operations = {op["operationId"]: (method, path, op) for path, item in schema["paths"].items()
                  for method, op in item.items()}
    for id, (_, _, op) in operations.items():
        for status in ("401", "403", "404", "409", "422", "500", "502", "503"):
            assert op["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("/Error"), id

    def answer(id, status="200"):
        return operations[id][2]["responses"][status]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]

    assert answer("whoami") == "Me" and answer("listEnvironments") == "EnvironmentList"
    assert answer("enableEnvironment") == answer("disableEnvironment") == "Environment"
    assert answer("listProjects") == "ProjectList" and answer("createProject", "201") == "Project"
    assert answer("getProject") == "ProjectDetail"
    assert answer("listBranches") == "BranchList" and answer("getHistory") == "CommitList"
    assert answer("runProject", "202") == answer("getRun") == "Run" and answer("listRuns") == "RunList"
    assert answer("getRunLogs") == "RunLogs" and answer("cancelRun") == "CancelResult"
    assert answer("listSchedules") == "ScheduleList"
    assert answer("createSchedule", "201") == answer("updateSchedule") == "Schedule"
    assert answer("deleteProject") == answer("deleteSchedule") == answer("deleteBranch") == "Deleted"
    assert "results" in schemas["Run"]["properties"] and "results" not in schemas["RunItem"]["properties"]
    assert set(schemas["RunItem"]["properties"]) == RUN_FIELDS | {"hint"}
    assert set(schemas["Schedule"]["properties"]) == SCHEDULE_FIELDS
    assert set(schemas["Me"]["properties"]) == {"user", "memberships", "databases", "extension", "origin",
                                                "environments", "enabledEnvironments", "userPortalUrl", "apps"}
    assert operations["deleteProject"][:2] == ("delete", "/api/v1/projects/{project}")
    assert "requestBody" not in operations["deleteProject"][2]
    assert operations["updateSchedule"][:2] == ("patch", "/api/v1/schedules/{schedule}")
    assert operations["deleteSchedule"][:2] == ("delete", "/api/v1/schedules/{schedule}")
    assert operations["getPipelineGraph"][:2] == ("get", "/api/v1/projects/{project}/pipeline")
    assert operations["getNode"][:2] == ("get", "/api/v1/projects/{project}/nodes/{node}")
    assert operations["compileProject"][:2] == ("post", "/api/v1/projects/{project}/compile")
    assert operations["runProject"][:2] == ("post", "/api/v1/projects/{project}/runs")
    assert operations["runProject"][2]["requestBody"].get("required", False) is False
    assert not {"startRun", "compile", "getPipeline", "getModel"} & set(operations)

    def query(id):
        return {p["name"]: p for p in operations[id][2]["parameters"] if p["in"] == "query"}

    assert query("deleteProject")["confirmName"]["required"] is True and set(query("deleteProject")) == {
        "confirmName", "force"}
    assert query("getRun")["wait"]["schema"] == {"type": "integer", "maximum": 600, "minimum": 0, "default": 0,
                                                 "title": "Wait", "description": query("getRun")["wait"]["description"]}
    assert set(query("getRunLogs")) == {"limit"}
    assert (query("getRunLogs")["limit"]["schema"]["default"], query("getRunLogs")["limit"]["schema"]["maximum"]) == (
        500, 5000)
    assert (query("listRuns")["limit"]["schema"]["default"], query("listRuns")["limit"]["schema"]["maximum"]) == (
        20, 200)
    assert set(schemas["RunInput"]["properties"]) == {"command", "environment", "ref", "select", "fullRefresh", "wait"}
    assert schemas["RunInput"]["properties"]["wait"]["default"] == 0
    assert "expectedRevision" in schemas["CommitInput"]["properties"]
    assert "defaultDatabase" in schemas["ProjectInput"]["properties"]
    assert "required" not in schemas["ScheduleChange"]
    assert set(schemas["ScheduleChange"]["properties"]) == {"environment", "cron", "command", "select", "ref",
                                                            "enabled"}
    # No path parameter is called id, and no body or response field of a shared shape is snake_case.
    assert not [path for path in schema["paths"] if "{id}" in path]
    for name in ("RunItem", "Run", "Schedule", "Project", "ProjectDetail", "Me", "Environment", "RunInput",
                 "CommitInput", "ProjectInput", "ScheduleInput", "ScheduleChange"):
        assert not [key for key in schemas[name]["properties"] if "_" in key], name


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
    # The platform serves Bridge contract 1.x (contracts/bridge/COMPATIBILITY.md in the core).
    supported = contract_range()
    assert in_range("1.0.0", supported) and in_range("1.4.2", supported)
    assert not in_range("0.1.4", supported) and not in_range("2.0.0", supported)


def test_the_viewer_revalidates_its_files(stack):
    for path in ("/", "/app.js", "/style.css"):
        assert stack.client.get(path).headers["cache-control"] == "no-cache"
    assert stack.client.get("/fonts/doto.ttf").headers["cache-control"].startswith("public")


def test_the_pipeline_timeline_leaves_out_previews(stack):
    id = create(stack)["id"]
    enable(stack)
    build(stack, id, command="build")
    stack.call("POST", f"/projects/{id}/preview", "carol", json={"model": "stg_regions"})
    stack.call("POST", f"/projects/{id}/compile", "carol", json={"select": "stg_regions"})
    pipeline = stack.call("GET", f"/projects/{id}/pipeline", "carol").json()
    assert [r["command"] for r in pipeline["runs"]] == ["build"]
    runs = stack.call("GET", f"/projects/{id}/runs", "carol").json()
    assert {r["command"] for r in runs["runs"]} == {"build", "show", "compile"}


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
    assert run["status"] in ("queued", "running") and run["results"] == [] and run["finishedAt"] is None
    assert stack.call("POST", f"/runs/{run['id']}/cancel", "carol").json()["code"] == "forbidden_role"
    cancelled = stack.call("POST", f"/runs/{run['id']}/cancel", "bob")
    assert cancelled.status_code == 200 and cancelled.json() == {"run": run["id"], "cancelling": True}
    done = wait_for(stack, run["id"], lambda r: r["status"] not in ("queued", "running"))
    assert done["status"] == "cancelled" and stack.launcher.killed == [run["id"]]
    assert done["error"].startswith("Stopped before it finished") and seconds(done["finishedAt"])
    again = stack.call("POST", f"/runs/{run['id']}/cancel", "bob")
    assert (again.status_code, again.json()["code"]) == (409, "run_finished")


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
    wip = build(stack, id, command="build", ref="wip")
    assert "Development is shared" in wip["hint"] and wip["status"] == "success" and wip["results"]
    # main has nothing to draw yet, so the viewer names the branches that do.
    main = stack.call("GET", f"/projects/{id}/pipeline?ref=main", "carol").json()
    assert main["refsWithPipeline"] == ["wip"] and "wip" in main["hint"]
    # A merge says main still needs a build before the viewer shows it.
    merged = stack.call("POST", f"/projects/{id}/merge", "alice", json={"branch": "wip"}).json()
    assert "Build main in development" in merged["hint"]
    built = build(stack, id, command="build")
    assert "hint" not in built
    main = stack.call("GET", f"/projects/{id}/pipeline?ref=main", "carol").json()
    assert main["overwrittenBy"] is None
    assert {n["id"]: n["status"] for n in main["nodes"]}["source.shop.raw.events"] == "external"
    # A later build of the branch replaces main's tables, and main's pipeline says so.
    again = build(stack, id, command="build", ref="wip")
    main = stack.call("GET", f"/projects/{id}/pipeline?ref=main", "carol").json()
    assert main["overwrittenBy"] == {"ref": "wip", "run": again["id"], "at": again["finishedAt"]}
    assert "hint" not in stack.call("POST", f"/projects/{id}/merge", "alice", json={"branch": "wip"}).json()


def test_docs_site_serves_only_docs_runs_of_main(stack):
    # Project code can write any file under out/; the site runs scripts with the viewer's session.
    def plant(run):
        docs = stack.launcher.artifacts / run / "out" / "docs"
        docs.mkdir(parents=True)
        (docs / "index.html").write_text("<script>planted</script>")

    stack.launcher.on_start = plant
    id = create(stack)["id"]
    enable(stack)
    head = stack.call("POST", f"/projects/{id}/branches", "bob", json={"name": "wip"}).json()["revision"]
    stack.call("POST", f"/projects/{id}/commits", "bob", json={
        "branch": "wip", "message": "x", "expectedRevision": head, "files": {"models/x.sql": "select 1"}})

    def site(body):
        run = stack.call("POST", f"/projects/{id}/runs", "carol", json={**body, "wait": 30}).json()
        assert run["status"] == "success", run
        return stack.client.get(f"/docs-site/{run['id']}/index.html", headers={"Authorization": "Bearer carol"})

    assert site({"command": "compile"}).status_code == 404
    assert site({"command": "docs", "ref": "wip"}).status_code == 404
    served = site({"command": "docs"})
    assert served.status_code == 200 and "script-src" in served.headers["content-security-policy"]


def test_only_heads_of_main_reach_production_and_docs(stack):
    # A Writer hides a commit inside a branch: the review sees only the branch's final tree.
    def plant(run):
        docs = stack.launcher.artifacts / run / "out" / "docs"
        docs.mkdir(parents=True)
        (docs / "index.html").write_text("<script>planted</script>")

    stack.launcher.on_start = plant
    id = create(stack)["id"]
    enable(stack)
    enable(stack, "production")
    head = stack.call("POST", f"/projects/{id}/branches", "bob", json={"name": "wip"}).json()["revision"]
    hidden = stack.call("POST", f"/projects/{id}/commits", "bob", json={
        "branch": "wip", "message": "wip", "expectedRevision": head,
        "files": {"models/evil.sql": "select 1"}}).json()["revision"]
    reviewed = stack.call("POST", f"/projects/{id}/commits", "bob", json={
        "branch": "wip", "message": "tidy", "expectedRevision": hidden,
        "files": {"models/evil.sql": None, "models/good.sql": "select 2"}}).json()["revision"]
    stale = stack.call("POST", f"/projects/{id}/merge", "alice", json={"branch": "wip", "revision": hidden})
    assert (stale.status_code, stale.json()["code"]) == (409, "revision_conflict")
    merged = stack.call("POST", f"/projects/{id}/merge", "alice", json={"branch": "wip", "revision": reviewed}).json()
    assert merged["merged"] is True

    def run(body, user="bob"):
        return stack.call("POST", f"/projects/{id}/runs", user, json={**body, "wait": 30}).json()

    for ref in (hidden, reviewed):
        assert run({"command": "build", "environment": "production", "ref": ref})["code"] == "main_required"
        docs = run({"command": "docs", "ref": ref}, "carol")
        assert stack.client.get(f"/docs-site/{docs['id']}/index.html",
                                headers={"Authorization": "Bearer alice"}).status_code == 404
    assert run({"command": "build", "environment": "production"})["status"] == "success"
    docs = run({"command": "docs"}, "carol")
    served = stack.client.get(f"/docs-site/{docs['id']}/index.html", headers={"Authorization": "Bearer alice"})
    assert served.status_code == 200


def test_a_sign_in_belongs_to_the_browser_that_started_it(tmp_path):
    """A callback URL sent to another browser signs nobody in (login CSRF)."""
    import asyncio

    import httpx
    import pytest

    from dbt_portal.auth import BrowserLogin
    from dbt_portal.errors import DbtError
    from tests.conftest import FakeVerifier, settings

    token = httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "alice",
                                                                          "refresh_token": "r"}))
    login = BrowserLogin(settings(tmp_path), FakeVerifier(), httpx.AsyncClient(transport=token))
    url, binding = login.start("/#/runs")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    with pytest.raises(DbtError):
        asyncio.run(login.finish(state, "code", "someone-elses-binding"))
    # The failed attempt used up the state.
    with pytest.raises(DbtError):
        asyncio.run(login.finish(state, "code", binding))
    url, binding = login.start("/#/runs")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    session, target = asyncio.run(login.finish(state, "code", binding))
    assert session in login.sessions and target == "/#/runs"


def test_the_login_route_sets_the_binding_cookie(stack):
    response = stack.client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "iceberg_dbt_login=" in cookie and "HttpOnly" in cookie and "Path=/auth/callback" in cookie
    state = response.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    stack.client.cookies.clear()
    failed = stack.client.get(f"/auth/callback?state={state}&code=x", follow_redirects=False)
    assert failed.headers["location"] == "/?signin=failed"
