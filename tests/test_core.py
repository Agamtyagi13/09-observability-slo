import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
from core import (Fault, FaultState, FaultValidationError, payments_delay_seconds,  # noqa: E402
                  payments_should_fail)


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    return False


def test_default_is_no_fault():
    assert FaultState().snapshot() == Fault(0, 0.0)


def test_set_partial_updates_keep_other_field():
    s = FaultState()
    s.set(latency_ms=900)
    s.set(error_rate=0.4)
    assert s.snapshot() == Fault(900, 0.4)


def test_clear():
    s = FaultState()
    s.set(500, 0.5)
    assert s.clear() == Fault(0, 0.0)


def test_validation_rejects_bad_input():
    s = FaultState()
    for kw in ({"latency_ms": -1}, {"latency_ms": 10_001}, {"latency_ms": 1.5}, {"latency_ms": True},
               {"error_rate": 1.1}, {"error_rate": -0.1}, {"error_rate": "x"}):
        assert raises(lambda kw=kw: s.set(**kw), FaultValidationError), kw
    assert s.snapshot() == Fault(0, 0.0)  # failed sets leave state untouched


def test_payment_delay_includes_injected_latency():
    rng = random.Random(1)
    base = payments_delay_seconds(Fault(), rng)
    slow = payments_delay_seconds(Fault(payments_latency_ms=900), random.Random(1))
    assert 0.03 <= base <= 0.06
    assert abs(slow - base - 0.9) < 1e-9


def test_failure_rate_is_statistically_close():
    rng = random.Random(42)
    f = Fault(payments_error_rate=0.4)
    n = sum(payments_should_fail(f, rng) for _ in range(20_000))
    assert abs(n / 20_000 - 0.4) < 0.02
    assert not any(payments_should_fail(Fault(), rng) for _ in range(1000))
    assert all(payments_should_fail(Fault(payments_error_rate=1.0), rng) for _ in range(1000))
