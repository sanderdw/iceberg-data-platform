"""Create this extension's .env and llm.env next to the handshake file the platform wrote (.env.bridge).

  LLM_MODEL=anthropic:claude-sonnet-5-5 ANTHROPIC_API_KEY=… uv run python -m scripts.setup

llm.env holds LLM_MODEL and its provider's credentials (mode 0600); compose gives it to the gateway
only. Without LLM_MODEL in the environment an existing llm.env is kept as it is; in a terminal the
script asks for the model and its key. The release installer runs this file in the platform image,
so it uses the standard library only.
"""

import getpass
import os
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
# Variables the setup takes from the environment; the release installers pass the same names.
PASSTHROUGH = ("LLM_MODEL", "BI_ALLOW_TEST_MODEL", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
               "OPENAI_BASE_URL", "AWS_BEARER_TOKEN_BEDROCK", "AWS_REGION")
VALUE = re.compile(r"^[^\s'\"$#\\`]+$")


@dataclass(frozen=True)
class Provider:
    prefixes: tuple
    label: str
    example: str
    secret: str
    optional: tuple = ()
    region: bool = False

    def names(self):
        return (self.secret, *self.optional, *(("AWS_REGION", "AWS_DEFAULT_REGION") if self.region else ()))


PROVIDERS = (
    Provider(("google",), "Google Gemini", "google:gemini-3.8-flash", "GOOGLE_API_KEY"),
    Provider(("anthropic",), "Anthropic", "anthropic:claude-sonnet-5-5", "ANTHROPIC_API_KEY"),
    Provider(("openai", "openai-chat", "openai-responses"), "OpenAI", "openai:gpt-5.5", "OPENAI_API_KEY",
             optional=("OPENAI_BASE_URL",)),
    Provider(("bedrock", "bedrock-mantle", "anthropic-bedrock"), "Amazon Bedrock",
             "bedrock:global.anthropic.claude-sonnet-5-5", "AWS_BEARER_TOKEN_BEDROCK", region=True),
)
HEADER = """# Conversational BI's LLM, read by the gateway only. Written by scripts/setup.py; edit and restart:
#   docker compose up -d --wait cbi-gateway
# LLM_MODEL is a Pydantic AI model string; its provider needs:
#   google:…                             GOOGLE_API_KEY
#   anthropic:…                          ANTHROPIC_API_KEY
#   openai:…, openai-chat:…              OPENAI_API_KEY (OPENAI_BASE_URL for a compatible endpoint)
#   bedrock:…, anthropic-bedrock:…       AWS_BEARER_TOKEN_BEDROCK and AWS_REGION
"""


def read(path):
    values = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip()
    return values


def provider_for(model):
    prefix = model.partition(":")[0]
    return next((p for p in PROVIDERS if prefix in p.prefixes), None)


def write(path, header, values):
    for key, value in values.items():
        if not VALUE.match(value):
            raise SystemExit(f"{key} has an empty value or one with spaces, quotes, $, # or a backslash.")
    if path.exists():
        path.unlink()
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as file:
        file.write(header + "".join(f"{k}={v}\n" for k, v in values.items()))


def choose(prompt):
    print("Which LLM answers in the chat?")
    for number, provider in enumerate(PROVIDERS, 1):
        print(f"  {number}  {provider.label:<16} {provider.example}")
    answer = prompt("Number, or any Pydantic AI model string [1]: ").strip() or "1"
    if answer.isdigit() and 1 <= int(answer) <= len(PROVIDERS):
        example = PROVIDERS[int(answer) - 1].example
        return prompt(f"Model [{example}]: ").strip() or example
    return answer


def llm_settings(current, env, interactive, ask, prompt):
    """The values for llm.env, or None to keep the existing file."""
    model = env.get("LLM_MODEL", "").strip()
    if not model:
        if current is not None:
            return None
        if not interactive:
            return {k: env[k] for k in PASSTHROUGH if env.get(k)}
        model = choose(prompt)
    provider = provider_for(model)
    # Variables someone added by hand (no setup variable) stay; another provider's credentials go.
    values = {"LLM_MODEL": model, **{k: v for k, v in (current or {}).items()
                                     if k not in PASSTHROUGH and k != "AWS_DEFAULT_REGION"}}
    if provider is None:
        values.update({k: env[k] for k in PASSTHROUGH if env.get(k) and k != "LLM_MODEL"})
        return values
    for name in provider.names():
        value = env.get(name) or (current or {}).get(name)
        if value:
            values[name] = value
    if interactive and not values.get(provider.secret):
        key = ask(f"{provider.secret} (empty to add it to llm.env later): ").strip()
        if key:
            values[provider.secret] = key
    if provider.region:
        region = values.get("AWS_REGION") or values.get("AWS_DEFAULT_REGION")
        if not region and interactive:
            region = prompt("AWS region [us-east-1]: ").strip() or "us-east-1"
        if region:
            values["AWS_REGION"] = values["AWS_DEFAULT_REGION"] = region
    return values


def setup(root=ROOT, env=None, ask=getpass.getpass, prompt=input, interactive=None):
    env = os.environ if env is None else env
    bridge = root / ".env.bridge"
    if not bridge.exists():
        raise SystemExit(
            "No .env.bridge. Register the extension with the platform first, from the platform directory:\n"
            "  uv run python -m scripts.setup --extension conversationalbi --origin http://localhost:3007 "
            "--handshake extensions/conversationalbi/.env.bridge")
    handshake = read(bridge)
    if handshake.get("BRIDGE_CONTRACT") != "1":
        raise SystemExit("Conversational BI needs a Bridge v1 handshake file.")
    config = root / ".env"
    values = read(config) if config.exists() else {}
    origin = urlsplit(handshake["EXTENSION_ORIGIN"])
    values.setdefault("RUNTIME_KEY", secrets.token_hex(24))
    values["PLATFORM_NETWORK"] = handshake["PLATFORM_NETWORK"]
    values["CBI_PORT"] = str(origin.port or (443 if origin.scheme == "https" else 80))

    llm_file = root / "llm.env"
    current = read(llm_file) if llm_file.exists() else None
    interactive = sys.stdin.isatty() if interactive is None else interactive
    llm = llm_settings(current, env, interactive, ask, prompt)
    write(config, "# Generated by scripts.setup; the bridge credentials stay in .env.bridge and the LLM's "
                  "in llm.env.\n", values)
    if llm is not None:
        write(llm_file, HEADER, llm)
    else:
        llm = current

    model = llm.get("LLM_MODEL")
    provider = provider_for(model or "google:")
    missing = [name for name in (provider.secret, *(("AWS_REGION",) if provider.region else ()))
               if not llm.get(name)] if provider else []
    if model:
        print(f"Chat model: {model} (in {llm_file})")
    else:
        print(f"No LLM_MODEL in {llm_file}: the chat uses {PROVIDERS[0].example}.")
    if missing:
        print(f"Add {' and '.join(missing)} to {llm_file} for the chat to answer. "
              "The API and MCP server work without an LLM.")
    if (root / "Dockerfile").exists():
        print("Ready. Build and start the extension:\n  docker compose build && docker compose up -d --wait")


if __name__ == "__main__":
    setup()
