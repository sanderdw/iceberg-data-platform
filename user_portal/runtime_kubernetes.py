"""Launch isolated marimo editors as Kubernetes pods. No notebook gets API access or platform credentials.

The notebooks namespace holds only notebook pods, their storage and their own Secrets: creating pods
there grants nothing else. ExternalName Services named polaris, rustfs and workspace-gateway point
at the platform, so notebooks see the same addresses as on Docker; a NetworkPolicy in the chart
replaces the per-session Docker network.
"""

import re
import secrets
import time

from server.kube import Kube, KubeError
from server.models import ServiceError

from .runtime import (
    LABEL,
    NotebookRuntime,
    RuntimeBackendError,
    Workspace,
    filespace_key,
    notebook_environment,
    wait_ready,
)

FILESPACE = "iceberg.users.filespace"


def quantity(value):
    """Docker memory sizes (2g, 512m) as Kubernetes quantities (2Gi, 512Mi)."""
    match = re.fullmatch(r"(\d+)([kmg]?)b?", value.strip().lower())
    if not match:
        return value
    number, unit = match.groups()
    return number + {"k": "Ki", "m": "Mi", "g": "Gi", "": ""}[unit]


class KubernetesNotebookRuntime(NotebookRuntime):
    def __init__(self, env, kube=None):
        self.kube = kube or Kube()
        self.scope = env.get("USER_STACK_NAME", "iceberg-workspaces")
        self.image = env.get("NOTEBOOK_IMAGE", "iceberg-user-notebook:0.1.0")
        self.namespace = env["NOTEBOOK_NAMESPACE"]
        self.limit = int(env.get("MAX_NOTEBOOKS", "8"))
        self.memory = quantity(env.get("NOTEBOOK_MEMORY", "2g"))
        self.memory_request = quantity(env.get("NOTEBOOK_MEMORY_REQUEST", "512m"))
        self.storage = env.get("NOTEBOOK_STORAGE", "5Gi")
        self.storage_class = env.get("NOTEBOOK_STORAGE_CLASS") or None
        self.pull_policy = env.get("NOTEBOOK_PULL_POLICY", "IfNotPresent")
        self.pull_secrets = [name for name in env.get("NOTEBOOK_PULL_SECRETS", "").split(",") if name.strip()]
        # The first start on a node pulls the image, which takes longer than a local Docker start.
        self.start_seconds = int(env.get("NOTEBOOK_START_SECONDS", "180"))
        self.workspaces = {}

    def path(self, kind, name=""):
        group = "/api/v1"
        return f"{group}/namespaces/{self.namespace}/{kind}" + (f"/{name}" if name else "")

    def recover(self):
        # Sessions are process-local. Remove only this stack's orphan runtimes; storage persists.
        selector = f"{LABEL}={self.scope}"
        try:
            self.kube.delete_collection(self.path("pods"), selector)
            self.kube.delete_collection(self.path("secrets"), selector)
        except KubeError as exc:
            raise RuntimeBackendError(str(exc)) from exc

    def ensure_storage(self, key):
        name = f"{self.scope}-work-{key}"
        claim = self.kube.get(self.path("persistentvolumeclaims", name), missing_ok=True)
        if claim is None:
            spec = {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": self.storage}}}
            if self.storage_class:
                spec["storageClassName"] = self.storage_class
            self.kube.create(self.path("persistentvolumeclaims"), {
                "metadata": {"name": name, "labels": {LABEL: self.scope, FILESPACE: key}}, "spec": spec})
        elif claim["metadata"].get("labels", {}).get(LABEL) != self.scope:
            raise ServiceError(409, "The notebook storage does not belong to this workspace stack.")
        return name

    def pod(self, id, key, claim, secret):
        labels = {LABEL: self.scope, FILESPACE: key, "app.kubernetes.io/name": "iceberg-data-platform",
                  "app.kubernetes.io/component": "notebook"}
        return {
            "metadata": {"name": f"{self.scope}-marimo-{id}", "labels": labels},
            "spec": {
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "terminationGracePeriodSeconds": 5,
                "imagePullSecrets": [{"name": name.strip()} for name in self.pull_secrets],
                "securityContext": {"runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001,
                                    "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                # A team's notebook storage is ReadWriteOnce: its sessions share one node.
                "affinity": {"podAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{
                    "labelSelector": {"matchLabels": {FILESPACE: key}}, "topologyKey": "kubernetes.io/hostname"}]}},
                "containers": [{
                    "name": "marimo", "image": self.image, "imagePullPolicy": self.pull_policy,
                    "ports": [{"containerPort": 2718, "name": "marimo"}],
                    "envFrom": [{"secretRef": {"name": secret}}],
                    "resources": {"limits": {"memory": self.memory, "cpu": "2"},
                                  "requests": {"memory": self.memory_request, "cpu": "100m"}},
                    "securityContext": {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]}},
                    "volumeMounts": [{"name": "work", "mountPath": "/work"}, {"name": "tmp", "mountPath": "/tmp"}],
                }],
                "volumes": [{"name": "work", "persistentVolumeClaim": {"claimName": claim}},
                            {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "256Mi"}}],
            },
        }

    def start(self, session, database, namespace, table, *, database_name=None, shared_objects=None):
        key = filespace_key(session.team, session.environment)
        for workspace in self.workspaces.values():
            if workspace.key == key and workspace.session_id == session.id and workspace.database == database:
                return workspace
        if len(self.workspaces) >= self.limit:
            raise ServiceError(409, "All notebook slots are in use. Close a workspace first.")
        id, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        name = f"{self.scope}-marimo-{id}"
        pod = None
        try:
            claim = self.ensure_storage(key)
            # Credentials live in a Secret owned by the pod, never in the pod spec.
            self.kube.create(self.path("secrets"), {
                "metadata": {"name": name, "labels": {LABEL: self.scope}}, "type": "Opaque",
                "stringData": notebook_environment(id, token, session, database, namespace, table,
                                                   database_name, shared_objects)})
            pod = self.kube.create(self.path("pods"), self.pod(id, key, claim, name))
            self.kube.patch(self.path("secrets", name), {"metadata": {"ownerReferences": [{
                "apiVersion": "v1", "kind": "Pod", "name": name, "uid": pod["metadata"]["uid"]}]}})
            ip = self.wait_for_ip(name)
            workspace = Workspace(id, session.id, session.user_id, session.team, database, key, name, None,
                                  f"http://{ip}:2718", token, session.environment,
                                  shared=shared_objects is not None)
            if wait_ready(workspace, lambda: self.phase(name) in ("Pending", "Running"), self.start_seconds):
                self.workspaces[id] = workspace
                return workspace
            raise ServiceError(503, "Marimo could not start. Check the notebook image and try again.")
        except KubeError as exc:
            self.remove(name)
            raise RuntimeBackendError(str(exc)) from exc
        except Exception:
            self.remove(name)
            raise

    def phase(self, name):
        pod = self.kube.get(self.path("pods", name), missing_ok=True)
        return (pod or {}).get("status", {}).get("phase")

    def wait_for_ip(self, name):
        deadline = time.monotonic() + self.start_seconds
        while time.monotonic() < deadline:
            pod = self.kube.get(self.path("pods", name), missing_ok=True) or {}
            status = pod.get("status", {})
            if status.get("podIP"):
                return status["podIP"]
            if status.get("phase") in ("Failed", "Succeeded") or not pod:
                break
            time.sleep(0.5)
        raise ServiceError(503, "Marimo could not start. Check the notebook image and try again.")

    def remove(self, name):
        try:
            self.kube.delete(self.path("pods", name), grace_seconds=0)
            self.kube.delete(self.path("secrets", name))
        except KubeError:
            pass

    def stop(self, id):
        workspace = self.workspaces[id]
        try:
            self.kube.delete(self.path("pods", workspace.container), grace_seconds=0)
            self.kube.delete(self.path("secrets", workspace.container))
        except KubeError as exc:
            raise RuntimeBackendError(str(exc)) from exc
        del self.workspaces[id]

    def close(self):
        errors = []
        for id in list(self.workspaces):
            try:
                self.stop(id)
            except RuntimeBackendError as exc:
                errors.append(exc)
        self.kube.close()
        if errors:
            raise ServiceError(502, "Some notebook pods could not be stopped.")
