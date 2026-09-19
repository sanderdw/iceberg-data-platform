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
        # Catalog roles with their securable grants; `missing` are dropped tables and views.
        self.catalog_roles = {}
        self.missing = set()
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
            handled = self.securables(parts, method, body)
            if handled is not None:
                return handled
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
            # Polaris refuses to drop a catalog that still has catalog roles of its own.
            if kind == "catalogs" and set(self.catalog_roles.get(name, {})) - {"catalog_admin"}:
                raise ServiceError(502, "catalog not empty")
            del objects[name]
            self.grants = {g for g in self.grants if not g.startswith(path + "/")}
        return None

    def drop(self, namespace, name):
        """A dropped table or view loses its grants; granting it again is a 404."""
        self.missing.add((tuple(namespace), name))
        for grants in (g for roles in self.catalog_roles.values() for g in roles.values()):
            grants[:] = [
                g for g in grants if (g.get("tableName") or g.get("viewName"), g.get("namespace")) != (name, namespace)
            ]

    def securables(self, parts, method, body):
        if parts[0] == "principals" and parts[2] == "reset" and method == "POST":
            if parts[1] not in self.resources["principals"]:
                raise ServiceError(404, "missing")
            return {
                "principal": copy.deepcopy(self.resources["principals"][parts[1]]),
                "credentials": {"clientId": parts[1], "clientSecret": "rotated-secret"},
            }
        if parts[0] != "catalogs" or parts[2] != "catalog-roles":
            return None
        roles = self.catalog_roles.setdefault(parts[1], {"catalog_admin": []})
        if len(parts) == 3 and method == "GET":
            return {"roles": [{"name": name} for name in roles]}
        if len(parts) == 3 and method == "POST":
            roles[body["catalogRole"]["name"]] = []
        elif len(parts) == 4 and method == "DELETE":
            roles.pop(parts[3], None)
        elif len(parts) == 5 and parts[4].split("?")[0] == "grants":
            if parts[3] not in roles:
                raise ServiceError(404, "missing")
            if method == "GET":
                return {"grants": copy.deepcopy(roles[parts[3]])}
            grant = copy.deepcopy(body["grant"])
            name = grant.get("tableName") or grant.get("viewName")
            if name and (tuple(grant["namespace"]), name) in self.missing:
                raise ServiceError(404, "missing")
            if method == "PUT" and grant not in roles[parts[3]]:
                roles[parts[3]].append(grant)
            elif method == "POST" and grant in roles[parts[3]]:
                roles[parts[3]].remove(grant)
            return {}
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
