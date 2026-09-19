"""Get the notebook owner's current token without exposing an OIDC refresh token."""

import os
from pathlib import Path

import httpx


def access_token():
    endpoint = os.environ.get("ICEBERG_SESSION_TOKEN_URL")
    if not endpoint:
        return os.environ.get("ICEBERG_ACCESS_TOKEN")
    try:
        # start.py moves this credential into marimo's private password file before exec.
        token_file = Path("/tmp/marimo-token")
        token = token_file.read_text() if token_file.exists() else os.environ["MARIMO_GATEWAY_TOKEN"]
        response = httpx.get(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20, trust_env=False,
        )
        response.raise_for_status()
        return response.json()["access_token"]
    except (httpx.HTTPError, KeyError, ValueError, OSError):
        raise RuntimeError("Could not renew notebook access. Check your portal session and try again.") from None
