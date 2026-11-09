"""Settings from the environment: the Bridge handshake file plus this extension's own options."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.1.0"
ENVIRONMENTS = ("development", "acceptance", "production")
# The agent under evaluation; another extension of the platform that answers questions for a team.
AGENT = "conversationalbi"


def contract_range():
    try:
        return tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["iceberg-evaluations"]["bridge-contract"]
    except (OSError, KeyError):
        return ">=0.1.3,<0.2"


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
    state_dir: Path
    agent_url: str = "http://cbi-gateway:3007"
    max_runs: int = 2
    # Questions of one run asked at once; the agent limits what it answers at once too (BI_EVAL_CONCURRENCY).
    concurrency: int = 2
    # Finished runs per question set whose case results are kept here; the Iceberg tables keep all.
    run_retention: int = 50
    # An LLM for the judge scorers; without one, only the scorers in code run.
    judge_model: str = ""
    mcp_callback_port: int = 3010
    contract: str = ">=0.1.3,<0.2"

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        issuer = env["OIDC_ISSUER"].rstrip("/")
        return cls(
            bridge_url=env.get("BRIDGE_URL", "http://bridge:3005").rstrip("/"),
            extension_id=env.get("BRIDGE_EXTENSION_ID", "evaluations"),
            client_id=env["BRIDGE_CLIENT_ID"],
            client_secret=env["BRIDGE_CLIENT_SECRET"],
            mcp_client_id=env.get("BRIDGE_MCP_CLIENT_ID", env["BRIDGE_CLIENT_ID"] + "-mcp"),
            issuer=issuer,
            internal_issuer=env.get("OIDC_INTERNAL_ISSUER", issuer).rstrip("/"),
            origin=env.get("EXTENSION_ORIGIN", "http://localhost:3012").rstrip("/"),
            state_dir=Path(env.get("EVAL_STATE_DIR", "/data")),
            agent_url=env.get("AGENT_URL", "http://cbi-gateway:3007").rstrip("/"),
            max_runs=max(1, int(env.get("EVAL_MAX_RUNS", "2"))),
            concurrency=max(1, min(int(env.get("EVAL_CONCURRENCY", "2")), 8)),
            run_retention=max(5, int(env.get("EVAL_RUN_RETENTION", "50"))),
            judge_model=env.get("JUDGE_MODEL", ""),
            mcp_callback_port=int(env.get("MCP_CALLBACK_PORT", "3010")),
            contract=contract_range(),
        )

    @property
    def secure(self):
        return self.origin.startswith("https://")
