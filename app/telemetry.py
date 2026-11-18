"""OpenTelemetry setup: traces, metrics and logs over OTLP to the collector gateway.

Set OTEL_SDK_DISABLED=true to run locally with no collector (instruments become no-ops).
"""
import logging
import os
import sys
from types import SimpleNamespace

from opentelemetry import metrics, trace

import metrics_names as mn


def current_trace_id():
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx and ctx.is_valid else "-"


def _stdout_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(h)
    return root


def setup():
    root = _stdout_logging()
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        meter = metrics.get_meter("shop-api")
        return SimpleNamespace(
            tracer=trace.get_tracer("shop-api"),
            requests=meter.create_counter(mn.REQUESTS),
            duration=meter.create_histogram(mn.DURATION, unit=mn.DURATION_UNIT),
        )

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # Reads OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES (set in the Deployment).
    resource = Resource.create({})

    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tp)

    view = View(
        instrument_name=mn.DURATION,
        aggregation=ExplicitBucketHistogramAggregation(mn.LATENCY_BUCKETS),
    )
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=10_000)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader], views=[view]))

    lp = LoggerProvider(resource=resource)
    lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(lp)
    root.addHandler(LoggingHandler(level=logging.INFO, logger_provider=lp))

    meter = metrics.get_meter("shop-api")
    return SimpleNamespace(
        tracer=trace.get_tracer("shop-api"),
        requests=meter.create_counter(mn.REQUESTS, description="HTTP requests"),
        duration=meter.create_histogram(mn.DURATION, unit=mn.DURATION_UNIT, description="HTTP request duration"),
    )
