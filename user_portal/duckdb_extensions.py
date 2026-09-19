"""Install the DuckDB extensions an image needs and record exactly which builds it got.

uv.lock pins the duckdb package, but extensions are fetched from DuckDB's repository
when the image is built, so two builds can differ. The record makes that traceable.
"""

import os
from pathlib import Path

import duckdb


def main():
    directory = os.environ["DUCKDB_EXTENSION_DIRECTORY"]
    connection = duckdb.connect(config={"extension_directory": directory})
    connection.execute("INSTALL httpfs; INSTALL iceberg; LOAD httpfs; LOAD iceberg")
    extensions = connection.execute("""
        SELECT extension_name, extension_version, installed_from
        FROM duckdb_extensions()
        WHERE installed AND install_mode <> 'STATICALLY_LINKED'
        ORDER BY extension_name
    """).fetchall()
    connection.close()
    record = [f"duckdb {duckdb.__version__}"]
    record += [f"{name} {version} (from {source})" for name, version, source in extensions]
    Path(directory, "VERSIONS").write_text("\n".join(record) + "\n")
    print("DuckDB extensions installed:", *record, sep="\n  ")


if __name__ == "__main__":
    main()
