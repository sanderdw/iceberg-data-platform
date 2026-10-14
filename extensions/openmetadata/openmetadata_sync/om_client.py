"""A small OpenMetadata 2.0 REST client: create-or-update with PUT, soft delete, paged lists.

PUT of a create request is idempotent and restores a soft-deleted entity of the same name, so a
re-sync never duplicates anything and a listing that returns brings its entities back. A bot's PUT
never replaces a description that is already set, so changed descriptions follow as a JSON Patch.
The bot token is sent as a bearer token only; it is never logged or part of an error.
"""

import html
import json as jsonlib
from urllib.parse import quote

import httpx

from .errors import SyncError


def enc(value):
    return quote(value, safe="")


class OpenMetadata:
    def __init__(self, url, token, http=None, verify=True):
        self.url = url.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=60, trust_env=False, verify=verify)
        self.headers = {"Authorization": f"Bearer {token}"}

    async def close(self):
        await self.http.aclose()

    async def request(self, method, path, json=None, params=None, patch=None):
        try:
            if patch is not None:
                response = await self.http.request(
                    method, self.url + "/v1" + path, content=jsonlib.dumps(patch),
                    headers={**self.headers, "Content-Type": "application/json-patch+json"})
            else:
                response = await self.http.request(method, self.url + "/v1" + path, json=json, params=params,
                                                   headers=self.headers)
        except httpx.HTTPError as exc:
            raise SyncError(503, "OpenMetadata is unavailable.", "catalog_unavailable") from exc
        if response.is_error:
            try:
                detail = str(response.json().get("message", ""))[:300]
            except ValueError:
                detail = ""
            code = {401: "catalog_unauthorized", 403: "catalog_forbidden", 404: "not_found"}.get(
                response.status_code, "catalog_error")
            raise SyncError(response.status_code if response.status_code < 500 else 502,
                            f"OpenMetadata refused {method} {path.split('?')[0]} "
                            f"(HTTP {response.status_code}){': ' + detail if detail else ''}", code)
        return response.json() if response.content else None

    async def version(self):
        return (await self.request("GET", "/system/version") or {}).get("version", "")

    async def put(self, collection, body):
        entity = await self.request("PUT", f"/{collection}", body)
        if ops := description_patch(body, entity):
            entity = await self.request("PATCH", f"/{collection}/{entity['id']}", patch=ops)
        return entity

    async def patch(self, collection, id, ops):
        return await self.request("PATCH", f"/{collection}/{id}", patch=ops)

    async def get(self, collection, fqn, fields=None):
        try:
            return await self.request("GET", f"/{collection}/name/{enc(fqn)}",
                                      params={"fields": fields} if fields else None)
        except SyncError as exc:
            if exc.status == 404:
                return None
            raise

    async def delete(self, collection, fqn, *, hard=False):
        """Soft delete by default, with the entity's children. Already gone is fine."""
        try:
            await self.request("DELETE", f"/{collection}/name/{enc(fqn)}",
                               params={"hardDelete": str(hard).lower(), "recursive": "true"})
            return True
        except SyncError as exc:
            if exc.status == 404:
                return False
            raise

    async def list(self, collection, params=None):
        """Every non-deleted entity of a collection that matches `params`, across pages."""
        items, after = [], None
        while True:
            page = await self.request("GET", f"/{collection}",
                                      params={**(params or {}), "limit": 100, **({"after": after} if after else {})})
            items.extend(page.get("data", []))
            after = (page.get("paging") or {}).get("after")
            if not after:
                return items

    async def lineage(self, from_ref, to_ref):
        await self.request("PUT", "/lineage", {"edge": {"fromEntity": from_ref, "toEntity": to_ref}})

    async def data_product_assets(self, name):
        assets, offset = [], 0
        while True:
            page = await self.request("GET", f"/dataProducts/name/{enc(name)}/assets",
                                      params={"limit": 1000, "offset": offset})
            data = page.get("data", [])
            assets.extend(data)
            offset += len(data)
            if not data or offset >= (page.get("paging") or {}).get("total", offset):
                return assets

    async def change_assets(self, name, action, refs):
        if refs:
            await self.request("PUT", f"/dataProducts/{enc(name)}/assets/{action}",
                               {"assets": [{"id": r["id"], "type": r["type"]} for r in refs]})


def stored(value):
    """OpenMetadata stores descriptions with some characters as HTML entities, such as `+` as `&#43;`."""
    return html.unescape(value or "")


def description_patch(wanted, entity, path=""):
    """JSON Patch operations for the descriptions that the platform sets and OpenMetadata kept different.

    The platform's description wins where it has one; where it has none, OpenMetadata keeps what
    people wrote there.
    """
    ops = []
    if wanted.get("description") and stored(entity.get("description")) != wanted["description"]:
        ops.append({"op": "add", "path": f"{path}/description", "value": wanted["description"]})
    for field in ("columns", "children"):
        current = {c["name"]: (i, c) for i, c in enumerate(entity.get(field) or [])}
        for column in wanted.get(field) or []:
            if column["name"] in current:
                i, found = current[column["name"]]
                ops += description_patch(column, found, f"{path}/{field}/{i}")
    return ops
