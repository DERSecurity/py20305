"""Tests for the Pricing (tariff) discovery walk in client.discovery."""

from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any

import pytest

from py20305.client.discovery import (
    _discover_tariff_profile,
    _discover_tariffs_for_fsa,
)
from py20305.client.errors import Sep2ProtocolError
from py20305.client.state import DiscoveredState


class _MockClient:
    """Minimal Sep2Client stub: get_list returns responses keyed by path."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses

    async def get_list(self, path: str, model_type: type) -> list[Any]:
        key = path.split("?")[0]
        if key not in self._responses:
            raise ValueError(f"no mock response for {key}")
        val = self._responses[key]
        if isinstance(val, Exception):
            raise val
        return val if isinstance(val, list) else [val]


def _link(href: str) -> NS:
    return NS(href=href)


def _tti(href: str, tou_tier: int = 0) -> NS:
    return NS(
        href=href,
        tou_tier=NS(value=tou_tier),
        consumption_tariff_interval_list_link=_link(f"{href}/cti"),
    )


def _rate_component(href: str, tti_href: str) -> NS:
    return NS(href=href, time_tariff_interval_list_link=_link(tti_href))


def _tariff_profile(href: str, rc_href: str, primacy: int = 1) -> NS:
    return NS(href=href, primacy=NS(value=primacy), rate_component_list_link=_link(rc_href))


def _fsa(href: str, tp_list_href: str | None) -> NS:
    link = _link(tp_list_href) if tp_list_href else None
    return NS(href=href, tariff_profile_list_link=link)


@pytest.mark.asyncio
async def test_discover_tariffs_full_walk():
    tp = _tariff_profile("/tp/1", "/tp/1/rc", primacy=3)
    rc = _rate_component("/tp/1/rc/1", "/tp/1/rc/1/tti")
    tti1, tti2 = _tti("/tp/1/rc/1/tti/1"), _tti("/tp/1/rc/1/tti/2")
    client = _MockClient(
        {
            "/tp": NS(poll_rate=900, tariff_profile=[tp]),
            "/tp/1/rc": NS(rate_component=[rc]),
            "/tp/1/rc/1/tti": NS(subscribable=1, time_tariff_interval=[tti1, tti2]),
        }
    )
    state = DiscoveredState()
    await _discover_tariffs_for_fsa(client, _fsa("/fsa/1", "/tp"), state)

    assert set(state.tariff_profiles) == {"/tp/1"}
    profile = state.tariff_profiles["/tp/1"]
    assert profile.primacy == 3
    assert profile.discovered_from_fsa_href == "/fsa/1"
    assert len(profile.rate_components) == 1

    component = profile.rate_components[0]
    assert component.href == "/tp/1/rc/1"
    assert [i.href for i in component.time_tariff_intervals] == [
        "/tp/1/rc/1/tti/1",
        "/tp/1/rc/1/tti/2",
    ]
    assert component.tti_list_subscribable is True


@pytest.mark.asyncio
async def test_poll_rate_captured():
    tp = _tariff_profile("/tp/1", "/tp/1/rc")
    client = _MockClient(
        {
            "/tp": NS(poll_rate=1200, tariff_profile=[tp]),
            "/tp/1/rc": NS(rate_component=[]),
        }
    )
    state = DiscoveredState()
    await _discover_tariffs_for_fsa(client, _fsa("/fsa/1", "/tp"), state)
    assert state.poll_rates.get("tariff") is not None


@pytest.mark.asyncio
async def test_missing_tariff_link_is_noop():
    client = _MockClient({})
    state = DiscoveredState()
    await _discover_tariffs_for_fsa(client, _fsa("/fsa/1", None), state)
    assert state.tariff_profiles == {}


@pytest.mark.asyncio
async def test_empty_tariff_list_yields_no_profiles():
    # Sep2Client.get_list normalizes a first-page 404/204 (the common
    # "no tariffs configured" case) to an empty list.
    client = _MockClient({"/tp": []})
    state = DiscoveredState()
    await _discover_tariffs_for_fsa(client, _fsa("/fsa/1", "/tp"), state)
    assert state.tariff_profiles == {}


@pytest.mark.asyncio
async def test_non_first_page_404_skipped():
    # A non-first-page 404 is NOT normalized by get_list and surfaces as a
    # Sep2ProtocolError; the walk skips it (mirrors DERProgram discovery).
    client = _MockClient({"/tp": Sep2ProtocolError("not found", status_code=404)})
    state = DiscoveredState()
    await _discover_tariffs_for_fsa(client, _fsa("/fsa/1", "/tp"), state)
    assert state.tariff_profiles == {}


@pytest.mark.asyncio
async def test_rate_component_without_href_skipped():
    tp = _tariff_profile("/tp/1", "/tp/1/rc")
    valid_rc = _rate_component("/tp/1/rc/1", "/tp/1/rc/1/tti")
    hrefless_rc = NS(href=None, time_tariff_interval_list_link=_link("/tp/1/rc/x/tti"))
    client = _MockClient(
        {
            "/tp": NS(poll_rate=None, tariff_profile=[tp]),
            "/tp/1/rc": NS(rate_component=[hrefless_rc, valid_rc]),
            "/tp/1/rc/1/tti": NS(subscribable=0, time_tariff_interval=[]),
        }
    )
    state = DiscoveredState()
    await _discover_tariffs_for_fsa(client, _fsa("/fsa/1", "/tp"), state)
    components = state.tariff_profiles["/tp/1"].rate_components
    assert [c.href for c in components] == ["/tp/1/rc/1"]


@pytest.mark.asyncio
async def test_profile_deduped_across_fsas():
    # The tariff tree is global: multiple FSAs point at the same /tp. A profile
    # already discovered is not re-walked, and its FSA attribution stays the
    # first one that referenced it.
    tp = _tariff_profile("/tp/1", "/tp/1/rc")
    client = _MockClient(
        {
            "/tp": NS(poll_rate=None, tariff_profile=[tp]),
            "/tp/1/rc": NS(rate_component=[]),
        }
    )
    state = DiscoveredState()
    await _discover_tariffs_for_fsa(client, _fsa("/fsa/1", "/tp"), state)
    await _discover_tariffs_for_fsa(client, _fsa("/fsa/2", "/tp"), state)
    assert set(state.tariff_profiles) == {"/tp/1"}
    assert state.tariff_profiles["/tp/1"].discovered_from_fsa_href == "/fsa/1"


@pytest.mark.asyncio
async def test_profile_without_rate_component_link():
    tp = NS(href="/tp/1", primacy=NS(value=1), rate_component_list_link=None)
    client = _MockClient({})
    profile = await _discover_tariff_profile(client, tp, "/tp/1", "/fsa/1")
    assert profile.rate_components == []
    assert profile.primacy == 1


@pytest.mark.asyncio
async def test_rate_component_without_intervals():
    tp = _tariff_profile("/tp/1", "/tp/1/rc")
    rc = _rate_component("/tp/1/rc/1", "/tp/1/rc/1/tti")
    client = _MockClient(
        {
            "/tp": NS(poll_rate=None, tariff_profile=[tp]),
            "/tp/1/rc": NS(rate_component=[rc]),
            "/tp/1/rc/1/tti": NS(subscribable=0, time_tariff_interval=[]),
        }
    )
    state = DiscoveredState()
    await _discover_tariffs_for_fsa(client, _fsa("/fsa/1", "/tp"), state)
    component = state.tariff_profiles["/tp/1"].rate_components[0]
    assert component.time_tariff_intervals == []
    assert component.tti_list_subscribable is False
