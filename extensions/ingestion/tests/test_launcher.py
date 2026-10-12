import base64
import io
import tarfile
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from launcher.app import Launcher, RunInput, create_app

TOKEN = "t" * 40
RUN = "run-" + "a" * 24


def archive():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo("job.json")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"{}"))
    return base64.b64encode(buffer.getvalue()).decode()


def docker_client():
    client = MagicMock()
    catalog, storage = MagicMock(name="catalog"), MagicMock(name="storage")
    client.containers.list.side_effect = lambda filters=None, **_: {
        "io.iceberg-platform.role=catalog": [catalog], "io.iceberg-platform.role=storage": [storage],
    }.get((filters or {}).get("label"), [])
    client.containers.create.return_value.put_archive.return_value = True
    client.containers.create.return_value.wait.return_value = {"StatusCode": 0}
    client.containers.create.return_value.get_archive.return_value = ([], {})
    client.containers.create.return_value.logs.return_value = b"log"
    return client, catalog, storage


def test_runs_are_isolated_with_fixed_flags(tmp_path):
    client, catalog, storage = docker_client()
    launcher = Launcher({"ARTIFACTS_DIR": str(tmp_path), "RUNNER_IMAGE": "runner:pinned"}, client)
    launcher.watch = MagicMock()
    data = RunInput(id=RUN, archive=archive(), token="polaris-token", secrets={"password": "s3cr3t"}, timeout=600)
    assert "s3cr3t" not in repr(data) and "polaris-token" not in repr(data)
    launcher.start(data)
    (internal, egress) = [c.kwargs for c in client.networks.create.call_args_list]
    assert internal["internal"] is True and internal["labels"]["io.iceberg-ingestion.run"] == RUN
    assert egress["internal"] is False and egress["labels"] == internal["labels"]
    assert [c.args[0] for c in client.networks.create.call_args_list] == [
        f"iceberg-ingestion-{RUN}", f"iceberg-ingestion-{RUN}-egress"]
    network = client.networks.create.return_value
    network.connect.assert_any_call(catalog, aliases=["polaris-control-plane"])
    network.connect.assert_any_call(storage, aliases=["rustfs"])
    network.connect.assert_any_call(client.containers.create.return_value)
    image = client.containers.create.call_args.args[0]
    flags = client.containers.create.call_args.kwargs
    assert image == "runner:pinned" and flags["network"] == network.name
    assert flags["read_only"] and flags["cap_drop"] == ["ALL"] and flags["user"] == "10001:10001"
    assert flags["security_opt"] == ["no-new-privileges:true"] and flags["pids_limit"] == 512
    assert flags["labels"]["io.iceberg-platform.extension"] == "ingestion"
    assert flags["environment"] == {"ING_TOKEN": "polaris-token", "ING_SECRETS": '{"password": "s3cr3t"}'}
    assert "volumes" not in flags and "privileged" not in flags and "extra_hosts" not in flags
    assert launcher.status(RUN)["state"] == "running" and "container" not in launcher.status(RUN)
    with pytest.raises(ValueError):
        launcher.start(RunInput(id=RUN, archive=archive(), token="x", timeout=600))


def test_only_the_launcher_can_allow_the_docker_host(tmp_path):
    client, *_ = docker_client()
    launcher = Launcher({"ARTIFACTS_DIR": str(tmp_path), "ING_ALLOW_PRIVATE_SOURCES": "true"}, client)
    launcher.watch = MagicMock()
    launcher.start(RunInput(id=RUN, archive=archive(), token="x", timeout=600))
    assert client.containers.create.call_args.kwargs["extra_hosts"] == {"host.docker.internal": "host-gateway"}
    with pytest.raises(ValidationError):
        RunInput(id=RUN, archive=archive(), token="x", timeout=600, extra_hosts={"a": "b"})


@pytest.mark.parametrize("secrets", [{"Password": "x"}, {"../x": "x"}, {"big": "x" * 70_000}])
def test_secret_names_and_sizes_are_checked(secrets):
    with pytest.raises(ValidationError):
        RunInput(id=RUN, archive=archive(), token="x", secrets=secrets, timeout=600)


def test_a_finished_run_removes_its_container_and_both_networks(tmp_path):
    client, *_ = docker_client()
    launcher = Launcher({"ARTIFACTS_DIR": str(tmp_path)}, client)
    internal, egress = MagicMock(), MagicMock()
    for network in (internal, egress):
        network.attrs = {"Containers": {"c1": {}}}
    container = client.containers.create.return_value
    container.get_archive.return_value = ([base64.b64decode(archive())], {})
    launcher.runs[RUN] = {"state": "running"}
    launcher.watch(RUN, container, (internal, egress), 600)
    container.remove.assert_called_with(force=True, v=True)
    for network in (internal, egress):
        network.disconnect.assert_called_with("c1", force=True)
        network.remove.assert_called()
    assert launcher.status(RUN)["exitCode"] == 0 and (tmp_path / RUN / "container.log").exists()


def test_missing_platform_services_leave_nothing_behind(tmp_path):
    client, *_ = docker_client()
    client.containers.list.side_effect = lambda **_: []
    launcher = Launcher({"ARTIFACTS_DIR": str(tmp_path)}, client)
    with pytest.raises(RuntimeError):
        launcher.start(RunInput(id=RUN, archive=archive(), token="x", timeout=600))
    client.networks.create.return_value.remove.assert_called()
    assert client.networks.create.call_count == 1 and not client.containers.create.called
    assert launcher.status(RUN) is None


def test_collect_keeps_only_regular_files(tmp_path):
    client, *_ = docker_client()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, kind in (("out/result.json", tarfile.REGTYPE), ("out/link", tarfile.SYMTYPE),
                           ("../escape", tarfile.REGTYPE)):
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
            info.size = 0 if kind == tarfile.SYMTYPE else 2
            tar.addfile(info, io.BytesIO(b"{}") if kind == tarfile.REGTYPE else None)
    container = MagicMock()
    container.get_archive.return_value = ([buffer.getvalue()], {})
    container.logs.return_value = b"log"
    Launcher({"ARTIFACTS_DIR": str(tmp_path)}, client).collect(RUN, container)
    assert (tmp_path / RUN / "out" / "result.json").exists()
    assert not (tmp_path / RUN / "out" / "link").exists() and not (tmp_path / "escape").exists()


def test_recover_removes_what_a_previous_launcher_left(tmp_path):
    client, *_ = docker_client()
    old = MagicMock()
    client.containers.list.side_effect = lambda all=False, filters=None, **_: [old] if all else []
    networks = [MagicMock(attrs={"Containers": {}}), MagicMock(attrs={"Containers": {}})]
    client.networks.list.return_value = networks
    Launcher({"ARTIFACTS_DIR": str(tmp_path)}, client).recover()
    old.remove.assert_called_with(force=True, v=True)
    assert client.networks.list.call_args.kwargs == {"filters": {"label": "io.iceberg-ingestion.run"}}
    assert all(n.remove.called for n in networks)


def test_logs_fall_back_to_the_runs_log(tmp_path):
    launcher = Launcher({"ARTIFACTS_DIR": str(tmp_path)}, docker_client()[0])
    (tmp_path / RUN / "out").mkdir(parents=True)
    (tmp_path / RUN / "out" / "run.log").write_text("loaded 3 rows")
    launcher.runs[RUN] = {"state": "finished"}
    assert launcher.logs(RUN, 100) == "loaded 3 rows" and launcher.logs("run-" + "b" * 24, 100) is None


def test_the_api_requires_the_launcher_token(tmp_path):
    launcher = MagicMock()
    launcher.status.return_value = {"state": "running"}
    with TestClient(create_app(launcher, {"LAUNCHER_TOKEN": TOKEN})) as client:
        assert client.get("/health").status_code == 200
        assert client.get(f"/runs/{RUN}").status_code == 401
        assert client.get(f"/runs/{RUN}", headers={"X-Launcher-Token": TOKEN}).json() == {"state": "running"}
        bad = client.post("/runs", headers={"X-Launcher-Token": TOKEN},
                          json={"id": "../../x", "archive": "", "token": "x", "timeout": 60})
        assert bad.status_code == 422
    with pytest.raises(RuntimeError):
        create_app(launcher, {"LAUNCHER_TOKEN": "short"})


def test_discard_removes_only_a_finished_runs_artifacts(tmp_path):
    launcher = Launcher({"ARTIFACTS_DIR": str(tmp_path)}, docker_client()[0])
    (tmp_path / RUN / "out").mkdir(parents=True)
    (tmp_path / "keep").mkdir()
    launcher.runs[RUN] = {"state": "running"}
    with pytest.raises(ValueError, match="not finished"):
        launcher.discard(RUN)
    launcher.runs[RUN] = {"state": "finished"}
    assert launcher.discard(RUN) and not (tmp_path / RUN).exists() and RUN not in launcher.runs
    assert launcher.discard(RUN) is False
    for bad in ("../keep", "keep", "run-" + "a" * 23 + "/"):
        with pytest.raises(ValueError, match="Invalid"):
            launcher.discard(bad)
    assert (tmp_path / "keep").exists()
    client = TestClient(create_app(launcher, {"LAUNCHER_TOKEN": TOKEN}))
    (tmp_path / RUN).mkdir()
    headers = {"X-Launcher-Token": TOKEN}
    assert client.delete(f"/runs/{RUN}/artifacts", headers=headers).json() == {"removed": True}
    assert client.delete(f"/runs/{RUN}/artifacts", headers=headers).status_code == 404
    assert client.delete(f"/runs/{RUN}/artifacts").status_code == 401
