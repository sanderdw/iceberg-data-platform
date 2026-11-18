"""Settings from the environment: the Bridge handshake file plus this extension's own options."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.0.1"
ENVIRONMENTS = ("development", "acceptance", "production")


def contract_range():
    try:
        return tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["iceberg-maintenance"]["bridge-contract"]
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
    state_dir: Path
    launcher_url: str
    launcher_token: str = field(repr=False)
    max_runs: int = 2
    # Finished runs per database whose logs and results are kept.
    run_retention: int = 30
    # Runs end 15 minutes before their catalog token expires; this only lowers that further.
    run_timeout: int = 43200
    mcp_callback_port: int = 3010
    contract: str = ">=0.1,<0.2"

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        issuer = env["OIDC_ISSUER"].rstrip("/")
        return cls(
            bridge_url=env.get("BRIDGE_URL", "http://bridge:3005").rstrip("/"),
            extension_id=env.get("BRIDGE_EXTENSION_ID", "maintenance"),
            client_id=env["BRIDGE_CLIENT_ID"],
            client_secret=env["BRIDGE_CLIENT_SECRET"],
            mcp_client_id=env.get("BRIDGE_MCP_CLIENT_ID", env["BRIDGE_CLIENT_ID"] + "-mcp"),
            issuer=issuer,
            internal_issuer=env.get("OIDC_INTERNAL_ISSUER", issuer).rstrip("/"),
            origin=env.get("EXTENSION_ORIGIN", "http://localhost:3013").rstrip("/"),
            state_dir=Path(env.get("MNT_STATE_DIR", "/data")),
            launcher_url=env.get("LAUNCHER_URL", "http://mnt-launcher:3019").rstrip("/"),
            launcher_token=env.get("LAUNCHER_TOKEN", ""),
            max_runs=max(1, int(env.get("MNT_MAX_CONCURRENT_RUNS", "2"))),
            run_retention=max(5, int(env.get("MNT_RUN_RETENTION", "30"))),
            run_timeout=max(60, min(int(env.get("MNT_RUN_TIMEOUT_SECONDS", "43200")), 43200)),
            mcp_callback_port=int(env.get("MCP_CALLBACK_PORT", "3010")),
            contract=contract_range(),
        )

    @property
    def secure(self):
        return self.origin.startswith("https://")
