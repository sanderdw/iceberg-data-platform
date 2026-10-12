"""Notebook credentials always belong to the logged-in user, never the gateway."""

import os

from pyiceberg.catalog import load_catalog
from pyiceberg.catalog.rest.auth import AuthManager
from pyiceberg.io.pyarrow import PyArrowFileIO

from user_portal.notebook.credentials import access_token


class InternalS3FileIO(PyArrowFileIO):
    def __init__(self, properties=None):
        # Polaris vends host-facing endpoint properties. Notebook containers use
        # the same buckets through the internal RustFS endpoint instead.
        super().__init__({**(properties or {}), "s3.endpoint": os.environ["ICEBERG_S3_ENDPOINT"]})


class SessionAuthManager(AuthManager):
    def auth_header(self):
        return f"Bearer {access_token()}"


def catalog_properties():
    """The PyIceberg REST catalog properties of the notebook's database, signed in as its owner."""
    token = access_token()
    credentials = (
        {"token": token}
        if token else
        {"credential": f"{os.environ['ICEBERG_CLIENT_ID']}:{os.environ['ICEBERG_CLIENT_SECRET']}"}
    )
    if os.environ.get("ICEBERG_SESSION_TOKEN_URL"):
        credentials = {"auth": {"type": "custom", "impl": "user_portal.notebook.connection.SessionAuthManager"}}
    return {
        "type": "rest",
        "uri": os.environ["ICEBERG_CATALOG_URI"],
        "warehouse": os.environ["ICEBERG_DATABASE"],
        **credentials,
        "scope": "PRINCIPAL_ROLE:ALL",
        "oauth2-server-uri": os.environ["ICEBERG_TOKEN_URI"],
        "header.X-Iceberg-Access-Delegation": "vended-credentials",
        "py-io-impl": "user_portal.notebook.connection.InternalS3FileIO",
        "s3.endpoint": os.environ["ICEBERG_S3_ENDPOINT"],
    }


def connect():
    return load_catalog(os.environ["ICEBERG_DATABASE"], **catalog_properties())


def dlt_destination(local_dir):
    """A dlt filesystem destination that writes Iceberg tables into the notebook's database.

    dlt keeps its own bookkeeping (`_dlt_loads`, `_dlt_pipeline_state`, `_dlt_version`) as plain files
    under `bucket_url`. That is `local_dir` here, so the bookkeeping never needs storage keys and
    never lands in your namespaces. The tables themselves go through the catalog with your own
    identity and vended credentials.
    """
    import dlt

    dlt.config["iceberg_catalog.iceberg_catalog_type"] = "rest"
    dlt.config["iceberg_catalog.iceberg_catalog_config"] = catalog_properties()
    patch_dlt_table_location()
    return dlt.destinations.filesystem(bucket_url=f"file://{os.path.abspath(local_dir)}")


def patch_dlt_table_location():
    """Let the catalog choose where a new table lives.

    dlt derives a new table's location from its `bucket_url`, a local folder here. Polaris only
    accepts locations inside the database's own bucket, so dlt passes no location and Polaris
    uses the database's default instead.
    """
    import dlt.common.libs.pyiceberg as dlt_iceberg

    create_table = dlt_iceberg.create_table
    if getattr(create_table, "catalog_location", False):
        return

    def catalog_create_table(catalog, table_id, table_location, *args, **kwargs):
        return create_table(catalog, table_id, None, *args, **kwargs)

    catalog_create_table.catalog_location = True
    dlt_iceberg.create_table = catalog_create_table
