"""Aggregations: an EndDevice that represents several devices (IEEE 2030.5-2023 §8.7, Annex C.3)."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from py20305.client.aggregation import (
    AggregatedDeviceInfo,
    AggregationInfo,
    distribution_of,
    is_aggregation,
    read_aggregation,
)
from py20305.client.csip_client import CsipClient
from py20305.client.discovery import discover, refresh_end_device_lists
from py20305.client.errors import Sep2ProtocolError
from py20305.client.state import DiscoveredState, EndDeviceState
from py20305.client.timebase import ServerTimebase
from py20305.models.sep.sep import (
    AggregatedDevice,
    AggregatedDeviceList,
    AggregatedDeviceListLink,
    AggregationDistributionType,
    AggregationPriority,
    AggregationPriorityLink,
    DeviceCapability,
    EndDevice1,
    EndDeviceList,
    EndDeviceListLink,
    MRidtype,
    PriorityData,
    Sfditype,
    Time,
    TimeLink,
    TimeOffsetType,
    TimeType,
)
from py20305.server_profile import Edition, ServerProfile

AGG = bytes.fromhex("a9" * 20)
MEMBER_A = "bff864bdf8f9eb91a9ec03e7f66ecf2844fe968e"
MEMBER_B = "5c6be97627820d7de91f22ad89f3e463bc6ed2c2"


def _edev(
    *,
    distribution: int | None = None,
    adev: bool = False,
    aggp: bool = False,
    href: str = "/edev/3",
    lfdi: bytes = AGG,
) -> EndDevice1:
    edev = EndDevice1(s_fdi=Sfditype(value=987654321005), changed_time=TimeType(value=0))
    edev.href = href
    edev.l_fdi = lfdi
    if distribution is not None:
        edev.distribution = AggregationDistributionType(value=distribution)
    if adev:
        edev.aggregated_device_list_link = AggregatedDeviceListLink(href=f"{href}/adev", all=2)
    if aggp:
        edev.aggregation_priority_link = AggregationPriorityLink(href=f"{href}/aggp")
    return edev


def _member(lfdi: str, sfdi: int, *, enabled: bool | None = None, index: int = 0) -> Any:
    device = AggregatedDevice(
        l_fdi=bytes.fromhex(lfdi),
        s_fdi=Sfditype(value=sfdi),
        changed_time=TimeType(value=1379905200),
        enabled=enabled,
    )
    device.href = f"/edev/3/adev/{index}"
    return device


def _adev_list(*devices: Any, poll_rate: int = 300) -> AggregatedDeviceList:
    listing = AggregatedDeviceList(**{"all": len(devices), "results": len(devices)})
    listing.aggregated_device = list(devices)
    listing.poll_rate = poll_rate
    return listing


def _priority(*lfdis: str) -> AggregationPriority:
    return AggregationPriority(
        m_rid=MRidtype(value=bytes.fromhex("5B4FF9B708CF41BB83C49F6E00007ED9")),
        priority_data=[PriorityData(l_fdi=bytes.fromhex(lfdi)) for lfdi in lfdis],
    )


class _Client:
    """A Sep2Client stand-in answering GETs from a table; an exception value is raised."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.timebase = ServerTimebase()
        self.profile = ServerProfile(base=Edition.E2023)
        self.discovered_devices: dict[str, str] = {}

    def update_discovered_devices(self, prefix_map: dict[str, str]) -> None:
        self.discovered_devices = dict(prefix_map)

    def observe_device_capability(self, body: bytes) -> ServerProfile:
        return self.profile

    async def get(self, path: str, model_type: type) -> Any:
        self.calls.append(path)
        value = self.responses[path.split("?")[0]]
        if isinstance(value, Exception):
            raise value
        return value

    async def get_with_body(self, path: str, model_type: type) -> tuple[Any, bytes]:
        return await self.get(path, model_type), b"<default/>"

    async def get_list(self, path: str, model_type: type) -> list[Any]:
        result = await self.get(path, model_type)
        return result if isinstance(result, list) else [result]


class TestReadAggregation:
    async def test_an_enddevice_without_aggregation_fields_is_no_aggregation(self):
        client = _Client({})
        assert await read_aggregation(client, _edev()) is None  # type: ignore[arg-type]
        assert client.calls == []

    async def test_the_annex_c3_flow_reads_the_list_then_the_priority(self):
        client = _Client(
            {
                "/edev/3/adev": _adev_list(
                    _member(MEMBER_A, 515316315839), _member(MEMBER_B, 248092158425, index=1)
                ),
                "/edev/3/aggp": _priority(MEMBER_A, MEMBER_B),
            }
        )
        edev = _edev(distribution=3, adev=True, aggp=True)
        info = await read_aggregation(client, edev)  # type: ignore[arg-type]
        assert client.calls == ["/edev/3/adev", "/edev/3/aggp"]
        assert info is not None
        assert info.distribution == 3
        assert info.priority == (MEMBER_A, MEMBER_B)
        assert info.enabled_members == (MEMBER_A, MEMBER_B)
        assert info.members[0] == AggregatedDeviceInfo(
            lfdi=MEMBER_A,
            sfdi=515316315839,
            enabled=True,
            href="/edev/3/adev/0",
            changed_time=1379905200,
        )
        assert info.list_poll_rate == 300

    async def test_a_member_the_server_disabled_is_reported_as_disabled(self):
        client = _Client(
            {
                "/edev/3/adev": _adev_list(
                    _member(MEMBER_A, 1, enabled=True), _member(MEMBER_B, 2, enabled=False)
                )
            }
        )
        info = await read_aggregation(client, _edev(distribution=2, adev=True))  # type: ignore[arg-type]
        assert info is not None
        assert info.enabled_members == (MEMBER_A,)
        assert info.disabled_members == (MEMBER_B,)

    async def test_the_priority_is_not_read_without_a_priority_distribution(self, caplog):
        client = _Client({"/edev/3/aggp": _priority(MEMBER_A)})
        with caplog.at_level(logging.INFO, logger="py20305.client.aggregation"):
            info = await read_aggregation(client, _edev(distribution=1, aggp=True))  # type: ignore[arg-type]
        assert client.calls == []
        assert info is not None
        assert info.priority == ()
        assert "without a Priority distribution" in caplog.text

    async def test_a_priority_distribution_without_a_priority_link_is_logged(self, caplog):
        client = _Client({})
        with caplog.at_level(logging.WARNING, logger="py20305.client.aggregation"):
            info = await read_aggregation(client, _edev(distribution=3))  # type: ignore[arg-type]
        assert info == AggregationInfo(distribution=3)
        assert "no AggregationPriorityLink" in caplog.text

    async def test_a_list_the_server_does_not_serve_leaves_the_members_empty(self):
        client = _Client({"/edev/3/adev": Sep2ProtocolError(404, "/edev/3/adev")})
        info = await read_aggregation(client, _edev(distribution=1, adev=True))  # type: ignore[arg-type]
        assert info is not None
        assert info.members == ()
        assert info.distribution == 1

    async def test_a_priority_that_cannot_be_read_leaves_the_order_empty(self):
        client = _Client({"/edev/3/aggp": Sep2ProtocolError(500, "/edev/3/aggp")})
        info = await read_aggregation(client, _edev(distribution=3, aggp=True))  # type: ignore[arg-type]
        assert info is not None
        assert info.priority == ()

    def test_the_distribution_is_read_from_the_enddevice(self):
        assert distribution_of(_edev(distribution=2)) == 2
        assert distribution_of(_edev()) is None

    def test_an_unspecified_distribution_alone_is_no_aggregation(self):
        assert is_aggregation(_edev(distribution=0)) is False
        assert is_aggregation(_edev(distribution=0, adev=True)) is True


def _discovery_client(edev: EndDevice1, **extra: Any) -> _Client:
    dcap = DeviceCapability()
    dcap.end_device_list_link = EndDeviceListLink(href="/edev")
    dcap.time_link = TimeLink(href="/tm")
    listing = EndDeviceList(**{"all": 1, "results": 1})
    listing.end_device = [edev]
    time = Time(
        current_time=TimeType(value=1_700_000_000),
        dst_end_time=TimeType(value=0),
        dst_offset=TimeOffsetType(value=0),
        dst_start_time=TimeType(value=0),
        quality=7,
        tz_offset=TimeOffsetType(value=0),
    )
    return _Client({"/dcap": dcap, "/tm": time, "/edev": listing, **extra})


class TestDiscovery:
    async def test_discovery_keeps_the_aggregation_on_the_enddevice_state(self):
        client = _discovery_client(
            _edev(distribution=2, adev=True), **{"/edev/3/adev": _adev_list(_member(MEMBER_A, 1))}
        )
        state = DiscoveredState()
        await discover(client, state)  # type: ignore[arg-type]
        aggregation = state.end_devices["/edev/3"].aggregation
        assert aggregation is not None
        assert aggregation.distribution == 2
        assert aggregation.enabled_members == (MEMBER_A,)

    async def test_an_ordinary_enddevice_has_no_aggregation(self):
        state = DiscoveredState()
        await discover(_discovery_client(_edev()), state)  # type: ignore[arg-type]
        assert state.end_devices["/edev/3"].aggregation is None

    async def test_an_enddevice_list_refresh_reads_the_aggregation_again(self):
        client = _discovery_client(
            _edev(distribution=1, adev=True), **{"/edev/3/adev": _adev_list(_member(MEMBER_A, 1))}
        )
        state = DiscoveredState()
        await discover(client, state)  # type: ignore[arg-type]
        listing = EndDeviceList(**{"all": 1, "results": 1})
        listing.end_device = [_edev(distribution=3, adev=True, aggp=True)]
        client.responses["/edev"] = listing
        client.responses["/edev/3/adev"] = _adev_list(
            _member(MEMBER_A, 1, enabled=False), _member(MEMBER_B, 2, index=1)
        )
        client.responses["/edev/3/aggp"] = _priority(MEMBER_B, MEMBER_A)
        await refresh_end_device_lists(client, state)  # type: ignore[arg-type]
        aggregation = state.end_devices["/edev/3"].aggregation
        assert aggregation is not None
        assert aggregation.distribution == 3
        assert aggregation.priority == (MEMBER_B, MEMBER_A)
        assert aggregation.disabled_members == (MEMBER_A,)


class TestClient:
    def test_the_aggregation_is_found_by_lfdi(self):
        client = CsipClient("https://example.com")
        info = AggregationInfo(distribution=1)
        client.state.end_devices["/edev/3"] = EndDeviceState(
            device=_edev(distribution=1), href="/edev/3", lfdi=AGG, aggregation=info
        )
        assert client.aggregation(AGG.hex().upper()) is info
        assert client.aggregation("00" * 20) is None

    async def test_registering_a_member_posts_an_aggregated_device(self):
        client = CsipClient("https://example.com")
        client._http.get_list = AsyncMock(return_value=[_adev_list()])  # type: ignore[method-assign]
        client._http.post = AsyncMock(return_value="/edev/3/adev/2")  # type: ignore[method-assign]
        location = await client.register_aggregated_device(
            aggregated_device_list_href="/edev/3/adev", lfdi=MEMBER_A, device_category=0x800000
        )
        assert location == "/edev/3/adev/2"
        # §8.7.3.2: the duplicate check filters by the member's sFDI.
        query = client._http.get_list.await_args.args[0]
        assert query.startswith("/edev/3/adev?sFDI=")
        path, device = client._http.post.await_args.args
        assert path == "/edev/3/adev"
        assert device.l_fdi == bytes.fromhex(MEMBER_A)
        assert device.device_category.value == bytes.fromhex("00800000")

    async def test_a_member_the_list_already_holds_is_not_posted_again(self):
        client = CsipClient("https://example.com")
        listed = _adev_list(_member(MEMBER_A, 1))
        client._http.get_list = AsyncMock(return_value=[listed])  # type: ignore[method-assign]
        client._http.post = AsyncMock()  # type: ignore[method-assign]
        with pytest.raises(ValueError, match="already listed"):
            await client.register_aggregated_device(
                aggregated_device_list_href="/edev/3/adev", lfdi=MEMBER_A
            )
        client._http.post.assert_not_awaited()

    async def test_a_process_without_own_enddevice_duties_does_not_register(self):
        client = CsipClient("https://example.com")
        client._own_end_device = False
        with pytest.raises(RuntimeError):
            await client.register_aggregated_device(
                aggregated_device_list_href="/edev/3/adev", lfdi=MEMBER_A
            )
