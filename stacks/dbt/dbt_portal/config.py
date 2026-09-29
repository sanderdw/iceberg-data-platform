"""Settings from the environment: the Bridge handshake file plus this stack's own options."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.1.0"
ENVIRONMENTS = ("development", "acceptance", "production")


def contract_range():
    try:
        return tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["iceberg-dbt"]["bridge-contract"]
    except (OSError, KeyError):
        return ">=1.0,<2"


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
    launcher_url: str
    launcher_token: str = field(repr=False)
    user_portal_url: str = ""
    max_runs: int = 4
    run_timeout: int = 2700
    mcp_callback_port: int = 3010
    contract: str = ">=1.0,<2"

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        issuer = env["OIDC_ISSUER"].rstrip("/")
        return cls(
            bridge_url=env.get("BRIDGE_URL", "http://bridge:3005").rstrip("/"),
            extension_id=env.get("BRIDGE_EXTENSION_ID", "dbt"),
            client_id=env["BRIDGE_CLIENT_ID"],
            client_secret=env["BRIDGE_CLIENT_SECRET"],
            mcp_client_id=env.get("BRIDGE_MCP_CLIENT_ID", env["BRIDGE_CLIENT_ID"] + "-mcp"),
            issuer=issuer,
            internal_issuer=env.get("OIDC_INTERNAL_ISSUER", issuer).rstrip("/"),
            origin=env.get("EXTENSION_ORIGIN", "http://localhost:3004").rstrip("/"),
            state_dir=Path(env.get("DBT_STATE_DIR", "/data")),
            launcher_url=env.get("LAUNCHER_URL", "http://dbt-launcher:3006").rstrip("/"),
            launcher_token=env.get("LAUNCHER_TOKEN", ""),
            max_runs=int(env.get("MAX_CONCURRENT_RUNS", "4")),
            # Polaris tokens last one hour; a run must finish well within it.
            run_timeout=min(int(env.get("RUN_TIMEOUT_SECONDS", "2700")), 3000),
            mcp_callback_port=int(env.get("MCP_CALLBACK_PORT", "3010")),
            contract=contract_range(),
        )

    @property
    def secure(self):
        return self.origin.startswith("https://")
