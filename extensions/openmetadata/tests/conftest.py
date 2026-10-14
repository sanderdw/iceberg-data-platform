"""In-process fakes of the platform Bridge and of OpenMetadata 2.0's REST API, over httpx.MockTransport.

The OpenMetadata fake enforces what the sync relies on: parents exist, fully qualified names are
built and quoted as OpenMetadata does, tags name live glossary terms or classification tags, PUT
creates or updates and restores a soft-deleted entity with the same id, soft delete cascades to
children, data products are always hard deleted, and the request schema enums of 2.0.5.
"""

import copy
import hashlib
import json
import re
import uuid
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest

from openmetadata_sync.bridge_client import BridgeClient
from openmetadata_sync.config import Settings
from openmetadata_sync.om_client import OpenMetadata
from openmetadata_sync.sync import Syncer

TOKEN = "om-bot-jwt-do-not-log"
DB = "db-" + "d" * 32
TEAM = "team-" + "e" * 32
SHARE = "share-" + "a" * 32
NAME = re.compile(r'^((?!::)[^>"\x00-\x1f])*$')
DATA_TYPES = {"BOOLEAN", "INT", "BIGINT", "FLOAT", "DOUBLE", "DECIMAL", "DATE", "TIME", "TIMESTAMP", "TIMESTAMPZ",
              "STRING", "UUID", "BYTES", "VARIANT", "GEOMETRY", "GEOGRAPHY", "STRUCT", "ARRAY", "MAP", "FIXED",
              "UNKNOWN"}
TABLE_TYPES = {"Regular", "External", "View", "Iceberg"}
METRIC_TYPES = {"COUNT", "SUM", "AVERAGE", "RATIO", "PERCENTAGE", "MIN", "MAX", "MEDIAN", "OTHER"}
PARENTS = {"databases": ("service", "services/databaseServices"),
           "databaseSchemas": ("database", "databases"), "tables": ("databaseSchema", "databaseSchemas")}


def escaped(entity):
    """OpenMetadata stores some characters of a description as HTML entities."""
    for item in [entity, *walk(entity.get("columns", []))]:
        if item.get("description"):
            item["description"] = item["description"].replace("+", "&#43;")
    return entity


def walk(columns):
    for c in columns:
        yield c
        yield from walk(c.get("children") or [])


def keep_descriptions(existing, entity):
    if existing.get("description"):
        entity["description"] = existing["description"]
    tags = list(existing.get("tags") or [])
    tags += [t for t in entity.get("tags") or [] if t["tagFQN"] not in {x["tagFQN"] for x in tags}]
    if tags:
        entity["tags"] = tags
    old = {c["name"]: c for c in existing.get("columns") or existing.get("children") or []}
    for c in entity.get("columns") or entity.get("children") or []:
        if c["name"] in old:
            keep_descriptions(old[c["name"]], c)


def quote(name):
    return f'"{name}"' if "." in name else name


class FakeOpenMetadata:
    def __init__(self):
        self.entities = {}  # collection -> fqn -> entity
        self.lineage = set()
        self.assets = {}  # data product name -> {id: ref}
        self.calls = []
        self.patches = []
        self.forbid = set()

    def error(self, status, message):
        return httpx.Response(status, json={"code": status, "message": message})

    def find_id(self, id):
        return next((e for c in self.entities.values() for e in c.values() if e["id"] == id), None)

    def handle(self, request):
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return self.error(401, "Not authorized")
        url = urlsplit(str(request.url))
        path = unquote(url.path.removeprefix("/api/v1"))
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, query, body))
        if path == "/system/version":
            return httpx.Response(200, json={"version": "2.0.5"})
        if path == "/lineage" and request.method == "PUT":
            edge = body["edge"]
            if not self.find_id(edge["fromEntity"]["id"]) or not self.find_id(edge["toEntity"]["id"]):
                return self.error(404, "entity not found")
            self.lineage.add((edge["fromEntity"]["id"], edge["toEntity"]["id"]))
            return httpx.Response(200)
        if match := re.fullmatch(r"/dataProducts/name/(.+)/assets", path):
            return httpx.Response(200, json={"data": list(self.assets.get(match[1], {}).values()),
                                             "paging": {"total": len(self.assets.get(match[1], {}))}})
        if match := re.fullmatch(r"/dataProducts/(.+)/assets/(add|remove)", path):
            product = self.entities.get("dataProducts", {}).get(quote(match[1]))
            if not product:
                return self.error(404, "data product not found")
            assets = self.assets.setdefault(match[1], {})
            for a in body["assets"]:
                if match[2] == "add":
                    if not self.find_id(a["id"]):
                        return self.error(404, "asset not found")
                    assets[a["id"]] = {"id": a["id"], "type": a["type"]}
                else:
                    assets.pop(a["id"], None)
            return httpx.Response(200, json={"status": "success"})
        if request.method == "PATCH":
            assert request.headers["content-type"] == "application/json-patch+json"
            collection, _, id = path.strip("/").rpartition("/")
            entity = next((e for e in self.entities.get(collection, {}).values() if e["id"] == id), None)
            if not entity:
                return self.error(404, "not found")
            if collection == "tables" and not entity.get("domains"):
                return self.error(400, "Domain cannot be empty when data products are provided.")
            for op in body:
                assert op["op"] in ("add", "replace") and op["path"].endswith("/description")
                target = entity
                for part in op["path"].strip("/").split("/")[:-1]:
                    target = target[int(part)] if part.isdigit() else target[part]
                target["description"] = escaped({"description": op["value"]})["description"]
            self.patches.append((collection, body))
            return httpx.Response(200, json=entity)
        for collection in ("services/databaseServices", "databaseSchemas", "databases", "tables", "metrics",
                           "dataProducts", "domains", "teams", "glossaries", "glossaryTerms", "classifications",
                           "tags"):
            if path == "/" + collection and request.method == "PUT":
                return self.put(collection, body)
            if path == "/" + collection and request.method == "GET":
                return self.list(collection, query)
            if path.startswith(f"/{collection}/name/"):
                fqn = path.removeprefix(f"/{collection}/name/")
                if request.method == "GET":
                    entity = self.entities.get(collection, {}).get(fqn)
                    if not entity or entity["deleted"]:
                        return self.error(404, "not found")
                    return httpx.Response(200, json=entity)
                if request.method == "DELETE":
                    return self.delete(collection, fqn, query)
        return self.error(404, f"no route {request.method} {path}")

    def put(self, collection, body):
        if collection in self.forbid:
            return self.error(403, "Principal ingestion-bot is not allowed")
        name = body.get("name", "")
        if not name or not NAME.match(name) or len(name) > 256:
            return self.error(400, f"invalid name {name!r}")
        fqn = quote(name)
        if collection in PARENTS:
            field, parent_collection = PARENTS[collection]
            parent = self.entities.get(parent_collection, {}).get(body[field])
            if not parent or parent["deleted"]:
                return self.error(404, f"{field} {body[field]} not found")
            fqn = f"{body[field]}.{quote(name)}"
        glossary_id = None
        if collection == "glossaryTerms":
            # A term sits under its parent term, or directly under its glossary.
            glossary = self.entities.get("glossaries", {}).get(quote(body["glossary"]))
            parent = self.entities["glossaryTerms"].get(body["parent"]) if body.get("parent") else glossary
            if not glossary or not parent or parent["deleted"]:
                return self.error(404, "glossary or parent term not found")
            assert body["description"]
            fqn, glossary_id = f"{parent['fullyQualifiedName']}.{quote(name)}", glossary["id"]
            # Term names are unique in the whole glossary, not only among siblings.
            if any(e["name"] == name and e["glossaryId"] == glossary_id and k != fqn
                   for k, e in self.entities.get("glossaryTerms", {}).items()):
                return self.error(400, f"A term with the name '{name}' already exists in the glossary.")
        if collection == "tags":
            classification = self.entities.get("classifications", {}).get(quote(body["classification"]))
            if not classification or classification["deleted"]:
                return self.error(404, "classification not found")
            assert body["description"]
            fqn = f"{classification['fullyQualifiedName']}.{quote(name)}"
        live = {"Glossary": set(self.live("glossaryTerms")), "Classification": set(self.live("tags"))}
        for item in [body, *walk(body.get("columns", []))]:
            for t in item.get("tags", []):
                assert t["source"] in live and t["labelType"] == "Automated"
                if t["tagFQN"] not in live[t["source"]]:
                    return self.error(404, f"{t['source'].lower()} tag {t['tagFQN']} not found")
        if collection == "services/databaseServices":
            assert body["serviceType"] == "CustomDatabase"
        if collection == "tables":
            assert body["tableType"] in TABLE_TYPES and body["columns"]
            self.check_columns(body["columns"])
            assert "viewDefinition" not in body and "schemaDefinition" not in body
            # OpenMetadata refuses a foreign key to a table it doesn't have yet.
            live = {fqn for fqn, e in self.entities.get("tables", {}).items() if not e["deleted"]}
            for c in body.get("tableConstraints", []):
                for referred in c.get("referredColumns", []):
                    if referred.rsplit(".", 1)[0] not in live:
                        return self.error(400, f"Table not found: {referred.rsplit('.', 1)[0]}")
        if collection == "metrics" and "metricType" in body:
            assert body["metricType"] in METRIC_TYPES
            for d in body.get("dimensions", []):
                assert d["type"] in ("CATEGORICAL", "TIME")
        if collection == "domains":
            assert body["domainType"] == "Source-aligned"
        if collection == "teams":
            assert body["teamType"] == "Group"
        if collection in ("dataProducts", "metrics", "services/databaseServices", "databases", "databaseSchemas",
                          "tables"):
            for domain in body.get("domains", []):
                if domain not in self.entities.get("domains", {}):
                    return self.error(404, f"domain {domain} not found")
        for owner in body.get("owners", []):
            if not self.find_id(owner["id"]):
                return self.error(404, "owner not found")
        existing = self.entities.setdefault(collection, {}).get(fqn)
        entity = {**escaped(copy.deepcopy(body)), "id": existing["id"] if existing else str(uuid.uuid4()),
                  "fullyQualifiedName": fqn, "deleted": False}
        if glossary_id:
            entity["glossaryId"] = glossary_id
        if existing:
            # A bot's PUT never replaces a description that is already set, and keeps the tags it
            # doesn't name, as in OpenMetadata.
            keep_descriptions(existing, entity)
        if existing and existing["deleted"]:
            # PUT restores a soft-deleted entity and its children, as OpenMetadata does.
            for children in self.entities.values():
                for key, child in children.items():
                    if key.startswith(fqn + "."):
                        child["deleted"] = False
        self.entities[collection][fqn] = entity
        return httpx.Response(200 if existing else 201, json=entity)

    def check_columns(self, columns):
        for c in columns:
            assert NAME.match(c["name"]) and c["dataType"] in DATA_TYPES, c
            if c["dataType"] == "ARRAY":
                assert c["arrayDataType"] in DATA_TYPES
            self.check_columns(c.get("children", []))

    def list(self, collection, query):
        items = [e for e in self.entities.get(collection, {}).values() if not e["deleted"]]
        for field in ("service", "database", "databaseSchema"):
            if field in query:
                items = [e for e in items if e.get(field) == query[field]]
        if "glossary" in query:
            items = [e for e in items if e.get("glossaryId") == query["glossary"]]
        if "parent" in query:
            items = [e for e in items if e["fullyQualifiedName"].startswith(quote(query["parent"]) + ".")]
        start = int(query.get("after", 0))
        limit = int(query.get("limit", 10))
        page = items[start:start + limit]
        after = str(start + limit) if start + limit < len(items) else None
        return httpx.Response(200, json={"data": page, "paging": {"after": after} if after else {}})

    def delete(self, collection, fqn, query):
        entity = self.entities.get(collection, {}).get(fqn)
        if not entity:
            return self.error(404, "not found")
        hard = collection == "dataProducts" or query.get("hardDelete") == "true"
        for children in self.entities.values():
            for key in list(children):
                own = key == fqn and children is self.entities[collection]
                if own or (key.startswith(fqn + ".") and query.get("recursive") == "true"):
                    if hard:
                        del children[key]
                    else:
                        children[key]["deleted"] = True
        if collection in ("glossaryTerms", "tags") and hard:
            # Deleting a term or tag removes it, and its children, from everything that carries it.
            gone = (fqn, fqn + ".")
            for entity in [*self.entities.get("tables", {}).values(), *self.entities.get("glossaryTerms", {}).values()]:
                for item in [entity, *walk(entity.get("columns", []))]:
                    if item.get("tags"):
                        item["tags"] = [t for t in item["tags"]
                                        if t["tagFQN"] != gone[0] and not t["tagFQN"].startswith(gone[1])]
        return httpx.Response(200, json={})

    def live(self, collection):
        return sorted(fqn for fqn, e in self.entities.get(collection, {}).items() if not e["deleted"])


class FakeBridge:
    def __init__(self):
        self.listings = {}
        self.version = "0.1.1"
        self.capabilities = ["user-context", "automation-principals", "automation-tokens", "shared-data", "listings"]
        self.refuse = None
        self.fetched = []

    def handle(self, request):
        url = urlsplit(str(request.url))
        if url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "bridge-service-token", "expires_in": 300})
        if url.path == "/bridge/v1":
            return httpx.Response(200, json={"contractVersion": self.version, "capabilities": self.capabilities})
        if request.headers.get("authorization") != "Bearer bridge-service-token":
            return httpx.Response(401, json={"error": "invalid", "code": "unauthorized"})
        if self.refuse:
            return httpx.Response(403, json={"error": "not a listing extension", "code": self.refuse})
        if url.path == "/bridge/v1/listings":
            return httpx.Response(200, json={"listings": [
                # As the core: the version covers the header and object names, not their schemas.
                {"id": id, "version": hashlib.sha256(json.dumps(
                    {**{k: v for k, v in item.items() if k != "objects"},
                     "objects": [[o["kind"], o["namespace"], o["name"]] for o in item["objects"]]},
                    sort_keys=True).encode()).hexdigest(),
                 "databaseId": item["database"]["id"], "environment": item["database"]["environment"]}
                for id, item in self.listings.items()]})
        id = url.path.rsplit("/", 1)[-1]
        if id not in self.listings:
            return httpx.Response(404, json={"error": "This listing does not exist.", "code": "not_found"})
        body = self.listings[id]
        etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() + '"'
        self.fetched.append((id, request.headers.get("if-none-match") == etag))
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(200, json=body, headers={"ETag": etag})


def column(id, name, type_, doc="", required=False, children=()):
    return {"id": id, "name": name, "type": type_, "required": required, "doc": doc, "children": list(children)}


def listing(id=SHARE, name="orders-product", objects=None, environment="development"):
    return {
        "id": id, "name": name, "description": "Orders of the webshop", "contact": "data@example.test",
        "expiresAt": None, "version": "v1",
        "owner": {"team": TEAM, "teamName": "data-team"},
        "database": {"id": DB, "name": "sales", "environment": environment},
        "objects": objects if objects is not None else [ORDERS, CUSTOMERS, REPORT, REVENUE],
        "unavailable": [],
    }


ORDERS = {
    "kind": "table", "namespace": ["sales"], "name": "orders", "uuid": "u1", "comment": "All orders",
    "columns": [
        column(1, "id", "long", "Order number", True),
        column(2, "customer_id", "long"),
        column(3, "amount", "decimal(10,2)"),
        column(4, "ordered_at", "timestamptz"),
        column(5, "address", "struct<city: string>", children=[column(6, "city", "string")]),
        column(7, "tags", "list<string>", children=[column(8, "element", "string")]),
        column(9, "lines", "list<struct<sku: string>>", children=[
            column(10, "element", "struct<sku: string>", children=[column(11, "sku", "string")])]),
    ],
    "partitionFields": [{"name": "ordered_at_day", "sourceColumn": "ordered_at", "transform": "day"}],
    "rowCount": 42, "updatedAt": 1700000000000,
    "producer": {"name": "dbt", "inputs": [{"database": DB, "namespace": ["sales"], "name": "customers"},
                                           {"database": DB, "namespace": ["raw"], "name": "hidden"}],
                 "inputsTruncated": False},
}
CUSTOMERS = {
    "kind": "table", "namespace": ["sales"], "name": "customers", "uuid": "u2", "comment": "",
    "columns": [column(1, "id", "long", required=True), column(2, "region", "string", "Sales region")],
    "partitionFields": [], "rowCount": None, "updatedAt": None, "producer": None,
}
REPORT = {"kind": "view", "namespace": ["sales", "eu"], "name": "report", "uuid": "u3", "comment": "Monthly",
          "columns": [column(1, "total", "decimal(12,2)")], "updatedAt": 5}
REVENUE = {
    "kind": "semantic-model", "namespace": ["sales"], "name": "revenue", "specVersion": "0.2.0", "entityVersion": "3",
    "models": [{
        "name": "Revenue", "description": "Webshop revenue", "aiContext": [],
        "datasets": [
            {"name": "ORDERS", "description": "", "source": "lakehouse.sales.orders",
             "table": {"namespace": ["sales"], "name": "orders"}, "primaryKey": ["id"], "aiContext": [],
             "fields": [{"name": "ordered_at", "label": "", "description": "Order time", "datatype": "DateTimeTz",
                         "dimension": True, "timeDimension": True, "synonyms": [], "aiContext": [],
                         "expressions": [{"dialect": "ANSI_SQL", "expression": "ordered_at"}]}]},
            {"name": "CUSTOMERS", "description": "", "source": "lakehouse.sales.customers",
             "table": {"namespace": ["sales"], "name": "customers"}, "primaryKey": ["id"], "aiContext": [],
             "fields": [{"name": "region", "label": "", "description": "", "datatype": "String", "dimension": True,
                         "timeDimension": False, "synonyms": [], "aiContext": [], "expressions": []}]},
        ],
        "relationships": [{"name": "orders_customers", "from": "ORDERS", "to": "CUSTOMERS",
                           "fromColumns": ["customer_id"], "toColumns": ["id"]}],
        "metrics": [
            {"name": "revenue", "description": "Sum of order amounts", "datatype": "Decimal", "synonyms": ["sales"],
             "aiContext": [], "expressions": [{"dialect": "ANSI_SQL", "expression": "SUM(ORDERS.amount)"}]},
            {"name": "average_order", "description": "", "datatype": "Decimal", "synonyms": [], "aiContext": [],
             "expressions": [{"dialect": "ANSI_SQL", "expression": "SUM(ORDERS.amount) / COUNT(*)"}]},
        ],
    }],
}


def settings(**extra):
    return Settings(**{"bridge_url": "http://bridge:3005", "extension_id": "openmetadata",
                       "client_id": "ext-openmetadata", "client_secret": "bridge-secret",
                       "issuer": "http://localhost:8080/realms/iceberg",
                       "internal_issuer": "http://keycloak:8080/realms/iceberg", "origin": "http://localhost:3011",
                       "om_url": "http://openmetadata-server:8585/api", "om_token": TOKEN, **extra})


@pytest.fixture
def world():
    om, bridge = FakeOpenMetadata(), FakeBridge()
    config = settings()
    om_client = OpenMetadata(config.om_url, config.om_token, http=httpx.AsyncClient(
        transport=httpx.MockTransport(om.handle)))
    bridge_client = BridgeClient(config, http=httpx.AsyncClient(transport=httpx.MockTransport(bridge.handle)))
    syncer = Syncer(config, bridge_client, om_client)

    class World:
        pass

    w = World()
    w.om, w.bridge, w.syncer, w.settings = om, bridge, syncer, config
    w.om_client, w.bridge_client = om_client, bridge_client
    return w
