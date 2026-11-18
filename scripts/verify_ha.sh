#!/usr/bin/env bash
# Proves the observability plane survives losing a replica. Uses the API-server service proxy (no port-forward).
set -uo pipefail
NS=observability
pass=0; fail=0
ok()  { echo "PASS  $*"; pass=$((pass+1)); }
bad() { echo "FAIL  $*"; fail=$((fail+1)); }

prom_query() { kubectl get --raw "/api/v1/namespaces/$NS/services/kps-prometheus:http-web/proxy/api/v1/query?query=$1" 2>/dev/null; }
am_peers()   { kubectl get --raw "/api/v1/namespaces/$NS/services/kps-alertmanager:http-web/proxy/api/v2/status" 2>/dev/null \
                 | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["cluster"]["peers"]))' 2>/dev/null; }
ready() { kubectl -n $NS get "$1" -o jsonpath='{.status.readyReplicas}' 2>/dev/null; }

echo "== baseline =="
[ "$(ready statefulset/prometheus-kps-prometheus)" = "2" ] && ok "2 Prometheus replicas ready" || bad "Prometheus replicas != 2"
[ "$(ready statefulset/alertmanager-kps-alertmanager)" = "3" ] && ok "3 Alertmanager replicas ready" || bad "Alertmanager replicas != 3"
[ "$(ready deployment/otel-collector)" = "2" ] && ok "2 collector replicas ready" || bad "collector replicas != 2"
[ "$(ready deployment/kps-grafana)" = "2" ] && ok "2 Grafana replicas ready" || bad "Grafana replicas != 2"
[ "$(am_peers)" = "3" ] && ok "Alertmanager gossip cluster has 3 peers" || bad "Alertmanager peers != 3"

echo "== kill one of each and re-check =="
kubectl -n $NS delete pod prometheus-kps-prometheus-0 --wait=false >/dev/null
kubectl -n $NS delete pod alertmanager-kps-alertmanager-0 --wait=false >/dev/null
kubectl -n $NS delete pod -l app.kubernetes.io/name=opentelemetry-collector --field-selector=status.phase=Running --wait=false 2>/dev/null | head -1 >/dev/null
sleep 5
out=$(prom_query 'sum(app_requests_total)')
echo "$out" | grep -q '"status":"success"' && ok "Prometheus still answers queries via the Service" || bad "Prometheus query failed"
[ "$(am_peers)" -ge 2 ] 2>/dev/null && ok "Alertmanager still serving with >=2 peers" || bad "Alertmanager lost quorum of peers"
kubectl -n shop exec deploy/loadgen -- python -c "import urllib.request;print(urllib.request.urlopen('http://shop-api.shop.svc:8080/healthz').status)" >/dev/null 2>&1 \
  && ok "shop-api still serving" || bad "shop-api not serving"

echo "== wait for recovery =="
kubectl -n $NS rollout status statefulset/prometheus-kps-prometheus --timeout=180s >/dev/null 2>&1 && ok "Prometheus back to 2 replicas" || bad "Prometheus did not recover"
kubectl -n $NS rollout status statefulset/alertmanager-kps-alertmanager --timeout=180s >/dev/null 2>&1 && ok "Alertmanager back to 3 replicas" || bad "Alertmanager did not recover"
echo; echo "passed=$pass failed=$fail"; [ $fail -eq 0 ]
