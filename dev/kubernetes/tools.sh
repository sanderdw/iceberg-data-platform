#!/bin/sh
# Install the pinned Kubernetes tools (linux-amd64) with checksum verification.
# Usage: sh dev/kubernetes/tools.sh [bin-dir]   (default ~/.local/bin; CI passes $RUNNER_TEMP/bin)
set -eu

bin=${1:-"$HOME/.local/bin"}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$bin"

fetch() { # url sha256 file
  curl --fail --silent --show-error --location "$1" -o "$work/$3"
  echo "$2  $work/$3" | sha256sum --check --quiet
}

fetch https://dl.k8s.io/release/v1.37.2/bin/linux/amd64/kubectl \
  c04487523e7aa7d1493f7808174c899db93d7a9cf57531420cd76b9bd415f8f4 kubectl
install -m 0755 "$work/kubectl" "$bin/kubectl"

fetch https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz \
  d1c0278d46beab79582d7e50f5b3dc206944ba8e577cde618513fd07daa53983 helm.tar.gz
tar -xzf "$work/helm.tar.gz" -C "$work" linux-amd64/helm
install -m 0755 "$work/linux-amd64/helm" "$bin/helm"

fetch https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64 \
  aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d kind
install -m 0755 "$work/kind" "$bin/kind"

fetch https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-linux-amd64.tar.gz \
  9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883 kubeconform.tar.gz
tar -xzf "$work/kubeconform.tar.gz" -C "$work" kubeconform
install -m 0755 "$work/kubeconform" "$bin/kubeconform"

echo "Installed kubectl 1.37.2, helm 4.3.2, kind 0.33.0 and kubeconform 0.8.0 in $bin"
