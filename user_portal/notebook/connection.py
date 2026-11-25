"""Notebook credentials always belong to the logged-in user, never the gateway."""

import logging
import os
import random
import threading
import time
from functools import lru_cache

from pyiceberg.catalog import load_catalog
from pyiceberg.catalog.rest import AUTH, RestCatalog
from pyiceberg.catalog.rest.auth import AuthManager
from pyiceberg.exceptions import CommitFailedException
from pyiceberg.io.pyarrow import PyArrowFileIO

from user_portal.notebook.credentials import access_token

logger = logging.getLogger(__name__)
# Vended storage credentials are renewed this long before they expire, so requests in flight keep working.
RENEW_BEFORE_SECONDS = 300
# After a failed renewal the catalog is asked again this much later; the old credentials stay in use meanwhile.
RENEW_RETRY_SECONDS = 30


def credentials_expire_at(properties):
    """When the vended S3 credentials in `properties` expire, in epoch seconds; None if not stated.

    Iceberg REST names it `s3.session-token-expires-at-ms`, in milliseconds since the epoch.
    """
    expiries = [int(value) for key, value in properties.items()
                if key.startswith("s3.") and key.endswith("expires-at-ms") and str(value).isdigit()]
    return min(expiries) / 1000 if expiries else None


def vended_credentials(catalog, identifier, location):
    """Fresh S3 credentials for one table: the catalog's credentials endpoint, else a full loadTable."""
    try:
        properties = catalog.load_credentials(identifier, location)
    except (AttributeError, NotImplementedError):
        properties = {}  # A catalog without `GET .../credentials`.
    if not properties.get("s3.access-key-id"):
        properties = catalog.load_table(identifier).io.properties
    return {key: value for key, value in properties.items() if key.startswith("s3.") and key != "s3.endpoint"}


class InternalS3FileIO(PyArrowFileIO):
    """PyArrow FileIO on the internal storage endpoint, renewing the table's vended credentials.

    Polaris vends host-facing endpoint properties; notebook containers use the same buckets through
    the internal RustFS endpoint instead. Once a REST catalog has bound it to its table
    (`renew_from`), it asks for new credentials shortly before they expire.
    """

    def __init__(self, properties=None):
        super().__init__({**(properties or {}), "s3.endpoint": os.environ["ICEBERG_S3_ENDPOINT"]})
        self._source = None
        self._retry_at = 0
        self._lock = threading.Lock()

    def renew_from(self, catalog, identifier, location):
        self._source = (catalog, tuple(identifier), location)

    def _renewal_due(self):
        expires_at, now = credentials_expire_at(self.properties), time.time()
        return expires_at is not None and now >= expires_at - RENEW_BEFORE_SECONDS and now >= self._retry_at

    def renew_credentials(self, *, force=False):
        """Replace the vended credentials when they are about to expire; True if they were replaced."""
        if self._source is None or not (force or self._renewal_due()):
            return False
        with self._lock:
            if not (force or self._renewal_due()):
                return False  # Another thread renewed them meanwhile.
            catalog, identifier, location = self._source
            try:
                fresh = vended_credentials(catalog, identifier, location)
            except Exception as exc:  # noqa: BLE001 - Keep the old credentials until they expire; retry later.
                self._retry_at = time.time() + RENEW_RETRY_SECONDS
                # Only the error's type: provider messages can contain credentials.
                logger.warning("Could not renew the storage credentials of %s (%s); retrying in %s seconds.",
                               ".".join(identifier), type(exc).__name__, RENEW_RETRY_SECONDS)
                return False
            if not fresh.get("s3.access-key-id"):
                self._retry_at = time.time() + RENEW_RETRY_SECONDS
                return False
            # Credentials vended this close to their expiry are renewed after a pause, not on every file.
            self._retry_at = time.time() + RENEW_RETRY_SECONDS
            stale = {key for key in self.properties if key.startswith("s3.") and key != "s3.endpoint"}
            self.properties = {**{k: v for k, v in self.properties.items() if k not in stale}, **fresh}
            # File systems keep the credentials they were made with.
            self.fs_by_scheme = lru_cache(self._initialize_fs)
            return True

    def new_input(self, location):
        self.renew_credentials()
        return super().new_input(location)

    def new_output(self, location):
        self.renew_credentials()
        return super().new_output(location)

    def delete(self, location):
        self.renew_credentials()
        return super().delete(location)

    def __getstate__(self):
        state = super().__getstate__()
        state.pop("_lock", None)
        state["_source"] = None  # A copy in another process cannot reach this catalog.
        return state

    def __setstate__(self, state):
        super().__setstate__(state)
        self._lock = threading.Lock()


def renew_table_credentials():
    """Bind every table that a REST catalog in this process loads to its `InternalS3FileIO`.

    PyIceberg does not renew a FileIO's vended credentials itself. Only tables that use this FileIO
    (`py-io-impl`) are affected; dlt and Polars load their catalogs through the same class.
    """
    original = RestCatalog._response_to_table
    if getattr(original, "renews_credentials", False):
        return

    def response_to_table(self, identifier_tuple, table_response):
        table = original(self, identifier_tuple, table_response)
        if isinstance(table.io, InternalS3FileIO):
            table.io.renew_from(self, identifier_tuple, table.metadata_location)
        return table

    response_to_table.renews_credentials = True
    RestCatalog._response_to_table = response_to_table


renew_table_credentials()


def commit_with_retry(load, change, *, table=None, attempts=5, base_delay=0.2, max_delay=5.0, sleep=None):
    """`change(table)`, again with a freshly loaded table (`load()`) when the commit conflicts.

    Polaris answers a commit that lost a race with a concurrent one, or whose requirements no longer
    hold, with 409 (PyIceberg's `CommitFailedException`); such a commit did not land. `change` must
    derive everything from the table it receives, so applying it again is safe. Appends and
    overwrites need no wrapper: PyIceberg retries their snapshot commits itself. Other errors are
    raised at once. Waits grow exponentially, with full jitter, up to `attempts` tries. The first try
    uses `table` when given.
    """
    for attempt in range(attempts):
        try:
            return change(table if attempt == 0 and table is not None else load())
        except CommitFailedException:
            if attempt == attempts - 1:
                raise
            (sleep or time.sleep)(random.uniform(0, min(max_delay, base_delay * 2**attempt)))


class SessionAuthManager(AuthManager):
    def auth_header(self):
        return f"Bearer {access_token()}"


SESSION_AUTH = {"type": "custom", "impl": "user_portal.notebook.connection.SessionAuthManager"}


class SessionRestCatalog(RestCatalog):
    """The REST catalog, signing every request with the notebook owner's current session token.

    It sets up that auth itself, so its properties stay plain strings: Polars' Iceberg sink copies
    a table's catalog properties and accepts nothing else.
    """

    def _create_session(self):
        self.properties[AUTH] = SESSION_AUTH
        try:
            return super()._create_session()
        finally:
            del self.properties[AUTH]


def catalog_properties():
    """The PyIceberg REST catalog properties of the notebook's database, signed in as its owner."""
    token = access_token()
    credentials = (
        {"token": token}
        if token else
        {"credential": f"{os.environ['ICEBERG_CLIENT_ID']}:{os.environ['ICEBERG_CLIENT_SECRET']}"}
    )
    if os.environ.get("ICEBERG_SESSION_TOKEN_URL"):
        credentials = {"auth": SESSION_AUTH}
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
    properties = catalog_properties()
    if properties.pop(AUTH, None):
        return SessionRestCatalog(os.environ["ICEBERG_DATABASE"], **properties)
    return load_catalog(os.environ["ICEBERG_DATABASE"], **properties)


def polars_storage_options(table):
    """The storage settings Polars needs, besides credentials, to read or write a loaded table's files.

    Polars' Iceberg scan and sink take the credentials Polaris vended for the table from its FileIO,
    and renew them from the catalog before they expire. Passing credentials here would stop that,
    so these are only the internal storage endpoint and its region.
    """
    properties = table.io.properties
    options = {}
    if endpoint := os.environ.get("ICEBERG_S3_ENDPOINT") or properties.get("s3.endpoint"):
        options["s3.endpoint"] = endpoint
    if properties.get("s3.access-key-id"):
        options["s3.region"] = properties.get("s3.region") or "us-east-1"
    if options.get("s3.endpoint", "").startswith("http://"):
        options["allow_http"] = "true"
    return options


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
