"""Authenticated definitions and cancellable jobs for the workspace portal."""

import asyncio
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from uuid import uuid4

from fastapi import Request
from pydantic import Field, model_validator
from starlette.concurrency import run_in_threadpool

from server.models import ServiceError

from .definitions import Dashboard, Input, Report, report_sql
from .runtime import ReportRuntime
from .store import ReportStore


class SaveReport(Input):
    definition: Report
    revision: int | None = Field(default=None, ge=1)


class SaveDashboard(Input):
    definition: Dashboard
    revision: int | None = Field(default=None, ge=1)


class Revision(Input):
    revision: int = Field(ge=1)


class Run(Input):
    definition: Report | None = None
    report_id: str | None = None
    dashboard_id: str | None = None
    filter_value: str | None = Field(default=None, max_length=512)
    refresh: bool = False

    @model_validator(mode="after")
    def one_target(self):
        if sum(x is not None for x in (self.definition, self.report_id, self.dashboard_id)) != 1:
            raise ValueError("Select one report or dashboard to run.")
        return self


class Reports:
    def __init__(self, directory, sessions, lock, *, store=None, runner=None):
        self.directory, self.sessions, self.lock = directory, sessions, lock
        self.store = store or ReportStore(os.environ.get("USER_REPORTS_PATH", ".data/reports.sqlite"))
        self.runner = runner
        self.jobs = OrderedDict()
        self.cache = OrderedDict()

    def role(self, session):
        profile = self.directory.profile(session)
        role = next((t["role"] for t in profile["teams"] if t["id"] == session.team), None)
        if not role:
            raise ServiceError(403, "You no longer have access to this team.")
        return role

    def current(self, job):
        session = self.sessions.get(job["session"])
        if (
            not session
            or session.expires <= time.monotonic()
            or (session.team, session.environment) != job["context"]
        ):
            raise ServiceError(403, "Your workspace changed or session expired. Run the report again.")
        return session

    def validate_report(self, session, definition):
        source = definition.source
        self.directory.details(session, source.database, source.namespace, "table", source.table)
        try:
            return report_sql(definition)
        except ValueError as error:
            raise ServiceError(422, str(error)) from None

    def cancel_session(self, identifier):
        for job in self.jobs.values():
            if job["session"] == identifier:
                job["cancel"].set()
                job["cards"] = []
                job["status"] = "cancelled"
        for key, item in list(self.cache.items()):
            if item["session"] == identifier:
                self.cache.pop(key)

    def prune(self):
        now = time.monotonic()
        for key, item in list(self.cache.items()):
            if item["until"] < now:
                self.cache.pop(key)
        while sum(c["size"] for c in self.cache.values()) > 16_000_000:
            self.cache.popitem(last=False)
        for key, job in list(self.jobs.items()):
            if job["task"].done() and now - job["created"] > 120:
                self.jobs.pop(key)
        size = sum(j.get("size", 0) for j in self.jobs.values())
        for key, job in list(self.jobs.items()):
            if job["task"].done() and (len(self.jobs) > 16 or size > 32_000_000):
                size -= job.get("size", 0)
                self.jobs.pop(key)

    def prepare(self, session, definition, pinned):
        source = definition.source
        key = (source.database, tuple(source.namespace), source.table)
        details = self.directory.details(session, source.database, source.namespace, "table", source.table)
        original = pinned.setdefault(key, details)
        if (original["uuid"], original["schemaId"]) != (details["uuid"], details["schemaId"]):
            raise ServiceError(409, "The table changed during dashboard refresh. Refresh again.")
        return {
            "database": source.database,
            "namespace": source.namespace,
            "table": source.table,
            "uri": self.directory.url + "/api/catalog",
            "s3Endpoint": self.directory.s3_endpoint,
            "token": session.token,
            "snapshotId": original["currentSnapshotId"],
            "tableUuid": original["uuid"],
            "schemaId": original["schemaId"],
        }

    def authorize_card(self, job, card):
        session = self.current(job)
        source = card["definition"].source
        details = self.directory.details(session, source.database, source.namespace, "table", source.table)
        if card.get("result"):
            data = card["result"]["data"]
            if (data["tableUuid"], data["schemaId"]) != (details["uuid"], details["schemaId"]):
                raise ServiceError(409, "The table changed. Refresh this dashboard.")

    async def execute(self, job, refresh, filter_value):
        try:
            # Resolve every dependency before execution so a shared table has the
            # same snapshot in all cards, including cards with a cached result.
            prepared, pinned = [], {}
            async with self.lock:
                session = self.current(job)
                for card in job["cards"]:
                    try:
                        source = await run_in_threadpool(self.prepare, session, card["definition"], pinned)
                        prepared.append((card, source))
                    except ServiceError as error:
                        card["error"] = str(error)
            for card, source in prepared:
                if job["cancel"].is_set():
                    break
                request = {
                    "source": source,
                    "definition": card["definition"].model_dump(),
                    "filterColumn": card.get("filter_column", ""),
                    "filterValue": filter_value,
                }
                key = hashlib.sha256(
                    json.dumps(
                        [
                            job["session"],
                            job["context"],
                            {**request, "source": {k: v for k, v in source.items() if k != "token"}},
                        ],
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                try:
                    cached = self.cache.get(key)
                    hit = not refresh and cached is not None and cached["until"] > time.monotonic()
                    if hit:
                        result = deepcopy(cached["result"])
                    else:
                        async with self.lock:
                            if self.runner is None:
                                self.runner = ReportRuntime(os.environ)
                                await run_in_threadpool(self.runner.recover)
                        result = await run_in_threadpool(self.runner.run, request, job["cancel"])
                    async with self.lock:
                        if job["cancel"].is_set():
                            break
                        card["result"] = result
                        await run_in_threadpool(self.authorize_card, job, card)
                        size = len(json.dumps(result).encode())
                        if job["size"] + size > 8_000_000:
                            raise ServiceError(
                                422, "Dashboard result is too large. Reduce rows or split the dashboard."
                            )
                        job["size"] += size
                        if not hit:
                            self.cache[key] = {
                                "session": job["session"],
                                "until": time.monotonic() + 60,
                                "result": deepcopy(result),
                                "size": size,
                            }
                        result["cached"] = hit
                        result["executedAt"] = (
                            cached["result"].get("executedAt", time.time()) if hit else time.time()
                        )
                        if not hit:
                            self.cache[key]["result"]["executedAt"] = result["executedAt"]
                        self.prune()
                except Exception as error:  # noqa: BLE001 - No provider bodies or SQL/credentials escape.
                    card.pop("result", None)
                    card["error"] = (
                        str(error)
                        if isinstance(error, ServiceError)
                        else "Report could not run. Check the reporting image and try again."
                    )
            job["status"] = "cancelled" if job["cancel"].is_set() else "done"
        except Exception:  # noqa: BLE001 - Session/authorization changes discard all results.
            job["cancel"].set()
            job["cards"] = []
            job["status"] = "cancelled"

    async def start(self, session, data):
        await run_in_threadpool(self.role, session)
        self.prune()
        active = [j for j in self.jobs.values() if not j["task"].done()]
        if len(active) >= 4 or sum(j["session"] == session.id for j in active) >= 2:
            raise ServiceError(429, "Reporting slots are busy. Cancel a run or try again shortly.")
        cards = []
        if data.dashboard_id:
            dashboard = self.store.get(session, data.dashboard_id, "dashboard")
            for entry in dashboard["body"]["cards"]:
                report = self.store.get(session, entry["report_id"], "report")
                cards.append(
                    {
                        **entry,
                        "revision": report["revision"],
                        "definition": Report.model_validate(report["body"]),
                    }
                )
        elif data.report_id:
            report = self.store.get(session, data.report_id, "report")
            cards = [
                {
                    "report_id": report["id"],
                    "revision": report["revision"],
                    "definition": Report.model_validate(report["body"]),
                }
            ]
        else:
            await run_in_threadpool(self.validate_report, session, data.definition)
            cards = [{"definition": data.definition}]
        identifier = uuid4().hex
        job = {
            "id": identifier,
            "session": session.id,
            "context": (session.team, session.environment),
            "cards": cards,
            "cancel": threading.Event(),
            "status": "running",
            "created": time.monotonic(),
            "size": 0,
        }
        self.jobs[identifier] = job
        job["task"] = asyncio.create_task(self.execute(job, data.refresh, data.filter_value))
        return {"id": identifier, "status": "running"}

    def get_job(self, session, identifier):
        self.prune()
        job = self.jobs.get(identifier)
        if not job or job["session"] != session.id or job["context"] != (session.team, session.environment):
            raise ServiceError(404, "This report run is unavailable.")
        self.role(session)
        self.current(job)
        return job

    async def close(self):
        for job in self.jobs.values():
            job["cancel"].set()
        await asyncio.gather(*(j["task"] for j in self.jobs.values()), return_exceptions=True)
        if self.runner is not None:
            await run_in_threadpool(self.runner.close)
        self.store.close()


def install(app, reports):
    @app.get("/api/reports")
    def listing(request: Request):
        reports.role(request.state.session)
        return {"items": reports.store.list(request.state.session)}

    @app.get("/api/reports/{identifier}")
    def get(identifier: str, request: Request):
        reports.role(request.state.session)
        return reports.store.get(request.state.session, identifier)

    def save(data, request, kind, identifier=None):
        session = request.state.session
        role = reports.role(session)
        if kind == "report":
            reports.validate_report(session, data.definition)
        return reports.store.save(
            session, role, kind, data.definition.model_dump(), identifier, data.revision
        )

    @app.post("/api/reports", status_code=201)
    def create(data: SaveReport, request: Request):
        return save(data, request, "report")

    @app.put("/api/reports/{identifier}")
    def update(identifier: str, data: SaveReport, request: Request):
        return save(data, request, "report", identifier)

    @app.post("/api/dashboards", status_code=201)
    def create_dashboard(data: SaveDashboard, request: Request):
        return save(data, request, "dashboard")

    @app.put("/api/dashboards/{identifier}")
    def update_dashboard(identifier: str, data: SaveDashboard, request: Request):
        return save(data, request, "dashboard", identifier)

    @app.delete("/api/reports/{identifier}")
    def delete(identifier: str, data: Revision, request: Request):
        session = request.state.session
        reports.store.delete(session, reports.role(session), identifier, data.revision)
        return {"deleted": True}

    @app.post("/api/report-sql")
    def sql(data: SaveReport, request: Request):
        query, parameters = reports.validate_report(request.state.session, data.definition)
        return {"sql": query, "parameters": parameters}

    @app.post("/api/report-jobs", status_code=202)
    async def start(data: Run, request: Request):
        return await reports.start(request.state.session, data)

    @app.get("/api/report-jobs/{identifier}")
    def poll(identifier: str, request: Request):
        job = reports.get_job(request.state.session, identifier)
        cards = []
        if job["status"] == "done":
            for card in job["cards"]:
                result = {
                    "name": card["definition"].name,
                    "width": card.get("width", "full"),
                    "reportId": card.get("report_id"),
                    "revision": card.get("revision"),
                }
                try:
                    reports.authorize_card(job, card)
                    result.update({"error": card["error"]} if "error" in card else {"result": card["result"]})
                except ServiceError:
                    card.pop("result", None)
                    card["error"] = "Data access changed. Refresh the report with your current permissions."
                    result["error"] = card["error"]
                cards.append(result)
        return {"id": identifier, "status": job["status"], "cards": cards}

    @app.delete("/api/report-jobs/{identifier}")
    def cancel(identifier: str, request: Request):
        job = reports.get_job(request.state.session, identifier)
        job["cancel"].set()
        job["cards"] = []
        job["status"] = "cancelled"
        return {"status": "cancelled"}
