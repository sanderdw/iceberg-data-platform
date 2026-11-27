import json
import re
import time
from datetime import datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from maintenance import policy
from maintenance.api import OPERATIONS
from maintenance.bridge_client import in_range
from maintenance.config import ROOT, contract_range
from maintenance.errors import MaintenanceError
from maintenance.runs import summarize
from maintenance.scheduler import Scheduler
from maintenance.services import timestamp
from tests.conftest import DATABASES, DEV, OTHER, READINGS, TABLES, TEAM

PROD = DATABASES[("grid-capacity", "production")]
RFC3339 = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
RUN_KEYS = {"id", "database", "team", "environment", "mode", "tables", "status", "triggeredBy", "agent",
            "scheduled", "createdAt", "startedAt", "finishedAt", "exitCode", "summary", "error"}
STATUSES = {"queued", "running", "success", "incomplete", "partial", "failed", "error", "cancelled"}


def enable(stack, environment="development"):
    assert stack.call("POST", f"/teams/{TEAM}/environments/{environment}").status_code == 200


def save(stack, user="alice", database=DEV, **body):
    return stack.call("PATCH", f"/databases/{database}/policy", user, json=body)


def versions(policy_view):
    return {key: ref["version"] if ref else None for key, ref in policy_view["policies"].items()}


HOME = ("_maintenance",)


def schedule(stack, user="alice", database=DEV, **body):
    return stack.call("PUT", f"/databases/{database}/schedule", user, json=body)


def seconds(value):
    assert RFC3339.match(value), value
    return datetime.fromisoformat(value).timestamp()


def refused(stack, code, tool, user="alice", **arguments):
    with pytest.raises(ToolError) as error:
        stack.tool(tool, user, **arguments)
    assert f"[{code}]" in str(error.value), error.value


def test_times_are_rfc_3339_in_utc_with_milliseconds():
    assert timestamp(1795511702.1234) == "2026-11-24T09:15:02.123Z"
    assert timestamp(0) == "1970-01-01T00:00:00.000Z" and timestamp(None) is None


def test_whoami_lists_the_defaults_and_where_it_is_enabled(stack):
    me = stack.call("GET", "/me").json()
    # Built field by field: the Bridge's own keys, such as sharedDatabases, do not pass through.
    assert set(me) == {"user", "memberships", "databases", "extension", "origin", "environments",
                       "enabledEnvironments", "userPortalUrl", "apps", "policyTypes", "defaults"}
    assert me["user"] == {"id": "portal-alice" + "0" * 27, "name": "alice"}
    assert me["memberships"] == [{"team": TEAM, "teamName": "grid-planning", "role": "admin"}]
    assert me["databases"][0] == {"id": DEV, "name": "grid-capacity", "team": TEAM, "environment": "development",
                                  "status": "ready"}
    assert me["extension"] == "maintenance" and me["defaults"] == policy.defaults()
    assert me["policyTypes"]["snapshotExpiry"] == {"type": "system.snapshot-expiry", "title": "Expire snapshots"}
    assert list(me["policyTypes"]) == list(policy.TYPES)
    assert me["environments"] == ["development", "acceptance", "production"]
    assert me["enabledEnvironments"] == [{"team": TEAM, "environments": []}]
    assert stack.call("POST", f"/teams/{TEAM}/environments/development", "bob").json()["code"] == "forbidden_role"
    enable(stack)
    assert stack.call("GET", "/me", "carol").json()["enabledEnvironments"][0]["environments"] == ["development"]
    assert stack.tool("whoami") == stack.call("GET", "/me").json()


def test_environments_are_one_entry_shape_in_every_operation(stack):
    refusal = stack.call("GET", f"/teams/{TEAM}/environments", "dave")
    assert refusal.status_code == 403 and refusal.json()["code"] == "not_a_member"
    databases = [{"id": DEV, "name": "grid-capacity"}, {"id": DATABASES[("grid-assets", "development")],
                                                        "name": "grid-assets"}]
    off = {"team": TEAM, "environment": "development", "enabled": False, "enabledBy": None, "databases": databases}
    on = {**off, "enabled": True, "enabledBy": "alice"}
    listed = stack.call("GET", f"/teams/{TEAM}/environments", "carol").json()
    assert list(listed) == ["environments"] and listed["environments"][0] == off
    assert [e["environment"] for e in listed["environments"]] == ["development", "acceptance", "production"]
    enabled = stack.call("POST", f"/teams/{TEAM}/environments/development")
    # The environment entry, not the Bridge's automation principal.
    assert enabled.status_code == 200 and enabled.json() == on
    assert stack.call("GET", f"/teams/{TEAM}/environments").json()["environments"][0] == on
    assert stack.tool("list_environments", team=TEAM) == stack.call("GET", f"/teams/{TEAM}/environments").json()
    disabled = stack.call("DELETE", f"/teams/{TEAM}/environments/development")
    assert disabled.status_code == 200 and disabled.json() == off
    again = stack.call("DELETE", f"/teams/{TEAM}/environments/development")
    assert again.status_code == 409 and again.json()["code"] == "environment_not_enabled"
    assert stack.tool("enable_environment", team=TEAM, environment="production")["enabled"] is True
    assert stack.tool("disable_environment", team=TEAM, environment="production") == {
        "team": TEAM, "environment": "production", "enabled": False, "enabledBy": None,
        "databases": [{"id": PROD, "name": "grid-capacity"}]}
    refused(stack, "environment_not_enabled", "disable_environment", team=TEAM, environment="production")


def test_runs_need_an_enabled_environment_and_a_writer(stack):
    refusal = stack.run()
    assert refusal.status_code == 409 and refusal.json()["code"] == "environment_not_enabled"
    enable(stack)
    assert stack.run(user="carol").json()["code"] == "forbidden_role"
    unknown = stack.run(user="dave")
    assert unknown.status_code == 404 and unknown.json()["code"] == "unknown_database"


def test_a_health_check_uses_a_read_token_and_stores_each_tables_health(stack):
    enable(stack)
    run = stack.run().json()
    assert run["status"] == "success" and run["mode"] == "inspect", run
    assert stack.bridge.tokens[-1][1] == "read"
    job = stack.launcher.started[run["id"]]["job"]
    assert job["kind"] == "inspect" and job["warehouse"] == DEV and job["storageEndpoint"] == "http://rustfs:9000"
    assert job["deadline"] > time.time() and "policy" not in job and "exclude" not in job
    # The gateway lists the tables and asks Polaris which policies apply to each; here none does.
    assert [t["name"] for t in job["tables"]] == TABLES
    assert job["tables"][0]["identifier"] == ["marts", "feeder_capacity_daily"]
    expiry = job["tables"][0]["policies"]["snapshotExpiry"]
    assert expiry == {**policy.defaults()["snapshotExpiry"], "enabled": False, "policy": None, "problem": ""}
    # All of it with the run's read token, in Polaris's realm.
    assert {auth for _, _, auth in stack.polaris.requests} == {"Bearer token-read"}
    listed = stack.call("GET", f"/tables?database={DEV}", "carol").json()
    assert set(listed) == {"tables", "total"} and listed["total"] == 3
    tables = listed["tables"]
    assert {t["name"] for t in tables} == set(TABLES)
    readings = next(t for t in tables if t["name"] == READINGS)
    assert set(readings) == {"database", "databaseName", "team", "environment", "name", "state", "reasons", "health",
                             "policies", "inspectedAt", "inspectedBy", "lastRun", "lastStatus", "lastError",
                             "lastActions", "lastMaintainedAt"}
    assert readings["policies"]["dataCompaction"] == {
        "settings": {**policy.defaults()["dataCompaction"], "enabled": False}, "policy": None, "problem": None}
    # Without a policy the defaults judge the table, and say there is none.
    assert readings["state"] == "no-policy" and "30 files to compact" in readings["reasons"]
    assert readings["lastRun"] == run["id"] and readings["lastError"] is None
    assert readings["lastMaintainedAt"] is None and time.time() - seconds(readings["inspectedAt"]) < 60
    assert stack.call("GET", "/tables?attention=true", "carol").json() == {"tables": [], "total": 0}
    listed = stack.call("GET", "/databases").json()
    assert list(listed) == ["databases"]
    database = next(d for d in listed["databases"] if d["id"] == DEV)
    assert set(database) == {"id", "name", "team", "teamName", "environment", "role", "policy", "schedule", "tables",
                             "attention", "inspected", "lastRun"}
    assert database["tables"] == 3 and database["policy"] is False and database["schedule"] is None
    # Where Maintenance is not enabled it cannot read the catalog's policies.
    assert next(d for d in listed["databases"] if d["id"] == PROD)["policy"] is None
    # A run in a list carries no per-table results.
    assert database["lastRun"]["id"] == run["id"] and set(database["lastRun"]) == RUN_KEYS
    assert stack.tool("list_databases", "carol") == stack.call("GET", "/databases", "carol").json()
    assert stack.tool("list_table_health", "carol", database=DEV) == stack.call(
        "GET", f"/tables?database={DEV}", "carol").json()


def test_table_health_is_limited_and_counts_what_matches(stack):
    enable(stack)
    stack.run()
    assert stack.call("GET", "/tables").json()["total"] == 3
    first = stack.call("GET", "/tables?limit=2").json()
    # total counts before the limit.
    assert len(first["tables"]) == 2 and first["total"] == 3
    assert stack.tool("list_table_health", limit=1) == {"tables": first["tables"][:1], "total": 3}
    for limit in (0, 5001):
        response = stack.call("GET", f"/tables?limit={limit}")
        assert response.status_code == 422 and response.json()["code"] == "invalid_request"
    assert len(stack.call("GET", "/tables?limit=5000").json()["tables"]) == 3
    with pytest.raises(ToolError):
        stack.tool("list_table_health", limit=5001)
    # A filter that names a team you are not in is refused; one that names an unknown database is not found.
    other = stack.call("GET", f"/tables?team={OTHER}")
    assert other.status_code == 403 and other.json()["code"] == "not_a_member"
    assert stack.call("GET", f"/databases?team={OTHER}").json()["code"] == "not_a_member"
    unknown = stack.call("GET", "/tables?database=db-" + "9" * 32)
    assert unknown.status_code == 404 and unknown.json()["code"] == "unknown_database"
    one = stack.call("GET", f"/databases/{DEV}/tables/{READINGS}", "carol").json()
    assert one["role"] == "reader" and one["policies"]["snapshotExpiry"]["policy"] is None
    assert set(one["recentRuns"][0]) == RUN_KEYS and "policy" not in one
    assert stack.tool("get_table_health", "carol", database=DEV, table=READINGS) == one
    missing = stack.call("GET", f"/databases/{DEV}/tables/raw.nothing")
    assert missing.status_code == 404 and missing.json()["code"] == "unknown_table"


def test_policies_are_polaris_policies_for_administrators(stack):
    # Policies are read and written as the team's service account: the environment must be enabled.
    early = stack.call("GET", f"/databases/{DEV}/policy", "carol")
    assert early.status_code == 409 and early.json()["code"] == "environment_not_enabled"
    enable(stack)
    assert save(stack, "bob", settings={}).json()["code"] == "forbidden_role"
    none = stack.call("GET", f"/databases/{DEV}/policy", "carol").json()
    assert set(none) == {"database", "databaseName", "team", "environment", "exists", "settings", "policies",
                         "exclusions", "schedule", "role", "note"}
    assert none["exists"] is False and none["settings"] == policy.defaults() and none["schedule"] is None
    assert none["policies"] == dict.fromkeys(policy.TYPES) and none["exclusions"] == []
    bad = [({"dataCompaction": {"targetFileSizeBytes": 4 << 20}}, "between 16777216"),
           ({"orphanFileRemoval": {"maxOrphanFileAgeInDays": 0}}, "between 1"),
           ({"snapshotExpiry": {"minSnapshotsToKeep": 0}}, "between 1"),
           ({"snapshotExpiry": {"enabled": "yes"}}, "true or false"),
           ({"vacuum": {}}, "Unknown policy type"),
           ({"snapshotExpiry": {"olderThanHours": 5}}, "Unknown setting"),
           ({"dataCompaction": {"targetFileSizeBytes": 512 << 20, "maxInputBytesPerRun": 256 << 20}},
            "at least the target")]
    for settings, message in bad:
        response = save(stack, settings=settings)
        assert response.status_code == 422 and message in response.json()["error"], (settings, response.json())
        assert response.json()["code"] == "invalid_policy"
    assert not stack.polaris.catalogs[DEV]["policies"]
    # A change is a PATCH. The old method and the old fields are gone.
    replaced = stack.call("PUT", f"/databases/{DEV}/policy", json={})
    assert replaced.status_code == 405 and replaced.json()["code"] == "method_not_allowed"
    for old in ({"expectedRevision": 0}, {"exclude": []}, {"cron": "0 2 * * *"}):
        response = save(stack, **old)
        assert response.status_code == 422 and response.json()["code"] == "invalid_request", old
    response = save(stack, settings={"orphanFileRemoval": {"enabled": True}},
                    expectedVersions=dict.fromkeys(policy.TYPES))
    created = response.json()
    assert response.status_code == 200 and created["exists"] and set(created) == set(none)
    # One Polaris policy per type, in Maintenance's namespace, attached to the database's catalog.
    catalog = stack.polaris.catalogs[DEV]
    assert set(catalog["policies"]) == {(HOME, name) for name in policy.CATALOG_NAMES.values()}
    assert all(catalog["mappings"][(HOME, name)] == {("catalog", ())} for name in policy.CATALOG_NAMES.values())
    assert catalog["policies"][(HOME, "snapshot-expiry")]["type"] == "system.snapshot-expiry"
    assert stack.polaris.content(DEV, HOME, "snapshot-expiry") == {
        "version": "2025-02-03", "enable": True, "config": {"max_snapshot_age_days": 5, "min_snapshot_to_keep": 1}}
    assert stack.polaris.content(DEV, HOME, "orphan-file-removal") == {
        "version": "2025-02-03", "enable": True, "max_orphan_file_age_in_days": 3}
    assert created["policies"]["snapshotExpiry"] == {"name": "snapshot-expiry", "namespace": "_maintenance",
                                                     "version": 0, "managed": True, "problem": None}
    assert created["settings"]["orphanFileRemoval"]["enabled"]
    assert created["settings"]["snapshotExpiry"] == policy.defaults()["snapshotExpiry"]
    # A partial change writes only the type that changes: Polaris raises only its version.
    changed = save(stack, settings={"dataCompaction": {"targetFileSizeBytes": 256 << 20}},
                   expectedVersions={"dataCompaction": 0}).json()
    assert versions(changed) == {"snapshotExpiry": 0, "dataCompaction": 1, "metadataCompaction": 0,
                                 "orphanFileRemoval": 0}
    assert stack.polaris.content(DEV, HOME, "data-compaction")["config"]["target-file-size-bytes"] == 256 << 20
    assert changed["settings"]["orphanFileRemoval"]["enabled"]
    stale = save(stack, settings={"dataCompaction": {"minInputFiles": 3}}, expectedVersions={"dataCompaction": 0})
    assert stale.status_code == 409 and stale.json()["code"] == "revision_conflict"
    # Polaris itself refuses a write at an older version, as when someone changed the policy meanwhile.
    catalog_client = stack.services.polaris.catalog(DEV, principal=stack.bridge.automation[0]["id"], access="write")
    with pytest.raises(MaintenanceError) as error:
        stack.client.portal.call(catalog_client.update_policy, list(HOME), "data-compaction",
                                 {"version": "2025-02-03", "enable": False}, 0)
    assert error.value.code == "revision_conflict"
    assert stack.tool("get_policy", "carol", database=DEV) == stack.call(
        "GET", f"/databases/{DEV}/policy", "carol").json()
    assert stack.call("DELETE", f"/databases/{DEV}/policy", "bob").json()["code"] == "forbidden_role"
    deleted = stack.call("DELETE", f"/databases/{DEV}/policy")
    assert deleted.status_code == 200 and deleted.json() == {
        "deleted": True, "database": DEV, "note": "Scheduled maintenance stops. Table health and past runs stay."}
    # Its policies and its namespace are gone from Polaris.
    assert not catalog["policies"] and HOME not in catalog["namespaces"]
    missing = stack.call("DELETE", f"/databases/{DEV}/policy")
    assert missing.status_code == 404 and missing.json()["code"] == "no_policy"


def test_policies_other_tools_attached_are_updated_in_place_and_honoured(stack):
    enable(stack)
    # A Spark administrator attached a snapshot-expiry policy to the catalog, with a key Maintenance does not use.
    stack.polaris.put_policy(DEV, ["ops"], "retention", "system.snapshot-expiry", {
        "version": "2025-02-03", "enable": True,
        "config": {"max_snapshot_age_days": 2, "min_snapshot_to_keep": "3", "max_ref_age_days": 3}},
        {"type": "catalog"})
    read = stack.call("GET", f"/databases/{DEV}/policy").json()
    assert read["exists"] and read["settings"]["snapshotExpiry"] == {"enabled": True, "maxSnapshotAgeDays": 2,
                                                                     "minSnapshotsToKeep": 3}
    assert read["policies"]["snapshotExpiry"] == {"name": "retention", "namespace": "ops", "version": 0,
                                                  "managed": False, "problem": None}
    assert read["policies"]["dataCompaction"] is None
    saved = save(stack, settings={"snapshotExpiry": {"maxSnapshotAgeDays": 7}}).json()
    # Updated in place, its own key kept; the other types are created next to it.
    assert stack.polaris.content(DEV, ["ops"], "retention")["config"] == {
        "max_snapshot_age_days": 7, "min_snapshot_to_keep": 3, "max_ref_age_days": 3}
    assert saved["policies"]["snapshotExpiry"]["version"] == 1 and saved["policies"]["dataCompaction"]["managed"]
    # A data-compaction policy on the namespace raw, in the key Polaris's documentation uses.
    stack.polaris.put_policy(DEV, ["raw"], "raw-compaction", "system.data-compaction", {
        "version": "2025-02-03", "enable": True, "config": {"target_file_size_bytes": 268435456}},
        {"type": "namespace", "path": ["raw"]})
    # And an orphan-file policy on one table that is younger than Maintenance ever deletes.
    stack.polaris.put_policy(DEV, ["marts"], "too-soon", "system.orphan-file-removal", {
        "version": "2025-02-03", "enable": True, "max_orphan_file_age_in_days": 0.1},
        {"type": "table-like", "path": ["marts", "feeder_capacity_daily"]})
    run = stack.run("dry-run").json()
    job = {t["name"]: t["policies"] for t in stack.launcher.started[run["id"]]["job"]["tables"]}
    readings, marts = job[READINGS], job[TABLES[0]]
    assert readings["dataCompaction"]["policy"] == {"name": "raw-compaction", "namespace": "raw", "version": 0,
                                                    "appliedAt": "namespace"}
    assert readings["dataCompaction"]["targetFileSizeBytes"] == 268435456
    assert readings["snapshotExpiry"]["policy"] == {"name": "retention", "namespace": "ops", "version": 1,
                                                    "appliedAt": "catalog"}
    assert readings["snapshotExpiry"]["maxSnapshotAgeDays"] == 7
    assert marts["dataCompaction"]["policy"]["name"] == "data-compaction"
    assert marts["orphanFileRemoval"]["enabled"] is False and "max_orphan_file_age_in_days" in (
        marts["orphanFileRemoval"]["problem"])
    assert marts["orphanFileRemoval"]["policy"]["appliedAt"] == "table"
    # Each action names the policy that decided it.
    result = next(r for r in run["results"] if r["name"] == READINGS)
    assert result["actions"][0]["policy"]["name"] == "raw-compaction"
    health = stack.call("GET", f"/databases/{DEV}/tables/{TABLES[0]}").json()
    assert health["policies"]["orphanFileRemoval"]["problem"] and health["policies"]["orphanFileRemoval"][
        "policy"]["name"] == "too-soon"
    # Removing the database's policies detaches another tool's and keeps it; Maintenance's own are deleted.
    assert stack.call("DELETE", f"/databases/{DEV}/policy").status_code == 200
    catalog = stack.polaris.catalogs[DEV]
    assert catalog["mappings"][(("ops",), "retention")] == set()
    assert {key for key in catalog["policies"] if key[0] == HOME} == set()
    assert (("raw",), "raw-compaction") in catalog["policies"]


def test_an_exclusion_never_turns_maintenance_on(stack):
    """A shared disabled policy changed elsewhere is set back to off before it is attached."""
    enable(stack)
    save(stack)
    stack.polaris.put_policy(DEV, list(HOME), "excluded-orphan-file-removal", "system.orphan-file-removal", {
        "version": "2025-02-03", "enable": True, "max_orphan_file_age_in_days": 1},
        {"type": "table-like", "path": ["marts", "feeder_capacity_daily"]})
    assert save(stack, exclusions=[{"target": READINGS, "level": "table"}]).status_code == 200
    assert stack.polaris.content(DEV, list(HOME), "excluded-orphan-file-removal") == {
        "version": "2025-02-03", "enable": False}
    # A policy of that name but another type is refused, not attached.
    stack.call("DELETE", f"/databases/{DEV}/policy")
    save(stack)
    stack.polaris.put_policy(DEV, list(HOME), "excluded-snapshot-expiry", "system.data-compaction", {
        "version": "2025-02-03", "enable": False}, {"type": "table-like", "path": ["marts", "feeder_capacity_daily"]})
    stack.polaris.catalogs[DEV]["mappings"][(HOME, "excluded-snapshot-expiry")] = set()
    refused = save(stack, exclusions=[{"target": "raw", "level": "namespace", "types": ["snapshotExpiry"]}])
    assert refused.status_code == 409 and refused.json()["code"] == "exclusion_conflict"


def test_exclusions_are_disabled_policies_that_override_the_database(stack):
    enable(stack)
    save(stack)
    bad = [([{"target": "raw", "level": "table"}], "namespace.table", 422, "invalid_exclusions"),
           ([{"target": "raw.nothing", "level": "table"}], "no table raw.nothing", 422, "invalid_exclusions"),
           ([{"target": "raw", "level": "namespace"}] * 2, "twice", 422, "invalid_exclusions"),
           ([{"target": "_maintenance", "level": "namespace"}], "Name an excluded", 422, "invalid_exclusions"),
           ([{"target": "raw", "level": "namespace", "types": ["vacuum"]}], "types", 422, "invalid_request")]
    for exclusions, message, status, code in bad:
        response = save(stack, exclusions=exclusions)
        assert response.status_code == status and response.json()["code"] == code, response.json()
        assert message in response.json()["error"], response.json()
    response = save(stack, exclusions=[
        {"target": "raw.transformer_register", "level": "table"},
        {"target": "marts", "level": "namespace", "types": ["orphanFileRemoval", "snapshotExpiry"]}])
    view = response.json()
    assert response.status_code == 200, view
    assert [(e["target"], e["level"], e["types"], e["createdBy"]) for e in view["exclusions"]] == [
        ("marts", "namespace", ["snapshotExpiry", "orphanFileRemoval"], "alice"),
        ("raw.transformer_register", "table", list(policy.TYPES), "alice")]
    assert seconds(view["exclusions"][0]["createdAt"])
    # In Polaris: one disabled policy per type, attached to each namespace and table it switches off.
    catalog = stack.polaris.catalogs[DEV]
    assert stack.polaris.content(DEV, HOME, "excluded-snapshot-expiry") == {"version": "2025-02-03", "enable": False}
    assert catalog["mappings"][(HOME, "excluded-snapshot-expiry")] == {
        ("table-like", ("raw", "transformer_register")), ("namespace", ("marts",))}
    assert catalog["mappings"][(HOME, "excluded-data-compaction")] == {("table-like", ("raw", "transformer_register"))}
    # Polaris resolves the override: the table's policy over its namespace's over the database's.
    run = stack.run("maintain").json()
    job = {t["name"]: t["policies"] for t in stack.launcher.started[run["id"]]["job"]["tables"]}
    excluded = job["raw.transformer_register"]
    assert not any(p["enabled"] for p in excluded.values())
    assert {p["policy"]["appliedAt"] for p in excluded.values()} == {"table"}
    marts = job[TABLES[0]]
    assert marts["snapshotExpiry"]["policy"] == {"name": "excluded-snapshot-expiry", "namespace": "_maintenance",
                                                 "version": 0, "appliedAt": "namespace"}
    assert marts["dataCompaction"]["enabled"] and marts["dataCompaction"]["policy"]["appliedAt"] == "catalog"
    assert {r["name"]: r["status"] for r in run["results"]}["raw.transformer_register"] == "skipped"
    tables = {t["name"]: t for t in stack.call("GET", f"/tables?database={DEV}").json()["tables"]}
    assert tables["raw.transformer_register"]["state"] == "excluded" and tables[TABLES[0]]["state"] != "excluded"
    # A new list detaches what is no longer excluded.
    save(stack, exclusions=[{"target": "marts", "level": "namespace", "types": ["snapshotExpiry"]}])
    assert catalog["mappings"][(HOME, "excluded-snapshot-expiry")] == {("namespace", ("marts",))}
    assert catalog["mappings"][(HOME, "excluded-data-compaction")] == set()
    assert catalog["mappings"][(HOME, "excluded-orphan-file-removal")] == set()
    # A table with its own policy of a type, attached by another tool, cannot also be excluded for that type.
    stack.polaris.put_policy(DEV, ["raw"], "keep-history", "system.snapshot-expiry", {
        "version": "2025-02-03", "enable": True, "config": {"max_snapshot_age_days": 3650}},
        {"type": "table-like", "path": ["raw", "feeder_load_readings"]})
    conflict = save(stack, exclusions=[{"target": READINGS, "level": "table"}])
    assert conflict.status_code == 409 and conflict.json()["code"] == "exclusion_conflict"
    assert conflict.json()["detail"] == {"target": READINGS}
    # A failed request never ends an exclusion: removed ones are detached only after everything else worked.
    kept = stack.call("GET", f"/databases/{DEV}/policy").json()["exclusions"]
    assert [(e["level"], e["target"], e["types"]) for e in kept] == [("namespace", "marts", ["snapshotExpiry"])]
    assert catalog["mappings"][(HOME, "excluded-snapshot-expiry")] == {("namespace", ("marts",))}
    assert stack.tool("update_policy", database=DEV, exclusions=[])["exclusions"] == []


def test_the_policy_tool_changes_only_what_it_names(stack):
    enable(stack)
    refused(stack, "forbidden_role", "update_policy", "bob", database=DEV)
    created = stack.tool("update_policy", database=DEV, expected_versions={"snapshotExpiry": None})
    assert created["exists"] and created["settings"] == policy.defaults()
    assert versions(created) == dict.fromkeys(policy.TYPES, 0)
    refused(stack, "revision_conflict", "update_policy", database=DEV, expected_versions={"snapshotExpiry": None})
    changed = stack.tool("update_policy", database=DEV, settings={"metadataCompaction": {"minCountToMerge": 50}},
                         exclusions=[{"target": TABLES[2], "level": "table"}],
                         expected_versions={"metadataCompaction": 0})
    assert changed["settings"]["metadataCompaction"] == {"enabled": True, "minCountToMerge": 50}
    assert [e["target"] for e in changed["exclusions"]] == [TABLES[2]]
    assert changed == stack.call("GET", f"/databases/{DEV}/policy").json()
    kept = stack.tool("update_policy", database=DEV, settings={"orphanFileRemoval": {"enabled": True}})
    assert kept["exclusions"] == changed["exclusions"]
    assert stack.tool("delete_policy", database=DEV)["deleted"] is True
    refused(stack, "no_policy", "delete_policy", database=DEV)


def test_settings_map_onto_polaris_policy_content():
    defaults = policy.defaults()
    content = policy.to_content("dataCompaction", defaults["dataCompaction"])
    assert content == {"version": "2025-02-03", "enable": True, "config": {
        "target-file-size-bytes": 134217728, "min-input-files": 5, "delete-ratio-threshold": 0.3,
        "remove-dangling-deletes": True, "max-input-bytes-per-run": 2147483648}}
    assert policy.from_content("dataCompaction", json.dumps(content)) == (defaults["dataCompaction"], None)
    # The example in Polaris's documentation reads, and its other keys stay when Maintenance writes it.
    documented = json.dumps({"version": "2025-02-03", "enable": True, "config": {
        "target_file_size_bytes": 134217728, "compaction_strategy": "bin-pack",
        "max-concurrent-file-group-rewrites": 5}})
    settings, problem = policy.from_content("dataCompaction", documented)
    assert problem is None and settings["targetFileSizeBytes"] == 134217728
    kept = policy.to_content("dataCompaction", settings, documented)
    assert kept["config"]["compaction_strategy"] == "bin-pack" and "target_file_size_bytes" not in kept["config"]
    assert policy.to_content("orphanFileRemoval", defaults["orphanFileRemoval"]) == {
        "version": "2025-02-03", "enable": False, "max_orphan_file_age_in_days": 3}
    # Polaris wants enable as true or false; content Maintenance cannot read, or out of its bounds, is not applied.
    too_short = json.dumps({"enable": True, "config": {"max_snapshot_age_days": 0}})
    for content in ('{"enable": "true"}', "not json", too_short):
        settings, problem = policy.from_content("snapshotExpiry", content)
        assert problem and settings["enabled"] is False, content
    # Applicable policies: where each is attached, told apart by the catalog's own.
    catalog_level = {"snapshotExpiry": {"name": "snapshot-expiry", "namespace": ["_maintenance"]}}
    applied = policy.effective([
        {"policy-type": "system.snapshot-expiry", "name": "snapshot-expiry", "namespace": ["_maintenance"],
         "inherited": True, "version": 2, "content": json.dumps({"enable": True})},
        {"policy-type": "system.data-compaction", "name": "raw", "namespace": ["raw"], "inherited": True,
         "version": 0, "content": json.dumps({"enable": False})},
        {"policy-type": "system.orphan-file-removal", "name": "t", "namespace": ["raw"], "inherited": False,
         "version": 0, "content": json.dumps({"enable": True, "max_orphan_file_age_in_days": 30})}], catalog_level)
    assert [applied[k]["policy"]["appliedAt"] for k in ("snapshotExpiry", "dataCompaction", "orphanFileRemoval")] == [
        "catalog", "namespace", "table"]
    assert applied["metadataCompaction"] == {"settings": {**defaults["metadataCompaction"], "enabled": False},
                                             "policy": None, "problem": None}
    assert applied["orphanFileRemoval"]["settings"]["maxOrphanFileAgeInDays"] == 30


def test_a_schedule_is_set_and_removed_on_its_own(stack):
    enable(stack)
    # A schedule applies the policy, so it needs one.
    early = schedule(stack, cron="0 2 * * *")
    assert early.status_code == 409 and early.json()["code"] == "policy_required"
    missing = stack.call("DELETE", f"/databases/{DEV}/schedule")
    assert missing.status_code == 404 and missing.json()["code"] == "no_schedule"
    save(stack, exclusions=[{"target": TABLES[2], "level": "table"}])
    assert schedule(stack, "bob", cron="0 2 * * *").json()["code"] == "forbidden_role"
    response = schedule(stack, cron="0 2 * * *")
    made = response.json()
    assert response.status_code == 200 and set(made) == {"cron", "enabled", "nextRunAt", "lastRun"}
    assert made["cron"] == "0 2 * * *" and made["enabled"] is True and made["lastRun"] is None
    assert seconds(made["nextRunAt"]) > time.time()
    # Maintenance keeps its own minimum of an hour, stricter than the platform's five minutes.
    for cron in ("*/10 * * * *", "*/30 * * * *", "0 2 * *", "not a cron"):
        response = schedule(stack, cron=cron)
        assert response.status_code == 422 and response.json()["code"] == "invalid_cron", cron
    assert schedule(stack, cron="0 * * * *").json()["cron"] == "0 * * * *"
    assert schedule(stack, cron="").json()["code"] == "invalid_request"
    assert schedule(stack, enabled=False).json()["code"] == "invalid_request"
    # Paused: the schedule stays, and has no next run.
    assert schedule(stack, cron="0 2 * * *", enabled=False).json() == {
        "cron": "0 2 * * *", "enabled": False, "nextRunAt": None, "lastRun": None}
    # It is read with the policy and the database, and is not part of the policies' versions.
    read = stack.call("GET", f"/databases/{DEV}/policy").json()
    assert read["schedule"]["cron"] == "0 2 * * *" and versions(read) == dict.fromkeys(policy.TYPES, 0)
    kept = save(stack, settings={"dataCompaction": {"minInputFiles": 3}}, expectedVersions={"dataCompaction": 0}).json()
    assert kept["policies"]["dataCompaction"]["version"] == 1 and kept["schedule"]["cron"] == "0 2 * * *"
    assert [e["target"] for e in kept["exclusions"]] == [TABLES[2]]
    listed = next(d for d in stack.call("GET", "/databases").json()["databases"] if d["id"] == DEV)
    assert listed["schedule"] == kept["schedule"]
    assert stack.call("DELETE", f"/databases/{DEV}/schedule", "bob").json()["code"] == "forbidden_role"
    removed = stack.call("DELETE", f"/databases/{DEV}/schedule")
    assert removed.status_code == 200 and removed.json() == {"deleted": True, "database": DEV}
    after = stack.call("GET", f"/databases/{DEV}/policy").json()
    assert after["schedule"] is None and after["exists"] and after["policies"]["dataCompaction"]["version"] == 1
    assert stack.call("DELETE", f"/databases/{DEV}/schedule").json()["code"] == "no_schedule"


def test_the_schedule_tools_do_the_same(stack):
    enable(stack)
    refused(stack, "policy_required", "set_schedule", database=DEV, cron="0 2 * * *")
    refused(stack, "no_schedule", "delete_schedule", database=DEV)
    stack.tool("update_policy", database=DEV)
    refused(stack, "forbidden_role", "set_schedule", "bob", database=DEV, cron="0 2 * * *")
    refused(stack, "invalid_cron", "set_schedule", database=DEV, cron="*/5 * * * *")
    made = stack.tool("set_schedule", database=DEV, cron="0 3 * * 0")
    assert made["enabled"] is True and made == stack.call("GET", f"/databases/{DEV}/policy").json()["schedule"]
    assert stack.tool("set_schedule", database=DEV, cron="0 3 * * 0", enabled=False)["nextRunAt"] is None
    assert stack.tool("delete_schedule", database=DEV) == {"deleted": True, "database": DEV}
    refused(stack, "no_schedule", "delete_schedule", database=DEV)


def test_maintenance_needs_a_policy_and_a_write_token(stack):
    enable(stack)
    early = stack.run("maintain")
    # Not the delete's no_policy: that one means there is nothing to remove.
    assert early.status_code == 409 and early.json()["code"] == "policy_required"
    dry = stack.run("dry-run").json()
    assert dry["status"] == "success" and stack.bridge.tokens[-1][1] == "read"
    assert stack.launcher.started[dry["id"]]["job"]["dryRun"] is True
    save(stack, exclusions=[{"target": "raw.transformer_register", "level": "table"}])
    run = stack.run("maintain").json()
    assert run["status"] == "success" and stack.bridge.tokens[-1][1] == "write"
    job = stack.launcher.started[run["id"]]["job"]
    assert job["kind"] == "maintain" and not job["dryRun"] and [t["name"] for t in job["tables"]] == TABLES
    detail = stack.call("GET", f"/runs/{run['id']}", "carol").json()
    assert {r["name"]: r["status"] for r in detail["results"]}["raw.transformer_register"] == "skipped"
    table = stack.call("GET", f"/databases/{DEV}/tables/{READINGS}", "carol").json()
    assert table["state"] == "healthy" and table["lastActions"][0]["status"] == "done"
    # Each action says which policy drove it.
    assert table["lastActions"][0]["policy"] == {"name": "data-compaction", "namespace": "_maintenance", "version": 0,
                                                 "appliedAt": "catalog"}
    assert time.time() - seconds(table["lastMaintainedAt"]) < 60
    assert stack.call("GET", f"/databases/{DEV}/tables/raw.transformer_register").json()["state"] == "excluded"


def test_a_run_has_one_shape_when_started_read_and_listed(stack):
    enable(stack)
    response = stack.run()
    started = response.json()
    # 202 with the run as get returns it, results included, also when wait saw it finish.
    assert response.status_code == 202 and set(started) == RUN_KEYS | {"databaseName", "role", "results"}
    assert started["status"] == "success" and started["mode"] == "inspect" and started["tables"] == []
    assert started["triggeredBy"] == "bob" and started["agent"] == "ext-maintenance-mcp"
    assert started["scheduled"] is False and "schedule" not in started
    assert started["databaseName"] == "grid-capacity" and started["role"] == "writer"
    assert started["exitCode"] == 0 and started["error"] is None
    assert seconds(started["createdAt"]) <= seconds(started["startedAt"]) <= seconds(started["finishedAt"])
    assert [r["name"] for r in started["results"]] == TABLES
    # The summary holds the totals; neither the tables nor the automation principal.
    assert started["summary"]["totals"]["tables"] == 3
    assert "tables" not in started["summary"] and "principal" not in started["summary"]
    read = stack.call("GET", f"/runs/{started['id']}", "bob").json()
    assert read == started
    assert stack.tool("get_run", "bob", run=started["id"]) == read
    listed = stack.call("GET", f"/databases/{DEV}/runs", "bob").json()
    assert list(listed) == ["runs"] and listed["runs"] == [{k: read[k] for k in RUN_KEYS}]
    assert stack.tool("list_runs", "bob", database=DEV) == listed
    # A body is optional, and so is waiting: the run is then returned as it is, with no results yet.
    assert stack.call("POST", f"/databases/{PROD}/runs", "bob").json()["code"] == "environment_not_enabled"
    stack.launcher.hold = True
    queued = stack.call("POST", f"/databases/{DEV}/runs", "bob")
    assert queued.status_code == 202 and queued.json()["status"] in ("queued", "running")
    assert queued.json()["results"] == [] and queued.json()["finishedAt"] is None
    stack.call("POST", f"/runs/{queued.json()['id']}/cancel", "bob")
    assert len(stack.call("GET", f"/databases/{DEV}/runs?limit=1").json()["runs"]) == 1
    for limit in (0, 201):
        response = stack.call("GET", f"/databases/{DEV}/runs?limit={limit}")
        assert response.status_code == 422 and response.json()["code"] == "invalid_request"
    with pytest.raises(ToolError):
        stack.tool("list_runs", database=DEV, limit=201)
    unknown = stack.call("GET", "/runs/run-" + "0" * 24)
    assert unknown.status_code == 404 and unknown.json()["code"] == "unknown_run"


def test_a_run_log_returns_its_last_lines(stack):
    enable(stack)
    run = stack.run("dry-run").json()
    logs = stack.call("GET", f"/runs/{run['id']}/logs", "carol").json()
    assert logs == {"run": run["id"], "status": "success", "lines": ["Maintenance runner 0.0.1: maintain"]}
    (stack.launcher.artifacts / run["id"] / "out" / "run.log").write_text("one\ntwo\nthree\n")
    assert stack.call("GET", f"/runs/{run['id']}/logs?limit=2").json()["lines"] == ["two", "three"]
    assert stack.tool("get_run_logs", run=run["id"], limit=1) == {"run": run["id"], "status": "success",
                                                                  "lines": ["three"]}
    assert len(stack.tool("get_run_logs", run=run["id"])["lines"]) == 3
    for limit in (0, 5001):
        response = stack.call("GET", f"/runs/{run['id']}/logs?limit={limit}")
        assert response.status_code == 422 and response.json()["code"] == "invalid_request"
    # tail was the old name: it no longer limits anything.
    assert len(stack.call("GET", f"/runs/{run['id']}/logs?tail=1").json()["lines"]) == 3
    with pytest.raises(ToolError):
        stack.tool("get_run_logs", run=run["id"], limit=5001)


def test_one_run_per_database_and_writers_stop_runs(stack):
    enable(stack)
    stack.launcher.hold = True
    first = stack.call("POST", f"/databases/{DEV}/runs", "bob", json={"mode": "inspect"}).json()
    busy = stack.run()
    assert busy.status_code == 409 and busy.json()["code"] == "run_in_progress"
    assert stack.call("POST", f"/runs/{first['id']}/cancel", "carol").json()["code"] == "forbidden_role"
    for _ in range(100):
        if stack.call("GET", f"/runs/{first['id']}").json()["status"] == "running":
            break
        time.sleep(0.01)
    # Waiting returns the run as it is when the time is up; it is not an error that it still runs.
    waited = stack.call("GET", f"/runs/{first['id']}?wait=1")
    assert waited.status_code == 200 and waited.json()["status"] == "running"
    stopping = stack.call("POST", f"/runs/{first['id']}/cancel", "bob")
    assert stopping.status_code == 200 and stopping.json() == {"cancelling": True, "run": first["id"]}
    # And it returns as soon as the run is final.
    began = time.monotonic()
    final = stack.call("GET", f"/runs/{first['id']}?wait=600").json()
    assert final["status"] == "cancelled" and time.monotonic() - began < 30
    assert stack.tool("get_run", run=first["id"], wait=600)["status"] == "cancelled"
    finished = stack.call("POST", f"/runs/{first['id']}/cancel", "bob")
    assert finished.status_code == 409 and finished.json()["code"] == "run_finished"
    refused(stack, "run_finished", "cancel_run", "bob", run=first["id"])
    for wait in (-1, 601):
        response = stack.call("GET", f"/runs/{first['id']}?wait={wait}")
        assert response.status_code == 422 and response.json()["code"] == "invalid_request"
        assert stack.call("POST", f"/databases/{DEV}/runs", "bob", json={"wait": wait}).status_code == 422
        with pytest.raises(ToolError):
            stack.tool("get_run", run=first["id"], wait=wait)


def test_a_failed_table_makes_a_partial_run(stack):
    enable(stack)
    stack.launcher.fail_table = TABLES[1]
    run = stack.run("dry-run").json()
    assert run["status"] == "partial" and run["error"] == "Some tables failed. The others were maintained."
    assert run["status"] in STATUSES and run["summary"]["totals"]["failed"] == 1
    failed = stack.call("GET", f"/databases/{DEV}/tables/{TABLES[1]}").json()
    assert failed["state"] == "failed" and failed["lastError"] == "commit conflict"


def test_a_table_filter_keeps_other_tables(stack):
    enable(stack)
    stack.run()
    stack.polaris.drop_table(DEV, TABLES[1])
    stack.polaris.drop_table(DEV, TABLES[2])
    assert stack.run(tables=[TABLES[0]]).json()["tables"] == [TABLES[0]]
    assert stack.call("GET", f"/tables?database={DEV}").json()["total"] == 3
    # A complete health check forgets tables that are gone.
    stack.run()
    assert [t["name"] for t in stack.call("GET", f"/tables?database={DEV}").json()["tables"]] == [TABLES[0]]


def test_schedules_maintain_and_skip_busy_databases(stack):
    enable(stack)
    save(stack)
    assert schedule(stack, cron="0 2 * * *").json()["lastRun"] is None
    scheduler = Scheduler(stack.services, interval=0)
    started = stack.client.portal.call(scheduler.tick, time.time() + 2 * 86400)
    assert len(started) == 1
    stored = stack.services.store.run(started[0])
    assert stored["kind"] == "maintain" and stored["triggered_by"] == "schedule" and stored["schedule"] == "0 2 * * *"
    run = stack.call("GET", f"/runs/{started[0]}?wait=10").json()
    # What is stored as the cron text is shown as a yes or no.
    assert run["scheduled"] is True and run["triggeredBy"] == "schedule" and run["agent"] is None
    assert run["mode"] == "maintain" and "schedule" not in run
    # The next time is moved forward even when nothing starts.
    assert stack.client.portal.call(scheduler.tick, time.time() + 2 * 86400) == []
    # The schedule names the last run it started, not a later one that a person started.
    by_hand = stack.run().json()
    assert by_hand["scheduled"] is False
    read = stack.call("GET", f"/databases/{DEV}/policy").json()["schedule"]
    assert read["lastRun"] == started[0] and seconds(read["nextRunAt"]) > time.time() + 2 * 86400
    listed = next(d for d in stack.call("GET", "/databases").json()["databases"] if d["id"] == DEV)
    assert listed["schedule"]["lastRun"] == started[0] and listed["lastRun"]["id"] == by_hand["id"]
    paused = schedule(stack, cron="0 2 * * *", enabled=False).json()
    assert paused == {"cron": "0 2 * * *", "enabled": False, "nextRunAt": None, "lastRun": started[0]}
    assert stack.client.portal.call(scheduler.tick, time.time() + 9 * 86400) == []


def test_agents_inspect_with_one_tool_and_maintain_with_another(stack):
    enable(stack)
    refused(stack, "forbidden_role", "inspect_database", "carol", database=DEV)
    inspected = stack.tool("inspect_database", "bob", database=DEV, wait=5)
    assert inspected["status"] == "success" and inspected["mode"] == "inspect" and len(inspected["results"]) == 3
    assert stack.bridge.tokens[-1][1] == "read"
    dry = stack.tool("inspect_database", "bob", database=DEV, mode="dry-run", tables=[READINGS], wait=5)
    assert dry["mode"] == "dry-run" and dry["tables"] == [READINGS] and stack.bridge.tokens[-1][1] == "read"
    # No policy applies yet, so a dry run plans nothing.
    assert dry["results"][0]["actions"][0]["status"] == "off"
    # The inspect tool cannot maintain, and the maintain tool does nothing else.
    with pytest.raises(ToolError):
        stack.tool("inspect_database", "bob", database=DEV, mode="maintain", wait=5)
    refused(stack, "policy_required", "run_maintenance", "bob", database=DEV)
    stack.tool("update_policy", database=DEV)
    planned = stack.tool("inspect_database", "bob", database=DEV, mode="dry-run", tables=[READINGS], wait=5)
    assert planned["results"][0]["actions"][0]["status"] == "planned"
    maintained = stack.tool("run_maintenance", "bob", database=DEV, wait=5)
    assert maintained["mode"] == "maintain" and maintained["status"] == "success"
    assert stack.bridge.tokens[-1][1] == "write"
    assert maintained == stack.call("GET", f"/runs/{maintained['id']}", "bob").json()
    # Without wait a tool returns at once, and get_run waits for the end.
    started = stack.tool("inspect_database", "bob", database=DEV)
    assert stack.tool("get_run", "bob", run=started["id"], wait=30)["status"] == "success"


def test_the_result_is_reduced_to_what_the_api_shows():
    summary = summarize({"status": "success", "totals": {"tables": 1, "bad": "x"}, "tables": [
        {"name": "a.b", "status": "success", "actions": [{"operation": "compaction", "status": "done", "detail": "x",
                                                         "metrics": {"inputFiles": 3, "nested": {"no": 1}}}]}]})
    assert summary["totals"] == {"tables": 1}
    assert summary["tables"][0]["actions"][0]["metrics"] == {"inputFiles": 3}
    assert summarize(None) == {}


def test_attention_reasons():
    from maintenance.services import applied_policies, attention

    health = {"compactableFiles": 5, "expirableSnapshots": 2, "positionDeleteFiles": 5, "manifests": 101}
    # Without a policy the defaults judge a table.
    none = applied_policies({})
    assert attention({"compactableFiles": 4}, none) == []
    assert attention(health, none) == ["5 files to compact", "2 snapshots to expire", "5 delete files",
                                       "101 manifests"]
    source = {"name": "p", "namespace": "_maintenance", "version": 0, "appliedAt": "catalog"}
    on = {key: {"settings": settings, "policy": source, "problem": None} for key, settings in policy.defaults().items()}
    on["dataCompaction"]["settings"] = {**on["dataCompaction"]["settings"], "minInputFiles": 50}
    assert attention(health, on) == ["2 snapshots to expire", "5 delete files", "101 manifests"]
    excluded = {key: {"settings": {**settings, "enabled": False}, "policy": {**source, "appliedAt": "table"},
                      "problem": None} for key, settings in policy.defaults().items()}
    assert attention(health, excluded) == []


def test_browser_writes_need_the_api_header(stack):
    response = stack.client.patch(f"/api/v1/databases/{DEV}/policy", json={})
    assert response.status_code == 403 and response.json()["code"] == "csrf"


def test_every_error_has_the_same_body(stack, monkeypatch):
    unknown = stack.call("GET", "/nothing/here")
    assert unknown.status_code == 404 and unknown.json() == {"error": "Not found.", "code": "not_found"}
    wrong = stack.call("POST", "/me")
    assert wrong.status_code == 405 and wrong.json()["code"] == "method_not_allowed" and wrong.json()["error"]
    assert stack.client.get("/missing.js").json() == {"error": "Not found.", "code": "not_found"}
    invalid = stack.call("GET", "/databases/not-an-id/policy")
    assert invalid.status_code == 422 and set(invalid.json()) == {"error", "code"}
    assert invalid.json()["code"] == "invalid_request"
    assert stack.call("GET", "/me", "mallory").json()["code"] == "unauthorized"

    async def broken(caller):
        raise RuntimeError("boom")

    monkeypatch.setattr(stack.services, "whoami", broken)
    failed = stack.call("GET", "/me")
    assert failed.status_code == 500 and failed.json() == {"error": "Something went wrong.", "code": "internal_error"}
    assert MaintenanceError(418, "teapot").code == "internal_error" and MaintenanceError(405, "no").code == (
        "method_not_allowed")


def test_every_operation_has_an_mcp_tool_and_the_contract_is_current(stack):
    schema = stack.app.openapi()
    operations = {op["operationId"] for path in schema["paths"].values() for op in path.values()}
    assert operations == set(OPERATIONS)
    tools = stack.client.portal.call(_tools, stack.app)
    # One operation may have two tools: starting a run is inspect_database and run_maintenance.
    expected = {name for value in OPERATIONS.values() for name in ((value,) if isinstance(value, str) else value)}
    assert expected == set(tools) and len(tools) == 18
    assert OPERATIONS["runMaintenance"] == ("inspect_database", "run_maintenance")
    contract = ROOT / "contracts" / "maintenance-api" / "v1" / "openapi.yaml"
    assert json.loads(contract.read_text()) == json.loads(json.dumps(schema)), (
        "Regenerate extensions/maintenance/contracts/maintenance-api/v1/openapi.yaml with "
        "`uv run python -m scripts.api_contract`")


def test_the_contract_names_the_operations_and_their_shapes(stack):
    schema = stack.app.openapi()
    paths = schema["paths"]
    found = {op["operationId"]: (method, path) for path, item in paths.items() for method, op in item.items()}
    assert found["listTableHealth"] == ("get", "/api/v1/tables")
    assert found["getTableHealth"] == ("get", "/api/v1/databases/{database}/tables/{table}")
    assert found["updatePolicy"] == ("patch", "/api/v1/databases/{database}/policy")
    assert found["setSchedule"] == ("put", "/api/v1/databases/{database}/schedule")
    assert found["deleteSchedule"] == ("delete", "/api/v1/databases/{database}/schedule")
    assert not {"listTables", "getTable", "setPolicy"} & set(found)
    models = {"whoami": "Me", "listEnvironments": "EnvironmentList", "enableEnvironment": "Environment",
              "disableEnvironment": "Environment", "listDatabases": "DatabaseList",
              "listTableHealth": "TableHealthList", "getPolicy": "Policy", "updatePolicy": "Policy",
              "setSchedule": "Schedule", "deleteSchedule": "Deleted", "runMaintenance": "Run", "listRuns": "RunList",
              "getRun": "Run", "getRunLogs": "RunLogs", "cancelRun": "CancelResult"}
    for operation, (method, path) in found.items():
        responses = paths[path][method]["responses"]
        success = "202" if operation == "runMaintenance" else "200"
        assert set(responses) == {success, "401", "403", "404", "409", "422", "500", "502", "503"}, operation
        for status in set(responses) - {success}:
            assert responses[status]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/Error"}, (operation, status)
        if operation in models:
            assert responses[success]["content"]["application/json"]["schema"] == {
                "$ref": f"#/components/schemas/{models[operation]}"}, operation
    schemas = schema["components"]["schemas"]
    assert "HTTPValidationError" not in schemas and set(schemas["Error"]["required"]) == {"error", "code"}
    assert set(schemas["Schedule"]["properties"]) == {"cron", "enabled", "nextRunAt", "lastRun"}
    assert set(schemas["PolicyInput"]["properties"]) == {"settings", "exclusions", "expectedVersions"}
    assert set(schemas["Settings"]["properties"]) == set(policy.TYPES)
    assert set(schemas["AppliedPolicy"]["properties"]["appliedAt"]["enum"]) == {"catalog", "namespace", "table"}
    assert set(schemas["RunSummary"]["properties"]) == RUN_KEYS
    assert set(schemas["RunSummary"]["properties"]["status"]["enum"]) == STATUSES
    parameters = {p["name"]: p["schema"] for p in paths["/api/v1/runs/{run}/logs"]["get"]["parameters"]}
    assert parameters["limit"] == {"type": "integer", "maximum": 5000, "minimum": 1, "default": 500,
                                   "title": "Limit"} and "tail" not in parameters
    wait = next(p for p in paths["/api/v1/runs/{run}"]["get"]["parameters"] if p["name"] == "wait")["schema"]
    assert (wait["minimum"], wait["maximum"], wait["default"]) == (0, 600, 0)
    limit = next(p for p in paths["/api/v1/tables"]["get"]["parameters"] if p["name"] == "limit")["schema"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 5000, 500)


def test_the_tools_say_what_they_take_and_what_they_change(stack):
    tools = stack.client.portal.call(_tools, stack.app)
    takes = {name: set(tool.input_schema["properties"]) for name, tool in tools.items()}
    assert not {"list_tables", "get_table", "set_policy"} & set(tools)
    assert takes["inspect_database"] == {"database", "mode", "tables", "wait"}
    assert tools["inspect_database"].input_schema["properties"]["mode"]["enum"] == ["inspect", "dry-run"]
    assert takes["run_maintenance"] == {"database", "tables", "wait"}
    assert takes["update_policy"] == {"database", "settings", "exclusions", "expected_versions"}
    assert takes["set_schedule"] == {"database", "cron", "enabled"} and takes["delete_schedule"] == {"database"}
    assert takes["get_run"] == {"run", "wait"} and takes["get_run_logs"] == {"run", "limit"}
    assert takes["list_table_health"] == {"database", "team", "environment", "attention", "limit"}
    assert takes["get_table_health"] == {"database", "table"} and takes["list_runs"] == {"database", "limit"}
    for name in ("inspect_database", "run_maintenance", "get_run"):
        wait = tools[name].input_schema["properties"]["wait"]
        assert (wait["minimum"], wait["maximum"], wait["default"]) == (0, 600, 0), name
    hints = {name: (tool.annotations.read_only_hint, tool.annotations.destructive_hint,
                    tool.annotations.idempotent_hint, tool.annotations.open_world_hint)
             for name, tool in tools.items()}
    read, write, destructive = (True, False, True, False), (False, False, False, False), (False, True, False, False)
    # A health check or a dry run changes no table; only maintaining is destructive.
    assert hints["inspect_database"] == write and hints["run_maintenance"] == destructive
    assert {name for name, hint in hints.items() if hint == destructive} == {
        "run_maintenance", "delete_policy", "delete_schedule", "disable_environment"}
    assert {name for name, hint in hints.items() if hint == write} == {
        "inspect_database", "update_policy", "set_schedule", "enable_environment", "cancel_run"}
    assert {name for name, hint in hints.items() if hint == read} == {
        "whoami", "list_environments", "list_databases", "list_table_health", "get_table_health", "get_policy",
        "list_runs", "get_run", "get_run_logs"}


async def _tools(app):
    from maintenance.mcp_server import create_mcp

    services = app.state.services
    mcp = create_mcp(services.settings, services, None)
    return {tool.name: tool for tool in await mcp.list_tools()}


def test_supports_the_platforms_bridge_contract():
    supported = contract_range()
    assert in_range("1.0.0", supported) and in_range("1.4.2", supported)
    assert not in_range("0.1.4", supported) and not in_range("2.0.0", supported)


@pytest.mark.parametrize("value", [
    [{"target": "a", "level": "table"}], [{"target": "a.b", "level": "view"}],
    [{"target": "x" * 400, "level": "namespace"}], [{"target": "_maintenance.t", "level": "table"}],
    [{"target": "a.b", "level": "table", "types": ["vacuum"]}], "a.b", [{"target": "a.b", "level": "table"}] * 501])
def test_exclusions_are_checked(value):
    with pytest.raises(MaintenanceError) as error:
        policy.exclusions(value)
    assert error.value.code == "invalid_exclusions"


def test_a_sign_in_belongs_to_the_browser_that_started_it(tmp_path):
    """A callback URL sent to another browser signs nobody in (login CSRF)."""
    import asyncio

    import httpx
    import pytest

    from maintenance.auth import BrowserLogin
    from maintenance.errors import MaintenanceError
    from tests.conftest import FakeVerifier, settings

    token = httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "alice",
                                                                          "refresh_token": "r"}))
    login = BrowserLogin(settings(tmp_path), FakeVerifier(), httpx.AsyncClient(transport=token))
    url, binding = login.start("/#/runs")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    with pytest.raises(MaintenanceError):
        asyncio.run(login.finish(state, "code", "someone-elses-binding"))
    # The failed attempt used up the state.
    with pytest.raises(MaintenanceError):
        asyncio.run(login.finish(state, "code", binding))
    url, binding = login.start("/#/runs")
    state = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))["state"]
    session, target = asyncio.run(login.finish(state, "code", binding))
    assert session in login.sessions and target == "/#/runs"


def test_the_login_route_sets_the_binding_cookie(stack):
    response = stack.client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "iceberg_maintenance_login=" in cookie and "HttpOnly" in cookie and "Path=/auth/callback" in cookie
    state = response.headers["location"].split("state=", 1)[1].split("&", 1)[0]
    stack.client.cookies.clear()
    failed = stack.client.get(f"/auth/callback?state={state}&code=x", follow_redirects=False)
    assert failed.headers["location"] == "/?signin=failed"


@pytest.mark.parametrize(("key", "content", "problem"), [
    # Another engine's units or extra scope would be ignored, so the policy is not run at all.
    ("orphanFileRemoval", {"enable": True, "config": {"max_orphan_file_age_in_days": 30}}, "config.max_orphan"),
    ("orphanFileRemoval", {"enable": True, "max_orphan_file_age_in_days": 30, "locations": ["s3://b/t/data"]},
     "locations"),
    ("orphanFileRemoval", {"enable": True, "max_orphan_file_age_in_days": 30,
                           "config": {"prefix_mismatch_mode": "ignore"}}, "config.prefix_mismatch_mode"),
    ("orphanFileRemoval", {"enable": True}, "no max_orphan_file_age_in_days"),
    ("snapshotExpiry", {"enable": True, "config": {"max_snapshot_age_ms": 86400000}}, "config.max_snapshot_age_ms"),
    ("snapshotExpiry", {"enable": True, "config": {"min_snapshot_to_keep": 3}}, "no max_snapshot_age_days"),
    ("snapshotExpiry", {"enable": True, "config": {"max_snapshot_age_days": "nan"}}, "finite"),
    ("dataCompaction", {"enable": True, "config": {"min-input-files": "inf"}}, "finite"),
])
def test_a_destructive_policy_maintenance_cannot_follow_exactly_is_not_run(key, content, problem):
    settings, found = policy.from_content(key, {"version": "2025-02-03", **content})
    assert not settings["enabled"] and problem in found


def test_a_policy_maintenance_can_follow_is_run_as_written():
    settings, problem = policy.from_content("snapshotExpiry", {"version": "2025-02-03", "enable": True, "config": {
        "max_snapshot_age_days": 30, "min_snapshot_to_keep": "3", "max_ref_age_days": 90}})
    assert problem is None and settings == {"enabled": True, "maxSnapshotAgeDays": 30, "minSnapshotsToKeep": 3}
    # Switched off, what else it holds does not matter.
    assert policy.from_content("orphanFileRemoval", {"enable": False, "locations": ["s3://b"]})[1] is None
    # Maintenance's own content always passes.
    for key in policy.TYPES:
        on = {**policy.defaults()[key], "enabled": True}
        assert policy.from_content(key, policy.to_content(key, on)) == (on, None)
