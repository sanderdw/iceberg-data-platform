"""Polaris, read with one-hour read tokens of a team's automation principal: models and table schemas.

Everything here is metadata. Rows are only read by the query worker, in its own process. Tokens
come from the Bridge and are cached per principal until ten minutes before they expire.
"""

import asyncio
import time
from urllib.parse import quote

import httpx

from .errors import CbiError

HEADERS = {"Polaris-Realm": "POLARIS"}
MAX_NAMESPACES = 200


def iceberg_type(value):
    return value if isinstance(value, str) else (value.get("type", "nested") if isinstance(value, dict) else "unknown")


class Catalog:
    def __init__(self, bridge, http=None, *, ttl=60):
        self.bridge = bridge
        self.http = http or httpx.AsyncClient(timeout=20, trust_env=False)
        self.ttl = ttl
        self._tokens = {}
        self._cache = {}
        self._locks = {}

    async def close(self):
        await self.http.aclose()

    async def uri(self):
        return (await self.bridge.discovery())["catalog"]["internalUri"].rstrip("/")

    async def token(self, principal):
        now = time.monotonic()
        cached = self._tokens.get(principal)
        if cached and cached[0] > now:
            return cached[1]
        async with self._locks.setdefault(principal, asyncio.Lock()):
            cached = self._tokens.get(principal)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            issued = await self.bridge.catalog_token(principal, "read", "conversational bi")
            lifetime = max(60, int(issued.get("expiresIn", 3600)) - 600)
            self._tokens[principal] = (time.monotonic() + lifetime, issued["accessToken"])
            return issued["accessToken"]

    def forget(self, principal):
        self._tokens.pop(principal, None)
        self._cache = {k: v for k, v in self._cache.items() if k[1] != principal}

    async def get(self, principal, url, **kwargs):
        for attempt in (1, 2):
            token = await self.token(principal)
            try:
                response = await self.http.get(url, headers={**HEADERS, "Authorization": f"Bearer {token}"}, **kwargs)
            except httpx.HTTPError as exc:
                raise CbiError(503, "The catalog is unavailable. Try again shortly.", "catalog_unavailable") from exc
            if response.status_code == 401 and attempt == 1:
                self._tokens.pop(principal, None)
                continue
            return response
        return response

    async def cached(self, key, load):
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        value = await load()
        if len(self._cache) > 2000:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        self._cache[key] = (now + self.ttl, value)
        return value

    async def base(self, principal, database):
        async def load():
            response = await self.get(principal, f"{await self.uri()}/v1/config", params={"warehouse": database})
            if response.is_error:
                raise CbiError(403, "This database is not readable for the team's Conversational BI account.",
                               "database_not_readable")
            config = response.json()
            prefix = {**config.get("defaults", {}), **config.get("overrides", {})}.get("prefix", "")
            return f"{await self.uri()}/v1" + (f"/{quote(prefix, safe='/')}" if prefix else "")
        return await self.cached(("base", principal, database), load)

    def models_url(self, uri, database, namespace):
        return (f"{uri}/polaris/v1/{quote(database, safe='')}/namespaces/"
                f"{quote(chr(31).join(namespace), safe='')}/semantic-models")

    async def load_model(self, principal, database, namespace, name):
        async def load():
            url = f"{self.models_url(await self.uri(), database, namespace)}/{quote(name, safe='')}"
            response = await self.get(principal, url)
            if response.status_code in (403, 404):
                raise CbiError(404 if response.status_code == 404 else 403,
                               "This semantic model does not exist or is not readable for your team.",
                               "model_not_readable")
            if response.status_code == 406:
                raise CbiError(503, "Semantic models are switched off on this platform.", "models_disabled")
            if response.is_error:
                raise CbiError(502, "The catalog could not return this semantic model.", "platform_error")
            return response.json()
        return await self.cached(("model", principal, database, tuple(namespace), name), load)

    async def model_names(self, principal, database, namespace):
        async def load():
            url, names, token = self.models_url(await self.uri(), database, namespace), [], None
            for _ in range(20):
                response = await self.get(principal, url, params={"pageToken": token} if token else None)
                if response.is_error:
                    return []
                page = response.json()
                names += [i["name"] for i in page.get("identifiers", []) if isinstance(i, dict) and i.get("name")]
                token = page.get("next-page-token")
                if not token:
                    break
            return sorted(set(names))
        return await self.cached(("names", principal, database, tuple(namespace)), load)

    async def namespaces(self, principal, database):
        """Every namespace of a database the principal can list, breadth first and bounded."""
        async def load():
            base, found, queue = await self.base(principal, database), [], [None]
            while queue and len(found) < MAX_NAMESPACES:
                parent = queue.pop(0)
                params = {"parent": chr(31).join(parent)} if parent else None
                response = await self.get(principal, f"{base}/namespaces", params=params)
                if response.is_error:
                    continue
                for namespace in response.json().get("namespaces", []):
                    found.append(list(namespace))
                    if len(namespace) < 4:
                        queue.append(list(namespace))
            return found
        return await self.cached(("namespaces", principal, database), load)

    async def table_schema(self, principal, database, namespace, table):
        """The current schema of a table: {column: Iceberg type}."""
        async def load():
            base = await self.base(principal, database)
            url = f"{base}/namespaces/{quote(chr(31).join(namespace), safe='')}/tables/{quote(table, safe='')}"
            response = await self.get(principal, url)
            if response.status_code in (403, 404):
                return None
            if response.is_error:
                raise CbiError(502, "The catalog could not describe a table of this model.", "platform_error")
            metadata = response.json()["metadata"]
            schema = next((s for s in metadata.get("schemas", [])
                           if s.get("schema-id") == metadata.get("current-schema-id")), {"fields": []})
            return {f["name"]: iceberg_type(f.get("type")) for f in schema.get("fields", [])}
        return await self.cached(("schema", principal, database, tuple(namespace), table), load)
