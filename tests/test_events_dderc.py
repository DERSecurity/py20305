"""Tests for DdercTracker."""

from __future__ import annotations

from py20305.events.dderc import DdercTracker


class TestDdercTrackerShouldApply:
    """Tests for should_apply (used by fallback after event completion)."""

    def test_first_application_allowed(self):
        t = DdercTracker()
        assert t.should_apply(b"\x01" * 20, "/derp/1", primacy=5)

    def test_same_primacy_allowed(self):
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", b"\xaa" * 16, primacy=5)
        assert t.should_apply(b"\x01" * 20, "/derp/1", primacy=5)

    def test_higher_priority_allowed(self):
        """Lower primacy number = higher priority."""
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", b"\xaa" * 16, primacy=5)
        assert t.should_apply(b"\x01" * 20, "/derp/1", primacy=3)

    def test_lower_priority_blocked(self):
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", b"\xaa" * 16, primacy=3)
        assert not t.should_apply(b"\x01" * 20, "/derp/1", primacy=5)

    def test_different_lfdi_independent(self):
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", b"\xaa" * 16, primacy=0)
        assert t.should_apply(b"\x02" * 20, "/derp/1", primacy=5)

    def test_different_program_independent(self):
        """Fallback path treats different programs independently (no cross-program blocking)."""
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", b"\xaa" * 16, primacy=0)
        assert t.should_apply(b"\x01" * 20, "/derp/2", primacy=5)


class TestDdercTrackerShouldApplyInitial:
    """Tests for should_apply_initial (used at startup/rediscovery)."""

    MRID_A = b"\xaa" * 16
    MRID_B = b"\xbb" * 16

    def test_first_application_allowed(self):
        t = DdercTracker()
        assert t.should_apply_initial(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=5)

    def test_same_primacy_same_mrid_blocked(self):
        """Initial re-application of the same DDERC at same primacy is redundant and blocked."""
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=5)
        assert not t.should_apply_initial(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=5)

    def test_same_primacy_different_mrid_allowed(self):
        """A DDERC update on the same program (different mrid) must re-dispatch."""
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=5)
        assert t.should_apply_initial(b"\x01" * 20, "/derp/1", self.MRID_B, primacy=5)

    def test_higher_priority_allowed(self):
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=5)
        assert t.should_apply_initial(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=3)

    def test_lower_priority_blocked(self):
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=3)
        assert not t.should_apply_initial(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=5)

    def test_cross_program_lower_priority_blocked(self):
        """Lower-priority program's DDERC blocked when higher-priority already applied."""
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=0)
        assert not t.should_apply_initial(b"\x01" * 20, "/derp/2", self.MRID_B, primacy=5)

    def test_cross_program_higher_priority_allowed(self):
        """Higher-priority program's DDERC allowed even when lower-priority already applied."""
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=5)
        assert t.should_apply_initial(b"\x01" * 20, "/derp/2", self.MRID_B, primacy=0)

    def test_cross_program_equal_primacy_allowed(self):
        """Equal-primacy program's DDERC is not blocked cross-program."""
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=3)
        assert t.should_apply_initial(b"\x01" * 20, "/derp/2", self.MRID_B, primacy=3)

    def test_different_lfdi_independent(self):
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", self.MRID_A, primacy=0)
        assert t.should_apply_initial(b"\x02" * 20, "/derp/1", self.MRID_A, primacy=5)


class TestDdercTrackerRecordAndClear:
    def test_record_updates_entry(self):
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", b"\xaa" * 16, primacy=0)
        t.record_application(b"\x01" * 20, "/derp/1", b"\xbb" * 16, primacy=5)
        # Now primacy=5 is recorded, so primacy=10 should be blocked
        assert not t.should_apply(b"\x01" * 20, "/derp/1", primacy=10)
        # But primacy=5 should still be allowed
        assert t.should_apply(b"\x01" * 20, "/derp/1", primacy=5)

    def test_clear(self):
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", b"\xaa" * 16, primacy=0)
        assert len(t) == 1
        t.clear()
        assert len(t) == 0
        assert t.should_apply(b"\x01" * 20, "/derp/1", primacy=5)

    def test_clear_devices_removes_matching_lfdis(self):
        """clear_devices removes all entries for the given LFDIs across programs."""
        t = DdercTracker()
        lfdi_a = b"\x01" * 20
        lfdi_b = b"\x02" * 20
        t.record_application(lfdi_a, "/derp/1", b"\xaa" * 16, primacy=0)
        t.record_application(lfdi_a, "/derp/2", b"\xbb" * 16, primacy=3)
        t.record_application(lfdi_b, "/derp/1", b"\xcc" * 16, primacy=0)
        assert len(t) == 3

        t.clear_devices({lfdi_a})

        assert len(t) == 1
        # lfdi_a entries cleared — should_apply_initial allows re-application
        assert t.should_apply_initial(lfdi_a, "/derp/1", b"\xaa" * 16, primacy=0)
        assert t.should_apply_initial(lfdi_a, "/derp/2", b"\xbb" * 16, primacy=3)
        # lfdi_b entry still intact — same-primacy blocked when mrid matches
        assert not t.should_apply_initial(lfdi_b, "/derp/1", b"\xcc" * 16, primacy=0)

    def test_clear_devices_no_match_is_noop(self):
        t = DdercTracker()
        t.record_application(b"\x01" * 20, "/derp/1", b"\xaa" * 16, primacy=0)
        t.clear_devices({b"\xff" * 20})
        assert len(t) == 1

    def test_clear_devices_multiple_lfdis(self):
        t = DdercTracker()
        lfdi_a = b"\x01" * 20
        lfdi_b = b"\x02" * 20
        lfdi_c = b"\x03" * 20
        t.record_application(lfdi_a, "/derp/1", b"\xaa" * 16, primacy=0)
        t.record_application(lfdi_b, "/derp/1", b"\xbb" * 16, primacy=0)
        t.record_application(lfdi_c, "/derp/1", b"\xcc" * 16, primacy=0)

        t.clear_devices({lfdi_a, lfdi_b})

        assert len(t) == 1
        assert t.should_apply_initial(lfdi_a, "/derp/1", b"\xaa" * 16, primacy=0)
        assert t.should_apply_initial(lfdi_b, "/derp/1", b"\xbb" * 16, primacy=0)
        assert not t.should_apply_initial(lfdi_c, "/derp/1", b"\xcc" * 16, primacy=0)
