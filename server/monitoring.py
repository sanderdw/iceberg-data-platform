"""Internal, cached monitoring service. Docker access never reaches the portal.

Each source has its own polling loop; a failed source preserves its last successful
sample with an explicit unavailable status. No exception bodies or credentials are
returned. History is bounded, in memory, and represents probes, not API traffic.
"""

import asyncio
import copy
import hmac
import json
import logging
import os
import shutil
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row

from .polaris import PolarisProvider
from .storage import RustFSStorage


def timestamp():
    return datetime.now(UTC).isoformat()


def container_usage(stats):
    current, previous = stats.get("cpu_stats", {}), stats.get("precpu_stats", {})
    cpu = current.get("cpu_usage", {}).get("total_usage", 0)
    before = previous.get("cpu_usage", {}).get("total_usage", 0)
    system_delta = current.get("system_cpu_usage", 0) - previous.get("system_cpu_usage", 0)
    cores = current.get("online_cpus") or len(current.get("cpu_usage", {}).get("percpu_usage", []))
    percent = None
    if previous.get("system_cpu_usage") and system_delta > 0 and cpu >= before and cores:
        percent = round((cpu - before) / system_delta * cores * 100, 2)
    memory = stats.get("memory_stats", {})
    cache = memory.get("stats", {})
    inactive = cache.get("total_inactive_file", cache.get("inactive_file", 0))
    return {
        "cpuPercent": percent,
        "memoryBytes": max(0, memory["usage"] - inactive) if "usage" in memory else None,
        "memoryLimitBytes": memory.get("limit"),
    }


def bucket_usage(storage, bucket, deadline, max_pages=1000):
    size, count, token = 0, 0, None
    for _ in range(max_pages):
        if time.monotonic() >= deadline:
            raise TimeoutError("Bucket scan budget exhausted")
        page = storage.call(
            "list_objects_v2", Bucket=bucket, MaxKeys=1000, **({"ContinuationToken": token} if token else {})
        )
        objects = page.get("Contents", [])
        size += sum(obj["Size"] for obj in objects)
        count += len(objects)
        if not page.get("IsTruncated"):
            return {"bytes": size, "objects": count}
        next_token = page.get("NextContinuationToken")
        if not next_token or next_token == token:
            raise ValueError("Invalid storage pagination")
        token = next_token
    raise TimeoutError("Bucket scan page budget exhausted")


class Monitor:
    def __init__(self, env):
        self.env = env
        self.samples = {}
        self.history = {}
        self.previous_transactions = None
        self.http = httpx.Client(timeout=4, trust_env=False)
        self.docker = httpx.Client(
            transport=httpx.HTTPTransport(uds=env.get("MONITOR_DOCKER_SOCKET", "/var/run/docker.sock")),
            base_url="http://docker",
            timeout=5,
        )
        self.provider = PolarisProvider(env, RustFSStorage(env))

    def close(self):
        self.http.close()
        self.docker.close()
        self.provider.close()

    async def poll(self, key, collect, interval):
        while True:
            try:
                data = await asyncio.to_thread(collect)
                self.samples[key] = {"status": "online", "updatedAt": timestamp(), "data": data}
            except Exception as exc:  # noqa: BLE001 -- isolate and sanitize collector failures
                # Collector failures are isolated; never expose upstream error bodies.
                logging.getLogger(__name__).warning("%s collection failed: %s", key, type(exc).__name__)
                old = self.samples.get(key, {})
                self.samples[key] = {**old, "status": "unavailable", "checkedAt": timestamp()}
            await asyncio.sleep(interval)

    def snapshot(self):
        return {
            "sampledAt": timestamp(),
            "refreshSeconds": 15,
            **{
                key: copy.deepcopy(self.samples.get(key, {"status": "collecting"}))
                for key in ("services", "containers", "postgres", "storage", "disk")
            },
        }

    def services(self):
        endpoints = [
            (
                "portal",
                "Admin portal",
                self.env.get("MONITOR_PORTAL_URL", "http://portal:3000") + "/api/health",
            ),
            (
                "polaris",
                "Apache Polaris",
                self.env.get("MONITOR_POLARIS_URL", "http://polaris:8182") + "/q/health",
            ),
            ("rustfs", "RustFS", self.env.get("S3_ADMIN_ENDPOINT", "http://rustfs:9000") + "/health"),
        ]
        if self.env.get("MONITOR_USERS_URL"):
            endpoints.append(("users", "User portal", self.env["MONITOR_USERS_URL"] + "/api/health"))
        rows = []
        for key, name, url in endpoints:
            start = time.monotonic()
            code = None
            try:
                response = self.http.get(url)
                code = response.status_code
                online = response.is_success
            except httpx.HTTPError:
                online = False
            elapsed = round((time.monotonic() - start) * 1000, 1)
            history = self.history.setdefault(key, deque(maxlen=60))
            history.append({"at": timestamp(), "ok": online, "latencyMs": elapsed if online else None})
            rows.append(
                {
                    "id": key,
                    "name": name,
                    "status": "online" if online else "offline",
                    "httpStatus": code,
                    "latencyMs": elapsed if online else None,
                    "checkedAt": history[-1]["at"],
                    "samples": len(history),
                    "availabilityPercent": round(sum(p["ok"] for p in history) / len(history) * 100, 1),
                    "history": list(history),
                }
            )
        return rows

    def docker_get(self, path, **params):
        response = self.docker.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def containers(self):
        projects = self.env.get("MONITOR_PROJECTS", "iceberg-platform,iceberg-workspaces").split(",")
        rows = []
        for project in projects:
            containers = self.docker_get(
                "/containers/json",
                all="true",
                filters=json.dumps({"label": [f"com.docker.compose.project={project.strip()}"]}),
            )
            for container in containers:
                labels = container.get("Labels", {})
                if labels.get("com.docker.compose.service") in ("polaris-bootstrap", "notebook-image"):
                    continue
                row = {
                    "name": container["Names"][0].lstrip("/"),
                    "state": container["State"],
                    "service": labels.get("com.docker.compose.service", "notebook"),
                }
                try:
                    details = self.docker_get(f"/containers/{container['Id']}/json")
                    row.update(restarts=details["RestartCount"], startedAt=details["State"]["StartedAt"])
                    if container["State"] == "running":
                        row.update(
                            container_usage(
                                self.docker_get(f"/containers/{container['Id']}/stats", stream="false")
                            )
                        )
                    row["status"] = "online"
                except httpx.HTTPError, KeyError, ValueError:
                    row["status"] = "unavailable"
                rows.append(row)
        return sorted(rows, key=lambda row: row["name"])

    def postgres(self):
        start = time.monotonic()
        with psycopg.connect(
            host=self.env.get("PGHOST", "postgres"),
            dbname=self.env.get("PGDATABASE", "polaris"),
            user=self.env.get("PGUSER", "polaris"),
            password=self.env.get("POSTGRES_PASSWORD", ""),
            connect_timeout=4,
            options="-c default_transaction_read_only=on -c statement_timeout=3000",
            row_factory=dict_row,
        ) as conn:
            row = conn.execute("""
                SELECT pg_database_size(current_database()) AS bytes,
                       (SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'
                         AND pid <> pg_backend_pid()) AS connections,
                       (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()
                         AND state = 'active' AND pid <> pg_backend_pid()) AS active,
                       (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()
                         AND state = 'idle' AND pid <> pg_backend_pid()) AS idle,
                       (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()
                         AND cardinality(pg_blocking_pids(pid)) > 0) AS blocked,
                       current_setting('max_connections')::int AS "maxConnections",
                       xact_commit + xact_rollback AS transactions, deadlocks,
                       stats_reset::text AS "statsReset"
                FROM pg_stat_database WHERE datname = current_database()
            """).fetchone()
        now = time.monotonic()
        row["latencyMs"] = round((now - start) * 1000, 1)
        row["transactionsPerSecond"] = None
        previous = self.previous_transactions
        if previous and previous[2] == row["statsReset"] and row["transactions"] >= previous[1]:
            row["transactionsPerSecond"] = round((row["transactions"] - previous[1]) / (now - previous[0]), 2)
        self.previous_transactions = (now, row["transactions"], row["statsReset"])
        return row

    def storage(self):
        deadline = time.monotonic() + 45
        databases = self.provider.list_databases()
        rows = []
        for database in databases:
            row = {"bucket": database["bucket"], "database": database["name"], "team": database["team"]}
            try:
                row.update(bucket_usage(self.provider.storage, database["bucket"], deadline))
                row.update(status="online", updatedAt=timestamp())
            except Exception:  # noqa: BLE001 -- incomplete scans must never become zero totals
                row["status"] = "unavailable"
            rows.append(row)
        complete = all(row["status"] == "online" for row in rows)
        return {
            "buckets": rows,
            "complete": complete,
            "bytes": sum(row["bytes"] for row in rows) if complete else None,
            "objects": sum(row["objects"] for row in rows) if complete else None,
        }

    def disk(self):
        usage = shutil.disk_usage(self.env.get("MONITOR_STORAGE_PATH", "/storage"))
        return {"totalBytes": usage.total, "usedBytes": usage.used, "freeBytes": usage.free}


def create_monitor_app():
    secret = os.environ.get("PORTAL_PASSWORD", "")
    if len(secret) < 16:
        raise RuntimeError("Set PORTAL_PASSWORD for the internal monitoring service.")
    monitor = Monitor(os.environ)

    @asynccontextmanager
    async def lifespan(app):
        tasks = [
            asyncio.create_task(monitor.poll(key, getattr(monitor, key), interval))
            for key, interval in (
                ("services", 15),
                ("containers", 15),
                ("postgres", 15),
                ("storage", 300),
                ("disk", 15),
            )
        ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            monitor.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/snapshot")
    def snapshot(request: Request):
        if not hmac.compare_digest(request.headers.get("x-monitor-token", "").encode(), secret.encode()):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return JSONResponse(monitor.snapshot(), headers={"Cache-Control": "no-store"})

    return app
