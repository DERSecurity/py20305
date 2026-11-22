# Aggregations

IEEE 2030.5-2023 §8.7 lets one EndDevice represent an aggregation of several
devices, such as a virtual power plant. The server describes the aggregation on
that EndDevice, and a DERControl sent to it is meant for the aggregation as a
whole. This page says what the client reads, where an application finds it,
and what the application does with it.

Everything here is for code that embeds the library. The `py20305` runner reads
the aggregation like any embedding application, but a control for an
aggregation's EndDevice reaches only the connector configured for that
EndDevice's LFDI.

## What the server provides

Annex C.3 ("Registration: Remote with AggregatedDeviceList") is the flow. The
service provider sets up, out of band, the aggregation's EndDevice, its
AggregatedDeviceList, its distribution and, for a Priority distribution, its
AggregationPriority:

| Resource | Where | Meaning |
|---|---|---|
| `distribution` | On the EndDevice (AbstractDevice, Annex B.5) | How controls SHALL be distributed among the members: 0 unspecified, 1 Uniform (an equal value for each member), 2 Prorate (an equal percentage of nameplate rating), 3 Priority (each member completely utilized before the next) (Annex B.24) |
| AggregatedDeviceList | `AggregatedDeviceListLink`, for example `/edev/3/adev` | The members, each with `lFDI`, `sFDI`, `enabled` and `changedTime`. It may not list every component of the aggregation (§8.7.1) |
| AggregationPriority | `AggregationPriorityLink`, for example `/edev/3/aggp` | The members' LFDIs, highest priority first. Present only for a Priority distribution (Annex B.24) |

## What the client reads

Discovery reads, for each EndDevice it walks that carries a non-zero
`distribution`, an `AggregatedDeviceListLink` or an `AggregationPriorityLink`:

1. the AggregatedDeviceList, every page;
2. the AggregationPriority, when the distribution is Priority.

It does not read an AggregationPriority for any other distribution, since the
standard says one SHALL NOT be present, and logs that the server links one. A
Priority distribution with no AggregationPriorityLink is logged as a warning.
A list or a priority the server does not serve, cannot be read or does not
parse is logged and left empty; the rest of discovery goes on.

The reads are repeated each time the EndDeviceList is refreshed, so a changed
distribution or priority, and a member the server marks `enabled` false before
removing it (§8.7.3.1), are seen at the EndDeviceList's poll rate.

The result is an `AggregationInfo` on the EndDevice's state:

```python
info = client.aggregation("a9" * 20)       # by the aggregation EndDevice's LFDI
if info is not None:
    info.distribution        # 0 to 3, or None when the EndDevice carries none
    info.enabled_members     # member LFDIs the server has not disabled
    info.disabled_members    # member LFDIs marked enabled=false
    info.priority            # member LFDIs, highest first (Priority only)
```

The same object is `client.state.end_devices[href].aggregation`.

## What the application does

A DERControl for the aggregation's EndDevice is delivered like any other: the
event processor applies it to the connector of that EndDevice's LFDI and posts
the event's Responses for that EndDevice, from the connector's outcome. Sharing
the control across the members, by the distribution, is the application's job,
usually inside that connector:

- A total, such as `opModTargetW` or a CSIP-AUS limit, is divided by the
  distribution: Uniform equally, Prorate in proportion to each member's
  nameplate rating, Priority filling members in priority order up to their
  ratings.
- A member the server disabled takes no share.
- The connector should raise when no member could take the control, so the
  server is told the event was not applied for the aggregation.

The standard does not say how to combine the members' details for the
aggregation's own resources (§8.7.3.1); an application that posts a
DERCapability for the aggregation decides that, for example by summing ratings
as CSIP-AUS does.

## Registering a member

Where the server accepts a POST to the AggregatedDeviceList (Annex A.2.5.2:
POST is optional), the client can add a member:

```python
location = await client.register_aggregated_device(
    aggregated_device_list_href=info.aggregated_device_list_href,
    lfdi=member_lfdi,
)
```

The `sFDI` is derived from the LFDI. The list is first read with the `sFDI`
query string parameter (§8.7.3.2), and an LFDI it already holds raises
`ValueError`, since a client SHALL NOT POST an AggregatedDevice the list already
contains (§8.7.3.1). Most servers provision members out of band, as Annex C.3
describes, and refuse the POST.
