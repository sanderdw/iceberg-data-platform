"""Write contracts/maintenance-api/v1/openapi.yaml (JSON, which is valid YAML) from the implementation."""

import json
import os
import tempfile

from maintenance.config import ROOT, Settings

CONTRACT = ROOT / "contracts" / "maintenance-api" / "v1" / "openapi.yaml"


def render():
    from maintenance.app import create_app

    with tempfile.TemporaryDirectory() as state:
        env = {"OIDC_ISSUER": "http://localhost:8080/realms/iceberg", "BRIDGE_CLIENT_ID": "ext-maintenance",
               "BRIDGE_CLIENT_SECRET": "unused", "MNT_STATE_DIR": state, "LAUNCHER_TOKEN": "unused"}
        app = create_app(Settings.from_env({**os.environ, **env}))
        return json.dumps(app.openapi(), indent=2) + "\n"


def main():
    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT.write_text(render())
    print(f"Wrote {CONTRACT}")


if __name__ == "__main__":
    main()
