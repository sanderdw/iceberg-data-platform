"""Keycloak OIDC: authorization code + PKCE, server-side one-use state.

Tokens never enter browser storage.
Refresh tokens remain server-side and renew access without interrupting the browser.
"""

import asyncio
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

import httpx2
from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry

from .models import ServiceError

# Sign-ins awaiting their callback; every entry expires after five minutes.
PENDING_PER_CLIENT = 20
PENDING_TOTAL = 1000
SESSION_MAX_AGE = 28800


@dataclass
class OIDCSession:
    claims: dict = field(repr=False)
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_until: float
    refresh_until: float
    expires: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)



class OIDC:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.issuer = env["OIDC_ISSUER"].rstrip("/")
        internal = env.get("OIDC_INTERNAL_ISSUER", self.issuer).rstrip("/")
        self.origin = env["OIDC_ORIGIN"].rstrip("/")
        self.client_id = env["OIDC_CLIENT_ID"]
        self.secure = self.origin.startswith("https://")
        for url in (self.issuer, internal, self.origin):
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.query or parsed.fragment:
                raise RuntimeError("Invalid OIDC URL configuration")
        if not self.secure and urlsplit(self.origin).hostname != "localhost":
            raise RuntimeError("HTTP OIDC is restricted to localhost")
        self.cookie = "iceberg_oidc_transaction_" + self.client_id
        self.pending = {}
        protocol = "/protocol/openid-connect"
        self.logout_url = self.issuer + protocol + "/logout?" + urlencode({
            "client_id": self.client_id, "post_logout_redirect_uri": self.origin + "/",
        })
        # Browser and container addresses differ, but issuer validation is exact.
        self.client = OAuth().register(
            "keycloak", client_id=self.client_id, client_secret=env["OIDC_CLIENT_SECRET"],
            authorize_url=self.issuer + protocol + "/auth",
            access_token_url=internal + protocol + "/token",
            jwks_uri=internal + protocol + "/certs", issuer=self.issuer,
            id_token_signing_alg_values_supported=["RS256"],
            client_kwargs={"scope": "openid profile", "code_challenge_method": "S256",
                           "timeout": 15, "trust_env": False},
        )

    async def access_claims(self, token):
        keys = KeySet.import_key_set(await self.client.fetch_jwk_set())
        try:
            decoded = jwt.decode(token, keys, registry=JWSRegistry(algorithms=["RS256"]))
        except InvalidKeyIdError:
            # One refresh also handles signing-key rotation; verification stays enabled.
            keys = KeySet.import_key_set(await self.client.fetch_jwk_set(force=True))
            decoded = jwt.decode(token, keys, registry=JWSRegistry(algorithms=["RS256"]))
        jwt.JWTClaimsRegistry(
            iss={"essential": True, "value": self.issuer},
            aud={"essential": True, "value": "polaris"},
            sub={"essential": True}, exp={"essential": True},
        ).validate(decoded.claims)
        return decoded.claims

    def session(self, token, claims, lifetime):
        now = time.monotonic()
        refresh = token.get("refresh_token", "")
        return OIDCSession(
            claims, token["access_token"], refresh, now + lifetime,
            now + max(0, token.get("refresh_expires_in", SESSION_MAX_AGE) - 5),
            now + (SESSION_MAX_AGE if refresh else lifetime),
        )

    async def renew(self, session):
        async with session.lock:
            now = time.monotonic()
            if session.expires <= now:
                raise ServiceError(401, "Your sign-in expired. Sign in again.")
            if session.access_until > now + 60:
                return
            if not session.refresh_token or session.refresh_until <= now:
                if session.access_until > now:
                    return
                session.expires = 0
                raise ServiceError(401, "Your sign-in expired. Sign in again.")
            try:
                token = await self.client.fetch_access_token(
                    grant_type="refresh_token", refresh_token=session.refresh_token,
                )
                claims = await self.access_claims(token["access_token"])
                if claims["sub"] != session.claims["sub"]:
                    raise ValueError("Mismatched subject")
                lifetime = claims["exp"] - time.time() - 5
                if lifetime <= 0:
                    raise ValueError("Expired token")
            except (httpx2.TransportError, httpx2.HTTPStatusError):
                if session.access_until > time.monotonic():
                    return  # The current token is still valid; retry renewal on the next request.
                raise ServiceError(503, "Sign-in service is temporarily unavailable. Please retry.") from None
            except Exception as exc:  # noqa: BLE001 - Never expose token responses or provider errors.
                session.expires = 0
                session.refresh_token = ""
                logging.getLogger(__name__).warning("OIDC renewal failed: %s", type(exc).__name__)
                raise ServiceError(401, "Your sign-in expired. Sign in again.") from None
            session.claims = claims
            session.access_token = token["access_token"]
            session.access_until = time.monotonic() + lifetime
            session.refresh_token = token.get("refresh_token", session.refresh_token)
            session.refresh_until = time.monotonic() + max(0, token.get("refresh_expires_in", SESSION_MAX_AGE) - 5)

    def install(self, app, accept):
        @app.get("/auth/login", include_in_schema=False)
        async def login(request: Request):
            now = time.monotonic()
            target = request.query_params.get("return_to", "/")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or parsed.path != "/" or not target.startswith("/"):
                target = "/"
            # Behind a reverse proxy this is the visitor only if FORWARDED_ALLOW_IPS names the proxy.
            host = request.client.host if request.client else "local"
            self.pending = {k: v for k, v in self.pending.items() if v[0] > now}
            # This route is unauthenticated, so a full table evicts instead of refusing: a
            # flooding client replaces its own oldest sign-ins and cannot deny others a login.
            mine = [k for k, v in self.pending.items() if v[2] == host]
            if len(mine) >= PENDING_PER_CLIENT:
                del self.pending[mine[0]]
            while len(self.pending) >= PENDING_TOTAL:
                del self.pending[next(iter(self.pending))]
            transaction = secrets.token_urlsafe(32)
            request.scope["session"] = {}
            response = await self.client.authorize_redirect(request, self.origin + "/auth/callback")
            self.pending[transaction] = (now + 300, request.session, host, target)
            response.set_cookie(self.cookie, transaction, max_age=300, httponly=True,
                                secure=self.secure, samesite="lax", path="/auth")
            return response

        @app.get("/auth/callback", include_in_schema=False)
        async def callback(request: Request):
            transaction = self.pending.pop(request.cookies.get(self.cookie), None)
            try:
                if not transaction or transaction[0] <= time.monotonic():
                    raise ValueError("Expired transaction")
                request.scope["session"] = transaction[1]
                request.state.oidc_return_to = transaction[3]
                token = await self.client.authorize_access_token(request, leeway=0)
                identity = token["userinfo"]  # Authlib validates signature, issuer, audience and nonce.
                claims = await self.access_claims(token["access_token"])
                if claims["sub"] != identity["sub"]:
                    raise ValueError("Mismatched subject")
                lifetime = min(identity["exp"], claims["exp"]) - time.time() - 5
                if lifetime <= 0:
                    raise ValueError("Expired token")
                response = await accept(request, claims, token["access_token"], lifetime, self.session(token, claims, lifetime))
            except Exception as exc:  # noqa: BLE001 - Provider failures must never disclose tokens.
                logging.getLogger(__name__).warning("OIDC sign-in failed: %s", type(exc).__name__)
                if isinstance(exc, ServiceError):
                    logging.getLogger(__name__).warning("OIDC portal authorization: %s", str(exc))
                # Never echo provider exceptions: they can contain tokens or user details.
                response = HTMLResponse(
                    '<h1>Sign-in failed</h1><p>Your account may not have access, or the sign-in expired.</p>'
                    '<p><a href="/auth/login">Try again</a> · <a href="/">Return to portal</a></p>',
                    status_code=403,
                )
            response.delete_cookie(self.cookie, path="/auth", httponly=True,
                                   secure=self.secure, samesite="lax")
            return response

    @staticmethod
    def redirect(target="/"):
        return RedirectResponse(target, status_code=303)
