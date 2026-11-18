"""shop-api: sample service with metrics, logs and traces, plus runtime fault injection."""
import functools
import logging
import os
import random
import time
import uuid
from dataclasses import asdict

from flask import Flask, jsonify, request
from opentelemetry.trace import Status, StatusCode

import telemetry
from core import (FaultState, FaultValidationError, inventory_delay_seconds,
                  payments_delay_seconds, payments_should_fail, products_delay_seconds)

tel = telemetry.setup()
log = logging.getLogger("shop")
rng = random.Random()
faults = FaultState()
app = Flask(__name__)


class UpstreamError(Exception):
    pass


def instrumented(route):
    """One server span + request counter + duration histogram per call; handler returns (body, code)."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start, code = time.perf_counter(), 500
            with tel.tracer.start_as_current_span(f"{request.method} {route}") as span:
                span.set_attribute("http.route", route)
                try:
                    body, code = fn(*args, **kwargs)
                    resp = jsonify(body), code
                except UpstreamError as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    log.error("request failed route=%s error=%s trace_id=%s", route, exc,
                              telemetry.current_trace_id())
                    resp = jsonify(error=str(exc)), 500
                span.set_attribute("http.status_code", code)
                attrs = {"route": route, "method": request.method, "code": str(code)}
                tel.requests.add(1, attrs)
                tel.duration.record(time.perf_counter() - start, attrs)
                return resp

        return wrapper

    return deco


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")  # deliberately not counted in SLIs


@app.get("/products")
@instrumented("/products")
def products():
    time.sleep(products_delay_seconds(rng))
    return {"items": [{"sku": f"sku-{i}", "price": 10 + i} for i in range(5)]}, 200


@app.post("/checkout")
@instrumented("/checkout")
def checkout():
    order_id = uuid.uuid4().hex[:8]
    f = faults.snapshot()
    with tel.tracer.start_as_current_span("inventory.reserve"):
        time.sleep(inventory_delay_seconds(rng))
    with tel.tracer.start_as_current_span("payments.charge") as span:
        span.set_attribute("payments.injected_latency_ms", f.payments_latency_ms)
        time.sleep(payments_delay_seconds(f, rng))
        if payments_should_fail(f, rng):
            raise UpstreamError("payment gateway returned 503")
    log.info("checkout confirmed order=%s trace_id=%s", order_id, telemetry.current_trace_id())
    return {"order": order_id, "status": "confirmed"}, 200


@app.route("/admin/fault", methods=["GET", "POST"])
def admin_fault():
    token = os.environ.get("ADMIN_TOKEN")
    if token and request.headers.get("X-Admin-Token") != token:
        return jsonify(error="forbidden"), 403
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        try:
            if body.get("clear"):
                faults.clear()
            else:
                faults.set(body.get("payments_latency_ms"), body.get("payments_error_rate"))
        except FaultValidationError as exc:
            return jsonify(error=str(exc)), 400
        log.warning("fault injection changed: %s trace_id=-", asdict(faults.snapshot()))
    return jsonify(asdict(faults.snapshot()))
