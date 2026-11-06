"""Tests for the supersession algorithm."""

from __future__ import annotations

from py20305.events.state_machine import EventRecord, EventState
from py20305.events.supersession import compute_supersession
from py20305.models.sep.sep import (
    DateTimeInterval,
    Dercontrol1,
    DercontrolBase,
    DerunitRefType,
    EventStatus,
    FixedVarControlType,
    MRidtype,
    SignedPerCent,
    TimeType,
)


def _make_derc(
    mrid_byte: int,
    start: int,
    duration: int,
    status_time: int = 1000,
    current_status: int = 0,
    fixed_var: bool = False,
) -> Dercontrol1:
    base = DercontrolBase()
    if fixed_var:
        base = DercontrolBase(
            op_mod_fixed_var=FixedVarControlType(
                ref_type=DerunitRefType(value=0),
                value=SignedPerCent(value=50),
            )
        )
    return Dercontrol1(
        m_rid=MRidtype(value=bytes([mrid_byte]) * 16),
        creation_time=TimeType(value=900),
        event_status=EventStatus(
            current_status=current_status,
            date_time=TimeType(value=status_time),
            potentially_superseded=False,
        ),
        interval=DateTimeInterval(duration=duration, start=TimeType(value=start)),
        dercontrol_base=base,
    )


def _make_record(
    mrid_byte: int,
    start: int = 1000,
    duration: int = 3600,
    primacy: int = 0,
    status_time: int = 1000,
    program_href: str = "/derp/1",
    current_status: int = 0,
    fixed_var: bool = False,
    creation_time: int = 900,
) -> EventRecord:
    return EventRecord(
        mrid=bytes([mrid_byte]) * 16,
        derc=_make_derc(mrid_byte, start, duration, status_time, current_status, fixed_var),
        program_href=program_href,
        primacy=primacy,
        state=EventState.ACTIVE,
        start=start,
        duration=duration,
        server_status_time=status_time,
        creation_time=creation_time,
    )


def _device_map(*pairs: tuple[str, set[str]]) -> dict[str, set[str]]:
    return dict(pairs)


class TestSupersessionBasic:
    def test_no_events(self):
        assert compute_supersession([], {}) == []

    def test_single_event_no_supersession(self):
        r = _make_record(0x01)
        assert compute_supersession([r], {"/derp/1": {"d1"}}) == []

    def test_non_overlapping_no_supersession(self):
        r1 = _make_record(0x01, start=1000, duration=100)
        r2 = _make_record(0x02, start=2000, duration=100)
        devices = _device_map(("/derp/1", {"d1"}))
        assert compute_supersession([r1, r2], devices) == []

    def test_different_params_no_supersession(self):
        # One has fixed_var, other has default (empty) params
        r1 = _make_record(0x01, fixed_var=True)
        r2 = _make_record(0x02, fixed_var=False)
        devices = _device_map(("/derp/1", {"d1"}))
        assert compute_supersession([r1, r2], devices) == []


class TestSupersessionPrimacy:
    def test_lower_primacy_wins(self):
        r1 = _make_record(0x01, primacy=0)
        r2 = _make_record(0x02, primacy=5)
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 1
        assert results[0].superseding_mrid == r1.mrid
        assert results[0].superseded_mrid == r2.mrid

    def test_same_primacy_newer_creation_time_wins(self):
        """IEEE 10.2.2.3 rule e): creationTime determines winner at same primacy."""
        r1 = _make_record(0x01, primacy=0, status_time=1000, creation_time=500)
        r2 = _make_record(0x02, primacy=0, status_time=2000, creation_time=600)
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 1
        # r2 has newer creationTime (600 > 500), so r2 wins
        assert results[0].superseding_mrid == r2.mrid
        assert results[0].superseded_mrid == r1.mrid

    def test_same_primacy_same_creation_time_first_wins(self):
        r1 = _make_record(0x01, primacy=0, creation_time=500)
        r2 = _make_record(0x02, primacy=0, creation_time=500)
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 1
        # r1 >= r2 in creation_time so r1 is winner
        assert results[0].superseding_mrid == r1.mrid

    def test_same_primacy_falls_back_to_status_time_when_no_creation_time(self):
        """When creationTime is 0, fall back to server_status_time."""
        r1 = _make_record(0x01, primacy=0, status_time=1000, creation_time=0)
        r2 = _make_record(0x02, primacy=0, status_time=2000, creation_time=0)
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 1
        assert results[0].superseding_mrid == r2.mrid
        assert results[0].superseded_mrid == r1.mrid

    def test_creation_time_overrides_status_time(self):
        """creationTime takes precedence even when status_time disagrees."""
        r1 = _make_record(0x01, primacy=0, status_time=9999, creation_time=100)
        r2 = _make_record(0x02, primacy=0, status_time=1, creation_time=200)
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 1
        # r2 has newer creation_time (200 > 100) despite older status_time
        assert results[0].superseding_mrid == r2.mrid


class TestSupersessionThreeEvents:
    def test_three_overlapping_returns_all_pairs(self):
        """Fix: original only returned the last pair. We return all."""
        r1 = _make_record(0x01, primacy=0)
        r2 = _make_record(0x02, primacy=5)
        r3 = _make_record(0x03, primacy=10)
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2, r3], devices)
        # 3 pairs: (r1,r2), (r1,r3), (r2,r3)
        assert len(results) == 3
        superseded_mrids = {r.superseded_mrid for r in results}
        assert r2.mrid in superseded_mrids
        assert r3.mrid in superseded_mrids


class TestSupersessionStatus4:
    def test_status4_does_not_affect_supersession(self):
        """Server currentStatus=4 is ignored — aggregator computes supersession independently.

        The aggregator handles per-device supersession which the server's global
        currentStatus cannot represent.  All events participate regardless of
        their server-side status.
        """
        r1 = _make_record(0x01, primacy=0)
        r2 = _make_record(0x02, primacy=5, current_status=4)
        r3 = _make_record(0x03, primacy=10)
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2, r3], devices)
        # All 3 pairs compared: r1 supersedes r2, r1 supersedes r3, r2 supersedes r3
        assert len(results) == 3
        superseded_mrids = {r.superseded_mrid for r in results}
        assert r2.mrid in superseded_mrids
        assert r3.mrid in superseded_mrids


class TestSupersessionDevices:
    def test_device_intersection(self):
        r1 = _make_record(0x01, primacy=0, program_href="/derp/1")
        r2 = _make_record(0x02, primacy=5, program_href="/derp/2")
        devices = _device_map(
            ("/derp/1", {"d1", "d2", "d3"}),
            ("/derp/2", {"d2", "d3", "d4"}),
        )
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 1
        assert results[0].affected_devices == frozenset({"d2", "d3"})

    def test_no_device_overlap_no_result(self):
        r1 = _make_record(0x01, primacy=0, program_href="/derp/1")
        r2 = _make_record(0x02, primacy=5, program_href="/derp/2")
        devices = _device_map(
            ("/derp/1", {"d1"}),
            ("/derp/2", {"d2"}),
        )
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 0

    def test_missing_program_in_device_map(self):
        r1 = _make_record(0x01, primacy=0, program_href="/derp/1")
        r2 = _make_record(0x02, primacy=5, program_href="/derp/unknown")
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2], devices)
        # No intersection with empty set
        assert len(results) == 0


class TestTimeOverlap:
    def test_successive_events_do_not_overlap(self):
        """CSIP EVENT.021: Successive Events share an endpoint and SHALL NOT
        be treated as overlapping. The earlier event's Effective End Time is
        the later event's Effective Start Time by spec.
        """
        # r1 = [1000, 1100), r2 = [1100, 1200) — touch at t=1100, no overlap
        r1 = _make_record(0x01, start=1000, duration=100, primacy=0)
        r2 = _make_record(0x02, start=1100, duration=100, primacy=5)
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2], devices)
        assert results == []

    def test_successive_events_do_not_supersede_at_same_primacy(self):
        """CSIP EVENT.021 regression: when two events from the same program
        are back-to-back (eff_end_A == eff_start_B), the newer event must
        NOT supersede the earlier one. Without the half-open fix, same-
        primacy tie-breaking would mark the earlier event SUPERSEDED at
        exactly its natural end instead of letting it complete cleanly.
        """
        r1 = _make_record(
            0x01, start=1000, duration=100, primacy=0, creation_time=500, fixed_var=True
        )
        r2 = _make_record(
            0x02, start=1100, duration=100, primacy=0, creation_time=600, fixed_var=True
        )
        devices = _device_map(("/derp/1", {"d1"}))
        assert compute_supersession([r1, r2], devices) == []

    def test_overlapping_modes_supersede(self):
        """WP2e: Events with overlapping (not identical) modes should supersede."""
        r1 = _make_record(0x01, primacy=0, fixed_var=True)
        r2 = _make_record(0x02, primacy=5, fixed_var=True)
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 1
        assert results[0].affected_modes  # should contain the overlapping mode

    def test_disjoint_modes_no_supersession(self):
        """WP2e: Events with disjoint modes should not supersede."""
        r1 = _make_record(0x01, fixed_var=True)  # has op_mod_fixed_var
        r2 = _make_record(0x02, fixed_var=False)  # has no modes (empty base)
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 0

    def test_cross_program_supersession_has_program_hrefs(self):
        """WP2d: SupersessionResult should track program hrefs."""
        r1 = _make_record(0x01, primacy=0, program_href="/derp/1")
        r2 = _make_record(0x02, primacy=5, program_href="/derp/2")
        devices = _device_map(
            ("/derp/1", {"d1"}),
            ("/derp/2", {"d1"}),
        )
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 1
        assert results[0].superseding_program_href == "/derp/1"
        assert results[0].superseded_program_href == "/derp/2"

    def test_same_program_supersession_hrefs(self):
        """WP2d: Same-program supersession should have equal hrefs."""
        r1 = _make_record(0x01, primacy=0, program_href="/derp/1")
        r2 = _make_record(0x02, primacy=5, program_href="/derp/1")
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 1
        assert results[0].superseding_program_href == results[0].superseded_program_href

    def test_just_before_no_overlap(self):
        r1 = _make_record(0x01, start=1000, duration=99, primacy=0)
        r2 = _make_record(0x02, start=1100, duration=100, primacy=5)
        devices = _device_map(("/derp/1", {"d1"}))
        results = compute_supersession([r1, r2], devices)
        assert len(results) == 0


def _reference_supersession(events, program_to_devices):
    """Every pair compared, as compute_supersession did before it grouped events by device.

    Kept as the definition the faster grouping is held to: the same results, in
    the same order.
    """
    from py20305.events.supersession import (
        SupersessionResult,
        _time_overlaps,
        compared_mode_keys,
    )

    results = []
    checked = set()
    for i, a in enumerate(events):
        for b in events[i + 1 :]:
            pair_key = (min(a.mrid, b.mrid), max(a.mrid, b.mrid))
            if pair_key in checked:
                continue
            checked.add(pair_key)
            if not _time_overlaps(a, b):
                continue
            params_a = compared_mode_keys(a.derc.dercontrol_base)
            params_b = compared_mode_keys(b.derc.dercontrol_base)
            overlap = params_a & params_b
            if params_a != params_b and not overlap:
                continue
            if a.primacy < b.primacy:
                winner, loser = a, b
            elif b.primacy < a.primacy:
                winner, loser = b, a
            else:
                a_time = a.creation_time or a.server_status_time
                b_time = b.creation_time or b.server_status_time
                winner, loser = (a, b) if a_time >= b_time else (b, a)
            affected = frozenset(
                program_to_devices.get(winner.program_href, set())
                & program_to_devices.get(loser.program_href, set())
            )
            if affected:
                results.append(
                    SupersessionResult(
                        superseded_mrid=loser.mrid,
                        superseding_mrid=winner.mrid,
                        affected_devices=affected,
                        superseded_program_href=loser.program_href,
                        superseding_program_href=winner.program_href,
                        affected_modes=overlap,
                    )
                )
    return results


_MODES = (
    {},
    {"op_mod_grid_connect_permit": True},
    {"op_mod_island_permit": True},
    {"op_mod_grid_connect_permit": True, "op_mod_island_permit": False},
    {
        "op_mod_fixed_var": FixedVarControlType(
            ref_type=DerunitRefType(value=0), value=SignedPerCent(value=50)
        )
    },
)


def _random_events(seed: int, count: int, programs: int):
    import random

    rng = random.Random(seed)
    events = []
    for n in range(count):
        record = _make_record(
            n % 256,
            start=rng.choice((1000, 1500, 3000, 4600)),
            duration=rng.choice((600, 1800, 3600)),
            primacy=rng.choice((0, 1, 2)),
            program_href=f"/derp/{rng.randrange(programs)}",
            creation_time=rng.choice((900, 950, 0)),
            status_time=rng.choice((1000, 1100)),
        )
        mrid = n.to_bytes(16, "big")
        record.mrid = mrid
        record.derc = record.derc.model_copy(
            update={"dercontrol_base": DercontrolBase(**rng.choice(_MODES))}
        )
        events.append(record)
    return events


def _random_devices(seed: int, programs: int, devices: int) -> dict[str, set[str]]:
    import random

    rng = random.Random(seed + 1)
    mapping = {}
    for p in range(programs):
        if rng.random() < 0.1:
            continue  # a program with no device known
        mapping[f"/derp/{p}"] = {
            f"/edev/{rng.randrange(devices)}" for _ in range(rng.choice((1, 1, 2, 3)))
        }
    return mapping


class TestSupersessionGroupedByDevice:
    """Only events whose programs reach a common device are compared, with the same results."""

    def test_the_results_and_their_order_are_those_of_comparing_every_pair(self):
        for seed in range(40):
            programs = 3 + seed % 7
            events = _random_events(seed, 12 + seed % 20, programs)
            devices = _random_devices(seed, programs, 2 + seed % 5)

            assert compute_supersession(events, devices) == _reference_supersession(
                events, devices
            ), f"seed {seed}"

    def test_events_of_programs_with_no_common_device_are_not_compared(self, monkeypatch):
        from py20305.events import supersession

        events = []
        for n in range(2000):
            record = _make_record(0, program_href=f"/derp/{n // 2}", fixed_var=True)
            record.mrid = n.to_bytes(16, "big")
            events.append(record)
        devices = {f"/derp/{p}": {f"/edev/{p}"} for p in range(1000)}
        visited = _count_calls(monkeypatch, supersession, "_time_overlaps")

        results = compute_supersession(events, devices)

        # Each program's two events overlap with the same modes: one pair each,
        # and no pair of events from two programs is looked at.
        assert len(results) == 1000
        assert len(visited) == 1000

    def test_each_events_modes_are_worked_out_once(self, monkeypatch):
        from py20305.events import supersession

        events = []
        for n in range(3):
            record = _make_record(0, primacy=n, fixed_var=True)
            record.mrid = n.to_bytes(16, "big")
            events.append(record)
        modes = _count_calls(monkeypatch, supersession, "compared_mode_keys")

        results = compute_supersession(events, {"/derp/1": {"/edev/1"}})

        # Three pairs, each needing both events' modes: three events, three calls.
        assert len(results) == 3
        assert len(modes) == 3

    def test_a_program_reaching_many_devices_compares_each_pair_once(self, monkeypatch):
        from py20305.events import supersession

        events = []
        for n in range(240):
            record = _make_record(0, start=1000 + n, fixed_var=True)
            record.mrid = n.to_bytes(16, "big")
            events.append(record)
        devices = {"/derp/1": {f"/edev/{d}" for d in range(200)}}
        visited = _count_calls(monkeypatch, supersession, "_time_overlaps")

        results = compute_supersession(events, devices)

        assert len(results) == 240 * 239 // 2
        assert len(visited) == 240 * 239 // 2
        assert all(len(result.affected_devices) == 200 for result in results)


def _count_calls(monkeypatch, module, name: str) -> list[tuple]:
    """Replace ``module.name`` with a wrapper recording each call's arguments."""
    calls: list[tuple] = []
    original = getattr(module, name)

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(module, name, counted)
    return calls
