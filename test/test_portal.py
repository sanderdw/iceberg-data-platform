from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.models import ServiceError
from server.polaris import provision
from test.conftest import HEADERS, PASSWORD, MemoryPolaris, members


def team(p, name="data-team"):
    r = p.post("/teams", {"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def database(p, owner, name="analytics"):
    r = p.post("/databases", {"name": name, "team": owner})
    assert r.status_code == 201, r.text
    return r.json()


def user(p, teams, name="analyst", role="reader"):
    r = p.post("/users", {"name": name, "memberships": members(teams, role)})
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
        assert p.post("/users", {"name": "analyst", "memberships": members(teams)}).status_code == status
    u = user(p, [owner])
    assert p.patch(f"/users/{u['id']}", {"memberships": []}).status_code == 422
    assert p.delete(f"/teams/{owner}").status_code == 409
    assert p.post("/databases", {"name": "analytics", "team": "team-" + "b" * 32}).status_code == 404


def test_team_delete_preflight_and_membership_cleanup(portal):
    p = portal
    first, second = team(p), team(p, "second-team")
    u = user(p, [first, second])
    last = user(p, [first], "last-member")
    assert p.delete(f"/teams/{first}").status_code == 409
    assert p.client.get("/api/users").json()[0]["teams"] == [first, second]
    assert p.patch(f"/users/{last['id']}", {"memberships": members([second])}).status_code == 200
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
    grant = lambda u, role: f"/principal-roles/{u['id']}/catalog-roles/{db['id']}/{role}"
    assert grant(old, "admin") in p.provider.grants
    assert grant(new, "reader") not in p.provider.grants
    assert grant(both, "reader") in p.provider.grants
    moved = p.patch(f"/databases/{db['id']}", {"team": second})
    assert moved.status_code == 200, moved.text
    assert moved.json()["bucket"] == db["bucket"]
    assert grant(old, "admin") not in p.provider.grants
    assert grant(new, "reader") in p.provider.grants
    assert grant(both, "reader") in p.provider.grants
    assert p.provider.storage.set_user_buckets.call_args.args[1] == []
    assert p.patch(f"/users/{old['id']}", {"memberships": members([second], "bucket-admin")}).status_code == 200
    assert grant(old, "admin") in p.provider.grants
    assert p.provider.storage.set_user_buckets.call_args.args[1] == [db["bucket"]]


def test_move_failure_restores_original_owner_and_access(portal):
    p = portal
    first, second = team(p), team(p, "second-team")
    old, new = user(p, [first], "old-user"), user(p, [second], "new-user")
    db = database(p, first)
    original = set(p.provider.grants)
    p.provider.fail = lambda path, method, body: path == f"/catalogs/{db['id']}" and method == "PUT"
    assert p.patch(f"/databases/{db['id']}", {"team": second}).status_code == 502
    assert p.provider.grants == original
    assert p.client.get("/api/databases").json()[0]["team"] == first
    assert old["id"] != new["id"]


def test_delete_database_can_resume_and_keeps_users(portal):
    p = portal
    owner = team(p)
    u = user(p, [owner], role="bucket-admin")
    d = database(p, owner)
    p.provider.storage.delete_bucket.side_effect = ServiceError(502, "storage down")
    assert p.delete(f"/databases/{d['id']}").status_code == 502
    assert p.client.get("/api/databases").json()[0]["status"] == "deleting"
    assert p.client.get(f"/api/databases/{d['id']}/connection").status_code == 409
    assert p.patch(f"/databases/{d['id']}", {"team": owner}).status_code == 409
    p.provider.storage.delete_bucket.side_effect = None
    assert p.delete(f"/databases/{d['id']}").status_code == 200
    p.provider.storage.delete_bucket.assert_called_with(d["bucket"])
    assert p.client.get("/api/databases").json() == []
    assert p.client.get("/api/users").json()[0]["id"] == u["id"]
    assert p.delete(f"/users/{u['id']}").status_code == 200
    assert p.delete(f"/teams/{owner}").status_code == 200


def test_provisioning_conflict_never_deletes_existing_catalog(portal):
    owner = team(portal)
    db = database(portal, owner)
    portal.provider.events.clear()
    assert portal.post("/databases", {"name": "analytics", "team": owner}).status_code == 409
    assert not any(
        path == f"/catalogs/{db['id']}" and method == "DELETE" for path, method, _ in portal.provider.events
    )


def test_failed_user_grant_compensates_s3_and_identity(portal):
    owner = team(portal)
    database(portal, owner)
    portal.provider.fail = lambda path, method, body: path.endswith("/principal-roles") and method == "PUT"
    assert (
        portal.post("/users", {"name": "analyst", "memberships": members([owner], "bucket-admin")}).status_code
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
        created = pool.submit(portal.post, "/users", {"name": "analyst", "memberships": members([owner])})
        deleted = pool.submit(portal.delete, f"/teams/{owner}")
        statuses = created.result().status_code, deleted.result().status_code
    assert statuses in [(201, 409), (404, 200)]
    overview = portal.client.get("/api/overview").json()
    available = {t["id"] for t in overview["teams"]}
    assert all(u["teams"] and set(u["teams"]) <= available for u in overview["users"])


def test_database_names_are_scoped_to_team_and_environment(portal):
    first, second = team(portal, "team-a"), team(portal, "team-b")
    member = user(portal, [first], "sander")
    created = []
    for owner, env in [
        (first, "development"),
        (first, "acceptance"),
        (first, "production"),
        (second, "development"),
    ]:
        response = portal.post("/databases", {"name": "db1", "team": owner, "environment": env})
        assert response.status_code == 201
        db = response.json()
        assert (db["name"], db["team"], db["environment"]) == ("db1", owner, env)
        connection = portal.client.get(f"/api/databases/{db['id']}/connection").json()
        assert connection["warehouse"] == db["id"]
        grant = f"/principal-roles/{member['id']}/catalog-roles/{db['id']}/reader"
        assert (grant in portal.provider.grants) == (owner == first)
        created.append(db)
    assert len({d["id"] for d in created}) == 4
    assert len({d["bucket"] for d in created}) == 4
    assert portal.post("/databases", {"name": "db1", "team": first}).status_code == 409
    assert (
        portal.post("/databases", {"name": "db2", "team": first, "environment": "staging"}).status_code == 422
    )
    grants = set(portal.provider.grants)
    assert portal.patch(f"/databases/{created[0]['id']}", {"team": second}).status_code == 409
    assert portal.provider.grants == grants
    # The same display name in another environment does not prevent a move.
    assert portal.patch(f"/databases/{created[2]['id']}", {"team": second}).status_code == 200


@pytest.mark.parametrize("old_role", ["reader", "writer", "admin", "bucket-admin"])
@pytest.mark.parametrize("new_role", ["reader", "writer", "admin", "bucket-admin"])
def test_change_team_role_leaves_other_team_untouched(portal, old_role, new_role):
    first, second = team(portal), team(portal, "second-team")
    changed, kept = database(portal, first, "data_0"), database(portal, second, "data_1")
    account = user(portal, [first, second], role={first: old_role, second: "writer"})
    original = dict(portal.provider.resources["principals"][account["id"]])
    portal.provider.events.clear()
    memberships = members([first, second], {first: new_role, second: "writer"})
    response = portal.patch(f"/users/{account['id']}", {"memberships": memberships})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["user"] == {
        **account,
        "memberships": memberships,
        "bucketAccess": new_role == "bucket-admin",
    }
    assert "credentials" not in result  # The OAuth identity is preserved.
    assert ("bucketCredentials" in result) == (new_role == "bucket-admin" and old_role != new_role)
    assert (
        portal.provider.resources["principals"][account["id"]]["createTimestamp"]
        == original["createTimestamp"]
    )
    prefix = f"/principal-roles/{account['id']}/catalog-roles/"
    effective = "admin" if new_role == "bucket-admin" else new_role
    assert {g for g in portal.provider.grants if g.startswith(prefix)} == {
        f"{prefix}{changed['id']}/{effective}",
        f"{prefix}{kept['id']}/writer",
    }
    assert not any(kept["id"] in path for path, _, _ in portal.provider.events)
    if old_role == "bucket-admin" and new_role != old_role:
        portal.provider.storage.set_user_buckets.assert_called_with(
            original["properties"]["portal.bucket-access-key"], []
        )
    assert "bucketCredentials" not in str(portal.client.get("/api/users").json())


def test_role_change_revokes_before_granting(portal):
    owner = team(portal)
    db = database(portal, owner)
    account = user(portal, [owner])
    portal.provider.events.clear()
    assert portal.patch(f"/users/{account['id']}", {"memberships": members([owner], "writer")}).status_code == 200
    path = f"/principal-roles/{account['id']}/catalog-roles/{db['id']}"
    changes = [(p, m) for p, m, _ in portal.provider.events if p.startswith(path)]
    assert changes == [(path + "/reader", "DELETE"), (path, "PUT")]


def test_bucket_admin_on_one_team_scopes_s3_to_that_team(portal):
    first, second = team(portal), team(portal, "second-team")
    owned, other = database(portal, first, "owned"), database(portal, second, "other")
    account = user(portal, [first, second], role={first: "bucket-admin", second: "reader"})
    portal.provider.storage.create_user.assert_called_once()
    assert portal.provider.storage.create_user.call_args.args[1] == [owned["bucket"]]
    prefix = f"/principal-roles/{account['id']}/catalog-roles/"
    assert {g for g in portal.provider.grants if g.startswith(prefix)} == {
        f"{prefix}{owned['id']}/admin",
        f"{prefix}{other['id']}/reader",
    }
    database(portal, second, "later")
    portal.provider.storage.set_user_buckets.assert_not_called()
    assert portal.patch(f"/databases/{owned['id']}", {"team": second}).status_code == 200
    assert portal.provider.storage.set_user_buckets.call_args.args[1] == []
    assert f"{prefix}{owned['id']}/reader" in portal.provider.grants
    assert f"{prefix}{owned['id']}/admin" not in portal.provider.grants


def test_move_between_teams_with_different_roles_rebinds_and_rolls_back(portal):
    first, second = team(portal), team(portal, "second-team")
    db = database(portal, first)
    account = user(portal, [first, second], role={first: "reader", second: "writer"})
    path = f"/principal-roles/{account['id']}/catalog-roles/{db['id']}"
    grants = set(portal.provider.grants)
    portal.provider.fail = lambda p, method, body: p == f"/catalogs/{db['id']}" and method == "PUT"
    assert portal.patch(f"/databases/{db['id']}", {"team": second}).status_code == 502
    assert portal.provider.grants == grants
    portal.provider.fail = None
    portal.provider.events.clear()
    assert portal.patch(f"/databases/{db['id']}", {"team": second}).status_code == 200
    changes = [(p, m) for p, m, _ in portal.provider.events if p.startswith(path)]
    assert changes == [(path + "/reader", "DELETE"), (path, "PUT")]
    assert path + "/writer" in portal.provider.grants


def test_legacy_single_role_principal_is_read_and_migrated(portal):
    first, second = team(portal), team(portal, "second-team")
    database(portal, first)
    account = user(portal, [first, second], role="writer")
    principal = portal.provider.resources["principals"][account["id"]]
    del principal["properties"]["portal.memberships"]
    principal["properties"].update({"portal.teams": f'["{first}", "{second}"]', "portal.role": "writer"})
    assert portal.client.get("/api/users").json() == [account]
    grants = set(portal.provider.grants)
    assert portal.patch(f"/users/{account['id']}", {"memberships": account["memberships"]}).status_code == 200
    assert portal.provider.grants == grants
    properties = portal.provider.resources["principals"][account["id"]]["properties"]
    assert "portal.teams" not in properties and "portal.role" not in properties
    assert portal.client.get("/api/users").json() == [account]


def test_team_delete_failure_restores_member_roles(portal):
    first, second = team(portal), team(portal, "second-team")
    account = user(portal, [first, second], role={first: "admin", second: "reader"})
    portal.provider.fail = lambda path, method, body: path == f"/principal-roles/{first}" and method == "DELETE"
    assert portal.delete(f"/teams/{first}").status_code == 502
    assert portal.provider.list_users() == [account]
    portal.provider.fail = None
    assert portal.delete(f"/teams/{first}").status_code == 200
    assert portal.provider.list_users()[0]["memberships"] == members([second])


def test_delete_database_retry_after_role_change_leaves_no_grant(portal):
    owner = team(portal)
    account = user(portal, [owner])
    db = database(portal, owner)
    portal.provider.storage.delete_bucket.side_effect = ServiceError(502, "storage down")
    assert portal.delete(f"/databases/{db['id']}").status_code == 502
    # A deleting database is out of scope, so this role change cannot revoke its grant.
    assert portal.patch(f"/users/{account['id']}", {"memberships": members([owner], "writer")}).status_code == 200
    portal.provider.storage.delete_bucket.side_effect = None
    portal.provider.events.clear()
    assert portal.delete(f"/databases/{db['id']}").status_code == 200
    base = f"/principal-roles/{account['id']}/catalog-roles/{db['id']}/"
    removed = {p for p, m, _ in portal.provider.events if m == "DELETE" and p.startswith(base)}
    assert removed == {base + role for role in ("reader", "writer", "admin")}


def test_demoted_s3_key_stays_denied_until_repromotion(portal):
    first, second = team(portal), team(portal, "second-team")
    database(portal, first)
    target = database(portal, second, "target")
    account = user(portal, [first], role="bucket-admin")
    key = portal.provider.resources["principals"][account["id"]]["properties"]["portal.bucket-access-key"]
    path = f"/users/{account['id']}"
    assert portal.patch(path, {"memberships": members([first])}).status_code == 200
    portal.provider.storage.set_user_buckets.assert_called_with(key, [])
    portal.provider.storage.set_user_buckets.reset_mock()
    assert portal.patch(path, {"memberships": members([second])}).status_code == 200
    future = database(portal, second, "future_db")
    portal.provider.storage.set_user_buckets.assert_not_called()
    result = portal.patch(path, {"memberships": members([second], "bucket-admin")}).json()
    assert "bucketCredentials" not in result  # Reuse the existing S3 credentials.
    assert result["user"]["bucketAccess"] is True
    assert portal.provider.storage.set_user_buckets.call_args.args[1] == sorted(
        [target["bucket"], future["bucket"]]
    )
    portal.provider.storage.create_user.assert_called_once()


@pytest.mark.parametrize(
    "old_role,new_role", [("reader", "bucket-admin"), ("bucket-admin", "reader"), ("reader", "writer")]
)
@pytest.mark.parametrize("failure", ["grant", "metadata"])
def test_failed_role_change_restores_grants_metadata_and_s3(portal, old_role, new_role, failure):
    owner = team(portal)
    db = database(portal, owner)
    account = user(portal, [owner], role=old_role)
    grants = set(portal.provider.grants)
    effective = "admin" if new_role == "bucket-admin" else new_role
    portal.provider.fail = lambda path, method, body: (
        method == "PUT"
        and (
            path == f"/principals/{account['id']}"
            if failure == "metadata"
            else path.endswith(f"/catalog-roles/{db['id']}") and body["catalogRole"]["name"] == effective
        )
    )
    body = {"memberships": members([owner], new_role)}
    assert portal.patch(f"/users/{account['id']}", body).status_code == 502
    assert portal.provider.grants == grants
    assert portal.provider.list_users() == [account]
    if old_role == "bucket-admin":
        assert portal.provider.storage.set_user_buckets.call_args.args[1] == [db["bucket"]]
    elif new_role == "bucket-admin":
        portal.provider.storage.delete_user.assert_called_once()


def test_access_edit_validation_and_provider_failure(portal):
    owner = team(portal)
    database(portal, owner)
    account = user(portal, [owner])
    path = f"/users/{account['id']}"
    for body in (
        {},
        {"memberships": members([owner], "owner")},
        {"memberships": [{"team": owner}]},
        {"memberships": members([owner]), "role": "admin"},
        {"teams": [owner]},
    ):
        assert portal.patch(path, body).status_code == 422
    assert portal.patch(path + "/role", {"role": "reader"}).status_code in (404, 405)
    assert portal.patch("/users/missing", {"memberships": members([owner])}).status_code == 404
    grants = set(portal.provider.grants)
    portal.provider.storage.create_user.side_effect = ServiceError(502, "storage down")
    assert portal.patch(path, {"memberships": members([owner], "bucket-admin")}).status_code == 502
    assert portal.provider.grants == grants
    assert portal.provider.list_users() == [account]
    portal.client.delete("/api/session", headers=HEADERS)
    assert portal.patch(path, {"memberships": members([owner], "admin")}).status_code == 401
