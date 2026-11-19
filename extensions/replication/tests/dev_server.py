"""The real gateway with the platform faked, for UI work without a running platform.

    uv run python -m tests.dev_server          # http://localhost:3014

Open http://localhost:3014/dev/login?user=alice (admin), bob (writer) or carol (reader). The Bridge,
Keycloak and the launcher are the test fakes: runs take a few seconds and write a log and change counts.
"""

import asyncio
import tempfile
import time

import httpx
import uvicorn
from fastapi.responses import RedirectResponse

import replication.app
import replication.validate
from replication.app import create_app
from replication.auth import COOKIE, Caller, Session
from tests.conftest import SOURCE, TABLES, TEAM, FakeBridge, FakeLauncher, FakeVerifier, settings

LOGINS = []


class RecordingLogin(replication.app.BrowserLogin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        LOGINS.append(self)


class BrowserVerifier(FakeVerifier):
    async def verify(self, token):
        caller = await super().verify(token)
        return Caller(caller.token, caller.subject, caller.name, "ext-replication", caller.expires_at)


class SlowLauncher(FakeLauncher):
    """Runs stay running for a few seconds, with a log that grows."""

    def __init__(self, artifacts, seconds=8):
        super().__init__(artifacts)
        self.seconds = seconds
        self.began = {}

    def handler(self, request):
        path = request.url.path
        if request.method == "POST" and path == "/runs":
            response = super().handler(request)
            self.began[response.json()["id"]] = time.monotonic()
            return response
        id = path.split("/")[2] if path.startswith("/runs/") else None
        elapsed = time.monotonic() - self.began.get(id, 0)
        if request.method == "GET" and path.endswith("/logs"):
            lines = [f"INFO Thread[public.orders]: successfully wrote {1000 * i} records" for i in range(int(elapsed))]
            return httpx.Response(200, text="\n".join(lines))
        if request.method == "GET" and id and elapsed < self.seconds and id not in self.killed:
            return httpx.Response(200, json={"state": "running"})
        return super().handler(request)


async def seed(app, bridge):
    services = app.state.services
    await bridge.enable("alice", TEAM, "development")
    alice = Caller("alice", "sub-alice", "alice", "ext-replication", 4102444800.0)
    crm = await services.create_replication(alice, {
        "team": TEAM, "environment": "development", "name": "crm-orders",
        "description": "Orders and customers from the CRM database", "database": "sales", "namespace": "raw_crm",
        "sourceType": "postgres", "source": SOURCE, "tables": TABLES})
    await services.set_replication_secrets(alice, crm["id"], {"password": "dev-secret-password"})
    await services.set_schedule(alice, crm["id"], "*/15 * * * *")
    await services.create_replication(alice, {
        "team": TEAM, "environment": "development", "name": "billing", "database": "raw-data",
        "namespace": "raw_billing", "sourceType": "postgres",
        "source": {**SOURCE, "host": "billing.example.com", "database": "billing", "slot": "iceberg_billing",
                   "publication": "iceberg_billing"}})


async def main():
    # Example hosts resolve to a public address, as in the tests.
    replication.validate.resolve_addresses = lambda host: ["93.184.216.34"]
    bridge = FakeBridge()
    replication.app.BrowserLogin = RecordingLogin
    config = settings(tempfile.mkdtemp(prefix="replication-dev-"))
    app = create_app(config, bridge=bridge, verifier=BrowserVerifier())
    launcher = SlowLauncher(config.state_dir / "artifacts")
    app.state.services.runs.http = httpx.AsyncClient(base_url="http://launcher",
                                                     transport=httpx.MockTransport(launcher.handler))

    @app.get("/dev/login")
    def dev_login(user: str = "alice"):
        session_id = f"dev-{user}-{time.monotonic_ns()}"
        LOGINS[0].sessions[session_id] = Session(user, "", time.monotonic() + 28800, time.monotonic() + 28800)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, session_id, httponly=True, samesite="lax")
        return response

    # Before the app's static-file route, which would otherwise match /dev/login.
    app.router.routes.insert(0, app.router.routes.pop())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=3014, log_level="warning"))

    async def seeded():
        while not server.started:
            await asyncio.sleep(0.1)
        if not app.state.services.store.replications([TEAM]):
            await seed(app, bridge)

    await asyncio.gather(server.serve(), seeded())


if __name__ == "__main__":
    asyncio.run(main())
