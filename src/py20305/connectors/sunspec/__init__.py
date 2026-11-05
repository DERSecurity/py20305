"""The SunSpec Modbus connector: a downstream adapter on the IEEE 2030.5 contract.

Everything SunSpec stays in this package. :class:`SunSpecConnector` reads and
writes SunSpec points over Modbus and converts them to and from the IEEE
2030.5 connector contract, so the rest of the client sees readings, element
names and ``update_<element>`` calls only.

:class:`SunSpecModbusConnector` is the Modbus core underneath: it reads and
writes SunSpec points by their SunSpec names, for an application that keeps
its own conversion (an out-of-process connector, for one).

The connectors need the ``sunspec`` extra (``pip install py20305[sunspec]``).
:class:`SunSpecDeviceConfig` does not, so a configuration naming a SunSpec
device loads without it; the connector is imported on first use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from py20305.connectors.errors import (
    ConnectorConnectionError,
    ConnectorError,
    ConnectorTimeoutError,
    ConnectorValueError,
    ConnectorWriteError,
)
from py20305.connectors.sunspec.config import SunSpecDeviceConfig

if TYPE_CHECKING:
    from py20305.connectors.sunspec._modbus import (
        SunSpecModbusConnector as SunSpecModbusConnector,
    )
    from py20305.connectors.sunspec.connector import SunSpecConnector as SunSpecConnector

__all__ = [
    "ConnectorConnectionError",
    "ConnectorError",
    "ConnectorTimeoutError",
    "ConnectorValueError",
    "ConnectorWriteError",
    "SunSpecConnector",
    "SunSpecDeviceConfig",
    "SunSpecModbusConnector",
]


def __getattr__(name: str) -> Any:
    if name == "SunSpecConnector":
        from py20305.connectors.sunspec.connector import SunSpecConnector

        return SunSpecConnector
    if name == "SunSpecModbusConnector":
        from py20305.connectors.sunspec._modbus import SunSpecModbusConnector

        return SunSpecModbusConnector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
