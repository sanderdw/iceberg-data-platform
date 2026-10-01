"""Image build step: copy the DuckDB extensions from their pinned PyPI wheels; the worker never downloads."""

import importlib
import os
import shutil
from pathlib import Path

import duckdb

EXTENSIONS = ("iceberg", "httpfs", "avro")


def main():
    platform = duckdb.execute("PRAGMA platform").fetchone()[0]
    version = "v" + duckdb.__version__
    target = Path(os.environ["DUCKDB_EXTENSION_DIRECTORY"]) / version / platform
    target.mkdir(parents=True, exist_ok=True)
    for name in EXTENSIONS:
        package = importlib.import_module(f"duckdb_extension_{name}")
        source = Path(package.__file__).parent / "extensions" / version / f"{name}.duckdb_extension"
        shutil.copyfile(source, target / source.name)
    connection = duckdb.connect(config={"extension_directory": os.environ["DUCKDB_EXTENSION_DIRECTORY"],
                                        "autoinstall_known_extensions": False})
    connection.execute("LOAD httpfs; LOAD avro; LOAD iceberg")
    print(connection.execute("SELECT extension_name, extension_version FROM duckdb_extensions() WHERE loaded "
                             "ORDER BY 1").fetchall())


if __name__ == "__main__":
    main()
