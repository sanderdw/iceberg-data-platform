"""Remove a deleted project's producer metadata from the tables it built.

Every write run stamps the table-property conventions (contracts/bridge/v1/table-properties.md) on
the tables it materialized. When a project is deleted, those properties would keep pointing at a
project that no longer exists. This removes them with a metadata-only Iceberg REST commit, as the
team's automation principal of each environment. Data, table comments and column docs stay.
"""

from urllib.parse import quote

import httpx

PREFIX = "iceberg-data-platform."
GROUPS = ("producer", "lineage", "quality")


def stamped_tables(runs):
    """Per environment, the (warehouse, namespace parts, name) of every table a write run published."""
    tables = {}
    for run in runs:
        summary = run["summary"] or {}
        published = (summary.get("published") or {}).get("tables") or []
        for warehouse in (summary.get("catalogs") or {}).values():
            for table in published:
                *namespace, name = table.split(".")
                tables.setdefault(run["environment"], set()).add((warehouse, tuple(namespace), name))
    return tables


def ours(key):
    return any(key == PREFIX + group or key.startswith(f"{PREFIX}{group}.") for group in GROUPS)


class Cleaner:
    def __init__(self, http=None):
        self.http = http or httpx.AsyncClient(timeout=30, trust_env=False)
        self.prefixes = {}

    async def close(self):
        await self.http.aclose()

    async def prefix(self, catalog_uri, warehouse, headers):
        if warehouse not in self.prefixes:
            response = await self.http.get(f"{catalog_uri}/v1/config", params={"warehouse": warehouse},
                                           headers=headers)
            response.raise_for_status()
            self.prefixes[warehouse] = response.json().get("overrides", {}).get("prefix", warehouse)
        return self.prefixes[warehouse]

    async def strip(self, catalog_uri, token, project_id, warehouse, namespace, name):
        """'removed', 'missing' or 'other' (another producer wrote the table since)."""
        headers = {"Authorization": f"Bearer {token}"}
        prefix = await self.prefix(catalog_uri, warehouse, headers)
        url = f"{catalog_uri}/v1/{quote(prefix, safe='')}/namespaces/{quote(chr(31).join(namespace), safe='')}" \
              f"/tables/{quote(name, safe='')}"
        response = await self.http.get(url, headers=headers)
        if response.status_code == 404:
            return "missing"
        response.raise_for_status()
        metadata = response.json()["metadata"]
        properties = metadata.get("properties") or {}
        if f"/projects/{project_id}/" not in properties.get(PREFIX + "producer.url", ""):
            return "other"
        removals = sorted(key for key in properties if ours(key))
        response = await self.http.post(url, headers=headers, json={
            "requirements": [{"type": "assert-table-uuid", "uuid": metadata["table-uuid"]}],
            "updates": [{"action": "remove-properties", "removals": removals}],
        })
        response.raise_for_status()
        return "removed"
