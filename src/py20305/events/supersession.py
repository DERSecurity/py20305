"""Pure-function supersession algorithm for DERControl events.

Compares the pairs of active/scheduled events whose programs reach a common
device. If two events have
overlapping time ranges and identical DERControlBase parameters
(excluding opModConnect/opModEnergize), the lower-primacy event
supersedes the higher-primacy one. Same primacy: newer creationTime wins
(IEEE 10.2.2.3 rule e), with server_status_time as fallback.

Fixes from original:
- Returns ALL superseded pairs (original only returned the last pair)
- Status=4 is handled per-event, not by clearing all results
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from py20305.events.state_machine import EventRecord
from py20305.models.csipaus.elements import doe_limits
from py20305.models.sep.sep import DercontrolBase

_EXCLUDED_FIELDS = frozenset({"op_mod_connect", "op_mod_energize"})


@dataclass(frozen=True)
class SupersessionResult:
    """One supersession relationship: superseding event beats superseded event."""

    superseded_mrid: bytes
    superseding_mrid: bytes
    affected_devices: frozenset[str]
    superseded_program_href: str = ""
    superseding_program_href: str = ""
    affected_modes: frozenset[str] = frozenset()


def compared_mode_keys(base: DercontrolBase) -> frozenset[str]:
    """Return the modes supersession compares, as it compares them.

    ``DERControlBase`` field names, leaving out connect and energize, plus the
    CSIP-AUS envelope limits by element name (``opModExpLimW``, ``opModImpLimW``,
    ``opModGenLimW``, ``opModLoadLimW``). The limits are extension elements
    carried in ``other_element``, not fields, and they are modes of the
    DERControlBase: CSIP-AUS v1.1a section 5.2.4.1 has DERControls follow the
    event rules of IEEE 2030.5, and IEEE 2030.5-2023 section 10.2.2.3 rule q
    (rule t in IEEE 2030.5-2018 section 10.2.3.3) supersedes overlapping events
    mode by mode. An event with none of those elements has no CSIP-AUS modes to
    compare, so servers that do not use them see no change.

    Narrower than :func:`~py20305.events.modes_bitmask.get_active_mode_names`,
    which also names connect and energize.
    Anything deciding what a supersession covered has to ask in this
    vocabulary: ``superseded_modes`` is recorded from it, so comparing it
    against the wider set says a device was only partly taken over whenever an
    event carries a mode this cannot see.
    """
    keys: set[str] = set(doe_limits(base))
    for name in DercontrolBase.model_fields:
        if name in _EXCLUDED_FIELDS:
            continue
        if name.startswith(("dercontrol_base_", "other_element", "any_attributes")):
            continue
        val = getattr(base, name, None)
        if val is not None:
            keys.add(name)
    return frozenset(keys)


def _time_overlaps(a: EventRecord, b: EventRecord) -> bool:
    """Check if two events overlap in time.

    Intervals are treated as half-open: [start, end). EventRecord.start
    and .end are the post-randomization (effective) values per
    state_machine.py and .end is exclusive, so successive events
    (earlier eff_end == later eff_start) do NOT overlap. This matches
    CSIP EVENT.021 and the processor's own successive-predecessor
    detection in _find_successive_predecessor.

    Zero-duration events never reach this predicate: the processor
    discards eff_end < now at intake (processor.py: EXPIRED response,
    never stored).
    """
    return a.start < b.end and b.start < a.end


def compute_supersession(
    events: list[EventRecord],
    program_to_devices: dict[str, set[str]],
) -> list[SupersessionResult]:
    """Compute all supersession pairs among the given events.

    Only events whose programs reach a common device can supersede one
    another. Each event is compared with the later events of the programs it
    shares a device with, in the order of *events*, so each such pair is
    compared once and the results, and their order, are those of comparing
    every pair. Each event's compared modes are worked out once.

    Args:
        events: List of active/scheduled EventRecords to compare.
        program_to_devices: Mapping from program_href to set of device hrefs
            belonging to that program's FSA group.

    Returns:
        List of SupersessionResult for each superseded pair.
    """
    programs_on: dict[str, set[str]] = {}
    for program, devices in program_to_devices.items():
        for device in devices:
            programs_on.setdefault(device, set()).add(program)
    sharing: dict[str, set[str]] = {}

    def programs_sharing(program: str) -> set[str]:
        """The programs that reach a device *program* reaches, itself included."""
        if program not in sharing:
            reach: set[str] = set()
            for device in program_to_devices.get(program, ()):
                reach |= programs_on[device]
            sharing[program] = reach
        return sharing[program]

    indices_of: dict[str, list[int]] = {}
    for index, event in enumerate(events):
        indices_of.setdefault(event.program_href, []).append(index)

    modes: dict[int, frozenset[str]] = {}

    def modes_of(index: int) -> frozenset[str]:
        if index not in modes:
            modes[index] = compared_mode_keys(events[index].derc.dercontrol_base)
        return modes[index]

    results: list[SupersessionResult] = []
    checked: set[tuple[bytes, bytes]] = set()

    for i, a in enumerate(events):
        later: list[int] = []
        for program in programs_sharing(a.program_href):
            indices = indices_of.get(program, [])
            later.extend(indices[bisect.bisect_right(indices, i) :])
        later.sort()
        for j in later:
            b = events[j]
            pair_key = (min(a.mrid, b.mrid), max(a.mrid, b.mrid))
            if pair_key in checked:
                continue
            checked.add(pair_key)

            if not _time_overlaps(a, b):
                continue

            # Per-mode supersession: intersecting modes supersede independently.
            # Exact match (including both-empty) or non-empty overlap triggers supersession.
            params_a = modes_of(i)
            params_b = modes_of(j)
            overlap = params_a & params_b
            if params_a != params_b and not overlap:
                continue

            # Determine winner: lower primacy number wins;
            # same primacy: newer creationTime wins (IEEE 10.2.2.3 rule e)
            if a.primacy < b.primacy:
                winner, loser = a, b
            elif b.primacy < a.primacy:
                winner, loser = b, a
            else:
                # Use creation_time (preferred per IEEE), fall back to
                # server_status_time for events that lack creationTime.
                a_time = a.creation_time or a.server_status_time
                b_time = b.creation_time or b.server_status_time
                if a_time >= b_time:
                    winner, loser = a, b
                else:
                    winner, loser = b, a

            # Compute affected devices: intersection of both programs' device sets
            devs_winner = program_to_devices.get(winner.program_href, set())
            devs_loser = program_to_devices.get(loser.program_href, set())
            affected = frozenset(devs_winner & devs_loser)

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
