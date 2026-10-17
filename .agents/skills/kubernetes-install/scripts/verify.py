"""Check an installed Iceberg Data Platform from the outside: pods, certificates, and that every host
serves what it should and refuses what must stay internal.

    python3 verify.py [--namespace iceberg] [--insecure]

Reads the hosts from the namespace's Ingresses, so it needs no settings. --insecure accepts
untrusted certificates (Let's Encrypt staging). Prints one line per check and a JSON summary as its
last line; exits 1 when a check fails. Standard library only; prints no secret.
"""

import argparse
import json
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

# The backend Service of a host tells what it is. (path, accepted status codes)
BLOCKED = (404, 503)
CHECKS = {
    "portal": [("/api/health", (200,))],
    "users": [("/api/health", (200,)), ("/internal/notebooks/x/token", BLOCKED)],
    "keycloak": [("/realms/iceberg/.well-known/openid-configuration", (200,)), ("/admin/", BLOCKED),
                 ("/realms/master/", BLOCKED)],
    "polaris": [("/api/catalog/v1/config", (401,)), ("/api/management/v1/catalogs", BLOCKED)],
    "rustfs": [("/rustfs/admin/v3/info", BLOCKED), ("/minio/health/live", BLOCKED)],
    "dbt-portal": [("/api/health", (200,))],
    "ing-gateway": [("/api/health", (200,))],
    "cbi-gateway": [("/api/health", (200,))],
}
NAMES = {"portal": "administration portal", "users": "user portal", "keycloak": "sign-in", "polaris": "catalog",
         "rustfs": "S3", "dbt-portal": "dbt", "ing-gateway": "Ingestion", "cbi-gateway": "Conversational BI"}


def kubectl_json(*args):
    result = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True, check=False)
    return json.loads(result.stdout) if result.returncode == 0 else {}


def hosts(namespace):
    """{service: host} from the Ingress rules; a host's first path names its service."""
    found = {}
    for ingress in kubectl_json("-n", namespace, "get", "ingress").get("items", []):
        for rule in ingress["spec"].get("rules", []):
            backend = rule["http"]["paths"][0]["backend"]["service"]["name"]
            found.setdefault(backend, rule["host"])
    return found


def status(url, context):
    try:
        return urllib.request.urlopen(url, context=context, timeout=20).status
    except urllib.error.HTTPError as error:
        return error.code
    except Exception as error:  # noqa: BLE001 - any connection problem is a failed check
        return type(error).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--namespace", default="iceberg")
    parser.add_argument("--insecure", action="store_true", help="accept untrusted certificates")
    args = parser.parse_args()
    results, failed = [], 0

    pods = kubectl_json("-n", args.namespace, "get", "pods").get("items", [])
    for pod in pods:
        phase = pod["status"].get("phase")
        ready = all(c.get("ready") for c in pod["status"].get("containerStatuses", []))
        ok = phase == "Succeeded" or (phase == "Running" and ready)
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} pod {pod['metadata']['name']}: {phase}{'' if ready else ', not ready'}")
    for certificate in kubectl_json("-n", args.namespace, "get", "certificate").get("items", []):
        ok = any(c["type"] == "Ready" and c["status"] == "True" for c in certificate.get("status", {}).get("conditions", []))
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} certificate {certificate['metadata']['name']}: {'ready' if ok else 'not ready'}")

    context = ssl._create_unverified_context() if args.insecure else ssl.create_default_context()
    found = hosts(args.namespace)
    if not found:
        print(f"FAIL no Ingress in namespace {args.namespace}")
        failed += 1
    for service, host in sorted(found.items()):
        for path, expected in CHECKS.get(service, []):
            url = f"https://{host}{path}"
            code = status(url, context)
            ok = code in expected
            failed += not ok
            results.append({"url": url, "status": code, "ok": ok})
            print(f"{'PASS' if ok else 'FAIL'} {url} -> {code} (expected {' or '.join(map(str, expected))})")
    print(json.dumps({"ok": not failed, "failed": failed,
                      "addresses": {NAMES.get(s, s): f"https://{h}" for s, h in sorted(found.items())}}))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
