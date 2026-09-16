"""Native DuckDB reads with user-scoped Polaris credentials and internal S3 routing."""

import os
from urllib.parse import quote, urlsplit

import duckdb
import httpx


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def table_reference(namespace, table):
    # DuckDB represents nested Iceberg namespaces as a single dotted schema name.
    return ".".join('"' + part.replace('"', '""') + '"' for part in ("lakehouse", ".".join(namespace), table))


def connect_duckdb(namespace, table):
    """Attach Polaris read-only; refresh this connection to renew temporary credentials.

    Only credential/config metadata goes through HTTPX. DuckDB's Iceberg extension
    resolves snapshots, scans manifests and reads Parquet directly from RustFS.
    """
    connection = None
    try:
        endpoint = os.environ["ICEBERG_CATALOG_URI"].rstrip("/")
        warehouse = os.environ["ICEBERG_DATABASE"]
        with httpx.Client(timeout=30, trust_env=False) as client:
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
            response = client.get(
                f"{base}/namespaces/{quote(chr(31).join(namespace), safe='')}/tables/{quote(table, safe='')}",
                headers={"X-Iceberg-Access-Delegation": "vended-credentials"},
            )
            response.raise_for_status()
            loaded = response.json()
        storage = loaded["config"]
        location = loaded["metadata"]["location"].rstrip("/") + "/"
        s3 = urlsplit(os.environ["ICEBERG_S3_ENDPOINT"])
        if s3.scheme not in {"http", "https"} or not s3.netloc:
            raise ValueError("Invalid internal S3 endpoint")
        # Explicit extension directory works with the runtime's temporary HOME
        # and read-only root filesystem. Extensions are installed at image build.
        settings = {"threads": 2, "memory_limit": "512MB"}
        if directory := os.environ.get("DUCKDB_EXTENSION_DIRECTORY"):
            settings["extension_directory"] = directory
        connection = duckdb.connect(config=settings)
        connection.execute("LOAD httpfs; LOAD iceberg;")
        connection.execute(f"CREATE SECRET polaris (TYPE iceberg, TOKEN {sql_literal(token)})")
        connection.execute(f"""
            CREATE SECRET table_storage (
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
        # Disable automatic vending so it cannot replace the internal S3 endpoint
        # with Polaris's host-facing address. The credentials above are still
        # vended by Polaris for this user and this table; no static S3 keys are used.
        connection.execute(f"""
            ATTACH {sql_literal(warehouse)} AS lakehouse (
                TYPE iceberg, ENDPOINT {sql_literal(endpoint)}, SECRET polaris,
                ACCESS_DELEGATION_MODE 'none', SUPPORT_NESTED_NAMESPACES true,
                READ_ONLY
            )
        """)
        return connection
    except duckdb.Error, httpx.HTTPError, KeyError, ValueError:
        if connection is not None:
            connection.close()
        # Provider/SQL errors can include tokens and generated CREATE SECRET SQL.
        raise RuntimeError(
            "Could not connect to the Iceberg table. Check the selected table and your read permissions, "
            "and rebuild the notebook image if DuckDB extensions are missing."
        ) from None
