"""The real gateway with the platform faked, for UI work without a running platform.

    uv run python -m tests.dev_server          # http://localhost:3013

Open http://localhost:3013/dev/login?user=alice (admin), bob (writer) or carol (reader). The Bridge,
Keycloak, Apache Polaris and the launcher are the test fakes: runs take a few seconds and write each table's
health. The development database has a policy, an excluded table and a namespace policy set by another tool.
"""

import asyncio
import tempfile
import time

import httpx
import uvicorn
from fastapi.responses import RedirectResponse

import maintenance.app
from maintenance.app import create_app
from maintenance.auth import COOKIE, Caller, Session
from tests.conftest import DATABASES, TEAM, FakeBridge, FakeLauncher, FakePolaris, FakeVerifier, settings, wire

LOGINS = []


class RecordingLogin(maintenance.app.BrowserLogin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        LOGINS.append(self)


class BrowserVerifier(FakeVerifier):
    async def verify(self, token):
        caller = await super().verify(token)
        return Caller(caller.token, caller.subject, caller.name, "ext-maintenance", caller.expires_at)


class SlowLauncher(FakeLauncher):
    """Runs stay running for a few seconds, with a log that grows."""

    def __init__(self, artifacts, seconds=6):
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
            lines = [f"raw.table_{i}: 12 snapshots, 40 data files (2.0 GB), 30 small" for i in range(int(elapsed))]
            return httpx.Response(200, text="\n".join(lines))
        if request.method == "GET" and id and elapsed < self.seconds and id not in self.killed:
            return httpx.Response(200, json={"state": "running"})
        return super().handler(request)


async def seed(app, bridge, polaris, launcher):
    services = app.state.services
    await bridge.enable("alice", TEAM, "development")
    await bridge.enable("alice", TEAM, "production")
    alice = Caller("alice", "sub-alice", "alice", "ext-maintenance", 4102444800.0)
    dev = DATABASES[("grid-capacity", "development")]
    await services.update_policy(alice, dev, {
        "settings": {"orphanFileRemoval": {"enabled": True}},
        "exclusions": [{"target": "raw.transformer_register", "level": "table"},
                       {"target": "marts", "level": "namespace", "types": ["orphanFileRemoval"]}]})
    # A policy another tool attached to a namespace: Maintenance honours it.
    polaris.put_policy(dev, ["raw"], "raw-compaction", "system.data-compaction", {
        "version": "2025-02-03", "enable": True, "config": {"target-file-size-bytes": 268435456}},
        {"type": "namespace", "path": ["raw"]})
    await services.set_schedule(alice, dev, "0 2 * * *")
    launcher.seconds = 0
    await services.run_maintenance(alice, dev, "inspect", wait=10)
    await services.run_maintenance(alice, DATABASES[("grid-capacity", "production")], "inspect", wait=10)
    launcher.seconds = 6


async def main():
    bridge = FakeBridge()
    maintenance.app.BrowserLogin = RecordingLogin
    config = settings(tempfile.mkdtemp(prefix="maintenance-dev-"))
    app = create_app(config, bridge=bridge, verifier=BrowserVerifier())
    polaris, launcher = FakePolaris(), SlowLauncher(config.state_dir / "artifacts")
    wire(app, polaris, launcher)

    @app.get("/dev/login")
    def dev_login(user: str = "alice"):
        session_id = f"dev-{user}-{time.monotonic_ns()}"
        LOGINS[0].sessions[session_id] = Session(user, "", time.monotonic() + 28800, time.monotonic() + 28800)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, session_id, httponly=True, samesite="lax")
        return response

    # Before the app's static-file route, which would otherwise match /dev/login.
    app.router.routes.insert(0, app.router.routes.pop())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=3013, log_level="warning"))

    async def seeded():
        while not server.started:
            await asyncio.sleep(0.1)
        await seed(app, bridge, polaris, launcher)

    await asyncio.gather(server.serve(), seeded())


if __name__ == "__main__":
    asyncio.run(main())
