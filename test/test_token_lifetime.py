"""One platform setting decides how long Polaris tokens and vended storage credentials last."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_tokens_and_storage_credentials_share_the_platform_lifetime():
    polaris = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]["polaris"]["environment"]
    assert polaris["polaris.authentication.token-broker.max-token-generation"] == "PT${POLARIS_TOKEN_LIFETIME:-3600}S"
    assert polaris['polaris.features."STORAGE_CREDENTIAL_DURATION_SECONDS"'] == "${POLARIS_TOKEN_LIFETIME:-3600}"
    assert "# POLARIS_TOKEN_LIFETIME=3600" in (ROOT / ".env.example").read_text()
