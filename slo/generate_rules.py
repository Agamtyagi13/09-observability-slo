#!/usr/bin/env python3
"""Generate multiwindow, multi-burn-rate SLO rules and the SLO dashboard from slo/slo.yaml.

  python slo/generate_rules.py                 # writes k8s/slo-rules-prod.yaml, slo-rules-demo.yaml, dashboard-slo.yaml
  python slo/generate_rules.py --check         # exit 1 if committed files are out of date

Burn rate = observed error ratio / allowed error ratio (1 - objective).
A burn rate of 1 consumes exactly the whole 30-day budget in 30 days.
"""
import argparse
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import metrics_names as mn  # noqa: E402

# (long window, short window, burn rate). Both windows must exceed the threshold to fire.
PROFILES = {
    "prod": {  # Google SRE Workbook ch.5: 2% of budget in 1h, 5% in 6h, 10% in 1d, 10% in 3d
        "page": {"pairs": [("1h", "5m", 14.4), ("6h", "30m", 6.0)], "for": "2m"},
        "ticket": {"pairs": [("1d", "2h", 3.0), ("3d", "6h", 1.0)], "for": "15m"},
    },
    "demo": {  # compressed windows so a fault-injection demo alerts within minutes
        "page": {"pairs": [("10m", "1m", 14.4)], "for": "1m"},
        "ticket": {"pairs": [("30m", "5m", 3.0)], "for": "3m"},
    },
}
SEVERITY_WORD = {"page": "FastBurn", "ticket": "SlowBurn"}


def load_cfg(path=ROOT / "slo" / "slo.yaml"):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def rec_name(window):
    return f"slo:sli_error:ratio_rate{window}"


def _route_filter(cfg):
    return "".join(f',route!="{r}"' for r in cfg.get("excluded_routes", []))


def error_ratio_expr(cfg, slo, window):
    sel = cfg["selector"] + _route_filter(cfg)
    if slo["type"] == "availability":
        total = mn.prom_counter(mn.REQUESTS)
        # `or vector(0)`: with no 5xx series the numerator is empty, which would blank the SLI when healthy.
        return (f'(sum(rate({total}{{{sel},code=~"5.."}}[{window}])) or vector(0)) '
                f'/ sum(rate({total}{{{sel}}}[{window}]))')
    if slo["type"] == "latency":
        thr = slo["threshold_seconds"]
        if thr not in mn.LATENCY_BUCKETS:
            raise ValueError(f"threshold {thr}s is not a histogram bucket boundary {mn.LATENCY_BUCKETS}")
        base = mn.prom_histogram(mn.DURATION)
        return (f'1 - (sum(rate({base}_bucket{{{sel},le="{thr}"}}[{window}])) '
                f'/ sum(rate({base}_count{{{sel}}}[{window}])))')
    raise ValueError(f"unknown SLO type {slo['type']}")


def all_windows(profile):
    ws = {"30d"}
    for sev in PROFILES[profile].values():
        for long_w, short_w, _ in sev["pairs"]:
            ws.update([long_w, short_w])
    unit = {"m": 1, "h": 60, "d": 1440}
    return sorted(ws, key=lambda w: int(w[:-1]) * unit[w[-1]])


def threshold(burn, objective):
    return float(f"{burn * (1 - objective):.10g}")


def camel(s):
    return "".join(p.capitalize() for p in s.replace("-", " ").split())


def build_rule(cfg, profile):
    svc = cfg["service"]
    recording, alerts = [], []
    for slo in cfg["slos"]:
        labels = {"service": svc, "slo": slo["name"]}
        for w in all_windows(profile):
            recording.append({"record": rec_name(w), "expr": error_ratio_expr(cfg, slo, w), "labels": dict(labels)})
        budget = 1 - slo["objective"]
        recording.append({
            "record": "slo:error_budget_remaining:ratio",
            "expr": f'1 - ({rec_name("30d")}{{slo="{slo["name"]}",service="{svc}"}} / {budget:.10g})',
            "labels": dict(labels),
        })
        recording.append({
            "record": "slo:objective:ratio", "expr": f"vector({slo['objective']})", "labels": dict(labels)})
        for sev, spec in PROFILES[profile].items():
            for long_w, short_w, burn in spec["pairs"]:
                thr = threshold(burn, slo["objective"])
                sel = f'{{slo="{slo["name"]}",service="{svc}"}}'
                alerts.append({
                    "alert": f"{camel(svc)}{camel(slo['name'])}{SEVERITY_WORD[sev]}",
                    "expr": f"{rec_name(long_w)}{sel} > {thr} and {rec_name(short_w)}{sel} > {thr}",
                    "for": spec["for"],
                    "labels": {"severity": sev, "slo": slo["name"], "service": svc,
                               "burn_window": f"{long_w}/{short_w}"},
                    "annotations": {
                        "summary": f"{svc} {slo['name']} SLO burning {burn}x budget ({long_w} and {short_w} windows)",
                        "description": (f"{slo['description']}. Error ratio exceeds {thr:.4%} "
                                        f"({burn}x the allowed {budget:.3%}) over both {long_w} and {short_w}. "
                                        f"At this rate the 30d budget is gone in {30 / burn:.1f} days."),
                        "runbook_url": f"runbooks/slo-burn.md#{slo['name']}-{'fast' if sev == 'page' else 'slow'}-burn",
                        "dashboard": "Grafana > Shop API SLOs",
                    },
                })
    return {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PrometheusRule",
        "metadata": {"name": f"{svc}-slo", "namespace": cfg["namespace"],
                     "labels": {"app.kubernetes.io/part-of": "slo-platform", "slo-profile": profile}},
        "spec": {"groups": [
            {"name": f"{svc}-slo-recording", "interval": "30s", "rules": recording},
            {"name": f"{svc}-slo-alerts", "rules": alerts},
        ]},
    }


# ------------------------------------------------------------------ dashboard
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}


def _target(expr, legend="", ds=PROM, ref="A"):
    return {"datasource": ds, "expr": expr, "legendFormat": legend, "refId": ref}


def _panel(pid, title, kind, x, y, w, h, targets, unit=None, desc=""):
    p = {"id": pid, "title": title, "type": kind, "description": desc,
         "gridPos": {"x": x, "y": y, "w": w, "h": h}, "datasource": targets[0]["datasource"],
         "targets": [dict(t, refId=chr(65 + i)) for i, t in enumerate(targets)], "fieldConfig": {"defaults": {}, "overrides": []}}
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    if kind == "logs":
        p["options"] = {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending"}
    return p


def build_dashboard(cfg):
    svc, sel = cfg["service"], cfg["selector"]
    req = mn.prom_counter(mn.REQUESTS)
    dur = mn.prom_histogram(mn.DURATION)
    av = next(s for s in cfg["slos"] if s["type"] == "availability")
    lat = next(s for s in cfg["slos"] if s["type"] == "latency")
    budget = lambda s: 1 - s["objective"]  # noqa: E731
    tot = f'sum by (k8s_pod_name) (rate({req}{{{sel}}}[5m]))'
    pods = f'({tot.replace("{" + sel + "}", "{" + sel + ",code=~\"5..\"}")} / {tot}) or ({tot} * 0)'
    panels = [
        _panel(1, "Availability SLI (30d)", "stat", 0, 0, 6, 4,
               [_target(f'1 - {rec_name("30d")}{{slo="availability"}}')], "percentunit"),
        _panel(2, "Availability error budget remaining", "gauge", 6, 0, 6, 4,
               [_target('slo:error_budget_remaining:ratio{slo="availability"}')], "percentunit"),
        _panel(3, "Latency SLI (30d, < %gs)" % lat["threshold_seconds"], "stat", 12, 0, 6, 4,
               [_target(f'1 - {rec_name("30d")}{{slo="latency"}}')], "percentunit"),
        _panel(4, "Latency error budget remaining", "gauge", 18, 0, 6, 4,
               [_target('slo:error_budget_remaining:ratio{slo="latency"}')], "percentunit"),
        _panel(5, "Availability burn rate (1 = on budget; page at 14.4)", "timeseries", 0, 4, 12, 8,
               [_target(f'{rec_name("5m")}{{slo="availability"}} / {budget(av):.10g}', "5m"),
                _target(f'{rec_name("1h")}{{slo="availability"}} / {budget(av):.10g}', "1h"),
                _target(f'{rec_name("6h")}{{slo="availability"}} / {budget(av):.10g}', "6h")]),
        _panel(6, "Latency burn rate (1 = on budget; page at 14.4)", "timeseries", 12, 4, 12, 8,
               [_target(f'{rec_name("5m")}{{slo="latency"}} / {budget(lat):.10g}', "5m"),
                _target(f'{rec_name("1h")}{{slo="latency"}} / {budget(lat):.10g}', "1h"),
                _target(f'{rec_name("6h")}{{slo="latency"}} / {budget(lat):.10g}', "6h")]),
        _panel(7, "Request rate by route", "timeseries", 0, 12, 8, 8,
               [_target(f'sum by (route) (rate({req}{{{sel}}}[5m]))', "{{route}}")], "reqps"),
        _panel(8, "p99 latency by route", "timeseries", 8, 12, 8, 8,
               [_target(f'histogram_quantile(0.99, sum by (le, route) (rate({dur}_bucket{{{sel}}}[5m])))', "{{route}}")], "s"),
        _panel(9, "5xx ratio by pod", "timeseries", 16, 12, 8, 8,
               [_target(pods, "{{k8s_pod_name}}")], "percentunit",
               "Use this to tell a single bad pod from a fleet-wide problem."),
        _panel(10, "Error logs (click a line, then the TraceID link, to jump to the trace)", "logs", 0, 20, 24, 10,
               [_target(f'{{service_name="{svc}"}} |~ "(?i)fail|error|fault"', ds=LOKI)]),
    ]
    return {"uid": "shop-api-slo", "title": "Shop API SLOs", "tags": ["slo", svc], "timezone": "browser",
            "schemaVersion": 39, "version": 1, "refresh": "30s", "time": {"from": "now-3h", "to": "now"},
            "panels": panels}


def dashboard_configmap(cfg):
    return {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": "shop-api-slo-dashboard", "namespace": cfg["namespace"],
                         "labels": {"grafana_dashboard": "1"}},
            "data": {"shop-api-slo.json": json.dumps(build_dashboard(cfg), indent=1)}}


def render(obj):
    return yaml.safe_dump(obj, sort_keys=False, width=10_000)


def outputs(cfg=None):
    cfg = cfg or load_cfg()
    return {
        "slo-rules-prod.yaml": render(build_rule(cfg, "prod")),
        "slo-rules-demo.yaml": render(build_rule(cfg, "demo")),
        "dashboard-slo.yaml": render(dashboard_configmap(cfg)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    stale = []
    for name, text in outputs().items():
        path = ROOT / "k8s" / name
        if a.check:
            if not path.exists() or path.read_text() != text:
                stale.append(name)
        else:
            path.write_text(text)
            print("wrote", path.relative_to(ROOT))
    if stale:
        print("out of date:", ", ".join(stale), "- run `make rules`")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
