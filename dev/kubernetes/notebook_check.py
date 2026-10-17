"""Check what a notebook pod, or a dbt or Ingestion run, can reach and what it may not.

    python3 dev/kubernetes/notebook_check.py iceberg              # the notebooks namespace
    python3 dev/kubernetes/notebook_check.py iceberg dbt          # dbt runs
    python3 dev/kubernetes/notebook_check.py iceberg ingestion    # Ingestion runs

Starts a short-lived pod with the notebook or runner image under the same restrictions as the real
pods, and tries each address from inside it. Exits non-zero when a check fails.
"""

import json
import subprocess
import sys

namespace = sys.argv[1] if len(sys.argv) > 1 else "iceberg"
target = sys.argv[2] if len(sys.argv) > 2 else "notebooks"
platform = f"{namespace}.svc.cluster.local"
# The platform's own services, which no notebook or run may reach (Postgres only when bundled).
PLATFORM = [(f"keycloak.{platform}", 8080, False), (f"portal.{platform}", 3000, False),
            (f"bridge.{platform}", 3005, False), (f"polaris.{platform}", 8182, False),
            (f"postgres.{platform}", 5432, False)]
INTERNET = ("1.1.1.1", 443)

# (address, port, expected to connect), the image's deployment and its environment variable
if target == "notebooks":
    pods, deployment, variable, python = f"{namespace}-notebooks", "users", "NOTEBOOK_IMAGE", "/app/.venv/bin/python"
    CHECKS = [("polaris", 8181, True), ("rustfs", 9000, True), ("workspace-gateway", 3002, True),
              (*INTERNET, True), *PLATFORM]
else:
    pods, deployment, variable = f"{namespace}-{target}-runs", target, "RUNNER_IMAGE"
    python = "/opt/runner/.venv/bin/python"
    launcher = {"dbt": ("dbt-launcher", 3006), "ingestion": ("ing-launcher", 3018)}[target]
    # dbt runs have no internet; Ingestion runs reach public sources.
    CHECKS = [("polaris-control-plane", 8181, True), ("rustfs", 9000, True), (*launcher, True),
              (*INTERNET, target == "ingestion"), *PLATFORM]
notebooks = pods
PROBE = """
import json, socket, sys
results = []
for host, port, _ in json.loads(sys.argv[1]):
    try:
        socket.create_connection((host, port), timeout=4).close()
        results.append(True)
    except OSError:
        results.append(False)
print(json.dumps(results))
"""


def kubectl(*args, check=True):
    return subprocess.run(["kubectl", *args], check=check, capture_output=True, text=True)


def setting(name):
    return kubectl("-n", namespace, "get", "deploy", deployment, "-o",
                   f"jsonpath={{.spec.template.spec.containers[*].env[?(@.name=='{name}')].value}}").stdout.split()[0]


image = setting(variable)
policy = setting("NOTEBOOK_PULL_POLICY" if target == "notebooks" else "RUNNER_PULL_POLICY")
secrets = kubectl("-n", pods, "get", "secret", "-o", "jsonpath={.items[*].metadata.name}").stdout.split()
overrides = {"spec": {
    "automountServiceAccountToken": False,
    "imagePullSecrets": [{"name": name} for name in secrets if name == "registry"],
    "securityContext": {"runAsUser": 10001, "runAsGroup": 10001, "runAsNonRoot": True,
                        "seccompProfile": {"type": "RuntimeDefault"}},
    "containers": [{
        "name": "probe", "image": image, "imagePullPolicy": policy or "IfNotPresent",
        "command": [python, "-c", PROBE, json.dumps(CHECKS)],
        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]}},
    }],
}}
kubectl("-n", notebooks, "delete", "pod", "probe", "--ignore-not-found")
run = kubectl("-n", notebooks, "run", "probe", "--image", image, "--restart=Never", "--quiet", "--attach", "--rm",
              f"--overrides={json.dumps(overrides)}", "--pod-running-timeout=5m", check=False)
if run.returncode:
    sys.exit(f"Probe pod failed: {run.stderr.strip() or run.stdout.strip()}")
results = json.loads(run.stdout.strip().splitlines()[-1])
failed = 0
for (host, port, expected), connected in zip(CHECKS, results, strict=True):
    ok = connected == expected
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'} {host}:{port} {'reachable' if connected else 'blocked'}"
          f" (expected {'reachable' if expected else 'blocked'})")
sys.exit(1 if failed else 0)
