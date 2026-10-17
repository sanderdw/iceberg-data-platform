# Kubernetes

This page is about building and testing the charts. To install the platform on a cluster, see
[Install on Kubernetes](../install-kubernetes.md). Lessons from building it are in
`dev/kubernetes/LESSONS.md`.

The Helm chart in `deploy/helm/iceberg-data-platform` runs the same images as Compose. Extension
charts live in their own folders (`extensions/<id>/deploy/helm/iceberg-<id>`) and are versioned and
released with the extension.

## How the chart maps Compose

| Compose | Chart |
| --- | --- |
| Services on `iceberg-platform_default` | Services with the same names in the release namespace, so `http://keycloak:8080` and the Bridge aliases stay valid |
| `scripts.setup` writes `.env` | The Secret `iceberg-platform`, generated once with Helm's `lookup` and kept |
| `scripts.setup` writes the realm import | An init container runs `scripts.setup --realm-json` in the portal image |
| `keycloak start-dev` with its own database | `keycloak start` on Postgres (database `keycloak`) |
| The `postgres` service | The bundled StatefulSet, or `postgres.external`: no pod, and Polaris, Keycloak and the monitor connect to your server with `sslmode` (`test/test_helm_chart.py` renders both) |
| `polaris-bootstrap` | An init container of the Polaris pod, or with `polaris.external` (the official Apache Polaris chart) a hook Job before the Keycloak bootstrap. The official chart's values are rendered to the ConfigMap `iceberg-polaris-values`; `POLARIS=official sh dev/kubernetes/kind_check.sh` tests that path |
| `keycloak-bootstrap` on every `up` | A post-install and post-upgrade hook Job |
| `.env.bridge` per extension | The Secret `iceberg-bridge-<id>`, from the `extensions` value |
| Docker socket in `users`, `monitor` and the launchers | The backends below |

## Backends

Every Docker-specific part has a switch whose default is Docker, so Compose behaves as before:

- **Notebooks**: `NOTEBOOK_BACKEND=docker|kubernetes|none`. `user_portal/runtime_kubernetes.py` starts
  one pod per session in `NOTEBOOK_NAMESPACE`. The team's storage is a ReadWriteOnce PVC, so sessions of
  one team and environment share a node (pod affinity). The portal proxies to the pod IP, which keeps
  the token endpoint's address check.
- **Monitor**: `MONITOR_BACKEND=kubernetes` lists the pods of `MONITOR_NAMESPACES` and their
  `metrics.k8s.io` usage. It runs next to RustFS, which lets it mount the RustFS volume read-only.
- **dbt and Ingestion launchers**: `LAUNCHER_BACKEND=kubernetes` runs one Job per run in
  `RUNS_NAMESPACE`. The runner (`runner/remote.py`) fetches its staged `/work` from
  `GET /jobs/{id}/input` and returns `out/` with `PUT /jobs/{id}/output`, using a ticket that is valid
  for one run (see the dbt extension's `DECISIONS.md`). The launcher is a second container in the portal
  pod, so both share the artifacts volume. Only that container gets a service account token.

`server/kube.py` is the small API client the core uses. The extensions have their own copy in
`launcher/kube.py`, because they never import platform code.

## Test on kind

The CI job `kubernetes` does this on every change:

```bash
sh dev/kubernetes/tools.sh .local/bin && export PATH="$PWD/.local/bin:$PATH"
kind create cluster
docker build -t local/iceberg-data-platform-portal:dev .
docker build -t local/iceberg-data-platform-users:dev -f user_portal/Dockerfile .
docker build -t local/iceberg-data-platform-notebook:dev -f user_portal/notebook/Dockerfile .
for image in portal users notebook; do kind load docker-image local/iceberg-data-platform-$image:dev; done
helm install iceberg deploy/helm/iceberg-data-platform -n iceberg --create-namespace \
  -f deploy/helm/iceberg-data-platform/values-localhost.yaml \
  --set image.registry=local --set image.tag=dev --set image.pullPolicy=Never --wait --timeout 15m
```

Port-forward the Services to the Compose ports, write `.env` with
`python3 dev/kubernetes/local_env.py iceberg > .env`, and the smoke tests run unchanged.
`dev/kubernetes/notebook_check.py` checks what a notebook pod can and cannot reach.

## Test on a cloud cluster

`dev/kubernetes/scaleway.py` creates a Scaleway Kapsule cluster with Traefik and cert-manager, pushes
the working tree's images to a private registry, installs the chart on `<ip>.sslip.io` hostnames and
checks the routed and refused paths. It reads the API key from `scaleway.env` (git-ignored). Run
`scaleway.py down` after every session; `status` shows what is still running. `down` asks whether to
keep the reserved IP, so the next cluster gets the same hostnames; without a terminal it keeps it, and
`--release-ip` releases it without asking.
