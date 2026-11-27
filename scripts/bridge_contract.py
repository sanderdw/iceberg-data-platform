"""Write contracts/bridge/v1/openapi.yaml from the bridge implementation.

Record every change in contracts/bridge/CHANGELOG.md and follow COMPATIBILITY.md.
"""

import json

import yaml

from server.bridge import CONTRACT, create_bridge_app


def render():
    app = create_bridge_app(env={"OIDC_ISSUER": "http://localhost:8080/realms/iceberg"})
    # Through JSON first, so the file holds only plain YAML types.
    schema = json.loads(json.dumps(app.openapi()))
    return yaml.safe_dump(schema, sort_keys=False, allow_unicode=True, width=120)


def main():
    CONTRACT.write_text(render())
    print(f"Wrote {CONTRACT}")


if __name__ == "__main__":
    main()
