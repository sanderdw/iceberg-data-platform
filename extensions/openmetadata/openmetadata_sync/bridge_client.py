"""Client for the core's Extension Bridge v1: this extension's only door into the platform.

It uses only the service account of this extension's confidential client, and only the
`listings` capability: listed data shares with the metadata of their objects, never data.
Responses are handled by their stable `code`.
"""

import asyncio
import time

import httpx

from .errors import SyncError

CAPABILITY = "listings"


def version_tuple(value):
    return tuple(int(part) for part in value.split(".")[:3])


def in_range(version, spec):
    """`version` against a comma-separated range such as `>=0.1.1,<0.2`."""
    current = (*version_tuple(version), 0, 0, 0)[:3]
    for clause in (c.strip() for c in spec.split(",") if c.strip()):
        for op in (">=", "<=", "==", ">", "<"):
            if clause.startswith(op):
                bound = (*version_tuple(clause[len(op):]), 0, 0, 0)[:3]
                ok = {">=": current >= bound, "<=": current <= bound, "==": current == bound,
                      ">": current > bound, "<": current < bound}[op]
                if not ok:
                    return False
                break
    return True


class BridgeClient:
    def __init__(self, settings, http=None):
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=60, trust_env=False)
        self.base = settings.bridge_url + "/bridge/v1"
        self._discovery = None
        self._token = None
        self._token_until = 0.0
        self._lock = asyncio.Lock()

    async def close(self):
        await self.http.aclose()

    async def discovery(self, *, refresh=False):
        if self._discovery is None or refresh:
            try:
                response = await self.http.get(self.base)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise SyncError(503, "The platform bridge is unavailable.", "bridge_unavailable") from exc
            discovery = response.json()
            version = discovery.get("contractVersion", "0")
            if not in_range(version, self.settings.contract):
                raise SyncError(503, f"The platform offers Bridge contract {version}; this extension supports "
                                f"{self.settings.contract}. Upgrade the platform or this extension.",
                                "incompatible_contract")
            if CAPABILITY not in discovery.get("capabilities", []):
                raise SyncError(503, "The platform does not offer data catalog listings.", "incompatible_contract")
            self._discovery = discovery
        return self._discovery

    async def service_token(self):
        async with self._lock:
            if self._token and self._token_until > time.monotonic():
                return self._token
            try:
                response = await self.http.post(
                    self.settings.internal_issuer + "/protocol/openid-connect/token",
                    data={"grant_type": "client_credentials", "client_id": self.settings.client_id,
                          "client_secret": self.settings.client_secret},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise SyncError(503, "This extension could not sign in to the platform.",
                                "bridge_unavailable") from exc
            issued = response.json()
            self._token = issued["access_token"]
            self._token_until = time.monotonic() + max(0, issued.get("expires_in", 60) - 30)
            return self._token

    async def get(self, path, headers=None):
        await self.discovery()
        token = await self.service_token()
        try:
            response = await self.http.get(self.base + path,
                                           headers={"Authorization": f"Bearer {token}", **(headers or {})})
        except httpx.HTTPError as exc:
            raise SyncError(503, "The platform bridge is unavailable. Try again shortly.",
                            "bridge_unavailable") from exc
        if response.status_code == 401:
            # The service token expired early, for example after a Keycloak restart.
            self._token = None
        if response.is_error:
            try:
                body = response.json()
            except ValueError:
                body = {}
            status = response.status_code if response.status_code in (401, 403, 404, 409, 422) else 502
            raise SyncError(status, body.get("error", "The platform refused the request."), body.get("code") or None)
        return response

    async def listings(self):
        """[{id, version, databaseId, environment}] of every listed data share."""
        return (await self.get("/listings")).json()["listings"]

    async def listing(self, id, etag=None):
        """(listing, etag), or (None, etag) when it did not change since `etag`."""
        response = await self.get(f"/listings/{id}", {"If-None-Match": etag} if etag else None)
        if response.status_code == 304:
            return None, etag
        return response.json(), response.headers.get("etag")
