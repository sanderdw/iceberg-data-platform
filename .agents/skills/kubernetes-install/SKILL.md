---
name: kubernetes-install
description: Install, upgrade or remove the Iceberg Data Platform on a Kubernetes cluster with its Helm charts. Checks that the cluster is ready (ingress controller, cert-manager, storage, NetworkPolicy enforcement), asks for the domain, certificates, bundled or managed PostgreSQL and the extensions (dbt, Ingestion, Conversational BI), installs them, verifies every host from the outside and reports where to sign in. Use when the user wants the platform on Kubernetes, on a cloud provider's cluster or on kind, or wants to upgrade, extend or uninstall such an installation.
---

# Install the Iceberg Data Platform on Kubernetes

The platform's charts are published at `oci://ghcr.io/sanderdw/charts`: `iceberg-data-platform` and one chart per extension (`iceberg-dbt`, `iceberg-ingestion`, `iceberg-conversationalbi`). The user documentation is [Install on Kubernetes](https://sanderdw.github.io/iceberg-data-platform/install-kubernetes/); this skill follows it. Commands refer to this skill's files at `.agents/skills/kubernetes-install`.

You need `kubectl` and `helm` (3.15 or later) on PATH, and `python3` for the scripts. Install nothing on the user's computer without asking.

**Rules for the whole skill:**
- Work only in the current kubectl context, after the user confirmed it (step 1), and only in the platform's namespace (default `iceberg`) and the namespaces the charts create (`<namespace>-notebooks`, `<namespace>-dbt-runs`, `<namespace>-ingestion-runs`). Installing an ingress controller or cert-manager changes the whole cluster: offer it, never do it unasked.
- **Ask before every `helm install`, `helm upgrade`, `helm uninstall` and `kubectl delete`**, and show the command first. Use your agent's question tool if it has one; otherwise ask in chat and wait.
- Never print a credential except the administrator's one-time password at the end (step 7). Pass API keys and database passwords through Secrets created with `kubectl create secret ... --from-literal`, read from environment variables the user sets, never as `--set` values in a command you show.
- On an error, report what failed and the command's output. Don't retry with other settings unasked.

## 1. Confirm the cluster

Run `kubectl config current-context` and `kubectl cluster-info`. Tell the user which cluster this is and ask them to confirm that you install there, and in which namespace (`iceberg` by default). Ask whether this is a new installation, an upgrade (step 8), a change of extensions (step 6) or a removal (step 9).

## 2. Preflight

```bash
python3 .agents/skills/kubernetes-install/scripts/preflight.py --namespace <namespace>
```

It checks the Kubernetes version (1.32 or later), IngressClasses, cert-manager and its ready ClusterIssuers, a default StorageClass, metrics-server, the user's rights, and whether the cluster enforces NetworkPolicies. For the last check it starts two `busybox` pods in a temporary namespace, which it deletes again; tell the user before running it (or add `--skip-network-test`). The last line is JSON.

What is missing:
- **No NetworkPolicy enforcement:** stop. Notebooks and runs would not be isolated. The user needs a CNI that enforces policies (Cilium, Calico, or kind's kindnet).
- **No ingress controller:** offer to install Traefik:
  `helm upgrade --install traefik oci://ghcr.io/traefik/helm/traefik --version 41.6.0 -n traefik --create-namespace --set ingressClass.isDefaultClass=true --wait`.
  On a cloud provider this creates a load balancer, which costs money; say so.
- **No cert-manager or ClusterIssuer:** offer cert-manager
  (`helm upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager --version v1.21.2 -n cert-manager --create-namespace --set crds.enabled=true --wait`)
  and a Let's Encrypt ClusterIssuer with the HTTP-01 solver for the IngressClass. Use the staging server (`https://acme-staging-v02.api.letsencrypt.org/directory`) first; switch to production (`https://acme-v02.api.letsencrypt.org/directory`) once it works. Let's Encrypt limits how often a domain gets new certificates.
- **No metrics-server:** a warning only; the Infrastructure page then shows no CPU and memory.
- **kind or another local cluster without ingress:** skip ingress and certificates and use the chart's `values-localhost.yaml` with port-forwards, as in the documentation's "Try it on your computer".

## 3. Ask the choices

Ask these together, with a recommended answer for each:

1. **Domain.** Every portal gets `<host>.<domain>`: `admin`, `users`, `auth`, `catalog`, `s3`, plus `dbt`, `ingest`, `cbi` for extensions. The user needs a wildcard DNS record `*.<domain>` pointing at the ingress controller's load balancer (`kubectl -n <ingress namespace> get svc` shows its address). Without a domain, for a test, use `<address with dashes>.sslip.io`, such as `51-158-173-247.sslip.io`. The domain is fixed once installed: Keycloak keeps the first addresses.
2. **Certificates.** The ClusterIssuer from step 2; staging first for a new domain.
3. **PostgreSQL.** Bundled (one pod, fine for tests and demos) or a managed PostgreSQL 15+ such as the cloud provider's (recommended for real use: backups and high availability). Object storage stays RustFS either way: the platform needs RustFS's per-user accounts and short-lived, scoped credentials, which provider object storage does not offer. For managed Postgres see step 4.
4. **Polaris.** The chart's own (one pod, the default) or the official Apache Polaris chart, for replicas, autoscaling and metrics. The official chart's Polaris still belongs to this platform only; see step 5b.
5. **Extensions.** None, or any of dbt, Ingestion (dlt) and Conversational BI. Conversational BI needs an LLM: a Pydantic AI model string such as `anthropic:claude-sonnet-5-5` and its provider's key.
6. **Demo data.** Off by default. With `demo=true` the platform creates demo accounts and a demo team, for a class or a test.
7. **Version.** The newest chart: `helm show chart oci://ghcr.io/sanderdw/charts/iceberg-data-platform | grep '^version:'`, unless the user wants a specific one.

## 4. Managed PostgreSQL (only when chosen)

The user creates the server, reachable from the cluster (preferably on its private network, without a public endpoint), with two databases and a user for each that owns it: `polaris` and `keycloak`. Offer the provider's commands when you know them; for Scaleway they are in the documentation's [Use a managed PostgreSQL](https://sanderdw.github.io/iceberg-data-platform/install-kubernetes/#use-a-managed-postgresql). Then ask the user to put both passwords in environment variables, and create the Secret:

```bash
kubectl create namespace <namespace>   # if it does not exist
kubectl -n <namespace> create secret generic iceberg-postgres \
  --from-literal=POLARIS_DB_PASSWORD="$POLARIS_DB_PASSWORD" --from-literal=KEYCLOAK_DB_PASSWORD="$KEYCLOAK_DB_PASSWORD"
```

## 5. Install the platform

Build the command from the choices, show it, ask, then run it:

```bash
helm upgrade --install iceberg oci://ghcr.io/sanderdw/charts/iceberg-data-platform --version <version> \
  -n <namespace> --create-namespace \
  --set baseDomain=<domain> --set ingress.className=<class> --set tls.clusterIssuer=<issuer> \
  --wait --timeout 15m
```

Add, when chosen:
- managed PostgreSQL: `--set postgres.external.enabled=true --set postgres.external.host=<address> --set postgres.external.existingSecret=iceberg-postgres` (and `--set postgres.external.sslmode=verify-full` if the server's certificate is publicly trusted)
- demo data: `--set demo=true`
- the official Polaris chart: `--set polaris.external.enabled=true`, and leave out `demo=true` until step 5b is done
- the extensions' hosts, so one install registers them: `--set extensions.dbt.host=dbt --set extensions.ingestion.host=ingest --set extensions.conversationalbi.host=cbi`

The first install takes 5 to 10 minutes: Keycloak builds itself on the first start and the certificates are issued. If `--wait` times out, run `kubectl -n <namespace> get pods` and `kubectl -n <namespace> get certificate`, and see the documentation's "Check the installation".

### 5b. The official Polaris chart (only when chosen)

The platform wrote that chart's values to a ConfigMap. Save them, check the file is not empty (an empty file starts Polaris in memory, without the platform's database), then install:

```bash
kubectl -n <namespace> get configmap iceberg-polaris-values -o jsonpath='{.data.values\.yaml}' > polaris-values.yaml
grep -q relational-jdbc polaris-values.yaml && helm upgrade --install catalog polaris \
  --repo https://downloads.apache.org/polaris/helm-chart --version <Polaris version in images.polaris> \
  -n <namespace> -f polaris-values.yaml --wait --timeout 10m
```

Own settings, such as `replicaCount`, go in a second `-f` file; more than one replica needs `authentication.tokenBroker.secret`. Then, if the user wants demo data: `helm upgrade iceberg ... --reset-then-reuse-values --set demo=true --wait`. On every later platform upgrade, save the ConfigMap again and upgrade `catalog` with it.

## 6. Install the extensions

Each extension must be registered in the platform release (its `extensions.<id>.host` from step 5; to add one later, `helm upgrade iceberg ... --reset-then-reuse-values --set extensions.<id>.host=<host> --wait`). Then install its chart in the same namespace, with the same ingress settings:

```bash
common="-n <namespace> --set ingress.className=<class> --set tls.clusterIssuer=<issuer> --wait --timeout 10m"
helm upgrade --install dbt oci://ghcr.io/sanderdw/charts/iceberg-dbt $common
helm upgrade --install ingestion oci://ghcr.io/sanderdw/charts/iceberg-ingestion $common
```

Conversational BI takes its LLM from a Secret, so the key never appears in a command or in Helm's values. Ask the user to set the key in an environment variable named after the provider (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, or `AWS_BEARER_TOKEN_BEDROCK` with `AWS_REGION`), then:

```bash
kubectl -n <namespace> create secret generic conversationalbi-llm \
  --from-literal=LLM_MODEL=<model> --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
helm upgrade --install conversationalbi oci://ghcr.io/sanderdw/charts/iceberg-conversationalbi $common \
  --set llm.existingSecret=conversationalbi-llm
```

Ingestion runs reach public sources only. For a source inside the user's network, add `--set runs.allowPrivateSources=true` to its chart, after explaining that runs can then reach private addresses.

## 7. Verify and report

```bash
python3 .agents/skills/kubernetes-install/scripts/verify.py --namespace <namespace>
```

It checks every pod and certificate, then every host from the outside: the portals and sign-in answer, and the Keycloak administration console, the master realm, the Polaris management API and RustFS's administration paths are refused. Add `--insecure` while the certificates come from Let's Encrypt staging. Fix or report every FAIL before you report success.

Then tell the user:
- the addresses from the JSON's `addresses`: the administration portal first, then the user portal and the extensions;
- to sign in to the administration portal as `platform-admin` with the one-time password, which Keycloak replaces at the first sign-in. Read it once and show it once:
  `kubectl -n <namespace> get secret iceberg-platform -o jsonpath='{.data.PLATFORM_ADMIN_PASSWORD}' | base64 -d`;
- that the Secret `iceberg-platform` holds every platform credential and must be backed up with the volumes (and the managed database, if used), together with the extensions' Secrets `iceberg-dbt`, `iceberg-ingestion` (its key decrypts stored source passwords) and `iceberg-conversationalbi`;
- with staging certificates: to switch to the production issuer (`helm upgrade ... --reset-then-reuse-values --set tls.clusterIssuer=<production issuer>`, and the same for each extension) before inviting anyone;
- the MCP addresses for AI agents: `https://users.<domain>/mcp` and `https://admin.<domain>/mcp`, plus `/mcp` on each extension's host;
- the Keycloak administration console, which is never published: `kubectl -n <namespace> port-forward svc/keycloak 8080`, then `http://localhost:8080/admin` as `admin` with `KEYCLOAK_ADMIN_PASSWORD` from the same Secret;
- the next steps: the documentation's [addresses and sign-in](https://sanderdw.github.io/iceberg-data-platform/install-kubernetes/#addresses-and-sign-in) and [first steps](https://sanderdw.github.io/iceberg-data-platform/first-steps/), and the `demo-company` skill for a class.

## 8. Upgrade

Find the installed versions with `helm -n <namespace> list`, and the newest with `helm show chart oci://ghcr.io/sanderdw/charts/<chart>`. Upgrade the platform first, then the official Polaris chart if used (step 5b, with the ConfigMap saved again), then each extension, keeping the settings:

```bash
helm upgrade iceberg oci://ghcr.io/sanderdw/charts/iceberg-data-platform --version <version> -n <namespace> \
  --reset-then-reuse-values --wait --timeout 15m
```

Tell the user before that the portals and Keycloak restart in place and are unavailable for a minute or two. Run step 7's verify afterwards.

## 9. Remove

Ask which of these the user means, and confirm the exact namespace names before deleting anything:

- **Uninstall, keep the data:** `helm uninstall` each extension, then `helm uninstall iceberg -n <namespace>`. The Secrets `iceberg-platform`, `iceberg-dbt`, `iceberg-ingestion` and `iceberg-conversationalbi`, the volumes and the notebook files stay; installing again with the same release name and namespace finds them.
- **Delete everything, including all data:** after the uninstall, `kubectl delete namespace <namespace> <namespace>-notebooks <namespace>-dbt-runs <namespace>-ingestion-runs`. This cannot be undone. A managed PostgreSQL is not part of the cluster; tell the user to delete its databases themselves if they want them gone.
