"""Tests for the backend layer: no DNS provider is live-wired, so the default
backend must fail fast on every method; the system clock is the one real-IO
surface and is smoke-checked.
"""

from __future__ import annotations

import pytest

from dns_mcp.backend import BackendError, SystemClock, UnconfiguredBackend
from dns_mcp.core import Record, RecordKey

KEY = RecordKey(name="www", type="A")
RECORD = Record(name="www", type="A", value="203.0.113.10", ttl=300)


def test_unconfigured_backend_is_not_wired() -> None:
    be = UnconfiguredBackend(provider="porkbun", zone="example.com")
    with pytest.raises(BackendError, match="not wired up"):
        be.list_records("example.com", credentials=None)
    with pytest.raises(BackendError, match="not wired up"):
        be.get_record("example.com", KEY, credentials=None)
    with pytest.raises(BackendError, match="not wired up"):
        be.create_record("example.com", RECORD, credentials=None)
    with pytest.raises(BackendError, match="not wired up"):
        be.update_record("example.com", RECORD, credentials=None)
    with pytest.raises(BackendError, match="not wired up"):
        be.delete_record("example.com", KEY, credentials=None)


def test_unconfigured_backend_constructs_without_config() -> None:
    # Constructing it must not require config or touch the network.
    assert isinstance(UnconfiguredBackend(), UnconfiguredBackend)


def test_system_clock_now_iso_is_utc() -> None:
    stamp = SystemClock().now_iso()
    assert stamp.endswith("Z")


def test_system_clock_monotonic_advances() -> None:
    clock = SystemClock()
    first = clock.monotonic_ns()
    second = clock.monotonic_ns()
    assert second >= first
