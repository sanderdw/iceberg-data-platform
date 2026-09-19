from server.polaris import MANAGED
from test.conftest import members
from test.test_portal import team, user

SHARE = "share-" + "a" * 32


def share_principal(provider, database="db-" + "b" * 32):
    properties = {
        "portal.managed-by": MANAGED,
        "portal.kind": "share",
        "portal.name": "partner",
        "portal.database": database,
    }
    provider.management("/principals", "POST", {"principal": {"name": SHARE, "properties": properties}})


def test_share_principal_is_never_a_user(portal):
    p = portal
    owner = team(p)
    u = user(p, [owner])
    share_principal(p.provider)
    assert [x["id"] for x in p.client.get("/api/users").json()] == [u["id"]]
    assert [x["id"] for x in p.client.get("/api/overview").json()["users"]] == [u["id"]]
    assert p.patch(f"/users/{SHARE}", {"memberships": members([owner])}).status_code == 404
    assert p.delete(f"/users/{SHARE}").status_code == 404
    assert SHARE in p.provider.resources["principals"]
    # Team deletion and database provisioning iterate users; a share must not break them.
    second = team(p, "second-team")
    assert p.post("/databases", {"name": "analytics", "team": second}).status_code == 201
    assert p.delete(f"/teams/{owner}").status_code == 409
