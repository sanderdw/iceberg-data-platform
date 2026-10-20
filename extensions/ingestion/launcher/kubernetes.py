"""The launcher's Kubernetes backend (LAUNCHER_BACKEND=kubernetes): one Job per run.

Runs go to their own namespace (RUNS_NAMESPACE), which holds nothing but runs: the right to create
pods there grants no other Secret. The run pod gets the same fixed isolation as the Docker container,
and a NetworkPolicy in the chart replaces the internal Docker network. Instead of put_archive and
get_archive, the runner fetches its staged /work and returns out/ through the launcher's two job
endpoints, with a ticket that is valid for one run, one download and one upload.
"""

import base64
import json
import logging
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from ingestion.config import VERSION
from launcher.app import RUN_ID
from launcher.kube import Kube, KubeError

EXTENSION_LABEL = "io.iceberg-platform.extension"
RUN_LABEL = "io.iceberg-ingestion.run"
LOG = logging.getLogger(__name__)
# A run's out/ archive may not exceed this.
MAX_OUTPUT = 2 * 1024**3


class KubernetesLauncher:
    backend = "kubernetes"

    def __init__(self, env, kube=None):
        self.kube = kube or Kube()
        self.namespace = env["RUNS_NAMESPACE"]
        self.image = env.get("RUNNER_IMAGE", f"iceberg-ingestion-runner:{VERSION}")
        self.pull_policy = env.get("RUNNER_PULL_POLICY", "IfNotPresent")
        self.pull_secrets = [n.strip() for n in env.get("RUNNER_PULL_SECRETS", "").split(",") if n.strip()]
        self.run_url = env["RUN_URL"].rstrip("/")
        self.artifacts = Path(env.get("ARTIFACTS_DIR", "/artifacts"))
        self.memory = env.get("RUN_MEMORY", "2g").upper().replace("G", "Gi").replace("M", "Mi")
        self.cpus = env.get("RUN_CPUS", "2")
        self.work_size = env.get("RUN_WORK_SIZE", "4Gi")
        self.extension = env.get("BRIDGE_EXTENSION_ID", "ingestion")
        self.poll = float(env.get("RUN_POLL_SECONDS", "3"))
        self.runs = {}
        self.lock = threading.Lock()

    def path(self, kind, name="", group="/api/v1"):
        return f"{group}/namespaces/{self.namespace}/{kind}" + (f"/{name}" if name else "")

    def jobs(self, name=""):
        return self.path("jobs", name, "/apis/batch/v1")

    def recover(self):
        """Runs are process-local: remove what a previous launcher left behind."""
        selector = f"{RUN_LABEL}"
        self.kube.delete_collection(self.jobs(), selector, propagation="Background")
        self.kube.delete_collection(self.path("secrets"), selector)

    def job(self, id, timeout):
        labels = {EXTENSION_LABEL: self.extension, RUN_LABEL: id}
        pod = {
            "metadata": {"labels": labels},
            "spec": {
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "imagePullSecrets": [{"name": n} for n in self.pull_secrets],
                "securityContext": {"runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001,
                                    "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "runner", "image": self.image, "imagePullPolicy": self.pull_policy,
                    "command": ["/opt/runner/.venv/bin/python", "-m", "runner.remote"],
                    "env": [{"name": "RUN_URL", "value": self.run_url}, {"name": "RUN_ID", "value": id}],
                    "envFrom": [{"secretRef": {"name": f"iceberg-ingestion-{id}"}}],
                    "resources": {"limits": {"memory": self.memory, "cpu": self.cpus},
                                  "requests": {"memory": "512Mi", "cpu": "250m"}},
                    "securityContext": {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]}},
                    "volumeMounts": [{"name": "work", "mountPath": "/work"}, {"name": "tmp", "mountPath": "/tmp"}],
                }],
                "volumes": [{"name": "work", "emptyDir": {"sizeLimit": self.work_size}},
                            {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "512Mi"}}],
            },
        }
        return {"metadata": {"name": f"iceberg-ingestion-{id}", "labels": labels},
                "spec": {"backoffLimit": 0, "activeDeadlineSeconds": timeout, "ttlSecondsAfterFinished": 3600,
                         "template": pod}}

    def start(self, data):
        with self.lock:
            if data.id in self.runs:
                raise ValueError("This run was already started.")
            ticket = secrets.token_urlsafe(32)
            self.runs[data.id] = {"state": "starting", "startedAt": time.time(), "ticket": ticket,
                                  "input": base64.b64decode(data.archive), "uploaded": False}
        name = f"iceberg-ingestion-{data.id}"
        try:
            # The catalog token, the source's credentials and the ticket live in a Secret owned by the Job,
            # never in its spec.
            self.kube.create(self.path("secrets"), {
                "metadata": {"name": name, "labels": {EXTENSION_LABEL: self.extension, RUN_LABEL: data.id}},
                "type": "Opaque", "stringData": {"ING_TOKEN": data.token, "ING_SECRETS": json.dumps(data.secrets),
                                                 "RUN_TICKET": ticket}})
            job = self.kube.create(self.jobs(), self.job(data.id, data.timeout))
            self.kube.patch(self.path("secrets", name), {"metadata": {"ownerReferences": [{
                "apiVersion": "batch/v1", "kind": "Job", "name": name, "uid": job["metadata"]["uid"]}]}})
        except Exception:
            self.remove(data.id)
            with self.lock:
                self.runs.pop(data.id, None)
            raise
        with self.lock:
            self.runs[data.id].update(state="running")
        threading.Thread(target=self.watch, args=(data.id, data.timeout), daemon=True).start()

    def remove(self, id):
        name = f"iceberg-ingestion-{id}"
        try:
            self.kube.delete(self.jobs(name), propagation="Background")
            self.kube.delete(self.path("secrets", name))
        except KubeError:
            pass

    def pod(self, id):
        pods = self.kube.list(self.path("pods"), f"{RUN_LABEL}={id}")
        return pods[0] if pods else None

    def watch(self, id, timeout):
        exit_code, timed_out = None, False
        deadline = time.monotonic() + timeout + 120
        try:
            while time.monotonic() < deadline:
                job = self.kube.get(self.jobs(f"iceberg-ingestion-{id}"), missing_ok=True)
                if job is None:
                    break
                conditions = {c["type"]: c for c in job.get("status", {}).get("conditions", [])
                              if c.get("status") == "True"}
                if "Complete" in conditions or "Failed" in conditions:
                    timed_out = conditions.get("Failed", {}).get("reason") == "DeadlineExceeded"
                    break
                time.sleep(self.poll)
            else:
                timed_out = True
            pod = self.pod(id)
            if pod:
                for status in pod.get("status", {}).get("containerStatuses", []):
                    exit_code = status.get("state", {}).get("terminated", {}).get("exitCode", exit_code)
                self.save_logs(id, pod["metadata"]["name"])
        except (KubeError, OSError) as exc:
            LOG.error("Run %s could not be collected: %s", id, type(exc).__name__)
        finally:
            self.remove(id)
            with self.lock:
                run = self.runs[id]
                if exit_code is None and not timed_out and run.get("cancelled"):
                    exit_code = 137
                run.update(state="finished", exitCode=exit_code, timedOut=timed_out, finishedAt=time.time())
                run.pop("input", None)
                run.pop("ticket", None)

    def save_logs(self, id, pod):
        target = self.artifacts / id
        target.mkdir(parents=True, exist_ok=True)
        try:
            text = self.kube.text(self.path("pods", f"{pod}/log"), {"tailLines": 20000})
        except KubeError:
            text = ""
        (target / "container.log").write_bytes(text.encode()[-2_000_000:])

    # The two job endpoints the run itself calls.
    def check_ticket(self, id, ticket):
        with self.lock:
            run = self.runs.get(id)
            return bool(run and run.get("ticket") and secrets.compare_digest(ticket, run["ticket"]))

    def take_input(self, id, ticket):
        """The staged /work, once."""
        if not self.check_ticket(id, ticket):
            return None
        with self.lock:
            return self.runs[id].pop("input", None)

    def accept_output(self, id, ticket, path, extract):
        """Unpack the uploaded out/ archive (a file at `path`) where the portal reads artifacts; once."""
        if not self.check_ticket(id, ticket):
            return False
        with self.lock:
            if self.runs[id]["uploaded"]:
                return False
            self.runs[id]["uploaded"] = True
        target = self.artifacts / id
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        with open(path, "rb") as archive:
            extract(archive, target)
        return True

    def status(self, id):
        with self.lock:
            run = self.runs.get(id)
            hidden = ("ticket", "input", "uploaded", "cancelled")
            return {k: v for k, v in run.items() if k not in hidden} if run else None

    def logs(self, id, tail):
        with self.lock:
            run = self.runs.get(id)
        if not run:
            return None
        if run.get("state") == "running":
            try:
                pod = self.pod(id)
                if pod:
                    return self.kube.text(self.path("pods", f"{pod['metadata']['name']}/log"), {"tailLines": tail})
            except KubeError:
                pass
        path = self.artifacts / id / "out" / "run.log"
        return path.read_text(errors="replace")[-200_000:] if path.exists() else ""

    def discard(self, id):
        if not re.fullmatch(RUN_ID, id):
            raise ValueError("Invalid run id.")
        with self.lock:
            run = self.runs.get(id)
            if run and run.get("state") != "finished":
                raise ValueError("The run has not finished.")
            self.runs.pop(id, None)
        target = self.artifacts / id
        if not target.exists():
            return False
        shutil.rmtree(target)
        return True

    def cancel(self, id):
        with self.lock:
            run = self.runs.get(id)
            if not run or run.get("state") != "running":
                return False
            run["cancelled"] = True
        self.remove(id)
        return True
