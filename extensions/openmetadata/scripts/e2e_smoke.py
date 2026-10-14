"""Check a live installation: a listed data share appears in OpenMetadata as a data product.

The platform side (a table and a listed share of it) is prepared by the caller, such as the CI
workflow, which passes the share id. This script reads only OpenMetadata and the extension's status.

  E2E_SHARE_ID=share-... uv run python -m scripts.e2e_smoke
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent


def env():
    values = {}
    for line in (ROOT / ".env").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip()
    return values


def get(url, token=None):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def wait(check, what, seconds=240):
    deadline = time.monotonic() + seconds
    while True:
        try:
            result = check()
            if result:
                return result
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        if time.monotonic() > deadline:
            raise SystemExit(f"FAIL: {what}")
        time.sleep(5)


def main():
    config = env()
    api = os.environ.get("OM_PUBLIC_URL", "http://localhost:8585/api").rstrip("/")
    token = config["OM_JWT_TOKEN"]
    share = os.environ["E2E_SHARE_ID"]
    prefix = config.get("OM_SERVICE_PREFIX") or "iceberg"
    status = f"http://localhost:{config.get('OM_PORT', '3011')}/api/status"
    wait(lambda: get(status)["state"] == "ok", "the extension reports a successful sync")
    product = wait(lambda: get(f"{api}/v1/dataProducts/name/{quote(prefix + '-' + share, safe='')}", token),
                   "the data product appears in OpenMetadata")
    assets = get(f"{api}/v1/dataProducts/name/{quote(product['fullyQualifiedName'], safe='')}/assets?limit=100", token)
    tables = [a for a in assets["data"] if a["type"] == "table"]
    if not tables:
        raise SystemExit("FAIL: the data product has no tables")
    table = get(f"{api}/v1/tables/name/{quote(tables[0]['fullyQualifiedName'], safe='')}?fields=columns", token)
    if not table["columns"] or table.get("tableType") not in ("Iceberg", "View"):
        raise SystemExit("FAIL: the table has no columns")
    print(f"PASS: {product['displayName']} with {len(assets['data'])} assets; "
          f"{table['fullyQualifiedName']} has {len(table['columns'])} columns")


if __name__ == "__main__":
    main()
