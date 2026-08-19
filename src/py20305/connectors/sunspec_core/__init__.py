"""Transport-neutral SunSpec/Modbus connector core.

A framework-neutral implementation of the SunSpec Modbus device logic with
no dependency on any host framework. Both the bundled in-process connector
are thin adapters over
:class:`SunSpecModbusConnector`.

Because this package imports nothing framework-specific, it ships verbatim in
unchanged in an out-of-process connector, which then behaves identically
to the bundled one -- they share this source.

``SunSpecModbusConnector`` is imported lazily (PEP 562) so that pulling in the
error types alone -- which ``connectors/base.py`` re-exports, and which is on
the import path of much of the client -- does not load the Modbus
implementation module.
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

if TYPE_CHECKING:
    from ._modbus import SunSpecModbusConnector as SunSpecModbusConnector

__all__ = [
    "ConnectorConnectionError",
    "ConnectorError",
    "ConnectorTimeoutError",
    "ConnectorValueError",
    "ConnectorWriteError",
    "SunSpecModbusConnector",
]


def __getattr__(name: str) -> Any:
    if name == "SunSpecModbusConnector":
        from ._modbus import SunSpecModbusConnector

        return SunSpecModbusConnector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
