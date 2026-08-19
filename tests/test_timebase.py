"""Tests for the application-level server timebase (client/timebase.py)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from py20305 import diagnostics
from py20305.client.timebase import (
    ServerTimebase,
    TimeObservation,
    observe_time_resource,
)


def _freeze_time(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    monkeypatch.setattr("time.time", lambda: value)


class TestOffsetMath:
    def test_observe_computes_server_minus_local(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        offset = tb.observe(1_030)
        assert offset == 30.0
        assert tb.offset() == 30.0
        assert tb.now() == 1_030.0

    def test_negative_offset(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        tb.observe(940)
        assert tb.offset() == -60.0
        assert tb.now() == 940.0

    def test_reobserve_replaces_offset(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        tb.observe(1_010)
        tb.observe(1_020)
        assert tb.offset() == 20.0


class TestScopeFallback:
    def test_fsa_scope_overrides_global(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        tb.observe(1_010)  # global
        tb.observe(1_050, fsa_href="/fsa/1")
        assert tb.offset("/fsa/1") == 50.0
        assert tb.offset("/fsa/other") == 10.0  # falls back to global
        assert tb.offset() == 10.0

    def test_unobserved_returns_local_clock(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        assert tb.offset() == 0.0
        assert tb.offset("/fsa/1") == 0.0
        assert tb.now() == 1_000.0

    def test_fsa_only_no_global(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        tb.observe(1_050, fsa_href="/fsa/1")
        assert tb.offset("/fsa/1") == 50.0
        assert tb.offset() == 0.0  # no global observation yet


class TestDisabled:
    def test_disabled_returns_local_but_still_observes(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase(enabled=False)
        _freeze_time(monkeypatch, 1_000.0)
        tb.observe(1_100)
        assert tb.offset() == 0.0
        assert tb.now() == 1_000.0
        snap = tb.snapshot()
        assert snap["enabled"] is False
        assert snap["global"] is not None
        assert snap["global"]["offset_seconds"] == 100.0

    def test_disabled_drift_diagnostic_still_fires(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase(enabled=False, drift_warn_seconds=30)
        with patch.object(diagnostics, "report") as rep:
            tb.observe(int(time.time()) + 60)
        rep.assert_called_once()
        assert "LOCAL clock" in rep.call_args.args[1]


class TestDriftDiagnostic:
    def test_fires_at_threshold(self):
        tb = ServerTimebase(drift_warn_seconds=30)
        with patch.object(diagnostics, "report") as rep:
            tb.observe(int(time.time()) + 45, href="/tm")
        rep.assert_called_once()
        kwargs = rep.call_args.kwargs
        assert kwargs["dedup_key"] == "timebase:drift:global"
        assert kwargs["details"]["href"] == "/tm"

    def test_scoped_dedup_key(self):
        tb = ServerTimebase(drift_warn_seconds=30)
        with patch.object(diagnostics, "report") as rep:
            tb.observe(int(time.time()) - 90, fsa_href="/fsa/1")
        assert rep.call_args.kwargs["dedup_key"] == "timebase:drift:/fsa/1"

    def test_below_threshold_silent(self):
        tb = ServerTimebase(drift_warn_seconds=30)
        with patch.object(diagnostics, "report") as rep:
            tb.observe(int(time.time()) + 5)
        rep.assert_not_called()

    def test_zero_threshold_disables(self):
        tb = ServerTimebase(drift_warn_seconds=0)
        with patch.object(diagnostics, "report") as rep:
            tb.observe(int(time.time()) + 9_999)
        rep.assert_not_called()

    def test_quality_in_details(self):
        tb = ServerTimebase(drift_warn_seconds=1)
        with patch.object(diagnostics, "report") as rep:
            tb.observe(int(time.time()) + 60, quality=3)
        assert rep.call_args.kwargs["details"]["quality"] == 3


class TestObserveTimeResource:
    def test_real_time_resource_observed(self, monkeypatch: pytest.MonkeyPatch):
        from py20305.models.sep.sep import Time, TimeOffsetType, TimeType

        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        t = Time(
            current_time=TimeType(value=1_025),
            dst_end_time=TimeType(value=0),
            dst_offset=TimeOffsetType(value=0),
            dst_start_time=TimeType(value=0),
            quality=3,
            tz_offset=TimeOffsetType(value=0),
        )
        observe_time_resource(tb, t, href="/tm")
        assert tb.offset() == 25.0
        assert tb.snapshot()["global"]["quality"] == 3

    def test_mock_response_skipped(self):
        tb = ServerTimebase()
        observe_time_resource(tb, MagicMock())  # current_time.value is a Mock
        assert tb.snapshot()["global"] is None

    def test_none_resource_skipped(self):
        tb = ServerTimebase()
        observe_time_resource(tb, None)
        assert tb.snapshot()["global"] is None

    def test_non_int_quality_coerced(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        t = MagicMock()
        t.current_time.value = 1_010
        t.quality = MagicMock()  # not an int
        observe_time_resource(tb, t)
        assert tb.offset() == 10.0
        assert tb.snapshot()["global"]["quality"] == 0

    def test_fsa_scoped_observation(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        t = MagicMock()
        t.current_time.value = 1_040
        t.quality = 4
        observe_time_resource(tb, t, fsa_href="/fsa/9", href="/fsa/9/tm")
        assert tb.offset("/fsa/9") == 40.0
        assert tb.snapshot()["per_fsa"]["/fsa/9"]["href"] == "/fsa/9/tm"


def test_observation_dataclass_frozen():
    obs = TimeObservation(offset=1.0, receipt_epoch=2.0, quality=3, href="/tm")
    with pytest.raises(AttributeError):
        obs.offset = 2.0  # type: ignore[misc]


def test_observe_uses_single_receipt_instant(monkeypatch: pytest.MonkeyPatch):
    """offset and receipt_epoch must describe the same time.time() call; a
    second capture would skew the stored offset/age pair."""
    ticks = iter([1_000.0, 2_000.0, 3_000.0])
    monkeypatch.setattr("time.time", lambda: next(ticks))
    tb = ServerTimebase(drift_warn_seconds=0)

    tb.observe(1_030)

    obs = tb._global
    assert obs is not None
    assert obs.offset == 30.0  # computed against the 1000.0 tick...
    assert obs.receipt_epoch == 1_000.0  # ...and stamped with that same tick
