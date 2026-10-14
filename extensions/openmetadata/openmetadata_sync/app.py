"""The service: a background sync and two read-only endpoints for health checks and monitoring.

It has no user interface and no write API; `/` sends the portal's link on to OpenMetadata.
/api/status shows counts, times and the last error
code, never names, tokens or OpenMetadata responses.
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from .bridge_client import BridgeClient
from .config import VERSION, Settings
from .om_client import OpenMetadata
from .sync import Syncer

LOG = logging.getLogger(__name__)


def create_app(settings=None, bridge=None, om=None, start=True):
    settings = settings or Settings.from_env()
    syncer = None
    if settings.configured:
        bridge = bridge or BridgeClient(settings)
        om = om or OpenMetadata(settings.om_url, settings.om_token, verify=settings.verify_tls)
        syncer = Syncer(settings, bridge, om)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(syncer.run()) if syncer and start else None
        if not syncer:
            LOG.warning("OM_URL and OM_JWT_TOKEN are not set; nothing is published.")
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if syncer:
                await bridge.close()
                await om.close()

    app = FastAPI(title="OpenMetadata for the Iceberg Data Platform", version=VERSION, docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.syncer = syncer

    @app.get("/", include_in_schema=False)
    def home():
        # The user portal links here: open the catalog where the listings are, or else the status.
        return RedirectResponse(settings.ui_url or "/api/status", status_code=302)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/status")
    def status():
        if not syncer:
            return {"version": VERSION, "state": "not_configured"}
        return {"version": VERSION, **syncer.status}

    return app
