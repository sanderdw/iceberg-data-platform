"""OLake's configuration files from a job, and its answers read back.

OLake takes three files: the source connection, the destination (the Iceberg REST catalog) and the
streams to sync. Only checked job fields reach them (replication/validate.py); the run adds the
password and the catalog token, which never leave the run container.
"""

import json
import re

ANSI = re.compile(r"\x1b\[[0-9;]*m")
# OLake reads PostgreSQL's TLS modes under these names.
TLS = {"disable": "disable", "require": "require", "verify-ca": "verify-ca", "verify-full": "verify-full"}
# How long OLake waits for the first change before it ends a change data capture run, in seconds.
INITIAL_WAIT = 120
# Verified TLS without a CA certificate of the source's own trusts the public certificate authorities.
SYSTEM_CA = "/etc/ssl/certs/ca-certificates.crt"


def ssl_config(source, system_ca=SYSTEM_CA):
    """OLake needs a CA for verify-ca and verify-full: the source's own, or the public authorities."""
    mode = TLS[source.get("tls", "verify-full")]
    ssl = {"mode": mode}
    if mode in ("verify-ca", "verify-full"):
        if source.get("caCertificate"):
            ssl["server_ca"] = source["caCertificate"]
        else:
            ssl.update(cert_source="path", server_ca_path=system_ca)
    return ssl


def source_config(job, secrets, preserve_toast=False):
    source = job["source"]
    config = {
        "host": source["host"], "port": source["port"], "database": source["database"],
        "username": source["username"], "password": secrets.get(source["password"].removeprefix("$secret:"), ""),
        "ssl": ssl_config(source), "max_threads": source.get("maxThreads", 4), "retry_count": 2,
    }
    if source.get("slot") and source.get("publication"):
        # Without REPLICA IDENTITY FULL, an update leaves unchanged large (TOAST) values out of the change; with
        # preserve_toast_values OLake reads them back from the row an upsert replaces. It then allows only upserts,
        # so the sync turns it on only when no chosen table is append-only (see append_only).
        config["update_method"] = {"replication_slot": source["slot"], "publication": source["publication"],
                                   "initial_wait_time": INITIAL_WAIT, "preserve_toast_values": preserve_toast}
    return config


def destination_config(job, token, catalog_url, jar="/home/olake-iceberg-java-writer.jar"):
    return {"type": "ICEBERG", "writer": {
        "sink_jar_path": jar,
        "catalog_type": "rest", "rest_catalog_url": catalog_url, "iceberg_s3_path": job["warehouse"],
        "iceberg_db": job["namespace"], "token": token, "oauth2_uri": f"{catalog_url}/v1/oauth/tokens",
        "rest_auth_type": "oauth2", "rest_access_delegation": "vended-credentials",
        "s3_endpoint": job["storageEndpoint"], "s3_path_style": True, "aws_region": "us-east-1",
        "arrow_writes": True,
    }}


def available(path):
    """The streams OLake discovered (its available_streams.json): name -> its description."""
    return available_from(json.loads(path.read_text()))


def available_from(document):
    found = {}
    for entry in document.get("streams", []):
        stream = entry.get("stream") or {}
        found[f"{stream.get('namespace')}.{stream.get('name')}"] = stream
    return found


def describe(streams):
    """What a source check reports about each table."""
    return [{"name": name, "primaryKey": sorted(s.get("source_defined_primary_key") or []),
             "cursorFields": sorted(s.get("available_cursor_fields") or []),
             "modes": sorted(s.get("supported_sync_modes") or []),
             "columns": len((s.get("type_schema") or {}).get("properties") or {})}
            for name, s in sorted(streams.items())]


def selected_streams(job, streams):
    """The job's tables as OLake's selected_streams.json. Raises if a table is not in the source."""
    missing = sorted(set(job["tables"]) - set(streams))
    if missing:
        raise RuntimeError(f"The source has no table {', '.join(missing)}, or the account cannot read it, or it is "
                           "not in the publication.")
    # "Copy everything again" copies the existing rows whatever the first run did.
    first_copy = job["options"].get("firstRun", "copy") == "copy" or bool(job.get("fullRefresh"))
    selected = {}
    for name, spec in sorted(job["tables"].items()):
        schema, table = name.split(".", 1)
        stream = streams[name]
        mode = spec["mode"]
        if mode == "cdc" and not first_copy:
            mode = "strict_cdc"
        if mode not in (stream.get("supported_sync_modes") or []):
            raise RuntimeError(f"{name} cannot be synced with {spec['mode']}.")
        keyless = not stream.get("source_defined_primary_key")
        entry = {
            "stream_name": table, "sync_mode": mode, "partition_regex": spec.get("partition", ""),
            "normalization": True, "append_mode": bool(spec.get("appendOnly") or keyless),
            "update_type": job["options"].get("deleteMode", "pos"),
            "destination_database": job["namespace"], "destination_table": spec["table"],
        }
        if mode == "incremental":
            entry["cursor_field"] = spec["cursor"]
        selected.setdefault(schema, []).append(entry)
    return {"selected_streams": selected}


def append_only(selected):
    """The chosen tables OLake appends to: those without a primary key, or marked appendOnly."""
    return sorted(f"{schema}.{entry['stream_name']}" for schema, entries in selected["selected_streams"].items()
                  for entry in entries if entry["append_mode"])


def messages(text, key):
    """OLake's JSON messages of one kind (such as connectionStatus) in its log output."""
    found = []
    for line in ANSI.sub("", text).splitlines():
        start = line.find('{"' + key + '"')
        if start >= 0:
            try:
                found.append(json.loads(line[start:])[key])
            except ValueError:
                continue
    return found


def checks(status):
    """OLake's prerequisites, as the API shows them."""
    return [{"name": p.get("name", ""), "required": bool(p.get("required")), "passed": bool(p.get("passed")),
             "currentValue": p.get("current_value", ""), "recommendedValue": p.get("recommended_value", ""),
             "description": p.get("description", "")}
            for p in (status or {}).get("prerequisites") or []]


def first_copy_done(state):
    """Whether every table's first copy is complete: OLake keeps the chunks still to copy in its state."""
    return all(not ((s.get("state") or {}).get("chunks")) for s in (state or {}).get("streams") or [])


def position(state):
    """How far the run read the change log (PostgreSQL's LSN), from OLake's state."""
    value = (((state or {}).get("global") or {}).get("state") or {}).get("lsn")
    return str(value) if value else None


def records_read(text):
    match = re.findall(r"Total records read: (\d+)", ANSI.sub("", text))
    return int(match[-1]) if match else 0
