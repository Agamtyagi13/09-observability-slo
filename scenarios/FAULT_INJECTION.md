# Fault-injection scenarios and diagnosis walkthrough

Everything below uses the **demo** burn-rate windows (`make demo-rules`) so alerts fire within minutes.
In production the same rules use 1h/5m and 6h/30m windows.

Setup once:
```bash
make demo-rules
make grafana            # separate terminal; http://localhost:3000  admin/admin  -> Dashboards -> Shop API SLOs
```
Steady state first: wait ~5 minutes. Both SLIs near 100%, burn rates near 0, no alerts (`make alerts`).

---

## Scenario A: slow payments dependency (latency SLO)

**Inject** (note the timestamp it prints):
```bash
make fault-latency        # +900 ms on payments.charge, all pods
```

**What should happen**

| Time after inject | Expected |
|---|---|
| 0-1 min | `/checkout` p99 jumps from ~100 ms to ~1 s; p99 panel bends |
| ~3 min | latency 10m burn rate passes 14.4 (about half of all requests are checkouts, so ~50% are slow -> burn rate ~50; the 10m window reaches 14.4% slow after ~3 min) |
| ~4-5 min | `ShopApiLatencyFastBurn` goes Pending, then Firing after `for: 1m`; `oncall-page` receives it (`make alerts`) |
| any time | availability SLO unaffected (slow is not failed): proves the two SLIs are independent |

**Diagnose like an on-call engineer** (this is the demonstration):
1. Alert annotation points to the dashboard and runbook section `latency-fast-burn`.
2. *Latency burn rate* panel: 5m line rose first, then 1h: a fresh, sharp change, not a slow leak.
3. *p99 latency by route*: only `/checkout` moved; `/products` flat. Scope = one route.
4. *5xx ratio by pod*: flat. So it is slow, not failing, and affects every pod, which suggests a shared dependency or release.
5. Tempo > Search (or TraceQL): `{ resource.service.name = "shop-api" && duration > 500ms }`.
   Open a trace: `POST /checkout` ~1 s, child `inventory.reserve` ~10 ms, child `payments.charge` ~950 ms
   with attribute `payments.injected_latency_ms = 900`. **Root cause located in one click.**
6. Logs: from the trace use *Logs for this span*, or run
   `{service_name="shop-api"} |= "fault injection changed"` to see the change event the pods logged.
7. **Mitigate**: `make fault-clear`. Watch p99 fall, the 5m burn rate drop within a minute, the alert resolve and a
   `resolved` notification arrive (`make alerts`).
8. Record error budget consumed: Latency budget gauge before vs after.

---

## Scenario B: one bad pod (availability SLO, partial blast radius)

```bash
make fault-one-pod        # 60% payment failures on the first pod only
```

Expected: overall 5xx ratio about 0.5 (checkout share) x 1/3 (one pod) x 0.6 = **~10%** -> burn rate ~100x -> `ShopApiAvailabilityFastBurn` pages.

Diagnosis: *5xx ratio by pod* shows exactly one pod high; the other two flat. Logs show
`request failed route=/checkout error=payment gateway returned 503 trace_id=...` only from that pod's
`k8s_pod_name`. Mitigation: `kubectl -n shop delete pod <that pod>` (PDB keeps 2 serving), or `make fault-clear`.
Compare the blast radius with Scenario A: the same fault type, but per-pod breakdown tells you it is local.

## Scenario C: all pods erroring (full availability burn)

```bash
make fault-errors         # 40% payment failures everywhere
```
Expected: 5xx ratio ~20%, burn rate ~200x, page within ~2-3 minutes, `ShopApiAvailabilitySlowBurn`
(ticket) is **inhibited** by the page (same slo+service), proving the routing rules work.

## Scenario D: the monitoring survives its own failures

```bash
make verify-ha
```
Deletes one Prometheus, one Alertmanager and one collector pod and checks that queries, alert routing
and the app keep working and that replicas recover.

## Evidence checklist for the demo

- [ ] Dashboard screenshot before, during and after Scenario A
- [ ] Tempo trace showing the slow `payments.charge` span
- [ ] Loki log line with `trace_id` and the Grafana TraceID link
- [ ] `make alerts` output showing firing then resolved notifications
- [ ] `make verify-ha` output (all PASS)
- [ ] Error budget gauge before vs after

## Expected detection times (production profile, for the write-up)

At a sustained 10% error ratio on a 99.9% SLO (burn rate 100): the 1h window crosses 1.44% after about
`1.44 / 10 x 60 = 8.6` minutes, plus `for: 2m`, so a page in roughly **11 minutes**, having consumed about 2.5% of the monthly budget.
A 2% error ratio (burn 20) pages in about 43 minutes. A 0.2% error ratio (burn 2) never pages, but the 3d/6h
slow-burn ticket fires after about 1.5 days, once the 3-day average exceeds 0.1% (burn >= 1).
