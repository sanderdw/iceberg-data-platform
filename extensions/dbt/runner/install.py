"""Image build step: install the DuckDB driver and extensions dbt v2 needs, so runs download nothing.

dbt v2 loads its DuckDB driver through the ADBC driver manager and would otherwise fetch it from
a CDN at run time. Run containers have no internet, so the pinned `duckdb` wheel's own ADBC entry
point is registered in a driver manifest, and the extensions are installed now from DuckDB's signed
repository into the default directory, which the attach dbt generates also uses.
"""

import os
from pathlib import Path

import duckdb

EXTENSIONS = ("iceberg", "httpfs", "avro")


def main():
    home = Path(os.environ["HOME"])
    drivers = Path(os.environ["ADBC_DRIVER_PATH"])
    home.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    connection.execute("; ".join(f"INSTALL {name}" for name in EXTENSIONS))
    platform = connection.execute("PRAGMA platform").fetchall()[0][0]
    connection.close()
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
    connection = duckdb.connect(config={"autoinstall_known_extensions": False})
    connection.execute("LOAD httpfs; LOAD iceberg")
    loaded = connection.execute(
        "SELECT extension_name, extension_version, installed_from FROM duckdb_extensions() "
        "WHERE loaded AND install_mode <> 'STATICALLY_LINKED' ORDER BY 1").fetchall()
    (home / "VERSIONS").write_text("\n".join([f"duckdb {duckdb.__version__} ({platform})",
                                              *(f"{n} {v} (from {s})" for n, v, s in loaded)]) + "\n")
    print((home / "VERSIONS").read_text())


if __name__ == "__main__":
    main()
