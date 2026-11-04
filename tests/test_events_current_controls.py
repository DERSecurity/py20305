"""Tests for CurrentControlsTracker."""

from __future__ import annotations

from py20305.events.current_controls import CurrentControlsTracker


class TestCurrentControlsTracker:
    def test_update_tracks_modes(self):
        tracker = CurrentControlsTracker()
        tracker.update("d1", [("update_op_mod_volt_var", {"enabled": True})])
        modes = tracker.get_active_modes("d1")
        assert "update_op_mod_volt_var" in modes
        assert modes["update_op_mod_volt_var"]["enabled"] is True

    def test_update_overwrites_existing(self):
        tracker = CurrentControlsTracker()
        tracker.update("d1", [("update_op_mod_volt_var", {"enabled": True, "value": 100})])
        tracker.update("d1", [("update_op_mod_volt_var", {"enabled": True, "value": 200})])
        modes = tracker.get_active_modes("d1")
        assert modes["update_op_mod_volt_var"]["value"] == 200

    def test_clear_device(self):
        tracker = CurrentControlsTracker()
        tracker.update("d1", [("update_op_mod_volt_var", {"enabled": True})])
        tracker.clear_device("d1")
        assert not tracker.has_changes("d1")

    def test_clear_all(self):
        tracker = CurrentControlsTracker()
        tracker.update("d1", [("update_op_mod_volt_var", {"enabled": True})])
        tracker.update("d2", [("update_op_mod_volt_watt", {"enabled": True})])
        tracker.clear()
        assert not tracker.has_changes("d1")
        assert not tracker.has_changes("d2")

    def test_has_changes(self):
        tracker = CurrentControlsTracker()
        assert not tracker.has_changes("d1")
        tracker.update("d1", [("update_op_mod_volt_var", {"enabled": True})])
        assert tracker.has_changes("d1")

    def test_get_active_modes_unknown_device(self):
        tracker = CurrentControlsTracker()
        assert tracker.get_active_modes("unknown") == {}
