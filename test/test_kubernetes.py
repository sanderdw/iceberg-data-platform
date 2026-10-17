"""The Kubernetes backends: API client, notebook pods, monitor rows and the realm-only setup."""

import json
from types import SimpleNamespace

import httpx
import pytest

from scripts.setup import write_realm
from server.kube import Kube, KubeError
from server.models import ServiceError
from server.monitoring import pod_rows, quantity
from user_portal import runtime_kubernetes
from user_portal.runtime import DisabledNotebookRuntime, notebook_runtime
from user_portal.runtime_kubernetes import KubernetesNotebookRuntime


def test_kube_client_reads_the_rotating_token_and_follows_pages(tmp_path):
    token = tmp_path / "token"
    seen = []

    def api(request):
        seen.append((request.headers["authorization"], dict(request.url.params)))
        if request.url.params.get("continue") == "next":
            return httpx.Response(200, json={"items": [{"n": 2}], "metadata": {}})
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, json={"reason": "NotFound"})
        if request.url.path.endswith("/forbidden"):
            return httpx.Response(403, json={"reason": "Forbidden"})
        return httpx.Response(200, json={"items": [{"n": 1}], "metadata": {"continue": "next"}})

    kube = Kube("https://api", token_file=token, transport=httpx.MockTransport(api))
    token.write_text("first\n")
    assert kube.list("/api/v1/pods", "a=b") == [{"n": 1}, {"n": 2}]
    assert seen[0] == ("Bearer first", {"limit": "500", "labelSelector": "a=b"})
    token.write_text("rotated")
    assert kube.get("/api/v1/missing", missing_ok=True) is None
    assert seen[-1][0] == "Bearer rotated"
    with pytest.raises(KubeError, match="403: Forbidden"):
        kube.get("/api/v1/forbidden")


class FakeKube:
    def __init__(self, phase="Running"):
        self.objects, self.calls, self.phase = {}, [], phase

    def get(self, path, missing_ok=False):
        self.calls.append(("get", path))
        found = self.objects.get(path)
        if found and "/pods/" in path:
            found = {**found, "status": {"phase": self.phase, "podIP": "100.64.1.9"}}
        return found

    def create(self, path, body):
        self.calls.append(("create", path))
        stored = {**body, "metadata": {**body["metadata"], "uid": "uid-1"}}
        self.objects[f"{path}/{body['metadata']['name']}"] = stored
        return stored

    def patch(self, path, body):
        self.calls.append(("patch", path))
        self.objects[path]["metadata"].update(body["metadata"])

    def delete(self, path, grace_seconds=None):
        self.calls.append(("delete", path))
        self.objects.pop(path, None)

    def delete_collection(self, path, selector):
        self.calls.append(("delete_collection", path, selector))

    def close(self):
        pass


ENV = {"NOTEBOOK_NAMESPACE": "iceberg-notebooks", "NOTEBOOK_IMAGE": "registry/notebook:1",
       "NOTEBOOK_PULL_SECRETS": "registry", "NOTEBOOK_START_SECONDS": "2"}
SESSION = SimpleNamespace(id="s1", user_id="u1", team="team-a", environment="development",
                          oidc_subject="sub", token="user-token", client_id="", secret="")


def test_notebook_pod_keeps_credentials_in_an_owned_secret(monkeypatch):
    kube = FakeKube()
    runtime = KubernetesNotebookRuntime(ENV, kube=kube)
    monkeypatch.setattr(runtime_kubernetes, "wait_ready", lambda workspace, alive, seconds: alive())
    workspace = runtime.start(SESSION, "db-1", ["ns"], None)
    assert workspace.upstream == "http://100.64.1.9:2718"
    base = "/api/v1/namespaces/iceberg-notebooks"
    claim = next(o for p, o in kube.objects.items() if "/persistentvolumeclaims/" in p)
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    pod = kube.objects[f"{base}/pods/{workspace.container}"]
    spec = pod["spec"]
    container, = spec["containers"]
    assert "env" not in container and container["envFrom"] == [{"secretRef": {"name": workspace.container}}]
    assert spec["automountServiceAccountToken"] is False
    assert spec["securityContext"]["runAsUser"] == 10001
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["resources"]["limits"]["memory"] == "2Gi"
    assert spec["imagePullSecrets"] == [{"name": "registry"}]
    affinity = spec["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]
    assert affinity["labelSelector"]["matchLabels"] == {"iceberg.users.filespace": workspace.key}
    assert pod["metadata"]["labels"]["iceberg.users.filespace"] == workspace.key
    secret = kube.objects[f"{base}/secrets/{workspace.container}"]
    assert secret["stringData"]["ICEBERG_ACCESS_TOKEN"] == "user-token"
    assert secret["stringData"]["ICEBERG_CATALOG_URI"] == "http://polaris:8181/api/catalog"
    assert secret["metadata"]["ownerReferences"][0]["uid"] == "uid-1"
    # The same session, team and database reuses its notebook.
    assert runtime.start(SESSION, "db-1", ["ns"], None) is workspace
    runtime.stop(workspace.id)
    assert f"{base}/pods/{workspace.container}" not in kube.objects
    runtime.recover()
    assert ("delete_collection", f"{base}/pods", "iceberg.users.runtime=iceberg-workspaces") in kube.calls


def test_notebook_that_does_not_start_is_removed(monkeypatch):
    kube = FakeKube(phase="Failed")
    runtime = KubernetesNotebookRuntime(ENV, kube=kube)
    monkeypatch.setattr(runtime_kubernetes, "wait_ready", lambda workspace, alive, seconds: alive())
    with pytest.raises(ServiceError, match="could not start"):
        runtime.start(SESSION, "db-1", ["ns"], None)
    assert not [p for p in kube.objects if "/pods/" in p or "/secrets/" in p]
    assert runtime.workspaces == {}


def test_notebook_storage_of_another_stack_is_refused():
    kube = FakeKube()
    runtime = KubernetesNotebookRuntime(ENV, kube=kube)
    path = "/api/v1/namespaces/iceberg-notebooks/persistentvolumeclaims/iceberg-workspaces-work-key"
    kube.objects[path] = {"metadata": {"labels": {"iceberg.users.runtime": "someone-else"}}}
    with pytest.raises(ServiceError, match="does not belong"):
        runtime.ensure_storage("key")


def test_backend_selection_and_disabled_notebooks():
    runtime = notebook_runtime({"NOTEBOOK_BACKEND": "none"})
    assert isinstance(runtime, DisabledNotebookRuntime)
    runtime.recover()
    with pytest.raises(ServiceError, match="turned off"):
        runtime.start(SESSION, "db", [], None)
    assert runtime_kubernetes.quantity("2g") == "2Gi" and runtime_kubernetes.quantity("512m") == "512Mi"


def test_monitor_rows_from_pods_and_metrics():
    assert quantity("250m") == 0.25 and quantity("512Mi") == 512 * 2**20 and quantity("1500000n") == 0.0015
    assert quantity("83672Ki") == 83672 * 2**10 and quantity("2") == 2
    pods = [{
        "metadata": {"name": "polaris-abc", "namespace": "iceberg",
                     "labels": {"app.kubernetes.io/component": "polaris"}},
        "spec": {"containers": [{"name": "polaris", "resources": {"limits": {"memory": "1Gi"}}}]},
        "status": {"phase": "Running", "containerStatuses": [
            {"name": "polaris", "restartCount": 2, "state": {"running": {"startedAt": "2026-10-17T10:00:00Z"}}}]},
    }]
    metrics = [{"metadata": {"name": "polaris-abc", "namespace": "iceberg"},
                "containers": [{"name": "polaris", "usage": {"cpu": "125m", "memory": "300Mi"}}]}]
    row, = pod_rows(pods, metrics)
    assert row == {"name": "polaris-abc", "state": "running", "service": "polaris", "restarts": 2,
                   "startedAt": "2026-10-17T10:00:00Z", "cpuPercent": 12.5, "memoryBytes": 300 * 2**20,
                   "memoryLimitBytes": 2**30, "status": "online"}
    # Without metrics-server the pod is still listed.
    assert pod_rows(pods, [])[0]["cpuPercent"] is None


def test_realm_only_setup_uses_the_given_origins(tmp_path):
    target = tmp_path / "import" / "iceberg-realm.json"
    write_realm(target, {"PORTAL_ORIGIN": "https://admin.example.com", "USER_ORIGIN": "https://users.example.com"})
    realm = json.loads(target.read_text())
    clients = {c["clientId"]: c for c in realm["clients"]}
    assert clients["iceberg-admin"]["redirectUris"] == ["https://admin.example.com/auth/callback"]
    assert clients["iceberg-users"]["webOrigins"] == ["https://users.example.com"]
    assert clients["iceberg-admin"]["secret"] == "${OIDC_PORTAL_SECRET}"
    assert realm["users"][0]["username"] == "platform-admin"
    assert "${PLATFORM_ADMIN_PASSWORD}" in json.dumps(realm["users"])
