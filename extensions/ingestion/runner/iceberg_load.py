"""Load append and replace tables into Iceberg without holding the load in memory.

dlt's Iceberg load job reads every file it extracted for a table into one Arrow table, uncompressed,
and writes it in one call: a 2 GB Parquet file can need 10 GB of memory. This reads the same files
in pieces of about PIECE bytes and writes each piece's data files before it reads the next. All of
them go into one snapshot (after the delete, for replace), in one commit, as pyiceberg's own
`append` and `overwrite` do: readers see the whole load at once, and a failed run changes nothing.

pyiceberg's streaming writes don't help here: they hand every file to `Executor.map`, which reads
the whole stream before it writes the first file.

Merge keeps dlt's own path, because an upsert needs the new rows at once to match them.
"""

import itertools
import warnings

import pyarrow as pa

PIECE = 128 * 1024**2


def pieces(dataset, schema, size=PIECE):
    """The dataset as Arrow tables of about `size` bytes each, cast to `schema`."""
    # One thread, one batch ahead: threaded scans decode many row groups at once and keep their memory.
    scan = dataset.to_batches(batch_size=64 * 1024, batch_readahead=1, fragment_readahead=1, use_threads=False)
    held, total = [], 0
    for batch in scan:
        held.append(batch.cast(schema))
        total += batch.nbytes
        if total >= size:
            yield pa.Table.from_batches(held, schema)
            held, total = [], 0
    if held:
        yield pa.Table.from_batches(held, schema)


def write(table, dataset, disposition, size=PIECE):
    from dlt.common.libs.pyiceberg import ensure_iceberg_compatible_arrow_schema
    from pyiceberg.expressions import AlwaysTrue
    from pyiceberg.io.pyarrow import _check_pyarrow_schema_compatible, _dataframe_to_data_files
    from pyiceberg.table import Operation

    schema = ensure_iceberg_compatible_arrow_schema(dataset.schema)
    with table.transaction() as transaction:
        metadata = transaction.table_metadata
        _check_pyarrow_schema_compatible(metadata.schema(), provided_schema=schema,
                                         format_version=metadata.format_version)
        if disposition == "replace":
            with warnings.catch_warnings():  # an empty table has nothing to delete
                warnings.filterwarnings("ignore", "Delete operation did not match any records")
                transaction.delete(AlwaysTrue(), _isolation_operation=Operation.OVERWRITE)
        counter = itertools.count(0)
        with transaction.update_snapshot().fast_append() as append:
            for piece in pieces(dataset, schema, size):
                if not piece.num_rows:
                    continue
                for data_file in _dataframe_to_data_files(table_metadata=transaction.table_metadata, df=piece,
                                                          io=table.io, write_uuid=append.commit_uuid,
                                                          counter=counter):
                    append.append_data_file(data_file)
                del piece  # before the next piece is read


def install(size=PIECE):
    """Route dlt's Iceberg load job through `write` for append and replace."""
    from dlt.common.destination.exceptions import DestinationUndefinedEntity
    from dlt.destinations.impl.filesystem.filesystem import IcebergLoadFilesystemJob

    original = getattr(IcebergLoadFilesystemJob.run, "original", IcebergLoadFilesystemJob.run)

    def run(self):
        disposition = self._load_table["write_disposition"]
        if disposition not in ("append", "replace"):
            return original(self)
        dataset = self.arrow_dataset
        try:
            table = self._job_client.load_open_table("iceberg", self.load_table_name, schema=dataset.schema)
        except DestinationUndefinedEntity:
            return original(self)  # creates the table, then calls run again
        write(table, dataset, disposition, size)

    run.original = original
    IcebergLoadFilesystemJob.run = run
