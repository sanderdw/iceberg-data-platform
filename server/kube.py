"""Minimal in-cluster Kubernetes API client for the notebook runtime and the monitor.

Only what those two need: JSON requests with the pod's service-account token, re-read on every
request because bound tokens rotate. No kubeconfig, no watch streams.
"""

import os
import ssl
from pathlib import Path

import httpx

ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class KubeError(Exception):
    def __init__(self, status, reason):
        super().__init__(f"Kubernetes API {status}: {reason}")
        self.status = status
        self.reason = reason


class Kube:
    def __init__(self, base_url=None, token_file=ACCOUNT / "token", ca_file=ACCOUNT / "ca.crt",
                 transport=None, timeout=15):
        if base_url is None:
            host = os.environ["KUBERNETES_SERVICE_HOST"]
            host = f"[{host}]" if ":" in host else host
            base_url = f"https://{host}:{os.environ.get('KUBERNETES_SERVICE_PORT', '443')}"
        self.token_file = Path(token_file)
        verify = ssl.create_default_context(cafile=str(ca_file)) if transport is None else True
        self.http = httpx.Client(base_url=base_url, verify=verify, transport=transport, timeout=timeout,
                                 trust_env=False)

    def namespace(self):
        return (ACCOUNT / "namespace").read_text().strip()

    def request(self, method, path, *, json=None, params=None, content_type="application/json", missing_ok=False):
        headers = {"Authorization": f"Bearer {self.token_file.read_text().strip()}", "Accept": "application/json"}
        if json is not None:
            headers["Content-Type"] = content_type
        response = self.http.request(method, path, json=json, params=params, headers=headers)
        if response.status_code == 404 and missing_ok:
            return None
        if response.status_code >= 400:
            try:
                reason = response.json().get("reason") or response.reason_phrase
            except ValueError:
                reason = response.reason_phrase
            raise KubeError(response.status_code, reason)
        return response.json() if response.content else None

    def get(self, path, missing_ok=False):
        return self.request("GET", path, missing_ok=missing_ok)

    def list(self, path, selector=None):
        items, token = [], None
        while True:
            params = {"limit": 500, **({"labelSelector": selector} if selector else {}),
                      **({"continue": token} if token else {})}
            page = self.request("GET", path, params=params)
            items += page.get("items", [])
            token = page.get("metadata", {}).get("continue")
            if not token:
                return items

    def create(self, path, body):
        return self.request("POST", path, json=body)

    def patch(self, path, body):
        return self.request("PATCH", path, json=body, content_type="application/merge-patch+json")

    def delete(self, path, grace_seconds=None):
        params = {"gracePeriodSeconds": grace_seconds} if grace_seconds is not None else None
        return self.request("DELETE", path, params=params, missing_ok=True)

    def delete_collection(self, path, selector):
        return self.request("DELETE", path, params={"labelSelector": selector})

    def close(self):
        self.http.close()
