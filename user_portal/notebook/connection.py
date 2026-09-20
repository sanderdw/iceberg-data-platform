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


def connect():
    token = access_token()
    credentials = (
        {"token": token}
        if token else
        {"credential": f"{os.environ['ICEBERG_CLIENT_ID']}:{os.environ['ICEBERG_CLIENT_SECRET']}"}
    )
    if os.environ.get("ICEBERG_SESSION_TOKEN_URL"):
        credentials = {"auth": {"type": "custom", "impl": "user_portal.notebook.connection.SessionAuthManager"}}
    return load_catalog(
        os.environ["ICEBERG_DATABASE"],
        type="rest",
        uri=os.environ["ICEBERG_CATALOG_URI"],
        warehouse=os.environ["ICEBERG_DATABASE"],
        **credentials,
        scope="PRINCIPAL_ROLE:ALL",
        **{
            "oauth2-server-uri": os.environ["ICEBERG_TOKEN_URI"],
            "header.X-Iceberg-Access-Delegation": "vended-credentials",
            "py-io-impl": "user_portal.notebook.connection.InternalS3FileIO",
            "s3.endpoint": os.environ["ICEBERG_S3_ENDPOINT"],
        },
    )
