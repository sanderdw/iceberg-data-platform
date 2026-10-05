"""The agent's AG-UI endpoint, for the CopilotKit runtime only (internal port 3017, never published).

A request must carry the runtime key (only the runtime has it) and a run ticket, which the gateway
minted for a signed-in browser session and the runtime forwards unchanged. The ticket leads to the
session and a fresh Keycloak token; the person's teams are read from the Bridge again for every
run. The agent itself is defined in `analyst.yaml`.
"""

import hmac
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic_ai import Agent
from pydantic_ai.ui.ag_ui import AGUIAdapter

from ..errors import CbiError
from .capability import SemanticLayer
from .deps import CbiDeps, CbiState
from .models import resolve_model

AGENT_FILE = Path(__file__).parent / "analyst.yaml"
MAX_BODY = 2 * 1024 * 1024
LOG = logging.getLogger(__name__)


def create_agent(settings, path=AGENT_FILE, model=None):
    return Agent.from_file(path, deps_type=CbiDeps, custom_capability_types=[SemanticLayer],
                           model=model or resolve_model(settings).model, defer_model_check=True)


def create_agent_app(settings, services, login, tickets, agent=None, model=None):
    agent = agent or create_agent(settings, model=model)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def refuse(status, message):
        return JSONResponse({"error": message}, status_code=status)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/")
    async def run(request: Request):
        if not settings.runtime_key or not hmac.compare_digest(
                request.headers.get("x-cbi-runtime-key", "").encode(), settings.runtime_key.encode()):
            return refuse(401, "Unknown caller.")
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return refuse(413, "The conversation is too long. Start a new chat.")
        found = tickets.resolve(request.headers.get("x-cbi-run-ticket"))
        if found is None:
            return refuse(401, "The run ticket expired. Reload the page.")
        session_id, subject = found
        try:
            caller = await login.caller(session_id)
            if caller.subject != subject:
                return refuse(401, "The run ticket belongs to another session.")
            me = await services.me(caller, fresh=True)
        except CbiError as exc:
            return refuse(exc.status, str(exc))
        deps = CbiDeps(state=CbiState(), caller=caller, services=services, me=me)
        return await AGUIAdapter.dispatch_request(request, agent=agent, deps=deps)

    return app
