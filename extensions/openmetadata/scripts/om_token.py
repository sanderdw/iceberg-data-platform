"""Write the ingestion-bot's JWT of the local OpenMetadata (compose.openmetadata.yaml) to .env.

For demos and CI only: it signs in as the quickstart administrator (admin@open-metadata.org /
admin). With your own OpenMetadata, copy a bot's token from Settings > Bots instead.

  uv run python -m scripts.om_token [--url http://localhost:8585/api]
"""

import argparse
import base64
import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def call(url, token=None, body=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def bot_token(api, email="admin@open-metadata.org", password="admin"):
    login = call(f"{api}/v1/users/login",
                 body={"email": email, "password": base64.b64encode(password.encode()).decode()})
    admin = login["accessToken"]
    bot = call(f"{api}/v1/bots/name/ingestion-bot?fields=botUser", admin)
    mechanism = call(f"{api}/v1/users/auth-mechanism/{bot['botUser']['id']}", admin)
    return mechanism["config"]["JWTToken"]


def write(config, token):
    lines = config.read_text().splitlines() if config.exists() else []
    lines = [line for line in lines if not line.startswith("OM_JWT_TOKEN=")] + [f"OM_JWT_TOKEN={token}"]
    if config.exists():
        config.unlink()
    with os.fdopen(os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as file:
        file.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8585/api")
    args = parser.parse_args()
    write(ROOT / ".env", bot_token(args.url.rstrip("/")))
    print("OM_JWT_TOKEN written to .env. Recreate om-sync to use it: docker compose up -d om-sync")


if __name__ == "__main__":
    main()
