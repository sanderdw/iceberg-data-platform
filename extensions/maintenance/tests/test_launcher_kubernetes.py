"""The launcher's Kubernetes backend: one Job per run, and the two job endpoints the run calls.

runner/remote_test.go checks the runner's side of the same contract.
"""

import base64
import io
import tarfile
import threading

from fastapi.testclient import TestClient

from launcher.app import RunInput, create_app
from launcher.kubernetes import KubernetesLauncher

TOKEN = "t" * 40
RUN = "run-" + "b" * 24
ENV = {"RUNS_NAMESPACE": "iceberg-maintenance-runs", "RUN_URL": "http://mnt-launcher:3019", "RUNNER_IMAGE": "runner:1",
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


def wait_finished(launcher):
    for _ in range(100):
        if launcher.status(RUN)["state"] == "finished":
            break
        threading.Event().wait(0.05)
    return launcher.status(RUN)


def test_a_run_is_a_job_with_fixed_isolation_and_its_token_in_an_owned_secret(tmp_path):
    kube = FakeKube()
    launcher = KubernetesLauncher({**ENV, "ARTIFACTS_DIR": str(tmp_path)}, kube=kube)
    staged = tar({"job.json": b"{}"})
    launcher.start(RunInput(id=RUN, archive=base64.b64encode(staged).decode(), token="catalog-token", timeout=600))
    job = kube.objects[f"/apis/batch/v1/namespaces/iceberg-maintenance-runs/jobs/iceberg-maintenance-{RUN}"]
    spec = job["spec"]
    assert spec["backoffLimit"] == 0 and spec["activeDeadlineSeconds"] == 600
    assert job["metadata"]["labels"] == {"io.iceberg-platform.extension": "maintenance",
                                         "io.iceberg-maintenance.run": RUN}
    pod = spec["template"]["spec"]
    container, = pod["containers"]
    assert container["image"] == "runner:1" and pod["automountServiceAccountToken"] is False
    assert container["command"] == ["/usr/local/bin/maintenance-runner"]
    # RUN_URL puts the Go runner in remote mode; the ticket comes from the Secret.
    assert container["env"] == [{"name": "RUN_URL", "value": "http://mnt-launcher:3019"},
                                {"name": "RUN_ID", "value": RUN}]
    assert container["resources"]["limits"] == {"memory": "2Gi", "cpu": "2"}
    assert container["securityContext"] == {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                                            "capabilities": {"drop": ["ALL"]}}
    assert pod["securityContext"]["runAsUser"] == 10001 and pod["imagePullSecrets"] == [{"name": "registry"}]
    assert "catalog-token" not in str(job)
    secret = kube.objects[f"/api/v1/namespaces/iceberg-maintenance-runs/secrets/iceberg-maintenance-{RUN}"]
    assert set(secret["stringData"]) == {"MNT_TOKEN", "RUN_TICKET"}
    assert secret["stringData"]["MNT_TOKEN"] == "catalog-token"
    assert secret["metadata"]["ownerReferences"][0] == {"apiVersion": "batch/v1", "kind": "Job",
                                                        "name": f"iceberg-maintenance-{RUN}", "uid": "uid-7"}
    ticket = secret["stringData"]["RUN_TICKET"]

    with TestClient(create_app(launcher, ENV)) as client:
        # recover() removed what an earlier launcher left behind.
        assert ("/apis/batch/v1/namespaces/iceberg-maintenance-runs/jobs", "io.iceberg-maintenance.run") in kube.deleted
        # The job endpoints take the run's ticket, not the launcher token, and give the input once.
        assert client.get(f"/jobs/{RUN}/input", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(f"/jobs/{RUN}/input", headers={"X-Launcher-Token": TOKEN}).status_code == 401
        auth = {"Authorization": f"Bearer {ticket}"}
        assert client.get(f"/jobs/{RUN}/input", headers=auth).content == staged
        assert client.get(f"/jobs/{RUN}/input", headers=auth).status_code == 401
        out = tar({"out/run.log": b"done", "out/result.json": b'{"status": "success"}', "../escape": b"x",
                   "/etc/passwd": b"x"})
        assert client.put(f"/jobs/{RUN}/output", headers=auth, content=out).json() == {"stored": True}
        assert client.put(f"/jobs/{RUN}/output", headers=auth, content=out).status_code == 409
        assert (tmp_path / RUN / "out" / "run.log").read_text() == "done"
        assert (tmp_path / RUN / "out" / "result.json").exists()
        assert not (tmp_path / "escape").exists()
        # The gateway's endpoints still need the launcher token.
        assert client.get(f"/runs/{RUN}").status_code == 401
        assert client.get(f"/runs/{RUN}/logs", headers={"X-Launcher-Token": TOKEN}).text == "runner log"
        kube.done.set()
        status = wait_finished(launcher)
        assert status["state"] == "finished" and status["exitCode"] == 0 and status["timedOut"] is False
        assert "ticket" not in status and "input" not in status
        assert (tmp_path / RUN / "container.log").read_text() == "runner log"
        assert f"/apis/batch/v1/namespaces/iceberg-maintenance-runs/jobs/iceberg-maintenance-{RUN}" in kube.deleted
        # After the run the ticket is gone.
        assert client.put(f"/jobs/{RUN}/output", headers=auth, content=out).status_code == 401
        assert client.delete(f"/runs/{RUN}/artifacts", headers={"X-Launcher-Token": TOKEN}).json() == {"removed": True}


def test_a_cancelled_run_ends_with_its_job_removed(tmp_path):
    kube = FakeKube()
    launcher = KubernetesLauncher({**ENV, "ARTIFACTS_DIR": str(tmp_path)}, kube=kube)
    launcher.start(RunInput(id=RUN, archive=base64.b64encode(tar({"job.json": b"{}"})).decode(), token="t",
                            timeout=600))
    with TestClient(create_app(launcher, ENV)) as client:
        assert client.delete(f"/runs/{RUN}", headers={"X-Launcher-Token": TOKEN}).json() == {"cancelled": True}
        kube.list = lambda path, selector=None: []
        kube.done.set()
        status = wait_finished(launcher)
        assert status["state"] == "finished" and status["exitCode"] == 137


def test_a_run_that_cannot_be_created_leaves_nothing_behind(tmp_path):
    kube = FakeKube()

    def refuse(path, body):
        if "/jobs" in path:
            from launcher.kube import KubeError

            raise KubeError(403, "Forbidden")
        return FakeKube.create(kube, path, body)

    kube.create = refuse
    launcher = KubernetesLauncher({**ENV, "ARTIFACTS_DIR": str(tmp_path)}, kube=kube)
    with TestClient(create_app(launcher, ENV)) as client:
        response = client.post("/runs", headers={"X-Launcher-Token": TOKEN}, json={
            "id": RUN, "archive": base64.b64encode(b"x").decode(), "token": "t", "timeout": 600})
        assert response.status_code == 503
    assert launcher.status(RUN) is None
    assert f"/api/v1/namespaces/iceberg-maintenance-runs/secrets/iceberg-maintenance-{RUN}" in kube.deleted


def test_job_endpoints_are_closed_on_docker():
    class Docker:
        def recover(self):
            pass

    with TestClient(create_app(Docker(), {"LAUNCHER_TOKEN": TOKEN})) as client:
        assert client.get(f"/jobs/{RUN}/input").status_code == 401
        assert client.put(f"/jobs/{RUN}/output", content=b"x").status_code == 401


def test_the_backend_follows_launcher_backend(monkeypatch):
    import launcher.kubernetes as kubernetes

    kube = FakeKube()
    monkeypatch.setattr(kubernetes, "Kube", lambda: kube)
    with TestClient(create_app(env=ENV)) as client:
        # The Kubernetes backend answers the job endpoints itself, and recovered its runs namespace.
        assert client.get(f"/jobs/{RUN}/input").json() == {"error": "Unknown run or ticket."}
    assert ("/api/v1/namespaces/iceberg-maintenance-runs/secrets", "io.iceberg-maintenance.run") in kube.deleted
