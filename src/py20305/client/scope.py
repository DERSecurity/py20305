"""Which EndDevices one client process acts for.

A host application that runs several processes as one client splits the
EndDevices the certificate can see between them. Each process's client takes a
device filter: discovery walks the FunctionSetAssignments, DERPrograms and
DERControls of only the EndDevices the filter accepts, so the process builds
event state for, dispatches to and responds for only those devices.

A filter is any callable that takes an EndDevice's href and its LFDI as 40
lowercase hex characters (empty when the server gives none) and returns
whether this process acts for it. :class:`DeviceSet` is the ready-made one: a
set of hrefs and LFDIs the host adds to and removes from as it moves devices
between processes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

__all__ = ["DeviceFilter", "DeviceSet"]

#: ``(end_device_href, lfdi_hex) -> acts for it``.
DeviceFilter = Callable[[str, str], bool]


class DeviceSet:
    """A device filter that accepts the EndDevices named by href or by LFDI.

    An EndDevice is accepted when its href or its LFDI is in the set. LFDIs are
    compared without regard to case.
    """

    def __init__(self, *, hrefs: Iterable[str] = (), lfdis: Iterable[str] = ()) -> None:
        self.hrefs: set[str] = set(hrefs)
        self.lfdis: set[str] = {lfdi.lower() for lfdi in lfdis}

    def __call__(self, href: str, lfdi: str) -> bool:
        """Return whether the EndDevice at ``href`` with ``lfdi`` is in the set."""
        return href in self.hrefs or (bool(lfdi) and lfdi.lower() in self.lfdis)

    def add(self, *, hrefs: Iterable[str] = (), lfdis: Iterable[str] = ()) -> None:
        """Accept these EndDevices as well."""
        self.hrefs.update(hrefs)
        self.lfdis.update(lfdi.lower() for lfdi in lfdis)

    def discard(self, *, hrefs: Iterable[str] = (), lfdis: Iterable[str] = ()) -> None:
        """Stop accepting these EndDevices. Unknown entries are ignored."""
        self.hrefs.difference_update(hrefs)
        self.lfdis.difference_update(lfdi.lower() for lfdi in lfdis)
