"""Shared loss-of-communications (comms-loss) mode state.

A single mutable object read by both ``CsipClient`` (which owns the time-based
detector) and ``EventProcessor`` (which gates control application).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommsLossState:
    """Cross-component comms-loss flag and resume boundary.

    ``active`` is set once upstream silence has lasted the configured window and
    cleared on the first restored contact. ``resume_after_epoch`` is the latest
    end time (epoch seconds) of any event opted out during the outage; on
    recovery the client skips re-polled events at or before this boundary and
    resumes only the schedule that follows it.
    """

    active: bool = False
    resume_after_epoch: int | None = None
