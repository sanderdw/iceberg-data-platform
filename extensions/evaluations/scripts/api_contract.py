"""Write contracts/evaluations-api/v1/openapi.yaml (JSON, which is valid YAML) from the implementation."""

import json
import os
import tempfile

from evaluations.config import ROOT, Settings

CONTRACT = ROOT / "contracts" / "evaluations-api" / "v1" / "openapi.yaml"


def render():
    from evaluations.app import create_app

    env = {"OIDC_ISSUER": "http://localhost:8080/realms/iceberg", "BRIDGE_CLIENT_ID": "ext-evaluations",
           "BRIDGE_CLIENT_SECRET": "unused", "EVAL_STATE_DIR": tempfile.mkdtemp()}
    app = create_app(Settings.from_env({**os.environ, **env}))
    return json.dumps(app.openapi(), indent=2) + "\n"


def main():
    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT.write_text(render())
    print(f"Wrote {CONTRACT}")


if __name__ == "__main__":
    main()
