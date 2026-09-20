"""Reproduce named DuckDB-profile routing in the pinned dbt Charts release.

Run with the reporting environment's Python. No data service or credentials are
needed. A profile containing a deliberately unavailable dbt-duckdb plugin still
executes a plain SQL board: the named-source path bypasses that plugin interface.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="reporting-adapter-probe-") as directory:
        root = Path(directory)
        (root / "profiles.yml").write_text(
            "probe:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
            "      path: ':memory:'\n      schema: main\n      threads: 1\n"
            "      plugins:\n        - module: missing_reporting_probe_plugin\n"
        )
        (root / "dbt_project.yml").write_text(
            "name: reporting_probe\nversion: '1.0'\nconfig-version: 2\nprofile: probe\nmodel-paths: []\n"
        )
        (root / "dbt_charts.yml").write_text(
            "sources:\n  probe:\n    type: dbt_profile\n    profile: probe\n    target: dev\n"
        )
        (root / "board.yml").write_text(
            "source: probe\nqueries:\n  probe: SELECT 42 AS value\ncharts:\n  probe:\n"
            "    type: kpi\n    query: probe\n    value: value\nrows: [probe]\n"
        )
        subprocess.run(
            [
                str(Path(sys.executable).with_name("dct")),
                "render",
                str(root / "board.yml"),
                "--project-dir",
                directory,
                "--format",
                "data",
                "--output",
                str(root / "data.json"),
                "--no-cache",
            ],
            env={
                "PATH": os.defpath,
                "HOME": directory,
                "DBT_PROFILES_DIR": directory,
                "DO_NOT_TRACK": "1",
                "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
            },
            cwd=directory,
            capture_output=True,
            timeout=30,
            check=True,
        )
        data = json.loads((root / "data.json").read_text())
        assert data["queries"]["probe"]["rows"] == [{"value": 42}]
        print(
            "CONFIRMED: the named DuckDB profile executes without loading its configured dbt-duckdb plugin."
        )
        print("Use the controlled DuckDB query worker and inline aggregate results for dbt Charts rendering.")


if __name__ == "__main__":
    main()
