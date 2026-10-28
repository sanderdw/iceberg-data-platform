"""Image build step: install the DuckDB extensions from DuckDB's signed repository; the worker never downloads.

uv.lock pins the duckdb package, but extensions are fetched when the image is built, so two builds
can differ. VERSIONS in the extension directory records exactly which builds an image got.
"""

import os
from pathlib import Path

import duckdb

EXTENSIONS = ("iceberg", "httpfs", "avro")


def main():
    directory = os.environ["DUCKDB_EXTENSION_DIRECTORY"]
    connection = duckdb.connect(config={"extension_directory": directory})
    connection.execute("; ".join(f"INSTALL {name}" for name in EXTENSIONS))
    connection.close()
    # Load as the worker does: from the directory only, with automatic installs off.
    connection = duckdb.connect(config={"extension_directory": directory, "autoinstall_known_extensions": False})
    connection.execute("LOAD httpfs; LOAD avro; LOAD iceberg")
    extensions = connection.execute("""
        SELECT extension_name, extension_version, installed_from
        FROM duckdb_extensions()
        WHERE loaded AND install_mode <> 'STATICALLY_LINKED'
        ORDER BY extension_name
    """).fetchall()
    record = [f"duckdb {duckdb.__version__}"] + [f"{n} {v} (from {s})" for n, v, s in extensions]
    Path(directory, "VERSIONS").write_text("\n".join(record) + "\n")
    print("DuckDB extensions installed:", *record, sep="\n  ")


if __name__ == "__main__":
    main()
