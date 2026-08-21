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


class TestObservationAge:
    """Age must survive the device stepping the clock this endpoint exists to set.

    `age_seconds` gates whether a caller trusts the reading enough to set its
    RTC from it. If age were measured on the wall clock, the very act of
    correcting that RTC would rewrite how old an existing observation looks --
    backwards, so a stale reading reads as fresh, or negative.
    """

    def test_a_backward_clock_step_does_not_make_a_stale_reading_look_fresh(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("time.time", lambda: 10_000.0)
        monkeypatch.setattr("time.monotonic", lambda: 500.0)
        tb = ServerTimebase(drift_warn_seconds=0)
        tb.observe(10_060)

        # The device applies the correction and steps its RTC back an hour,
        # while the monotonic clock advances the 30s that really elapsed.
        monkeypatch.setattr("time.time", lambda: 10_000.0 - 3_600.0 + 30.0)
        monkeypatch.setattr("time.monotonic", lambda: 530.0)

        age = tb.snapshot()["global"]["age_seconds"]
        assert age == 30.0

    def test_age_is_never_negative(self, monkeypatch: pytest.MonkeyPatch):
        """The fallback path still reads a wall clock that can run backwards."""
        monkeypatch.setattr("time.time", lambda: 10_000.0)
        tb = ServerTimebase(drift_warn_seconds=0)
        tb.observe(10_060)
        # An observation carrying no monotonic stamp, as one constructed
        # directly would be.
        tb._global = TimeObservation(offset=60.0, receipt_epoch=10_000.0, quality=3, href="/tm")

        monkeypatch.setattr("time.time", lambda: 9_000.0)
        assert tb.snapshot()["global"]["age_seconds"] == 0.0


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


class TestServerNow:
    """server_now() answers "what does the head-end say", not "what should I schedule against"."""

    def test_none_when_never_observed(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        # now() falls back to the local clock here; server_now() must not, or a
        # caller writing this to a device clock cannot tell the two apart.
        assert tb.now() == 1_000.0
        assert tb.server_now() is None

    def test_applies_observed_offset(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        tb.observe(1_030)
        assert tb.server_now() == 1_030.0

    def test_tracks_local_clock_between_observations(self, monkeypatch: pytest.MonkeyPatch):
        """The reading advances with the local clock rather than pinning to the
        last currentTime seen -- otherwise it would be a staircase, not a clock."""
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        tb.observe(1_030)
        _freeze_time(monkeypatch, 1_060.0)
        assert tb.server_now() == 1_090.0

    def test_reports_server_time_even_when_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """`enabled` governs whether this client follows server time, not what
        the server's time is; the reading stays available for troubleshooting."""
        tb = ServerTimebase(enabled=False)
        _freeze_time(monkeypatch, 1_000.0)
        tb.observe(1_030)
        assert tb.offset() == 0.0
        assert tb.now() == 1_000.0
        assert tb.server_now() == 1_030.0

    def test_fsa_scope_overrides_global(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        tb.observe(1_010)
        tb.observe(1_050, fsa_href="/fsa/1")
        assert tb.server_now("/fsa/1") == 1_050.0
        assert tb.server_now("/fsa/other") == 1_010.0
        assert tb.server_now() == 1_010.0

    def test_fsa_only_no_global(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase()
        _freeze_time(monkeypatch, 1_000.0)
        tb.observe(1_050, fsa_href="/fsa/1")
        assert tb.server_now("/fsa/1") == 1_050.0
        assert tb.server_now() is None


def _freeze_clocks(monkeypatch: pytest.MonkeyPatch, wall: float, mono: float) -> None:
    """Freeze both clocks independently, so a wall-clock step can be told apart
    from elapsed time -- which is the distinction this module turns on."""
    monkeypatch.setattr("time.time", lambda: wall)
    monkeypatch.setattr("time.monotonic", lambda: mono)


class TestStaleFsaFallback:
    """A per-FSA observation nobody is renewing stops being the better answer.

    §9.2.3 makes the FSA's own Time resource authoritative for that FSA's
    events, and it stays preferred for as long as it is current. Once it is
    not, it is the more specific way to be wrong, and it is wrong silently --
    a frozen offset reads exactly like a fresh one at the point of use.
    """

    def test_fresh_fsa_observation_still_wins(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase(drift_warn_seconds=0, fsa_stale_seconds=3600)
        _freeze_clocks(monkeypatch, 1_000.0, 100.0)
        tb.observe(1_010)
        tb.observe(1_050, fsa_href="/fsa/1")
        _freeze_clocks(monkeypatch, 1_100.0, 200.0)
        assert tb.offset("/fsa/1") == 50.0

    def test_stale_fsa_observation_yields_to_a_newer_global(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase(drift_warn_seconds=0, fsa_stale_seconds=60)
        _freeze_clocks(monkeypatch, 1_000.0, 100.0)
        tb.observe(1_050, fsa_href="/fsa/1")  # offset 50, then never renewed
        _freeze_clocks(monkeypatch, 5_000.0, 4_100.0)  # 4000s of real elapsed time
        tb.observe(5_200)  # global offset 200, fresh
        assert tb.offset("/fsa/1") == 200.0

    def test_stale_fsa_is_kept_when_global_is_older_still(self, monkeypatch: pytest.MonkeyPatch):
        """Falling back to an even staler global would be a downgrade, not a fix."""
        tb = ServerTimebase(drift_warn_seconds=0, fsa_stale_seconds=60)
        _freeze_clocks(monkeypatch, 1_000.0, 100.0)
        tb.observe(1_200)  # global first
        _freeze_clocks(monkeypatch, 2_000.0, 1_100.0)
        tb.observe(2_050, fsa_href="/fsa/1")  # FSA newer; both later go stale
        _freeze_clocks(monkeypatch, 9_000.0, 8_100.0)
        assert tb.offset("/fsa/1") == 50.0

    def test_stale_fsa_with_no_global_is_still_used(self, monkeypatch: pytest.MonkeyPatch):
        """A stale offset beats no offset; the alternative is the raw local clock."""
        tb = ServerTimebase(drift_warn_seconds=0, fsa_stale_seconds=60)
        _freeze_clocks(monkeypatch, 1_000.0, 100.0)
        tb.observe(1_050, fsa_href="/fsa/1")
        _freeze_clocks(monkeypatch, 9_000.0, 8_100.0)
        assert tb.offset("/fsa/1") == 50.0

    def test_server_now_uses_the_same_resolution(self, monkeypatch: pytest.MonkeyPatch):
        tb = ServerTimebase(drift_warn_seconds=0, fsa_stale_seconds=60)
        _freeze_clocks(monkeypatch, 1_000.0, 100.0)
        tb.observe(1_050, fsa_href="/fsa/1")
        _freeze_clocks(monkeypatch, 5_000.0, 4_100.0)
        tb.observe(5_200)
        assert tb.server_now("/fsa/1") == 5_200.0

    def test_a_wall_clock_step_does_not_age_an_observation(self, monkeypatch: pytest.MonkeyPatch):
        """Staleness is elapsed time, not wall-clock distance.

        A device that syncs its RTC from this client steps the wall clock by
        exactly the offset the client just reported. If that counted as age,
        every observation would go stale the instant the fix it enabled was
        applied -- and the timebase would abandon the scope that was working.
        """
        tb = ServerTimebase(drift_warn_seconds=0, fsa_stale_seconds=60)
        _freeze_clocks(monkeypatch, 1_000.0, 100.0)
        tb.observe(1_050, fsa_href="/fsa/1")
        tb.observe(1_010)
        # Wall clock jumps an hour; no real time has passed.
        _freeze_clocks(monkeypatch, 4_600.0, 100.5)
        assert tb.offset("/fsa/1") == 50.0
