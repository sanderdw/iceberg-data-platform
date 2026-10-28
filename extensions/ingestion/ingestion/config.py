"""Settings from the environment: the Bridge handshake file plus this extension's own options."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.1.2"
ENVIRONMENTS = ("development", "acceptance", "production")


def contract_range():
    try:
        return tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["iceberg-ingestion"]["bridge-contract"]
    except (OSError, KeyError):
        return ">=0.1,<0.2"


def flag(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


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
    # AES-GCM key for source secrets (32 bytes, hex). scripts/setup.py generates it.
    secret_key: str = field(repr=False, default="")
    max_runs: int = 4
    # One team cannot take every slot; runs of one team queue behind each other beyond this.
    max_runs_per_team: int = 2
    # Finished runs per pipeline whose logs are kept.
    run_retention: int = 30
    # Runs end 15 minutes before their catalog token expires; this only lowers that further.
    run_timeout: int = 43200
    # Laptop demos: allow sources on private addresses, such as a database on the same machine.
    allow_private: bool = False
    mcp_callback_port: int = 3010
    contract: str = ">=0.1,<0.2"

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        issuer = env["OIDC_ISSUER"].rstrip("/")
        return cls(
            bridge_url=env.get("BRIDGE_URL", "http://bridge:3005").rstrip("/"),
            extension_id=env.get("BRIDGE_EXTENSION_ID", "ingestion"),
            client_id=env["BRIDGE_CLIENT_ID"],
            client_secret=env["BRIDGE_CLIENT_SECRET"],
            mcp_client_id=env.get("BRIDGE_MCP_CLIENT_ID", env["BRIDGE_CLIENT_ID"] + "-mcp"),
            issuer=issuer,
            internal_issuer=env.get("OIDC_INTERNAL_ISSUER", issuer).rstrip("/"),
            origin=env.get("EXTENSION_ORIGIN", "http://localhost:3008").rstrip("/"),
            state_dir=Path(env.get("ING_STATE_DIR", "/data")),
            launcher_url=env.get("LAUNCHER_URL", "http://ing-launcher:3018").rstrip("/"),
            launcher_token=env.get("LAUNCHER_TOKEN", ""),
            secret_key=env.get("ING_SECRET_KEY", ""),
            max_runs=max(1, int(env.get("ING_MAX_CONCURRENT_RUNS", "4"))),
            max_runs_per_team=max(1, int(env.get("ING_MAX_RUNS_PER_TEAM", "2"))),
            run_retention=max(5, int(env.get("ING_RUN_RETENTION", "30"))),
            # Each run is also limited by its catalog token, whose lifetime the platform sets (runs.time_limit).
            run_timeout=max(60, min(int(env.get("ING_RUN_TIMEOUT_SECONDS", "43200")), 43200)),
            allow_private=flag(env.get("ING_ALLOW_PRIVATE_SOURCES", "")),
            mcp_callback_port=int(env.get("MCP_CALLBACK_PORT", "3010")),
            contract=contract_range(),
        )

    @property
    def secure(self):
        return self.origin.startswith("https://")
