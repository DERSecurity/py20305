"""The SunSpec Modbus connector, on the IEEE 2030.5 connector contract.

:class:`SunSpecConnector` drives a SunSpec device over Modbus TCP, Modbus RTU
or Modbus TCP with TLS through the Modbus core, and converts what it reads and
writes in :mod:`py20305.connectors.sunspec._ieee2030_5`. Nothing SunSpec
leaves this package: monitoring is a list of readings, ratings and settings
use the DERCapability and DERSettings element names, status uses the
DERStatus types, and controls arrive as ``update_<element>`` calls.

A DER is read through models 701 to 705 (and 706, 712 and 713 where the
device has them). A meter is read through models 201 to 204, 211 to 214 or
220; set its ``role`` to ``site_meter`` or ``der_meter``, and ``power_sign``
when the meter's sign of real power is known.

Needs the ``sunspec`` extra (``pip install py20305[sunspec]``).
"""

from __future__ import annotations

import copy
from typing import Any

from py20305.connectors.base import BaseConnector, ConnectorPayload
from py20305.connectors.modes import ALL_ELEMENTS
from py20305.connectors.reading import Reading
from py20305.connectors.sunspec import _ieee2030_5 as convert
from py20305.connectors.sunspec._modbus import SunSpecModbusConnector
from py20305.telemetry.mup import ReadingProfile

__all__ = ["SunSpecConnector"]


class SunSpecConnector(BaseConnector):
    """A SunSpec Modbus device, presented with the IEEE 2030.5 connector contract.

    Takes the keyword arguments of
    :class:`~py20305.connectors.sunspec.config.SunSpecDeviceConfig`'s
    ``connector_kwargs()``. Construction connects to the device and scans its
    models; a device that refuses the scan raises
    :class:`~py20305.connectors.errors.ConnectorConnectionError`.

    Elements the device has no write for keep the base class's no-op, so the
    dispatcher reports them as unsupported by design.
    """

    connector_name: str = "SunSpecConnector"
    #: It reaches the device over Modbus, so its exchanges say so.
    telemetry_protocol: str = "modbus"
    #: A Modbus unit names one device.
    devices_per_endpoint: str = "one"
    #: The registry passes the client's reading profile; see ``__init__``.
    takes_reading_profile: bool = True

    def __init__(
        self, *, reading_profile: ReadingProfile = ReadingProfile.DEFAULT, **kwargs: Any
    ) -> None:
        """Connect to the device and scan its models.

        ``reading_profile`` is the client's. Under
        ``SIGNED_LOAD_CONVENTION`` the real, reactive and apparent power
        totals and the state of energy carry the wiring's PhaseCode, as that
        profile registers them; under ``DEFAULT`` they are unphased.
        """
        from py20305.diagnostics import report

        self._core = SunSpecModbusConnector(report=report, **kwargs)
        self._controls = convert.Controls()
        self.der_type = int(kwargs.get("der_type", 83))
        self._line_to_line_voltage = kwargs.get("role", "der") == "der"
        self._phased_totals = reading_profile is ReadingProfile.SIGNED_LOAD_CONVENTION

    @property
    def endpoint_id(self) -> str:
        """Return ``host:port`` for TCP or ``rtu:<line>`` for serial."""
        return self._core.endpoint_id

    async def fetch_monitoring(self) -> list[Reading]:
        values = await self._core.fetch_monitoring()
        return convert.readings(
            values,
            line_to_line_voltage=self._line_to_line_voltage,
            phased_totals=self._phased_totals,
        )

    async def fetch_nameplate(self) -> dict[str, Any]:
        values = await self._core.fetch_nameplate()
        # The core read model 702 for the ratings, so the model holds them.
        models = self._core._target.models
        return convert.nameplate(values, models[702][0] if models.get(702) else None)

    async def fetch_configuration(self) -> dict[str, Any]:
        return convert.settings(await self._core.fetch_configuration())

    async def fetch_status(self) -> dict[str, Any]:
        return convert.status(await self._core.fetch_status())

    async def _apply(self, element: str, params: dict[str, Any]) -> ConnectorPayload:
        # A write the device or the core refuses must not stay in the merged
        # state, or the next write of a shared function would send it again.
        before = copy.deepcopy(self._controls)
        method, core_params = self._controls.translate(element, params)
        try:
            await getattr(self._core, method)(core_params)
        except BaseException:
            self._controls = before
            raise
        return None


def _element_method(element: str, method: str) -> Any:
    async def update(self: SunSpecConnector, params: dict[str, Any]) -> ConnectorPayload:
        return await self._apply(element, params)

    update.__name__ = method
    update.__qualname__ = f"SunSpecConnector.{method}"
    update.__doc__ = f"Apply or withdraw {element} on the device."
    return update


for _element in ALL_ELEMENTS:
    if _element.element in convert.CORE_METHOD:
        setattr(
            SunSpecConnector,
            _element.method,
            _element_method(_element.element, _element.method),
        )
del _element
