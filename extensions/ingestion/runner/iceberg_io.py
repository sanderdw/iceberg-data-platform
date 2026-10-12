import os

from pyiceberg.io.pyarrow import PyArrowFileIO


class InternalS3FileIO(PyArrowFileIO):
    def __init__(self, properties=None):
        # Polaris vends the host-facing storage endpoint. Runs reach the same buckets through the
        # internal endpoint; vended credentials are not bound to a host name.
        super().__init__({**(properties or {}), "s3.endpoint": os.environ["ICEBERG_S3_ENDPOINT"]})
