"""In-process SunSpec Modbus connector.

Thin adapter exposing the transport-neutral
:class:`py20305.connectors.sunspec_core.SunSpecModbusConnector` as a
:class:`BaseConnector`. The Modbus/SunSpec logic lives in the core so this
in-process connector and an out-of-process one share one
implementation; here we only wire the client's ``diagnostics.report`` into
the core's optional report callback.
"""

from __future__ import annotations

from typing import Any

from py20305.connectors.base import BaseConnector
from py20305.connectors.sunspec_core import SunSpecModbusConnector

# Re-export the Modbus error classifier: it moved into the core, but external
# callers (and tests) still reference it via this module.
from py20305.connectors.sunspec_core._modbus import _is_permanent_modbus_error

__all__ = ["ConnectorSunSpec", "_is_permanent_modbus_error"]


class ConnectorSunSpec(SunSpecModbusConnector, BaseConnector):
    """SunSpec Modbus connector for in-process (bundled-connector) use.

    Inherits the Modbus/SunSpec behaviour from
    :class:`SunSpecModbusConnector` and the no-op control-mode defaults from
    :class:`BaseConnector` (covering modes SunSpec doesn't implement).
    Constructs the core with the client's ``diagnostics.report`` as its
    diagnostics sink.
    """

    connector_name: str = "SunSpecConnector"
    #: It reaches the device over Modbus, so its exchanges say so.
    telemetry_protocol: str = "modbus"

    def __init__(self, **kwargs: Any) -> None:
        from py20305.diagnostics import report

        super().__init__(report=report, **kwargs)
