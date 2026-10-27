# Install on Kubernetes

The Helm chart runs the same platform as the [Compose installation](install.md) on a Kubernetes
cluster. It covers Apache Polaris, RustFS, Keycloak, Postgres and both portals, and starts notebooks
as pods. Extensions have their own charts and install next to it.

An AI agent can do these steps with you: the [`kubernetes-install`](agent-skills.md) skill checks the cluster, asks the choices below, installs after you approve each command, and verifies every host.

## What you need

- Kubernetes 1.32 or later, with a default StorageClass and NetworkPolicy support (most CNIs, such as Cilium and Calico)
- An ingress controller, such as Traefik, with an IngressClass
- [cert-manager](https://cert-manager.io/) with a ClusterIssuer, for example Let's Encrypt with HTTP-01
- A DNS name for each host, usually one wildcard record pointing at the ingress controller's load balancer
- Optionally a managed PostgreSQL 15 or later (see [use a managed PostgreSQL](#use-a-managed-postgresql))
- `helm` 3.15 or later, and metrics-server for the CPU and memory figures on the Infrastructure page

Plain HTTP sign-in only works on `localhost`, so a cluster needs HTTPS. Each portal gets its own host under one base domain:

| Host | Serves |
| --- | --- |
| `admin.<domain>` | Administration portal |
| `users.<domain>` | User portal and notebooks |
| `auth.<domain>` | Keycloak sign-in (only the `iceberg` realm) |
| `catalog.<domain>` | Iceberg REST catalog for your own tools |
| `s3.<domain>` | S3 endpoint for your own tools |

## Install

```bash
helm install iceberg oci://ghcr.io/sanderdw/charts/iceberg-data-platform --version 0.9.10 \
  --namespace iceberg --create-namespace \
  --set baseDomain=data.example.com \
  --set ingress.className=traefik \
  --set tls.clusterIssuer=letsencrypt-prod \
  --wait --timeout 15m
```

Choose the base domain once: Keycloak keeps the addresses of the first installation. Install one
platform per namespace.

The chart prints where to sign in. Sign in to `https://admin.<domain>` as `platform-admin` with the
one-time password from the platform Secret; Keycloak then asks for a new one:

```bash
kubectl -n iceberg get secret iceberg-platform -o jsonpath='{.data.PLATFORM_ADMIN_PASSWORD}' | base64 -d; echo
```

Continue with the [first steps](first-steps.md), using the addresses below. Download
`iceberg_connect.py` from the user portal (**Catalog › Connect from your computer**): it carries the
catalog and S3 addresses of your cluster.

## Addresses and sign-in

| What | Address | Sign-in |
| --- | --- | --- |
| Administration portal | `https://admin.<domain>` | `platform-admin` with the one-time password above |
| User portal and notebooks | `https://users.<domain>` | A Keycloak account an administrator created |
| API documentation | `/docs` on either portal | Portal session |
| User MCP server | `https://users.<domain>/mcp` | Keycloak sign-in through the `iceberg-mcp` client |
| Administration MCP server | `https://admin.<domain>/mcp` | Keycloak sign-in, `platform-admin` role |
| Iceberg REST catalog | `https://catalog.<domain>/api/catalog` | Keycloak token ([connect from your computer](connect.md)) |
| S3 | `https://s3.<domain>` | Credentials the catalog hands out |
| Extensions | `https://dbt.<domain>`, `https://ingest.<domain>`, `https://cbi.<domain>`, each with `/mcp` | Keycloak account |
| Keycloak administration console | `http://localhost:8080/admin` after `kubectl -n iceberg port-forward svc/keycloak 8080` | `admin` with `KEYCLOAK_ADMIN_PASSWORD` from the platform Secret |

MCP clients still listen on `localhost` for their sign-in callback (`mcpCallbackPort`, 3010), as on
Compose; see [AI agents](ai-agents.md). The Keycloak console is never published: it opens only through
the port-forward, for someone with access to the cluster. There is no pgAdmin and no RustFS console.

Every credential is a key in the Secret `iceberg-platform`. With `demo=true` the demo accounts'
passwords are there too, as `DEMO_ADMIN_PASSWORD`, `DEMO_WRITER_PASSWORD`, `DEMO_READER_PASSWORD` and
`DEMO_OUTSIDER_PASSWORD`:

```bash
kubectl -n iceberg get secret iceberg-platform -o jsonpath='{.data.DEMO_WRITER_PASSWORD}' | base64 -d; echo
```

## Postgres, object storage and volumes

Cloud providers offer managed PostgreSQL, object storage and file storage next to their Kubernetes
service. What the platform can use:

| Part | Bundled in the chart | Provider service | Choice |
| --- | --- | --- | --- |
| **Postgres** (Polaris's catalog, Keycloak's users) | One Postgres pod with a block volume | Managed PostgreSQL, such as Scaleway Managed Database | Managed is better beyond a demo: backups, point-in-time recovery, high availability and upgrades, and it stays plain PostgreSQL. See [use a managed PostgreSQL](#use-a-managed-postgresql). |
| **Object storage** (table data) | RustFS on a block volume | S3-compatible object storage, such as Scaleway Object Storage | Keep RustFS. The platform creates a storage account and policy per user through the RustFS administration API, and Polaris hands out short-lived credentials scoped to one table through STS. Provider object storage has neither in this form, so it cannot replace RustFS without giving up that access control. It does suit backups of the RustFS volume. |
| **Notebook files** (per team and environment) | A volume per team from the default StorageClass | The provider's block storage, through its CSI driver | Already the provider's block storage. Choose a StorageClass that keeps volumes when their claim is deleted (`storageClass`, such as `sbs-default-retain` on Scaleway). The volumes are ReadWriteOnce, so the notebooks of one team and environment run on the same node. |

Whatever you choose, back up these together, because each needs the others to restore a working
platform:

- Postgres (the bundled volume `data-postgres-0`, or your managed database) and the RustFS volume
  `data-rustfs-0`;
- the Secret `iceberg-platform`, and the extensions' Secrets `iceberg-dbt`, `iceberg-ingestion` (its
  `ING_SECRET_KEY` encrypts the stored source passwords) and `iceberg-conversationalbi`;
- the extensions' volumes `dbt-state` (projects and their Git repositories), `dbt-artifacts`,
  `ing-state` and `ing-artifacts`, and the notebook volumes in `iceberg-notebooks`.

### Use a managed PostgreSQL

The platform needs two databases on PostgreSQL 15 or later, each with its own user: one for Polaris's
catalog and one for Keycloak. Give the database an address on the cluster's private network, so it
needs no public endpoint.

1. Create the database server, with the two databases and their users. Each user needs all rights on
   its own database, including creating tables in its `public` schema. On Scaleway, for example:

    ```bash
    scw rdb instance create name=iceberg engine=PostgreSQL-17 node-type=db-play2-nano \
      user-name=iceberg-owner generate-password=true volume-type=sbs_5k volume-size=20GB \
      init-endpoints.0.private-network.private-network-id=<the cluster's private network> \
      init-endpoints.0.private-network.enable-ipam=true
    for name in polaris keycloak; do
      scw rdb user create instance-id=<id> name=$name password=<password>
      scw rdb database create instance-id=<id> name=$name
      scw rdb privilege set instance-id=<id> database-name=$name user-name=$name permission=all
    done
    ```

2. Store both passwords in a Secret in the platform's namespace:

    ```bash
    kubectl create namespace iceberg
    kubectl -n iceberg create secret generic iceberg-postgres \
      --from-literal=POLARIS_DB_PASSWORD=<password> --from-literal=KEYCLOAK_DB_PASSWORD=<password>
    ```

3. Install with the database's private address:

    ```bash
    helm install iceberg oci://ghcr.io/sanderdw/charts/iceberg-data-platform --version 0.9.10 -n iceberg \
      --set baseDomain=data.example.com --set tls.clusterIssuer=letsencrypt-prod \
      --set postgres.external.enabled=true --set postgres.external.host=<private address> \
      --set postgres.external.existingSecret=iceberg-postgres --wait --timeout 15m
    ```

The chart then starts no Postgres of its own. Polaris, Keycloak and the Infrastructure page connect
with `sslmode=require`, which encrypts the connection; `postgres.external.sslmode=verify-full` also
checks the server's certificate. Other names for the databases or users go in
`postgres.external.polaris` and `postgres.external.keycloak`. The provider's backups now cover the
catalog and the users; back up the RustFS volume and the Secret `iceberg-platform` with them.

## Use the official Apache Polaris chart

By default the chart runs Polaris itself, as one pod. To run Polaris with the official
[Apache Polaris chart](https://polaris.apache.org/) instead, for its replicas, autoscaling, metrics
and maintenance job, install the two charts together. The platform chart then:

- runs no Polaris of its own, but creates the realm and its root credentials in the database (the
  official chart has no bootstrap);
- keeps the Services `polaris` and `polaris-control-plane`, which find the official chart's pods by
  label, so the portals, notebooks and extensions reach Polaris as before;
- writes the official chart's values, computed from your settings, to the ConfigMap
  `iceberg-polaris-values`: the database, RustFS's credentials, Keycloak sign-in, the labels and the
  feature flags.

That Polaris belongs to this platform: the platform creates catalogs, principals and roles in it with
the realm's root principal. It is not a way to share an existing Polaris with other applications.

Install in this order, because the official chart's values come from the platform, and demo data
needs a running Polaris:

```bash
helm install iceberg oci://ghcr.io/sanderdw/charts/iceberg-data-platform --version 0.9.10 -n iceberg \
  --create-namespace --set baseDomain=data.example.com --set tls.clusterIssuer=letsencrypt-prod \
  --set polaris.external.enabled=true --wait --timeout 15m
kubectl -n iceberg get configmap iceberg-polaris-values -o jsonpath='{.data.values\.yaml}' > polaris-values.yaml
helm install catalog polaris --repo https://downloads.apache.org/polaris/helm-chart --version 1.8.0 \
  -n iceberg -f polaris-values.yaml --wait --timeout 10m
# Optional, now that Polaris runs: demo accounts and data.
helm upgrade iceberg oci://ghcr.io/sanderdw/charts/iceberg-data-platform --version 0.9.10 -n iceberg \
  --reset-then-reuse-values --set demo=true --wait
```

Keep `polaris-values.yaml` as it is and put your own settings, such as `replicaCount`, `resources` or
`autoscaling`, in a second file (`-f polaris-values.yaml -f my-polaris.yaml`). With more than one
replica, every replica needs the same token signing keys: set `authentication.tokenBroker.secret` in
your file. Install the Polaris version the platform names in `images.polaris`. After a platform
upgrade, read the ConfigMap again and upgrade the `catalog` release with it.

## The platform Secret

The chart generates every credential once and stores it in the Secret `iceberg-platform`. Upgrades
keep it, and it stays after `helm uninstall`. Back it up: the data in Postgres and RustFS only
opens with these credentials. An upgrade that finds no Secret stops instead of creating new ones.

GitOps tools that render charts without cluster access, such as Argo CD, cannot read the live
Secret. Create it yourself and set `existingSecret`; the keys are listed in the chart's `values.yaml`.

## Add extensions

Each extension is two steps: register it in the platform release, which creates its Keycloak clients
and its handshake Secret `iceberg-bridge-<id>`, then install its chart in the same namespace. The
extension gets the host `<host>.<domain>` and its own certificate.

```bash
chart=oci://ghcr.io/sanderdw/charts
# 1. Register the extensions you want, with their hosts.
helm upgrade iceberg $chart/iceberg-data-platform --version 0.9.10 -n iceberg --reset-then-reuse-values \
  --set extensions.dbt.host=dbt --set extensions.ingestion.host=ingest \
  --set extensions.conversationalbi.host=cbi --wait
# 2. Install their charts, with the same ingress settings as the platform.
common="-n iceberg --set ingress.className=traefik --set tls.clusterIssuer=letsencrypt-prod --wait"
helm install dbt $chart/iceberg-dbt --version 0.1.2 $common
helm install ingestion $chart/iceberg-ingestion --version 0.1.2 $common
# Conversational BI reads its LLM from a Secret, so the key stays out of Helm's values.
kubectl -n iceberg create secret generic conversationalbi-llm \
  --from-literal=LLM_MODEL=anthropic:claude-sonnet-5-5 --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
helm install conversationalbi $chart/iceberg-conversationalbi --version 0.1.4 $common \
  --set llm.existingSecret=conversationalbi-llm
```

- **dbt and Ingestion** run every build or load as a Kubernetes Job in a namespace of their own
  (`iceberg-dbt-runs`, `iceberg-ingestion-runs`), with the same isolation as on Docker. Ingestion runs
  reach public sources only; `--set runs.allowPrivateSources=true` also allows sources in your own network.
- **Conversational BI** needs an LLM: `LLM_MODEL` is a Pydantic AI model string, next to its
  provider's variables, as in [change the model](conversational-bi.md#change-the-model). To change the
  model, replace the Secret and restart the gateway: `kubectl -n iceberg rollout restart deploy/cbi-gateway`.
  For a quick test, `--set llm.model=... --set-string llm.env.<VARIABLE>=...` also works, but then the
  key is stored in Helm's release history.

`helm show values oci://ghcr.io/sanderdw/charts/iceberg-dbt` lists every setting of an extension
chart. See [extensions](extensions.md) for what each one does. OpenMetadata has no chart yet: it runs
with Compose only.

## Settings

Set chart values with `helm upgrade ... --reset-then-reuse-values --set <value>`, and list them all with
`helm show values oci://ghcr.io/sanderdw/charts/iceberg-data-platform`. The ones you are most likely to
change are in [configuration](configuration.md#kubernetes).

## Files

You need no files from this repository: Helm pulls the charts from `oci://ghcr.io/sanderdw/charts`,
and the images from `ghcr.io/sanderdw`. Each GitHub release also attaches its chart
(`iceberg-data-platform-<version>.tgz`, and `iceberg-<extension>-<version>.tgz` on the extension
releases) for installations without access to ghcr.io; install it with `helm install iceberg ./<file>.tgz`.

The [agent skills](agent-skills.md), `kubernetes-install` included, and the `AGENTS.md` for AI agents
are in the release's installation archive (`iceberg-data-platform-<version>-install.tar.gz`). Unpack
it anywhere and start your agent there; you don't need to run its installer. A checkout of the
repository has them too.

## Try it on your computer

[kind](https://kind.sigs.k8s.io/) runs the chart without ingress or certificates, on the Compose
addresses:

```bash
kind create cluster
helm pull oci://ghcr.io/sanderdw/charts/iceberg-data-platform --version 0.9.10 --untar
helm install iceberg ./iceberg-data-platform -n iceberg --create-namespace \
  -f iceberg-data-platform/values-localhost.yaml --wait --timeout 15m
for forward in portal:3000 users:3002 keycloak:8080 polaris:8181 rustfs:9000; do
  kubectl -n iceberg port-forward "svc/${forward%%:*}" "${forward##*:}" &
done
```

Open `http://localhost:3000`. `helm show values` lists every setting of the chart.

## Update

Install a newer version with `helm upgrade`. The platform Secret and the volumes stay; the hook job
reconciles Keycloak's clients again:

```bash
helm upgrade iceberg oci://ghcr.io/sanderdw/charts/iceberg-data-platform --version <version> -n iceberg \
  --reset-then-reuse-values --wait --timeout 15m
```

`--reset-then-reuse-values` keeps your settings and also applies the defaults of settings the new
version added; `--reuse-values` would leave those empty. Update an extension the same way with its own
chart and version. The portals and Keycloak run as one instance each and restart in place, so they are
unavailable for a minute during an upgrade.

## Restart and logs

```bash
kubectl -n iceberg rollout restart deploy/portal deploy/users     # restart the portals
kubectl -n iceberg logs deploy/bridge                             # the Bridge's audit trail
kubectl -n iceberg logs deploy/portal                             # the administration portal
kubectl -n iceberg logs deploy/keycloak
```

The workloads are the Deployments `portal`, `users`, `bridge`, `keycloak`, `monitor` and `polaris`,
and the StatefulSets `postgres` and `rustfs`; each extension adds its own (`dbt`, `ingestion`,
`cbi-gateway`, `cbi-runtime`).

## Check the installation

```bash
kubectl -n iceberg get pods                 # every pod Running, the keycloak-bootstrap job Completed
kubectl -n iceberg get certificate          # READY True once Let's Encrypt has issued the certificate
kubectl -n iceberg logs job/keycloak-bootstrap
```

- **A certificate stays not ready:** HTTP-01 needs port 80 of every host to reach the ingress
  controller. Check the DNS record and `kubectl -n iceberg describe certificaterequest`.
- **Sign-in fails with "Invalid redirect URI":** the browser address differs from the one at the first
  install. `baseDomain` and the hosts are fixed once Keycloak has imported its realm.
- **A notebook does not start:** the first notebook on a node pulls a large image. Raise
  `notebooks.startSeconds`, and check `kubectl -n iceberg-notebooks get events`.

## Uninstall

```bash
helm uninstall conversationalbi dbt ingestion -n iceberg   # the extensions you installed
helm uninstall iceberg -n iceberg
```

This removes the platform and the extensions, but keeps their Secrets (`iceberg-platform`,
`iceberg-dbt`, `iceberg-ingestion`, `iceberg-conversationalbi`), the volumes with your data and the
notebooks namespace `iceberg-notebooks` with the notebook files. Installing again with the same
names finds them. To remove everything, delete the namespaces `iceberg` and `iceberg-notebooks`;
this cannot be undone. A managed PostgreSQL is not part of the cluster: delete its databases yourself.

## More

- [Security model on Kubernetes](security.md#on-kubernetes): namespaces, rights and NetworkPolicies.
- [Kubernetes for developers](develop/kubernetes.md): how the chart maps Compose, and testing on kind.
