"""The Bridge client against Bridge contract 1.x (contracts/bridge in the core)."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from evaluations.bridge_client import BridgeClient, in_range
from evaluations.config import contract_range
from evaluations.errors import EvalError


def test_supports_bridge_contract_1():
    supported = contract_range()
    assert in_range("1.0.0", supported) and in_range("1.4.2", supported)
    assert not in_range("0.1.4", supported) and not in_range("2.0.0", supported)


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
    with pytest.raises(EvalError) as refused:
        asyncio.run(client.request("DELETE", "/automation-principals/svc-" + "0" * 32, "token"))
    assert (refused.value.status, refused.value.code) == expected
