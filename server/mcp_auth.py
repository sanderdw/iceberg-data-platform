"""Keycloak bearer-token authentication and mounting shared by both portals' MCP endpoints."""

import logging
from contextlib import asynccontextmanager

from fastapi.responses import JSONResponse
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool

from .models import ServiceError

MCP_CLIENT_ID = "iceberg-mcp"
# Room for the largest valid tool call, a 100-part namespace of escaped names, and no more.
MCP_MAX_BODY = 256 * 1024
LOG = logging.getLogger(__name__)


def is_mcp_path(path):
    return path == "/mcp" or path.startswith("/.well-known/")


def protected_resource(oidc, resource_name):
    """RFC 9728 metadata: which authorization server issues tokens for the MCP endpoint."""
    return {
        "resource": oidc.origin + "/mcp",
        "authorization_servers": [oidc.issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["openid", "profile"],
        "resource_name": resource_name,
    }


class KeycloakVerifier:
    """Accept only Keycloak tokens issued to the MCP client; tools decide what the caller may do."""

    def __init__(self, oidc):
        self.oidc = oidc

    async def verify_token(self, token):
        try:
            # Signature, issuer, the Polaris audience, subject and expiry, like portal sign-in.
            claims = await self.oidc.access_claims(token)
        except Exception as exc:  # noqa: BLE001 - Token and provider errors must never be disclosed.
            LOG.info("MCP token rejected: %s", type(exc).__name__)
            return None
        # azp is the signed authorized party: tokens of the browser portals are not accepted here.
        if claims.get("azp") != MCP_CLIENT_ID:
            LOG.info("MCP token rejected: not issued to the MCP client")
            return None
        mapping = claims.get("polaris", {})
        name = mapping.get("principal_name")
        # An account without a platform link is authenticated but gets a readable tool
        # error instead of a 401, which would only send its MCP client back to sign-in.
        if not isinstance(name, str) or not name.startswith("portal-") or mapping.get("principal_id") != 0:
            name = ""
        # Client roles of this portal's own Keycloak client, such as platform-admin.
        roles = claims.get("resource_access", {}).get(self.oidc.client_id, {}).get("roles", [])
        return AccessToken(
            token=token,
            client_id=MCP_CLIENT_ID,
            scopes=claims.get("scope", "").split(),
            expires_at=int(claims["exp"]),
            subject=claims["sub"],
            claims={"iss": claims["iss"], "polaris": {"principal_name": name}, "roles": list(roles)},
        )


def current_access():
    access = get_access_token()
    if access is None:
        raise ToolError("Sign in through Keycloak to use these tools.")
    return access


def mcp_server(name, oidc, *, title, instructions):
    return MCPServer(
        name,
        title=title,
        instructions=instructions,
        token_verifier=KeycloakVerifier(oidc),
        auth=AuthSettings(
            issuer_url=oidc.issuer,
            resource_server_url=oidc.origin + "/mcp",
            # Keycloak has no RFC 8707 resource binding; the verifier checks aud and azp itself.
            validate_token_resource=False,
        ),
        log_level="WARNING",
    )


async def bounded_body(scope, receive, limit):
    """The whole request body, or None once it exceeds `limit` bytes."""
    length = dict(scope["headers"]).get(b"content-length", b"")
    if length.isdigit() and int(length) > limit:
        return None
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return bytes(body)
        body.extend(message.get("body", b""))
        if len(body) > limit:
            return None
        if not message.get("more_body"):
            return bytes(body)


class MCPEndpoint:
    """ASGI endpoint for /mcp that forwards to the transport built by the current lifespan."""

    def __init__(self):
        self.app = None

    async def __call__(self, scope, receive, send):
        if self.app is None:
            response = JSONResponse({"error": "The MCP transport is not running."}, status_code=503)
            await response(scope, receive, send)
            return
        # The gateways' body cap does not cover this route, and the transport parses the
        # whole JSON body before any tool argument is validated.
        body = await bounded_body(scope, receive, MCP_MAX_BODY)
        if body is None:
            await JSONResponse({"error": "Request is too large."}, status_code=413)(scope, receive, send)
            return
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)


def mount_mcp(app, mcp, oidc, resource_name):
    """Serve the MCP endpoint at /mcp and its resource metadata.

    Returns an async context manager for the host lifespan. The transport is built inside
    it because the SDK's session manager runs once per instance, while an application
    can start several lifespans, as tests do.
    """
    endpoint = MCPEndpoint()
    app.state.mcp = mcp
    # An exact route with an ASGI endpoint serves every method; a mount would need a trailing path.
    app.add_route("/mcp", endpoint)
    metadata = protected_resource(oidc, resource_name)

    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    def resource_metadata():
        return metadata

    @asynccontextmanager
    async def running():
        endpoint.app = mcp.streamable_http_app(
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
            # Bearer tokens are not ambient browser credentials, so Host allow-listing
            # would only refuse LAN and proxied names.
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            endpoint.app = None

    return running


async def call(fn, *args):
    """Run a blocking provider call off the event loop; user-facing errors become tool errors."""
    try:
        return await run_in_threadpool(fn, *args)
    except ServiceError as exc:
        raise ToolError(str(exc)) from None


async def run_locked(lock, fn, *args):
    async with lock:
        return await call(fn, *args)
