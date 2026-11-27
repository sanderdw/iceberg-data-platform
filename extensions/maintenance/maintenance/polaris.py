"""Apache Polaris, with tokens of a team's automation principal: the database's tables and its policies.

Polaris stores and resolves maintenance policies (its Policy API) and never runs them; Maintenance does. The
team's `write` token manages policies (the catalog role `writer` holds CATALOG_MANAGE_CONTENT); a `read` token
lists tables and resolves the policies that apply to them. Tokens come from the Bridge and are cached until ten
minutes before they expire.
"""

import asyncio
import json
import time
from urllib.parse import quote

import httpx

from . import policy as policies
from .errors import MaintenanceError

HEADERS = {"Polaris-Realm": "POLARIS"}
MAX_NAMESPACES = 500
MAX_TABLES = 2000
# Applicable-policy lookups in flight at once while a run resolves its tables.
CONCURRENCY = 8
# How long the policies attached to a catalog are cached for lists.
CATALOG_TTL = 30


def unit(parts):
    """A namespace as one path or query value: its parts joined by the unit separator."""
    return chr(31).join(parts)


class Polaris:
    def __init__(self, bridge, http=None):
        self.bridge = bridge
        self.http = http or httpx.AsyncClient(timeout=30, trust_env=False)
        self._tokens = {}
        self._locks = {}
        self._catalog = {}

    async def close(self):
        await self.http.aclose()

    async def uri(self):
        return (await self.bridge.discovery())["catalog"]["internalUri"].rstrip("/")

    async def token(self, principal, access):
        key = (principal, access)
        cached = self._tokens.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._tokens.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            issued = await self.bridge.catalog_token(principal, access, f"maintenance policies ({access})")
            lifetime = max(60, int(issued.get("expiresIn") or 3600) - 600)
            self._tokens[key] = (time.monotonic() + lifetime, issued["accessToken"])
            return issued["accessToken"]

    def forget(self, principal, access):
        self._tokens.pop((principal, access), None)

    def catalog(self, database, *, principal=None, access="read", token=None):
        return Catalog(self, database, principal, access, token)

    def cached_policies(self, database):
        hit = self._catalog.get(database)
        return hit[1] if hit and hit[0] > time.monotonic() else None

    def remember_policies(self, database, found):
        self._catalog[database] = (time.monotonic() + CATALOG_TTL, found)

    def changed(self, database):
        self._catalog.pop(database, None)


class Catalog:
    """One database (Polaris catalog), as one principal with one access level, or with a given token."""

    def __init__(self, polaris, database, principal, access, token):
        self.polaris = polaris
        self.database = database
        self.principal = principal
        self.access = access
        self.fixed = token

    async def request(self, method, path, *, api="v1", **kwargs):
        uri = await self.polaris.uri()
        prefix = "v1" if api == "v1" else "polaris/v1"
        url = f"{uri}/{prefix}/{quote(self.database, safe='')}{path}"
        for attempt in (1, 2):
            token = self.fixed or await self.polaris.token(self.principal, self.access)
            try:
                response = await self.polaris.http.request(
                    method, url, headers={**HEADERS, "Authorization": f"Bearer {token}"}, **kwargs)
            except httpx.HTTPError as exc:
                raise MaintenanceError(503, "The catalog is unavailable. Try again shortly.",
                                       "catalog_unavailable") from exc
            if response.status_code == 401 and attempt == 1 and not self.fixed:
                self.polaris.forget(self.principal, self.access)
                continue
            return response
        return response

    @staticmethod
    def refused(response, what):
        try:
            message = response.json().get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            message = ""
        if response.status_code in (401, 403):
            return MaintenanceError(502, f"The catalog refused to {what} for the team's Maintenance account.",
                                    "platform_error")
        detail = f"{response.status_code}: {message}" if message else str(response.status_code)
        return MaintenanceError(502, f"The catalog could not {what} ({detail}).", "platform_error")

    async def pages(self, path, key, *, api="v1", params=None, what="list", missing_ok=False):
        found, token = [], None
        for _ in range(200):
            query = {**(params or {}), **({"pageToken": token} if token else {})}
            response = await self.request("GET", path, api=api, params=query or None)
            if response.status_code == 404 and missing_ok:
                return None
            if response.is_error:
                raise self.refused(response, what)
            page = response.json()
            found += page.get(key) or []
            token = page.get("next-page-token")
            if not token:
                break
        return found

    # Tables

    async def namespaces(self):
        """Every namespace, nested ones included, breadth first and bounded; never Maintenance's own."""
        found, queue = [], [None]
        while queue and len(found) < MAX_NAMESPACES:
            parent = queue.pop(0)
            params = {"parent": unit(parent)} if parent else None
            children = await self.pages("/namespaces", "namespaces", params=params, what="list the namespaces",
                                        missing_ok=bool(parent)) or []
            for namespace in children:
                if list(namespace)[:1] == [policies.HOME]:
                    continue
                found.append(list(namespace))
                queue.append(list(namespace))
        return found

    async def tables(self, namespace):
        identifiers = await self.pages(f"/namespaces/{quote(unit(namespace), safe='')}/tables", "identifiers",
                                       what="list the tables", missing_ok=True) or []
        return [i["name"] for i in identifiers if isinstance(i, dict) and isinstance(i.get("name"), str)]

    # Policies

    async def applicable(self, namespace=None, table=None):
        """The policies that apply to the catalog, a namespace or a table, inherited ones included; None when
        the namespace or table does not exist."""
        params = {}
        if namespace:
            params["namespace"] = unit(namespace)
        if table:
            params["target-name"] = table
        return await self.pages("/applicable-policies", "applicable-policies", api="polaris", params=params,
                                what="resolve the policies", missing_ok=True)

    async def catalog_policies(self, *, fresh=False):
        """The policies attached to the catalog itself, per type key: the database's policy."""
        if not fresh:
            hit = self.polaris.cached_policies(self.database)
            if hit is not None:
                return hit
        found = {}
        for entry in await self.applicable() or []:
            key = policies.KEYS.get(entry.get("policy-type"))
            if key and not entry.get("inherited") and key not in found:
                found[key] = entry
        self.polaris.remember_policies(self.database, found)
        return found

    def policy_path(self, namespace, name=""):
        return f"/namespaces/{quote(unit(namespace), safe='')}/policies" + (f"/{quote(name, safe='')}" if name else "")

    async def create_home(self):
        response = await self.request("POST", "/namespaces", json={
            "namespace": [policies.HOME],
            "properties": {"comment": "Apache Polaris policies of the Maintenance extension. It holds no tables."}})
        if response.status_code not in (200, 409):
            raise self.refused(response, "create the namespace for maintenance policies")

    async def drop_home(self):
        """Removes Maintenance's namespace when nothing is left in it; Polaris refuses while it holds policies."""
        await self.request("DELETE", f"/namespaces/{quote(policies.HOME, safe='')}")

    async def create_policy(self, name, ptype, content, description):
        """The new policy in Maintenance's namespace, or None when one of that name exists."""
        response = await self.request("POST", self.policy_path([policies.HOME]), api="polaris", json={
            "name": name, "type": ptype, "description": description, "content": json.dumps(content)})
        if response.status_code == 404:
            await self.create_home()
            response = await self.request("POST", self.policy_path([policies.HOME]), api="polaris", json={
                "name": name, "type": ptype, "description": description, "content": json.dumps(content)})
        if response.status_code == 409:
            return None
        if response.is_error:
            raise self.refused(response, f"create the policy {name}")
        return response.json()["policy"]

    async def load_policy(self, namespace, name):
        response = await self.request("GET", self.policy_path(namespace, name), api="polaris")
        if response.status_code == 404:
            return None
        if response.is_error:
            raise self.refused(response, f"read the policy {name}")
        return response.json()["policy"]

    async def update_policy(self, namespace, name, content, version):
        """Polaris changes a policy only at the version it was read at: otherwise 409, someone changed it."""
        response = await self.request("PUT", self.policy_path(namespace, name), api="polaris", json={
            "content": json.dumps(content), "current-policy-version": version})
        if response.status_code == 409:
            raise MaintenanceError(409, f"The policy {name} changed since you read it. Read it again.",
                                   "revision_conflict")
        if response.is_error:
            raise self.refused(response, f"update the policy {name}")
        return response.json()["policy"]

    async def drop_policy(self, namespace, name):
        response = await self.request("DELETE", self.policy_path(namespace, name), api="polaris",
                                      params={"detach-all": "true"})
        if response.status_code not in (204, 404):
            raise self.refused(response, f"remove the policy {name}")

    async def attach(self, namespace, name, target):
        """Attach a policy. 409: another policy of its type is attached there already; 404: no such target."""
        response = await self.request("PUT", self.policy_path(namespace, name) + "/mappings", api="polaris",
                                      json={"target": target})
        if response.status_code in (404, 409):
            return response.status_code
        if response.is_error:
            raise self.refused(response, f"attach the policy {name}")
        return 204

    async def detach(self, namespace, name, target):
        response = await self.request("POST", self.policy_path(namespace, name) + "/mappings", api="polaris",
                                      json={"target": target})
        if response.status_code not in (204, 404):
            raise self.refused(response, f"detach the policy {name}")


async def resolve(catalog, wanted=()):
    """The database's tables with the policies that apply to each, sorted by name: what a run maintains.

    `wanted` limits it to those tables (namespace.table). Polaris resolves each table's policies itself, so policies
    another tool attached, to the catalog, a namespace or the table, count as well."""
    catalog_level = await catalog.catalog_policies(fresh=True)
    wanted = {name.lower() for name in wanted}
    identifiers = []
    for namespace in await catalog.namespaces():
        for name in await catalog.tables(namespace):
            full = ".".join([*namespace, name])
            if not wanted or full.lower() in wanted:
                identifiers.append([*namespace, name])
        if len(identifiers) >= MAX_TABLES:
            break
    identifiers = identifiers[:MAX_TABLES]
    slots = asyncio.Semaphore(CONCURRENCY)

    async def one(identifier):
        async with slots:
            applicable = await catalog.applicable(identifier[:-1], identifier[-1])
        if applicable is None:
            return None
        return {"name": ".".join(identifier), "identifier": identifier,
                "policies": policies.effective(applicable, catalog_level)}

    found = [t for t in await asyncio.gather(*(one(i) for i in identifiers)) if t]
    return sorted(found, key=lambda t: t["name"])
