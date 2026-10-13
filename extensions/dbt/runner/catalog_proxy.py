"""A loopback proxy to the Iceberg REST catalog that points vended storage at internal storage.

Polaris vends the platform's public `S3_ENDPOINT` with each table. When that is not a loopback
address (a quick share or a reverse proxy, usually HTTPS), a run without internet cannot reach it,
and a TCP forwarder cannot help: the TLS handshake needs that host's certificate. Instead, dbt
talks to the catalog through this proxy, which replaces the vended `s3.endpoint` by the internal
storage endpoint. DuckDB then signs its S3 requests for the internal host, which storage accepts:
vended credentials are not bound to a host name.
"""

import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
              "transfer-encoding", "upgrade", "host", "content-length", "accept-encoding"}
MAX_BODY = 64 * 1024 * 1024


def rewrite(value, public, internal):
    """Every `s3.endpoint` equal to the public endpoint, anywhere in a catalog response."""
    if isinstance(value, dict):
        return {key: (internal if key == "s3.endpoint" and isinstance(item, str) and item.rstrip("/") == public
                      else rewrite(item, public, internal)) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite(item, public, internal) for item in value]
    return value


def handler(upstream, public, internal):
    target = urlsplit(upstream)

    class Proxy(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def relay(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.send_error(413)
                return
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            # Plain bodies only, so a JSON response can be read and rewritten.
            headers["Accept-Encoding"] = "identity"
            connection = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=300)
            try:
                connection.request(self.command, self.path, body=body, headers=headers)
                response = connection.getresponse()
                data = response.read(MAX_BODY + 1)
                if len(data) > MAX_BODY:
                    self.send_error(502)
                    return
                if "json" in (response.getheader("Content-Type") or "") and b"s3.endpoint" in data:
                    data = json.dumps(rewrite(json.loads(data), public, internal)).encode()
                self.send_response(response.status, response.reason)
                for key, value in response.getheaders():
                    if key.lower() not in HOP_BY_HOP:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (OSError, http.client.HTTPException, ValueError):
                self.send_error(502)
            finally:
                connection.close()

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = relay

    return Proxy


def start(listen, upstream, public, internal):
    """Serve on 127.0.0.1:`listen` in a daemon thread; returns once it accepts connections."""
    server = ThreadingHTTPServer(("127.0.0.1", listen), handler(upstream, public.rstrip("/"), internal.rstrip("/")))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
