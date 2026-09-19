import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.models import ServiceError
from server.polaris import PolarisProvider

PASSWORD = "local-test-password-at-least-16"
HEADERS = {"X-Portal-Request": "1", "Content-Type": "application/json"}


def members(teams, role="reader"):
    """Memberships payload; `role` is one role for all teams or a {team: role} mapping."""
    roles = role if isinstance(role, dict) else dict.fromkeys(teams, role)
    return [{"team": team, "role": roles[team]} for team in teams]


class MemoryPolaris(PolarisProvider):
    """In-memory implementation of Polaris's wire protocol, not the domain rules."""

    def __init__(self):
        self.env = {}
        self.public_url = "http://localhost:8181"
        self.resources = {"catalogs": {}, "principals": {}, "principal-roles": {}}
        self.grants = set()
        self.events = []
        self.fail = None
        self.storage = Mock()
        self.storage.create_bucket.side_effect = lambda name, team: f"db-{name}-123456789abc"
        self.storage.create_user.side_effect = lambda key, buckets: {
            "accessKeyId": key,
            "secretAccessKey": "s3-secret",
            "buckets": buckets,
            "endpoint": "http://localhost:9000",
        }

    def management(self, path, method="GET", body=None):
        self.events.append((path, method, copy.deepcopy(body)))
        if self.fail and self.fail(path, method, body):
            raise ServiceError(502, "injected failure")
        parts = path.strip("/").split("/")
        kind = parts[0]
        if len(parts) > 2:
            if method == "DELETE":
                self.grants.discard(path)
            elif method in ("POST", "PUT"):
                self.grants.add(
                    path
                    + ("/" + body["catalogRole"]["name"] if "catalogRole" in body and method == "PUT" else "")
                )
            return {}
        objects = self.resources[kind]
        if len(parts) == 1:
            if method == "GET":
                return {"roles" if kind == "principal-roles" else kind: copy.deepcopy(list(objects.values()))}
            resource = copy.deepcopy(
                body[
                    {"catalogs": "catalog", "principals": "principal", "principal-roles": "principalRole"}[
                        kind
                    ]
                ]
            )
            name = resource["name"]
            if name in objects:
                raise ServiceError(409, "duplicate")
            resource.update(entityVersion=1, createTimestamp=1000000000000)
            objects[name] = resource
            return (
                {
                    "principal": copy.deepcopy(resource),
                    "credentials": {"clientId": name, "clientSecret": "secret"},
                }
                if kind == "principals"
                else copy.deepcopy(resource)
            )
        name = parts[1]
        if name not in objects:
            raise ServiceError(404, "missing")
        if method == "GET":
            return copy.deepcopy(objects[name])
        if method == "PUT":
            assert body["currentEntityVersion"] == objects[name]["entityVersion"]
            objects[name]["properties"] = copy.deepcopy(body["properties"])
            objects[name]["entityVersion"] += 1
            return copy.deepcopy(objects[name])
        if method == "DELETE":
            del objects[name]
            self.grants = {g for g in self.grants if not g.startswith(path + "/")}
        return None

    def request(self, path, method="GET", body=None, retry=True):
        self.events.append((path, method, body))
        return {"namespaces": [], "identifiers": []}


@pytest.fixture
def portal():
    provider = MemoryPolaris()
    with TestClient(create_app(provider, PASSWORD)) as client:
        response = client.post("/api/session", json={"password": PASSWORD}, headers=HEADERS)
        assert response.status_code == 200
        yield SimpleNamespace(
            client=client,
            provider=provider,
            post=lambda path, data: client.post("/api" + path, json=data, headers=HEADERS),
            patch=lambda path, data: client.patch("/api" + path, json=data, headers=HEADERS),
            delete=lambda path: client.delete("/api" + path, headers=HEADERS),
        )
