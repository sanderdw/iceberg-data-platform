"""Replication definitions: what a replication may say, checked on save and again before each run.

A replication is configuration, never code. Each source type accepts a fixed set of keys; anything
that would choose the destination, the catalog, local paths or an SSH tunnel is refused. Secret
values never appear in a definition: it refers to them as "$secret:<name>", and they are set apart,
write-only.

Sources are operational databases, which usually live on a private network. Loopback, link-local
and multicast addresses are always refused. Other private addresses are refused unless they fall in
a network the platform administrator lists in REP_ALLOWED_SOURCE_NETWORKS; REP_ALLOW_PRIVATE_SOURCES
lifts every address rule for laptop demos. A run never resolves the source itself: the gateway
resolves and checks it before each run and the launcher pins the checked address in the run
container (see runs.pin_source).
"""

import ipaddress
import json
import re
import socket

from .errors import ReplicationError

SOURCE_TYPES = ("postgres",)
# OLake's sync modes. "cdc" copies the existing rows first; "strict_cdc" only follows changes from now on.
MODES = ("cdc", "incremental", "full_refresh")
DELETE_MODES = ("pos", "dv")
FIRST_RUN = ("copy", "changes")
SECRET_REF = re.compile(r"^\$secret:([a-z][a-z0-9_]{0,63})$")
SECRET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
REPLICATION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
NAMESPACE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
# schema.table, as PostgreSQL names them without quotes.
TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}\.[A-Za-z_][A-Za-z0-9_$]{0,62}$")
DESTINATION_TABLE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
# OLake's partition expression: one or more "/{column,transform}" parts.
PARTITION = re.compile(r"^(/\{[A-Za-z_][A-Za-z0-9_]{0,127},(identity|year|month|day|hour|bucket\[\d{1,4}\]|"
                       r"truncate\[\d{1,4}\])\})+$")
USERNAME = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")
HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}))*\.?$")
TLS = ("disable", "require", "verify-ca", "verify-full")
MAX_SOURCE = 64 * 1024
MAX_SECRET = 16 * 1024
MAX_TABLES = 200
# Both log scrubbers (secrets.MIN_SCRUB, runner/scrub.py) mask only values at least this long.
MIN_SECRET = 4
LOCAL_NAMES = ("localhost", "host.docker.internal", "gateway.docker.internal")


def fail(message, field=None):
    raise ReplicationError(422, message, "invalid_replication", {"field": field} if field else None)


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


def integer(value, field, low, high, default):
    value = default if value is None else value
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        fail(f"{field} must be a whole number from {low} to {high}.", field)
    return value


def secret_ref(value, field):
    """A secret position: it must refer to a secret, so the definition itself never holds one."""
    if not isinstance(value, str) or not SECRET_REF.match(value):
        fail(f"{field} must refer to a secret as \"$secret:<name>\"; set the value with "
             "set_replication_secrets.", field)
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

def networks(value):
    """REP_ALLOWED_SOURCE_NETWORKS: comma-separated CIDRs, such as 10.20.0.0/16."""
    found = []
    for part in (value or "").split(","):
        if part.strip():
            try:
                found.append(ipaddress.ip_network(part.strip(), strict=False))
            except ValueError:
                raise ValueError(f"REP_ALLOWED_SOURCE_NETWORKS has an invalid network: {part.strip()}") from None
    return tuple(found)


def address_problem(address, allowed=(), allow_private=False):
    """Why a source address is refused, or None."""
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if allow_private:
        return None
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return "a loopback, link-local or reserved address"
    if not ip.is_global and not any(ip in network for network in allowed):
        return ("a private address outside the networks the platform administrator allows "
                "(REP_ALLOWED_SOURCE_NETWORKS)")
    return None


def resolve_addresses(name):
    return sorted({info[4][0] for info in socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)})


def check_host(host, field, allowed=(), allow_private=False):
    """A source host name or address, checked without resolving it."""
    if not host or not isinstance(host, str):
        fail(f"{field} needs a host.", field)
    name = host.strip("[]").rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(name)
    except ValueError:
        literal = None
    if literal is None and not HOST.match(name):
        fail(f"{field} has an invalid host name.", field)
    if literal is not None:
        problem = address_problem(name, allowed, allow_private)
        if problem:
            fail(f"{field} is {problem}.", field)
    elif not allow_private and (name in LOCAL_NAMES or name.endswith(".localhost")):
        fail(f"{field} names this machine, which runs cannot reach.", field)
    return name


def pin(host, allowed=(), allow_private=False):
    """The one address a run may use for the source host: resolved and checked now, then pinned in the run.

    A name is resolved here, in the gateway, so the run cannot be pointed elsewhere by DNS afterwards.
    """
    try:
        ipaddress.ip_address(host.strip("[]"))
        return None  # an address needs no pinning
    except ValueError:
        pass
    try:
        addresses = resolve_addresses(host)
    except OSError:
        raise ReplicationError(422, f"The source host {host} cannot be resolved.", "source_unresolved") from None
    for address in addresses:
        problem = address_problem(address, allowed, allow_private)
        if problem:
            raise ReplicationError(422, f"The source host {host} resolves to {problem}.", "source_refused")
    preferred = [a for a in addresses if ":" not in a] or addresses
    return preferred[0]


# Source types

def postgres(source, allowed, allow_private):
    allowed_keys(source, {"host", "port", "database", "username", "password", "tls", "slot", "publication",
                          "maxThreads"}, "source")
    check_host(source.get("host"), "source.host", allowed, allow_private)
    source["port"] = integer(source.get("port"), "source.port", 1, 65535, 5432)
    text(source.get("database"), "source.database", limit=63, pattern=IDENTIFIER)
    text(source.get("username"), "source.username", limit=128, pattern=USERNAME)
    secret_ref(source.get("password"), "source.password")
    source["tls"] = source.get("tls") or "verify-full"
    if source["tls"] not in TLS:
        fail(f"source.tls must be one of {', '.join(TLS)}.", "source.tls")
    for key in ("slot", "publication"):
        if source.get(key) is not None:
            text(source[key], f"source.{key}", limit=63, pattern=re.compile(r"^[a-z_][a-z0-9_]{0,62}$"))
    source["maxThreads"] = integer(source.get("maxThreads"), "source.maxThreads", 1, 16, 4)
    return source


CHECKS = {"postgres": postgres}


def endpoint_identity(source_type, source):
    """Where a replication connects. Its secrets belong to this place: change it and they must be set again."""
    return f"{source_type}://{str(source.get('username', '')).lower()}@{str(source['host']).lower()}:" \
           f"{source.get('port', '')}/{source.get('database', '')}"


def tables(value, source):
    """Per source table (schema.table): how it is kept in step, and where and how it lands."""
    value = {} if value is None else value
    if not isinstance(value, dict) or len(value) > MAX_TABLES:
        fail(f"tables must be an object with at most {MAX_TABLES} source tables, keyed by schema.table.", "tables")
    checked, destinations = {}, set()
    for name, spec in value.items():
        field = f"tables.{name}"
        text(name, "tables key", limit=127, pattern=TABLE)
        spec = allowed_keys(spec or {}, {"mode", "cursor", "partition", "table", "appendOnly"}, field)
        mode = spec.get("mode", "cdc")
        if mode not in MODES:
            fail(f"{field}.mode must be one of {', '.join(MODES)}.", field)
        if mode == "cdc" and not (source.get("slot") and source.get("publication")):
            fail("Change data capture needs source.slot and source.publication.", "source.slot")
        cursor = spec.get("cursor")
        if mode == "incremental":
            text(cursor, f"{field}.cursor", limit=128, pattern=COLUMN)
        elif cursor is not None:
            fail(f"{field}.cursor is only for incremental tables.", field)
        destination = spec.get("table") or name.split(".", 1)[1].lower()
        text(destination, f"{field}.table", limit=64, pattern=DESTINATION_TABLE)
        if destination in destinations:
            fail(f"Two source tables land in the same Iceberg table {destination}; give one a table name.", field)
        destinations.add(destination)
        if spec.get("partition") is not None:
            text(spec["partition"], f"{field}.partition", limit=300, pattern=PARTITION)
        append = spec.get("appendOnly", False)
        if not isinstance(append, bool):
            fail(f"{field}.appendOnly must be true or false.", field)
        checked[name] = {"mode": mode, "table": destination, **({"cursor": cursor} if cursor else {}),
                         **({"partition": spec["partition"]} if spec.get("partition") else {}),
                         "appendOnly": append}
    return checked


def options(value):
    value = allowed_keys(value or {}, {"deleteMode", "firstRun"}, "options")
    delete_mode = value.get("deleteMode", "pos")
    if delete_mode not in DELETE_MODES:
        # Equality deletes are never offered: PyIceberg and Polars refuse tables that have them.
        fail("options.deleteMode must be pos (positional deletes) or dv (deletion vectors, Iceberg v3).",
             "options.deleteMode")
    first_run = value.get("firstRun", "copy")
    if first_run not in FIRST_RUN:
        fail("options.firstRun must be copy (existing rows, then changes) or changes (only changes from now on).",
             "options.firstRun")
    return {"deleteMode": delete_mode, "firstRun": first_run}


def definition(source_type, source, table_specs, option_values=None, *, allowed=(), allow_private=False):
    """A checked copy of a replication's source, tables and options, and the secrets it needs."""
    if source_type not in SOURCE_TYPES:
        fail(f"sourceType must be one of {', '.join(SOURCE_TYPES)}.", "sourceType")
    try:
        encoded = json.dumps(source)
    except (TypeError, ValueError):
        fail("source must be JSON.", "source")
    if len(encoded) > MAX_SOURCE:
        fail("source is larger than 64 KiB.", "source")
    source = CHECKS[source_type](json.loads(encoded), allowed, allow_private)
    return {"source": source, "tables": tables(table_specs, source), "options": options(option_values),
            "secrets": sorted(refs(source)), "endpoint": endpoint_identity(source_type, source)}


def connection(source_type, source, *, allowed=(), allow_private=False):
    """Only the source part, for a source check before any table is chosen."""
    if source_type not in SOURCE_TYPES:
        fail(f"sourceType must be one of {', '.join(SOURCE_TYPES)}.", "sourceType")
    source = CHECKS[source_type](json.loads(json.dumps(source)), allowed, allow_private)
    return {"source": source, "secrets": sorted(refs(source)), "endpoint": endpoint_identity(source_type, source)}


def secret_values(values, needed=None):
    if not isinstance(values, dict) or not values or len(values) > 20:
        fail("Give 1 to 20 secrets as {name: value}.", "secrets")
    for name, value in values.items():
        if not SECRET_NAME.match(name):
            fail("Secret names are lowercase letters, digits and _.", "secrets")
        if value is not None and (not isinstance(value, str) or not MIN_SECRET <= len(value) <= MAX_SECRET):
            fail(f"Secret {name} must be text of 4 characters to 16 KiB, or null to remove it.", f"secrets.{name}")
        if needed is not None and value is not None and name not in needed:
            fail(f"The replication does not refer to $secret:{name}.", f"secrets.{name}")
    return values


def replication_name(name):
    return text(name, "name", limit=64, pattern=REPLICATION_NAME)


def namespace(name):
    return text(name, "namespace", limit=64, pattern=NAMESPACE)
