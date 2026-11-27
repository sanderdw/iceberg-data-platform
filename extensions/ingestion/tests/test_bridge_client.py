"""The Bridge client against Bridge contract 1.x (contracts/bridge in the core)."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from ingestion.bridge_client import BridgeClient
from ingestion.errors import IngestionError


@pytest.mark.parametrize(("status", "body", "expected"), [
    (404, {"error": "Gone.", "code": "unknown_automation_principal"}, (404, "unknown_automation_principal")),
    (503, {"error": "Polaris is down.", "code": "unavailable"}, (503, "unavailable")),
    (502, {"error": "Refused.", "code": "platform_error"}, (502, "platform_error")),
    (500, {"error": "Failed.", "code": "internal_error"}, (500, "internal_error")),
    # Not a Bridge answer, such as a proxy's error page: the platform failed.
    (504, None, (502, "platform_error")),
])
def test_a_bridge_error_keeps_its_status_and_code(status, body, expected):
    def handler(request):
        return httpx.Response(status, json=body) if body else httpx.Response(status, text="Gateway Timeout")

    client = BridgeClient(SimpleNamespace(bridge_url="http://bridge:3005"),
                          http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(IngestionError) as refused:
        asyncio.run(client.request("DELETE", "/automation-principals/svc-" + "0" * 32, "token"))
    assert (refused.value.status, refused.value.code) == expected
