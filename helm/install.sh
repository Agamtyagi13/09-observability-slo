#!/usr/bin/env bash
# Installs the observability stack. Chart versions are overridable; if a pinned version is missing,
# run `helm search repo <chart> --versions | head` and export the variable.
set -euo pipefail
cd "$(dirname "$0")"
KPS_VERSION="${KPS_VERSION:-65.1.1}"
LOKI_VERSION="${LOKI_VERSION:-6.16.0}"
TEMPO_VERSION="${TEMPO_VERSION:-1.10.3}"
OTEL_VERSION="${OTEL_VERSION:-0.108.0}"
NS=observability

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo add grafana https://grafana.github.io/helm-charts >/dev/null
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts >/dev/null
helm repo update >/dev/null

kubectl apply -f ../k8s/00-namespaces.yaml

helm upgrade --install kps prometheus-community/kube-prometheus-stack -n $NS \
  --version "$KPS_VERSION" -f values-prometheus-stack.yaml --wait --timeout 10m
helm upgrade --install loki grafana/loki -n $NS --version "$LOKI_VERSION" -f values-loki.yaml --wait --timeout 10m
helm upgrade --install tempo grafana/tempo -n $NS --version "$TEMPO_VERSION" -f values-tempo.yaml --wait --timeout 10m
helm upgrade --install otel-collector open-telemetry/opentelemetry-collector -n $NS \
  --version "$OTEL_VERSION" -f values-otel-collector.yaml --wait --timeout 5m
echo "stack installed"
