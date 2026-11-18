#!/usr/bin/env bash
# Fault injection for the shop-api demo.
#   inject_fault.sh latency <ms> [pods]     add <ms> to payments latency on the first [pods] pods (default: all)
#   inject_fault.sh errors  <0..1> [pods]   make payments fail with that probability
#   inject_fault.sh clear                   remove all faults
#   inject_fault.sh status                  show each pod's current fault state
set -euo pipefail
NS=shop
mode="${1:?usage: inject_fault.sh latency|errors|clear|status ...}"
arg="${2:-}"
limit="${3:-999}"

pods=$(kubectl -n "$NS" get pods -l app=shop-api -o jsonpath='{.items[*].metadata.name}')

post() { # pod json
  kubectl -n "$NS" exec "$1" -- python -c "
import urllib.request,sys
r=urllib.request.Request('http://localhost:8080/admin/fault',data=sys.argv[1].encode(),headers={'Content-Type':'application/json'},method='POST')
print(urllib.request.urlopen(r,timeout=5).read().decode().strip())" "$2"
}
get() {
  kubectl -n "$NS" exec "$1" -- python -c "
import urllib.request
print(urllib.request.urlopen('http://localhost:8080/admin/fault',timeout=5).read().decode().strip())"
}

i=0
for p in $pods; do
  i=$((i+1))
  case "$mode" in
    latency) [ $i -le "$limit" ] && { printf '%s ' "$p"; post "$p" "{\"payments_latency_ms\": $arg}"; } ;;
    errors)  [ $i -le "$limit" ] && { printf '%s ' "$p"; post "$p" "{\"payments_error_rate\": $arg}"; } ;;
    clear)   printf '%s ' "$p"; post "$p" '{"clear": true}' ;;
    status)  printf '%s ' "$p"; get "$p" ;;
    *) echo "unknown mode $mode"; exit 2 ;;
  esac
done
date -u +"injected/changed at %Y-%m-%dT%H:%M:%SZ"
