"""Tests for CurrentControlsTracker."""

from __future__ import annotations

from py20305.events.current_controls import CurrentControlsTracker


class TestCurrentControlsTracker:
    def test_update_tracks_modes(self):
        tracker = CurrentControlsTracker()
        tracker.update("d1", [("update_qv", {"qv_mode_enable": 1})])
        modes = tracker.get_active_modes("d1")
        assert "update_qv" in modes
        assert modes["update_qv"]["qv_mode_enable"] == 1

    def test_update_overwrites_existing(self):
        tracker = CurrentControlsTracker()
        tracker.update("d1", [("update_qv", {"qv_mode_enable": 1, "qv_vref": 100})])
        tracker.update("d1", [("update_qv", {"qv_mode_enable": 1, "qv_vref": 200})])
        modes = tracker.get_active_modes("d1")
        assert modes["update_qv"]["qv_vref"] == 200

    def test_clear_device(self):
        tracker = CurrentControlsTracker()
        tracker.update("d1", [("update_qv", {"qv_mode_enable": 1})])
        tracker.clear_device("d1")
        assert not tracker.has_changes("d1")

    def test_clear_all(self):
        tracker = CurrentControlsTracker()
        tracker.update("d1", [("update_qv", {"qv_mode_enable": 1})])
        tracker.update("d2", [("update_pv", {"pv_mode_enable": 1})])
        tracker.clear()
        assert not tracker.has_changes("d1")
        assert not tracker.has_changes("d2")

    def test_has_changes(self):
        tracker = CurrentControlsTracker()
        assert not tracker.has_changes("d1")
        tracker.update("d1", [("update_qv", {"qv_mode_enable": 1})])
        assert tracker.has_changes("d1")

    def test_get_active_modes_unknown_device(self):
        tracker = CurrentControlsTracker()
        assert tracker.get_active_modes("unknown") == {}
