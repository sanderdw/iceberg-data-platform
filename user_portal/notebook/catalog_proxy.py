"""A loopback proxy that signs DuckDB's Iceberg catalog requests with the notebook owner's current token.

DuckDB keeps the bearer token it attached with: replacing its iceberg secret, `ATTACH OR REPLACE`
and a `401` all leave it sending the original one, and a notebook's token lasts 15 minutes.
So DuckDB attaches to this proxy instead. Each request it forwards to the real catalog with the
owner's current token, renewed shortly before it expires and once more after a `401`, and streams
the answer back unchanged.

The proxy listens on 127.0.0.1 only and forwards only to the configured catalog's REST paths.
DuckDB proves itself with a random per-process secret, its iceberg secret's TOKEN, so other
processes that share the loopback interface cannot use the owner's identity. Neither that secret
nor a token ever appears in a log or an error message.
"""

import base64
import http.client
import json
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

# Not forwarded either way; the proxy sets its own framing, host and authorization.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection",
              "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length", "authorization",
              "expect"}
MAX_BODY = 64 * 1024 * 1024
CHUNK = 64 * 1024
# A token is replaced this long before it expires; one without a readable expiry is kept this long.
RENEW_BEFORE_SECONDS = 60
UNKNOWN_EXPIRY_SECONDS = 60
MIN_CACHE_SECONDS = 5
UPSTREAM_TIMEOUT_SECONDS = 300
# An idle or stalled connection is closed after this long, and only this many are served at once: a process that
# shares loopback cannot tie up the kernel's threads without the proxy secret.
CLIENT_TIMEOUT_SECONDS = 60
MAX_CONNECTIONS = 32
logger = logging.getLogger(__name__)
_clock = time.time


def token_expiry(token):
    """The `exp` claim of a JWT, in epoch seconds, read without verifying it; None for any other token."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        exp = claims["exp"]
        return float(exp) if isinstance(exp, int | float) and not isinstance(exp, bool) else None
    except (IndexError, KeyError, TypeError, ValueError):
        return None


class Tokens:
    """The owner's current catalog token, fetched again shortly before it expires."""

    def __init__(self, sign_in):
        self.sign_in = sign_in
        self.lock = threading.Lock()
        self.token = None
        self.renew_at = 0.0

    def current(self):
        with self.lock:
            if self.token is None or _clock() >= self.renew_at:
                self._fetch()
            return self.token

    def renew(self, refused):
        """A new token after the catalog refused `refused`, unless another request already replaced it."""
        with self.lock:
            # None: another request's renewal just failed, so this one tries again.
            if self.token is None or self.token == refused:
                self._fetch()
            return self.token

    def _fetch(self):
        self.token = None  # A failed renewal never leaves an expired token in use.
        token = self.sign_in()
        if not token:
            raise RuntimeError("No catalog token")
        now, expires_at = _clock(), token_expiry(token)
        renew_at = expires_at - RENEW_BEFORE_SECONDS if expires_at else now + UNKNOWN_EXPIRY_SECONDS
        self.token, self.renew_at = token, max(renew_at, now + MIN_CACHE_SECONDS)


class SignInError(Exception):
    """The owner's token could not be renewed."""


class CatalogProxy:
    """Serves the catalog at `endpoint` for DuckDB, which must send `Authorization: Bearer <secret>`."""

    def __init__(self, upstream, sign_in):
        target = urlsplit(upstream.rstrip("/"))
        if target.scheme not in {"http", "https"} or not target.hostname or target.query or target.fragment:
            raise ValueError("Invalid catalog endpoint")
        self.target = target
        self.prefix = target.path.rstrip("/")
        self.tokens = Tokens(sign_in)
        self.secret = secrets.token_urlsafe(32)
        self.pid = os.getpid()
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.proxy = self
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}{self.prefix}"
        threading.Thread(target=self.server.serve_forever, name="iceberg-catalog-proxy", daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def authorized(self, values):
        expected = f"Bearer {self.secret}"
        return len(values) == 1 and secrets.compare_digest(values[0].encode(), expected.encode())

    def allowed(self, target):
        """Whether `target`, an origin-form request target, is one of the catalog's REST paths."""
        if not target.startswith("/") or "#" in target or any(ord(c) <= 32 or ord(c) == 127 for c in target):
            return False  # Absolute-form, authority-form and `*` targets would make this an open proxy.
        segments = target.partition("?")[0].split("/")[1:]
        decoded = [unquote(segment) for segment in segments]
        # Judged on the decoded path, as the catalog sees it: no empty, dot or matrix segments, and no segment
        # that decodes to a separator or to another escape.
        if any(not part or part in {".", ".."} or any(c in part for c in "/\\;%") for part in decoded):
            return False
        path = "/" + "/".join(decoded)
        # DuckDB signs in with its TOKEN; the proxy never hands out tokens.
        return path.startswith(self.prefix + "/v1/") and not path.startswith(self.prefix + "/v1/oauth/")

    def _connection(self):
        port = self.target.port
        if self.target.scheme == "https":
            return http.client.HTTPSConnection(self.target.hostname, port or 443, timeout=UPSTREAM_TIMEOUT_SECONDS)
        return http.client.HTTPConnection(self.target.hostname, port or 80, timeout=UPSTREAM_TIMEOUT_SECONDS)

    def _send(self, method, target, body, headers, token):
        connection = self._connection()
        try:
            connection.request(method, target, body=body, headers={**headers, "Authorization": f"Bearer {token}"})
            return connection, connection.getresponse()
        except BaseException:
            connection.close()
            raise

    def _token(self, refused=None):
        try:
            return self.tokens.current() if refused is None else self.tokens.renew(refused)
        except Exception:  # noqa: BLE001 - Provider errors can contain credentials.
            raise SignInError from None

    def forward(self, method, target, body, headers):
        """The catalog's response, with the current token; after a `401`, renews it once and tries again."""
        token = self._token()
        connection, response = self._send(method, target, body, headers, token)
        if response.status != 401:
            return connection, response
        response.read()
        connection.close()
        return self._send(method, target, body, headers, self._token(refused=token))


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = CLIENT_TIMEOUT_SECONDS

    def log_message(self, *args):
        pass  # Request lines carry table names; headers carry the proxy secret.

    def _error(self, status, code, message):
        body = json.dumps({"error": {"message": message, "type": code, "code": status}}).encode()
        self.close_connection = True  # An unread request body must not be parsed as the next request.
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            parts, size = [], 0
            while (length := int(self.rfile.readline(1024).split(b";")[0].strip(), 16)) > 0:
                size += length
                if size > MAX_BODY:
                    raise OverflowError
                parts.append(self.rfile.read(length))
                self.rfile.readline(1024)
            while self.rfile.readline(1024).strip():
                pass  # Trailers.
            return b"".join(parts)
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY:
            raise OverflowError
        return self.rfile.read(length) if length else None

    def relay(self):
        proxy, self._headers_sent = self.server.proxy, False
        if not proxy.authorized(self.headers.get_all("Authorization") or []):
            self._error(403, "ForbiddenException", "This proxy serves only the notebook's own DuckDB connections.")
            return
        if not proxy.allowed(self.path):
            self._error(403, "ForbiddenException", "This proxy forwards only Iceberg catalog requests.")
            return
        try:
            body = self._body()
        except OverflowError:
            self._error(413, "BadRequestException", "Request body too large.")
            return
        except ValueError:
            self._error(400, "BadRequestException", "Malformed request body.")
            return
        drop = HOP_BY_HOP | {name.strip().lower() for name in self.headers.get("Connection", "").split(",")}
        headers = {k: v for k, v in self.headers.items() if k.lower() not in drop}
        connection = None
        try:
            connection, response = proxy.forward(self.command, self.path, body, headers)
            self._stream(response)
        except SignInError:
            self._error(401, "NotAuthorizedException",
                        "Could not renew notebook access. Check your portal session and try again.")
        except (OSError, http.client.HTTPException, ValueError) as exc:
            logger.warning("Iceberg catalog request failed (%s).", type(exc).__name__)
            if self._headers_sent:
                self.close_connection = True  # A truncated response must end the connection.
            else:
                self._error(502, "ServiceUnavailableException", "The Iceberg catalog could not be reached.")
        finally:
            if connection is not None:
                connection.close()

    def _stream(self, response):
        self.send_response_only(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() not in HOP_BY_HOP:
                self.send_header(key, value)
        if self.command == "HEAD" or response.status in {204, 304} or response.status < 200:
            if (length := response.getheader("Content-Length")) is not None and self.command == "HEAD":
                self.send_header("Content-Length", length)
            self.end_headers()
            self._headers_sent = True
            return
        chunked = response.length is None
        self.send_header("Transfer-Encoding" if chunked else "Content-Length",
                         "chunked" if chunked else str(response.length))
        self.end_headers()
        self._headers_sent = True
        while data := response.read1(CHUNK):
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data) if chunked else data)
        if chunked:
            self.wfile.write(b"0\r\n\r\n")
        elif response.length:
            raise http.client.IncompleteRead(b"", response.length)  # The catalog closed early.

    _headers_sent = False
    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = relay


_lock = threading.Lock()
_proxies = {}


def shared(upstream, sign_in):
    """This process's proxy to `upstream`, started on first use and shared by all its connections."""
    with _lock:
        proxy = _proxies.get(upstream)
        if proxy is None or proxy.pid != os.getpid():  # A forked kernel has no server thread.
            proxy = _proxies[upstream] = CatalogProxy(upstream, sign_in)
        return proxy
