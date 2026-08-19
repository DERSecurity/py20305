"""Response shapes for the management API.

Turns client state into the dicts the API returns. Distinct from
:mod:`py20305.json_form`, which projects IEEE 2030.5 models into
JSON generally: these functions add the API's own vocabulary on top of that
projection -- a device's ``role`` and ``registration``, a program's summary
form, a tariff's intervals with their computed status.
"""

from __future__ import annotations

import logging
from typing import Any

from py20305.json_form import (
    safe_serialize,
    serialize_mrid,
    unwrap_value,
)

logger = logging.getLogger(__name__)

def serialize_device_info(
    device_href: str,
    edev_state: Any,
    connector_lfdis: set[str] | None = None,
    *,
    is_client_mode: bool = False,
    own_lfdi: str | None = None,
) -> dict[str, Any]:
    """Serialize an EndDeviceState to the management API's device shape.

    Args:
        device_href: The device href, used as deviceId.
        edev_state: EndDeviceState object.
        connector_lfdis: Set of lowercase LFDIs that have connectors configured.
            Decides whether this EndDevice is bound to a connector.
        is_client_mode: If True, every device is an EndDevice and none
            holds the IEEE 2030.5 aggregator role. This is the client's
            own operating mode.
        own_lfdi: This client's own certificate LFDI. An EndDevice matching it
            is the client's own registration.

    Returns:
        Dict describing the device.
    """
    lfdi_hex = edev_state.lfdi.hex() if isinstance(edev_state.lfdi, bytes) else str(edev_state.lfdi)
    lfdi_norm = lfdi_hex.lower()
    has_connector = connector_lfdis is not None and lfdi_norm in connector_lfdis

    # Identify the device holding the IEEE 2030.5 aggregator role by its own
    # certificate identity, not by absence from the connector map. Absence is
    # exactly the shape a certificate/LFDI provisioning mismatch produces, so
    # keying on it labels the mismatched device an aggregator and then filters
    # it out of every selector -- a misconfiguration rendering as an empty
    # list rather than as a misconfiguration. Fall back to that heuristic only
    # when this client's own LFDI is unavailable, i.e. no TLS cert is loaded.
    is_aggregator = False
    if not is_client_mode:
        if own_lfdi is not None:
            is_aggregator = lfdi_norm == own_lfdi.lower()
        elif connector_lfdis is not None:
            is_aggregator = not has_connector

    if is_aggregator:
        registration = "aggregator"
    elif has_connector or (is_client_mode and connector_lfdis is None):
        # The standalone client service supplies no connector set: it manages a
        # single device that is its own certificate identity, so anything the
        # server lists is registered. When a set *is* supplied -- including in
        # client mode -- the LFDI still has to match, or this would label a
        # mismatched row registered while /device-connectors calls it unmatched,
        # and the UI would offer a connector that does not exist.
        registration = "registered"
    else:
        registration = "unmatched"

    return {
        "deviceId": device_href,
        "href": edev_state.href,
        "lfdi": lfdi_hex,
        "role": "Aggregator" if is_aggregator else "EndDevice",
        "isAggregator": is_aggregator,
        "registration": registration,
    }


def serialize_unregistered_device(spec: Any) -> dict[str, Any]:
    """Serialize a locally-configured device the server has not registered.

    These have no EndDevice upstream and so no href, but they do have a
    connector, which is all Connector Inspection needs -- the connector_ops
    routes resolve by LFDI as readily as by href. Surfacing them is what keeps
    a provisioning gap legible: the operator sees the device they configured,
    labeled unregistered, instead of an empty panel.
    """
    lfdi_norm = str(spec.lfdi).lower()
    return {
        "deviceId": lfdi_norm,
        "href": None,
        "lfdi": lfdi_norm,
        "role": "EndDevice",
        "isAggregator": False,
        "registration": "unregistered",
    }


def serialize_der_program(program_href: str, program_state: Any) -> dict[str, Any]:
    """Serialize a DerProgramState to the UI format.

    Args:
        program_href: The program href
        program_state: DerProgramState object

    Returns:
        Dict with program info for the UI
    """
    program = program_state.program
    return {
        "href": program_href,
        "primacy": program_state.primacy,
        "description": getattr(program, "description", None),
        "default_dercontrol": safe_serialize(program_state.default_dercontrol),
        "der_controls_count": len(program_state.der_controls),
        "der_curves_count": len(program_state.der_curves),
    }


def _tariff_interval_status(interval: Any, now: int) -> str:
    """Display status for a TimeTariffInterval, derived from the clock.

    Mirrors the server's read-time projection: cancelled/superseded statuses
    (2/3/4) are honoured; otherwise the nominal window vs *now* gives
    scheduled / active / completed.
    """
    event_status = getattr(interval, "event_status", None)
    current = getattr(event_status, "current_status", None) if event_status is not None else None
    if current in (2, 3):
        return "cancelled"
    if current == 4:
        return "superseded"
    window = getattr(interval, "interval", None)
    start = unwrap_value(getattr(window, "start", None)) if window is not None else None
    duration = getattr(window, "duration", None) if window is not None else None
    if not isinstance(start, int) or not isinstance(duration, int):
        return "unknown"
    if now < start:
        return "scheduled"
    if now >= start + duration:
        return "completed"
    return "active"


def serialize_tariff_profile(
    tp_state: Any, prices_by_mrid: dict[str, list[dict[str, Any]]], now: int
) -> dict[str, Any]:
    """Serialize a ``TariffProfileState`` (+ fetched CTI prices) for the UI.

    ``prices_by_mrid`` maps each interval's hex mRID to its ConsumptionTariff
    block list; ``now`` is the (FSA-scoped) clock used to flag the active interval.
    """
    profile = tp_state.profile
    rate_components = []
    for rc in tp_state.rate_components:
        intervals = []
        for iv in rc.time_tariff_intervals:
            mrid = serialize_mrid(iv.m_rid)
            window = getattr(iv, "interval", None)
            start = unwrap_value(getattr(window, "start", None)) if window is not None else None
            duration = getattr(window, "duration", None) if window is not None else None
            end = start + duration if isinstance(start, int) and isinstance(duration, int) else None
            intervals.append(
                {
                    "mrid": mrid,
                    "start": start,
                    "duration": duration,
                    "end": end,
                    "touTier": unwrap_value(getattr(iv, "tou_tier", None)),
                    "status": _tariff_interval_status(iv, now),
                    "prices": prices_by_mrid.get(mrid, []) if mrid is not None else [],
                }
            )
        intervals.sort(key=lambda x: (x["start"] is None, x["start"] or 0))
        rate_components.append({"href": rc.href, "intervals": intervals})
    return {
        "href": tp_state.href,
        "primacy": tp_state.primacy,
        "currency": unwrap_value(profile.currency),
        "priceMultiplier": unwrap_value(profile.price_power_of_ten_multiplier),
        "rateCode": getattr(profile, "rate_code", None),
        "description": getattr(profile, "description", None),
        "discoveredFromFsa": tp_state.discovered_from_fsa_href,
        "rateComponents": rate_components,
    }

