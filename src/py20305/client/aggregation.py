"""Aggregations: an EndDevice that represents several devices (IEEE 2030.5-2023 §8.7).

IEEE 2030.5-2023 §8.7.1 lets an EndDevice "represent an aggregation of multiple
assets". The server describes the aggregation on the EndDevice itself:

* ``distribution`` (AbstractDevice, AggregationDistributionType, Annex B.5 and
  B.24) "specifies how controls SHALL be distributed among members of the
  aggregation": 0 unspecified, 1 Uniform (an equal value for each member),
  2 Prorate (an equal percentage of nameplate rating), 3 Priority (each member
  of the AggregationPriority completely utilized before the next).
* The AggregatedDeviceList (``AggregatedDeviceListLink``) lists members as
  AggregatedDevice resources. It "MAY NOT represent all components of the
  aggregation" (§8.7.1). A server marks a member ``enabled`` false before it
  removes it (§8.7.3.1).
* The AggregationPriority (``AggregationPriorityLink``) lists the members'
  LFDIs, highest priority first. It SHALL be present when the distribution is
  Priority and SHALL NOT be present otherwise (Annex B.24).

Annex C.3 is the flow: the service provider sets all of this up out of band,
and the client reads its EndDevice, then its AggregatedDeviceList, then, for a
Priority distribution, its AggregationPriority. :func:`read_aggregation` does
those reads for one EndDevice and returns an :class:`AggregationInfo`, which
discovery keeps on the EndDevice's state. Sharing a control across the members
is the application's job: the client delivers a control for the aggregation's
EndDevice to that EndDevice's connector, as for any EndDevice, and reports the
event's Responses for it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from py20305.client.errors import Sep2PayloadError, Sep2ProtocolError
from py20305.client.poll_rate import normalize_poll_rate
from py20305.models.sep.sep import AggregatedDeviceList, AggregationPriority

if TYPE_CHECKING:
    from py20305.client.http import Sep2Client

logger = logging.getLogger(__name__)

#: The AggregationDistributionType value for Priority (IEEE 2030.5-2023 Annex B.24).
PRIORITY = 3


@dataclass(frozen=True)
class AggregatedDeviceInfo:
    """One AggregatedDevice of an AggregatedDeviceList (IEEE 2030.5-2023 Annex B.24).

    ``lfdi`` is lowercase hex. ``enabled`` is the resource's ``enabled``; an
    absent value is True, since a server sets it to false only to show that a
    member is no longer registered (§8.7.3.1).
    """

    lfdi: str
    sfdi: int
    enabled: bool
    href: str | None
    changed_time: int
    device_category: int | None = None


@dataclass(frozen=True)
class AggregationInfo:
    """What the server says about an EndDevice that represents an aggregation.

    ``distribution`` is the EndDevice's AggregationDistributionType value, or
    None when the EndDevice carries none (which the standard reads as 0).
    ``members`` is every AggregatedDevice the list served, in list order.
    ``priority`` holds the AggregationPriority's LFDIs, highest first; it is
    read only for a Priority distribution. ``list_poll_rate`` is the
    AggregatedDeviceList's ``pollRate`` in seconds, or None when the list was
    not read.
    """

    distribution: int | None
    members: tuple[AggregatedDeviceInfo, ...] = ()
    priority: tuple[str, ...] = ()
    aggregated_device_list_href: str | None = None
    aggregation_priority_href: str | None = None
    list_poll_rate: int | None = None

    @property
    def enabled_members(self) -> tuple[str, ...]:
        """Return the LFDIs of the members the server has not disabled."""
        return tuple(member.lfdi for member in self.members if member.enabled)

    @property
    def disabled_members(self) -> tuple[str, ...]:
        """Return the LFDIs of the members the server marked disabled (§8.7.3.1)."""
        return tuple(member.lfdi for member in self.members if not member.enabled)


def _href(link: Any) -> str | None:
    href = getattr(link, "href", None)
    return href if isinstance(href, str) and href else None


def distribution_of(end_device: Any) -> int | None:
    """Return an EndDevice's ``distribution`` value, or None when it carries none."""
    distribution = getattr(end_device, "distribution", None)
    value = getattr(distribution, "value", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def is_aggregation(end_device: Any) -> bool:
    """Return whether an EndDevice describes an aggregation.

    It does when it carries a ``distribution`` other than 0, an
    AggregatedDeviceListLink or an AggregationPriorityLink.
    """
    return bool(
        distribution_of(end_device)
        or _href(getattr(end_device, "aggregated_device_list_link", None))
        or _href(getattr(end_device, "aggregation_priority_link", None))
    )


def _member(device: Any) -> AggregatedDeviceInfo:
    category = getattr(device, "device_category", None)
    category_value = getattr(category, "value", None)
    changed = getattr(getattr(device, "changed_time", None), "value", 0)
    sfdi = getattr(getattr(device, "s_fdi", None), "value", 0)
    return AggregatedDeviceInfo(
        lfdi=bytes(device.l_fdi).hex(),
        sfdi=int(sfdi or 0),
        enabled=device.enabled is not False,
        href=getattr(device, "href", None),
        changed_time=int(changed or 0),
        device_category=(
            int.from_bytes(category_value, "big") if isinstance(category_value, bytes) else None
        ),
    )


async def read_aggregation(client: Sep2Client, end_device: Any) -> AggregationInfo | None:
    """Read the aggregation an EndDevice describes, or return None when it describes none.

    Follows Annex C.3: the AggregatedDeviceList when the EndDevice links one,
    then the AggregationPriority when the distribution is Priority. A list or a
    priority the server does not serve (404 or 204), cannot be read, or does
    not parse is logged and left empty, so one EndDevice's aggregation never
    stops discovery of the others.
    """
    if not is_aggregation(end_device):
        return None
    edev_href = getattr(end_device, "href", None) or "<EndDevice>"
    distribution = distribution_of(end_device)
    list_href = _href(getattr(end_device, "aggregated_device_list_link", None))
    priority_href = _href(getattr(end_device, "aggregation_priority_link", None))

    members: list[AggregatedDeviceInfo] = []
    poll_rate: int | None = None
    if list_href:
        try:
            pages = await client.get_list(list_href, AggregatedDeviceList)
        except (Sep2ProtocolError, Sep2PayloadError) as exc:
            logger.warning("AggregatedDeviceList at %s could not be read: %s", list_href, exc)
            pages = []
        for page in pages:
            if poll_rate is None:
                poll_rate = normalize_poll_rate(page.poll_rate, resource_key="adev")
            members.extend(_member(device) for device in page.aggregated_device)

    priority: tuple[str, ...] = ()
    if distribution == PRIORITY:
        if priority_href is None:
            # Annex B.24: the AggregationPriority SHALL be present for Priority.
            logger.warning(
                "%s has a Priority distribution and no AggregationPriorityLink", edev_href
            )
        else:
            try:
                order = await client.get(priority_href, AggregationPriority)
                priority = tuple(bytes(data.l_fdi).hex() for data in order.priority_data)
            except (Sep2ProtocolError, Sep2PayloadError) as exc:
                logger.warning(
                    "AggregationPriority at %s could not be read: %s", priority_href, exc
                )
    elif priority_href is not None:
        # Annex B.24: the AggregationPriority SHALL NOT be present otherwise.
        logger.info(
            "%s links an AggregationPriority without a Priority distribution; it is not read",
            edev_href,
        )
    return AggregationInfo(
        distribution=distribution,
        members=tuple(members),
        priority=priority,
        aggregated_device_list_href=list_href,
        aggregation_priority_href=priority_href,
        list_poll_rate=poll_rate,
    )


__all__ = [
    "PRIORITY",
    "AggregatedDeviceInfo",
    "AggregationInfo",
    "distribution_of",
    "is_aggregation",
    "read_aggregation",
]
