"""Tests for ActiveModeTracker (per-mode DDERC fallback)."""

from __future__ import annotations

from py20305.events.active_modes import ActiveModeTracker


class TestActiveModeTracker:
    def test_register_and_unregister_all_released(self):
        tracker = ActiveModeTracker()
        tracker.register("d1", b"\x01" * 16, frozenset({"mode_a", "mode_b"}))
        released = tracker.unregister("d1", b"\x01" * 16)
        assert released == frozenset({"mode_a", "mode_b"})

    def test_unregister_partial_overlap(self):
        """Event A: modes X, Y. Event B: mode Z. A completes -> X, Y released."""
        tracker = ActiveModeTracker()
        tracker.register("d1", b"\x01" * 16, frozenset({"x", "y"}))
        tracker.register("d1", b"\x02" * 16, frozenset({"z"}))
        released = tracker.unregister("d1", b"\x01" * 16)
        assert released == frozenset({"x", "y"})

    def test_unregister_shared_modes_not_released(self):
        """Event A: modes X, Y. Event B: modes X, Z. A completes -> only Y released."""
        tracker = ActiveModeTracker()
        tracker.register("d1", b"\x01" * 16, frozenset({"x", "y"}))
        tracker.register("d1", b"\x02" * 16, frozenset({"x", "z"}))
        released = tracker.unregister("d1", b"\x01" * 16)
        assert released == frozenset({"y"})

    def test_unregister_unknown_device(self):
        tracker = ActiveModeTracker()
        released = tracker.unregister("unknown", b"\x01" * 16)
        assert released == frozenset()

    def test_unregister_unknown_mrid(self):
        tracker = ActiveModeTracker()
        tracker.register("d1", b"\x01" * 16, frozenset({"x"}))
        released = tracker.unregister("d1", b"\x99" * 16)
        assert released == frozenset()

    def test_clear(self):
        tracker = ActiveModeTracker()
        tracker.register("d1", b"\x01" * 16, frozenset({"x"}))
        tracker.clear()
        released = tracker.unregister("d1", b"\x01" * 16)
        assert released == frozenset()

    def test_multi_device(self):
        """Each device tracks modes independently."""
        tracker = ActiveModeTracker()
        tracker.register("d1", b"\x01" * 16, frozenset({"x"}))
        tracker.register("d2", b"\x01" * 16, frozenset({"y"}))
        r1 = tracker.unregister("d1", b"\x01" * 16)
        r2 = tracker.unregister("d2", b"\x01" * 16)
        assert r1 == frozenset({"x"})
        assert r2 == frozenset({"y"})
