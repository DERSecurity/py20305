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


class TestRelease:
    def test_release_removes_only_the_named_modes(self):
        tracker = ActiveModeTracker()
        tracker.register("d1", b"\x01" * 16, frozenset({"x", "y"}))

        tracker.release("d1", b"\x01" * 16, frozenset({"x"}))

        assert tracker.covered("d1") == frozenset({"y"})
        assert tracker.unregister("d1", b"\x01" * 16) == frozenset({"y"})

    def test_release_of_every_mode_drops_the_registration(self):
        tracker = ActiveModeTracker()
        tracker.register("d1", b"\x01" * 16, frozenset({"x"}))

        tracker.release("d1", b"\x01" * 16, frozenset({"x", "unregistered"}))

        assert tracker.covered("d1") == frozenset()
        assert tracker.unregister("d1", b"\x01" * 16) == frozenset()

    def test_a_released_mode_is_released_by_the_other_event_when_it_ends(self):
        """Event A: X. Event B: X. A releases X, so B's end releases it."""
        tracker = ActiveModeTracker()
        tracker.register("d1", b"\x01" * 16, frozenset({"x"}))
        tracker.register("d1", b"\x02" * 16, frozenset({"x"}))

        tracker.release("d1", b"\x01" * 16, frozenset({"x"}))

        assert tracker.unregister("d1", b"\x02" * 16) == frozenset({"x"})

    def test_release_leaves_other_events_and_devices_alone(self):
        tracker = ActiveModeTracker()
        tracker.register("d1", b"\x01" * 16, frozenset({"x"}))
        tracker.register("d1", b"\x02" * 16, frozenset({"x"}))
        tracker.register("d2", b"\x01" * 16, frozenset({"x"}))

        tracker.release("d1", b"\x01" * 16, frozenset({"x"}))

        assert tracker.covered_among("d1", frozenset({b"\x02" * 16})) == frozenset({"x"})
        assert tracker.covered_among("d2", frozenset({b"\x01" * 16})) == frozenset({"x"})

    def test_release_for_an_unknown_device_or_event_does_nothing(self):
        tracker = ActiveModeTracker()
        tracker.register("d1", b"\x01" * 16, frozenset({"x"}))

        tracker.release("unknown", b"\x01" * 16, frozenset({"x"}))
        tracker.release("d1", b"\x99" * 16, frozenset({"x"}))

        assert tracker.covered("d1") == frozenset({"x"})


def test_devices_of_lists_the_devices_an_event_is_registered_on():
    tracker = ActiveModeTracker()
    tracker.register("/edev/1", b"" * 16, frozenset({"a"}))
    tracker.register("/edev/2", b"" * 16, frozenset({"a"}))
    tracker.register("/edev/3", b"" * 16, frozenset({"a"}))

    assert tracker.devices_of(b"" * 16) == {"/edev/1", "/edev/2"}
    tracker.unregister("/edev/2", b"" * 16)
    assert tracker.devices_of(b"" * 16) == {"/edev/1"}
    assert tracker.devices_of(b"	" * 16) == frozenset()
