"""Tests for the Pricing Info API: ClientAPIService.get_tariffs + serializer."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from py20305.api.serializers import _tariff_interval_status, serialize_tariff_profile
from py20305.api.service import ClientAPIService

HOUR = 3600


def _interval(
    mrid=b"\xaa", start=1000, duration=HOUR, *, tou=0, status=0, cti_href="/tp/1/rc/1/tti/1/cti"
):
    return SimpleNamespace(
        m_rid=SimpleNamespace(value=mrid),
        interval=SimpleNamespace(start=SimpleNamespace(value=start), duration=duration),
        event_status=SimpleNamespace(current_status=status),
        tou_tier=tou,
        consumption_tariff_interval_list_link=SimpleNamespace(href=cti_href),
    )


def _profile_state(intervals):
    profile = SimpleNamespace(
        currency=840,
        price_power_of_ten_multiplier=-6,
        rate_code="RTP",
        description="Test tariff",
    )
    rc = SimpleNamespace(href="/tp/1/rc/1", time_tariff_intervals=intervals)
    return SimpleNamespace(
        profile=profile,
        href="/tp/1",
        primacy=1,
        rate_components=[rc],
        discovered_from_fsa_href=None,
    )


def _cti_page(price=250000, start_value=0, block=0):
    return SimpleNamespace(
        consumption_tariff_interval=[
            SimpleNamespace(price=price, start_value=start_value, consumption_block=block)
        ]
    )


def _service(tariff_profiles, *, get_list=None):
    client = MagicMock()
    client.state.tariff_profiles = tariff_profiles
    client.http = MagicMock()
    client.http.get_list = get_list or AsyncMock(return_value=[_cti_page()])
    client.http.timebase = object()  # not a ServerTimebase -> falls back to wall clock
    return ClientAPIService(client=client)


class TestGetTariffs:
    @pytest.mark.asyncio
    async def test_empty_state_returns_no_profiles(self):
        service = _service({})
        assert await service.get_tariffs() == {"profiles": []}

    @pytest.mark.asyncio
    async def test_serializes_profile_and_fetches_prices(self):
        service = _service({"/tp/1": _profile_state([_interval()])})
        result = await service.get_tariffs()

        assert len(result["profiles"]) == 1
        profile = result["profiles"][0]
        assert profile["href"] == "/tp/1"
        assert profile["currency"] == 840
        assert profile["priceMultiplier"] == -6
        assert profile["rateCode"] == "RTP"
        interval = profile["rateComponents"][0]["intervals"][0]
        assert interval["mrid"] == "aa"
        assert interval["prices"] == [{"price": 250000, "startValue": 0, "consumptionBlock": 0}]
        # start=1000 is far in the past -> Completed regardless of wall clock.
        assert interval["status"] == "completed"

    @pytest.mark.asyncio
    async def test_price_fetch_failure_degrades_to_empty(self):
        service = _service(
            {"/tp/1": _profile_state([_interval()])},
            get_list=AsyncMock(side_effect=RuntimeError("boom")),
        )
        result = await service.get_tariffs()
        interval = result["profiles"][0]["rateComponents"][0]["intervals"][0]
        assert interval["prices"] == []  # interval still present, just unpriced

    @pytest.mark.asyncio
    async def test_interval_without_cti_link_has_no_prices(self):
        service = _service({"/tp/1": _profile_state([_interval(cti_href=None)])})
        get_list = service._client.http.get_list
        result = await service.get_tariffs()
        assert result["profiles"][0]["rateComponents"][0]["intervals"][0]["prices"] == []
        get_list.assert_not_awaited()  # no link -> no fetch


class TestTariffIntervalStatus:
    def test_scheduled_active_completed(self):
        iv = _interval(start=1000, duration=HOUR)
        assert _tariff_interval_status(iv, now=500) == "scheduled"
        assert _tariff_interval_status(iv, now=1000 + 10) == "active"
        assert _tariff_interval_status(iv, now=1000 + HOUR) == "completed"

    def test_cancelled_and_superseded_preserved(self):
        assert _tariff_interval_status(_interval(status=2), now=1000 + 10) == "cancelled"
        assert _tariff_interval_status(_interval(status=4), now=1000 + 10) == "superseded"

    def test_serialize_flags_active_interval(self):
        tp = _profile_state([_interval(start=1000, duration=HOUR)])
        out = serialize_tariff_profile(tp, {"aa": [{"price": 1}]}, now=1000 + 10)
        assert out["rateComponents"][0]["intervals"][0]["status"] == "active"
