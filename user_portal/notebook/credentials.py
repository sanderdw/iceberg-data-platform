"""Get the notebook owner's current token without exposing an OIDC refresh token."""

import os

import httpx


def access_token():
    endpoint = os.environ.get("ICEBERG_SESSION_TOKEN_URL")
    if not endpoint:
        return os.environ.get("ICEBERG_ACCESS_TOKEN")
    try:
        response = httpx.get(
            endpoint,
            headers={"Authorization": f"Bearer {os.environ['MARIMO_GATEWAY_TOKEN']}"},
            timeout=20, trust_env=False,
        )
        response.raise_for_status()
        return response.json()["access_token"]
    except (httpx.HTTPError, KeyError, ValueError):
        raise RuntimeError("Could not renew notebook access. Check your portal session and try again.") from None
