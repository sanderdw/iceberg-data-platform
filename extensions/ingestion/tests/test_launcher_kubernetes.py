import base64
import io
import socket
import tarfile
import threading

from fastapi.testclient import TestClient

from launcher.app import RunInput, create_app
from launcher.kubernetes import KubernetesLauncher

TOKEN = "t" * 40
RUN = "run-" + "b" * 24
ENV = {"RUNS_NAMESPACE": "iceberg-ingestion-runs", "RUN_URL": "http://ing-launcher:3018", "RUNNER_IMAGE": "runner:1",
       "RUNNER_PULL_SECRETS": "registry", "LAUNCHER_TOKEN": TOKEN, "LAUNCHER_BACKEND": "kubernetes",
       "RUN_POLL_SECONDS": "0"}


def tar(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as out:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            out.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FakeKube:
    def __init__(self):
        self.objects, self.deleted, self.done = {}, [], threading.Event()

    def create(self, path, body):
        self.objects[f"{path}/{body['metadata']['name']}"] = body
        return {**body, "metadata": {**body["metadata"], "uid": "uid-7"}}

    def patch(self, path, body):
        self.objects[path]["metadata"].update(body["metadata"])

    def get(self, path, missing_ok=False):
        self.done.wait(5)
        if path in self.deleted:
            return None
        return {"status": {"conditions": [{"type": "Complete", "status": "True"}]}}

    def list(self, path, selector=None):
        return [{"metadata": {"name": "pod-1"},
                 "status": {"containerStatuses": [{"state": {"terminated": {"exitCode": 0}}}]}}]

    def text(self, path, params=None):
        return "runner log"

    def delete(self, path, grace_seconds=None, propagation=None):
        self.deleted.append(path)

    def delete_collection(self, path, selector, propagation=None):
        self.deleted.append((path, selector))


def test_a_run_is_a_job_with_fixed_isolation_and_its_secrets_in_an_owned_secret(tmp_path):
    kube = FakeKube()
    launcher = KubernetesLauncher({**ENV, "ARTIFACTS_DIR": str(tmp_path)}, kube=kube)
    staged = tar({"job.json": b"{}"})
    launcher.start(RunInput(id=RUN, archive=base64.b64encode(staged).decode(), token="catalog-token", timeout=600,
                            secrets={"password": "source-secret"}))
    job = kube.objects[f"/apis/batch/v1/namespaces/iceberg-ingestion-runs/jobs/iceberg-ingestion-{RUN}"]
    spec = job["spec"]
    assert spec["backoffLimit"] == 0 and spec["activeDeadlineSeconds"] == 600
    pod = spec["template"]["spec"]
    container, = pod["containers"]
    assert container["image"] == "runner:1" and pod["automountServiceAccountToken"] is False
    assert container["securityContext"] == {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                                            "capabilities": {"drop": ["ALL"]}}
    assert pod["securityContext"]["runAsUser"] == 10001 and pod["imagePullSecrets"] == [{"name": "registry"}]
    assert "catalog-token" not in str(job) and "source-secret" not in str(job)
    secret = kube.objects[f"/api/v1/namespaces/iceberg-ingestion-runs/secrets/iceberg-ingestion-{RUN}"]
    assert secret["stringData"]["ING_TOKEN"] == "catalog-token"
    assert secret["stringData"]["ING_SECRETS"] == '{"password": "source-secret"}'
    assert secret["metadata"]["ownerReferences"][0] == {"apiVersion": "batch/v1", "kind": "Job",
                                                        "name": f"iceberg-ingestion-{RUN}", "uid": "uid-7"}
    ticket = secret["stringData"]["RUN_TICKET"]

    with TestClient(create_app(launcher, ENV)) as client:
        # The job endpoints take the run's ticket, not the launcher token, and give the input once.
        assert client.get(f"/jobs/{RUN}/input", headers={"Authorization": "Bearer wrong"}).status_code == 401
        auth = {"Authorization": f"Bearer {ticket}"}
        assert client.get(f"/jobs/{RUN}/input", headers=auth).content == staged
        assert client.get(f"/jobs/{RUN}/input", headers=auth).status_code == 401
        out = tar({"out/run.log": b"done", "../escape": b"x", "/etc/passwd": b"x"})
        assert client.put(f"/jobs/{RUN}/output", headers=auth, content=out).json() == {"stored": True}
        assert client.put(f"/jobs/{RUN}/output", headers=auth, content=out).status_code == 409
        assert (tmp_path / RUN / "out" / "run.log").read_text() == "done"
        assert not (tmp_path / "escape").exists()
        kube.done.set()
        for _ in range(100):
            if launcher.status(RUN)["state"] == "finished":
                break
            threading.Event().wait(0.05)
        status = launcher.status(RUN)
        assert status["state"] == "finished" and status["exitCode"] == 0 and status["timedOut"] is False
        assert "ticket" not in status
        assert (tmp_path / RUN / "container.log").read_text() == "runner log"
        # After the run the ticket is gone.
        assert client.put(f"/jobs/{RUN}/output", headers=auth, content=out).status_code == 401


def test_job_endpoints_are_closed_on_docker():
    class Docker:
        def recover(self):
            pass

    with TestClient(create_app(Docker(), {"LAUNCHER_TOKEN": TOKEN})) as client:
        assert client.get(f"/jobs/{RUN}/input").status_code == 401


def test_the_remote_runner_fetches_runs_and_returns_out(tmp_path, monkeypatch):
    from runner import remote

    calls = []

    def call(method, path, data=None):
        calls.append((method, path))
        if method == "GET":
            return tar({"job.json": b"{}", "project/x.sql": b"select 1"})
        assert socket.getaddrinfo is remote.guard._system
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            calls.append(sorted(archive.getnames()))
        return b"{}"

    def run_pipeline():
        # The source guard is active during the run; the upload to the launcher must still work.
        remote.guard.install()
        assert (tmp_path / "project" / "x.sql").read_text() == "select 1"
        (tmp_path / "out").mkdir()
        (tmp_path / "out" / "run.log").write_text("ok")
        return 0

    monkeypatch.setattr(remote, "WORK", tmp_path)
    monkeypatch.setattr(remote, "call", call)
    monkeypatch.setattr(remote.entrypoint, "main", run_pipeline)
    assert remote.main() == 0
    assert calls == [("GET", "input"), ("PUT", "output"), ["out", "out/run.log"]]
