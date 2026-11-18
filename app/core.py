"""Pure-Python fault-injection state and timing logic (no third-party imports, unit-testable)."""
import threading
from dataclasses import dataclass


class FaultValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Fault:
    payments_latency_ms: int = 0
    payments_error_rate: float = 0.0


class FaultState:
    def __init__(self):
        self._fault = Fault()
        self._lock = threading.Lock()

    def snapshot(self):
        with self._lock:
            return self._fault

    def set(self, latency_ms=None, error_rate=None):
        cur = self.snapshot()
        lat = cur.payments_latency_ms if latency_ms is None else latency_ms
        err = cur.payments_error_rate if error_rate is None else error_rate
        if isinstance(lat, bool) or not isinstance(lat, int) or not 0 <= lat <= 10_000:
            raise FaultValidationError("payments_latency_ms must be an integer 0..10000")
        if isinstance(err, bool) or not isinstance(err, (int, float)) or not 0 <= err <= 1:
            raise FaultValidationError("payments_error_rate must be a number 0..1")
        with self._lock:
            self._fault = Fault(int(lat), float(err))
            return self._fault

    def clear(self):
        with self._lock:
            self._fault = Fault()
            return self._fault


def inventory_delay_seconds(rng):
    return rng.uniform(0.005, 0.015)


def products_delay_seconds(rng):
    return rng.uniform(0.010, 0.030)


def payments_delay_seconds(fault, rng):
    return rng.uniform(0.030, 0.060) + fault.payments_latency_ms / 1000.0


def payments_should_fail(fault, rng):
    return rng.random() < fault.payments_error_rate
