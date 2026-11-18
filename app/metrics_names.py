"""Single source of truth for instrument names, shared by the app and the SLO rule generator.

OpenTelemetry -> Prometheus name translation (collector prometheus exporter):
  counter  "app.requests"          -> app_requests_total
  histogram "app.request.duration" (unit s) -> app_request_duration_seconds_{bucket,count,sum}
"""
REQUESTS = "app.requests"
DURATION = "app.request.duration"
DURATION_UNIT = "s"
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5)

_UNIT_SUFFIX = {"s": "seconds"}


def prom_counter(name):
    return name.replace(".", "_") + "_total"


def prom_histogram(name, unit=DURATION_UNIT):
    return name.replace(".", "_") + "_" + _UNIT_SUFFIX[unit]
