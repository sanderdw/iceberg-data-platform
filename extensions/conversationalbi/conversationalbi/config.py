"""Settings from the environment: the Bridge handshake file plus this extension's own options."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.1.5"
ENVIRONMENTS = ("development", "acceptance", "production")
DEFAULT_MODEL = "google:gemini-3.8-flash"


def contract_range():
    try:
        return tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["iceberg-conversationalbi"][
            "bridge-contract"]
    except (OSError, KeyError):
        return ">=0.1,<0.2"


@dataclass(frozen=True)
class Settings:
    bridge_url: str
    extension_id: str
    client_id: str
    client_secret: str = field(repr=False)
    mcp_client_id: str
    issuer: str
    internal_issuer: str
    origin: str
    runtime_url: str = "http://cbi-runtime:4000"
    runtime_key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    model_configured: bool = True
    allow_test_model: bool = False
    max_rows: int = 5000
    llm_rows: int = 20
    llm_bytes: int = 8192
    query_timeout: int = 30
    max_queries: int = 4
    mcp_callback_port: int = 3010
    contract: str = ">=0.1,<0.2"
    # Extensions whose service account may ask questions for a team that enabled both (evaluation.py).
    agent_callers: tuple = ("evaluations",)
    # Other LLM_MODEL strings such a caller may ask with, to compare models; their keys live in llm.env.
    eval_models: tuple = ()
    eval_concurrency: int = 2
    # The evaluation's control arm: an LLM that writes its own SQL. Never the chat; off unless set.
    eval_schema_only: bool = False
    # The conversation record, only for teams that enabled the Evaluations extension (conversations.py).
    state_dir: Path = Path("/data")
    conversation_retention_days: int = 30

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        issuer = env["OIDC_ISSUER"].rstrip("/")
        return cls(
            bridge_url=env.get("BRIDGE_URL", "http://bridge:3005").rstrip("/"),
            extension_id=env.get("BRIDGE_EXTENSION_ID", "conversationalbi"),
            client_id=env["BRIDGE_CLIENT_ID"],
            client_secret=env["BRIDGE_CLIENT_SECRET"],
            mcp_client_id=env.get("BRIDGE_MCP_CLIENT_ID", env["BRIDGE_CLIENT_ID"] + "-mcp"),
            issuer=issuer,
            internal_issuer=env.get("OIDC_INTERNAL_ISSUER", issuer).rstrip("/"),
            origin=env.get("EXTENSION_ORIGIN", "http://localhost:3007").rstrip("/"),
            runtime_url=env.get("RUNTIME_URL", "http://cbi-runtime:4000").rstrip("/"),
            runtime_key=env.get("RUNTIME_KEY", ""),
            model=env.get("LLM_MODEL", "") or DEFAULT_MODEL,
            model_configured=bool(env.get("LLM_MODEL", "")),
            allow_test_model=env.get("BI_ALLOW_TEST_MODEL") == "1",
            max_rows=max(1, min(int(env.get("BI_MAX_ROWS", "5000")), 5000)),  # a query asks for at most 5,000
            llm_rows=max(0, min(int(env.get("BI_LLM_ROWS", "20")), 200)),
            llm_bytes=max(0, min(int(env.get("BI_LLM_BYTES", "8192")), 65536)),
            query_timeout=min(int(env.get("BI_QUERY_TIMEOUT", "30")), 300),
            max_queries=max(1, int(env.get("BI_MAX_QUERIES", "4"))),
            mcp_callback_port=int(env.get("MCP_CALLBACK_PORT", "3010")),
            contract=contract_range(),
            agent_callers=tuple(i for i in env.get("BI_AGENT_CALLERS", "evaluations").replace(" ", "").split(",")
                                if i),
            eval_models=tuple(m for m in env.get("BI_EVAL_MODELS", "").replace(" ", "").split(",") if m),
            eval_concurrency=max(1, int(env.get("BI_EVAL_CONCURRENCY", "2"))),
            eval_schema_only=env.get("BI_EVAL_SCHEMA_ONLY", "false").lower() == "true",
            state_dir=Path(env.get("BI_STATE_DIR", "/data")),
            conversation_retention_days=max(0, min(int(env.get("BI_CONVERSATION_RETENTION_DAYS", "30")), 365)),
        )

    @property
    def secure(self):
        return self.origin.startswith("https://")
