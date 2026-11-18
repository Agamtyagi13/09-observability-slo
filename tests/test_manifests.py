import json
import pathlib
import re
import sys
import threading
import urllib.request
from http.server import HTTPServer

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def load_all(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def vals(name):
    return yaml.safe_load((ROOT / "helm" / name).read_text())


def test_all_yaml_parses():
    n = 0
    for p in list((ROOT / "k8s").glob("*.yaml")) + list((ROOT / "helm").glob("*.yaml")) + [ROOT / "kind-config.yaml"]:
        assert load_all(p), p
        n += 1
    assert n >= 12


def test_ha_replica_counts():
    kps = vals("values-prometheus-stack.yaml")
    assert kps["prometheus"]["prometheusSpec"]["replicas"] == 2
    assert kps["alertmanager"]["alertmanagerSpec"]["replicas"] == 3
    assert kps["grafana"]["replicas"] == 2
    assert vals("values-otel-collector.yaml")["replicaCount"] == 2
    dep = next(d for d in load_all(ROOT / "k8s" / "10-shop-api.yaml") if d["kind"] == "Deployment")
    assert dep["spec"]["replicas"] == 3


def test_disruption_budgets_present():
    pdb = next(d for d in load_all(ROOT / "k8s" / "10-shop-api.yaml") if d["kind"] == "PodDisruptionBudget")
    assert pdb["spec"]["minAvailable"] == 2
    kps = vals("values-prometheus-stack.yaml")
    assert kps["prometheus"]["podDisruptionBudget"]["enabled"]
    assert kps["alertmanager"]["podDisruptionBudget"]["enabled"]
    assert vals("values-otel-collector.yaml")["podDisruptionBudget"]["enabled"]


def test_prometheus_replica_label_is_deduplicated_label():
    assert vals("values-prometheus-stack.yaml")["prometheus"]["prometheusSpec"]["replicaExternalLabelName"] == "prometheus_replica"


def test_alertmanager_routes_reference_defined_receivers_and_inhibit():
    cfg = vals("values-prometheus-stack.yaml")["alertmanager"]["config"]
    names = {r["name"] for r in cfg["receivers"]}
    assert cfg["route"]["receiver"] in names
    for r in cfg["route"]["routes"]:
        assert r["receiver"] in names
    inh = cfg["inhibit_rules"][0]
    assert inh["equal"] == ["slo", "service"]
    assert {"severity=\"page\"" in inh["source_matchers"], "severity=\"ticket\"" in inh["target_matchers"]} == {True}


def test_collector_pipelines_cover_all_signals_and_export_targets():
    cfg = vals("values-otel-collector.yaml")["config"]
    p = cfg["service"]["pipelines"]
    assert set(p) == {"traces", "logs", "metrics"}
    for pipe in p.values():
        assert pipe["processors"][0] == "memory_limiter"      # must be first
        for e in pipe["exporters"]:
            assert e in cfg["exporters"]
    assert cfg["exporters"]["prometheus"]["resource_to_telemetry_conversion"]["enabled"]


def test_servicemonitor_ports_exist_on_collector():
    sm = load_all(ROOT / "k8s" / "40-otel-servicemonitor.yaml")[0]
    ports = vals("values-otel-collector.yaml")["ports"]
    for ep in sm["spec"]["endpoints"]:
        assert ports[ep["port"]]["enabled"]


def test_datasource_uids_match_dashboard():
    ds = {d["uid"] for d in vals("values-prometheus-stack.yaml")["grafana"]["additionalDataSources"]}
    assert ds == {"loki", "tempo"}
    cm = load_all(ROOT / "k8s" / "dashboard-slo.yaml")[0]
    dash = json.loads(cm["data"]["shop-api-slo.json"])
    used = {p["datasource"]["uid"] for p in dash["panels"]}
    assert used <= {"prometheus", "loki"}
    assert cm["metadata"]["labels"]["grafana_dashboard"] == "1"


def test_dashboard_uses_only_recorded_or_app_metrics():
    dash = json.loads(load_all(ROOT / "k8s" / "dashboard-slo.yaml")[0]["data"]["shop-api-slo.json"])
    rec = {r["record"] for r in load_all(ROOT / "k8s" / "slo-rules-prod.yaml")[0]["spec"]["groups"][0]["rules"]}
    rec |= {r["record"] for r in load_all(ROOT / "k8s" / "slo-rules-demo.yaml")[0]["spec"]["groups"][0]["rules"]}
    for p in dash["panels"]:
        for t in p["targets"]:
            for name in re.findall(r"slo:[a-z_]+:ratio(?:_rate\w+)?", t["expr"]):
                assert name in rec, name


def _slug(h):
    return re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-")


def test_runbook_anchors_exist():
    text = (ROOT / "runbooks" / "slo-burn.md").read_text()
    anchors = {_slug(m.group(1)) for m in re.finditer(r"^#+\s+(.+)$", text, re.M)}
    urls = []
    for f in ("slo-rules-prod.yaml", "slo-rules-demo.yaml"):
        for r in load_all(ROOT / "k8s" / f)[0]["spec"]["groups"][1]["rules"]:
            urls.append(r["annotations"]["runbook_url"])
    urls += [r["annotations"]["runbook_url"] for r in load_all(ROOT / "k8s" / "50-meta-rules.yaml")[0]["spec"]["groups"][0]["rules"]]
    for u in urls:
        path, anchor = u.split("#")
        assert (ROOT / path).exists() and anchor in anchors, u


def test_app_pods_have_security_context_and_otel_env():
    dep = next(d for d in load_all(ROOT / "k8s" / "10-shop-api.yaml") if d["kind"] == "Deployment")
    c = dep["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"] for e in c["env"]}
    assert {"OTEL_SERVICE_NAME", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_RESOURCE_ATTRIBUTES", "POD_NAME"} <= env
    assert c["securityContext"]["readOnlyRootFilesystem"] and dep["spec"]["template"]["spec"]["securityContext"]["runAsNonRoot"]


def test_alert_sink_roundtrip():
    import alert_sink as s
    srv = HTTPServer(("127.0.0.1", 0), s.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    body = json.dumps({"alerts": [{"status": "firing", "startsAt": "t0",
                                   "labels": {"alertname": "X", "severity": "page", "slo": "latency"}}]}).encode()
    urllib.request.urlopen(urllib.request.Request(base + "/page", data=body, method="POST"), timeout=5)
    got = json.loads(urllib.request.urlopen(base + "/alerts", timeout=5).read())
    srv.shutdown()
    assert got[-1]["alertname"] == "X" and got[-1]["receiver"] == "page" and got[-1]["status"] == "firing"
