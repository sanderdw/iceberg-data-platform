"""Who is calling: Keycloak tokens for the bridge audience, from an agent (bearer) or a browser session.

Agents and scripts send a bearer token issued to `ext-<id>-mcp` (or `ext-<id>`). Browsers sign in
through `ext-<id>` with the authorization code flow and PKCE; their tokens stay on the server.
Either way the token is only good for this stack and the Bridge: Polaris refuses it.
"""

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

import httpx
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry

from .errors import DbtError

AUDIENCE = "iceberg-bridge"
COOKIE = "iceberg_dbt_session"
LOG = logging.getLogger(__name__)


@dataclass
class Caller:
    token: str = field(repr=False)
    subject: str
    name: str
    client: str
    expires_at: float

    @property
    def agent(self):
        """Calls through the MCP client come from an AI agent acting for the user."""
        return self.client.endswith("-mcp")


@dataclass
class Session:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_until: float
    expires: float


class TokenVerifier:
    def __init__(self, settings, http=None):
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=10, trust_env=False)
        self.keys = None

    async def fetch_keys(self):
        response = await self.http.get(self.settings.internal_issuer + "/protocol/openid-connect/certs")
        response.raise_for_status()
        self.keys = KeySet.import_key_set(response.json())

    async def verify(self, token):
        try:
            if self.keys is None:
                await self.fetch_keys()
            registry = JWSRegistry(algorithms=["RS256"])
            try:
                decoded = jwt.decode(token, self.keys, registry=registry)
            except InvalidKeyIdError:
                await self.fetch_keys()
                decoded = jwt.decode(token, self.keys, registry=registry)
            claims = decoded.claims
            jwt.JWTClaimsRegistry(
                iss={"essential": True, "value": self.settings.issuer},
                aud={"essential": True, "value": AUDIENCE},
                sub={"essential": True}, exp={"essential": True}, azp={"essential": True},
            ).validate(claims)
        except Exception as exc:  # noqa: BLE001 - Token and provider errors are never disclosed.
            LOG.info("Token rejected: %s", type(exc).__name__)
            raise DbtError(401, "Sign in again: the token is invalid or expired.", "unauthorized") from None
        if claims["azp"] not in (self.settings.client_id, self.settings.mcp_client_id):
            raise DbtError(401, "This token was not issued to the dbt extension.", "unauthorized")
        name = (claims.get("polaris") or {}).get("principal_name")
        if not isinstance(name, str) or not name.startswith("portal-"):
            raise DbtError(403, "Your account is not linked to a platform user.", "not_linked")
        return Caller(token, claims["sub"], claims.get("preferred_username", ""), claims["azp"], float(claims["exp"]))


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class BrowserLogin:
    """Authorization code with PKCE; sessions and refresh tokens stay in this process."""

    def __init__(self, settings, verifier, http=None):
        self.settings = settings
        self.verifier = verifier
        self.http = http or httpx.AsyncClient(timeout=15, trust_env=False)
        self.pending = {}
        self.sessions = {}

    @property
    def redirect_uri(self):
        return self.settings.origin + "/auth/callback"

    def start(self, return_to="/"):
        now = time.monotonic()
        self.pending = {k: v for k, v in self.pending.items() if v["until"] > now}
        while len(self.pending) >= 500:
            del self.pending[next(iter(self.pending))]
        state, (code_verifier, challenge) = secrets.token_urlsafe(24), pkce()
        parsed = urlsplit(return_to)
        if parsed.scheme or parsed.netloc or not return_to.startswith("/") or return_to.startswith("//"):
            return_to = "/"
        self.pending[state] = {"verifier": code_verifier, "until": now + 300, "return_to": return_to}
        query = urlencode({
            "client_id": self.settings.client_id, "response_type": "code", "redirect_uri": self.redirect_uri,
            "scope": "openid profile", "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return self.settings.issuer + "/protocol/openid-connect/auth?" + query

    async def token(self, data):
        try:
            response = await self.http.post(
                self.settings.internal_issuer + "/protocol/openid-connect/token",
                data={**data, "client_id": self.settings.client_id, "client_secret": self.settings.client_secret},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DbtError(401, "Sign-in failed. Try again.", "unauthorized") from exc
        return response.json()

    async def finish(self, state, code):
        pending = self.pending.pop(state or "", None)
        if not pending or pending["until"] <= time.monotonic() or not code:
            raise DbtError(401, "The sign-in expired. Try again.", "unauthorized")
        issued = await self.token({"grant_type": "authorization_code", "code": code,
                                   "redirect_uri": self.redirect_uri, "code_verifier": pending["verifier"]})
        caller = await self.verifier.verify(issued["access_token"])
        now = time.monotonic()
        session_id = secrets.token_urlsafe(32)
        self.sessions[session_id] = Session(
            issued["access_token"], issued.get("refresh_token", ""),
            now + max(0, caller.expires_at - time.time() - 5), now + 28800,
        )
        return session_id, pending["return_to"]

    async def caller(self, session_id):
        session = self.sessions.get(session_id or "")
        now = time.monotonic()
        if not session or session.expires <= now:
            self.sessions.pop(session_id or "", None)
            raise DbtError(401, "Sign in to continue.", "unauthorized")
        if session.access_until <= now + 30:
            if not session.refresh_token:
                raise DbtError(401, "Your sign-in expired. Sign in again.", "unauthorized")
            try:
                issued = await self.token({"grant_type": "refresh_token", "refresh_token": session.refresh_token})
            except DbtError:
                self.sessions.pop(session_id, None)
                raise
            caller = await self.verifier.verify(issued["access_token"])
            session.access_token = issued["access_token"]
            session.refresh_token = issued.get("refresh_token", session.refresh_token)
            session.access_until = time.monotonic() + max(0, caller.expires_at - time.time() - 5)
            return caller
        return await self.verifier.verify(session.access_token)

    def logout(self, session_id):
        self.sessions.pop(session_id or "", None)
        return self.settings.issuer + "/protocol/openid-connect/logout?" + urlencode(
            {"client_id": self.settings.client_id, "post_logout_redirect_uri": self.settings.origin + "/"})
