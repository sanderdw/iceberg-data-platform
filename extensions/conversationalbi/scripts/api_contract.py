"""Write contracts/conversationalbi-api/v1/openapi.yaml (JSON, which is valid YAML) from the implementation."""

import json
import os

from conversationalbi.config import ROOT, Settings

CONTRACT = ROOT / "contracts" / "conversationalbi-api" / "v1" / "openapi.yaml"


def render():
    from conversationalbi.app import create_app

    env = {"OIDC_ISSUER": "http://localhost:8080/realms/iceberg", "BRIDGE_CLIENT_ID": "ext-conversationalbi",
           "BRIDGE_CLIENT_SECRET": "unused", "LLM_MODEL": "test:flights", "BI_ALLOW_TEST_MODEL": "1"}
    app = create_app(Settings.from_env({**os.environ, **env}))
    return json.dumps(app.openapi(), indent=2) + "\n"


def main():
    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT.write_text(render())
    print(f"Wrote {CONTRACT}")


if __name__ == "__main__":
    main()
