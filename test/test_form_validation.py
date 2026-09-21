"""Form errors identify the rule without exposing submitted values or credentials."""

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.identity import CreateIdentity, UserManagement
from test.conftest import HEADERS, PASSWORD, MemoryPolaris
from test.test_identity_management import MemoryKeycloak


@pytest.mark.parametrize("path,body,message", [
    ("/teams", {"name": "Data Team"}, "Team name must use 3–48 lowercase"),
    ("/teams", {"name": "valid", "description": "x" * 281}, "Description must be at most 280 characters."),
    ("/databases", {"name": "Database", "team": "team-" + "a" * 32}, "Database name must use 3–48 lowercase"),
    ("/databases", {"name": "valid", "team": ""}, "Select an existing team."),
    ("/databases", {"name": "valid", "team": "team-" + "a" * 32, "environment": "test"},
     "Select Development, Acceptance or Production."),
    ("/users", {"name": "valid", "memberships": []}, "Select between 1 and 100 teams"),
    ("/users", {"name": "valid", "memberships": [{"team": "team-" + "a" * 32, "role": "owner"}]},
     "Select an available access role for each team."),
    ("/teams", {"name": "valid", "do-not-echo-this-field": "private-value"}, "unsupported field"),
    ("/session", {"password": "private-value" * 100}, "Password must be at most 1024 characters."),
    ("/session", {"password": ""}, "Password cannot be empty."),
])
def test_admin_form_errors(portal, path, body, message):
    response = portal.post(path, body)
    assert response.status_code == 422
    assert message in response.json()["error"]
    assert "private-value" not in response.text
    assert "do-not-echo-this-field" not in response.text


@pytest.mark.parametrize("field,value,message", [
    ("email", "alice@localhost", "Enter an email address such as name@example.com."),
    ("email", "x" * 255, "Email must be at most 254 characters."),
    ("first_name", "   ", "First name cannot be empty."),
    ("last_name", "\t", "Last name cannot be empty."),
    ("last_name", "x" * 101, "Last name must be at most 100 characters."),
])
def test_identity_form_rejected_before_provisioning(field, value, message):
    provider, kc = MemoryPolaris(), MemoryKeycloak()
    with TestClient(create_app(provider, PASSWORD, user_management=UserManagement(kc))) as client:
        client.post("/api/session", json={"password": PASSWORD}, headers=HEADERS)
        body = {"name": "alice", "memberships": [{"team": "team-" + "a" * 32, "role": "reader"}],
                "first_name": "Alice", "last_name": "Analyst", "email": "alice@example.test", field: value}
        response = client.post("/api/identity/users", json=body, headers=HEADERS)
        assert (response.status_code, response.json()["error"]) == (422, message)
        assert not kc.events
        assert not provider.events


def test_personal_names_preserve_case_accents_and_internal_spaces():
    data = CreateIdentity(name="alice", memberships=[{"team": "team-" + "a" * 32, "role": "reader"}],
                          first_name="  Élodie  ", last_name="  Van der Meer  ", email="  alice@example.test  ")
    assert (data.first_name, data.last_name, data.email) == ("Élodie", "Van der Meer", "alice@example.test")
