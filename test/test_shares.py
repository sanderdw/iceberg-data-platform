import json
from datetime import UTC, datetime, timedelta

import pytest

from server.models import DatabaseInput, ServiceError, ShareInput, ShareUpdate, TeamInput
from server.polaris import MANAGED, MAX_SHARES
from test.conftest import MemoryPolaris, members
from test.test_portal import team, user

SHARE = "share-" + "a" * 32
CREATOR = {"id": "portal-" + "c" * 32, "name": "team-admin"}
TABLE = {"kind": "table", "namespace": ["sales"], "name": "orders"}
OTHER = {"kind": "table", "namespace": ["sales", "eu"], "name": "returns"}
VIEW = {"kind": "view", "namespace": ["sales"], "name": "report"}


def share_principal(provider, database="db-" + "b" * 32):
    properties = {
        "portal.managed-by": MANAGED,
        "portal.kind": "share",
        "portal.name": "partner",
        "portal.database": database,
        "portal.objects": "[]",
    }
    provider.management("/principals", "POST", {"principal": {"name": SHARE, "properties": properties}})


@pytest.fixture
def stack():
    provider = MemoryPolaris()
    owner = provider.save_team(TeamInput(name="data-team"))["id"]
    db = provider.create_database(DatabaseInput(name="analytics", team=owner))["id"]
    return provider, db


def share_input(db, objects=(TABLE, VIEW), name="partner", **extra):
    return ShareInput.model_validate({"database": db, "name": name, "objects": list(objects), **extra})


def share_state(provider):
    """Everything a share leaves behind in Polaris."""
    return (
        sorted(n for kind in ("principals", "principal-roles") for n in provider.resources[kind] if "share-" in n),
        sorted(n for roles in provider.catalog_roles.values() for n in roles if n.startswith("share-")),
        set(provider.grants),
    )


def test_share_principal_is_never_a_user(portal):
    p = portal
    owner = team(p)
    u = user(p, [owner])
    # An orphaned share would be revoked by the first listing; this one has its database.
    share_principal(p.provider, p.post("/databases", {"name": "shared", "team": owner}).json()["id"])
    assert [x["id"] for x in p.client.get("/api/users").json()] == [u["id"]]
    assert [x["id"] for x in p.client.get("/api/overview").json()["users"]] == [u["id"]]
    assert p.patch(f"/users/{SHARE}", {"memberships": members([owner])}).status_code == 404
    assert p.delete(f"/users/{SHARE}").status_code == 404
    assert SHARE in p.provider.resources["principals"]
    # Team deletion and database provisioning iterate users; a share must not break them.
    second = team(p, "second-team")
    assert p.post("/databases", {"name": "analytics", "team": second}).status_code == 201
    assert p.delete(f"/teams/{owner}").status_code == 409


def test_create_grants_only_the_selected_objects_and_activates_last(stack):
    provider, db = stack
    result = provider.create_share(share_input(db, recipient="Partner BV"), CREATOR)
    id = result["share"]["id"]
    assert provider.catalog_roles[db][id] == [
        {"type": "table", "namespace": ["sales"], "tableName": "orders", "privilege": "TABLE_READ_DATA"},
        {"type": "view", "namespace": ["sales"], "viewName": "report", "privilege": "VIEW_READ_PROPERTIES"},
    ]
    assert f"/principal-roles/{id}/catalog-roles/{db}/{id}" in provider.grants
    assert provider.events[-2][:2] == (f"/principals/{id}/principal-roles", "PUT")
    assert result["credentials"] == {"clientId": id, "clientSecret": "secret"}
    assert result["connection"]["credential"] == f"{id}:secret"
    assert result["connection"]["warehouse"] == db
    assert result["connection"]["identifiers"] == [
        {"kind": "table", "identifier": "sales.orders"},
        {"kind": "view", "identifier": "sales.report"},
    ]
    assert "bucket" not in result["connection"]
    assert "secret" not in json.dumps(provider.resources["principals"][id])
    share = provider.list_shares(db)[0]
    assert (share["recipient"], share["createdBy"], share["status"]) == ("Partner BV", "team-admin", "active")
    assert provider.list_users() == [] and len(provider.list_teams()) == 1


@pytest.mark.parametrize(
    "failure",
    [
        lambda path, method, body: path.endswith("/grants") and body["grant"].get("viewName"),
        lambda path, method, body: path == "/principal-roles" and method == "POST",
        lambda path, method, body: "/principal-roles/share-" in path and method == "PUT",
        lambda path, method, body: path == "/principals" and method == "POST",
        lambda path, method, body: path.endswith("/principal-roles") and path.startswith("/principals/share-"),
    ],
)
def test_failed_create_leaves_nothing_behind(stack, failure):
    provider, db = stack
    before = share_state(provider)
    provider.fail = failure
    with pytest.raises(ServiceError):
        provider.create_share(share_input(db), CREATOR)
    provider.fail = None
    assert share_state(provider) == before
    assert provider.list_shares() == []


def test_create_preflight(stack):
    provider, db = stack
    provider.create_share(share_input(db), CREATOR)
    for data, status in [
        (share_input(db), 409),
        (share_input("db-" + "f" * 32, name="elsewhere"), 404),
    ]:
        with pytest.raises(ServiceError) as exc:
            provider.create_share(data, CREATOR)
        assert exc.value.status == status
    provider.drop(["sales", "eu"], "returns")
    before = share_state(provider)
    with pytest.raises(ServiceError) as exc:
        provider.create_share(share_input(db, (TABLE, OTHER), "dropped"), CREATOR)
    assert exc.value.status == 404 and share_state(provider) == before
    for n in range(MAX_SHARES - 1):
        provider.create_share(share_input(db, name=f"partner-{n}"), CREATOR)
    with pytest.raises(ServiceError) as exc:
        provider.create_share(share_input(db, name="one-too-many"), CREATOR)
    assert exc.value.status == 409


def test_edit_revokes_before_granting_and_reports_drift(stack):
    provider, db = stack
    id = provider.create_share(share_input(db), CREATOR)["share"]["id"]
    provider.events.clear()
    updated = provider.update_share(id, ShareUpdate.model_validate({"objects": [TABLE, OTHER]}))
    calls = [(m, b["grant"].get("tableName") or b["grant"]["viewName"]) for _, m, b in provider.events if b and "grant" in b]
    assert calls == [("POST", "report"), ("PUT", "returns")]
    assert [(o["name"], o["granted"]) for o in updated["objects"]] == [("orders", True), ("returns", True)]
    assert updated["extraGrants"] == []
    # Dropped and recreated: a new entity without the grant. Saving again re-grants it.
    provider.drop(["sales"], "orders")
    drifted = provider.list_shares(db, drift=True)[0]
    assert [(o["name"], o["granted"]) for o in drifted["objects"]] == [("orders", False), ("returns", True)]
    with pytest.raises(ServiceError) as exc:
        provider.update_share(id, ShareUpdate.model_validate({"objects": [TABLE, OTHER]}))
    assert exc.value.status == 404
    provider.missing.clear()
    saved = provider.update_share(id, ShareUpdate.model_validate({"objects": [TABLE, OTHER]}))
    assert all(o["granted"] for o in saved["objects"])
    # Renamed: the grant follows the entity. It shows as an extra and goes at the next save.
    grant = next(g for g in provider.catalog_roles[db][id] if g["tableName"] == "returns")
    grant["tableName"] = "renamed"
    drifted = provider.list_shares(db, drift=True)[0]
    assert [e["name"] for e in drifted["extraGrants"]] == ["renamed"]
    saved = provider.update_share(id, ShareUpdate.model_validate({"objects": [TABLE]}))
    assert saved["extraGrants"] == [] and [g["tableName"] for g in provider.catalog_roles[db][id]] == ["orders"]


def test_edit_without_objects_leaves_grants_alone_and_clears_expiry(stack):
    provider, db = stack
    expiry = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    id = provider.create_share(share_input(db, expiresAt=expiry), CREATOR)["share"]["id"]
    assert provider.list_shares(db)[0]["expiresAt"]
    provider.drop(["sales"], "orders")
    provider.events.clear()
    updated = provider.update_share(id, ShareUpdate.model_validate({"expiresAt": None, "recipient": "New BV"}))
    assert not any(b and "grant" in b for _, _, b in provider.events)
    assert (updated["expiresAt"], updated["recipient"]) == (None, "New BV")
    assert [o["name"] for o in updated["objects"]] == ["orders", "report"]


def test_failed_edit_restores_grants_and_selection(stack):
    provider, db = stack
    id = provider.create_share(share_input(db), CREATOR)["share"]["id"]
    grants = list(provider.catalog_roles[db][id])
    provider.fail = lambda path, method, body: path == f"/principals/{id}" and method == "PUT"
    with pytest.raises(ServiceError):
        provider.update_share(id, ShareUpdate.model_validate({"objects": [OTHER]}))
    provider.fail = None
    assert sorted(provider.catalog_roles[db][id], key=json.dumps) == sorted(grants, key=json.dumps)
    assert [o["name"] for o in provider.list_shares(db)[0]["objects"]] == ["orders", "report"]


def test_rotate_keeps_the_client_id(stack):
    provider, db = stack
    id = provider.create_share(share_input(db), CREATOR)["share"]["id"]
    rotated = provider.rotate_share(id)
    assert rotated["credentials"] == {"clientId": id, "clientSecret": "rotated-secret"}
    assert rotated["connection"]["credential"] == f"{id}:rotated-secret"
    assert provider.events[-2][:2] == (f"/principals/{id}/reset", "POST")


def test_revoke_stops_access_first_and_resumes(stack):
    provider, db = stack
    before = share_state(provider)
    id = provider.create_share(share_input(db), CREATOR)["share"]["id"]
    provider.fail = lambda path, method, body: path == f"/principal-roles/{id}" and method == "DELETE"
    provider.events.clear()
    with pytest.raises(ServiceError):
        provider.delete_share(id)
    deletes = [path for path, method, _ in provider.events if method == "DELETE"]
    assert deletes[0] == f"/principals/{id}/principal-roles/{id}"
    assert provider.resources["principals"][id]["properties"]["portal.deleting"] == "true"
    for action in (
        lambda: provider.update_share(id, ShareUpdate.model_validate({"recipient": "x"})),
        lambda: provider.rotate_share(id),
    ):
        with pytest.raises(ServiceError) as exc:
            action()
        assert exc.value.status == 409
    provider.fail = None
    # Any listing resumes a half-finished revocation.
    assert provider.list_shares() == []
    assert share_state(provider) == before


def test_expired_and_orphaned_shares_are_revoked(stack):
    provider, db = stack
    before = share_state(provider)
    keep = provider.create_share(share_input(db, name="keep"), CREATOR)["share"]["id"]
    gone = provider.create_share(share_input(db, name="expired"), CREATOR)["share"]["id"]
    properties = provider.resources["principals"][gone]["properties"]
    properties["portal.expires-at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    provider.expire_shares()
    assert [s["id"] for s in provider.list_shares()] == [keep]
    assert gone not in provider.catalog_roles[db]
    # The other portal process removed the database underneath the share.
    del provider.resources["catalogs"][db]
    provider.expire_shares()
    assert share_state(provider)[0] == before[0] == []


def test_database_deletion_revokes_its_shares_and_resumes(stack):
    provider, db = stack
    other = provider.create_database(DatabaseInput(name="other", team=provider.list_teams()[0]["id"]))["id"]
    kept = provider.create_share(share_input(other), CREATOR)["share"]["id"]
    gone = provider.create_share(share_input(db), CREATOR)["share"]["id"]
    # A share role written by the gateway while the deletion was already running.
    provider.management(f"/catalogs/{db}/catalog-roles", "POST", {"catalogRole": {"name": "share-" + "e" * 32}})
    provider.storage.delete_bucket.side_effect = ServiceError(502, "storage down")
    with pytest.raises(ServiceError):
        provider.delete_database(db)
    assert gone not in provider.resources["principals"]
    with pytest.raises(ServiceError) as exc:
        provider.create_share(share_input(db, name="too-late"), CREATOR)
    assert exc.value.status == 409
    provider.storage.delete_bucket.side_effect = None
    provider.delete_database(db)
    assert [d["id"] for d in provider.list_databases()] == [other]
    assert [s["id"] for s in provider.list_shares()] == [kept]


def test_shared_database_cannot_change_owner(stack):
    provider, db = stack
    second = provider.save_team(TeamInput(name="second-team"))["id"]
    id = provider.create_share(share_input(db), CREATOR)["share"]["id"]
    with pytest.raises(ServiceError) as exc:
        provider.move_database(db, second)
    assert exc.value.status == 409 and "data shares" in str(exc.value)
    provider.delete_share(id)
    assert provider.move_database(db, second)["team"] == second


def test_platform_admin_sees_and_revokes_shares_but_cannot_create_them(portal):
    p = portal
    owner = team(p)
    db = p.post("/databases", {"name": "analytics", "team": owner}).json()["id"]
    id = p.provider.create_share(share_input(db, recipient="Partner BV"), CREATOR)["share"]["id"]
    overview = p.client.get("/api/overview").json()
    assert [(s["id"], s["recipient"], s["database"]) for s in overview["shares"]] == [(id, "Partner BV", db)]
    assert "secret" not in str(overview["shares"]).lower()
    assert p.post("/shares", share_input(db, name="other").model_dump(mode="json", by_alias=True)).status_code == 405
    assert p.patch(f"/databases/{db}", {"team": team(p, "second-team")}).status_code == 409
    assert p.delete("/shares/portal-" + "a" * 32).status_code == 422
    assert p.delete("/shares/share-" + "f" * 32).status_code == 404
    assert p.delete(f"/shares/{id}").status_code == 200
    assert p.client.get("/api/overview").json()["shares"] == []
    assert p.delete(f"/databases/{db}").status_code == 200


def test_share_routes_need_an_admin_session():
    from fastapi.testclient import TestClient

    from server.app import create_app
    from test.conftest import HEADERS, PASSWORD

    with TestClient(create_app(MemoryPolaris(), PASSWORD)) as c:
        assert c.delete(f"/api/shares/{SHARE}", headers=HEADERS).status_code == 401
