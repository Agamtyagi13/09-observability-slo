#!/usr/bin/env bash
# Shows what Prometheus is firing and what the notification sink received.
set -euo pipefail
PROM=$(kubectl get --raw "/api/v1/namespaces/observability/services/kps-prometheus:http-web/proxy/api/v1/alerts")
SINK=$(kubectl get --raw "/api/v1/namespaces/observability/services/alert-sink:http/proxy/alerts")
PROM="$PROM" SINK="$SINK" python3 - <<'PY'
import json, os
alerts = json.loads(os.environ["PROM"])["data"]["alerts"]
print("== firing/pending alerts (Prometheus) ==")
if not alerts:
    print("none")
for a in alerts:
    lb = a["labels"]
    print(f"{a['state']:8} {lb['alertname']:34} {lb.get('burn_window', ''):8} {lb.get('severity', '')}")
print("\n== notifications received by alert-sink ==")
for n in json.loads(os.environ["SINK"]):
    print(n["startsAt"], n["status"], n["alertname"], "->", n["receiver"])
PY
