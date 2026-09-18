"""Native DuckDB on Iceberg with user-scoped Polaris credentials and internal S3 routing."""

import os
from urllib.parse import quote, urlsplit

import duckdb
import httpx

READ_ERROR = (
    "Could not connect to the Iceberg table. Check the selected table and your read permissions, "
    "and rebuild the notebook image if DuckDB extensions are missing."
)
WRITE_ERROR = (
    "Could not connect to the Iceberg catalog for writing. Check that your role in the active team "
    "allows writing, and rebuild the notebook image if DuckDB extensions are missing."
)


def _register_variant_with_marimo():
    # marimo's datasets panel has no mapping for DuckDB's VARIANT and logs a warning for
    # every such column. It shows the column as "unknown" either way, so say that up front.
    try:
        from marimo._data import get_datasets

        get_datasets._UNKNOWN_TYPES.add("variant")
    except ImportError, AttributeError:
        pass  # No marimo (plain Python) or its internals moved: only the warning returns.


_register_variant_with_marimo()


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def schema_reference(namespace):
    # DuckDB represents nested Iceberg namespaces as a single dotted schema name.
    return ".".join('"' + part.replace('"', '""') + '"' for part in ("lakehouse", ".".join(namespace)))


def table_reference(namespace, table):
    return schema_reference(namespace) + '."' + table.replace('"', '""') + '"'


def _catalog(client):
    """Authenticate this user and resolve the catalog's REST prefix."""
    endpoint = os.environ["ICEBERG_CATALOG_URI"].rstrip("/")
    warehouse = os.environ["ICEBERG_DATABASE"]
    token = os.environ.get("ICEBERG_ACCESS_TOKEN")
    if not token:
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
        token = response.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    response = client.get(f"{endpoint}/v1/config", params={"warehouse": warehouse})
    response.raise_for_status()
    config = response.json()
    properties = {**config.get("defaults", {}), **config.get("overrides", {})}
    prefix = properties.get("prefix", "")
    base = f"{endpoint}/v1" + (f"/{quote(prefix, safe='/')}" if prefix else "")
    return token, endpoint, warehouse, base


def _vended(client, base, namespace, table, missing_ok=False):
    """Temporary S3 credentials and location that Polaris vends for one table."""
    response = client.get(
        f"{base}/namespaces/{quote(chr(31).join(namespace), safe='')}/tables/{quote(table, safe='')}",
        headers={"X-Iceberg-Access-Delegation": "vended-credentials"},
    )
    if response.status_code == 404 and missing_ok:
        return None
    response.raise_for_status()
    loaded = response.json()
    return loaded["config"], loaded["metadata"]["location"].rstrip("/") + "/"


def _storage_secret(connection, storage, location):
    s3 = urlsplit(os.environ["ICEBERG_S3_ENDPOINT"])
    if s3.scheme not in {"http", "https"} or not s3.netloc:
        raise ValueError("Invalid internal S3 endpoint")
    connection.execute(f"""
        CREATE OR REPLACE SECRET table_storage (
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


def connect_duckdb(namespace, table, *, read_only=True, missing_ok=False):
    """Attach Polaris as `lakehouse`; refresh this connection to renew temporary credentials.

    Only credential/config metadata goes through HTTPX. DuckDB's Iceberg extension
    resolves snapshots, scans manifests and reads or writes Parquet directly on RustFS.

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
            token, endpoint, warehouse, base = _catalog(client)
            vended = _vended(client, base, namespace, table, missing_ok)
        # Explicit extension directory works with the runtime's temporary HOME
        # and read-only root filesystem. Extensions are installed at image build.
        settings = {"threads": 2, "memory_limit": "512MB"}
        if directory := os.environ.get("DUCKDB_EXTENSION_DIRECTORY"):
            settings["extension_directory"] = directory
        connection = duckdb.connect(config=settings)
        connection.execute("LOAD httpfs; LOAD iceberg;")
        connection.execute(f"CREATE SECRET polaris (TYPE iceberg, TOKEN {sql_literal(token)})")
        if vended:
            _storage_secret(connection, *vended)
        # Disable automatic vending so it cannot replace the internal S3 endpoint
        # with Polaris's host-facing address. The credentials above are still
        # vended by Polaris for this user and this table; no static S3 keys are used.
        connection.execute(f"""
            ATTACH {sql_literal(warehouse)} AS lakehouse (
                TYPE iceberg, ENDPOINT {sql_literal(endpoint)}, SECRET polaris,
                ACCESS_DELEGATION_MODE 'none', SUPPORT_NESTED_NAMESPACES true{", READ_ONLY" if read_only else ""}
            )
        """)
        return connection
    except duckdb.Error, httpx.HTTPError, KeyError, ValueError:
        if connection is not None:
            connection.close()
        # Provider/SQL errors can include tokens and generated CREATE SECRET SQL.
        raise RuntimeError(READ_ERROR if read_only else WRITE_ERROR) from None


def refresh_table_credentials(connection, namespace, table):
    """Vend fresh S3 credentials for one table, such as a table this connection just created."""
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            _, _, _, base = _catalog(client)
            _storage_secret(connection, *_vended(client, base, namespace, table))
    except duckdb.Error, httpx.HTTPError, KeyError, ValueError:
        raise RuntimeError(
            "Could not get storage credentials for this table. Check that it exists and your permissions."
        ) from None
