"""Settings from the environment: the Bridge handshake file plus this extension's own options."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.1.2"


def contract_range():
    try:
        return tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["iceberg-openmetadata"]["bridge-contract"]
    except (OSError, KeyError):
        return ">=0.1.1,<0.2"


def flag(value, default=True):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def web_url(value):
    value = value.strip().rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        raise ValueError("OM_UI_URL is an http or https address, such as http://localhost:8585.")
    return value


@dataclass(frozen=True)
class Settings:
    bridge_url: str
    extension_id: str
    client_id: str
    client_secret: str = field(repr=False)
    issuer: str
    internal_issuer: str
    origin: str
    # The OpenMetadata API, such as http://openmetadata-server:8585/api.
    om_url: str
    # A bot's JWT (the ingestion-bot or a bot of its own). Never logged or returned.
    om_token: str = field(repr=False)
    # Prefix of everything this extension owns in OpenMetadata: services `<prefix>-<environment>`,
    # metrics `<prefix>__…` and data products `<prefix>-share-…`. One per platform installation.
    prefix: str = "iceberg"
    domain: str = "Iceberg Data Platform"
    poll_seconds: int = 60
    full_sync_seconds: int = 900
    verify_tls: bool = True
    contract: str = ">=0.1.1,<0.2"
    # Where people browse the catalog, such as http://localhost:8585. The portal's link opens it.
    ui_url: str = ""

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        issuer = env["OIDC_ISSUER"].rstrip("/")
        return cls(
            bridge_url=env.get("BRIDGE_URL", "http://bridge:3005").rstrip("/"),
            extension_id=env.get("BRIDGE_EXTENSION_ID", "openmetadata"),
            client_id=env["BRIDGE_CLIENT_ID"],
            client_secret=env["BRIDGE_CLIENT_SECRET"],
            issuer=issuer,
            internal_issuer=env.get("OIDC_INTERNAL_ISSUER", issuer).rstrip("/"),
            origin=env.get("EXTENSION_ORIGIN", "http://localhost:3011").rstrip("/"),
            om_url=env.get("OM_URL", "").rstrip("/"),
            om_token=env.get("OM_JWT_TOKEN", ""),
            prefix=env.get("OM_SERVICE_PREFIX", "") or "iceberg",
            domain=env.get("OM_DOMAIN", "") or "Iceberg Data Platform",
            poll_seconds=max(10, int(env.get("OM_POLL_SECONDS") or 60)),
            full_sync_seconds=max(60, int(env.get("OM_FULL_SYNC_SECONDS") or 900)),
            verify_tls=flag(env.get("OM_VERIFY_TLS")),
            contract=contract_range(),
            ui_url=web_url(env.get("OM_UI_URL", "")),
        )

    @property
    def configured(self):
        return bool(self.om_url and self.om_token)
