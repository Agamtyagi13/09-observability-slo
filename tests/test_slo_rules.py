import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "slo"))
sys.path.insert(0, str(ROOT / "app"))
import generate_rules as g  # noqa: E402
import metrics_names as mn  # noqa: E402

CFG = g.load_cfg()


def rule(profile):
    return g.build_rule(CFG, profile)


def alerts(profile):
    return rule(profile)["spec"]["groups"][1]["rules"]


def recordings(profile):
    return rule(profile)["spec"]["groups"][0]["rules"]


def test_thresholds_match_sre_workbook():
    assert g.threshold(14.4, 0.999) == 0.0144
    assert g.threshold(6, 0.999) == 0.006
    assert g.threshold(3, 0.999) == 0.003
    assert g.threshold(1, 0.999) == 0.001
    assert g.threshold(14.4, 0.99) == 0.144


def test_alert_counts_per_profile():
    assert len(alerts("prod")) == 2 * (2 + 2)   # 2 SLOs x (2 page + 2 ticket windows)
    assert len(alerts("demo")) == 2 * (1 + 1)


def test_every_alert_references_recorded_windows():
    for prof in ("prod", "demo"):
        recorded = {r["record"] + "{" for r in recordings(prof)}
        for a in alerts(prof):
            for tok in a["expr"].split(" and "):
                assert tok.split("{")[0] + "{" in recorded, (prof, tok)


def test_multiwindow_requires_both_windows():
    for a in alerts("prod"):
        assert a["expr"].count(" > ") == 2 and " and " in a["expr"]


def test_alert_labels_and_annotations_present():
    for a in alerts("prod"):
        assert a["labels"]["severity"] in ("page", "ticket")
        assert {"summary", "description", "runbook_url"} <= set(a["annotations"])


def test_availability_expr_survives_zero_errors():
    expr = g.error_ratio_expr(CFG, CFG["slos"][0], "5m")
    assert "or vector(0)" in expr and mn.prom_counter(mn.REQUESTS) in expr
    assert 'route!="/healthz"' in expr


def test_latency_expr_uses_bucket_and_count():
    expr = g.error_ratio_expr(CFG, CFG["slos"][1], "5m")
    base = mn.prom_histogram(mn.DURATION)
    assert f'{base}_bucket' in expr and f'{base}_count' in expr and 'le="0.5"' in expr


def test_threshold_must_be_a_bucket_boundary():
    bad = dict(CFG["slos"][1], threshold_seconds=0.3)
    try:
        g.error_ratio_expr(CFG, bad, "5m")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_metric_name_translation():
    assert mn.prom_counter(mn.REQUESTS) == "app_requests_total"
    assert mn.prom_histogram(mn.DURATION) == "app_request_duration_seconds"


def test_windows_sorted_and_include_30d():
    ws = g.all_windows("prod")
    assert ws == ["5m", "30m", "1h", "2h", "6h", "1d", "3d", "30d"]


def test_committed_files_are_up_to_date():
    for name, text in g.outputs(CFG).items():
        assert (ROOT / "k8s" / name).read_text() == text, f"{name} is stale: run `make rules`"


def test_detection_time_math_matches_runbook():
    # constant error ratio e: the 1h window crosses threshold t after (t/e) * 60 minutes
    thr, e = g.threshold(14.4, 0.999), 0.10
    assert abs(thr / e * 60 - 8.64) < 0.01
    # budget consumed by then (fraction of 30d budget)
    consumed = e * (thr / e * 60) / (30 * 24 * 60) / 0.001
    assert abs(consumed - 0.02) < 1e-6           # exactly the "2% of budget in 1h" design point


def test_prometheusrule_is_valid_yaml_object():
    d = yaml.safe_load((ROOT / "k8s" / "slo-rules-prod.yaml").read_text())
    assert d["kind"] == "PrometheusRule" and d["metadata"]["name"] == "shop-api-slo"
    d2 = yaml.safe_load((ROOT / "k8s" / "slo-rules-demo.yaml").read_text())
    assert d2["metadata"]["name"] == d["metadata"]["name"]    # demo profile overwrites prod in place
