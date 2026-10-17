"""Print a .env for the smoke tests against a chart installed with values-localhost.yaml.

With the Services port-forwarded to the Compose ports, the smokes run unchanged:

    python3 dev/kubernetes/local_env.py iceberg > .env

Reads the platform Secret with kubectl; prints credentials, so redirect it to a file.
"""

import base64
import json
import subprocess
import sys

namespace = sys.argv[1] if len(sys.argv) > 1 else "iceberg"


def secret(name):
    out = subprocess.run(["kubectl", "-n", namespace, "get", "secret", name, "-o", "json"],
                         check=True, capture_output=True, text=True).stdout
    return {key: base64.b64decode(value).decode() for key, value in json.loads(out)["data"].items()}


values = secret("iceberg-platform")
extensions = subprocess.run(["kubectl", "-n", namespace, "get", "deploy", "portal", "-o",
                             "jsonpath={.spec.template.spec.containers[0].env[?(@.name=='PLATFORM_EXTENSIONS')].value}"],
                            check=True, capture_output=True, text=True).stdout
values.update({
    "POLARIS_URL": "http://localhost:8181", "POLARIS_PUBLIC_URL": "http://localhost:8181",
    "POLARIS_CLIENT_ID": "portal-admin", "RUSTFS_ACCESS_KEY": "iceberg-local",
    "S3_ENDPOINT": "http://localhost:9000", "S3_ADMIN_ENDPOINT": "http://localhost:9000",
    "S3_INTERNAL_ENDPOINT": "http://rustfs:9000", "AWS_REGION": "us-east-1",
    "PORTAL_ORIGIN": "http://localhost:3000", "USER_ORIGIN": "http://localhost:3002",
    "KEYCLOAK_ORIGIN": "http://localhost:8080", "OIDC_ISSUER": "http://localhost:8080/realms/iceberg",
    "OIDC_INTERNAL_ISSUER": "http://localhost:8080/realms/iceberg", "PLATFORM_ADMIN_USERNAME": "platform-admin",
    "MCP_CALLBACK_PORT": "3010", "PLATFORM_EXTENSIONS": extensions,
})
print("\n".join(f"{key}={value}" for key, value in sorted(values.items())))
