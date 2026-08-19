"""Pure-function supersession algorithm for DERControl events.

Compares all pairs of active/scheduled events. If two events have
overlapping time ranges and identical DERControlBase parameters
(excluding opModConnect/opModEnergize), the lower-primacy event
supersedes the higher-primacy one. Same primacy: newer creationTime wins
(IEEE 10.2.2.3 rule e), with server_status_time as fallback.

Fixes from original:
- Returns ALL superseded pairs (original only returned the last pair)
- Status=4 is handled per-event, not by clearing all results
"""

from __future__ import annotations

from dataclasses import dataclass

from py20305.events.state_machine import EventRecord
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


def _extract_param_keys(base: DercontrolBase) -> frozenset[str]:
    """Return the set of non-None DERControlBase field names, excluding connect/energize."""
    keys: list[str] = []
    for name in DercontrolBase.model_fields:
        if name in _EXCLUDED_FIELDS:
            continue
        if name.startswith(("dercontrol_base_", "other_element", "any_attributes")):
            continue
        val = getattr(base, name, None)
        if val is not None:
            keys.append(name)
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

    Args:
        events: List of active/scheduled EventRecords to compare.
        program_to_devices: Mapping from program_href to set of device hrefs
            belonging to that program's FSA group.

    Returns:
        List of SupersessionResult for each superseded pair.
    """
    results: list[SupersessionResult] = []
    checked: set[tuple[bytes, bytes]] = set()

    for i, a in enumerate(events):
        for b in events[i + 1 :]:
            pair_key = (min(a.mrid, b.mrid), max(a.mrid, b.mrid))
            if pair_key in checked:
                continue
            checked.add(pair_key)

            if not _time_overlaps(a, b):
                continue

            # Per-mode supersession: intersecting modes supersede independently.
            # Exact match (including both-empty) or non-empty overlap triggers supersession.
            params_a = _extract_param_keys(a.derc.dercontrol_base)
            params_b = _extract_param_keys(b.derc.dercontrol_base)
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
