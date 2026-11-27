"""One replication run, or one source check, in an isolated run container built on OLake's image.

The launcher stages /work with `job.json` and the replication's previous OLake state. This writes
OLake's configuration files, runs its command line and collects the outcome in /work/out:
`result.json`, `run.log` and, after a sync, the new `state/state.json`. The catalog token arrives
in REP_TOKEN and the source's credentials in REP_SECRETS; both are scrubbed from everything the
run writes, and OLake's telemetry is off.

OLake talks to the catalog through a loopback proxy (runner/catalog_proxy.py) that points vended
storage credentials at the platform's internal storage. A sync stops at the job's deadline, before
the catalog token expires: OLake commits in chunks and records the chunks still to copy, so the
next run continues (status "incomplete").
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from runner import catalog_proxy, olake, publish
from runner.scrub import Scrubber

WORK = Path(os.environ.get("RUNNER_WORK", "/work"))
OLAKE = os.environ.get("OLAKE_BIN", "/home/olake")
JAR = os.environ.get("OLAKE_JAR", "/home/olake-iceberg-java-writer.jar")
PROXY_PORT = 18181
MAX_LOG = 5_000_000
# After the stop signal OLake commits what it has and saves its state; it gets this long.
STOP_GRACE = 150


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


class Log:
    """OLake's output and the runner's own lines: scrubbed, without colours, to stdout and run.log."""

    def __init__(self, path, scrub):
        self.file, self.scrub, self.written, self.lock = open(path, "w", encoding="utf-8"), scrub, 0, threading.Lock()
        self.text = []

    def line(self, text):
        text = self.scrub(olake.ANSI.sub("", text.rstrip("\n")))
        with self.lock:
            print(text, flush=True)
            if self.written < MAX_LOG:
                self.file.write(text[: MAX_LOG - self.written] + "\n")
                self.written += len(text) + 1
            self.text.append(text)
            if len(self.text) > 20000:
                del self.text[:5000]

    def close(self):
        self.file.close()


def environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith("REP_")}
    env.update({
        "TELEMETRY_DISABLED": "true", "HOME": "/tmp", "OLAKE_INDEX_DB_DIR": str(WORK / "index"),
        "JAVA_TOOL_OPTIONS": f"-Djava.io.tmpdir={WORK / 'tmp'} -XX:+ExitOnOutOfMemoryError",
    })
    return env


def olake_command(log, args, deadline=None):
    """Run OLake; returns (exit code, stopped at the deadline, its output)."""
    start = len(log.text)
    process = subprocess.Popen([OLAKE, *args], cwd=WORK / "olake", env=environment(), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True)
    stopped = threading.Event()

    def stop():
        stopped.set()
        log.line("[replication] Time limit reached: stopping OLake so it commits and saves its state.")
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(STOP_GRACE)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)

    timer = threading.Timer(max(1, deadline), stop) if deadline else None
    if timer:
        timer.daemon = True
        timer.start()
    for line in process.stdout:
        log.line(line)
    code = process.wait()
    if timer:
        timer.cancel()
    if code:
        log.line(f"[replication] OLake ended with exit code {code}" +
                 (" (killed: probably out of memory, see RUN_MEMORY)" if code in (-9, 137) else ""))
    return code, stopped.is_set(), "\n".join(log.text[start:])


def failure(code, output):
    """OLake's own reason for a failed run, or what its exit code says."""
    lines = [line for line in output.splitlines()
             if (" FATAL " in line or " ERROR " in line) and "Picked up JAVA_TOOL_OPTIONS" not in line]
    if lines:
        return lines[-1].split(" ", 2)[-1][:2000]
    if code in (-9, 137):
        return ("OLake was stopped by the system, probably because it ran out of memory. Ask the platform "
                "administrator to raise RUN_MEMORY, or lower source.maxThreads.")
    return f"OLake ended with exit code {code}. Read the run log."


def write(path, value):
    path.write_text(json.dumps(value, indent=2))
    path.chmod(0o600)


def discover(log, config):
    code, _, output = olake_command(log, ["discover", "--config", str(config)])
    path = config.parent / "available_streams.json"
    if code != 0 or not path.exists():
        statuses = olake.messages(output, "connectionStatus")
        raise RuntimeError((statuses[-1].get("message") if statuses else None) or
                           "OLake could not list the source's tables. Read the run log.")
    return olake.available(path)


def check(job, secrets, log):
    folder = WORK / "olake"
    config = folder / "source.json"
    write(config, olake.source_config(job, secrets))
    code, _, output = olake_command(log, ["check", "--config", str(config)])
    statuses = olake.messages(output, "connectionStatus")
    status = statuses[-1] if statuses else {}
    if not status:
        raise RuntimeError(f"OLake could not check the source (exit code {code}). Read the run log.")
    # Prerequisites are only checked once OLake is connected; a failed connection has none.
    result = {"connected": status.get("status") == "SUCCEEDED" or bool(status.get("prerequisites")),
              "ready": status.get("status") == "SUCCEEDED", "message": status.get("message") or "",
              "checks": olake.checks(status), "streams": []}
    if result["connected"]:
        try:
            result["streams"] = olake.describe(discover(log, config))
        except RuntimeError as exc:
            result["message"] = result["message"] or str(exc)
    return {**result, "status": "success"}


def sync(job, secrets, token, log, out):
    folder = WORK / "olake"
    config, destination = folder / "source.json", folder / "destination.json"
    selected, state_file = folder / "selected_streams.json", folder / "state.json"
    write(config, olake.source_config(job, secrets))
    upstream = job["catalogUri"].rstrip("/")
    proxy = catalog_proxy.start(PROXY_PORT, upstream, job.get("publicStorageEndpoint") or job["storageEndpoint"],
                                job["storageEndpoint"])
    path = upstream.split("://", 1)[1].split("/", 1)
    catalog_url = f"http://127.0.0.1:{PROXY_PORT}" + (f"/{path[1]}" if len(path) > 1 else "")
    try:
        write(destination, olake.destination_config(job, token, catalog_url, JAR))
        streams = discover(log, config)
        chosen = olake.selected_streams(job, streams)
        appended = olake.append_only(chosen)
        if "update_method" in json.loads(config.read_text()):
            if appended:
                log.line(f"[replication] {', '.join(appended)} are appended to (no primary key or appendOnly), so "
                         "large values an update leaves unchanged are not read back from the table: give the "
                         "replication's change-capture tables REPLICA IDENTITY FULL at the source.")
            else:
                # Read unchanged large values back from the table; OLake then allows only upserts, so it lists the
                # tables again with that setting.
                write(config, olake.source_config(job, secrets, preserve_toast=True))
                streams = discover(log, config)
        write(selected, chosen)
        previous = WORK / "state" / "state.json"
        if previous.exists() and not job.get("fullRefresh"):
            shutil.copyfile(previous, state_file)
            log.line("[replication] Continuing from the saved change log position.")
        else:
            write(state_file, {})
        started_ms = int(time.time() * 1000) - 5000
        log.line(f"[replication] {job['replicationName']}: {len(job['tables'])} tables into {job['namespace']}")
        code, stopped, output = olake_command(log, [
            "sync", "--config", str(config), "--destination", str(destination),
            "--available-streams", str(folder / "available_streams.json"), "--selected-streams", str(selected),
            "--state", str(state_file)], deadline=job.get("deadline"))
    finally:
        proxy.shutdown()
    state = json.loads(state_file.read_text() or "{}") if state_file.exists() else {}
    if code != 0 and not stopped:
        raise RuntimeError(failure(code, output))
    target = out / "state"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(state_file, target / "state.json")
    done = olake.first_copy_done(state)
    position = olake.position(state)
    tables, published = publish.main(job, token, started_ms, position, os.environ.get("DRIVER_VERSION", ""))
    status = "incomplete" if stopped or not done else "success"
    return {"status": status, "tables": tables, "published": published, "readUpTo": position,
            "firstCopyComplete": done, "recordsRead": olake.records_read(output), "stoppedAtLimit": stopped}


def main():
    out = WORK / "out"
    out.mkdir(parents=True, exist_ok=True)
    for name in ("olake", "index", "tmp"):
        (WORK / name).mkdir(parents=True, exist_ok=True)
    token = os.environ.pop("REP_TOKEN", "")
    secrets = json.loads(os.environ.pop("REP_SECRETS", "") or "{}")
    scrub = Scrubber([token, *secrets.values()])
    result = {"status": "failed", "kind": None, "startedAt": now(), "olakeVersion": os.environ.get("DRIVER_VERSION"),
              "error": None}
    log = Log(out / "run.log", scrub)
    try:
        job = json.loads((WORK / "job.json").read_text())
        result["kind"] = job.get("kind", "sync")
        if result["kind"] == "check":
            result.update(check(job, secrets, log))
        else:
            result.update(sync(job, secrets, token, log, out))
    except Exception as exc:  # noqa: BLE001 - Any failure ends the run with a scrubbed message.
        for line in traceback.format_exc().splitlines():
            log.line(line)
        result["status"] = "failed"
        result["error"] = scrub(f"{exc}")[:4000]
    finally:
        result["finishedAt"] = now()
        (out / "result.json").write_text(json.dumps(result, indent=2, default=str))
        log.close()
        # OLake's own configuration files hold the password and token: never leave them behind.
        for name in ("source.json", "destination.json"):
            (WORK / "olake" / name).unlink(missing_ok=True)
    return 0 if result["status"] in ("success", "incomplete") else 1


if __name__ == "__main__":
    sys.exit(main())
