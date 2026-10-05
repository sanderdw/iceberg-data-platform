"""The real gateway and agent listener with the platform faked, for UI work without a running platform.

    uv run python -m tests.dev_server          # gateway :3007, agent :3017 (LLM_MODEL=test:flights)
    RUNTIME_KEY=... AGENT_URL=http://127.0.0.1:3017/ node runtime/server.mjs

Open http://localhost:3007/dev/login?user=bob. The Bridge, Polaris and the worker are the test fakes:
team A owns and is shared the flights model, and queries run on the local fixture data.
"""

import asyncio
import os
import time

import httpx
import uvicorn
from fastapi.responses import RedirectResponse

from conversationalbi.__main__ import Server
from conversationalbi.app import create_app
from conversationalbi.auth import COOKIE, Session
from tests.conftest import TEAM, FakeBridge, FakeCatalog, FakeExecutor, FakeVerifier, settings


async def main():
    key = os.environ.setdefault("RUNTIME_KEY", "k" * 32)
    bridge = FakeBridge()
    await bridge.enable("alice", TEAM, "development")
    app = create_app(settings(runtime_key=key, runtime_url="http://127.0.0.1:4000",
                              model=os.environ.get("LLM_MODEL", "test:flights")),
                     bridge=bridge, verifier=FakeVerifier(), catalog=FakeCatalog(bridge), executor=FakeExecutor(),
                     runtime=httpx.AsyncClient(base_url="http://127.0.0.1:4000", timeout=300))

    @app.get("/dev/login")
    def dev_login(user: str = "bob"):
        session_id = f"dev-{user}-{time.monotonic_ns()}"
        app.state.login.sessions[session_id] = Session(user, "", time.monotonic() + 28800, time.monotonic() + 28800)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, session_id, httponly=True, samesite="lax")
        return response

    # Before the app's static-file route, which would otherwise match /dev/login.
    app.router.routes.insert(0, app.router.routes.pop())
    servers = [Server(uvicorn.Config(app, host="127.0.0.1", port=3007, log_level="warning")),
               Server(uvicorn.Config(app.state.agent_app, host="127.0.0.1", port=3017, log_level="warning"))]
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
