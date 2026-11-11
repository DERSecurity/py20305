"""The default doeModesSupported and doeModesEnabled follow the limits a connector implements.

IEEE 2030.5 defines the supported-modes bitmap as the modes "implemented by the
device", and ``csipaus-ext.xsd`` gives the DOEControlType bits as opModExpLimW 0,
opModImpLimW 1, opModGenLimW 2 and opModLoadLimW 3.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree

from py20305.connectors.base import BaseConnector
from py20305.connectors.modes import implemented_doe_modes
from py20305.server_profile import Edition, ServerProfile
from py20305.telemetry.der_capability import build_der_capability
from py20305.telemetry.der_resource_manager import DerResourceManager
from py20305.telemetry.der_settings import build_der_settings

_AUS = "https://csipaus.org/ns"


class _NoLimits(BaseConnector):
    """Overrides none of the four DOE limits."""


class _ExportAndGen(BaseConnector):
    async def update_op_mod_exp_lim_w(self, params: dict[str, Any]) -> None:
        return None

    async def update_op_mod_gen_lim_w(self, params: dict[str, Any]) -> None:
        return None


class _AllLimits(_ExportAndGen):
    async def update_op_mod_imp_lim_w(self, params: dict[str, Any]) -> None:
        return None

    async def update_op_mod_load_lim_w(self, params: dict[str, Any]) -> None:
        return None


class _OfferBacked:
    """A connector whose methods come from a live offer through ``__getattr__``."""

    def __init__(self, offered: set[str]) -> None:
        self._offered = offered

    def __getattr__(self, item: str) -> Any:
        if item in self._offered:
            return AsyncMock()
        raise AttributeError(item)


def test_a_connector_implementing_no_limit_implements_no_doe_mode():
    assert implemented_doe_modes(_NoLimits()) == 0x00


def test_a_connector_implementing_some_limits_implements_exactly_those_doe_modes():
    assert implemented_doe_modes(_ExportAndGen()) == 0b0101


def test_a_connector_implementing_all_limits_implements_all_four_doe_modes():
    assert implemented_doe_modes(_AllLimits()) == 0x0F


def test_a_plugin_backed_connector_implements_the_limits_its_offer_carries():
    offer = _OfferBacked({"update_op_mod_imp_lim_w", "update_op_mod_load_lim_w"})

    assert implemented_doe_modes(offer) == 0b1010


def test_capability_advertises_the_implemented_limits_in_csip_aus_mode():
    cap = build_der_capability(
        {"rtgMaxW": 5000}, csip_aus_mode=True, doe_modes_implemented=0b0101
    )

    assert cap.other_element[0].value == b"\x05"


def test_capability_advertises_no_limits_for_a_connector_implementing_none():
    cap = build_der_capability({"rtgMaxW": 5000}, csip_aus_mode=True, doe_modes_implemented=0)

    assert cap.other_element[0].value == b"\x00"


def test_an_explicit_doe_modes_supported_wins_over_the_implemented_limits():
    cap = build_der_capability(
        {"rtgMaxW": 5000, "doeModesSupported": 0x0C},
        csip_aus_mode=True,
        doe_modes_implemented=0x01,
    )

    assert cap.other_element[0].value == b"\x0c"


def test_capability_has_no_doe_extension_outside_csip_aus_mode():
    cap = build_der_capability({"rtgMaxW": 5000}, doe_modes_implemented=0x0F)

    assert cap.other_element == []


def test_settings_enable_the_implemented_limits_in_csip_aus_mode():
    st = build_der_settings({"setMaxW": 5000}, csip_aus_mode=True, doe_modes_implemented=0b0101)

    assert st.other_element[0].value == b"\x05"


def test_settings_enable_no_limits_for_a_connector_implementing_none():
    st = build_der_settings({"setMaxW": 5000}, csip_aus_mode=True, doe_modes_implemented=0)

    assert st.other_element[0].value == b"\x00"


def test_an_explicit_doe_modes_enabled_wins_over_the_implemented_limits():
    st = build_der_settings(
        {"setMaxW": 5000, "doeModesEnabled": 0x02}, csip_aus_mode=True, doe_modes_implemented=0x0F
    )

    assert st.other_element[0].value == b"\x02"


def test_settings_have_no_doe_extension_outside_csip_aus_mode():
    st = build_der_settings({"setMaxW": 5000}, doe_modes_implemented=0x0F)

    assert st.other_element == []


def _client(*, csip_aus_mode: bool) -> MagicMock:
    client = AsyncMock()
    client.profile = ServerProfile(base=Edition.E2023)
    client.csip_aus_mode = csip_aus_mode
    client.csipaus_namespace = _AUS
    client.put_bytes = AsyncMock(return_value=200)
    return client


def _manager(client: MagicMock, connector: Any) -> DerResourceManager:
    async def resolve(_lfdi: str) -> Any:
        return connector

    manager = DerResourceManager(client, resolve)
    manager.start_device("ab", "/cap", "/set", None)
    return manager


def _doe_element(body: bytes, name: str) -> str | None:
    found = etree.fromstring(body).find(f"{{{_AUS}}}{name}")
    return None if found is None else found.text


class _Reporting(_ExportAndGen):
    async def fetch_nameplate(self) -> dict[str, Any]:
        return {"rtgMaxW": 5000}

    async def fetch_configuration(self) -> dict[str, Any]:
        return {"setMaxW": 5000}


@pytest.mark.asyncio
async def test_the_manager_puts_the_limits_a_connector_implements_to_a_csip_aus_server():
    client = _client(csip_aus_mode=True)
    manager = _manager(client, _Reporting())

    await manager._capability_cycle("ab")
    await manager._settings_cycle("ab")

    capability_body = client.put_bytes.call_args_list[0][0][1]
    settings_body = client.put_bytes.call_args_list[1][0][1]
    assert _doe_element(capability_body, "doeModesSupported") == "05"
    assert _doe_element(settings_body, "doeModesEnabled") == "05"


@pytest.mark.asyncio
async def test_the_manager_puts_no_doe_elements_to_a_plain_server():
    client = _client(csip_aus_mode=False)
    manager = _manager(client, _Reporting())

    await manager._capability_cycle("ab")
    await manager._settings_cycle("ab")

    for call in client.put_bytes.call_args_list:
        assert b"doeModes" not in call[0][1]


class _Offer(_Reporting):
    """A connector whose implemented limits change while it runs."""

    def __init__(self) -> None:
        self.offered: set[str] = {"update_op_mod_exp_lim_w"}

    def __getattribute__(self, item: str) -> Any:
        if item.startswith("update_op_mod_") and item.endswith("_lim_w"):
            if item not in object.__getattribute__(self, "offered"):
                raise AttributeError(item)
            return AsyncMock()
        return object.__getattribute__(self, item)


@pytest.mark.parametrize("policy", ["periodic", "on_change"])
@pytest.mark.asyncio
async def test_a_change_in_the_implemented_limits_puts_the_settings_again(policy):
    client = _client(csip_aus_mode=True)
    connector = _Offer()

    async def resolve(_lfdi: str) -> Any:
        return connector

    manager = DerResourceManager(client, resolve, put_policy=policy)
    manager.start_device("ab", "/cap", "/set", None)
    await manager._settings_cycle("ab")
    await manager._settings_cycle("ab")
    assert client.put_bytes.await_count == 1

    connector.offered.add("update_op_mod_gen_lim_w")
    await manager._settings_cycle("ab")

    assert client.put_bytes.await_count == 2
    assert _doe_element(client.put_bytes.call_args[0][1], "doeModesEnabled") == "05"
