"""One dbt invocation in an isolated run container.

The launcher stages /work with the project at a fixed revision, generated `profiles/` and
`catalogs.yml`, and `job.json`. This runs dbt, collects its artifacts into /work/out, and for
builds stamps table metadata as the automation principal (runner/publish.py). The catalog token
arrives in DBT_ENV_SECRET_POLARIS_TOKEN, which dbt scrubs from its logs.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from runner import forward

WORK = Path(os.environ.get("RUNNER_WORK", "/work"))
COMMANDS = {"build", "run", "test", "seed", "compile", "show", "parse", "docs"}
INFO_SCHEMA = {"build", "run", "test", "seed", "compile", "parse"}
MAX_LOG = 5_000_000


def arguments(job):
    command = list(job["command"])
    if not command or command[0] not in COMMANDS or (command[0] == "docs" and command[1:2] != ["generate"]):
        raise SystemExit(f"Command not allowed: {command[:1]}")
    extra = ["--profiles-dir", str(WORK / "profiles"), "--profile", "iceberg_platform",
             "--target", job["environment"]]
    if command[0] in INFO_SCHEMA:
        extra.append("--generate-info-schema")
    if command[0] == "docs":
        extra += ["--output-dir", str(WORK / "out" / "docs")]
    return ["dbt", *command, *extra]


def collect(project, out):
    target = project / "target"
    for name in ("run_results.json", "manifest.json", "semantic_manifest.json"):
        if (target / name).exists():
            (out / "target").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target / name, out / "target" / name)
    for part in (Path("info_schema") / "v1", Path("compiled")):
        if (target / part).is_dir():
            shutil.copytree(target / part, out / "target" / part, dirs_exist_ok=True)


def show_rows(log_text):
    """`dbt show --output json` prints the rows as one JSON array line."""
    for line in reversed(log_text.splitlines()):
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def main():
    job = json.loads((WORK / "job.json").read_text())
    out = WORK / "out"
    out.mkdir(exist_ok=True)
    project = WORK / "project"
    started = time.time()
    result = {"startedAt": started}
    if job.get("forward"):
        forward.start(job["forward"]["listen"], job["forward"]["target"])
    args = arguments(job)
    # dbt keeps state under ~/.dbt, so HOME must be writable; the image's DuckDB extensions stay read-only.
    home = Path("/tmp/home")
    home.mkdir(parents=True, exist_ok=True)
    installed = Path(os.environ.get("HOME", "/opt/runner/home")) / ".duckdb"
    if installed.is_dir() and not (home / ".duckdb").exists():
        (home / ".duckdb").symlink_to(installed)
    env = {**os.environ, "HOME": str(home), "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DO_NOT_TRACK": "1",
           "NO_COLOR": "1"}
    with open(out / "dbt.log", "wb") as log:
        process = subprocess.Popen(args, cwd=project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        written = 0
        for line in process.stdout:
            sys.stdout.buffer.write(line)
            sys.stdout.flush()
            if written < MAX_LOG:
                log.write(line)
                written += len(line)
        result["exitCode"] = process.wait()
    collect(project, out)
    if job["command"][0] == "show":
        rows = show_rows((out / "dbt.log").read_text(errors="replace"))
        if rows is not None:
            (out / "show.json").write_text(json.dumps(rows[:500]))
    if job.get("publish") and (out / "target" / "run_results.json").exists():
        from runner import publish

        try:
            token = os.environ["DBT_ENV_SECRET_POLARIS_TOKEN"]
            result["published"] = publish.main(job["publish"], out / "target", token)
        except Exception as exc:  # noqa: BLE001 - Metadata never fails a run; the error is reported.
            result["published"] = {"error": type(exc).__name__}
    result["finishedAt"] = time.time()
    (out / "result.json").write_text(json.dumps(result))
    return result["exitCode"]


if __name__ == "__main__":
    sys.exit(main())
