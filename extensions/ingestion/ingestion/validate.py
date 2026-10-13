"""Declarative pipeline definitions: what a pipeline may say, checked on save and again before each run.

Pipelines are configuration, never code. Each source type accepts a fixed set of keys; anything
that would choose the destination, the catalog or local paths is refused. Secret values never
appear in a definition: a definition refers to them as "$secret:<name>", and they are set apart,
write-only. Sources on loopback, private or link-local addresses are refused unless the platform
administrator sets ING_ALLOW_PRIVATE_SOURCES=true (for laptop demos); the run checks every address
it resolves again.
"""

import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit

from .errors import IngestionError

SOURCE_TYPES = ("rest_api", "sql_database", "filesystem")
DISPOSITIONS = ("append", "replace", "merge")
SECRET_REF = re.compile(r"^\$secret:([a-z][a-z0-9_]{0,63})$")
SECRET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PIPELINE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
NAMESPACE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
RESOURCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,127}$")
HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}))*\.?$")
MAX_SOURCE = 64 * 1024
MAX_SECRET = 16 * 1024
# Both log scrubbers (secrets.MIN_SCRUB, runner/scrub.py) mask only values at least this long.
MIN_SECRET = 4
PAGINATORS = {
    "json_link": {"next_url_path"},
    "header_link": {"links_next_key"},
    "offset": {"limit", "offset", "offset_param", "limit_param", "total_path", "maximum_offset",
               "stop_after_empty_page", "has_more_path"},
    "page_number": {"base_page", "page", "page_param", "total_path", "maximum_page", "stop_after_empty_page",
                    "has_more_path"},
    "cursor": {"cursor_path", "cursor_param", "cursor_body_path", "stop_after_empty_page", "has_more_path"},
    "single_page": set(),
    "auto": set(),
}
AUTH = {
    "bearer": ({"type", "token"}, {"token"}),
    "api_key": ({"type", "name", "location", "api_key"}, {"api_key"}),
    "http_basic": ({"type", "username", "password"}, {"password"}),
}
TLS = ("disable", "require", "verify-full")
FORMATS = ("csv", "jsonl", "parquet")
# Host names that always mean this machine or its private network.
LOCAL_NAMES = ("localhost", "host.docker.internal", "gateway.docker.internal")
LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def fail(message, field=None):
    raise IngestionError(422, message, "invalid_pipeline", {"field": field} if field else None)


def allowed_keys(value, keys, field):
    if not isinstance(value, dict):
        fail(f"{field} must be an object.", field)
    extra = sorted(set(value) - set(keys))
    if extra:
        fail(f"{field} does not accept {', '.join(extra)}.", field)
    return value


def text(value, field, *, limit=500, pattern=None, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        fail(f"{field} must be text of at most {limit} characters.", field)
    if pattern is not None and not pattern.match(value):
        fail(f"{field} has characters that are not allowed.", field)
    return value


def scalar(value, field):
    if value is not None and not isinstance(value, str | int | float | bool):
        fail(f"{field} must be text, a number or true/false.", field)
    if isinstance(value, str) and len(value) > 2000:
        fail(f"{field} is too long.", field)
    return value


def secret_ref(value, field):
    """A secret position: it must refer to a secret, so the definition itself never holds one."""
    if not isinstance(value, str) or not SECRET_REF.match(value):
        fail(f"{field} must refer to a secret as \"$secret:<name>\"; set the value with set_pipeline_secrets.", field)
    return SECRET_REF.match(value)[1]


def refs(value):
    """Every secret a definition refers to, wherever it appears."""
    found = set()
    if isinstance(value, str) and SECRET_REF.match(value):
        found.add(SECRET_REF.match(value)[1])
    elif isinstance(value, dict):
        for item in value.values():
            found |= refs(item)
    elif isinstance(value, list):
        for item in value:
            found |= refs(item)
    return found


# Addresses

def is_private(address):
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def resolve_addresses(name):
    return {info[4][0] for info in socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)}


def check_host(host, field, allow_private, resolve=True):
    if not host or not isinstance(host, str):
        fail(f"{field} needs a host.", field)
    name = host.strip("[]").rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(name)
    except ValueError:
        literal = None
    if literal is None and not HOST.match(name):
        fail(f"{field} has an invalid host name.", field)
    if allow_private:
        return name
    if literal is not None:
        if is_private(name):
            fail(f"{field} points to a private, loopback or link-local address. A platform administrator allows "
                 "this only for demos, with ING_ALLOW_PRIVATE_SOURCES=true.", field)
        return name
    if name in LOCAL_NAMES or name.endswith(LOCAL_SUFFIXES) or "." not in name:
        fail(f"{field} names a host on this machine or a private network, which runs cannot reach.", field)
    if resolve:
        try:
            addresses = resolve_addresses(name)
        except OSError:
            # Unknown here does not mean unknown to the run; the run checks every address it resolves.
            return name
        if any(is_private(a.split("%")[0]) for a in addresses):
            fail(f"{field} resolves to a private, loopback or link-local address.", field)
    return name


def check_url(url, field, allow_private, schemes=("https", "http"), resolve=True):
    if not isinstance(url, str) or len(url) > 2000:
        fail(f"{field} must be a URL.", field)
    parts = urlsplit(url)
    if parts.scheme not in schemes:
        fail(f"{field} must start with {' or '.join(s + '://' for s in schemes)}.", field)
    if parts.username or parts.password:
        fail(f"{field} must not contain a user name or password; use a secret.", field)
    if parts.scheme in ("http", "https"):
        check_host(parts.hostname, field, allow_private, resolve)
        try:
            parts.port  # noqa: B018 - raises on an invalid port
        except ValueError:
            fail(f"{field} has an invalid port.", field)
    return url


# Source types

def rest_params(value, field):
    if value is None:
        return None
    if not isinstance(value, dict) or len(value) > 50:
        fail(f"{field} must be an object with at most 50 entries.", field)
    for key, item in value.items():
        text(key, f"{field} key", limit=200)
        if isinstance(item, dict):
            # Nested endpoints: {"type": "resolve", "resource": "parent", "field": "id"}, or an incremental marker.
            allowed_keys(item, {"type", "resource", "field", "cursor_path", "initial_value", "end_value"},
                         f"{field}.{key}")
            if item.get("type") not in ("resolve", "incremental"):
                fail(f"{field}.{key} may only resolve a parent field or mark the incremental value.", f"{field}.{key}")
            for k, v in item.items():
                scalar(v, f"{field}.{key}.{k}")
        else:
            scalar(item, f"{field}.{key}")
    return value


def paginator(value, field):
    if value is None:
        return None
    if isinstance(value, str):
        value = {"type": value}
    kind = value.get("type") if isinstance(value, dict) else None
    if kind not in PAGINATORS:
        fail(f"{field}.type must be one of {', '.join(PAGINATORS)}.", field)
    allowed_keys(value, PAGINATORS[kind] | {"type"}, field)
    for key, item in value.items():
        scalar(item, f"{field}.{key}")
    return value


def rest_api(source, allow_private, resolve):
    allowed_keys(source, {"client", "resource_defaults", "resources"}, "source")
    client = allowed_keys(source.get("client"), {"base_url", "headers", "auth", "paginator"}, "source.client")
    check_url(client.get("base_url"), "source.client.base_url", allow_private, resolve=resolve)
    headers = client.get("headers")
    if headers is not None:
        if not isinstance(headers, dict) or len(headers) > 30:
            fail("source.client.headers must be an object with at most 30 entries.", "source.client.headers")
        for key, item in headers.items():
            text(key, "source.client.headers key", limit=100, pattern=re.compile(r"^[A-Za-z0-9-]+$"))
            if key.lower() in ("host", "content-length", "transfer-encoding", "connection"):
                fail(f"source.client.headers may not set {key}.", "source.client.headers")
            if not isinstance(item, str) or len(item) > 2000:
                fail("source.client.headers values must be text.", "source.client.headers")
    auth = client.get("auth")
    if auth is not None:
        kind = auth.get("type") if isinstance(auth, dict) else None
        if kind not in AUTH:
            fail(f"source.client.auth.type must be one of {', '.join(AUTH)}.", "source.client.auth")
        keys, secret_keys = AUTH[kind]
        allowed_keys(auth, keys, "source.client.auth")
        for key in secret_keys:
            secret_ref(auth.get(key), f"source.client.auth.{key}")
        if kind == "api_key":
            text(auth.get("name"), "source.client.auth.name", limit=100)
            if auth.get("location", "header") not in ("header", "query"):
                fail("source.client.auth.location must be header or query.", "source.client.auth.location")
        if kind == "http_basic":
            text(auth.get("username"), "source.client.auth.username", limit=200)
    paginator(client.get("paginator"), "source.client.paginator")
    defaults = source.get("resource_defaults")
    if defaults is not None:
        allowed_keys(defaults, {"endpoint", "max_table_nesting"}, "source.resource_defaults")
        if defaults.get("endpoint") is not None:
            endpoint(defaults["endpoint"], "source.resource_defaults.endpoint", partial=True)
    resources = source.get("resources")
    if not isinstance(resources, list) or not 1 <= len(resources) <= 50:
        fail("source.resources must list 1 to 50 endpoints.", "source.resources")
    names = []
    for i, resource in enumerate(resources):
        field = f"source.resources[{i}]"
        allowed_keys(resource, {"name", "endpoint", "include_from_parent", "max_table_nesting", "table_name"}, field)
        names.append(text(resource.get("name"), f"{field}.name", limit=64, pattern=RESOURCE))
        if "table_name" in resource:
            text(resource["table_name"], f"{field}.table_name", limit=64, pattern=RESOURCE)
        endpoint(resource.get("endpoint"), f"{field}.endpoint")
        parents = resource.get("include_from_parent")
        if parents is not None and (not isinstance(parents, list) or
                                    not all(isinstance(p, str) and COLUMN.match(p) for p in parents)):
            fail(f"{field}.include_from_parent must list field names.", field)
        nesting = resource.get("max_table_nesting")
        if nesting is not None and (not isinstance(nesting, int) or not 0 <= nesting <= 10):
            fail(f"{field}.max_table_nesting must be 0 to 10.", field)
    if len(set(names)) != len(names):
        fail("source.resources names must be unique.", "source.resources")
    return names


def endpoint(value, field, partial=False):
    if isinstance(value, str) and not partial:
        value = {"path": value}
    allowed_keys(value, {"path", "method", "params", "json", "data_selector", "paginator", "incremental"}, field)
    path = value.get("path")
    if path is not None or not partial:
        text(path, f"{field}.path", limit=1000)
        # An absolute URL here would make dlt ignore base_url, and with it the address checks.
        if "://" in path or path.startswith("//") or "\\" in path:
            fail(f"{field}.path must be a path below base_url, not a URL.", f"{field}.path")
    if value.get("method", "GET") not in ("GET", "POST"):
        fail(f"{field}.method must be GET or POST.", f"{field}.method")
    rest_params(value.get("params"), f"{field}.params")
    rest_params(value.get("json"), f"{field}.json")
    if value.get("data_selector") is not None:
        text(value["data_selector"], f"{field}.data_selector", limit=200)
    paginator(value.get("paginator"), f"{field}.paginator")
    incremental = value.get("incremental")
    if incremental is not None:
        allowed_keys(incremental, {"cursor_path", "start_param", "end_param", "initial_value", "end_value"},
                     f"{field}.incremental")
        text(incremental.get("cursor_path"), f"{field}.incremental.cursor_path", limit=200)
        for key, item in incremental.items():
            scalar(item, f"{field}.incremental.{key}")


def sql_database(source, allow_private, resolve):
    allowed_keys(source, {"dialect", "host", "port", "database", "username", "password", "tls", "schema",
                          "chunk_size", "tables"}, "source")
    if source.get("dialect") not in ("postgresql", "mysql"):
        fail("source.dialect must be postgresql or mysql (MySQL and MariaDB).", "source.dialect")
    check_host(source.get("host"), "source.host", allow_private, resolve)
    port = source.get("port", 5432 if source["dialect"] == "postgresql" else 3306)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        fail("source.port must be a port number.", "source.port")
    text(source.get("database"), "source.database", limit=128, pattern=IDENTIFIER)
    text(source.get("username"), "source.username", limit=128, pattern=re.compile(r"^[A-Za-z0-9_.@-]+$"))
    if source.get("password") is not None:
        secret_ref(source["password"], "source.password")
    if source.get("tls", "verify-full") not in TLS:
        fail(f"source.tls must be one of {', '.join(TLS)}.", "source.tls")
    if source.get("schema") is not None:
        text(source["schema"], "source.schema", limit=128, pattern=IDENTIFIER)
    chunk = source.get("chunk_size")
    if chunk is not None and (not isinstance(chunk, int) or not 100 <= chunk <= 100_000):
        fail("source.chunk_size must be 100 to 100000.", "source.chunk_size")
    tables = source.get("tables")
    if not isinstance(tables, list) or not 1 <= len(tables) <= 100:
        fail("source.tables must list 1 to 100 tables.", "source.tables")
    names = []
    for i, table in enumerate(tables):
        field = f"source.tables[{i}]"
        if isinstance(table, str):
            table = tables[i] = {"name": table}
        allowed_keys(table, {"name", "incremental"}, field)
        names.append(text(table.get("name"), f"{field}.name", limit=64, pattern=RESOURCE))
        incremental = table.get("incremental")
        if incremental is not None:
            allowed_keys(incremental, {"cursor", "initial_value"}, f"{field}.incremental")
            text(incremental.get("cursor"), f"{field}.incremental.cursor", limit=128, pattern=COLUMN)
            scalar(incremental.get("initial_value"), f"{field}.incremental.initial_value")
    if len(set(names)) != len(names):
        fail("source.tables must be unique.", "source.tables")
    return names


def filesystem(source, allow_private, resolve):
    allowed_keys(source, {"url", "format", "table", "file_glob", "s3", "incremental", "csv"}, "source")
    url = check_url(source.get("url"), "source.url", allow_private, ("https", "http", "s3"), resolve)
    if source.get("format") not in FORMATS:
        fail(f"source.format must be one of {', '.join(FORMATS)}.", "source.format")
    name = text(source.get("table"), "source.table", limit=64, pattern=RESOURCE)
    s3 = url.startswith("s3://")
    if source.get("file_glob") is not None:
        if not s3:
            fail("source.file_glob is for s3:// locations; an https:// URL names one file.", "source.file_glob")
        glob = text(source["file_glob"], "source.file_glob", limit=200, pattern=re.compile(r"^[A-Za-z0-9_.*?/\[\]-]+$"))
        if ".." in glob or glob.startswith("/"):
            fail("source.file_glob must stay below the location.", "source.file_glob")
    if source.get("s3") is not None:
        if not s3:
            fail("source.s3 is for s3:// locations.", "source.s3")
        keys = allowed_keys(source["s3"], {"endpoint_url", "region", "access_key_id", "secret_access_key"},
                            "source.s3")
        if keys.get("endpoint_url") is not None:
            check_url(keys["endpoint_url"], "source.s3.endpoint_url", allow_private, resolve=resolve)
        if keys.get("region") is not None:
            text(keys["region"], "source.s3.region", limit=64, pattern=re.compile(r"^[a-z0-9-]+$"))
        if keys.get("access_key_id") is not None:
            text(keys["access_key_id"], "source.s3.access_key_id", limit=256)
            secret_ref(keys.get("secret_access_key"), "source.s3.secret_access_key")
    elif s3:
        fail("source.s3 needs the storage's endpoint_url or region and keys.", "source.s3")
    if source.get("incremental") is not None:
        if not isinstance(source["incremental"], bool):
            fail("source.incremental must be true or false.", "source.incremental")
        if source["incremental"] and not s3:
            fail("source.incremental needs an s3:// location with file dates.", "source.incremental")
    csv = source.get("csv")
    if csv is not None:
        allowed_keys(csv, {"delimiter"}, "source.csv")
        if not isinstance(csv.get("delimiter", ","), str) or len(csv.get("delimiter", ",")) != 1:
            fail("source.csv.delimiter must be one character.", "source.csv.delimiter")
    return [name]


CHECKS = {"rest_api": rest_api, "sql_database": sql_database, "filesystem": filesystem}


def endpoint_identity(source_type, source):
    """Where a pipeline connects. Its secrets belong to this place: change it and they must be set again."""
    if source_type == "rest_api":
        return urlsplit(source["client"]["base_url"]).netloc.lower()
    if source_type == "sql_database":
        return f"{source['dialect']}://{source['host'].lower()}:{source.get('port', '')}"
    s3 = source.get("s3") or {}
    return f"{urlsplit(source['url']).netloc.lower()}|{s3.get('endpoint_url', '')}|{s3.get('access_key_id', '')}"


def resources_hints(value, names):
    """Write disposition, primary key and partitioning per resource; every resource gets one."""
    value = value or {}
    if not isinstance(value, dict):
        fail("resources must be an object keyed by resource name.", "resources")
    unknown = sorted(set(value) - set(names))
    if unknown:
        fail(f"resources names {', '.join(unknown)}, which the source does not have. It has {', '.join(names)}.",
             "resources")
    hints = {}
    for name in names:
        hint = value.get(name) or {}
        field = f"resources.{name}"
        allowed_keys(hint, {"writeDisposition", "primaryKey", "partition"}, field)
        disposition = hint.get("writeDisposition", "append")
        if disposition not in DISPOSITIONS:
            fail(f"{field}.writeDisposition must be append, replace or merge.", field)
        key = hint.get("primaryKey") or []
        if isinstance(key, str):
            key = [key]
        partition = hint.get("partition") or []
        for column in [*key, *partition]:
            text(column, f"{field} column", limit=128, pattern=COLUMN)
        if len(key) > 16 or len(partition) > 4:
            fail(f"{field} allows at most 16 key and 4 partition columns.", field)
        if disposition == "merge" and not key:
            fail(f"{field}: merge updates rows by their primary key, so it needs primaryKey.", field)
        hints[name] = {"writeDisposition": disposition, "primaryKey": list(key), "partition": list(partition)}
    return hints


def definition(source_type, source, resources, *, allow_private=False, resolve=True):
    """A checked copy of a pipeline's source and resource hints, and the secrets it needs."""
    if source_type not in SOURCE_TYPES:
        fail(f"sourceType must be one of {', '.join(SOURCE_TYPES)}.", "sourceType")
    try:
        encoded = json.dumps(source)
    except (TypeError, ValueError):
        fail("source must be JSON.", "source")
    if len(encoded) > MAX_SOURCE:
        fail("source is larger than 64 KiB.", "source")
    source = json.loads(encoded)
    names = CHECKS[source_type](source, allow_private, resolve)
    return {"source": source, "resources": resources_hints(resources, names), "secrets": sorted(refs(source)),
            "resourceNames": names, "endpoint": endpoint_identity(source_type, source)}


def secret_values(values, needed=None):
    if not isinstance(values, dict) or not values or len(values) > 20:
        fail("Give 1 to 20 secrets as {name: value}.", "secrets")
    for name, value in values.items():
        if not SECRET_NAME.match(name):
            fail("Secret names are lowercase letters, digits and _.", "secrets")
        if value is not None and (not isinstance(value, str) or not MIN_SECRET <= len(value) <= MAX_SECRET):
            fail(f"Secret {name} must be text of 4 characters to 16 KiB, or null to remove it.", f"secrets.{name}")
        if needed is not None and value is not None and name not in needed:
            fail(f"The pipeline does not refer to $secret:{name}.", f"secrets.{name}")
    return values


def pipeline_name(name):
    return text(name, "name", limit=64, pattern=PIPELINE_NAME)


def namespace(name):
    return text(name, "namespace", limit=64, pattern=NAMESPACE)
