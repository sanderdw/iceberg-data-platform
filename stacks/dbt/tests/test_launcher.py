import base64
import io
import tarfile
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

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
    launcher.start(RunInput(id=RUN, archive=archive(), token="polaris-token", timeout=600))
    network_kwargs = client.networks.create.call_args.kwargs
    assert network_kwargs["internal"] is True and network_kwargs["labels"]["io.iceberg-dbt.run"] == RUN
    network = client.networks.create.return_value
    network.connect.assert_any_call(catalog, aliases=["polaris-control-plane"])
    network.connect.assert_any_call(storage, aliases=["rustfs"])
    image = client.containers.create.call_args.args[0]
    flags = client.containers.create.call_args.kwargs
    assert image == "runner:pinned"
    assert flags["read_only"] and flags["cap_drop"] == ["ALL"] and flags["user"] == "10001:10001"
    assert flags["security_opt"] == ["no-new-privileges:true"] and flags["pids_limit"] == 512
    assert flags["labels"]["io.iceberg-platform.extension"] == "dbt"
    assert flags["environment"] == {"DBT_ENV_SECRET_POLARIS_TOKEN": "polaris-token"}
    assert "volumes" not in flags and "privileged" not in flags
    with pytest.raises(ValueError):
        launcher.start(RunInput(id=RUN, archive=archive(), token="x", timeout=600))


def test_missing_platform_services_leave_nothing_behind(tmp_path):
    client, *_ = docker_client()
    client.containers.list.side_effect = lambda **_: []
    launcher = Launcher({"ARTIFACTS_DIR": str(tmp_path)}, client)
    with pytest.raises(RuntimeError):
        launcher.start(RunInput(id=RUN, archive=archive(), token="x", timeout=600))
    client.networks.create.return_value.remove.assert_called()
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
