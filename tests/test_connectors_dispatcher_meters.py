"""A meter takes no controls.

A control addressed to a meter is refused with ``DeviceNotConfiguredError``,
which the event processor reports as IEEE 2030.5-2023 Table 31 status 252 (Not
Applicable). The writes that carry no response, a default control, a clear and
a mode disable, skip the meter without touching it.
"""

from __future__ import annotations

import pytest

from py20305.commands import CommandOrigin
from py20305.connectors.config import PrintDemoDeviceConfig
from py20305.connectors.control_errors import DeviceNotConfiguredError
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.events.response import ResponseCode, response_code_for_dispatch_error
from py20305.models.sep.sep import (
    DateTimeInterval,
    DefaultDercontrol,
    Dercontrol1,
    DercontrolBase,
    EventStatus,
    MRidtype,
    TimeType,
)

DER = "aa" * 20
SITE_METER = "bb" * 20
DER_METER = "cc" * 20


def _dispatcher() -> tuple[ConnectorDispatcher, ConnectorConfigRegistry]:
    registry = ConnectorConfigRegistry(
        [
            PrintDemoDeviceConfig(lfdi=DER),
            PrintDemoDeviceConfig(lfdi=SITE_METER, role="site_meter"),
            PrintDemoDeviceConfig(lfdi=DER_METER, role="der_meter"),
        ]
    )
    hrefs = {"/edev/1": DER, "/edev/2": SITE_METER}
    return ConnectorDispatcher(registry, lfdi_resolver=hrefs.get), registry


def _derc() -> Dercontrol1:
    return Dercontrol1(
        m_rid=MRidtype(value=b"\x01" * 16),
        creation_time=TimeType(value=0),
        event_status=EventStatus(
            current_status=0, date_time=TimeType(value=0), potentially_superseded=False
        ),
        interval=DateTimeInterval(duration=60, start=TimeType(value=0)),
        dercontrol_base=DercontrolBase(op_mod_connect=True),
    )


def _dderc() -> DefaultDercontrol:
    return DefaultDercontrol(
        m_rid=MRidtype(value=b"\x02" * 16),
        dercontrol_base=DercontrolBase(op_mod_connect=True),
    )


def _constructed(registry: ConnectorConfigRegistry, lfdi: str) -> bool:
    proxy = registry.get_connector(lfdi)
    assert proxy is not None
    return proxy._instance is not None


class TestTakesControls:
    def test_a_der_takes_controls(self):
        dispatcher, _ = _dispatcher()

        assert dispatcher.takes_controls(DER) is True

    @pytest.mark.parametrize("lfdi", [SITE_METER, DER_METER])
    def test_a_meter_takes_no_controls(self, lfdi):
        dispatcher, _ = _dispatcher()

        assert dispatcher.takes_controls(lfdi) is False

    def test_a_device_the_registry_does_not_know_is_treated_as_a_der(self):
        dispatcher, _ = _dispatcher()

        assert dispatcher.takes_controls("dd" * 20) is True


class TestControlsToMetersAreRefused:
    @pytest.mark.parametrize("lfdi", [SITE_METER, DER_METER])
    async def test_a_control_by_lfdi_to_a_meter_is_refused(self, lfdi):
        dispatcher, registry = _dispatcher()

        with pytest.raises(DeviceNotConfiguredError, match="takes no controls"):
            await dispatcher.apply_control_by_lfdi(lfdi, _derc(), [])

        assert not _constructed(registry, lfdi)

    async def test_a_control_by_href_to_a_meter_is_refused(self):
        dispatcher, registry = _dispatcher()

        with pytest.raises(DeviceNotConfiguredError):
            await dispatcher.apply_control("/edev/2", _derc(), [])

        assert not _constructed(registry, SITE_METER)

    async def test_a_named_operation_on_a_meter_is_refused(self):
        dispatcher, _ = _dispatcher()

        with pytest.raises(DeviceNotConfiguredError):
            await dispatcher.apply_operation(
                SITE_METER, "connect", {"connected": True}, origin=CommandOrigin.IEEE2030_5
            )

    def test_the_refusal_is_reported_as_not_applicable(self):
        assert (
            response_code_for_dispatch_error(DeviceNotConfiguredError("meter"))
            == ResponseCode.NOT_APPLICABLE
        )

    async def test_a_control_to_a_der_is_still_applied(self):
        dispatcher, registry = _dispatcher()

        await dispatcher.apply_control_by_lfdi(DER, _derc(), [])

        assert _constructed(registry, DER)


class TestWritesWithoutAResponseSkipMeters:
    async def test_a_default_control_skips_a_meter(self):
        dispatcher, registry = _dispatcher()

        await dispatcher.apply_default_control_by_lfdi(SITE_METER, _dderc(), [])
        await dispatcher.apply_default_control("/edev/2", _dderc(), [])

        assert not _constructed(registry, SITE_METER)

    async def test_a_clear_skips_a_meter(self):
        dispatcher, registry = _dispatcher()

        await dispatcher.clear_control_by_lfdi(DER_METER)
        await dispatcher.clear_control("/edev/2")

        assert not _constructed(registry, DER_METER)
        assert not _constructed(registry, SITE_METER)

    async def test_a_mode_disable_skips_a_meter(self):
        dispatcher, registry = _dispatcher()

        await dispatcher.disable_modes_by_lfdi(SITE_METER, frozenset({"op_mod_fixed_w"}))
        await dispatcher.disable_modes("/edev/2", frozenset({"op_mod_fixed_w"}))

        assert not _constructed(registry, SITE_METER)

    async def test_a_default_control_still_reaches_a_der(self):
        dispatcher, registry = _dispatcher()

        await dispatcher.apply_default_control_by_lfdi(DER, _dderc(), [])

        assert _constructed(registry, DER)
