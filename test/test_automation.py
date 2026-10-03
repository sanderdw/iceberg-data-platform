import pytest

from server.models import DatabaseInput, ServiceError, ShareInput, TeamInput, UserInput
from test.conftest import MemoryPolaris, members

CREATOR = {"id": "portal-" + "c" * 32, "name": "team-admin"}


@pytest.fixture
def stack():
    provider = MemoryPolaris()
    team = provider.save_team(TeamInput(name="data-team"))["id"]
    dev = provider.create_database(DatabaseInput(name="sales", team=team))["id"]
    prod = provider.create_database(DatabaseInput(name="sales", team=team, environment="production"))["id"]
    return provider, team, dev, prod


def assigned(provider, role, database):
    """The catalog roles a principal role holds on a database."""
    prefix = f"/principal-roles/{role}/catalog-roles/{database}/"
    return {g.removeprefix(prefix) for g in provider.grants if g.startswith(prefix)}


def automation_state(provider):
    return (
        sorted(n for kind in ("principals", "principal-roles") for n in provider.resources[kind] if "svc-" in n),
        {g for g in provider.grants if "svc-" in g},
    )


def test_grants_one_environment_and_activates_last(stack):
    provider, team, dev, prod = stack
    item, created = provider.create_automation(team, "development", "dbt", CREATOR)
    id = item["id"]
    assert created and id.startswith("svc-") and item["status"] == "active"
    assert (item["team"], item["environment"], item["extension"], item["createdBy"]) == (
        team, "development", "dbt", "team-admin")
    assert assigned(provider, id, dev) == {"writer"}
    assert assigned(provider, id + "-read", dev) == {"reader"}
    assert assigned(provider, id, prod) == set() and assigned(provider, id + "-read", prod) == set()
    # Both principal roles are granted to the principal only after all catalog grants.
    activations = [e for e in provider.events if e[0] == f"/principals/{id}/principal-roles"]
    assert [e[2]["principalRole"]["name"] for e in activations] == [id, id + "-read"]
    assert provider.events[-1][0] == f"/principals/{id}/principal-roles"
    assert provider.list_users() == []
    # Idempotent per team, environment and extension.
    again, created = provider.create_automation(team, "development", "dbt", CREATOR)
    assert (again["id"], created) == (id, False)
    other, created = provider.create_automation(team, "development", "other-tool", CREATOR)
    assert created and other["id"] != id


def test_automation_principal_is_never_a_user(portal):
    p = portal
    team = p.post("/teams", {"name": "data-team"}).json()["id"]
    user = p.post("/users", {"name": "alice", "memberships": members([team])}).json()["user"]
    item, _ = p.provider.create_automation(team, "development", "dbt", CREATOR)
    assert [u["id"] for u in p.client.get("/api/users").json()] == [user["id"]]
    assert p.patch(f"/users/{item['id']}", {"memberships": members([team])}).status_code == 404
    assert p.delete(f"/users/{item['id']}").status_code == 404
    # Database provisioning iterates access holders; an automation principal must not break it.
    assert p.post("/databases", {"name": "analytics", "team": team}).status_code == 201
    # Legacy compatibility: an older portal reads every non-share principal as a user.
    principal = p.provider.resources["principals"][item["id"]]
    assert p.provider.user(principal)["memberships"] == []


def test_database_lifecycle_follows_the_environment(stack):
    provider, team, dev, prod = stack
    id = provider.create_automation(team, "development", "dbt", CREATOR)[0]["id"]
    prod_id = provider.create_automation(team, "production", "dbt", CREATOR)[0]["id"]
    added = provider.create_database(DatabaseInput(name="marketing", team=team))["id"]
    assert assigned(provider, id, added) == {"writer"} and assigned(provider, prod_id, added) == set()
    assert assigned(provider, prod_id, prod) == {"writer"}

    other = provider.save_team(TeamInput(name="other-team"))["id"]
    other_id = provider.create_automation(other, "development", "dbt", CREATOR)[0]["id"]
    provider.move_database(added, other)
    assert assigned(provider, id, added) == set() and assigned(provider, id + "-read", added) == set()
    assert assigned(provider, other_id, added) == {"writer"}
    assert assigned(provider, other_id + "-read", added) == {"reader"}

    provider.delete_database(added)
    assert assigned(provider, other_id, added) == set()
    assert [d["id"] for d in provider.automation_scope(id)["databases"]] == [dev]


def test_scope_lists_only_the_environment_and_checks_the_extension(stack):
    provider, team, _, prod = stack
    id = provider.create_automation(team, "production", "dbt", CREATOR)[0]["id"]
    scope = provider.automation_scope(id, extension="dbt")
    assert [(d["id"], d["name"], d["environment"]) for d in scope["databases"]] == [(prod, "sales", "production")]
    with pytest.raises(ServiceError) as exc:
        provider.automation_scope(id, extension="other-tool")
    assert exc.value.status == 404
    with pytest.raises(ServiceError):
        provider.automation_scope("svc-" + "f" * 32)


@pytest.mark.parametrize(
    "failure",
    [
        lambda path, method, body: path == "/principal-roles" and method == "POST"
        and body["principalRole"]["name"].endswith("-read"),
        lambda path, method, body: path == "/principals" and method == "POST",
        lambda path, method, body: "/principal-roles/svc-" in path and "-read/" in path and method == "PUT",
        lambda path, method, body: path.startswith("/principal-roles/team-") and method == "PUT",
    ],
)
def test_failed_create_leaves_nothing_behind(stack, failure):
    provider, team, *_ = stack
    before = automation_state(provider)
    provider.fail = failure
    with pytest.raises(ServiceError):
        provider.create_automation(team, "development", "dbt", CREATOR)
    provider.fail = None
    assert automation_state(provider) == before
    assert provider.list_automation() == []


def test_revocation_removes_everything_and_resumes(stack):
    provider, team, *_ = stack
    before = automation_state(provider)
    id = provider.create_automation(team, "development", "dbt", CREATOR)[0]["id"]
    # An interrupted revocation leaves its marker; the next listing completes it.
    provider.fail = lambda path, method, body: path == f"/principals/{id}" and method == "DELETE"
    with pytest.raises(ServiceError):
        provider.delete_automation(id)
    provider.fail = None
    assert provider.resources["principals"][id]["properties"]["portal.deleting"] == "true"
    with pytest.raises(ServiceError):
        provider.require_automation(id)
    assert provider.list_automation() == []
    assert automation_state(provider) == before
    provider.delete_automation(id)  # Already gone: idempotent.


def test_team_deletion_revokes_its_automation_principals():
    provider = MemoryPolaris()
    team = provider.save_team(TeamInput(name="data-team"))["id"]
    keep = provider.save_team(TeamInput(name="keep-team"))["id"]
    provider.create_user(UserInput(name="alice", memberships=members([team, keep])))
    provider.create_automation(team, "development", "dbt", CREATOR)
    kept = provider.create_automation(keep, "development", "dbt", CREATOR)[0]
    provider.delete_team(team)
    assert provider.list_automation() == [kept]
    assert not [n for n in provider.resources["principal-roles"] if n.startswith("svc-") and kept["id"] not in n]


def test_orphans_of_a_removed_team_are_revoked(stack):
    provider, team, *_ = stack
    provider.create_automation(team, "development", "dbt", CREATOR)
    del provider.resources["principal-roles"][team]
    assert provider.list_automation() == []
    assert not [n for n in provider.resources["principals"] if n.startswith("svc-")]


def test_create_is_fenced_by_the_team(stack):
    provider, team, *_ = stack
    with pytest.raises(ServiceError) as exc:
        provider.create_automation("team-" + "f" * 32, "development", "dbt", CREATOR)
    assert exc.value.status == 404
    provider.resources["principal-roles"][team]["properties"]["portal.deleting"] = "true"
    with pytest.raises(ServiceError) as exc:
        provider.create_automation(team, "development", "dbt", CREATOR)
    assert exc.value.status == 409


def test_reconcile_grants_missing_databases(stack):
    provider, team, dev, _ = stack
    id = provider.create_automation(team, "development", "dbt", CREATOR)[0]["id"]
    provider.grants = {g for g in provider.grants if f"/principal-roles/{id}/" not in g}
    scope = provider.reconcile_automation(id)
    assert [d["id"] for d in scope["databases"]] == [dev]
    assert assigned(provider, id, dev) == {"writer"}


def test_platform_administrators_see_and_revoke(portal, monkeypatch):
    monkeypatch.setenv("PLATFORM_EXTENSIONS", "dbt=http://localhost:3004")
    p = portal
    team = p.post("/teams", {"name": "data-team"}).json()["id"]
    item, _ = p.provider.create_automation(team, "production", "dbt", CREATOR)
    overview = p.client.get("/api/overview").json()
    assert [i["id"] for i in overview["automationPrincipals"]] == [item["id"]]
    assert overview["extensions"] == [{"id": "dbt", "origin": "http://localhost:3004"}]
    assert [i["id"] for i in p.client.get("/api/automation-principals").json()] == [item["id"]]
    assert p.delete("/automation-principals/share-" + "a" * 32).status_code == 422
    assert p.delete(f"/automation-principals/{item['id']}").json() == {"deleted": True}
    assert p.delete(f"/automation-principals/{item['id']}").status_code == 404
    assert p.client.get("/api/automation-principals").json() == []


# Capability `shared-data`: team shares reach the recipient team's automation read role.

MODEL = {"kind": "semantic-model", "namespace": ["ops"], "name": "operations"}
FLIGHTS = {"kind": "table", "namespace": ["ops"], "name": "flights"}


@pytest.fixture
def partner(stack):
    provider, team, *_ = stack
    owner = provider.save_team(TeamInput(name="partner-team"))["id"]
    source = provider.create_database(DatabaseInput(name="ops", team=owner))["id"]
    return provider, team, owner, source


def share_with(provider, database, team, objects=(MODEL, FLIGHTS), name="flights"):
    return provider.create_share(ShareInput.model_validate({
        "database": database, "name": name, "external": False, "recipientTeam": team, "objects": list(objects),
    }), CREATOR)["share"]


def test_received_share_reaches_the_read_role_of_its_environment_only(partner):
    provider, team, _, source = partner
    dev = provider.create_automation(team, "development", "dbt", CREATOR)[0]["id"]
    prod = provider.create_automation(team, "production", "dbt", CREATOR)[0]["id"]
    share = share_with(provider, source, team)
    assert assigned(provider, dev + "-read", source) == {share["id"]}
    assert assigned(provider, dev, source) == set()
    assert assigned(provider, prod + "-read", source) == set() and assigned(provider, prod, source) == set()


def test_principal_created_after_a_share_gets_it_before_activation(partner):
    provider, team, _, source = partner
    share = share_with(provider, source, team)
    id = provider.create_automation(team, "development", "dbt", CREATOR)[0]["id"]
    grant = f"/principal-roles/{id}-read/catalog-roles/{source}"
    assert assigned(provider, id + "-read", source) == {share["id"]}
    order = [e[0] for e in provider.events]
    granted = max(i for i, e in enumerate(provider.events) if e[0] == grant and e[1] == "PUT")
    assert granted < order.index(f"/principals/{id}/principal-roles")


@pytest.mark.parametrize("end", ["revoke", "expire", "delete-database"])
def test_share_end_removes_the_automation_grant(partner, end):
    from datetime import UTC, datetime, timedelta

    provider, team, _, source = partner
    id = provider.create_automation(team, "development", "dbt", CREATOR)[0]["id"]
    share = share_with(provider, source, team)
    if end == "revoke":
        provider.delete_share(share["id"])
    elif end == "expire":
        path = f"/principals/{share['id']}"
        properties = provider.require(path)["properties"]
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        provider.update_properties(path, {**properties, "portal.expires-at": past})
        provider.expire_shares()
    else:
        provider.delete_database(source)
    assert assigned(provider, id + "-read", source) == set()
    assert provider.automation_scope(id)["sharedDatabases"] == []


def test_reconcile_regrants_a_missing_share(partner):
    provider, team, _, source = partner
    id = provider.create_automation(team, "development", "dbt", CREATOR)[0]["id"]
    share = share_with(provider, source, team)
    provider.grants.discard(f"/principal-roles/{id}-read/catalog-roles/{source}/{share['id']}")
    provider.reconcile_automation(id)
    assert assigned(provider, id + "-read", source) == {share["id"]}


def test_scope_lists_granted_objects_of_received_shares_only(partner):
    provider, team, owner, source = partner
    id = provider.create_automation(team, "development", "dbt", CREATOR)[0]["id"]
    share_with(provider, source, team)
    # A share to another team and a share from another environment stay out of scope.
    elsewhere = provider.save_team(TeamInput(name="elsewhere"))["id"]
    share_with(provider, source, elsewhere, objects=[FLIGHTS], name="other")
    prod = provider.create_database(DatabaseInput(name="ops", team=owner, environment="production"))["id"]
    share_with(provider, prod, team, objects=[FLIGHTS], name="prod")
    provider.drop(["ops"], "flights")
    [shared] = provider.automation_scope(id)["sharedDatabases"]
    assert shared["id"] == source and shared["team"] == owner
    # The dropped table's grant is gone; the model is still readable.
    assert shared["sharedObjects"] == [MODEL]
