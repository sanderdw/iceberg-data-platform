"""One dlt pipeline run in an isolated run container.

The launcher stages /work with `job.json` and the pipeline's previous dlt state. This loads the
source into the team's database with dlt's filesystem destination in Iceberg format: dlt's own
bookkeeping (`_dlt_*` tables, load packages) stays on the run's local disk, and Polaris places
every table under the database's location. The new state goes back to the gateway in /work/out,
so runs stay stateless. The catalog token arrives in ING_TOKEN and the source's credentials in
ING_SECRETS; both are scrubbed from everything the run writes.
"""

import json
import os
import shutil
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from runner import guard
from runner.scrub import Scrubber

WORK = Path(os.environ.get("RUNNER_WORK", "/work"))
MAX_LOG = 5_000_000


class Tee:
    def __init__(self, stream, log, scrub):
        self.stream, self.log, self.scrub, self.written = stream, log, scrub, 0

    def write(self, text):
        text = self.scrub(text)
        self.stream.write(text)
        if self.written < MAX_LOG:
            self.log.write(text[: MAX_LOG - self.written])
            self.written += len(text)
        return len(text)

    def flush(self):
        self.stream.flush()
        self.log.flush()

    def isatty(self):
        return False


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def configure(job, token):
    catalog = {"type": "rest", "uri": job["catalogUri"], "warehouse": job["warehouse"], "token": token,
               "header.X-Iceberg-Access-Delegation": "vended-credentials",
               "py-io-impl": "runner.iceberg_io.InternalS3FileIO"}
    os.environ.update({
        "DESTINATION__FILESYSTEM__BUCKET_URL": (WORK / "dlt").as_uri(),
        "ICEBERG_CATALOG__ICEBERG_CATALOG_TYPE": "rest",
        "ICEBERG_CATALOG__ICEBERG_CATALOG_CONFIG": json.dumps(catalog),
        "ICEBERG_S3_ENDPOINT": job["storageEndpoint"],
        "RESTORE_FROM_DESTINATION": "false",
        "RUNTIME__DLTHUB_TELEMETRY": "false",
    })
    return catalog


def catalog_location():
    """Let Polaris place new tables under the database's location instead of dlt's local bucket_url."""
    import dlt.common.libs.pyiceberg as dlt_iceberg

    create = dlt_iceberg.create_table
    if getattr(create, "platform", False):
        return

    def create_table(catalog, table_id, table_location, *args, **kwargs):
        return create(catalog, table_id, None, *args, **kwargs)

    create_table.platform = True
    dlt_iceberg.create_table = create_table


def platform_source(name, resources):
    """The resources as one source named after the pipeline (so its schema keeps that name).

    Child tables of nested lists always carry `_dlt_root_id`: merge needs it, and a table must keep
    its columns when a full refresh replaces what an earlier merge loaded.
    """
    import dlt

    @dlt.source(name=name, root_key=True)
    def source():
        return resources

    return source()


def pipeline_name(job):
    return job["pipelineId"].replace("-", "_")


def restore(job, folder):
    state = WORK / "state"
    if job.get("fullRefresh") or not (state / "state.json").exists():
        return False
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(state / "state.json", folder / "state.json")
    if (state / "schemas").is_dir():
        shutil.copytree(state / "schemas", folder / "schemas", dirs_exist_ok=True)
    return True


def keep(folder, out):
    target = out / "state"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    shutil.copyfile(folder / "state.json", target / "state.json")
    if (folder / "schemas").is_dir():
        shutil.copytree(folder / "schemas", target / "schemas")


def loaded_tables(pipeline, resources):
    """Rows per table of this load, plus each resource's table when it loaded nothing new."""
    from dlt.common.schema.utils import get_root_table

    counts = pipeline.last_trace.last_normalize_info.row_counts if pipeline.last_trace.last_normalize_info else {}
    schema = pipeline.default_schema
    tables = {name: rows for name, rows in counts.items() if not name.startswith("_dlt")}
    known = set(schema.data_table_names(seen_data_only=True))
    for resource in resources:
        if resource.name in known:
            tables.setdefault(resource.name, 0)
    roots = {name: get_root_table(schema.tables, name)["name"] for name in tables if name in schema.tables}
    return tables, roots


def run(job, secrets, token, out):
    configure(job, token)
    import dlt
    from pyiceberg.catalog import load_catalog

    from runner import publish, sources

    catalog_location()
    name = pipeline_name(job)
    pipelines = WORK / "pipelines"
    restored = restore(job, pipelines / name)
    print(f"Pipeline {job.get('pipelineName', name)}: {job['sourceType']} into {job['namespace']}"
          f"{' (continuing from the previous state)' if restored else ''}", flush=True)
    resources = sources.apply_hints(sources.build(job["sourceType"], job["source"], secrets, WORK),
                                    job.get("resources") or {}, job.get("fullRefresh", False))
    pipeline = dlt.pipeline(name, destination="filesystem", dataset_name=job["namespace"],
                            pipelines_dir=str(pipelines))
    info = pipeline.run(platform_source(name, resources), table_format="iceberg")
    print(info, flush=True)
    load_id = info.loads_ids[-1] if info.loads_ids else None
    tables, roots = loaded_tables(pipeline, resources)
    published = {"tables": [], "errors": []}
    if tables:
        catalog = load_catalog(f"platform-{job['warehouse']}", **json.loads(
            os.environ["ICEBERG_CATALOG__ICEBERG_CATALOG_CONFIG"]))
        published = publish.main(job, catalog, tables, load_id or job["runId"], dlt.__version__, pipeline.state,
                                 roots)
    keep(pipelines / name, out)
    return {"loadId": load_id, "dltVersion": dlt.__version__,
            "tables": [{"name": n, "rows": r} for n, r in sorted(tables.items())], "published": published}


def main():
    out = WORK / "out"
    out.mkdir(parents=True, exist_ok=True)
    token = os.environ.pop("ING_TOKEN", "")
    secrets = json.loads(os.environ.pop("ING_SECRETS", "") or "{}")
    scrub = Scrubber([token, *secrets.values()])
    result = {"status": "failed", "startedAt": now(), "loadId": None, "dltVersion": None, "tables": [],
              "published": {"tables": [], "errors": []}, "error": None}
    with open(out / "run.log", "w", encoding="utf-8") as log:
        sys.stdout, sys.stderr = Tee(sys.__stdout__, log, scrub), Tee(sys.__stderr__, log, scrub)
        try:
            job = json.loads((WORK / "job.json").read_text())
            guard.install(bool(job.get("allowPrivate")))
            result.update(run(job, secrets, token, out), status="success")
        except Exception as exc:  # noqa: BLE001 - Any failure ends the run with a scrubbed message.
            traceback.print_exc()
            result["error"] = scrub(f"{type(exc).__name__}: {exc}")[:4000]
        finally:
            result["finishedAt"] = now()
            (out / "result.json").write_text(json.dumps(result, indent=2, default=str))
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
