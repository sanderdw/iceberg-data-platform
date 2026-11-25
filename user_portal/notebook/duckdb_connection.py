"""Native DuckDB on Iceberg with user-scoped Polaris credentials and internal S3 routing."""

import logging
import os
import threading
import time
import weakref
from urllib.parse import quote, urlsplit
from weakref import WeakKeyDictionary

import duckdb
import httpx

from user_portal.notebook import catalog_proxy
from user_portal.notebook.credentials import access_token

READ_ERROR = (
    "Could not connect to the Iceberg table. Check the selected table and your read permissions, "
    "and rebuild the notebook image if DuckDB extensions are missing."
)
WRITE_ERROR = (
    "Could not connect to the Iceberg catalog for writing. Check that your role in the active team "
    "allows writing, and rebuild the notebook image if DuckDB extensions are missing."
)
# Table property naming the example that created a table; only that example may replace it.
EXAMPLE_PROPERTY = "iceberg-data-platform.example"
# Per connection, track which table each currently installed secret can read.
# Weak keys release this bookkeeping when a notebook releases its connection.
_discovery_tables = WeakKeyDictionary()
# Per connection, the storage secrets that renew themselves before Polaris's credentials expire.
_renewals = WeakKeyDictionary()
# Polaris lists this in /v1/config `endpoints` when it vends credentials without a full loadTable.
CREDENTIALS_ENDPOINT = "GET /v1/{prefix}/namespaces/{namespace}/tables/{table}/credentials"
# Storage secrets are renewed this long before they expire; a failed renewal is tried again after RETRY.
RENEW_BEFORE_SECONDS = 300
RENEW_RETRY_SECONDS = 30
logger = logging.getLogger(__name__)
_clock = time.time
_Timer = threading.Timer


def _configure_marimo_discovery():
    # marimo's datasets panel has no mapping for DuckDB's VARIANT and logs a warning for
    # every such column. It shows the column as "unknown" either way, so say that up front.
    try:
        from marimo._data import get_datasets

        get_datasets._UNKNOWN_TYPES.add("variant")
        if not getattr(get_datasets.get_table_columns, "_scoped_iceberg_discovery", False):
            original = get_datasets.get_table_columns

            def scoped_columns(connection, table_name):
                tables = _discovery_tables.get(connection) if connection is not None else None
                if (tables is not None and table_name.startswith('"lakehouse".')
                        and table_name not in tables.values()):
                    # Catalog listing is allowed, but DESCRIBE can read manifests.
                    # Do not trigger uncredentialed S3 requests for unrelated tables.
                    return []
                return original(connection, table_name)

            scoped_columns._scoped_iceberg_discovery = True
            get_datasets.get_table_columns = scoped_columns
    except ImportError, AttributeError:
        pass  # Marimo is optional; this adapter targets the pinned notebook version.


_configure_marimo_discovery()


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def schema_reference(namespace):
    # DuckDB represents nested Iceberg namespaces as a single dotted schema name.
    return ".".join('"' + part.replace('"', '""') + '"' for part in ("lakehouse", ".".join(namespace)))


def table_reference(namespace, table):
    return schema_reference(namespace) + '."' + table.replace('"', '""') + '"'


def _sign_in(client):
    """The notebook owner's current catalog token, or a client-credentials token in password mode."""
    token = access_token()
    if token:
        return token
    response = client.post(
        os.environ["ICEBERG_TOKEN_URI"],
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["ICEBERG_CLIENT_ID"],
            "client_secret": os.environ["ICEBERG_CLIENT_SECRET"],
            "scope": "PRINCIPAL_ROLE:ALL",
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _proxy_token():
    with httpx.Client(timeout=30, trust_env=False) as client:
        return _sign_in(client)


def _catalog(client):
    """Authenticate this user and resolve the catalog's REST prefix."""
    endpoint = os.environ["ICEBERG_CATALOG_URI"].rstrip("/")
    warehouse = os.environ["ICEBERG_DATABASE"]
    token = _sign_in(client)
    client.headers.update({"Authorization": f"Bearer {token}"})
    response = client.get(f"{endpoint}/v1/config", params={"warehouse": warehouse})
    response.raise_for_status()
    config = response.json()
    properties = {**config.get("defaults", {}), **config.get("overrides", {})}
    prefix = properties.get("prefix", "")
    base = f"{endpoint}/v1" + (f"/{quote(prefix, safe='/')}" if prefix else "")
    return token, endpoint, warehouse, base, CREDENTIALS_ENDPOINT in config.get("endpoints", [])


def _table_url(base, namespace, table):
    return f"{base}/namespaces/{quote(chr(31).join(namespace), safe='')}/tables/{quote(table, safe='')}"


def _matching_credentials(storage_credentials, location):
    """The `storage-credentials` entry with the longest prefix of `location`, as Iceberg REST specifies."""
    matches = [c for c in storage_credentials or [] if location.startswith(c.get("prefix", "\0"))]
    return max(matches, key=lambda c: len(c["prefix"]))["config"] if matches else {}


def _vended(client, base, namespace, table, missing_ok=False):
    """Temporary S3 credentials and location that Polaris vends for one table, from a full loadTable."""
    response = client.get(
        _table_url(base, namespace, table), headers={"X-Iceberg-Access-Delegation": "vended-credentials"},
    )
    if response.status_code == 404 and missing_ok:
        return None
    response.raise_for_status()
    loaded = response.json()
    location = loaded["metadata"]["location"].rstrip("/") + "/"
    return {**loaded["config"], **_matching_credentials(loaded.get("storage-credentials"), location)}, location


def _credentials(client, base, namespace, table, location):
    """Only new S3 credentials for a table, from Polaris's credentials endpoint; {} if it vends none here."""
    response = client.get(
        _table_url(base, namespace, table) + "/credentials",
        headers={"X-Iceberg-Access-Delegation": "vended-credentials"},
    )
    response.raise_for_status()
    return _matching_credentials(response.json().get("storage-credentials"), location)


def _expires_at(storage):
    """When vended S3 credentials expire (`s3.session-token-expires-at-ms`), in epoch seconds; None if not stated."""
    expiries = [int(v) for k, v in storage.items()
                if k.startswith("s3.") and k.endswith("expires-at-ms") and str(v).isdigit()]
    return min(expiries) / 1000 if expiries else None


def _storage_secret(connection, storage, location, *, secret_name="table_storage"):
    s3 = urlsplit(os.environ["ICEBERG_S3_ENDPOINT"])
    if s3.scheme not in {"http", "https"} or not s3.netloc:
        raise ValueError("Invalid internal S3 endpoint")
    secret_identifier = '"' + secret_name.replace('"', '""') + '"'
    connection.execute(f"""
        CREATE OR REPLACE SECRET {secret_identifier} (
            TYPE s3,
            KEY_ID {sql_literal(storage["s3.access-key-id"])},
            SECRET {sql_literal(storage["s3.secret-access-key"])},
            SESSION_TOKEN {sql_literal(storage["s3.session-token"])},
            REGION {sql_literal(storage.get("s3.region", "us-east-1"))},
            ENDPOINT {sql_literal(s3.netloc)},
            URL_STYLE 'path', USE_SSL {str(s3.scheme == "https").lower()},
            SCOPE {sql_literal(location)}
        )
    """)


class _Renewal:
    """Re-creates a connection's storage secrets shortly before Polaris's credentials expire.

    A timer thread does it through its own cursor: temporary secrets belong to the database
    instance, so the connection and all its cursors see the new ones.
    """

    def __init__(self, connection):
        self.connection = weakref.ref(connection)
        self.lock = threading.Lock()
        self.secrets = {}  # name -> (namespace, table, location, expires_at, renew_at)
        self.timer = None

    def install(self, connection, name, namespace, table, storage, location):
        with self.lock:
            _storage_secret(connection, storage, location, secret_name=name)
            self._track(name, namespace, table, location, _expires_at(storage))

    def _track(self, name, namespace, table, location, expires_at):
        if expires_at is None:
            self.secrets.pop(name, None)  # Polaris did not say when they expire: nothing to plan.
        else:
            # Credentials vended this close to their expiry are renewed after a pause, not in a loop.
            renew_at = max(expires_at - RENEW_BEFORE_SECONDS, _clock() + RENEW_RETRY_SECONDS)
            self.secrets[name] = (tuple(namespace), table, location, expires_at, renew_at)
        self._schedule()

    def _schedule(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        if self.secrets:
            due = min(entry[4] for entry in self.secrets.values())
            self.timer = _Timer(max(1.0, due - _clock()), self.renew)
            self.timer.daemon = True
            self.timer.start()

    def renew(self):
        connection = self.connection()
        if connection is None:
            return
        try:
            cursor = connection.cursor()
        except duckdb.Error:
            return  # The connection was closed.
        due = {}
        try:
            with self.lock:
                due = {name: entry for name, entry in self.secrets.items() if entry[4] <= _clock()}
            if due:
                with httpx.Client(timeout=30, trust_env=False) as client:
                    _, _, _, base, endpoint = _catalog(client)
                    for name, entry in due.items():
                        self._renew_one(cursor, client, base, endpoint, name, entry)
            with self.lock:
                self._schedule()
        except Exception as exc:  # noqa: BLE001 - The session token may be gone; try again later.
            self._failed(due, exc)
        finally:
            cursor.close()

    def _renew_one(self, cursor, client, base, endpoint, name, entry):
        namespace, table, location, *_ = entry
        try:
            storage = _credentials(client, base, namespace, table, location) if endpoint else {}
            if not storage.get("s3.access-key-id"):
                storage, location = _vended(client, base, namespace, table)
            with self.lock:
                if self.secrets.get(name) != entry:
                    return  # Replaced meanwhile, perhaps for another table.
                _storage_secret(cursor, storage, location, secret_name=name)
                self._track(name, namespace, table, location, _expires_at(storage))
        except Exception as exc:  # noqa: BLE001 - One table's renewal never stops the others.
            self._failed({name: entry}, exc)

    def _failed(self, entries, exc):
        # Only the error's type: provider messages and SQL can contain credentials.
        now = _clock()
        with self.lock:
            for name, entry in entries.items():
                if self.secrets.get(name) != entry:
                    continue  # Renewed or replaced meanwhile.
                namespace, table, location, expires_at, _ = entry
                logger.warning("Could not renew the storage credentials of %s (%s).",
                               ".".join([*namespace, table]), type(exc).__name__)
                if now >= expires_at:
                    self.secrets.pop(name, None)  # Expired: reconnect, or call refresh_table_credentials.
                else:
                    self.secrets[name] = (namespace, table, location, expires_at, now + RENEW_RETRY_SECONDS)
            self._schedule()


def _install_secret(connection, namespace, table, storage, location, secret_name="table_storage"):
    renewal = _renewals.get(connection)
    if renewal is None:
        renewal = _renewals[connection] = _Renewal(connection)
    renewal.install(connection, secret_name, namespace, table, storage, location)


def connect_duckdb(namespace, table, *, read_only=True, missing_ok=False):
    """Attach Polaris as `lakehouse`, with storage credentials for one table that renew themselves.

    Only credential/config metadata goes through HTTPX. DuckDB's Iceberg extension
    resolves snapshots, scans manifests and reads or writes Parquet directly on RustFS.
    Shortly before Polaris's storage credentials expire, a background thread replaces
    them. DuckDB talks to the catalog through this kernel's loopback proxy
    (`catalog_proxy`), which signs every request with the owner's current token, so
    the attachment outlives the 15-minute notebook token.

    The attach is read-only unless `read_only=False`. Polaris grants remain the
    boundary either way: a reader who attaches writable is refused on the first write.
    With `missing_ok`, a table that does not exist yet gets no storage credentials;
    create it, then call `refresh_table_credentials` before inserting rows.
    """
    connection = None
    try:
        if missing_ok and read_only:
            raise ValueError("missing_ok requires a writable connection")
        with httpx.Client(timeout=30, trust_env=False) as client:
            _, endpoint, warehouse, base, _ = _catalog(client)
            vended = _vended(client, base, namespace, table, missing_ok)
        # DuckDB keeps the token it attached with, so it attaches to a loopback proxy that
        # replaces this process's random secret by the owner's current token.
        proxy = catalog_proxy.shared(endpoint, _proxy_token)
        # Explicit extension directory works with the runtime's temporary HOME
        # and read-only root filesystem. Extensions are installed at image build.
        settings = {"threads": 2, "memory_limit": "512MB"}
        if directory := os.environ.get("DUCKDB_EXTENSION_DIRECTORY"):
            settings["extension_directory"] = directory
        connection = duckdb.connect(config=settings)
        connection.execute("LOAD httpfs; LOAD iceberg;")
        # Semantic models state their dates in UTC; CAST(timestamptz AS DATE) follows the session's zone.
        connection.execute("SET TimeZone = 'UTC'")
        connection.execute(f"CREATE SECRET polaris (TYPE iceberg, TOKEN {sql_literal(proxy.secret)})")
        if vended:
            _install_secret(connection, namespace, table, *vended)
        # Disable automatic vending so it cannot replace the internal S3 endpoint
        # with Polaris's host-facing address. The credentials above are still
        # vended by Polaris for this user and this table; no static S3 keys are used.
        connection.execute(f"""
            ATTACH {sql_literal(warehouse)} AS lakehouse (
                TYPE iceberg, ENDPOINT {sql_literal(proxy.endpoint)}, SECRET polaris,
                ACCESS_DELEGATION_MODE 'none', SUPPORT_NESTED_NAMESPACES true{", READ_ONLY" if read_only else ""}
            )
        """)
        _discovery_tables[connection] = (
            {"table_storage": table_reference(namespace, table)} if vended else {}
        )
        return connection
    except duckdb.Error, httpx.HTTPError, KeyError, ValueError, OSError:
        if connection is not None:
            connection.close()
        # Provider/SQL errors can include tokens and generated CREATE SECRET SQL.
        raise RuntimeError(READ_ERROR if read_only else WRITE_ERROR) from None


def drop_example_table(connection, namespace, table, example):
    """Drop a table only when `example` created it, as its EXAMPLE_PROPERTY table property records."""
    exists = connection.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_catalog = 'lakehouse' AND table_schema = ? AND table_name = ?",
        [".".join(namespace), table],
    ).fetchall()[0][0]
    if not exists:
        return
    reference = table_reference(namespace, table)
    owner = connection.execute(
        f"SELECT value FROM iceberg_table_properties({reference}) WHERE key = ?", [EXAMPLE_PROPERTY]
    ).fetchall()
    if owner != [(example,)]:
        raise RuntimeError(
            f"{'.'.join(namespace)}.{table} already exists and this example did not create it, "
            "so it is not replaced. Choose another NAMESPACE or TABLE in the first cell."
        )
    connection.execute(f"DROP TABLE {reference}")


def refresh_table_credentials(connection, namespace, table, *, secret_name="table_storage"):
    """Vend scoped S3 credentials for a table, renewed before they expire.

    Distinct secret names retain access for multi-table joins. Call it after creating a table
    that `connect_duckdb` had no credentials for, or to read another table.
    """
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            _, _, _, base, _ = _catalog(client)
            _install_secret(connection, namespace, table, *_vended(client, base, namespace, table), secret_name)
        _discovery_tables.setdefault(connection, {})[secret_name] = table_reference(namespace, table)
    except duckdb.Error, httpx.HTTPError, KeyError, ValueError:
        raise RuntimeError(
            "Could not get storage credentials for this table. Check that it exists and your permissions."
        ) from None
