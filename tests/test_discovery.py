"""Tests for hierarchical resource discovery."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from py20305.client.discovery import (
    _extract_href,
    discover,
    refresh_der_controls,
    refresh_der_programs,
    refresh_end_device_lists,
    refresh_function_set_assignments,
)
from py20305.client.errors import Sep2PayloadError, Sep2ProtocolError
from py20305.client.poll_rate import DEFAULT_POLL_RATE
from py20305.client.state import DiscoveredState
from py20305.client.timebase import ServerTimebase
from py20305.models.sep.sep import (
    DefaultDercontrol,
    DefaultDercontrolLink,
    Der1,
    DeravailabilityLink,
    DercapabilityLink,
    DercontrolBase,
    DercontrolList,
    DercontrolListLink,
    Derlist,
    DerlistLink,
    Derprogram1,
    DerprogramList,
    DerprogramListLink,
    DersettingsLink,
    DerstatusLink,
    DeviceCapability,
    EndDevice1,
    EndDeviceList,
    EndDeviceListLink,
    FunctionSetAssignments1,
    FunctionSetAssignmentsList,
    FunctionSetAssignmentsListLink,
    MirrorUsagePointListLink,
    MRidtype,
    Pintype,
    PrimacyType,
    Registration,
    RegistrationLink,
    SelfDevice,
    SelfDeviceLink,
    Sfditype,
    SubscriptionListLink,
    Time,
    TimeLink,
    TimeOffsetType,
    TimeType,
)


def _make_dcap(
    *,
    edev_href: str | None = "/edev",
    time_href: str | None = "/tm",
    mup_href: str | None = None,
    poll_rate: int | None = None,
    derp_list_href: str | None = None,
    sdev_href: str | None = None,
) -> DeviceCapability:
    dcap = DeviceCapability()
    if edev_href:
        dcap.end_device_list_link = EndDeviceListLink(href=edev_href)
    if time_href:
        dcap.time_link = TimeLink(href=time_href)
    if mup_href:
        dcap.mirror_usage_point_list_link = MirrorUsagePointListLink(href=mup_href)
    if derp_list_href:
        dcap.derprogram_list_link = DerprogramListLink(href=derp_list_href)
    if sdev_href:
        dcap.self_device_link = SelfDeviceLink(href=sdev_href)
    dcap.poll_rate = poll_rate
    return dcap


def _make_self_device(
    href: str = "/sdev",
    der_list_href: str | None = "/sdev/der",
    poll_rate: int | None = None,
) -> SelfDevice:
    sdev = SelfDevice(s_fdi=Sfditype(value=0))
    sdev.href = href
    if der_list_href:
        sdev.derlist_link = DerlistLink(href=der_list_href)
    if poll_rate is not None:
        sdev.poll_rate = poll_rate
    return sdev


def _make_der(
    href: str,
    *,
    settings_href: str | None = None,
    capability_href: str | None = None,
    status_href: str | None = None,
    availability_href: str | None = None,
) -> Der1:
    der = Der1()
    der.href = href
    if settings_href:
        der.dersettings_link = DersettingsLink(href=settings_href)
    if capability_href:
        der.dercapability_link = DercapabilityLink(href=capability_href)
    if status_href:
        der.derstatus_link = DerstatusLink(href=status_href)
    if availability_href:
        der.deravailability_link = DeravailabilityLink(href=availability_href)
    return der


def _make_der_list(*ders: Der1) -> Derlist:
    dl = Derlist(**{"all": len(ders), "results": len(ders)})
    dl.der = list(ders)
    return dl


def _make_time() -> Time:
    tt = TimeType(value=1000)
    zero = TimeOffsetType(value=0)
    return Time(
        current_time=tt,
        dst_end_time=tt,
        dst_offset=zero,
        dst_start_time=tt,
        quality=3,
        tz_offset=zero,
    )


def _make_edev(href: str, lfdi: bytes = b"\x01" * 20) -> EndDevice1:
    edev = EndDevice1(
        s_fdi=Sfditype(value=0),
        changed_time=TimeType(value=0),
    )
    edev.href = href
    edev.l_fdi = lfdi
    return edev


def _make_edev_list(*edevs: EndDevice1) -> EndDeviceList:
    edl = EndDeviceList(**{"all": len(edevs), "results": len(edevs)})
    edl.end_device = list(edevs)
    return edl


def _make_fsa(
    href: str,
    derp_list_href: str | None = None,
    time_href: str | None = None,
) -> FunctionSetAssignments1:
    fsa = FunctionSetAssignments1(m_rid=MRidtype(value=b"\x00" * 16))
    fsa.href = href
    if derp_list_href:
        fsa.derprogram_list_link = DerprogramListLink(href=derp_list_href)
    if time_href:
        fsa.time_link = TimeLink(href=time_href)
    return fsa


def _make_fsa_list(*fsas: FunctionSetAssignments1) -> FunctionSetAssignmentsList:
    fl = FunctionSetAssignmentsList(**{"all": len(fsas), "results": len(fsas)})
    fl.function_set_assignments = list(fsas)
    return fl


def _make_derp(href: str, primacy: int = 0) -> Derprogram1:
    derp = Derprogram1(m_rid=MRidtype(value=b"\x00" * 16), primacy=PrimacyType(value=primacy))
    derp.href = href
    return derp


def _make_derp_list(*derps: Derprogram1) -> DerprogramList:
    dl = DerprogramList(**{"all": len(derps), "results": len(derps)})
    dl.derprogram = list(derps)
    return dl


class _MockClient:
    """Mock Sep2Client with configurable GET responses.

    Values can be actual response objects or Sep2ProtocolError instances
    (which will be raised instead of returned).
    """

    def __init__(
        self,
        responses: dict[str, Any],
        raw_bodies: dict[str, bytes] | None = None,
        server_2018_compat: bool = False,
    ) -> None:
        self._responses = responses
        self._raw_bodies = raw_bodies or {}
        self.timebase = ServerTimebase()
        self.server_2018_compat = server_2018_compat

    async def get(self, path: str, model_type: type) -> Any:
        # Strip query params for lookup
        key = path.split("?")[0]
        if key not in self._responses:
            raise ValueError(f"No mock response for {key}")
        val = self._responses[key]
        if isinstance(val, Exception):
            raise val
        return val

    async def get_with_body(self, path: str, model_type: type) -> tuple[Any, bytes]:
        result = await self.get(path, model_type)
        raw = self._raw_bodies.get(path, b"<default/>")
        return result, raw

    async def get_list(self, path: str, model_type: type) -> list[Any]:
        result = await self.get(path, model_type)
        if isinstance(result, list):
            return result
        return [result]


@pytest.mark.asyncio
async def test_full_discovery_chain():
    """Discovery populates devices, programs, and mapping."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1", primacy=5)

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert state.dcap is not None
    assert state.time is not None
    assert "/edev/1" in state.end_devices
    assert "/derp/1" in state.der_programs
    assert state.der_programs["/derp/1"].primacy == 5
    assert state.device_mapping.program_to_devices["/derp/1"] == ["/edev/1"]
    assert state.device_mapping.device_to_programs["/edev/1"] == ["/derp/1"]


@pytest.mark.asyncio
async def test_no_edev_link():
    """Discovery with no EndDeviceListLink returns early."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(edev_href=None),
            "/tm": _make_time(),
        }
    )
    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert state.dcap is not None
    assert state.end_devices == {}


@pytest.mark.asyncio
async def test_poll_rate_extracted():
    """Server poll rate is normalized and stored."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(poll_rate=120, edev_href=None),
            "/tm": _make_time(),
        }
    )
    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert state.poll_rates["dcap"] == 120


@pytest.mark.asyncio
async def test_poll_rate_default_when_none():
    client = _MockClient(
        {
            "/dcap": _make_dcap(edev_href=None),
            "/tm": _make_time(),
        }
    )
    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert state.poll_rates["dcap"] == DEFAULT_POLL_RATE


@pytest.mark.asyncio
async def test_clears_previous_state():
    """Discovery clears existing state before running."""
    state = DiscoveredState()
    state.poll_rates["old"] = 999

    client = _MockClient(
        {
            "/dcap": _make_dcap(edev_href=None),
            "/tm": _make_time(),
        }
    )
    await discover(client, state)  # type: ignore[arg-type]

    assert "old" not in state.poll_rates


@pytest.mark.asyncio
async def test_mup_href_extracted():
    client = _MockClient(
        {
            "/dcap": _make_dcap(edev_href=None, mup_href="/mup"),
            "/tm": _make_time(),
        }
    )
    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert state.mup_list_href == "/mup"


@pytest.mark.asyncio
async def test_missing_fsa_link_skips_programs():
    """EndDevice without FSA link has no programs."""
    edev = _make_edev("/edev/1")
    # No function_set_assignments_list_link

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
        }
    )
    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert "/edev/1" in state.end_devices
    assert state.der_programs == {}


@pytest.mark.asyncio
async def test_no_time_link():
    """Discovery works without a TimeLink."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(time_href=None, edev_href=None),
        }
    )
    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert state.time is None
    assert state.time_href is None


@pytest.mark.asyncio
async def test_subscriptionlist_pollrate_error_is_nonfatal():
    """A non-protocol error reading the SubscriptionList pollRate must not abort
    discovery -- it's a best-effort optimization, so the reconcile poll falls
    back to the default cadence (poll_rates["sub"] left unset)."""
    edev = _make_edev("/edev/1")
    edev.subscription_list_link = SubscriptionListLink(href="/edev/1/sub")
    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/sub": OSError("transport down"),
        }
    )
    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert "/edev/1" in state.end_devices
    assert "sub" not in state.poll_rates


@pytest.mark.asyncio
async def test_dderc_204_no_content_does_not_crash_discovery():
    """A 204 No Content for a program's DefaultDERControl (an optional resource)
    must be tolerated like a 404, not propagated out of discovery. Regression: an
    unforgiven 204 crash-looped the aggregator on every connect when a program was
    assigned with a DefaultDERControlLink but no default control set."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")
    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1", primacy=5)
    derp.default_dercontrol_link = DefaultDercontrolLink(href="/derp/1/dderc")

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
            "/derp/1/dderc": Sep2ProtocolError("204 No Content", 204),
        }
    )
    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]  # must not raise

    assert "/derp/1" in state.der_programs
    assert state.der_programs["/derp/1"].default_dercontrol is None


def test_extract_href_none():
    assert _extract_href(None) is None


def test_extract_href_empty_string():
    link = EndDeviceListLink(href="")
    assert _extract_href(link) is None


def test_extract_href_valid():
    link = EndDeviceListLink(href="/edev")
    assert _extract_href(link) == "/edev"


# --- refresh_end_device_lists tests ---


@pytest.mark.asyncio
async def test_refresh_edev_updates_existing_devices():
    """refresh_end_device_lists updates device data for known hrefs."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1")

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert state.end_devices["/edev/1"].lfdi == b"\x01" * 20

    # Now refresh with updated LFDI
    updated_edev = _make_edev("/edev/1", lfdi=b"\x02" * 20)
    client._responses["/edev"] = _make_edev_list(updated_edev)
    await refresh_end_device_lists(client, state)  # type: ignore[arg-type]

    assert state.end_devices["/edev/1"].lfdi == b"\x02" * 20


@pytest.mark.asyncio
async def test_refresh_edev_ignores_unknown_hrefs():
    """refresh_end_device_lists skips devices not already in state."""
    edev = _make_edev("/edev/1")
    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert "/edev/1" in state.end_devices

    # Refresh with an extra unknown device
    new_edev = _make_edev("/edev/2")
    client._responses["/edev"] = _make_edev_list(edev, new_edev)
    await refresh_end_device_lists(client, state)  # type: ignore[arg-type]

    assert "/edev/2" not in state.end_devices


@pytest.mark.asyncio
async def test_refresh_edev_no_dcap():
    """refresh_end_device_lists returns early when dcap is None."""
    state = DiscoveredState()
    client = _MockClient({})
    await refresh_end_device_lists(client, state)  # type: ignore[arg-type]
    assert state.end_devices == {}


# --- refresh_function_set_assignments tests ---


@pytest.mark.asyncio
async def test_refresh_fsa_clears_and_rebuilds():
    """refresh_function_set_assignments clears and rebuilds FSA lists."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa1 = _make_fsa("/fsa/1")
    fsa2 = _make_fsa("/fsa/2")

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa1),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert len(state.end_devices["/edev/1"].fsa_list) == 1

    # Refresh with two FSAs
    client._responses["/edev/1/fsa"] = _make_fsa_list(fsa1, fsa2)
    await refresh_function_set_assignments(client, state)  # type: ignore[arg-type]

    assert len(state.end_devices["/edev/1"].fsa_list) == 2


@pytest.mark.asyncio
async def test_discover_derp_list_404_skips_fsa():
    """Discovery skips FSAs whose DERProgramList returns 404."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa_ok = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    fsa_missing = _make_fsa("/fsa/2", derp_list_href="/fsa/2/derp")
    derp = _make_derp("/derp/1", primacy=5)

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa_ok, fsa_missing),
            "/fsa/1/derp": _make_derp_list(derp),
            "/fsa/2/derp": Sep2ProtocolError("Not found", 404),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    # FSA 1's program discovered OK; FSA 2's 404 didn't crash discovery
    assert "/derp/1" in state.der_programs
    assert len(state.der_programs) == 1
    assert len(state.end_devices["/edev/1"].fsa_list) == 2


@pytest.mark.asyncio
async def test_refresh_fsa_handles_404():
    """refresh_function_set_assignments skips devices with 404 FSA."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1")
    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    # Now simulate 404 on refresh
    client._responses["/edev/1/fsa"] = Sep2ProtocolError("Not found", 404)
    await refresh_function_set_assignments(client, state)  # type: ignore[arg-type]

    # On 404 the old FSA data is preserved (clear only happens after successful fetch)
    assert len(state.end_devices["/edev/1"].fsa_list) == 1


# --- refresh_der_programs tests ---


@pytest.mark.asyncio
async def test_refresh_derp_updates_existing_programs():
    """refresh_der_programs updates program data and primacy."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1", primacy=5)

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert state.der_programs["/derp/1"].primacy == 5

    # Refresh with updated primacy
    updated_derp = _make_derp("/derp/1", primacy=10)
    client._responses["/fsa/1/derp"] = _make_derp_list(updated_derp)
    await refresh_der_programs(client, state)  # type: ignore[arg-type]

    assert state.der_programs["/derp/1"].primacy == 10


@pytest.mark.asyncio
async def test_refresh_derp_handles_404():
    """refresh_der_programs skips FSAs returning 404."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1", primacy=5)

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    # Simulate 404 on refresh
    client._responses["/fsa/1/derp"] = Sep2ProtocolError("Not found", 404)
    await refresh_der_programs(client, state)  # type: ignore[arg-type]

    # Program should still exist with original primacy (404 skips the update)
    assert state.der_programs["/derp/1"].primacy == 5


@pytest.mark.asyncio
async def test_refresh_derp_prunes_stale_programs():
    """refresh_der_programs removes programs no longer in any DERP list."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp1 = _make_derp("/derp/1", primacy=5)
    derp2 = _make_derp("/derp/2", primacy=10)

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp1, derp2),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert "/derp/1" in state.der_programs
    assert "/derp/2" in state.der_programs

    # Server reset: DERP list now only contains derp/1
    client._responses["/fsa/1/derp"] = _make_derp_list(derp1)
    removed = await refresh_der_programs(client, state)  # type: ignore[arg-type]

    assert "/derp/1" in state.der_programs
    assert "/derp/2" not in state.der_programs
    assert removed == ["/derp/2"]


@pytest.mark.asyncio
async def test_refresh_derp_preserves_programs_from_404_fsa():
    """Programs from FSAs returning 404 are preserved (can't verify removal)."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa1 = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    fsa2 = _make_fsa("/fsa/2", derp_list_href="/fsa/2/derp")
    derp1 = _make_derp("/derp/1", primacy=5)
    derp2 = _make_derp("/derp/2", primacy=10)

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa1, fsa2),
            "/fsa/1/derp": _make_derp_list(derp1),
            "/fsa/2/derp": _make_derp_list(derp2),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert "/derp/1" in state.der_programs
    assert "/derp/2" in state.der_programs

    # FSA/2's DERP list returns 404 — derp/2 should be preserved
    client._responses["/fsa/2/derp"] = Sep2ProtocolError("Not found", 404)
    removed = await refresh_der_programs(client, state)  # type: ignore[arg-type]

    assert "/derp/1" in state.der_programs
    assert "/derp/2" in state.der_programs
    assert removed == []


@pytest.mark.asyncio
async def test_refresh_after_p72_delete_stops_applying_controls():
    """Aggregator drops a server-deleted DERProgram from cache and stops
    fetching its child resources in subsequent refresh cycles.

    This is the §11 P7.2 acceptance criterion: when the operator deletes
    a DERProgram via DELETE /api/v1/der-programs/<path> on the
    server side (or the program disappears from the parent
    DERProgramList for any other reason), the aggregator's
    refresh_der_programs prunes the entry from state.der_programs, and
    the next refresh_der_controls cycle iterates only the surviving
    programs -- it must NOT touch the deleted program's /derc list,
    /dderc, or curve list, because those resources are gone server-
    side and any fetch would either 404 or (worse) hit a recreated
    program with the same href and apply stale controls under it.
    """
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp1 = _make_derp("/derp/1", primacy=5)
    derp1.dercontrol_list_link = DercontrolListLink(href="/derp/1/derc")
    derp2 = _make_derp("/derp/2", primacy=10)
    derp2.dercontrol_list_link = DercontrolListLink(href="/derp/2/derc")

    # Track every path the client is asked to fetch so the test can
    # assert NO fetch happens against the deleted program's children
    # in the post-delete refresh_der_controls cycle.
    fetched_paths: list[str] = []

    class _TrackingClient(_MockClient):
        async def get(self, path: str, model_type: type) -> Any:
            fetched_paths.append(path.split("?")[0])
            return await super().get(path, model_type)

        async def get_list(self, path: str, model_type: type) -> list[Any]:
            fetched_paths.append(path.split("?")[0])
            return await super().get_list(path, model_type)

    client = _TrackingClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp1, derp2),
            # Empty DERControlList for both programs at discovery time so
            # _discover_program completes without surprises.
            "/derp/1/derc": DercontrolList(**{"all": 0, "results": 0}),
            "/derp/2/derc": DercontrolList(**{"all": 0, "results": 0}),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert "/derp/1" in state.der_programs
    assert "/derp/2" in state.der_programs

    # Simulate the P7.2 delete: /derp/2 is gone from the parent list,
    # AND any direct GET on its child resources would now 404 (matching
    # the cascade -- /derp/2/derc, /derp/2/dderc, /derp/2/dc all
    # removed by delete_der_program).
    client._responses["/fsa/1/derp"] = _make_derp_list(derp1)
    client._responses["/derp/2/derc"] = Sep2ProtocolError("Not found", 404)

    removed = await refresh_der_programs(client, state)  # type: ignore[arg-type]
    assert removed == ["/derp/2"]
    assert "/derp/1" in state.der_programs
    assert "/derp/2" not in state.der_programs

    # Subsequent control-refresh cycle: must iterate only the surviving
    # program. The deleted program's /derc must NOT be fetched.
    fetched_paths.clear()
    await refresh_der_controls(client, state)  # type: ignore[arg-type]

    # /derp/1/derc is fetched (surviving program) but /derp/2/derc is
    # not -- the prune from refresh_der_programs above ensured the
    # deleted program never enters the iteration in the first place.
    assert "/derp/1/derc" in fetched_paths
    assert "/derp/2/derc" not in fetched_paths


@pytest.mark.asyncio
async def test_refresh_after_p72_delete_handles_grouped_program():
    """With a group lookup, the deleted program lives under a group list
    (/SY-ST-SS/derp), not under an edev. Same prune contract applies:
    the program is dropped from state.der_programs and subsequent
    refresh cycles don't touch its children.

    Mirrors the prior test but exercises the multi-edev fan-in path
    where two edev FSAs share the same /derp list -- the case
    delete_der_program syncs DERProgramListLink.all on every
    referencing FSA. Verifies the aggregator's prune is symmetric
    across both FSAs.
    """
    edev_a = _make_edev("/edev/2", lfdi=b"\x02" * 20)
    edev_a.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/2/fsa")
    edev_b = _make_edev("/edev/3", lfdi=b"\x03" * 20)
    edev_b.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/3/fsa")

    # Both edevs' FSAs reference the SAME /SY-ST-SS/derp list (the
    # multi-edev group case).
    fsa_a = _make_fsa("/edev/2/fsa/1", derp_list_href="/SY-ST-SS/derp")
    fsa_b = _make_fsa("/edev/3/fsa/1", derp_list_href="/SY-ST-SS/derp")
    derp1 = _make_derp("/SY-ST-SS/derp/1", primacy=5)
    derp1.dercontrol_list_link = DercontrolListLink(href="/SY-ST-SS/derp/1/derc")
    derp2 = _make_derp("/SY-ST-SS/derp/2", primacy=10)
    derp2.dercontrol_list_link = DercontrolListLink(href="/SY-ST-SS/derp/2/derc")

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev_a, edev_b),
            "/edev/2/fsa": _make_fsa_list(fsa_a),
            "/edev/3/fsa": _make_fsa_list(fsa_b),
            "/SY-ST-SS/derp": _make_derp_list(derp1, derp2),
            "/SY-ST-SS/derp/1/derc": DercontrolList(**{"all": 0, "results": 0}),
            "/SY-ST-SS/derp/2/derc": DercontrolList(**{"all": 0, "results": 0}),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert "/SY-ST-SS/derp/1" in state.der_programs
    assert "/SY-ST-SS/derp/2" in state.der_programs
    # Both edevs map to both group programs.
    assert state.device_mapping.program_to_devices["/SY-ST-SS/derp/2"] == [
        "/edev/2",
        "/edev/3",
    ]

    # Server-side P7.2 delete cascades through every FSA's view of the
    # group list (delete_der_program syncs all of them in the same
    # atomic_operation). Update both edevs' view of /SY-ST-SS/derp.
    client._responses["/SY-ST-SS/derp"] = _make_derp_list(derp1)

    removed = await refresh_der_programs(client, state)  # type: ignore[arg-type]
    assert removed == ["/SY-ST-SS/derp/2"]
    assert "/SY-ST-SS/derp/1" in state.der_programs
    assert "/SY-ST-SS/derp/2" not in state.der_programs
    # device_mapping must also drop the deleted program -- otherwise
    # control-application code that walks edev → programs would still
    # try to apply controls under the dead program.
    assert "/SY-ST-SS/derp/2" not in state.device_mapping.program_to_devices


# --- CSIP-AUS auto-detection tests ---


@pytest.mark.asyncio
async def test_csip_aus_detected_from_dcap_body():
    """Discovery sets csip_aus_mode when dcap body contains csipaus namespace."""
    raw_dcap = (
        b'<DeviceCapability xmlns="urn:ieee:std:2030.5:ns" xmlns:csipaus="https://csipaus.org/ns"/>'
    )
    client = _MockClient(
        {
            "/dcap": _make_dcap(edev_href=None),
            "/tm": _make_time(),
        },
        raw_bodies={"/dcap": raw_dcap},
    )
    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert state.csip_aus_mode is True


@pytest.mark.asyncio
async def test_csip_aus_not_detected_for_standard_server():
    """Discovery leaves csip_aus_mode False for standard IEEE 2030.5 servers."""
    raw_dcap = b'<DeviceCapability xmlns="urn:ieee:std:2030.5:ns"/>'
    client = _MockClient(
        {
            "/dcap": _make_dcap(edev_href=None),
            "/tm": _make_time(),
        },
        raw_bodies={"/dcap": raw_dcap},
    )
    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert state.csip_aus_mode is False


@pytest.mark.asyncio
async def test_csip_aus_cleared_on_rediscovery():
    """csip_aus_mode is reset to False when state is cleared."""
    state = DiscoveredState()
    state.csip_aus_mode = True

    raw_dcap = b'<DeviceCapability xmlns="urn:ieee:std:2030.5:ns"/>'
    client = _MockClient(
        {
            "/dcap": _make_dcap(edev_href=None),
            "/tm": _make_time(),
        },
        raw_bodies={"/dcap": raw_dcap},
    )
    await discover(client, state)  # type: ignore[arg-type]

    assert state.csip_aus_mode is False


# --- refresh_der_programs: new program discovery ---


@pytest.mark.asyncio
async def test_refresh_derp_discovers_new_program():
    """refresh_der_programs discovers programs added after initial discovery."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp1 = _make_derp("/derp/1", primacy=5)

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp1),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert len(state.der_programs) == 1
    assert "/derp/1" in state.der_programs

    # Simulate a new program appearing on the server
    derp2 = _make_derp("/derp/2", primacy=10)
    client._responses["/fsa/1/derp"] = _make_derp_list(derp1, derp2)

    await refresh_der_programs(client, state)  # type: ignore[arg-type]

    assert len(state.der_programs) == 2
    assert "/derp/2" in state.der_programs
    assert state.der_programs["/derp/2"].primacy == 10
    assert state.der_programs["/derp/2"].discovered_from_fsa_href == "/fsa/1"
    # Device mapping should include the new program
    assert "/edev/1" in state.device_mapping.program_to_devices["/derp/2"]
    assert "/derp/2" in state.device_mapping.device_to_programs["/edev/1"]


@pytest.mark.asyncio
async def test_refresh_derp_discovers_new_program_from_new_fsa():
    """refresh_der_programs discovers programs from FSAs added after initial discovery."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa1 = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp1 = _make_derp("/derp/1", primacy=5)

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa1),
            "/fsa/1/derp": _make_derp_list(derp1),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert len(state.der_programs) == 1

    # Simulate a new FSA appearing (via refresh_function_set_assignments)
    fsa2 = _make_fsa("/fsa/2", derp_list_href="/fsa/2/derp")
    derp2 = _make_derp("/derp/2", primacy=10)
    client._responses["/edev/1/fsa"] = _make_fsa_list(fsa1, fsa2)
    client._responses["/fsa/2/derp"] = _make_derp_list(derp2)

    # First refresh FSAs to pick up the new one
    await refresh_function_set_assignments(client, state)  # type: ignore[arg-type]
    assert len(state.end_devices["/edev/1"].fsa_list) == 2

    # Then refresh programs to discover the new program
    await refresh_der_programs(client, state)  # type: ignore[arg-type]

    assert len(state.der_programs) == 2
    assert "/derp/2" in state.der_programs
    assert state.der_programs["/derp/2"].primacy == 10


@pytest.mark.asyncio
async def test_refresh_derp_preserves_existing_programs():
    """refresh_der_programs updates existing and discovers new without losing data."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp1 = _make_derp("/derp/1", primacy=5)

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp1),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    # Update primacy of existing program AND add a new one
    updated_derp1 = _make_derp("/derp/1", primacy=3)
    derp2 = _make_derp("/derp/2", primacy=10)
    client._responses["/fsa/1/derp"] = _make_derp_list(updated_derp1, derp2)

    await refresh_der_programs(client, state)  # type: ignore[arg-type]

    # Existing program updated
    assert state.der_programs["/derp/1"].primacy == 3
    # New program discovered
    assert state.der_programs["/derp/2"].primacy == 10


# --- Gap 1: dcap fallback when no FSA ---


@pytest.mark.asyncio
async def test_dcap_fallback_when_no_fsa():
    """Gap 1: When EndDevice has no FSA, discover programs from dcap DERProgramListLink."""
    edev = _make_edev("/edev/1")
    # No FSA link on this device

    derp = _make_derp("/derp/1", primacy=5)

    client = _MockClient(
        {
            "/dcap": _make_dcap(derp_list_href="/dcap/derp"),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/dcap/derp": _make_derp_list(derp),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert "/derp/1" in state.der_programs
    assert state.der_programs["/derp/1"].primacy == 5
    assert state.der_programs["/derp/1"].discovered_from_fsa_href is None


@pytest.mark.asyncio
async def test_dcap_fallback_not_used_when_fsa_present():
    """Gap 1: dcap fallback is NOT used when device has FSAs."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1")

    client = _MockClient(
        {
            "/dcap": _make_dcap(derp_list_href="/dcap/derp"),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert "/derp/1" in state.der_programs
    # Program discovered from FSA, not dcap
    assert state.der_programs["/derp/1"].discovered_from_fsa_href == "/fsa/1"


# --- Gap 3: FSA removal detection ---


@pytest.mark.asyncio
async def test_fsa_removal_returns_affected_programs():
    """Gap 3: refresh_function_set_assignments returns programs from removed FSAs."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa1 = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    fsa2 = _make_fsa("/fsa/2", derp_list_href="/fsa/2/derp")
    derp1 = _make_derp("/derp/1")
    derp2 = _make_derp("/derp/2")

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa1, fsa2),
            "/fsa/1/derp": _make_derp_list(derp1),
            "/fsa/2/derp": _make_derp_list(derp2),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert {"/fsa/1", "/fsa/2"} == state.previous_fsa_hrefs["/edev/1"]

    # Remove fsa/2 on refresh
    client._responses["/edev/1/fsa"] = _make_fsa_list(fsa1)
    removed = await refresh_function_set_assignments(client, state)  # type: ignore[arg-type]

    assert "/derp/2" in removed
    assert "/derp/1" not in removed
    # Snapshot updated
    assert state.previous_fsa_hrefs["/edev/1"] == {"/fsa/1"}


@pytest.mark.asyncio
async def test_fsa_no_removal_returns_empty():
    """Gap 3: No removals returns empty list."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1")

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    # Same FSA on refresh
    removed = await refresh_function_set_assignments(client, state)  # type: ignore[arg-type]
    assert removed == []


# --- Gap 9: Per-FSA Time resource ---


@pytest.mark.asyncio
async def test_time_poll_scheduled_for_an_fsa_only_time_configuration():
    """A server may publish FSA TimeLinks and no DeviceCapability one.

    The poll rate is what causes the Time poll to be scheduled at all. Gating it
    on the global href alone left this deployment with a per-FSA observation
    frozen at discovery -- and that is the offset event classification reads, so
    scheduling silently followed the local clock while nothing looked wrong.
    """
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp", time_href="/fsa/1/tm")
    derp = _make_derp("/derp/1")

    client = _MockClient(
        {
            "/dcap": _make_dcap(time_href=None),  # no global TimeLink
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
            "/fsa/1/tm": _make_time(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert state.time_href is None
    assert "/fsa/1" in state.fsa_time
    assert "time" in state.poll_rates, (
        "an FSA Time resource still needs the Time poll scheduled, or its "
        "observation is never renewed"
    )


@pytest.mark.asyncio
async def test_no_time_anywhere_schedules_no_time_poll():
    """Nothing to refresh means no poll; the guard must not fire unconditionally."""
    client = _MockClient({"/dcap": _make_dcap(time_href=None, edev_href=None)})

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert "time" not in state.poll_rates


@pytest.mark.asyncio
async def test_an_advertised_fsa_time_href_is_kept_when_the_fetch_fails():
    """The advertisement is what schedules the poll, not the reading.

    ``fsa_time`` records only resources actually read, so an FSA Time endpoint
    that is 404 or down during discovery leaves nothing behind. Gating on it
    means the poll is never scheduled, and the endpoint is never asked again
    once it recovers -- the same never-renewed scope, reached a different way.
    """
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp", time_href="/fsa/1/tm")
    derp = _make_derp("/derp/1")

    client = _MockClient(
        {
            "/dcap": _make_dcap(time_href=None),  # no global TimeLink either
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
            "/fsa/1/tm": Sep2ProtocolError("Not found", 404),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert state.fsa_time == {}, "nothing was read, so nothing should be cached"
    assert state.fsa_time_hrefs == {"/fsa/1": "/fsa/1/tm"}
    assert "time" in state.poll_rates, (
        "the poll that would retry this endpoint has to be scheduled, or a "
        "transient failure at discovery is permanent"
    )


@pytest.mark.asyncio
async def test_a_removed_fsa_stops_being_polled_for_time():
    """A withdrawn FSA's Time resource is fetched forever otherwise.

    Its 404 is benign to the Time poll -- the surviving scopes still refresh --
    so nothing escalates it and nothing else removes the entry.
    """
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa1 = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp", time_href="/fsa/1/tm")
    fsa2 = _make_fsa("/fsa/2", derp_list_href="/fsa/2/derp", time_href="/fsa/2/tm")

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa1, fsa2),
            "/fsa/1/derp": _make_derp_list(_make_derp("/derp/1")),
            "/fsa/2/derp": _make_derp_list(_make_derp("/derp/2")),
            "/fsa/1/tm": _make_time(),
            "/fsa/2/tm": _make_time(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert set(state.fsa_time_hrefs) == {"/fsa/1", "/fsa/2"}
    assert "/fsa/2" in client.timebase.snapshot()["per_fsa"]

    client._responses["/edev/1/fsa"] = _make_fsa_list(fsa1)
    await refresh_function_set_assignments(client, state)  # type: ignore[arg-type]

    assert set(state.fsa_time_hrefs) == {"/fsa/1"}
    assert set(state.fsa_time) == {"/fsa/1"}
    assert "/fsa/2" not in client.timebase.snapshot()["per_fsa"]


@pytest.mark.asyncio
async def test_per_fsa_time_discovered():
    """Gap 9: FSA-specific Time resource is fetched during discovery."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp", time_href="/fsa/1/tm")
    derp = _make_derp("/derp/1")
    fsa_time = _make_time()

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
            "/fsa/1/tm": fsa_time,
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert "/fsa/1" in state.fsa_time
    assert state.fsa_time["/fsa/1"] == ("/fsa/1/tm", fsa_time)
    # Can retrieve via program
    assert state.get_fsa_time_for_program("/derp/1") is fsa_time
    # Both the global (dcap) and per-FSA Time fetches feed the timebase.
    snap = client.timebase.snapshot()
    assert snap["global"] is not None and snap["global"]["href"] == "/tm"
    assert "/fsa/1" in snap["per_fsa"]
    assert snap["per_fsa"]["/fsa/1"]["href"] == "/fsa/1/tm"


@pytest.mark.asyncio
async def test_fsa_without_time_link():
    """Gap 9: FSA without TimeLink is handled gracefully."""
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")  # No time_href
    derp = _make_derp("/derp/1")

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert state.fsa_time == {}
    assert state.get_fsa_time_for_program("/derp/1") is None


# --- Crash-loop resilience: unparseable DERControl/DERCurve ---


@pytest.mark.asyncio
async def test_discover_continues_on_derc_parse_error():
    """Discovery skips unparseable DERControl list without crashing.

    Regression test: a malformed DERControl on the server
    caused xsdata/Pydantic validation errors during get_list, which
    propagated as unhandled exceptions and crash-looped the aggregator.
    """
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1", primacy=5)
    # Give the program a DERControlListLink that will fail to parse
    derp.dercontrol_list_link = DercontrolListLink(href="/derp/1/derc")

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
            # Simulate a parse/validation error on the DERC list
            "/derp/1/derc": ValueError("Pydantic validation failed for DERControlBase"),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    # Discovery completed successfully
    assert "/edev/1" in state.end_devices
    assert "/derp/1" in state.der_programs
    # Program was discovered but has no controls (they were unparseable)
    assert state.der_programs["/derp/1"].der_controls == []
    assert state.der_programs["/derp/1"].primacy == 5


@pytest.mark.asyncio
async def test_refresh_derc_continues_on_parse_error():
    """refresh_der_controls skips unparseable DERC list without crashing.

    Same resilience as discover() but for the periodic refresh path.
    """
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1", primacy=5)
    derp.dercontrol_list_link = DercontrolListLink(href="/derp/1/derc")

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
            # Initial discovery succeeds with empty DERC list
            "/derp/1/derc": Sep2ProtocolError("Not found", 404),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert "/derp/1" in state.der_programs

    # Now simulate a parse error during refresh (bad control appeared on server)
    client._responses["/derp/1/derc"] = ValueError("xsdata parse failure")
    await refresh_der_controls(client, state)  # type: ignore[arg-type]

    # Refresh completed without crashing; controls remain empty
    assert state.der_programs["/derp/1"].der_controls == []


@pytest.mark.asyncio
async def test_refresh_clears_stale_dderc_when_link_removed():
    """refresh_der_controls clears cached DDERC when the server removes the link.

    If refresh_der_programs updates a program so that default_dercontrol_link
    is None (server no longer advertises a DDERC), refresh_der_controls must
    clear the previously cached default_dercontrol rather than leaving it stale.
    """
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1", primacy=5)
    derp.default_dercontrol_link = DefaultDercontrolLink(href="/derp/1/dderc")

    dderc = DefaultDercontrol(
        m_rid=MRidtype(value=b"\x00" * 16),
        dercontrol_base=DercontrolBase(),
    )

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
            "/derp/1/dderc": dderc,
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert state.der_programs["/derp/1"].default_dercontrol is dderc

    # Simulate what refresh_der_programs does: update the program object
    # so default_dercontrol_link is gone (server removed it).
    state.der_programs["/derp/1"].program.default_dercontrol_link = None

    await refresh_der_controls(client, state)  # type: ignore[arg-type]

    # The cached DDERC must be cleared, not left stale
    assert state.der_programs["/derp/1"].default_dercontrol is None


@pytest.mark.asyncio
async def test_refresh_der_controls_also_refreshes_curves():
    """When the server creates a new DERCurve and DERControl in quick
    succession (the website's two-step flow), the aggregator's cached
    curve list must be refreshed alongside the control list. Otherwise
    ``_find_curve`` in modes.translate_qv returns None and the mode
    silently falls back to ``qv_mode_enable: 0``. Regression test."""
    from py20305.models.sep.sep import (
        Dercurve1,
        DercurveList,
        DercurveListLink,
        DercurveType,
        DerunitRefType,
        PowerOfTenMultiplierType,
    )

    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")

    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1", primacy=5)
    derp.dercurve_list_link = DercurveListLink(href="/derp/1/dc")

    # Curve list is empty at initial discovery.
    initial_curves = DercurveList(**{"all": 0, "results": 0})
    initial_curves.dercurve = []

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
            "/derp/1/dc": initial_curves,
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert state.der_programs["/derp/1"].der_curves == []

    # Server creates a new DERCurve; refresh_der_controls must pick it up.
    new_curve = Dercurve1(
        m_rid=MRidtype(value=b"\xab" * 16),
        creation_time=TimeType(value=0),
        curve_type=DercurveType(value=11),
        x_multiplier=PowerOfTenMultiplierType(value=-2),
        y_multiplier=PowerOfTenMultiplierType(value=-2),
        y_ref_type=DerunitRefType(value=2),
    )
    new_curve.href = "/derp/1/dc/1"
    refreshed_curves = DercurveList(**{"all": 1, "results": 1})
    refreshed_curves.dercurve = [new_curve]
    client._responses["/derp/1/dc"] = refreshed_curves

    await refresh_der_controls(client, state)  # type: ignore[arg-type]

    cached = state.der_programs["/derp/1"].der_curves
    assert len(cached) == 1
    assert cached[0].href == "/derp/1/dc/1"


# --- Registration PIN verification ---


def _edev_with_registration(href: str, lfdi: bytes, reg_href: str) -> EndDevice1:
    edev = _make_edev(href, lfdi=lfdi)
    edev.registration_link = RegistrationLink(href=reg_href)
    return edev


def _registration(pin: int) -> Registration:
    return Registration(date_time_registered=TimeType(value=0), p_in=Pintype(value=pin))


@pytest.mark.asyncio
async def test_registration_pin_match_logs_verified(caplog):
    """Configured PIN matching the server's Registration logs verification."""
    lfdi = b"\xab" * 20
    edev = _edev_with_registration("/edev/1", lfdi, "/edev/1/rg")
    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/rg": _registration(111115),
        }
    )
    state = DiscoveredState()
    with caplog.at_level(logging.INFO, logger="py20305.client.discovery"):
        await discover(client, state, registration_pins={lfdi.hex(): 111115})  # type: ignore[arg-type]

    assert "/edev/1" in state.end_devices
    assert any("PIN verified" in r.message for r in caplog.records)
    assert not any("mismatch" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_registration_pin_mismatch_logs_warning(caplog):
    """Configured PIN differing from the server's Registration logs a warning."""
    lfdi = b"\xcd" * 20
    edev = _edev_with_registration("/edev/1", lfdi, "/edev/1/rg")
    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/rg": _registration(111115),
        }
    )
    state = DiscoveredState()
    with caplog.at_level(logging.WARNING, logger="py20305.client.discovery"):
        await discover(client, state, registration_pins={lfdi.hex(): 999999})  # type: ignore[arg-type]

    mismatch = [r for r in caplog.records if "PIN mismatch" in r.message]
    assert mismatch, "expected a PIN mismatch warning"
    assert mismatch[0].levelno == logging.WARNING


@pytest.mark.asyncio
async def test_registration_pin_unconfigured_lfdi_skips_fetch():
    """A device whose LFDI is absent from the config map is not verified, and
    its Registration resource is not even fetched (no /edev/1/rg in the mock,
    so a fetch would raise ValueError)."""
    lfdi = b"\xef" * 20
    edev = _edev_with_registration("/edev/1", lfdi, "/edev/1/rg")
    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
        }
    )
    state = DiscoveredState()
    # Map keyed by a different LFDI -> this edev is skipped before any fetch.
    await discover(client, state, registration_pins={"00" * 20: 111115})  # type: ignore[arg-type]

    assert "/edev/1" in state.end_devices
    assert state.end_devices["/edev/1"].registration_href == "/edev/1/rg"


@pytest.mark.asyncio
async def test_registration_pin_no_map_skips_fetch():
    """With no registration_pins, the Registration resource is never fetched."""
    lfdi = b"\xab" * 20
    edev = _edev_with_registration("/edev/1", lfdi, "/edev/1/rg")
    # Note: no "/edev/1/rg" entry -- a fetch would raise ValueError in _MockClient.
    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
        }
    )
    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    assert "/edev/1" in state.end_devices
    assert state.end_devices["/edev/1"].registration_href == "/edev/1/rg"


# ---------------------------------------------------------------------------
# SelfDevice discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_device_discovered_with_der_children():
    """SelfDevice and its DER child hrefs are recorded on the state."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": _make_self_device(),
            "/sdev/der": _make_der_list(
                _make_der(
                    "/sdev/der/1",
                    settings_href="/sdev/der/1/derg",
                    capability_href="/sdev/der/1/dercap",
                    status_href="/sdev/der/1/ders",
                    availability_href="/sdev/der/1/dera",
                )
            ),
            "/edev": _make_edev_list(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)

    assert state.self_device is not None
    assert state.self_device.href == "/sdev"
    assert len(state.self_device.ders) == 1
    assert state.self_device.der_settings_href == "/sdev/der/1/derg"
    assert state.self_device.der_capability_href == "/sdev/der/1/dercap"
    assert state.self_device.der_status_href == "/sdev/der/1/ders"
    assert state.self_device.der_availability_href == "/sdev/der/1/dera"


@pytest.mark.asyncio
async def test_self_device_absent_when_no_link():
    """No SelfDeviceLink leaves state.self_device None and discovery succeeds."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)

    assert state.self_device is None
    assert "sdev" not in state.poll_rates


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 204])
async def test_self_device_missing_resource_is_not_fatal(status: int):
    """A linked but absent SelfDevice is skipped, not raised."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": Sep2ProtocolError("absent", status_code=status),
            "/edev": _make_edev_list(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)

    assert state.self_device is None


@pytest.mark.asyncio
async def test_self_device_server_error_does_not_stop_discovery(caplog):
    """A 500 on the SelfDevice GET is contained: the rest of discovery runs.

    SelfDevice is informational and nothing downstream depends on it, so a
    server fault there must not cost the caller its control path. The fault
    is logged at error rather than swallowed silently.
    """
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": Sep2ProtocolError("boom", status_code=500),
            "/edev": _make_edev_list(_make_edev("/edev/1")),
        }
    )

    state = DiscoveredState()
    with caplog.at_level(logging.ERROR):
        await discover(client, state)

    assert state.self_device is None
    assert "sdev" not in state.poll_rates
    assert "/edev/1" in state.end_devices
    assert "SelfDevice at /sdev failed to read" in caplog.text


@pytest.mark.asyncio
async def test_self_device_der_list_server_error_keeps_device(caplog):
    """A 500 on the SelfDevice DER list keeps the device and continues."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": _make_self_device(),
            "/sdev/der": Sep2ProtocolError("boom", status_code=500),
            "/edev": _make_edev_list(_make_edev("/edev/1")),
        }
    )

    state = DiscoveredState()
    with caplog.at_level(logging.ERROR):
        await discover(client, state)

    assert state.self_device is not None
    assert state.self_device.der_settings_href is None
    assert "/edev/1" in state.end_devices
    assert "SelfDevice DER list at /sdev/der failed to read" in caplog.text


@pytest.mark.asyncio
async def test_self_device_payload_error_does_not_stop_discovery():
    """A malformed SelfDevice payload is skipped, not fatal."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": Sep2PayloadError("bad xml", path="/sdev", body_length=9),
            "/edev": _make_edev_list(_make_edev("/edev/1")),
        }
    )

    state = DiscoveredState()
    await discover(client, state)

    assert state.self_device is None
    assert "/edev/1" in state.end_devices


@pytest.mark.asyncio
async def test_self_device_without_der_list_link():
    """SelfDevice with no DERListLink is still recorded, with no child hrefs."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": _make_self_device(der_list_href=None),
            "/edev": _make_edev_list(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)

    assert state.self_device is not None
    assert state.self_device.ders == []
    assert state.self_device.der_settings_href is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 204])
async def test_self_device_der_list_missing_keeps_device(status: int):
    """An absent SelfDevice DER list keeps the device but leaves hrefs unset."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": _make_self_device(),
            "/sdev/der": Sep2ProtocolError("absent", status_code=status),
            "/edev": _make_edev_list(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)

    assert state.self_device is not None
    assert state.self_device.der_settings_href is None


@pytest.mark.asyncio
async def test_self_device_multi_der_list_uses_first_entry_only(caplog):
    """A multi-entry DERList is non-conformant: take entry one, warn, never mix.

    IEEE 2030.5-2023 permits a single DER per DERList (Annex B, DERList,
    p. 250), so composing child hrefs from different entries would describe
    a DER that does not exist. DER 2's settings link is therefore ignored
    even though DER 1 does not advertise one.
    """
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": _make_self_device(),
            "/sdev/der": _make_der_list(
                _make_der("/sdev/der/1", capability_href="/sdev/der/1/dercap"),
                _make_der(
                    "/sdev/der/2",
                    settings_href="/sdev/der/2/derg",
                    capability_href="/sdev/der/2/dercap",
                ),
            ),
            "/edev": _make_edev_list(),
        }
    )

    state = DiscoveredState()
    with caplog.at_level(logging.WARNING):
        await discover(client, state)

    assert state.self_device is not None
    assert state.self_device.der_capability_href == "/sdev/der/1/dercap"
    assert state.self_device.der_settings_href is None
    assert "holds 2 DER entries" in caplog.text


@pytest.mark.asyncio
async def test_self_device_subscribable_flags_recorded():
    """Subscribability of the SelfDevice and its DER is captured at discovery."""
    sdev = _make_self_device()
    sdev.subscribable = 1
    der = _make_der("/sdev/der/1", settings_href="/sdev/der/1/derg")
    der.subscribable = 1
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": sdev,
            "/sdev/der": _make_der_list(der),
            "/edev": _make_edev_list(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)

    assert state.self_device is not None
    assert state.self_device.subscribable is True
    assert state.self_device.der_subscribable is True


@pytest.mark.asyncio
async def test_self_device_poll_rate_recorded():
    """The SelfDevice pollRate lands in state.poll_rates under 'sdev'."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": _make_self_device(poll_rate=30),
            "/sdev/der": _make_der_list(),
            "/edev": _make_edev_list(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)

    assert state.poll_rates["sdev"] == 30


@pytest.mark.asyncio
async def test_self_device_without_poll_rate_records_the_schema_default():
    """No pollRate on the wire still records 900: SelfDevice declares that default.

    ``poll_rates["sdev"]`` therefore cannot distinguish a server-stated 900
    from silence, which matches how the dcap rate behaves.
    """
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": _make_self_device(),
            "/sdev/der": _make_der_list(),
            "/edev": _make_edev_list(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)

    assert state.poll_rates["sdev"] == DEFAULT_POLL_RATE


def _edev_with_der_list(href: str = "/edev/1", der_list_href: str = "/edev/1/der") -> EndDevice1:
    edev = _make_edev(href)
    edev.derlist_link = DerlistLink(href=der_list_href)
    return edev


@pytest.mark.asyncio
async def test_end_device_der_list_uses_first_entry_only(caplog):
    """The EndDevice DER walk selects one DER too, on the same IEEE grounds."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(_edev_with_der_list()),
            "/edev/1/der": _make_der_list(
                _make_der("/edev/1/der/1", capability_href="/edev/1/der/1/dercap"),
                _make_der("/edev/1/der/2", settings_href="/edev/1/der/2/derg"),
            ),
        }
    )

    state = DiscoveredState()
    with caplog.at_level(logging.WARNING):
        await discover(client, state)

    edev_state = state.end_devices["/edev/1"]
    assert edev_state.der_capability_href == "/edev/1/der/1/dercap"
    assert edev_state.der_settings_href is None
    assert "holds 2 DER entries" in caplog.text


@pytest.mark.asyncio
async def test_multi_der_gaps_filled_from_later_entries_in_2018_compat():
    """A 2018 server may spread child links across DER entries; honor that.

    Previous revisions of IEEE 2030.5 allowed several DER objects, so under
    ``server_2018_compat`` a link the first entry omits is taken from a later
    one rather than dropped.
    """
    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(_edev_with_der_list()),
            "/edev/1/der": _make_der_list(
                _make_der("/edev/1/der/1", capability_href="/edev/1/der/1/dercap"),
                _make_der(
                    "/edev/1/der/2",
                    settings_href="/edev/1/der/2/derg",
                    availability_href="/edev/1/der/2/dera",
                ),
            ),
        },
        server_2018_compat=True,
    )

    state = DiscoveredState()
    await discover(client, state)

    edev_state = state.end_devices["/edev/1"]
    assert edev_state.der_capability_href == "/edev/1/der/1/dercap"
    assert edev_state.der_settings_href == "/edev/1/der/2/derg"
    assert edev_state.der_availability_href == "/edev/1/der/2/dera"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(Sep2ProtocolError("boom", status_code=500), id="server-error"),
        pytest.param(
            Sep2PayloadError("bad xml", path="/edev/1/der", body_length=9), id="malformed-body"
        ),
    ],
)
async def test_end_device_der_list_failure_is_contained(failure: Exception, caplog):
    """An unreadable EndDevice DER list costs that device's PUTs, not discovery.

    Continuing leaves the DER hrefs unset, which stops DER resource PUTs for
    this device only (der_resource_manager skips None hrefs). Aborting would
    cost every other device plus the control path, so the fault is logged at
    error and the walk proceeds.
    """
    client = _MockClient(
        {
            "/dcap": _make_dcap(derp_list_href="/derp"),
            "/tm": _make_time(),
            "/edev": _make_edev_list(_edev_with_der_list()),
            "/edev/1/der": failure,
            "/derp": _make_derp_list(_make_derp("/derp/1")),
        }
    )

    state = DiscoveredState()
    with caplog.at_level(logging.ERROR):
        await discover(client, state)

    edev_state = state.end_devices["/edev/1"]
    assert edev_state.der_capability_href is None
    assert edev_state.der_settings_href is None
    # Discovery still completed: the program walk ran after the failure.
    assert "/derp/1" in state.der_programs
    assert "no DER resource PUTs for this device" in caplog.text


@pytest.mark.asyncio
async def test_self_device_cleared_on_rediscovery():
    """state.clear() drops a previously discovered SelfDevice."""
    client = _MockClient(
        {
            "/dcap": _make_dcap(sdev_href="/sdev"),
            "/tm": _make_time(),
            "/sdev": _make_self_device(der_list_href=None),
            "/edev": _make_edev_list(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)
    assert state.self_device is not None

    state.clear()
    assert state.self_device is None


@pytest.mark.asyncio
async def test_refresh_derp_maps_a_device_onto_a_known_program():
    """A device gaining an assignment for a known program must reach it.

    The refresh branched on whether the *program* was new, but the entry that
    needs creating is the *(program, device)* pair. A device whose new function
    set assignment named a program the client already knew took the update
    branch, its pair was never recorded, and it received none of that program's
    controls until a full discovery rebuilt the mapping.

    Driven the way the poll loop reaches it -- ``_do_poll_fsa`` and then
    ``_do_poll_derp`` -- because a *new* device cannot arrive through a refresh
    at all: ``refresh_end_device_lists`` only updates devices already known, and
    the sole place a new entry is created is full ``discover()``, which rebuilds
    the mapping from scratch and never had this bug.
    """
    edev1 = _make_edev("/edev/1")
    edev1.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")
    edev2 = _make_edev("/edev/2", lfdi=bytes([0xBB]) * 20)
    edev2.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/2/fsa")

    fsa1 = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1", primacy=5)

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev1, edev2),
            "/edev/1/fsa": _make_fsa_list(fsa1),
            "/fsa/1/derp": _make_derp_list(derp),
            # The second device carries no assignment yet.
            "/edev/2/fsa": _make_fsa_list(),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]
    assert state.device_mapping.program_to_devices["/derp/1"] == ["/edev/1"]

    # The server gives the second device an assignment naming the same program.
    client._responses["/edev/2/fsa"] = _make_fsa_list(
        _make_fsa("/fsa/2", derp_list_href="/fsa/2/derp")
    )
    client._responses["/fsa/2/derp"] = _make_derp_list(derp)

    await refresh_function_set_assignments(client, state)  # type: ignore[arg-type]
    await refresh_der_programs(client, state)  # type: ignore[arg-type]

    assert state.device_mapping.program_to_devices["/derp/1"] == ["/edev/1", "/edev/2"]
    assert state.device_mapping.device_to_programs["/edev/2"] == ["/derp/1"]


@pytest.mark.asyncio
async def test_repeated_refreshes_do_not_grow_the_mapping():
    """The reason this could not be fixed before the mapping admitted a pair once.

    An unconditional add on a list that appends would grow by one entry per
    poll, and that list is the dispatch target list.
    """
    edev = _make_edev("/edev/1")
    edev.function_set_assignments_list_link = FunctionSetAssignmentsListLink(href="/edev/1/fsa")
    fsa = _make_fsa("/fsa/1", derp_list_href="/fsa/1/derp")
    derp = _make_derp("/derp/1", primacy=5)

    client = _MockClient(
        {
            "/dcap": _make_dcap(),
            "/tm": _make_time(),
            "/edev": _make_edev_list(edev),
            "/edev/1/fsa": _make_fsa_list(fsa),
            "/fsa/1/derp": _make_derp_list(derp),
        }
    )

    state = DiscoveredState()
    await discover(client, state)  # type: ignore[arg-type]

    for _ in range(5):
        await refresh_der_programs(client, state)  # type: ignore[arg-type]

    assert state.device_mapping.program_to_devices["/derp/1"] == ["/edev/1"]
    assert state.device_mapping.device_to_programs["/edev/1"] == ["/derp/1"]
