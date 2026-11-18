# Highly Available Observability and SLO Platform

A sample service instrumented with **OpenTelemetry** (metrics, logs, traces), shipped through a replicated
collector to **Prometheus, Loki, Tempo**, visualised in **Grafana**, and governed by **SLIs/SLOs with
multiwindow burn-rate alerts** routed by a 3-node **Alertmanager** cluster, all on **Kubernetes**.
Includes a runbook and fault-injection scenarios that demonstrate end-to-end diagnosis.

## Architecture

```
 shop-api x3 (Flask + OTel SDK)  ── OTLP/gRPC: metrics, traces, logs ──►  otel-collector x2 (gateway)
   spans: POST /checkout                                                     │        │          │
          ├ inventory.reserve                                                │        │          │
          └ payments.charge  ◄── fault injection (latency / errors)          ▼        ▼          ▼
   logs carry trace_id=<id>                                         Prometheus    Tempo       Loki
                                                                    scrapes :8889 (traces)    (logs, OTLP)
                                                                    (x2, HA pair)    │          │
                                                                          │          └────┬─────┘
                       PrometheusRule: SLI recording rules                │               ▼
                       + multiwindow burn-rate alerts  ◄──────────────────┘       Grafana x2
                                │                                                  dashboards-as-code,
                                ▼                                                  Loki<->Tempo links
                    Alertmanager x3 (gossip, de-duplicates the 2 Prometheus replicas)
                       page ──► oncall-page      ticket ──► ticket-queue   (page inhibits ticket)
```

## SLIs and SLOs (`slo/slo.yaml`, the single source of truth)

| SLO | SLI (good events / valid events) | Objective | Budget (30d) |
|---|---|---|---|
| Availability | requests not answered with 5xx / all requests, excluding `/healthz` | 99.9% | 43.2 min of full outage |
| Latency | requests faster than 500 ms / all requests | 99% | 1% of requests may be slow |

`slo/generate_rules.py` turns that file into Prometheus recording rules for every window, the error-budget gauge,
the burn-rate alerts and the Grafana dashboard. Nothing is hand-edited, and a test fails if generated files drift.

**Burn-rate alerting** (Google SRE Workbook): an alert needs *both* a long and a short window above threshold, so it
is fast to fire and fast to clear.

| Severity | Long / short window | Burn rate | Budget consumed when it fires |
|---|---|---|---|
| page | 1h / 5m | 14.4x | 2% in 1h |
| page | 6h / 30m | 6x | 5% in 6h |
| ticket | 1d / 2h | 3x | 10% in 1d |
| ticket | 3d / 6h | 1x | 10% in 3d |

A `demo` profile compresses the windows (10m/1m, 30m/5m) so a fault-injection demo alerts within minutes.

## High availability: what is replicated and what is not

| Component | Setup | Failure behaviour |
|---|---|---|
| shop-api | 3 replicas, topology spread, PDB minAvailable 2, zero-unavailable rollouts | one pod/node loss is invisible |
| OTel collector | 2 replicas behind a Service, PDB, memory limiter, retrying exporters | one replica loss: SDKs reconnect to the other |
| Prometheus | 2 replicas, each scrapes and evaluates everything, `prometheus_replica` label stripped on alerts, PDB | queries and alerts continue on the survivor |
| Alertmanager | 3-node gossip cluster, PDB minAvailable 2 | notifications de-duplicated; survives one loss |
| Grafana | 2 replicas, fully provisioned (datasources + dashboards from code) | stateless, either serves |
| Loki, Tempo | single binary with persistent volume (demo footprint) | **not HA**: restart gap, data on PVC survives |
| Meta-monitoring | alerts for absent SLO metrics, replica loss, Alertmanager cluster size, collector drops | the platform pages when it goes blind |

Honest gap: Loki and Tempo are single instances here to fit a laptop. The production path is Loki
simple-scalable or distributed with object storage and `replication_factor: 3`, and Tempo distributed
(`tempo-distributed` chart) with S3/GCS. The collector config does not change, only the exporter endpoints.
Long-term metric HA (Thanos/Mimir) is likewise out of scope; retention is 15 days on local volumes.

## Layout

| Path | Purpose |
|---|---|
| `app/` | shop-api, OTel setup, fault-injection core, load generator, demo alert receiver, Dockerfile |
| `slo/slo.yaml`, `slo/generate_rules.py` | SLO definitions and generator |
| `k8s/` | app, load generator, ServiceMonitor, meta-rules, **generated** SLO rules (prod + demo) and dashboard |
| `helm/` | values and installer for kube-prometheus-stack, Loki, Tempo, OTel collector |
| `runbooks/slo-burn.md` | triage, PromQL/LogQL/TraceQL, mitigations, escalation |
| `scenarios/FAULT_INJECTION.md` | four scenarios with expected timings and the diagnosis walkthrough |
| `scripts/` | `inject_fault.sh`, `verify_ha.sh`, `check_alerts.sh` |
| `tests/` | 31 offline tests |

## Quickstart (kind, ~15 minutes)

Prerequisites: Docker, kind, kubectl, helm, 8 GB RAM free.

```bash
make cluster          # 1 control plane + 3 workers
make image            # build shop-api:0.1.0 and load it into kind
make stack            # Prometheus/Alertmanager/Grafana, Loki, Tempo, OTel collector
make deploy           # app, load generator, rules, dashboard, alert sink
make demo-rules       # compressed burn windows for the demo
make grafana          # http://localhost:3000  admin / admin  -> Shop API SLOs
```

Sanity checks after ~3 minutes of traffic:
```bash
kubectl -n shop get pods                                   # 3 shop-api + loadgen
kubectl -n observability get pods                          # 2 prometheus, 3 alertmanager, 2 grafana, 2 collectors, loki, tempo
kubectl -n observability port-forward svc/otel-collector 8889 &
curl -s localhost:8889/metrics | grep -E "^app_requests_total|^app_request_duration_seconds_bucket" | head
```
If those metric names differ in your collector version, adjust `app/metrics_names.py` and run `make rules`;
the SLO rules and dashboard are regenerated from it.

## Demo

```bash
make fault-latency    # payments +900 ms  -> latency SLO fast burn pages in ~4-5 min
make alerts           # firing alerts + notifications received
# diagnose: dashboard -> p99 by route -> Tempo slow trace -> payments.charge span -> logs
make fault-clear      # alert resolves
make fault-one-pod    # availability burn from one bad pod; "5xx ratio by pod" isolates it
make verify-ha        # kill a Prometheus, Alertmanager and collector pod; everything keeps working
```
Full expected timings and the step-by-step diagnosis are in `scenarios/FAULT_INJECTION.md`.

## Tests

```bash
pip install pytest pyyaml
make test
```
Covers fault-injection logic, burn-rate threshold math against the SRE workbook, rule/window consistency, the
zero-error edge case, generated-file drift, HA replica counts and PDBs, Alertmanager routing and inhibition,
collector pipelines, dashboard datasource wiring, runbook anchors referenced by every alert, and the alert sink.

## Design decisions worth explaining

- **SLOs as code**: one YAML drives rules, dashboard and docs, so thresholds cannot drift from the objective.
- **`or vector(0)`** in the availability SLI: without it, an empty 5xx numerator makes the SLI disappear exactly when healthy.
- **Latency SLI from histogram buckets**, with the 500 ms threshold validated against the instrument's explicit bucket boundaries.
- **Excluding `/healthz`** from SLIs: probe traffic would flatter the numbers.
- **Logs carry `trace_id=`** in the message so Grafana derived fields link a log line to its Tempo trace.
- **Page inhibits ticket** for the same SLO, so one incident produces one page.
- **Absence is an alert**: `SloMetricsAbsent` pages, because no data must never read as healthy.
- **Faults are injected in the app** (per pod, over an internal admin endpoint) so scenarios can be fleet-wide or single-pod.

## Known limits

- Not run end to end in the authoring sandbox (no Docker/Kubernetes there). Verified: Python logic, generated rules, all
  YAML, manifest/Helm consistency, shell syntax. Chart versions in `helm/install.sh` are overridable (`KPS_VERSION` etc.);
  if a chart changes a values key, `helm template` will say so.
- `admin/admin` Grafana credentials and the unauthenticated in-cluster admin endpoint are demo-only (`ADMIN_TOKEN` is supported).
- The 30-day windows need 30 days of data; on a fresh cluster they report over whatever exists, so budget gauges settle slowly.
