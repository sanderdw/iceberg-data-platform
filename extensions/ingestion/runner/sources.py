"""Build a dlt source from a pipeline's declarative configuration.

The gateway validates the configuration; this only interprets it. Secret values arrive separately
and replace the `"$secret:<name>"` placeholders. Write dispositions, keys and partitions come from
the pipeline's resource hints, never from the source configuration.
"""

import os
import re
import ssl
from pathlib import Path
from urllib.parse import unquote, urlsplit

SECRET = re.compile(r"^\$secret:([a-z][a-z0-9_]{0,63})$")
FORMATS = {"csv", "jsonl", "parquet"}
CHUNK = 10_000
MAX_DOWNLOAD = int(os.environ.get("ING_MAX_DOWNLOAD_BYTES", str(2 * 1024**3)))


class SourceError(ValueError):
    pass


def substitute(value, secrets):
    if isinstance(value, dict):
        return {k: substitute(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, secrets) for v in value]
    if isinstance(value, str) and (match := SECRET.fullmatch(value)):
        if match.group(1) not in secrets:
            raise SourceError(f"The secret {match.group(1)!r} is not set on this pipeline.")
        return secrets[match.group(1)]
    return value


def build(source_type, config, secrets, work):
    config = substitute(config, secrets)
    if source_type == "rest_api":
        return rest_api(config)
    if source_type == "sql_database":
        return sql_database(config)
    if source_type == "filesystem":
        return filesystem(config, Path(work))
    raise SourceError(f"Unknown source type {source_type!r}.")


def rest_api(config):
    from dlt.sources.rest_api import rest_api_source

    if not config.get("resources"):
        raise SourceError("A REST API source needs at least one resource.")
    return list(rest_api_source(config, name="rest_api").resources.values())


def tls_args(dialect, mode):
    if mode in (None, "disable"):
        return {}
    context = ssl.create_default_context()
    if mode == "require":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif mode != "verify-full":
        raise SourceError(f"Unknown TLS mode {mode!r}.")
    return {"ssl_context": context} if dialect == "postgresql" else {"ssl": context}


def engine(config):
    import sqlalchemy as sa

    drivers = {"postgresql": "postgresql+pg8000", "mysql": "mysql+pymysql"}
    dialect = config.get("dialect")
    if dialect not in drivers:
        raise SourceError(f"Unknown database dialect {dialect!r}.")
    url = sa.engine.URL.create(drivers[dialect], username=config.get("username"), password=config.get("password"),
                               host=config["host"], port=int(config["port"]), database=config["database"])
    return sa.create_engine(url, connect_args=tls_args(dialect, config.get("tls")), pool_pre_ping=True)


def sql_database(config):
    import dlt
    from dlt.sources.sql_database import sql_database as source

    tables = config.get("tables") or []
    if not tables:
        raise SourceError("A SQL database source needs at least one table.")
    built = source(credentials=engine(config), schema=config.get("schema"),
                   table_names=[t["name"] for t in tables], backend="pyarrow",
                   chunk_size=int(config.get("chunk_size") or 50_000))
    resources = []
    for table in tables:
        resource = built.resources[table["name"]]
        if incremental := table.get("incremental"):
            resource.apply_hints(incremental=dlt.sources.incremental(
                incremental["cursor"], initial_value=incremental.get("initial_value")))
        resources.append(resource)
    return resources


def read_csv_arrow(items, delimiter=","):
    import pyarrow.csv as pcsv

    for item in items:
        with item.open() as file:
            reader = pcsv.open_csv(file, parse_options=pcsv.ParseOptions(delimiter=delimiter),
                                   read_options=pcsv.ReadOptions(block_size=8 * 1024 * 1024))
            for batch in reader:
                if batch.num_rows:
                    yield batch


def read_jsonl(items):
    import json

    for item in items:
        with item.open() as file:
            rows = []
            for line in file:
                if line.strip():
                    rows.append(json.loads(line))
                if len(rows) >= CHUNK:
                    yield rows
                    rows = []
            if rows:
                yield rows


def read_parquet(items):
    import pyarrow.parquet as pq

    for item in items:
        with item.open() as file:
            yield from pq.ParquetFile(file).iter_batches(batch_size=CHUNK * 5)


def download(url, target):
    import requests

    name = Path(unquote(urlsplit(url).path)).name or "download"
    target.mkdir(parents=True, exist_ok=True)
    size = 0
    with requests.get(url, stream=True, timeout=(30, 300)) as response:
        response.raise_for_status()
        with open(target / name, "wb") as file:
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise SourceError(f"The file is larger than {MAX_DOWNLOAD:,} bytes.")
                file.write(chunk)
    return name


def filesystem(config, work):
    import dlt
    from dlt.sources.filesystem import filesystem as files

    fmt, table, url = config.get("format"), config.get("table"), config.get("url", "")
    if fmt not in FORMATS:
        raise SourceError(f"Unknown file format {fmt!r}.")
    if not table:
        raise SourceError("A file source needs a table name.")
    scheme = urlsplit(url).scheme
    if scheme in ("http", "https"):
        name = download(url, work / "in")
        listing = files(bucket_url=(work / "in").as_uri(), file_glob=name)
    elif scheme == "s3":
        import s3fs

        s3 = config.get("s3") or {}
        fs = s3fs.S3FileSystem(key=s3.get("access_key_id"), secret=s3.get("secret_access_key"),
                               anon=not s3.get("access_key_id"), endpoint_url=s3.get("endpoint_url"),
                               client_kwargs={"region_name": s3["region"]} if s3.get("region") else None)
        incremental = dlt.sources.incremental("modification_date") if config.get("incremental") else None
        listing = files(bucket_url=url, credentials=fs, file_glob=config.get("file_glob") or "*",
                        incremental=incremental)
    else:
        raise SourceError("File sources must be https, http or s3 URLs.")
    reader = {"csv": lambda: dlt.transformer(read_csv_arrow, name=table)(
                  delimiter=(config.get("csv") or {}).get("delimiter", ",")),
              "jsonl": lambda: dlt.transformer(read_jsonl, name=table)(),
              "parquet": lambda: dlt.transformer(read_parquet, name=table)()}[fmt]()
    return [listing | reader]


def apply_hints(resources, hints, full_refresh=False):
    from dlt.destinations.adapters import iceberg_adapter

    applied = []
    for resource in resources:
        hint = hints.get(resource.name, {})
        disposition = "replace" if full_refresh else hint.get("writeDisposition", "append")
        if disposition == "merge":
            if not hint.get("primaryKey"):
                raise SourceError(f"Merging {resource.name} needs a primary key.")
            disposition = {"disposition": "merge", "strategy": "upsert"}
        resource.apply_hints(write_disposition=disposition, primary_key=hint.get("primaryKey") or None)
        if hint.get("partition"):
            resource = iceberg_adapter(resource, partition=hint["partition"])
        applied.append(resource)
    return applied
