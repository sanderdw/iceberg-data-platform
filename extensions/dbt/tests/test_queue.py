"""The run queue: fair between teams, one build per project and environment, bounded artifacts."""

import asyncio
import dataclasses
import json

import httpx

from dbt_portal.runs import RunManager
from dbt_portal.store import Store
from tests.conftest import settings


def manager(tmp_path, **options):
    deleted = []

    def launcher(request):
        deleted.append(request.url.path.split("/")[2])
        return httpx.Response(200, json={"removed": True})

    config = dataclasses.replace(settings(tmp_path), **options)
    store = Store(tmp_path / "state.db")
    http = httpx.AsyncClient(base_url="http://launcher", transport=httpx.MockTransport(launcher))
    return RunManager(config, store, None, None, http=http), store, deleted


def test_waiting_builds_never_hold_a_slot_another_team_needs(tmp_path):
    runs, _, _ = manager(tmp_path, max_runs=2, max_runs_per_team=1)
    started, release = [], asyncio.Event()

    async def launch(run, project, principal, args):
        started.append(run["id"])
        await release.wait()

    runs.launch = launch

    async def scenario():
        shop, stock = {"id": "prj-shop", "team": "a"}, {"id": "prj-stock", "team": "a"}
        other = {"id": "prj-x", "team": "b"}
        build = {"environment": "development", "access": "write"}
        tasks = [asyncio.create_task(runs.execute({**build, "id": f"shop-{i}"}, shop, {}, [])) for i in range(3)]
        tasks.append(asyncio.create_task(runs.execute({**build, "id": "stock"}, stock, {}, [])))
        tasks.append(asyncio.create_task(runs.execute({**build, "id": "other"}, other, {}, [])))
        await asyncio.sleep(0.05)
        # Team a runs one build at a time; queued builds of shop and stock leave the second slot to team b.
        assert started == ["shop-0", "other"]
        release.set()
        await asyncio.gather(*tasks)
        assert sorted(started) == ["other", "shop-0", "shop-1", "shop-2", "stock"]

    asyncio.run(scenario())


def test_old_artifacts_go_but_the_pipeline_and_docs_runs_stay(tmp_path):
    runs, store, deleted = manager(tmp_path, run_retention=5)
    store.execute("INSERT INTO projects (id, team, name, description, default_database, created_by, created_at) "
                  "VALUES ('prj-1', 'a', 'shop', '', 'sales', 'bob', 0)")
    created = []
    for i in range(12):
        command = "docs" if i == 1 else "build"
        summary = {"infoSchema": i == 2}
        run = store.create_run(project="prj-1", environment="development", command=command, selector="", ref="main",
                               revision="r", access="write", triggered_by="bob")
        store.update_run(run["id"], status="success" if i != 2 else "failed", created_at=i, summary=json.dumps(summary))
        (runs.artifacts / run["id"]).mkdir(parents=True)
        created.append(run["id"])
    # The newest run is still going: never touched.
    store.update_run(created[11], status="running")
    asyncio.run(runs.prune("prj-1", "development"))
    # Kept: the five newest finished runs, the run the pipeline is drawn from and the newest docs.
    assert sorted(deleted) == sorted(created[i] for i in (0, 3, 4, 5))
