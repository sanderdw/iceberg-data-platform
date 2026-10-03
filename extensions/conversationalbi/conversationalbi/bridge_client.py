"""Client for the core's Extension Bridge v1: this extension's only door into the platform.

User calls forward the caller's own Keycloak token; service calls use the service account of
this extension's confidential client. Responses are handled by their stable `code`.
"""

import asyncio
import hashlib
import logging
import time

import httpx

from .errors import CbiError

LOG = logging.getLogger(__name__)


def version_tuple(value):
    return tuple(int(part) for part in value.split(".")[:3])


def in_range(version, spec):
    """`version` against a comma-separated range such as `>=0.1,<0.2`."""
    current = version_tuple(version)
    for clause in (c.strip() for c in spec.split(",") if c.strip()):
        for op in (">=", "<=", "==", ">", "<"):
            if clause.startswith(op):
                bound = version_tuple(clause[len(op):]) + (0,) * 3
                bound = bound[:3]
                ok = {">=": current >= bound, "<=": current <= bound, "==": current == bound,
                      ">": current > bound, "<": current < bound}[op]
                if not ok:
                    return False
                break
    return True


class BridgeClient:
    def __init__(self, settings, http=None):
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=30, trust_env=False)
        self.base = settings.bridge_url + "/bridge/v1"
        self._discovery = None
        self._service_token = None
        self._service_until = 0.0
        self._me = {}
        self._automation = None
        self._scopes = {}
        self._lock = asyncio.Lock()

    async def close(self):
        await self.http.aclose()

    async def request(self, method, path, token, **kwargs):
        try:
            response = await self.http.request(method, self.base + path,
                                               headers={"Authorization": f"Bearer {token}"}, **kwargs)
        except httpx.HTTPError as exc:
            raise CbiError(503, "The platform bridge is unavailable. Try again shortly.", "bridge_unavailable") from exc
        if response.is_error:
            try:
                body = response.json()
            except ValueError:
                body = {}
            status = response.status_code if response.status_code in (401, 403, 404, 409, 422) else 502
            raise CbiError(status, body.get("error", "The platform refused the request."),
                           body.get("code") or None)
        return response.json()

    async def discovery(self, *, refresh=False):
        if self._discovery is None or refresh:
            try:
                response = await self.http.get(self.base)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise CbiError(503, "The platform bridge is unavailable.", "bridge_unavailable") from exc
            discovery = response.json()
            version = discovery.get("contractVersion", "0")
            if not in_range(version, self.settings.contract):
                raise CbiError(503, f"The platform offers Bridge contract {version}; Conversational BI supports "
                                    f"{self.settings.contract}. Upgrade Conversational BI or the platform.",
                               "incompatible_contract")
            self._discovery = discovery
        return self._discovery

    async def service_token(self):
        async with self._lock:
            if self._service_token and self._service_until > time.monotonic():
                return self._service_token
            try:
                response = await self.http.post(
                    self.settings.internal_issuer + "/protocol/openid-connect/token",
                    data={"grant_type": "client_credentials", "client_id": self.settings.client_id,
                          "client_secret": self.settings.client_secret},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise CbiError(503, "This extension could not sign in to the platform.", "bridge_unavailable") from exc
            issued = response.json()
            self._service_token = issued["access_token"]
            self._service_until = time.monotonic() + max(0, issued.get("expires_in", 60) - 30)
            return self._service_token

    # User context

    async def capabilities(self):
        return set((await self.discovery()).get("capabilities", []))

    async def me(self, token, *, fresh=False):
        """The caller's teams, roles and databases; cached for 30 seconds per token unless `fresh`."""
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        cached = self._me.get(key)
        if cached and cached[0] > now and not fresh:
            return cached[1]
        result = await self.request("GET", "/me", token)
        self._me = {k: v for k, v in self._me.items() if v[0] > now}
        self._me[key] = (now + 30, result)
        return result

    async def team_automation(self, token, team):
        return (await self.request("GET", f"/teams/{team}/automation-principals", token))["automationPrincipals"]

    async def enable(self, token, team, environment):
        self._automation = None
        return await self.request("POST", f"/teams/{team}/automation-principals", token,
                                  json={"environment": environment})

    async def revoke(self, token, id):
        self._automation = None
        return await self.request("DELETE", f"/automation-principals/{id}", token)

    # Service calls

    async def automation(self, *, fresh=False):
        """This extension's automation principals; cached for 15 seconds."""
        now = time.monotonic()
        if fresh or self._automation is None or self._automation[0] <= now:
            token = await self.service_token()
            items = (await self.request("GET", "/automation-principals", token))["automationPrincipals"]
            self._automation = (now + 15, items)
        return self._automation[1]

    async def principal_for(self, team, environment):
        for item in await self.automation():
            if item["team"] == team and item["environment"] == environment and item["status"] == "active":
                return item
        # A team administrator may have enabled it seconds ago.
        for item in await self.automation(fresh=True):
            if item["team"] == team and item["environment"] == environment and item["status"] == "active":
                return item
        raise CbiError(409, f"Conversational BI is not enabled for {environment} in this team. A team "
                            "administrator enables it with enable_environment.", "environment_not_enabled")

    async def scope(self, id):
        """What one automation principal can read, including received shares; cached for 15 seconds."""
        now = time.monotonic()
        cached = self._scopes.get(id)
        if cached is None or cached[0] <= now:
            cached = (now + 15, await self.request("GET", f"/automation-principals/{id}", await self.service_token()))
            self._scopes = {k: v for k, v in self._scopes.items() if v[0] > now}
            self._scopes[id] = cached
        return cached[1]

    async def catalog_token(self, id, access, purpose):
        return await self.request("POST", f"/automation-principals/{id}/tokens", await self.service_token(),
                                  json={"access": access, "purpose": purpose[:120]})
