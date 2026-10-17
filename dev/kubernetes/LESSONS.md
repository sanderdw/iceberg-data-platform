# Lessons learned: the Kubernetes install option

Building the Helm charts and testing them on a Scaleway Kapsule cluster (nl-ams-1), October 2026.
Maintainer notes; the user documentation is `docs/install-kubernetes.md`.

## What worked well

- **Keep the Compose names as Service names.** Naming the Services `keycloak`, `polaris`, `rustfs`,
  `bridge` and so on, without a release prefix, kept every environment string from `compose.yaml`
  valid and the Bridge network contract (v1) unchanged. The price is one release per namespace,
  which is a fair rule for this platform.
- **Docker stays the default behind every switch.** `NOTEBOOK_BACKEND`, `MONITOR_BACKEND` and
  `LAUNCHER_BACKEND` default to `docker`, so the Compose installation and its CI did not change.
  Each Kubernetes backend has the same interface as the Docker one it replaces.
- **Separate namespaces for user code.** Notebooks and runs live in namespaces that hold nothing
  else. The right to create pods in a namespace gives access to every Secret in it, so this is what
  keeps the platform's credentials away from notebook and dbt code.
- **ExternalName Services in those namespaces.** Notebooks and runs reach `polaris`, `rustfs` and
  `workspace-gateway` under their Docker names, so the notebook image and the runners did not
  change.
- **The existing smoke tests ran unchanged.** With Polaris and RustFS port-forwarded, and the public
  hosts used for vended storage, `scripts.smoke`, `catalog_smoke`, `data_smoke`, `share_smoke` and
  the extensions' end-to-end smokes all passed on the cluster. That gave far more confidence than
  new Kubernetes-only tests would have.
- **Credentials generated once with `lookup`, kept with `resource-policy: keep`, and an upgrade that
  fails when the Secret is gone.** A `helm upgrade` that added the demo accounts kept every existing
  password.
- **A Job contract instead of `put_archive` and `get_archive`.** The runner fetches its staged
  `/work` and uploads `out/` through the launcher, with a ticket valid for one run. The launcher
  runs as a second container in the portal pod, so a ReadWriteOnce artifacts volume is enough.

## What surprised us

- **Kubernetes units.** metrics-server reports memory as `83672Ki`. The first parser only knew
  lowercase prefixes and failed on every pod, so the Infrastructure page showed no containers. Test
  parsers with real API output, not only with the values in the documentation.
- **Duplicate labels pass `helm lint` but fail kubeconform.** Merging the common labels and the
  selector labels into a pod template set `app.kubernetes.io/name` twice. Run kubeconform on every
  chart, not only lint.
- **An egress `ipBlock` may or may not cover pod addresses.** The notebooks' "internet except
  private ranges" rule kept them away from Keycloak and the portals on Cilium (Scaleway), but not on
  kind's kindnet, which matched the pod addresses against `0.0.0.0/0` and let them through.
  Kubernetes leaves this to the CNI. Keycloak and the portals now also refuse connections from
  namespaces labelled `io.iceberg-platform.isolated`, so the rule no longer depends on the CNI.
  Testing on two CNIs found this; one would not have.
- **`helm upgrade --reuse-values` drops new chart defaults.** A setting the new chart version added
  (here `keycloak.adminOrigin`) stays empty. Upgrade with `--reset-then-reuse-values` (Helm 3.14+),
  which applies the new defaults first and then the earlier settings; templates also give such
  settings their own `default`.
- **Keycloak's console and master realm on a cluster.** The Ingress never routes them. With
  `KC_HOSTNAME_ADMIN` alone the console opens on the port-forward but its sign-in still goes to the
  public host; the master realm's `frontendUrl` must be the port-forward address too. A plain-HTTP
  front end then makes Keycloak refuse master-realm sign-ins from pod addresses (`sslRequired`
  external), the bootstrap's included, so the bootstrap sets the master realm to `sslRequired: none`
  in the same update. Nothing outside the cluster reaches that realm.
- **Traefik answers 503, not 404, for a Service without endpoints.** The chart's `ingress-deny`
  Service blocks `/internal`, `/rustfs` and `/minio`; checks should accept either code.
- **Keycloak's HTTPS rule did not bite.** Kapsule gives pods 100.64.0.0/15 addresses. We expected
  Keycloak's default `sslRequired=external` to refuse plain-HTTP backchannel calls from those, but
  the bootstrap job and the portals worked unchanged.
- **The notebook token check holds on Cilium.** The user portal accepts a token request only from
  the notebook's own pod IP. Pod-to-Service traffic keeps the source address on Kapsule's Cilium, so
  it works. To be safe, the endpoint now also refuses any request with `X-Forwarded-For` or
  `Forwarded`, because the ingress controller and notebooks share the pod range.
- **The Ingestion gateway already refuses private hosts** when a pipeline is created, before any
  run starts, so a pipeline cannot test the run's own isolation. `notebook_check.py <namespace> dbt`
  (or `ingestion`) starts a probe pod with the runner image in the runs namespace instead.
- **The Polaris bootstrap logged its root credential.** The admin tool's `run-java.sh` prints its
  whole command line, `--credential=...` included. The chart now starts `java -jar` directly; the
  Compose `polaris-bootstrap` service still logs it.
- **Docker Desktop can take down WSL networking.** Starting Docker Desktop cut WSL off from the
  internet for a while. `wsl --shutdown` from Windows restores it.

## The official Apache Polaris chart

- **Decision: an external mode, not a subchart.** `polaris.external.enabled` runs no Polaris pod; the
  platform keeps its Services, which select the official chart's pods by label, bootstraps the realm
  in a hook Job (the official chart has none), and renders the official chart's values to the
  ConfigMap `iceberg-polaris-values`. A subchart would tie our releases to upstream's chart and still
  need our bootstrap, labels and Bridge names. Tested on Scaleway: every check passed.
- **The official chart takes `imagePullSecrets` as names**, not `{name: ...}` objects, and its schema
  refuses the objects.
- **Without values it silently runs in-memory**, with a random root credential it prints to its log.
  A values file that came out empty did exactly that; check the file before installing.
- **Order matters on a fresh install:** platform first (its ConfigMap holds the values), then the
  official chart, then demo data, whose bootstrap needs a running Polaris.

## Scaleway specifics

- **sslip.io works for testing without a domain:** `<host>.<ip-with-dashes>.sslip.io` with Let's
  Encrypt HTTP-01. Reserve a flexible load balancer IP, so the hostnames survive `down` and `up`.
- **Use proxy protocol v2 plus `scw-loadbalancer-use-hostname`.** Proxy protocol gives the portals
  real client addresses for their sign-in limits. `use-hostname` sends traffic from pods to the
  public address through the load balancer too, which cert-manager's HTTP-01 self-check needs.
- **Kapsule ships metrics-server and Cilium with NetworkPolicy**, and Block Storage CSI as the
  default StorageClass (`sbs-default`).
- **Images built from the working tree need a registry the cluster can reach.** A private Scaleway
  registry namespace with a pull Secret worked; the chart copies that Secret to the notebooks and
  runs namespaces. With a mutable `dev` tag, set `image.pullPolicy=Always`.
- **The autoscaler shrinks the pool to its minimum while idle**, and grows it when notebooks or
  runs need room. The first notebook on a new node waits for the image pull (`notebooks.startSeconds`).

## Managed services

- **Postgres: use the provider's managed PostgreSQL in production.** It is plain PostgreSQL with
  backups, point-in-time recovery and HA. `postgres.external` works: tested on Scaleway Managed
  Database (PostgreSQL 17, private network only) with the core smokes, notebooks and the monitor.
  Scaleway's `permission=all` lets each user create Polaris's and Keycloak's tables in `public`.
  The `scw` user, database and privilege commands take `instance-id=` as a named argument.
- **Object storage: keep RustFS.** The platform creates per-user S3 accounts and policies through
  the RustFS admin API, and Polaris vends short-lived, table-scoped credentials through STS
  AssumeRole, which RustFS implements. Scaleway Object Storage offers neither in that form. It does
  fit as a backup or replication target.
- **Notebook storage already uses the provider's block storage.** Consider a `-retain`
  StorageClass, and ReadWriteMany file storage later, so one team's sessions need not share a node.

## Still to do

- Stop the Compose `polaris-bootstrap` from logging its credential, as the chart does.
- Run the `kubernetes-install` skill once against the published charts: its preflight and verify
  scripts were tested on Scaleway, its `helm` commands need the charts on ghcr.io first.
- Release 0.9.9 (with Conversational BI 0.1.3, dbt 0.1.1 and Ingestion 0.1.1, none released before):
  release the extensions first, then the core, and make the new `charts/*` GHCR packages public.
- An OpenMetadata chart.
