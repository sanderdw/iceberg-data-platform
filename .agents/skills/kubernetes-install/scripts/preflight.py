"""Check that a Kubernetes cluster is ready for the Iceberg Data Platform chart. Changes nothing, except
a short-lived namespace for the NetworkPolicy test (removed again).

    python3 preflight.py [--namespace iceberg] [--skip-network-test]

Uses the current kubectl context. Prints one line per check and a JSON summary as its last line;
exits 1 when something required is missing. Standard library only.
"""

import argparse
import json
import secrets
import subprocess
import sys
import time

MIN_VERSION = (1, 32)
PROBE_IMAGE = "busybox:1.37.0"


def kubectl(*args, stdin=None, check=False):
    result = subprocess.run(["kubectl", *args], input=stdin, capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def items(*args):
    result = kubectl("get", *args, "-o", "json")
    return json.loads(result.stdout).get("items", []) if result.returncode == 0 else None


def network_policies_enforced():
    """Two pods in a temporary namespace: the client reaches the server, then a deny policy stops it."""
    namespace = "iceberg-preflight-" + secrets.token_hex(3)
    manifest = {"apiVersion": "v1", "kind": "List", "items": [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
        {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "server", "namespace": namespace,
                                                          "labels": {"app": "server"}},
         "spec": {"containers": [{"name": "server", "image": PROBE_IMAGE,
                                  "command": ["sh", "-c", "echo ok > /tmp/index.html; httpd -f -p 8080 -h /tmp"]}]}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "server", "namespace": namespace},
         "spec": {"selector": {"app": "server"}, "ports": [{"port": 8080}]}},
        {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "client", "namespace": namespace},
         "spec": {"containers": [{"name": "client", "image": PROBE_IMAGE, "command": ["sleep", "600"]}]}},
    ]}
    deny = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": "deny", "namespace": namespace},
            "spec": {"podSelector": {"matchLabels": {"app": "server"}}, "policyTypes": ["Ingress"]}}

    def reach():
        return kubectl("-n", namespace, "exec", "client", "--", "wget", "-T", "3", "-qO-",
                       "http://server:8080/").returncode == 0

    try:
        kubectl("apply", "-f", "-", stdin=json.dumps(manifest), check=True)
        kubectl("-n", namespace, "wait", "pod", "--all", "--for=condition=Ready", "--timeout=180s", check=True)
        deadline = time.monotonic() + 30
        while not reach():
            if time.monotonic() > deadline:
                return None  # Pods cannot talk at all: the test says nothing about policies.
            time.sleep(2)
        kubectl("apply", "-f", "-", stdin=json.dumps(deny), check=True)
        deadline = time.monotonic() + 30
        while reach():
            if time.monotonic() > deadline:
                return False
            time.sleep(2)
        return True
    finally:
        kubectl("delete", "namespace", namespace, "--wait=false")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--namespace", default="iceberg")
    parser.add_argument("--skip-network-test", action="store_true")
    args = parser.parse_args()
    summary, missing = {}, []

    def report(name, ok, detail, required=True):
        summary[name] = {"ok": ok, "detail": detail}
        if not ok and required:
            missing.append(name)
        print(f"{'OK  ' if ok else ('MISSING' if required else 'WARN')} {name}: {detail}")

    context = kubectl("config", "current-context").stdout.strip()
    version = kubectl("version", "-o", "json")
    if version.returncode:
        report("cluster", False, f"kubectl cannot reach the cluster of context {context or '(none)'}")
        print(json.dumps({"context": context, "ready": False, "checks": summary}))
        sys.exit(1)
    server = json.loads(version.stdout)["serverVersion"]
    numbers = (int(server["major"]), int("".join(c for c in server["minor"] if c.isdigit())))
    report("context", True, context, required=False)
    report("kubernetes", numbers >= MIN_VERSION, f"{server['gitVersion']} (needs 1.32 or later)")

    classes = items("ingressclass") or []
    default = [c["metadata"]["name"] for c in classes
               if c["metadata"].get("annotations", {}).get("ingressclass.kubernetes.io/is-default-class") == "true"]
    names = [c["metadata"]["name"] for c in classes]
    report("ingress", bool(classes), f"IngressClasses {names or 'none'}" + (f", default {default[0]}" if default else ""))

    issuers = items("clusterissuer")
    if issuers is None:
        report("cert-manager", False, "cert-manager is not installed (no ClusterIssuer resource)")
    else:
        ready = [i["metadata"]["name"] for i in issuers
                 if any(c["type"] == "Ready" and c["status"] == "True" for c in i.get("status", {}).get("conditions", []))]
        report("cert-manager", bool(ready), f"ready ClusterIssuers {ready or 'none'}")

    storage = items("storageclass") or []
    default_storage = [s["metadata"]["name"] for s in storage
                       if s["metadata"].get("annotations", {}).get("storageclass.kubernetes.io/is-default-class") == "true"]
    report("storage", bool(default_storage),
           f"default StorageClass {default_storage[0] if default_storage else 'none'}; "
           f"all: {[s['metadata']['name'] for s in storage]}")

    metrics = kubectl("get", "--raw", "/apis/metrics.k8s.io/v1beta1").returncode == 0
    report("metrics-server", metrics, "pod CPU and memory on the Infrastructure page", required=False)

    existing = kubectl("get", "namespace", args.namespace, "-o", "json")
    if existing.returncode == 0:
        releases = kubectl("-n", args.namespace, "get", "secret", "-l", "owner=helm", "-o",
                           "jsonpath={.items[*].metadata.labels.name}").stdout.split()
        report("namespace", True, f"{args.namespace} exists; Helm releases in it: {sorted(set(releases)) or 'none'}",
               required=False)
    else:
        report("namespace", True, f"{args.namespace} does not exist yet; the install creates it", required=False)

    can = {verb: kubectl("auth", "can-i", verb, *resource).stdout.strip() == "yes"
           for verb, resource in (("create", ("namespaces",)), ("create", ("roles", "-n", args.namespace)),
                                  ("create", ("networkpolicies", "-n", args.namespace)))}
    report("permissions", all(can.values()), "can create namespaces, Roles and NetworkPolicies" if all(can.values())
           else f"missing rights: {[k for k, v in can.items() if not v]}")

    if args.skip_network_test:
        report("network-policies", True, "not tested (--skip-network-test)", required=False)
    else:
        enforced = network_policies_enforced()
        report("network-policies", bool(enforced),
               "enforced" if enforced else ("pods cannot reach each other; check the CNI" if enforced is None
                                            else "NOT enforced: notebooks and runs would not be isolated"))

    print(json.dumps({"context": context, "ready": not missing, "missing": missing, "checks": summary}))
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
