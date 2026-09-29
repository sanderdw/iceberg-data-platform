"""Write contracts/bridge/v1/openapi.yaml from the bridge implementation.

The file is JSON, which is valid YAML, so it can be compared without a YAML parser.
Record every change in contracts/bridge/CHANGELOG.md and follow COMPATIBILITY.md.
"""

import json

from server.bridge import CONTRACT, create_bridge_app


def render():
    app = create_bridge_app(env={"OIDC_ISSUER": "http://localhost:8080/realms/iceberg"})
    return json.dumps(app.openapi(), indent=2, sort_keys=False) + "\n"


def main():
    CONTRACT.write_text(render())
    print(f"Wrote {CONTRACT}")


if __name__ == "__main__":
    main()
