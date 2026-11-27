"""The notebook's loopback catalog proxy: token replacement, renewal, refusals and streaming."""

import base64
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from user_portal.notebook import catalog_proxy

BIG = bytes(range(256)) * (12 * 1024 * 1024 // 256)  # 12 MiB, larger than any buffer on the way.


def jwt(name, exp):
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'none'})}.{part({'sub': name, 'exp': exp})}.signature"


class Catalog:
    """A fake Iceberg REST catalog that accepts only the tokens in `valid`."""

    def __init__(self):
        self.requests, self.valid = [], set()
        catalog = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def reply(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                catalog.requests.append((self.command, self.path, dict(self.headers), body))
                if self.headers.get("Authorization", "").removeprefix("Bearer ") not in catalog.valid:
                    return self.send(401, json.dumps({"error": {"code": 401}}).encode())
                if self.path.endswith("/big"):
                    return self.send(200, BIG, "application/octet-stream")
                if self.path.endswith("/chunked"):
                    self.send_response(200)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for start in range(0, len(BIG), 1_000_000):
                        piece = BIG[start:start + 1_000_000]
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
                    self.wfile.write(b"0\r\n\r\n")
                    return None
                return self.send(200, json.dumps({"path": self.path, "body": body.decode()}).encode())

            def send(self, status, body, kind="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Catalog", "kept")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            do_GET = do_POST = do_DELETE = do_HEAD = reply

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.uri = f"http://127.0.0.1:{self.server.server_port}/api/catalog"


@pytest.fixture
def setup(monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(catalog_proxy, "_clock", lambda: clock["now"])
    catalog, issued = Catalog(), []

    def sign_in():
        token = jwt(f"user-{len(issued)}", clock["now"] + 900)
        issued.append(token)
        catalog.valid.add(token)
        return token

    proxy = catalog_proxy.CatalogProxy(catalog.uri, sign_in)
    yield proxy, catalog, issued, clock
    proxy.close()
    catalog.server.server_close()


def call(proxy, method, path, secret=None, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", proxy.server.server_port, timeout=30)
    try:
        auth = {} if secret is False else {"Authorization": f"Bearer {secret or proxy.secret}"}
        connection.request(method, path, body=body, headers={**auth, **(headers or {})},
                           encode_chunked="Transfer-Encoding" in (headers or {}))
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_the_owners_token_replaces_the_proxy_secret(setup):
    proxy, catalog, issued, _ = setup
    assert proxy.endpoint == f"http://127.0.0.1:{proxy.server.server_port}/api/catalog"
    status, headers, body = call(proxy, "POST", "/api/catalog/v1/db/namespaces?parent=a%1Fb", body=b'{"x": 1}',
                                 headers={"Content-Type": "application/json", "Connection": "keep-alive, X-Drop",
                                          "X-Drop": "hop", "X-Iceberg-Access-Delegation": "vended-credentials"})
    assert status == 200 and headers["X-Catalog"] == "kept"
    assert json.loads(body) == {"path": "/api/catalog/v1/db/namespaces?parent=a%1Fb", "body": '{"x": 1}'}
    (_, _, sent, _), = catalog.requests
    assert sent["Authorization"] == f"Bearer {issued[0]}" and proxy.secret not in json.dumps(sent)
    assert sent["Host"] == catalog.uri.split("/")[2] and "X-Drop" not in sent
    assert sent["X-Iceberg-Access-Delegation"] == "vended-credentials"
    # The token is cached: a second request does not sign in again.
    assert call(proxy, "GET", "/api/catalog/v1/config?warehouse=db")[0] == 200
    assert len(issued) == 1


@pytest.mark.parametrize("secret", [False, "wrong", "token-of-another-process"])
def test_a_wrong_or_missing_proxy_secret_is_refused(setup, secret):
    proxy, catalog, issued, _ = setup
    status, _, body = call(proxy, "GET", "/api/catalog/v1/config", secret=secret)
    assert status == 403 and catalog.requests == [] and issued == []
    assert proxy.secret not in body.decode()


def test_two_authorization_headers_are_refused(setup):
    proxy, catalog, _, _ = setup
    connection = http.client.HTTPConnection("127.0.0.1", proxy.server.server_port, timeout=30)
    connection.putrequest("GET", "/api/catalog/v1/config")
    connection.putheader("Authorization", "Bearer wrong")
    connection.putheader("Authorization", f"Bearer {proxy.secret}")
    connection.endheaders()
    assert connection.getresponse().status == 403 and catalog.requests == []


@pytest.mark.parametrize("path", [
    "/api/catalog/v1/../../admin/users", "/api/catalog/v1/%2e%2e/%2E%2E/management/v1/catalogs",
    "/api/catalog/v1/db/..%5C..%5Cx", "/api/other/v1/config", "/api/catalog/v2/config", "/api/catalogue/v1/config",
    "/api/catalog/v1/oauth/tokens", "http://evil.example/api/catalog/v1/config", "//evil.example/api/catalog/v1/x",
    "*",
    # Encoded or doubled separators that a catalog could normalise into another path.
    "/api/catalog/v1/%6Fauth/tokens", "/api/catalog/v1//oauth/tokens", "/api/catalog/v1/x%2F..%2F..%2Fmanagement%2Fv1",
    "/api/catalog/v1/..;/..;/management/v1/catalogs", "/api/catalog/v1/x;y/tables", "/api/catalog/v1/%252e%252e/x",
])
def test_requests_outside_the_catalog_are_refused(setup, path):
    proxy, catalog, _, _ = setup
    assert call(proxy, "GET", path)[0] == 403
    assert catalog.requests == []


def test_a_rotated_token_is_used_after_expiry(setup):
    proxy, catalog, issued, clock = setup
    call(proxy, "GET", "/api/catalog/v1/config")
    clock["now"] += 900 - catalog_proxy.RENEW_BEFORE_SECONDS - 1
    call(proxy, "GET", "/api/catalog/v1/config")
    assert len(issued) == 1
    clock["now"] += 1
    catalog.valid.discard(issued[0])  # Expired at the catalog.
    assert call(proxy, "GET", "/api/catalog/v1/config")[0] == 200
    assert len(issued) == 2 and catalog.requests[-1][2]["Authorization"] == f"Bearer {issued[1]}"
    assert len(catalog.requests) == 3  # Renewed before use: no refused request.


def test_an_upstream_401_renews_the_token_once_and_retries(setup):
    proxy, catalog, issued, _ = setup
    call(proxy, "GET", "/api/catalog/v1/config")
    catalog.valid.clear()  # Revoked early, say after a Keycloak sign-out elsewhere.
    status, _, body = call(proxy, "POST", "/api/catalog/v1/db/namespaces", body=b"commit")
    assert status == 200 and json.loads(body)["body"] == "commit"
    assert len(issued) == 2
    assert [r[2]["Authorization"] for r in catalog.requests[1:]] == [f"Bearer {issued[0]}", f"Bearer {issued[1]}"]
    assert catalog.requests[-1][3] == b"commit"


def test_a_401_after_renewal_is_returned_without_a_loop(monkeypatch):
    catalog = Catalog()
    proxy = catalog_proxy.CatalogProxy(catalog.uri, lambda: "opaque-token")
    try:
        status, _, _ = call(proxy, "GET", "/api/catalog/v1/config")
        assert status == 401 and len(catalog.requests) == 2
    finally:
        proxy.close()


def test_a_failed_sign_in_answers_401_without_details(monkeypatch, caplog):
    catalog = Catalog()

    def sign_in():
        raise RuntimeError("private-refresh-token")

    proxy = catalog_proxy.CatalogProxy(catalog.uri, sign_in)
    try:
        status, _, body = call(proxy, "GET", "/api/catalog/v1/config")
        assert status == 401 and b"Could not renew notebook access" in body
        assert b"private" not in body and "private" not in caplog.text and catalog.requests == []
    finally:
        proxy.close()


def test_an_unreachable_catalog_answers_502_without_details(caplog):
    unused = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = unused.server_port
    unused.server_close()
    proxy = catalog_proxy.CatalogProxy(f"http://127.0.0.1:{port}/api/catalog", lambda: "private-token")
    try:
        status, _, body = call(proxy, "GET", "/api/catalog/v1/config")
        assert status == 502 and b"private" not in body and "private" not in caplog.text
    finally:
        proxy.close()


@pytest.mark.parametrize("path", ["/api/catalog/v1/big", "/api/catalog/v1/chunked"])
def test_large_responses_are_streamed_unchanged(setup, path):
    proxy, _, _, _ = setup
    status, headers, body = call(proxy, "GET", path)
    assert status == 200 and body == BIG
    assert ("Content-Length" in headers) == path.endswith("/big")


def test_head_keeps_the_catalogs_content_length(setup):
    proxy, _, _, _ = setup
    status, headers, body = call(proxy, "HEAD", "/api/catalog/v1/big")
    assert status == 200 and headers["Content-Length"] == str(len(BIG)) and body == b""


def test_chunked_request_bodies_are_forwarded(setup):
    proxy, catalog, _, _ = setup
    status, _, body = call(proxy, "POST", "/api/catalog/v1/db/tables", body=iter([b"part one, ", b"part two"]),
                           headers={"Transfer-Encoding": "chunked"})
    assert status == 200 and json.loads(body)["body"] == "part one, part two"
    assert catalog.requests[-1][2]["Content-Length"] == "18"


def test_one_proxy_per_process_and_catalog():
    first = catalog_proxy.shared("http://polaris.invalid:8181/api/catalog", lambda: "t")
    try:
        assert catalog_proxy.shared("http://polaris.invalid:8181/api/catalog", lambda: "t") is first
        assert first.server.server_address[0] == "127.0.0.1"
    finally:
        catalog_proxy._proxies.pop("http://polaris.invalid:8181/api/catalog").close()


def test_token_expiry_reads_only_a_numeric_jwt_exp():
    assert catalog_proxy.token_expiry(jwt("a", 1234)) == 1234
    assert catalog_proxy.token_expiry("opaque") is None
    assert catalog_proxy.token_expiry("a.%%%.c") is None
    assert catalog_proxy.token_expiry(jwt("a", "soon")) is None


class IcebergCatalog(Catalog):
    """The fake catalog, serving one real Iceberg table that PyIceberg wrote to local files."""

    def __init__(self, table):
        super().__init__()
        self.table = table
        loaded = {"metadata-location": table.metadata_location,
                  "metadata": json.loads(table.metadata.model_dump_json()),
                  "config": {"s3.access-key-id": "key", "s3.secret-access-key": "private-storage-secret",
                             "s3.session-token": "private-session-token"}}
        answers = {
            "/api/catalog/v1/config": {"defaults": {}, "overrides": {"prefix": "db"}, "endpoints": []},
            "/api/catalog/v1/db/namespaces": {"namespaces": [["analytics"]]},
            "/api/catalog/v1/db/namespaces/analytics": {"namespace": ["analytics"], "properties": {}},
            "/api/catalog/v1/db/namespaces/analytics/tables": {
                "identifiers": [{"namespace": ["analytics"], "name": "events"}]},
            "/api/catalog/v1/db/namespaces/analytics/tables/events": loaded,
        }
        handler = self.server.RequestHandlerClass
        catalog = self

        def reply(request):
            catalog.requests.append((request.command, request.path, dict(request.headers), b""))
            if request.headers.get("Authorization", "").removeprefix("Bearer ") not in catalog.valid:
                return handler.send(request, 401, b'{"error": {"code": 401}}')
            path, _, query = request.path.partition("?")
            answer = {"namespaces": []} if "parent=" in query else answers.get(path)
            if answer is None:
                return handler.send(request, 404, b'{"error": {"code": 404}}')
            return handler.send(request, 200, json.dumps(answer).encode())

        handler.do_GET = handler.do_HEAD = reply


def test_duckdb_keeps_querying_after_the_notebook_token_rotates(monkeypatch, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    pyarrow = pytest.importorskip("pyarrow")
    sql = pytest.importorskip("pyiceberg.catalog.sql")
    pytest.importorskip("sqlalchemy")
    from user_portal.notebook import duckdb_connection as native
    try:
        duckdb.connect().execute("LOAD httpfs; LOAD iceberg")
    except duckdb.Error:
        pytest.skip("DuckDB's iceberg extension is not installed")

    local = sql.SqlCatalog("local", uri=f"sqlite:///{tmp_path}/catalog.db", warehouse=f"file://{tmp_path}/warehouse")
    local.create_namespace("analytics")
    table = local.create_table("analytics.events", schema=pyarrow.schema([("id", pyarrow.int64())]))
    table.append(pyarrow.table({"id": [1, 2, 3]}))
    catalog = IcebergCatalog(table)
    clock, issued = {"now": 1_000_000.0}, []

    def access_token():  # The portal's session-token URL: a new 15-minute token each time.
        token = jwt(f"user-{len(issued)}", clock["now"] + 900)
        issued.append(token)
        catalog.valid.add(token)
        return token

    monkeypatch.setattr(catalog_proxy, "_clock", lambda: clock["now"])
    monkeypatch.setattr(native, "access_token", access_token)
    for key, value in {"ICEBERG_CATALOG_URI": catalog.uri, "ICEBERG_DATABASE": "db",
                       "ICEBERG_S3_ENDPOINT": "http://rustfs:9000"}.items():
        monkeypatch.setenv(key, value)

    connection = native.connect_duckdb(("analytics",), "events")
    try:
        query = "SELECT sum(id) FROM lakehouse.analytics.events"
        assert connection.execute(query).fetchone() == (6,)
        assert all(proxy_secret_absent(request, catalog_proxy._proxies[catalog.uri]) for request in catalog.requests)
        assert not any("oauth" in request[1] for request in catalog.requests)  # TOKEN secret: no sign-in.

        clock["now"] += 900  # Every token issued so far has expired.
        catalog.valid.clear()
        seen = len(catalog.requests)
        assert connection.execute(query).fetchone() == (6,)
        assert catalog.requests[seen:]  # DuckDB loaded the table again ...
        assert {request[2]["Authorization"] for request in catalog.requests[seen:]} == {f"Bearer {issued[-1]}"}

        catalog.valid.clear()  # ... and again after the catalog refused a token it had not yet expired.
        seen = len(catalog.requests)
        assert connection.execute(query).fetchone() == (6,)
        assert catalog.requests[seen + 1][2]["Authorization"] == f"Bearer {issued[-1]}"
        assert catalog.requests[seen][2]["Authorization"] == f"Bearer {issued[-2]}"
    finally:
        connection.close()
        catalog_proxy._proxies.pop(catalog.uri).close()


def proxy_secret_absent(request, proxy):
    return proxy.secret not in json.dumps(request[2]) and request[2]["Authorization"].startswith("Bearer ey")


def test_a_failed_renewal_elsewhere_makes_the_next_request_sign_in_again():
    tokens = iter(["first", None, "third"])
    renewals = catalog_proxy.Tokens(lambda: next(tokens))
    assert renewals.current() == "first"
    with pytest.raises(RuntimeError):
        renewals.renew("first")  # This sign-in fails: no token is kept.
    assert renewals.renew("first") == "third"  # Never "Bearer None".


def test_idle_connections_are_closed_and_their_number_is_capped():
    assert catalog_proxy._Handler.timeout == catalog_proxy.CLIENT_TIMEOUT_SECONDS
    proxy = catalog_proxy.CatalogProxy("http://127.0.0.1:1/api/catalog", lambda: "token")
    try:
        held = [proxy.server.slots.acquire(blocking=False) for _ in range(catalog_proxy.MAX_CONNECTIONS)]
        assert all(held)
        connection = http.client.HTTPConnection("127.0.0.1", proxy.server.server_port, timeout=5)
        connection.request("GET", "/api/catalog/v1/config", headers={"Authorization": f"Bearer {proxy.secret}"})
        with pytest.raises((ConnectionError, http.client.RemoteDisconnected)):
            connection.getresponse()
    finally:
        for _ in range(catalog_proxy.MAX_CONNECTIONS):
            proxy.server.slots.release()
        proxy.close()
