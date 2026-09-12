from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.models import ServiceError
from server.polaris import provision
from test.conftest import HEADERS, PASSWORD, MemoryPolaris


def team(p, name="data-team"):
    r = p.post("/teams", {"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def database(p, owner, name="analytics"):
    r = p.post("/databases", {"name": name, "team": owner})
    assert r.status_code == 201, r.text
    return r.json()


def user(p, teams, name="analyst", role="reader"):
    r = p.post("/users", {"name": name, "teams": teams, "role": role})
    assert r.status_code == 201, r.text
    return r.json()["user"]


def test_auth_static_csrf_validation_and_logout():
    with TestClient(create_app(MemoryPolaris(), PASSWORD)) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/").status_code == 200
        assert c.get("/.env").status_code == 404
        assert c.get("/api/overview").status_code == 401
        assert c.get("/openapi.json").status_code == 401
        assert c.post("/api/session", json={"password": PASSWORD}).status_code == 403
        assert (
            c.post(
                "/api/session", json={"password": PASSWORD}, headers={**HEADERS, "Origin": "http://evil.test"}
            ).status_code
            == 403
        )
        assert c.post("/api/session", json={"password": "wrong"}, headers=HEADERS).status_code == 401
        login = c.post("/api/session", json={"password": PASSWORD}, headers=HEADERS)
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "SameSite=strict" in login.headers["set-cookie"]
        assert c.get("/api/session").json() == {"authenticated": True}
        assert c.get("/openapi.json").json()["info"]["title"] == "Iceberg Workspace API"
        assert c.post("/api/teams", content='{"name":', headers=HEADERS).status_code == 422
        assert c.post("/api/teams", content="x" * 8193, headers=HEADERS).status_code == 413
        assert c.delete("/api/session", headers=HEADERS).status_code == 200
        assert c.get("/api/teams").status_code == 401


def test_login_throttle():
    with TestClient(create_app(MemoryPolaris(), PASSWORD)) as c:
        for _ in range(10):
            assert c.post("/api/session", json={"password": "bad"}, headers=HEADERS).status_code == 401
        assert c.post("/api/session", json={"password": PASSWORD}, headers=HEADERS).status_code == 429


@pytest.mark.parametrize("name", ["../root", "UPPER", "ab", "a" * 49, "<script>", None, {}])
def test_invalid_names(portal, name):
    assert portal.post("/teams", {"name": name}).status_code == 422


def test_teams_are_independent_and_rename_preserves_relationships(portal):
    p = portal
    owner = team(p)
    assert len(p.client.get("/api/overview").json()["teams"]) == 1
    d = database(p, owner)
    u = user(p, [owner])
    assert p.patch(f"/teams/{owner}", {"name": "renamed", "description": "Central team"}).status_code == 200
    overview = p.client.get("/api/overview").json()
    assert overview["teams"][0]["name"] == "renamed"
    assert overview["databases"][0]["team"] == owner
    assert overview["users"][0]["teams"] == [owner]
    assert overview["users"][0]["id"] == u["id"]
    assert overview["databases"][0]["bucket"] == d["bucket"]
    assert p.post("/teams", {"name": "renamed"}).status_code == 409


def test_memberships_required_exist_and_unique(portal):
    p = portal
    owner = team(p)
    for teams, status in [([], 422), ([owner, owner], 422), (["team-" + "a" * 32], 404)]:
        assert p.post("/users", {"name": "analyst", "role": "reader", "teams": teams}).status_code == status
    u = user(p, [owner])
    assert p.patch(f"/users/{u['id']}", {"teams": []}).status_code == 422
    assert p.delete(f"/teams/{owner}").status_code == 409
    assert p.post("/databases", {"name": "analytics", "team": "team-" + "b" * 32}).status_code == 404


def test_team_delete_preflight_and_membership_cleanup(portal):
    p = portal
    first, second = team(p), team(p, "second-team")
    u = user(p, [first, second])
    last = user(p, [first], "last-member")
    assert p.delete(f"/teams/{first}").status_code == 409
    assert p.client.get("/api/users").json()[0]["teams"] == [first, second]
    assert p.patch(f"/users/{last['id']}", {"teams": [second]}).status_code == 200
    assert p.delete(f"/teams/{first}").status_code == 200
    assert next(x for x in p.client.get("/api/users").json() if x["id"] == u["id"])["teams"] == [second]


def test_nonempty_team_cannot_be_deleted(portal):
    owner = team(portal)
    database(portal, owner)
    assert portal.delete(f"/teams/{owner}").status_code == 409


def test_move_and_membership_changes_update_catalog_and_s3_permissions(portal):
    p = portal
    first, second = team(p), team(p, "second-team")
    old = user(p, [first], "old-user", "bucket-admin")
    new = user(p, [second], "new-user")
    both = user(p, [first, second], "both-user")
    db = database(p, first)
    grant = lambda u, role: f"/principal-roles/{u['id']}/catalog-roles/analytics/{role}"
    assert grant(old, "admin") in p.provider.grants
    assert grant(new, "reader") not in p.provider.grants
    assert grant(both, "reader") in p.provider.grants
    moved = p.patch("/databases/analytics", {"team": second})
    assert moved.status_code == 200, moved.text
    assert moved.json()["bucket"] == db["bucket"]
    assert grant(old, "admin") not in p.provider.grants
    assert grant(new, "reader") in p.provider.grants
    assert grant(both, "reader") in p.provider.grants
    assert p.provider.storage.set_user_buckets.call_args.args[1] == []
    assert p.patch(f"/users/{old['id']}", {"teams": [second]}).status_code == 200
    assert grant(old, "admin") in p.provider.grants
    assert p.provider.storage.set_user_buckets.call_args.args[1] == [db["bucket"]]


def test_move_failure_restores_original_owner_and_access(portal):
    p = portal
    first, second = team(p), team(p, "second-team")
    old, new = user(p, [first], "old-user"), user(p, [second], "new-user")
    database(p, first)
    original = set(p.provider.grants)
    p.provider.fail = lambda path, method, body: path == "/catalogs/analytics" and method == "PUT"
    assert p.patch("/databases/analytics", {"team": second}).status_code == 502
    assert p.provider.grants == original
    assert p.client.get("/api/databases").json()[0]["team"] == first
    assert old["id"] != new["id"]


def test_delete_database_can_resume_and_keeps_users(portal):
    p = portal
    owner = team(p)
    u = user(p, [owner], role="bucket-admin")
    d = database(p, owner)
    p.provider.storage.delete_bucket.side_effect = ServiceError(502, "storage down")
    assert p.delete("/databases/analytics").status_code == 502
    assert p.client.get("/api/databases").json()[0]["status"] == "deleting"
    assert p.client.get("/api/databases/analytics/connection").status_code == 409
    assert p.patch("/databases/analytics", {"team": owner}).status_code == 409
    p.provider.storage.delete_bucket.side_effect = None
    assert p.delete("/databases/analytics").status_code == 200
    p.provider.storage.delete_bucket.assert_called_with(d["bucket"])
    assert p.client.get("/api/databases").json() == []
    assert p.client.get("/api/users").json()[0]["id"] == u["id"]
    assert p.delete(f"/users/{u['id']}").status_code == 200
    assert p.delete(f"/teams/{owner}").status_code == 200


def test_provisioning_conflict_never_deletes_existing_catalog(portal):
    owner = team(portal)
    database(portal, owner)
    portal.provider.events.clear()
    assert portal.post("/databases", {"name": "analytics", "team": owner}).status_code == 409
    assert not any(
        path == "/catalogs/analytics" and method == "DELETE" for path, method, _ in portal.provider.events
    )


def test_failed_user_grant_compensates_s3_and_identity(portal):
    owner = team(portal)
    database(portal, owner)
    portal.provider.fail = lambda path, method, body: path.endswith("/principal-roles") and method == "PUT"
    assert (
        portal.post("/users", {"name": "analyst", "teams": [owner], "role": "bucket-admin"}).status_code
        == 502
    )
    assert portal.provider.list_users() == []
    portal.provider.storage.delete_user.assert_called_once()


def test_cleanup_failure_reported():
    with pytest.raises(ServiceError, match="cleanup was incomplete"), provision() as undo:
        undo.append(lambda: (_ for _ in ()).throw(RuntimeError("cleanup")))
        raise RuntimeError("original")


def test_concurrent_delete_and_create_never_orphans_a_user(portal):
    owner = team(portal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        created = pool.submit(portal.post, "/users", {"name": "analyst", "teams": [owner], "role": "reader"})
        deleted = pool.submit(portal.delete, f"/teams/{owner}")
        statuses = created.result().status_code, deleted.result().status_code
    assert statuses in [(201, 409), (404, 200)]
    overview = portal.client.get("/api/overview").json()
    available = {t["id"] for t in overview["teams"]}
    assert all(u["teams"] and set(u["teams"]) <= available for u in overview["users"])
