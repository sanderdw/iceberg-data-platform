"""scripts/setup.py: .env and llm.env from the handshake file, the environment or the prompts."""

import ast
import stat
import sys
from pathlib import Path

import pytest

from scripts import setup as cbi_setup

HANDSHAKE = ("BRIDGE_CONTRACT=1\nEXTENSION_ORIGIN=http://localhost:3007\n"
             "PLATFORM_NETWORK=iceberg-platform_default\nBRIDGE_CLIENT_SECRET=" + "b" * 48 + "\n")


def never(*args):
    raise AssertionError("prompted although unattended")


def answers(*values):
    queue = list(values)
    return lambda text: queue.pop(0)


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".env.bridge").write_text(HANDSHAKE)
    return tmp_path


def run(root, env=None, interactive=False, ask=never, prompt=never):
    cbi_setup.setup(root, env=env or {}, ask=ask, prompt=prompt, interactive=interactive)
    return cbi_setup.read(root / ".env"), cbi_setup.read(root / "llm.env")


def test_needs_the_handshake_file(tmp_path):
    with pytest.raises(SystemExit, match="Register the extension"):
        cbi_setup.setup(tmp_path, env={}, interactive=False)


def test_unattended_writes_private_files_without_a_model(root, capsys):
    config, llm = run(root)
    assert set(config) == {"RUNTIME_KEY", "PLATFORM_NETWORK", "CBI_PORT"}
    assert llm == {}
    for name in (".env", "llm.env"):
        assert stat.S_IMODE((root / name).stat().st_mode) == 0o600
    assert "Add GOOGLE_API_KEY" in capsys.readouterr().out


@pytest.mark.parametrize("model, env, expected", [
    ("google:gemini-3.8-flash", {"GOOGLE_API_KEY": "g" * 20}, {"GOOGLE_API_KEY": "g" * 20}),
    ("anthropic:claude-sonnet-5-5", {"ANTHROPIC_API_KEY": "a" * 20}, {"ANTHROPIC_API_KEY": "a" * 20}),
    ("openai-chat:gpt-5.5", {"OPENAI_API_KEY": "o" * 20, "OPENAI_BASE_URL": "https://llm.example/v1"},
     {"OPENAI_API_KEY": "o" * 20, "OPENAI_BASE_URL": "https://llm.example/v1"}),
    ("bedrock:global.anthropic.claude-sonnet-5-5", {"AWS_BEARER_TOKEN_BEDROCK": "t" * 20, "AWS_REGION": "eu-west-1"},
     {"AWS_BEARER_TOKEN_BEDROCK": "t" * 20, "AWS_REGION": "eu-west-1", "AWS_DEFAULT_REGION": "eu-west-1"}),
    ("anthropic-bedrock:global.anthropic.claude-sonnet-5-5",
     {"AWS_BEARER_TOKEN_BEDROCK": "t" * 20, "AWS_REGION": "us-east-1"},
     {"AWS_BEARER_TOKEN_BEDROCK": "t" * 20, "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1"}),
])
def test_each_provider_from_the_environment(root, model, env, expected):
    stray = {"OPENAI_API_KEY": "x" * 20} if not model.startswith("openai") else {"GOOGLE_API_KEY": "x" * 20}
    config, llm = run(root, {"LLM_MODEL": model, **stray, **env})
    assert llm == {"LLM_MODEL": model, **expected}
    assert not set(config) & set(cbi_setup.PASSTHROUGH)


def test_the_menu_asks_for_the_model_and_its_key(root):
    _, llm = run(root, interactive=True, prompt=answers("2", ""), ask=answers("a" * 20))
    assert llm == {"LLM_MODEL": "anthropic:claude-sonnet-5-5", "ANTHROPIC_API_KEY": "a" * 20}


def test_bedrock_asks_for_the_region(root):
    _, llm = run(root, interactive=True, prompt=answers("4", "bedrock:eu.anthropic.claude-sonnet-5-5", ""),
                 ask=answers("t" * 20))
    assert llm == {"LLM_MODEL": "bedrock:eu.anthropic.claude-sonnet-5-5", "AWS_BEARER_TOKEN_BEDROCK": "t" * 20,
                   "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1"}


def test_any_model_string_without_prompts_for_unknown_providers(root):
    _, llm = run(root, interactive=True, prompt=answers("ollama:qwen3"))
    assert llm == {"LLM_MODEL": "ollama:qwen3"}
    _, llm = run(root, {"LLM_MODEL": "test:flights", "BI_ALLOW_TEST_MODEL": "1"})
    assert llm == {"LLM_MODEL": "test:flights", "BI_ALLOW_TEST_MODEL": "1"}


def test_a_rerun_keeps_llm_env_and_the_runtime_key(root):
    config, _ = run(root, {"LLM_MODEL": "anthropic:claude-sonnet-5-5", "ANTHROPIC_API_KEY": "a" * 20})
    before = (root / "llm.env").read_bytes()
    again, _ = run(root, interactive=True)
    assert (root / "llm.env").read_bytes() == before
    assert again["RUNTIME_KEY"] == config["RUNTIME_KEY"]


def test_a_new_model_reuses_its_key_and_drops_other_providers(root):
    (root / "llm.env").write_text("LLM_MODEL=anthropic:claude-sonnet-5\nANTHROPIC_API_KEY=" + "a" * 20 +
                                  "\nANTHROPIC_BASE_URL=https://proxy.example\n")
    _, llm = run(root, {"LLM_MODEL": "anthropic:claude-opus-5-5"})
    assert llm == {"LLM_MODEL": "anthropic:claude-opus-5-5", "ANTHROPIC_BASE_URL": "https://proxy.example",
                   "ANTHROPIC_API_KEY": "a" * 20}
    _, llm = run(root, {"LLM_MODEL": "openai:gpt-5.5", "OPENAI_API_KEY": "o" * 20})
    assert llm == {"LLM_MODEL": "openai:gpt-5.5", "ANTHROPIC_BASE_URL": "https://proxy.example",
                   "OPENAI_API_KEY": "o" * 20}


@pytest.mark.parametrize("value", ["has space", "quote'd", "$HOME", "a#b", "back\\slash"])
def test_unsafe_values_are_refused(root, value):
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        run(root, {"LLM_MODEL": "anthropic:claude-sonnet-5-5", "ANTHROPIC_API_KEY": value})


def test_runs_with_the_standard_library_only():
    # The release installer runs it in the platform image, which has none of this extension's packages.
    tree = ast.parse(Path(cbi_setup.__file__).read_text())
    modules = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    modules |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert modules <= sys.stdlib_module_names
