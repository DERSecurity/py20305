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

from py20305.client.errors import Sep2ConnectionError, Sep2PayloadError, Sep2ProtocolError
from py20305.client.poll_rate import normalize_poll_rate
from py20305.models.sep.sep import AggregatedDeviceList, AggregationPriority

if TYPE_CHECKING:
    from py20305.client.http import Sep2Client

logger = logging.getLogger(__name__)

#: The failures that leave one aggregation resource unread without stopping
#: discovery: a refused or unparsable response, and a timeout or lost connection.
#: A redirect and a request-limiter failure still propagate, so the caller
#: rediscovers or backs off as for any other resource.
_READ_FAILURES = (Sep2ProtocolError, Sep2PayloadError, Sep2ConnectionError)

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
    not read or its ``pollRate`` disables polling. ``list_poll_disabled`` is
    True when the list's ``pollRate`` is 0 or less, which disables polling.
    """

    distribution: int | None
    members: tuple[AggregatedDeviceInfo, ...] = ()
    priority: tuple[str, ...] = ()
    aggregated_device_list_href: str | None = None
    aggregation_priority_href: str | None = None
    list_poll_rate: int | None = None
    list_poll_disabled: bool = False

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


async def read_aggregation(
    client: Sep2Client, end_device: Any, previous: AggregationInfo | None = None
) -> AggregationInfo | None:
    """Read the aggregation an EndDevice describes, or return None when it describes none.

    Follows Annex C.3: the AggregatedDeviceList when the EndDevice links one,
    then the AggregationPriority when the distribution is Priority. A list or a
    priority that cannot be read (the server does not serve it, including a
    list whose first page is 404 or 204, the request times out or the
    connection fails) or does not parse is logged, so one
    EndDevice's aggregation never stops discovery of the others. When
    ``previous`` read the same resource, its members or priority are kept;
    otherwise that part is left empty.
    """
    if not is_aggregation(end_device):
        return None
    edev_href = getattr(end_device, "href", None) or "<EndDevice>"
    distribution = distribution_of(end_device)
    list_href = _href(getattr(end_device, "aggregated_device_list_link", None))
    priority_href = _href(getattr(end_device, "aggregation_priority_link", None))

    members: tuple[AggregatedDeviceInfo, ...] = ()
    poll_rate: int | None = None
    poll_disabled = False
    if list_href:
        try:
            pages = await client.get_list(list_href, AggregatedDeviceList)
        except _READ_FAILURES as exc:
            logger.warning("AggregatedDeviceList at %s could not be read: %s", list_href, exc)
            if previous is not None and previous.aggregated_device_list_href == list_href:
                members, poll_rate = previous.members, previous.list_poll_rate
                poll_disabled = previous.list_poll_disabled
        else:
            if not pages:
                # The paged reader answers a first-page 404 or 204 with no
                # pages, where a served list, even an empty one, has a page.
                logger.warning("AggregatedDeviceList at %s was not served", list_href)
                if previous is not None and previous.aggregated_device_list_href == list_href:
                    members, poll_rate = previous.members, previous.list_poll_rate
                    poll_disabled = previous.list_poll_disabled
            read: list[AggregatedDeviceInfo] = []
            for index, page in enumerate(pages):
                if index == 0:
                    # The first page's pollRate stands, including one that
                    # disables polling (normalized to None).
                    poll_rate = normalize_poll_rate(page.poll_rate, resource_key="adev")
                    poll_disabled = poll_rate is None
                read.extend(_member(device) for device in page.aggregated_device)
            if pages:
                members = tuple(read)

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
            except _READ_FAILURES as exc:
                logger.warning(
                    "AggregationPriority at %s could not be read: %s", priority_href, exc
                )
                if previous is not None and previous.aggregation_priority_href == priority_href:
                    priority = previous.priority
    elif priority_href is not None:
        # Annex B.24: the AggregationPriority SHALL NOT be present otherwise.
        logger.info(
            "%s links an AggregationPriority without a Priority distribution; it is not read",
            edev_href,
        )
    return AggregationInfo(
        distribution=distribution,
        members=members,
        priority=priority,
        aggregated_device_list_href=list_href,
        aggregation_priority_href=priority_href,
        list_poll_rate=poll_rate,
        list_poll_disabled=poll_disabled,
    )


def aggregation_poll_rate(info: AggregationInfo, fallback: int) -> int | None:
    """Return how often to read ``info`` again, in seconds, or None to not poll it.

    The AggregatedDeviceList's own ``pollRate`` decides; a list whose
    ``pollRate`` disables polling is not polled. An aggregation with no list
    readable yet, or with only a priority, uses ``fallback``.
    """
    if info.aggregated_device_list_href is not None and info.list_poll_disabled:
        return None
    return info.list_poll_rate or fallback


__all__ = [
    "PRIORITY",
    "AggregatedDeviceInfo",
    "AggregationInfo",
    "aggregation_poll_rate",
    "distribution_of",
    "is_aggregation",
    "read_aggregation",
]
