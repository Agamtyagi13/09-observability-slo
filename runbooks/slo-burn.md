# Runbook: SLO burn alerts for shop-api

Alerts fire from `k8s/slo-rules-*.yaml`. **Page** = fast burn (you are spending the monthly budget quickly).
**Ticket** = slow burn (you will miss the SLO unless someone acts this week).

| Alert | Meaning | Budget at risk |
|---|---|---|
| `ShopApiAvailabilityFastBurn` | 5xx ratio > 1.44% over 1h and 5m (14.4x) or > 0.6% over 6h and 30m (6x) | 2% of 30d budget per hour |
| `ShopApiLatencyFastBurn` | > 14.4% of requests slower than 500 ms over 1h and 5m, or > 6% over 6h and 30m | same |
| `*SlowBurn` | 3x over 1d and 2h, or 1x over 3d and 6h | steady leak |

Objectives: availability 99.9%, latency 99% < 500 ms, both over 30 days.

## First 5 minutes (any burn alert)

1. **Acknowledge** and open Grafana > *Shop API SLOs*. Note which SLO and which window fired.
2. **Scope it** with the dashboard panels:
   - *Request rate by route*: is it one route or all?
   - *5xx ratio by pod*: one pod = bad pod/node; all pods = shared dependency or release.
   - *p99 latency by route*: which route moved, and when did the line bend?
3. **Correlate with change**: `kubectl -n shop rollout history deploy/shop-api`, recent config/flag changes, deploys of dependencies.
4. **Decide**: if there is an obvious recent change, roll it back first and investigate afterwards.

## availability-fast-burn

Diagnose:
```promql
# which route is failing
sum by (route) (rate(app_requests_total{service_name="shop-api",code=~"5.."}[5m]))
# which pod
sum by (k8s_pod_name) (rate(app_requests_total{service_name="shop-api",code=~"5.."}[5m]))
```
```logql
{service_name="shop-api"} |~ "(?i)fail|error"
```
Click a log line, then the **TraceID** link to open the trace in Tempo. Look for the red span.
TraceQL for failing traces: `{ resource.service.name = "shop-api" && status = error }`

Mitigate (pick the narrowest that works):
- Single bad pod: `kubectl -n shop delete pod <pod>` (PDB keeps 2 serving).
- Bad release: `kubectl -n shop rollout undo deploy/shop-api`.
- Failing dependency (e.g. payments): fail over, disable the feature flag, or shed the dependent route.
- Demo only: `make fault-clear`.

## availability-slow-burn

Not an emergency. Find the persistent source of errors with the route/pod breakdown over 7 days, open a ticket
with the owning team, and attach the dashboard link. Check whether a retry storm or a flaky dependency explains it.

## latency-fast-burn

Diagnose:
```promql
histogram_quantile(0.99, sum by (le, route) (rate(app_request_duration_seconds_bucket{service_name="shop-api"}[5m])))
```
Find slow traces in Tempo (TraceQL): `{ resource.service.name = "shop-api" && duration > 500ms }`
Open one; the span with most of the duration is the culprit. A long `payments.charge` span with
`payments.injected_latency_ms` > 0 means a deliberately injected fault; otherwise it is the real dependency.
Check saturation: pod CPU throttling, GC pauses, connection pool exhaustion, noisy neighbours on the node.

Mitigate: roll back, scale out (`kubectl -n shop scale deploy/shop-api --replicas=5`), cut timeouts to the slow dependency, or degrade gracefully.

## latency-slow-burn

Review p95/p99 trend per route over 7 days, profile the slow route, and schedule the fix. If the threshold
is wrong for the product, change the SLO in `slo/slo.yaml` through review, never by silencing the alert.

## Telemetry pipeline alerts

These protect the SLO system itself. Treat `SloMetricsAbsent` as a page: **no data means you are blind**, not healthy.

| Alert | Check |
|---|---|
| `SloMetricsAbsent` | `kubectl -n shop get pods`; collector pods; `curl otel-collector:8889/metrics \| grep app_requests_total`; app `OTEL_EXPORTER_OTLP_ENDPOINT` |
| `PrometheusReplicasDegraded` | `kubectl -n observability get sts prometheus-kps-prometheus`; PVC status; node pressure |
| `AlertmanagerClusterDegraded` | `kubectl -n observability logs alertmanager-kps-alertmanager-0`; gossip port 9094 reachability |
| `OtelCollectorDroppingData` | Tempo/Loki pods up? `kubectl -n observability logs deploy/otel-collector` |
| `OtelCollectorRefusingData` | Memory limiter active: raise collector memory/replicas or reduce telemetry volume |

## Escalation and after-action

- Page not mitigated in 30 minutes: escalate to the service owner and the incident commander.
- After resolution: record the timeline (alert time, detection, mitigation, recovery), budget consumed
  (`slo:error_budget_remaining:ratio`), root cause, and follow-ups. If budget < 0, freeze risky releases until it recovers.
