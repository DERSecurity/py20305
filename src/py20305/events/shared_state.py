"""Event state that a client process shares so another process can take over its devices.

A host application that runs several client processes as one client moves
EndDevices from a process that stops to one that carries on. The process that
takes a device over needs what the first process knew about it, or it repeats
or drops work the server and the device have already seen:

- the events the device is under, with their state, their randomized start
  and duration, whether the control was applied to the device or rejected,
  and whether a higher-priority event superseded it there;
- the Responses already posted for the device, so none is posted twice;
- which DefaultDERControl was last applied to the device, so it is not
  written again;
- the loss-of-communications state: whether events were opted out, and the
  boundary before which recovered events are not resumed.

The event processor writes this to an :class:`EventStateStore` as it works,
one snapshot per EndDevice, and records each Response as it is posted. A
process taking devices over loads their snapshots (see
:meth:`CsipClient.take_over_devices`).

The store also holds the randomization offsets of each event on each
EndDevice, drawn from its randomizeStart and randomizeDuration. Each device
draws its own (IEEE 2030.5-2023 §10.2.3.2.2 and §10.2.3.2.3), and a device
moved between processes keeps the offsets it drew.

No store is the default: the processor keeps all of this in memory only, as
before. :class:`MemoryEventStateStore` keeps it in a dict, for tests and for
several clients in one process.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = ["SNAPSHOT_VERSION", "EventStateStore", "MemoryEventStateStore"]

#: The version of the device snapshot format. A snapshot of another version is ignored.
SNAPSHOT_VERSION = 1


@runtime_checkable
class EventStateStore(Protocol):
    """Shared event state, by EndDevice, plus the randomization offsets of each event.

    The processor calls these methods from its event loop and does not await
    them, so each must return quickly. Values are JSON types, so a store can
    write them to a database as they are. mRIDs and LFDIs are hex strings.

    Concurrency expectations:

    - One EndDevice is acted for by one process at a time, so its snapshot has
      one writer. The host moves a device by releasing it in one process
      before taking it over in another.
    - ``claim_offsets`` is compare-and-set per event and device: the first
      offsets stored for that pair win, and every later call returns them. It
      must be atomic across processes.
    - ``record_response`` only appends.
    """

    def load_device(self, device_href: str) -> dict[str, Any] | None:
        """Return the last snapshot saved for the EndDevice at ``device_href``, or None."""
        ...

    def save_device(self, device_href: str, snapshot: dict[str, Any]) -> None:
        """Replace the snapshot of the EndDevice at ``device_href``."""
        ...

    def record_response(self, lfdi: str, mrid: str, status: int, at: int) -> None:
        """Record that a Response with ``status`` for event ``mrid`` was posted for ``lfdi``."""
        ...

    def responses(self, lfdi: str) -> list[tuple[str, int, int]]:
        """Return ``(mrid, status, at)`` for each Response recorded for ``lfdi``."""
        ...

    def claim_offsets(self, mrid: str, device: str, offsets: tuple[int, int]) -> tuple[int, int]:
        """Return the stored ``(start, duration)`` offsets of event ``mrid`` on ``device``.

        Stores ``offsets`` first when that event and device have none.
        ``device`` is the EndDevice href, or the empty string for an event
        whose program has no device mapping.
        """
        ...

    def forget_offsets(self, mrid: str, device: str) -> None:
        """Drop the offsets of event ``mrid`` on ``device``, which have ended."""
        ...


class MemoryEventStateStore:
    """Shared event state in dicts, in this process.

    Responses older than ``response_max_age`` seconds, counted back from the
    newest recorded for the same LFDI, are dropped as new ones arrive. The
    processor keeps a Response in memory for two hours for the same purpose.
    """

    def __init__(self, *, response_max_age: int = 7200) -> None:
        self._devices: dict[str, dict[str, Any]] = {}
        self._responses: dict[str, list[tuple[str, int, int]]] = {}
        self._offsets: dict[tuple[str, str], tuple[int, int]] = {}
        self._response_max_age = response_max_age

    def load_device(self, device_href: str) -> dict[str, Any] | None:
        """Return the last snapshot saved for ``device_href``, or None."""
        return self._devices.get(device_href)

    def save_device(self, device_href: str, snapshot: dict[str, Any]) -> None:
        """Replace the snapshot of ``device_href``."""
        self._devices[device_href] = snapshot

    def record_response(self, lfdi: str, mrid: str, status: int, at: int) -> None:
        """Record one posted Response and drop the ones too old to matter."""
        kept = self._responses.setdefault(lfdi, [])
        kept.append((mrid, status, at))
        cutoff = at - self._response_max_age
        kept[:] = [entry for entry in kept if entry[2] >= cutoff]

    def responses(self, lfdi: str) -> list[tuple[str, int, int]]:
        """Return the Responses recorded for ``lfdi``."""
        return list(self._responses.get(lfdi, []))

    def claim_offsets(self, mrid: str, device: str, offsets: tuple[int, int]) -> tuple[int, int]:
        """Return the stored offsets of ``mrid`` on ``device``, storing ``offsets`` if none."""
        return self._offsets.setdefault((mrid, device), offsets)

    def forget_offsets(self, mrid: str, device: str) -> None:
        """Drop the offsets of ``mrid`` on ``device``."""
        self._offsets.pop((mrid, device), None)
