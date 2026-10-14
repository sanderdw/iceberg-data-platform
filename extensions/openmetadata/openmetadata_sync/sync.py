"""The reconcile loop: listed data shares on the platform → entities in OpenMetadata.

Every poll reads the cheap listing summary and fetches the listings whose version changed. Every
full sync fetches all of them again (conditionally, with their ETag) and publishes everything, so
schema and semantic model edits arrive and changes made in OpenMetadata to these entities heal.
After a complete pass, entities of this extension that no listing names any more are soft-deleted
(data products are deleted: OpenMetadata has no soft delete for them). A restart is one full push;
no state is stored.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime

from . import diagram, semantics
from . import mapping as m
from .errors import SyncError

LOG = logging.getLogger(__name__)


def ref(entity, type_):
    return {"id": entity["id"], "type": type_, "fullyQualifiedName": entity.get("fullyQualifiedName", "")}


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


class Syncer:
    def __init__(self, settings, bridge, om):
        self.settings = settings
        self.bridge = bridge
        self.om = om
        self.prefix = settings.prefix
        # Listing id → {version, etag, listing} of the last successful publish.
        self.cache = {}
        self.domain = None
        self.glossary = None
        self.classification = None
        self.services = {}
        self.teams = {}
        self.teams_forbidden = False
        self.status = {"state": "starting", "lastSyncAt": None, "lastFullSyncAt": None, "lastErrorCode": None,
                       "listings": 0, "tables": 0, "metrics": 0, "dataProducts": 0, "openMetadataVersion": None}

    # Shared entities, created once per full sync.

    async def ensure_domain(self):
        if self.domain is None:
            self.domain = (await self.om.put("domains", m.domain_request(self.settings.domain)))["fullyQualifiedName"]
        return self.domain

    async def ensure_glossary(self):
        if self.glossary is None:
            await self.ensure_domain()
            self.glossary = await self.om.put("glossaries", semantics.glossary_request(self.settings.domain))
        return self.glossary

    async def ensure_classification(self):
        if self.classification is None:
            self.classification = await self.om.put("classifications",
                                                    semantics.classification_request(self.settings.domain))
        return self.classification

    async def ensure_service(self, environment):
        if environment not in self.services:
            await self.ensure_domain()
            entity = await self.om.put("services/databaseServices",
                                       m.service_request(self.prefix, environment, self.settings.domain))
            self.services[environment] = entity["fullyQualifiedName"]
        return self.services[environment]

    async def owners(self, owner):
        """The owning team as OpenMetadata owner, or none when the bot may not create teams."""
        if self.teams_forbidden:
            return []
        if owner["team"] not in self.teams:
            try:
                entity = await self.om.put("teams", m.team_request(owner))
            except SyncError as exc:
                if exc.status != 403:
                    raise
                LOG.warning("The OpenMetadata bot may not create teams; listings are published without owners.")
                self.teams_forbidden = True
                return []
            self.teams[owner["team"]] = ref(entity, "team")
        return [self.teams[owner["team"]]]

    # One listing

    async def publish(self, listing):
        om, prefix = self.om, self.prefix
        owners = await self.owners(listing["owner"])
        await self.ensure_service(listing["database"]["environment"])
        domain = self.settings.domain
        await om.put("databases", m.database_request(prefix, listing, owners, domain))
        constraints = m.constraints(prefix, listing)
        terms, enriched, labels = semantics.enrich(prefix, listing, self.settings.domain)
        if labels:
            # Label tags first: terms, tables and columns carry them.
            await self.ensure_classification()
            for label in labels:
                await om.put("tags", semantics.tag_request(self.settings.domain, label))
        if terms:
            # Terms first: tables and columns are tagged with them.
            await self.ensure_glossary()
            for _, body in terms:
                await om.put("glossaryTerms", body)
        refs, schemas, deferred = {}, {}, []
        # OpenMetadata refuses a foreign key to a table it doesn't have yet: referred tables go
        # first, and a cycle or a self-reference gets its foreign keys in a second PUT.
        pending = [o for o in listing["objects"] if o["kind"] in ("table", "view")]
        targets = m.foreign_key_targets(prefix, listing, constraints)
        while pending:
            obj = next((o for o in pending if targets.get(m.key(o["namespace"], o["name"]), set()) <= refs.keys()),
                       pending[0])
            pending.remove(obj)
            namespace = tuple(obj["namespace"])
            if namespace not in schemas:
                # The schema joins the data product too, so its page shows where its tables are listed.
                schema = m.schema_request(prefix, listing, obj["namespace"], owners, domain)
                schemas[namespace] = ref(await om.put("databaseSchemas", schema), "databaseSchema")
            k = m.key(obj["namespace"], obj["name"])
            own = constraints.get(k, ())
            if not targets.get(k, set()) <= refs.keys():
                own = [c for c in own if c["constraintType"] != "FOREIGN_KEY"]
                deferred.append(obj)
            body = m.table_request(prefix, listing, obj, owners, own, enriched.get(k), domain)
            refs[k] = ref(await om.put("tables", body), "table")
        for obj in deferred:
            k = m.key(obj["namespace"], obj["name"])
            body = m.table_request(prefix, listing, obj, owners, constraints[k], enriched.get(k), domain)
            await om.put("tables", body)
        metrics = []
        for obj in (o for o in listing["objects"] if o["kind"] == "semantic-model"):
            for _, body, tables in m.metric_requests(prefix, listing, obj, self.settings.domain, owners):
                body["assets"] = [{"id": refs[k]["id"], "type": "table"} for k in tables]
                metric = ref(await om.put("metrics", body), "metric")
                metrics.append(metric)
                for k in tables:
                    await om.lineage(refs[k], metric)
        for upstream, downstream in m.lineage_inputs(prefix, listing):
            await om.lineage(refs[upstream], refs[downstream])
        product = m.data_product_request(prefix, listing, self.settings.domain, owners,
                                         diagram.markdown(prefix, listing, constraints), semantics.sections(listing))
        await om.put("dataProducts", product)
        wanted = {r["id"]: r for r in [*schemas.values(), *refs.values(), *metrics]}
        current = {a["id"]: a for a in await om.data_product_assets(product["name"])}
        await om.change_assets(product["name"], "remove", [a for id, a in current.items() if id not in wanted])
        await om.change_assets(product["name"], "add", [r for id, r in wanted.items() if id not in current])
        return len(refs), len(metrics)

    # Removal of what no listing names

    async def prune(self, listings):
        wanted, removed = m.wanted(self.prefix, listings), 0
        for environment in m.ENVIRONMENTS:
            service = m.service_name(self.prefix, environment)
            if await self.om.get("services/databaseServices", service) is None:
                continue
            for database in await self.om.list("databases", {"service": service}):
                if database["fullyQualifiedName"] not in wanted["databases"]:
                    removed += await self.om.delete("databases", database["fullyQualifiedName"])
                    continue
                for schema in await self.om.list("databaseSchemas", {"database": database["fullyQualifiedName"]}):
                    if schema["fullyQualifiedName"] not in wanted["schemas"]:
                        removed += await self.om.delete("databaseSchemas", schema["fullyQualifiedName"])
                        continue
                    for table in await self.om.list("tables", {"databaseSchema": schema["fullyQualifiedName"]}):
                        if table["fullyQualifiedName"] not in wanted["tables"]:
                            removed += await self.om.delete("tables", table["fullyQualifiedName"])
        for metric in await self.om.list("metrics"):
            if metric["name"].startswith(self.prefix + "__") and metric["name"] not in wanted["metrics"]:
                removed += await self.om.delete("metrics", metric["fullyQualifiedName"])
        glossary = await self.om.get("glossaries", m.clean(self.settings.domain))
        if glossary:
            # Terms of models that are no longer listed, with their tags on tables and columns.
            terms = semantics.wanted_terms(self.prefix, listings, self.settings.domain)
            root = m.quote(m.clean(self.settings.domain))
            ours = (f"{root}.{self.prefix}__", f'{root}."{self.prefix}__')
            for term in await self.om.list("glossaryTerms", {"glossary": glossary["id"]}):
                fqn = term["fullyQualifiedName"]
                if fqn.startswith(ours) and fqn not in terms:
                    removed += await self.om.delete("glossaryTerms", fqn, hard=True)
        classification = semantics.classification_name(self.settings.domain)
        if await self.om.get("classifications", classification):
            # The classification is this extension's own: a label no listed model uses goes, with its tags.
            labels = semantics.wanted_labels(self.prefix, listings, self.settings.domain)
            for label in await self.om.list("tags", {"parent": classification}):
                if label["fullyQualifiedName"] not in labels:
                    removed += await self.om.delete("tags", label["fullyQualifiedName"], hard=True)
        for product in await self.om.list("dataProducts"):
            if product["name"].startswith(f"{self.prefix}-share-") and product["name"] not in wanted["dataProducts"]:
                removed += await self.om.delete("dataProducts", product["fullyQualifiedName"], hard=True)
        return removed

    # The loop

    async def cycle(self, full=False):
        if full:
            self.domain, self.glossary, self.classification = None, None, None
            self.services, self.teams, self.teams_forbidden = {}, {}, False
        summaries = {s["id"]: s["version"] for s in await self.bridge.listings()}
        changed = bool(set(self.cache) - set(summaries))
        for id in list(self.cache):
            if id not in summaries:
                del self.cache[id]
        failures = []
        for id, version in summaries.items():
            cached = self.cache.get(id)
            if not full and cached and cached["version"] == version:
                continue
            try:
                listing, etag = await self.bridge.listing(id, cached["etag"] if cached else None)
            except SyncError as exc:
                if exc.status != 404:
                    raise
                # Revoked or unlisted since the summary; the next poll removes it.
                self.cache.pop(id, None)
                changed = True
                continue
            if listing is None:
                listing = cached["listing"]
            try:
                if full or not cached or listing != cached["listing"]:
                    await self.publish(listing)
                    changed = True
            except SyncError as exc:
                if exc.code == "catalog_unauthorized":
                    raise
                LOG.warning("Listing %s was not published: %s", id, exc)
                failures.append(exc.code)
                continue
            self.cache[id] = {"version": version, "etag": etag, "listing": listing}
        complete = not failures and set(self.cache) == set(summaries)
        if complete and (changed or full):
            removed = await self.prune([c["listing"] for c in self.cache.values()])
            if removed:
                LOG.info("Removed %d entities that are no longer listed.", removed)
        listings = [c["listing"] for c in self.cache.values()]
        self.status.update(
            listings=len(listings),
            tables=sum(o["kind"] in ("table", "view") for item in listings for o in item["objects"]),
            metrics=sum(len(m.metric_requests(self.prefix, item, o, "", []))
                        for item in listings for o in item["objects"] if o["kind"] == "semantic-model"),
            dataProducts=len(listings),
        )
        if failures:
            raise SyncError(502, "Some listings were not published.", failures[0])

    async def run(self):
        next_full = 0.0
        while True:
            full = time.monotonic() >= next_full
            self.status["state"] = "syncing"
            try:
                if full:
                    self.status["openMetadataVersion"] = await self.om.version()
                await self.cycle(full)
                self.status.update(state="ok", lastErrorCode=None, lastSyncAt=now())
                if full:
                    self.status["lastFullSyncAt"] = self.status["lastSyncAt"]
                    next_full = time.monotonic() + self.settings.full_sync_seconds
            except SyncError as exc:
                LOG.warning("Sync failed (%s): %s", exc.code, exc)
                self.status.update(state="error", lastErrorCode=exc.code)
            except Exception as exc:  # noqa: BLE001 - The loop survives; the cause is logged by type only.
                LOG.error("Sync failed: %s", type(exc).__name__)
                self.status.update(state="error", lastErrorCode="internal_error")
            await asyncio.sleep(self.settings.poll_seconds)
