"""PyIceberg storage access for runs: the internal endpoint, renewed credentials, retried metadata commits.

Polaris vends credentials per table that expire (`s3.session-token-expires-at-ms`). dlt keeps a loaded
table for the whole load of that table, merges in batches of 1,000 rows, and Polaris may hand out
cached credentials with less than their full lifetime left, so a long load can outlive them.
PyIceberg does not renew a FileIO's credentials itself; this FileIO does.
"""

import logging
import os
import random
import threading
import time
from functools import lru_cache

from pyiceberg.catalog.rest import RestCatalog
from pyiceberg.exceptions import CommitFailedException
from pyiceberg.io.pyarrow import PyArrowFileIO

logger = logging.getLogger(__name__)
# Credentials are renewed this long before they expire, so requests in flight keep working.
RENEW_BEFORE_SECONDS = 300
# After a failed renewal the catalog is asked again this much later; the old credentials stay in use meanwhile.
RENEW_RETRY_SECONDS = 30


def credentials_expire_at(properties):
    """When the vended S3 credentials in `properties` expire, in epoch seconds; None if not stated."""
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
    """PyArrow FileIO on the internal storage endpoint that renews its table's vended credentials.

    Polaris vends the host-facing storage endpoint. Runs reach the same buckets through the internal
    endpoint; vended credentials are not bound to a host name.
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
            # Credentials vended this close to their expiry are renewed after a pause, not on every file.
            self._retry_at = time.time() + RENEW_RETRY_SECONDS
            if not fresh.get("s3.access-key-id"):
                return False
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

    dlt builds its catalog itself, so the binding happens where every REST catalog turns a
    response into a table. Only tables that use this FileIO (`py-io-impl`) are affected.
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
    derive everything from the table it receives, so applying it again is safe. dlt's appends,
    overwrites and upserts need no wrapper: PyIceberg retries their snapshot commits itself. Other
    errors are raised at once. Waits grow exponentially, with full jitter, up to `attempts` tries.
    """
    for attempt in range(attempts):
        try:
            return change(table if attempt == 0 and table is not None else load())
        except CommitFailedException:
            if attempt == attempts - 1:
                raise
            (sleep or time.sleep)(random.uniform(0, min(max_delay, base_delay * 2**attempt)))
