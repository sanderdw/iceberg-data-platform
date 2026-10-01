"""LLM_MODEL: every supported provider builds, and a broken setting leaves the chat explaining it."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic_ai import Agent

from conversationalbi.agent.models import resolve_model
from conversationalbi.app import create_app
from scripts.setup import PASSTHROUGH
from tests.conftest import FakeBridge, FakeVerifier, settings

ROOT = Path(__file__).resolve().parent.parent
# What scripts/setup.py writes for Bedrock: Converse reads AWS_DEFAULT_REGION, the Anthropic client AWS_REGION.
BEDROCK = {"AWS_BEARER_TOKEN_BEDROCK": "t", "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1"}


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    for name in (*PASSTHROUGH, "AWS_DEFAULT_REGION", "GEMINI_API_KEY", "ANTHROPIC_AWS_API_KEY", "AWS_PROFILE",
                 "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent")


@pytest.mark.parametrize("model, env, expected", [
    ("google:gemini-3.8-flash", {"GOOGLE_API_KEY": "g"}, "GoogleModel"),
    ("anthropic:claude-sonnet-5-5", {"ANTHROPIC_API_KEY": "a"}, "AnthropicModel"),
    ("openai:gpt-5.5", {"OPENAI_API_KEY": "o"}, "OpenAIResponsesModel"),
    ("openai-chat:gpt-5.5", {"OPENAI_API_KEY": "o"}, "OpenAIChatModel"),
    ("bedrock:global.anthropic.claude-sonnet-5-5", BEDROCK, "BedrockConverseModel"),
    ("bedrock-mantle:openai.gpt-oss-120b", BEDROCK, "BedrockMantleResponsesModel"),
    ("anthropic-bedrock:global.anthropic.claude-sonnet-5-5", BEDROCK, "AnthropicModel"),
])
def test_every_provider_builds(monkeypatch, model, env, expected):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    choice = resolve_model(settings(model=model))
    assert (choice.status, type(choice.model).__name__) == ("ready", expected)


@pytest.mark.parametrize("overrides, status, problem", [
    ({"model": "anthropic:claude-sonnet-5-5"}, "missing_credentials", "ANTHROPIC_API_KEY"),
    ({"model": "anthropic-bedrock:global.anthropic.claude-sonnet-5-5"}, "missing_credentials", "region"),
    ({"model": "nope:model"}, "unknown_model", "Unknown model"),
    ({"model": "test:flights", "allow_test_model": False}, "misconfigured", "BI_ALLOW_TEST_MODEL"),
    ({"model": "google:gemini-3.8-flash", "model_configured": False}, "not_configured", "LLM_MODEL is not set"),
])
def test_a_broken_setting_explains_itself_in_the_chat(overrides, status, problem):
    choice = resolve_model(settings(**overrides))
    assert choice.status == status and problem in choice.problem
    reply = Agent(choice.model).run_sync("Which carrier is late most often?").output
    assert problem in reply and "llm.env" in reply and "To try Pydantic AI" not in reply


def test_health_reports_the_llm():
    app = create_app(settings(model="anthropic:claude-sonnet-5-5"), bridge=FakeBridge(), verifier=FakeVerifier())
    assert TestClient(app).get("/api/health").json()["llm"] == "missing_credentials"


def test_only_the_gateway_reads_llm_env():
    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    assert services["cbi-gateway"]["env_file"] == ["llm.env", ".env.bridge"]
    assert "env_file" not in services["cbi-runtime"]
    for service in services.values():
        assert not [name for name in service.get("environment", {})
                    if name in PASSTHROUGH or name.endswith("_API_KEY") or name.startswith("AWS_")]
