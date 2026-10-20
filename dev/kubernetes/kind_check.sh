#!/bin/sh
# Install the chart on a kind cluster from the working tree's images and run the smoke tests against it.
# The CI job `kubernetes` runs exactly this. Needs docker, the tools from tools.sh on PATH, and uv.
#   sh dev/kubernetes/kind_check.sh          # keeps the cluster afterwards: kind delete cluster --name iceberg-ci
#   POLARIS=official sh dev/kubernetes/kind_check.sh   # Polaris from the official Apache Polaris chart
set -eu

cluster=iceberg-ci
namespace=iceberg
chart=deploy/helm/iceberg-data-platform
work=$(mktemp -d)
forwards=""
cleanup() {
  for pid in $forwards; do kill "$pid" 2>/dev/null || true; done
  rm -rf "$work"
}
trap cleanup EXIT

docker build -q -t local/iceberg-data-platform-portal:ci .
docker build -q -t local/iceberg-data-platform-users:ci -f user_portal/Dockerfile .
docker build -q -t local/iceberg-data-platform-notebook:ci -f user_portal/notebook/Dockerfile .

kind get clusters | grep -qx "$cluster" || kind create cluster --name "$cluster" --wait 120s
kubectl config use-context "kind-$cluster" > /dev/null
for image in portal users notebook; do
  kind load docker-image --name "$cluster" "local/iceberg-data-platform-$image:ci"
done
platform="--set image.registry=local --set image.tag=ci --set image.pullPolicy=Never --set extensions.dbt.origin=http://localhost:3004"
if [ "${POLARIS:-bundled}" = official ]; then
  # As documented: the platform first (it writes the official chart's values), then Polaris, then demo data,
  # which needs a running Polaris.
  helm upgrade --install iceberg "$chart" -n "$namespace" --create-namespace -f "$chart/values-localhost.yaml" \
    $platform --set polaris.external.enabled=true --wait --timeout 15m
  kubectl -n "$namespace" get configmap iceberg-polaris-values -o jsonpath='{.data.values\.yaml}' > "$work/polaris.yaml"
  helm upgrade --install catalog polaris --repo https://downloads.apache.org/polaris/helm-chart --version 1.8.0 \
    -n "$namespace" -f "$work/polaris.yaml" --wait --timeout 10m
  helm upgrade iceberg "$chart" -n "$namespace" --reset-then-reuse-values --set demo=true --wait --timeout 15m
else
  helm upgrade --install iceberg "$chart" -n "$namespace" --create-namespace -f "$chart/values-localhost.yaml" \
    $platform --set demo=true --wait --timeout 15m
fi

# The Compose ports on localhost, so the browser origins of values-localhost.yaml hold.
for forward in portal:3000 users:3002 keycloak:8080 polaris:8181 rustfs:9000 bridge:3005; do
  kubectl -n "$namespace" port-forward "svc/${forward%%:*}" "${forward##*:}" > /dev/null 2>&1 &
  forwards="$forwards $!"
done
for port in 3000 3002 3005 8080 8181 9000; do
  timeout 60 sh -c "until curl -s -o /dev/null http://localhost:$port; do sleep 1; done"
done

# The smokes read their settings from the environment first (a .env in the checkout is never touched).
python3 dev/kubernetes/local_env.py "$namespace" > "$work/env"
set -a
. "$work/env"
set +a
for smoke in smoke catalog_smoke data_smoke share_smoke bridge_smoke; do
  echo "== $smoke"
  uv run --all-groups python -m "scripts.$smoke"
done
echo "== notebook network policy"
uv run --all-groups python dev/kubernetes/notebook_check.py "$namespace"
echo "All Kubernetes checks passed on kind."
