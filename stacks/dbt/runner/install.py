"""Image build step: install the DuckDB driver and extensions dbt v2 needs, without any download.

dbt v2 loads its DuckDB driver through the ADBC driver manager and would otherwise fetch it from
a CDN at run time. Run containers have no internet, so the pinned `duckdb` wheel's own ADBC entry
point is registered in a driver manifest, and the extensions come from their pinned PyPI wheels.
"""

import importlib
import os
import shutil
from pathlib import Path

import duckdb

EXTENSIONS = ("iceberg", "httpfs", "avro")


def main():
    home = Path(os.environ["HOME"])
    drivers = Path(os.environ["ADBC_DRIVER_PATH"])
    platform = duckdb.execute("PRAGMA platform").fetchone()[0]
    version = "v" + duckdb.__version__
    target = home / ".duckdb" / "extensions" / version / platform
    target.mkdir(parents=True, exist_ok=True)
    for name in EXTENSIONS:
        package = importlib.import_module(f"duckdb_extension_{name}")
        source = Path(package.__file__).parent / "extensions" / version / f"{name}.duckdb_extension"
        shutil.copyfile(source, target / source.name)
    library = next(Path(duckdb.__file__).parent.parent.glob("_duckdb*.so"))
    drivers.mkdir(parents=True, exist_ok=True)
    (drivers / "duckdb.toml").write_text(
        "manifest_version = 1\n"
        'name = "DuckDB"\n'
        f'version = "{duckdb.__version__}"\n'
        "[ADBC]\n"
        'version = "1.1.0"\n'
        "[Driver]\n"
        'entrypoint = "duckdb_adbc_init"\n'
        "[Driver.shared]\n"
        f'{platform} = "{library}"\n'
    )
    connection = duckdb.connect()
    connection.execute("LOAD httpfs; LOAD iceberg")
    loaded = connection.execute(
        "SELECT extension_name, extension_version FROM duckdb_extensions() WHERE loaded ORDER BY 1").fetchall()
    (home / "VERSIONS").write_text("\n".join([f"duckdb {duckdb.__version__} ({platform})",
                                              *(f"{n} {v}" for n, v in loaded)]) + "\n")
    print((home / "VERSIONS").read_text())


if __name__ == "__main__":
    main()
