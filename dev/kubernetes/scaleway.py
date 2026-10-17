"""Create, use and destroy a Scaleway Kapsule test cluster for the Kubernetes install option.

Maintainer tooling, not part of the product. Credentials come from ./scaleway.env (SCW_ACCESS_KEY,
SCW_SECRET_KEY, SCW_DEFAULT_PROJECT_ID, SCW_DEFAULT_REGION, SCW_DEFAULT_ZONE); state, the
kubeconfig and saved certificates go to .local/scaleway/, both git-ignored.

    python3 dev/kubernetes/scaleway.py up        # reserve the IP, create the cluster, install Traefik and cert-manager
    python3 dev/kubernetes/scaleway.py hello     # test Ingress with a Let's Encrypt staging certificate
    python3 dev/kubernetes/scaleway.py images    # build the working tree's images and push them to a private registry
    python3 dev/kubernetes/scaleway.py deploy    # install the platform chart (add --prod for trusted certificates)
    python3 dev/kubernetes/scaleway.py extension dbt   # register an extension and install its chart
    python3 dev/kubernetes/scaleway.py verify    # check the routed and refused paths per host
    python3 dev/kubernetes/scaleway.py postgres up <namespace>   # Managed PostgreSQL on the cluster's network
    python3 dev/kubernetes/scaleway.py down      # delete the cluster and its volumes; keeps the IP
    python3 dev/kubernetes/scaleway.py status    # what is still running, and costing money

Without a DNS zone the base domain is <ip-with-dashes>.sslip.io; set BASE_DOMAIN in scaleway.env
to use a real wildcard record instead.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".local" / "scaleway"
KUBECONFIG = STATE / "kubeconfig"
TAG = "iceberg-dev"
NAME = "iceberg-dev"
NAMESPACE = "iceberg"
TRAEFIK_CHART = "41.6.0"
CERT_MANAGER_CHART = "v1.21.2"
CHART = ROOT / "deploy" / "helm" / "iceberg-data-platform"
REGISTRY_NAMESPACE = "iceberg-dev"
# Images built from the working tree: name, Dockerfile, build context.
IMAGES = [
    ("iceberg-data-platform-portal", "Dockerfile", "."),
    ("iceberg-data-platform-users", "user_portal/Dockerfile", "."),
    ("iceberg-data-platform-notebook", "user_portal/notebook/Dockerfile", "."),
]
EXTENSION_IMAGES = {
    "dbt": [("iceberg-dbt", "extensions/dbt/Dockerfile", "extensions/dbt"),
            ("iceberg-dbt-runner", "extensions/dbt/runner/Dockerfile", "extensions/dbt")],
    "ingestion": [("iceberg-ingestion", "extensions/ingestion/Dockerfile", "extensions/ingestion"),
                  ("iceberg-ingestion-runner", "extensions/ingestion/runner/Dockerfile", "extensions/ingestion")],
    "conversationalbi": [
        ("iceberg-conversationalbi", "extensions/conversationalbi/Dockerfile", "extensions/conversationalbi"),
        ("iceberg-conversationalbi-runtime", "extensions/conversationalbi/runtime/Dockerfile",
         "extensions/conversationalbi/runtime")],
}


def load_env():
    env_file = ROOT / "scaleway.env"
    if not env_file.is_file():
        sys.exit(f"Missing {env_file.relative_to(ROOT)}; it needs SCW_ACCESS_KEY, SCW_SECRET_KEY and SCW_DEFAULT_PROJECT_ID")
    for line in env_file.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))
    os.environ.setdefault("SCW_DEFAULT_REGION", "nl-ams")
    os.environ.setdefault("SCW_DEFAULT_ZONE", "nl-ams-1")
    os.environ["KUBECONFIG"] = str(KUBECONFIG)
    bin_dir = ROOT / ".local" / "bin"
    if bin_dir.is_dir():
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    for tool in ("scw", "helm", "kubectl"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found; run: sh dev/kubernetes/tools.sh .local/bin")


def run(*args, capture=False, check=True, stdin=None):
    result = subprocess.run(args, text=True, input=stdin, capture_output=capture, check=False)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        sys.exit(f"Failed: {' '.join(args[:4])} ...\n{detail}")
    return result.stdout if capture else result.returncode


def scw(*args):
    out = run("scw", *args, "-o", "json", capture=True)
    return json.loads(out) if out.strip() else None


def zone():
    return os.environ["SCW_DEFAULT_ZONE"]


def region():
    return os.environ["SCW_DEFAULT_REGION"]


def flexible_ip(create=False):
    ips = [ip for ip in scw("lb", "ip", "list", f"zone={zone()}") or [] if TAG in (ip.get("tags") or [])]
    if ips or not create:
        return ips[0] if ips else None
    return scw("lb", "ip", "create", f"zone={zone()}", f"tags.0={TAG}")


def base_domain(ip):
    return os.environ.get("BASE_DOMAIN") or f"{ip['ip_address'].replace('.', '-')}.sslip.io"


def cluster():
    found = [c for c in scw("k8s", "cluster", "list", f"region={region()}", f"name={NAME}") or [] if c["name"] == NAME]
    return found[0] if found else None


def up(args):
    STATE.mkdir(parents=True, exist_ok=True)
    ip = flexible_ip(create=True)
    print(f"Load balancer IP {ip['ip_address']}; base domain {base_domain(ip)}")
    existing = cluster()
    if not existing:
        print(f"Creating Kapsule cluster {NAME} ({args.nodes} x {args.node_type}, autoscaling 1-{args.max_nodes})")
        existing = scw("k8s", "cluster", "create", f"region={region()}", f"name={NAME}", "type=kapsule", "cni=cilium",
                       "version=latest", f"tags.0={TAG}", "pools.0.name=default", f"pools.0.node-type={args.node_type}",
                       f"pools.0.size={args.nodes}", "pools.0.min-size=1", f"pools.0.max-size={args.max_nodes}",
                       "pools.0.autoscaling=true", "pools.0.autohealing=true", f"pools.0.zone={zone()}",
                       f"pools.0.tags.0={TAG}", "auto-upgrade.enable=false")
    run("scw", "k8s", "cluster", "wait", existing["id"], f"region={region()}", "timeout=20m")
    KUBECONFIG.write_text(run("scw", "k8s", "kubeconfig", "get", existing["id"], f"region={region()}", capture=True))
    KUBECONFIG.chmod(0o600)
    for _ in range(60):
        if run("kubectl", "get", "nodes", "-o", "name", capture=True, check=False).strip():
            break
        time.sleep(10)
    run("kubectl", "wait", "node", "--all", "--for=condition=Ready", "--timeout=15m")
    install_ingress(ip)
    install_cert_manager()
    print(f"\nReady. export KUBECONFIG={KUBECONFIG.relative_to(ROOT)}\nBase domain: {base_domain(ip)}")


def install_ingress(ip):
    # The Scaleway load balancer keeps the reserved IP and passes client addresses with proxy protocol v2.
    # use-hostname makes in-cluster traffic to the public address go through the load balancer too, so
    # it carries the proxy protocol header (cert-manager's HTTP-01 self check needs that).
    prefix = "service.beta.kubernetes.io/scw-loadbalancer"
    values = {
        "service": {"annotations": {
            f"{prefix}-ip-ids": ip["id"],
            f"{prefix}-zone": zone(),
            f"{prefix}-proxy-protocol-v2": "*",
            f"{prefix}-use-hostname": "true",
        }},
        "ports": {name: {"proxyProtocol": {"trustedIPs": ["0.0.0.0/0"]},
                         "forwardedHeaders": {"trustedIPs": []}} for name in ("web", "websecure")},
        "ingressClass": {"enabled": True, "isDefaultClass": True, "name": "traefik"},
        "providers": {"kubernetesIngress": {"publishedService": {"enabled": True}}},
        "accessLog": {"enabled": True},
    }
    path = STATE / "traefik-values.json"
    path.write_text(json.dumps(values))
    run("helm", "upgrade", "--install", "traefik", "oci://ghcr.io/traefik/helm/traefik", "--version", TRAEFIK_CHART,
        "--namespace", "traefik", "--create-namespace", "-f", str(path), "--wait", "--timeout", "10m")


def install_cert_manager():
    run("helm", "upgrade", "--install", "cert-manager", "oci://quay.io/jetstack/charts/cert-manager",
        "--version", CERT_MANAGER_CHART, "--namespace", "cert-manager", "--create-namespace",
        "--set", "crds.enabled=true", "--wait", "--timeout", "10m")
    email = os.environ.get("ACME_EMAIL")
    issuers = []
    for name, server in (("letsencrypt-staging", "https://acme-staging-v02.api.letsencrypt.org/directory"),
                         ("letsencrypt-prod", "https://acme-v02.api.letsencrypt.org/directory")):
        acme = {"server": server, "privateKeySecretRef": {"name": f"{name}-account"},
                "solvers": [{"http01": {"ingress": {"ingressClassName": "traefik"}}}]}
        if email:
            acme["email"] = email
        issuers.append({"apiVersion": "cert-manager.io/v1", "kind": "ClusterIssuer",
                        "metadata": {"name": name}, "spec": {"acme": acme}})
    run("kubectl", "apply", "-f", "-", stdin=json.dumps({"apiVersion": "v1", "kind": "List", "items": issuers}))


def issuer(args):
    return "letsencrypt-prod" if args.prod else "letsencrypt-staging"


def hello(args):
    domain = base_domain(require_ip())
    host = f"hello.{domain}"
    manifest = {"apiVersion": "v1", "kind": "List", "items": [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "hello"}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "hello", "namespace": "hello"},
         "spec": {"selector": {"matchLabels": {"app": "hello"}}, "template": {
             "metadata": {"labels": {"app": "hello"}},
             "spec": {"containers": [{"name": "whoami", "image": "traefik/whoami:v1.11.0",
                                      "ports": [{"containerPort": 80}]}]}}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "hello", "namespace": "hello"},
         "spec": {"selector": {"app": "hello"}, "ports": [{"port": 80}]}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "Ingress",
         "metadata": {"name": "hello", "namespace": "hello",
                      "annotations": {"cert-manager.io/cluster-issuer": issuer(args)}},
         "spec": {"ingressClassName": "traefik", "tls": [{"hosts": [host], "secretName": "hello-tls"}],
                  "rules": [{"host": host, "http": {"paths": [{"path": "/", "pathType": "Prefix", "backend": {
                      "service": {"name": "hello", "port": {"number": 80}}}}]}}]}},
    ]}
    if args.delete:
        run("kubectl", "delete", "namespace", "hello", "--ignore-not-found")
        return
    run("kubectl", "apply", "-f", "-", stdin=json.dumps(manifest))
    run("kubectl", "-n", "hello", "wait", "certificate/hello-tls", "--for=condition=Ready", "--timeout=5m")
    print(f"https://{host} has a {issuer(args)} certificate")


def docker():
    # In WSL without Docker Desktop integration only docker.exe reaches the daemon.
    return os.environ.get("DOCKER") or shutil.which("docker") or shutil.which("docker.exe") or "docker"


def registry():
    found = [n for n in scw("registry", "namespace", "list", f"region={region()}", f"name={REGISTRY_NAMESPACE}") or []
             if n["name"] == REGISTRY_NAMESPACE]
    namespace = found[0] if found else scw("registry", "namespace", "create", f"region={region()}",
                                           f"name={REGISTRY_NAMESPACE}", "is-public=false")
    return namespace["endpoint"]


def images(args):
    endpoint = registry()
    host = endpoint.split("/")[0]
    run(docker(), "login", host, "-u", "nologin", "--password-stdin", stdin=os.environ["SCW_SECRET_KEY"])
    tag = args.tag
    selected = IMAGES if not args.extension else [i for e in args.extension for i in EXTENSION_IMAGES[e]]
    for name, dockerfile, context in selected:
        if args.only and name not in args.only:
            continue
        image = f"{endpoint}/{name}:{tag}"
        print(f"Building {image}")
        run(docker(), "build", "--platform", "linux/amd64", "-t", image, "-f", dockerfile, context)
        run(docker(), "push", image)
    # The cluster pulls with the same key; the chart copies this Secret to the notebooks namespace.
    for namespace in (NAMESPACE,):
        run("kubectl", "create", "namespace", namespace, check=False, capture=True)
        secret = run("kubectl", "-n", namespace, "create", "secret", "docker-registry", "registry", f"--docker-server={host}",
                     "--docker-username=nologin", f"--docker-password={os.environ['SCW_SECRET_KEY']}",
                     "--dry-run=client", "-o", "json", capture=True)
        run("kubectl", "apply", "-f", "-", stdin=secret, capture=True)
    print(f"Pushed to {endpoint} with tag {tag}; deploy with --image-tag {tag}")


def require_ip():
    ip = flexible_ip()
    if not ip:
        sys.exit("No reserved IP; run up first")
    return ip


def deploy(args):
    domain = base_domain(require_ip())
    command = ["helm", "upgrade", "--install", "iceberg", str(CHART), "--namespace", NAMESPACE, "--create-namespace",
               "--reset-then-reuse-values",
               "--set", f"baseDomain={domain}", "--set", f"tls.clusterIssuer={issuer(args)}",
               "--wait", "--timeout", "15m"]
    if args.image_tag:
        command += ["--set", f"image.tag={args.image_tag}", "--set", f"image.registry={registry()}",
                    "--set", "imagePullSecrets[0].name=registry"]
    for item in args.set or []:
        command += ["--set", item]
    run(*command)
    print(f"Admin portal: https://admin.{domain}")


def extension(args):
    """Register the extension in the platform release, then install its chart next to it."""
    hosts = {"dbt": "dbt", "ingestion": "ingest", "conversationalbi": "cbi"}
    args.set = [*(args.set or []), f"extensions.{args.id}.host={hosts[args.id]}"]
    args.image_tag = args.image_tag or "dev"
    deploy(args)
    chart = ROOT / "extensions" / args.id / "deploy" / "helm" / f"iceberg-{args.id}"
    command = ["helm", "upgrade", "--install", args.id, str(chart), "--namespace", NAMESPACE,
               "--set", f"tls.clusterIssuer={issuer(args)}", "--set", f"image.tag={args.image_tag}",
               "--set", f"image.registry={registry()}", "--set", "imagePullSecrets[0].name=registry",
               "--wait", "--timeout", "10m"]
    for item in args.extension_set or []:
        command += ["--set", item]
    run(*command)


def password():
    import secrets
    import string

    # Scaleway asks for upper and lower case, a digit and a symbol.
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(32)) + "aZ9-"


def database():
    found = [i for i in scw("rdb", "instance", "list", f"region={region()}", f"name={NAME}-pg") or []
             if i["name"] == f"{NAME}-pg"]
    return found[0] if found else None


def postgres(args):
    """A Managed PostgreSQL with the two databases and users, and the chart's Secret in the namespace."""
    state = STATE / "postgres.json"
    if args.action == "down":
        found = database()
        if found:
            scw("rdb", "instance", "delete", found["id"], f"region={region()}")
            print(f"Deleted {found['name']}")
        state.unlink(missing_ok=True)
        return
    found = database()
    if not found:
        network = cluster()["private_network_id"]
        print(f"Creating {NAME}-pg on the cluster's private network (no public endpoint)")
        found = scw("rdb", "instance", "create", f"region={region()}", f"name={NAME}-pg", "engine=PostgreSQL-17",
                    "node-type=db-play2-pico", "volume-type=sbs_5k", "volume-size=10GB", f"tags.0={TAG}",
                    "user-name=iceberg-owner", f"password={password()}", "is-ha-cluster=false",
                    f"init-endpoints.0.private-network.private-network-id={network}",
                    "init-endpoints.0.private-network.enable-ipam=true")
    run("scw", "rdb", "instance", "wait", found["id"], f"region={region()}", "timeout=30m", capture=True)
    found = scw("rdb", "instance", "get", found["id"], f"region={region()}")
    host = next(e["ip"] for e in found["endpoints"] if e.get("private_network"))
    passwords = json.loads(state.read_text()) if state.exists() else {}
    for name in ("polaris", "keycloak"):
        if name not in passwords:
            passwords[name] = password()
            scw("rdb", "user", "create", f"instance-id={found['id']}", f"region={region()}", f"name={name}",
                f"password={passwords[name]}", "is-admin=false")
            scw("rdb", "database", "create", f"instance-id={found['id']}", f"region={region()}", f"name={name}")
            scw("rdb", "privilege", "set", f"instance-id={found['id']}", f"region={region()}",
                f"database-name={name}", f"user-name={name}", "permission=all")
            state.write_text(json.dumps(passwords))
            state.chmod(0o600)
    state.write_text(json.dumps(passwords))
    state.chmod(0o600)
    run("kubectl", "create", "namespace", args.namespace, check=False, capture=True)
    secret = run("kubectl", "-n", args.namespace, "create", "secret", "generic", "iceberg-postgres",
                 f"--from-literal=POLARIS_DB_PASSWORD={passwords['polaris']}",
                 f"--from-literal=KEYCLOAK_DB_PASSWORD={passwords['keycloak']}", "--dry-run=client", "-o", "json",
                 capture=True)
    run("kubectl", "apply", "-f", "-", stdin=secret, capture=True)
    print(f"Postgres at {host}:5432; Secret {args.namespace}/iceberg-postgres. Install with:\n"
          f"  --set postgres.external.enabled=true --set postgres.external.host={host} "
          "--set postgres.external.existingSecret=iceberg-postgres")


def verify(args):
    import ssl
    import urllib.error
    import urllib.request

    domain = base_domain(require_ip())
    context = ssl.create_default_context() if args.prod else ssl._create_unverified_context()
    # Unrouted paths answer 404; paths sent to the chart's endpoint-less ingress-deny Service answer
    # 503 (Traefik) or 404, depending on the controller. Either way the request never reaches the service.
    blocked = (404, 503)
    checks = [
        ("admin", "/api/health", (200,)), ("users", "/api/health", (200,)),
        ("users", "/internal/notebooks/x/token", blocked),
        ("auth", "/realms/iceberg/.well-known/openid-configuration", (200,)), ("auth", "/admin/", blocked),
        ("auth", "/realms/master/", blocked), ("catalog", "/api/catalog/v1/config", (401,)),
        ("catalog", "/api/management/v1/catalogs", blocked), ("s3", "/rustfs/admin/v3/info", blocked),
        ("s3", "/minio/health/live", blocked),
    ]
    failed = 0
    for host, path, expected in checks:
        url = f"https://{host}.{domain}{path}"
        try:
            status = urllib.request.urlopen(url, context=context, timeout=20).status
        except urllib.error.HTTPError as error:
            status = error.code
        except Exception as error:  # noqa: BLE001 - report any connection problem as a failed check
            status = type(error).__name__
        ok = status in expected
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} {url} -> {status} (expected {' or '.join(map(str, expected))})")
    sys.exit(1 if failed else 0)


def down(args):
    if database():
        postgres(argparse.Namespace(action="down"))
    found = cluster()
    if found:
        print(f"Deleting cluster {found['id']} with its load balancers and volumes")
        scw("k8s", "cluster", "delete", found["id"], f"region={region()}", "with-additional-resources=true")
        run("scw", "k8s", "cluster", "wait", found["id"], f"region={region()}", "timeout=20m", check=False)
    ip = flexible_ip()
    if ip and args.release_ip:
        scw("lb", "ip", "delete", ip["id"], f"zone={zone()}")
        print(f"Released IP {ip['ip_address']}")
    elif ip:
        print(f"Kept IP {ip['ip_address']} (a few euro cents a day); --release-ip removes it")
    KUBECONFIG.unlink(missing_ok=True)


def status(args):
    clusters = [c for c in scw("k8s", "cluster", "list", f"region={region()}") or [] if TAG in (c.get("tags") or [])]
    for c in clusters:
        print(f"cluster {c['name']} {c['status']} {c['version']}")
    for lb in scw("lb", "lb", "list", f"zone={zone()}") or []:
        print(f"load balancer {lb['name']} {lb['status']} {lb['type']}")
    for ip in scw("lb", "ip", "list", f"zone={zone()}") or []:
        print(f"ip {ip['ip_address']} tags={ip.get('tags')} lb={ip.get('lb_id')}")
    for instance in scw("rdb", "instance", "list", f"region={region()}") or []:
        print(f"database {instance['name']} {instance['status']} {instance['node_type']}")
    for volume in scw("block", "volume", "list", f"zone={zone()}") or []:
        print(f"volume {volume['name']} {volume['size'] // 10**9} GB {volume['status']}")
    if not clusters:
        print("No test cluster running")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("up")
    p.add_argument("--node-type", default=os.environ.get("SCW_NODE_TYPE", "DEV1-L"))
    p.add_argument("--nodes", type=int, default=2)
    p.add_argument("--max-nodes", type=int, default=3)
    p.set_defaults(func=up)
    p = sub.add_parser("hello")
    p.add_argument("--prod", action="store_true")
    p.add_argument("--delete", action="store_true")
    p.set_defaults(func=hello)
    p = sub.add_parser("images")
    p.add_argument("--tag", default="dev")
    p.add_argument("--only", action="append")
    p.add_argument("--extension", action="append", choices=sorted(EXTENSION_IMAGES),
                   help="Build this extension's images instead of the platform's")
    p.set_defaults(func=images)
    p = sub.add_parser("deploy")
    p.add_argument("--prod", action="store_true")
    p.add_argument("--image-tag")
    p.add_argument("--set", action="append")
    p.set_defaults(func=deploy)
    p = sub.add_parser("extension")
    p.add_argument("id", choices=sorted(EXTENSION_IMAGES))
    p.add_argument("--prod", action="store_true")
    p.add_argument("--image-tag")
    p.add_argument("--set", action="append", help="A value for the platform chart")
    p.add_argument("--extension-set", action="append", help="A value for the extension's chart")
    p.set_defaults(func=extension)
    p = sub.add_parser("verify")
    p.add_argument("--prod", action="store_true")
    p.set_defaults(func=verify)
    p = sub.add_parser("down")
    p.add_argument("--release-ip", action="store_true")
    p.set_defaults(func=down)
    p = sub.add_parser("postgres")
    p.add_argument("action", choices=["up", "down"])
    p.add_argument("namespace", nargs="?", default=NAMESPACE)
    p.set_defaults(func=postgres)
    sub.add_parser("status").set_defaults(func=status)
    args = parser.parse_args()
    load_env()
    args.func(args)


if __name__ == "__main__":
    main()
