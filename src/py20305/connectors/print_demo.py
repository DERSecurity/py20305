"""Demo connector that logs all calls and returns hardcoded telemetry.

Useful for testing and demonstration without real hardware.
Captures the last control params for each method for test inspection.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from py20305.connectors.base import BaseConnector, ConnectorPayload
from py20305.connectors.modes import ALL_ELEMENTS
from py20305.connectors.reading import Reading

logger = logging.getLogger(__name__)


class PrintDemoConnector(BaseConnector):
    """Logs all method calls and returns realistic mock data."""

    connector_name: str = "PrintDemoConnector"

    def __init__(self) -> None:
        self.last_control: dict[str, Any] = {}

    def _record(self, method: str, params: Any) -> None:
        self.last_control[method] = params
        logger.info("PrintDemo.%s(%s)", method, params)

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    async def fetch_monitoring(self) -> list[Reading]:
        # Each reading states its IEEE 2030.5 ReadingType. Leave a quantity out
        # and it is not posted, rather than posted as 0. Add readings with a
        # phase code (128 A, 64 B, 32 C; 129 AN, 65 BN, 33 CN for voltage) to
        # post per-line values.
        return [
            # Real Power as a Maximum rather than Normal, flagged "derived"
            # (computed, not directly measured).
            Reading(uom=38, value=10000, data_qualifier=8, quality_flags=0x20),
            Reading(uom=63, value=0),
            # Frequency flagged "questionable".
            Reading(uom=33, value=60, quality_flags=0x10),
            # CSIP-AUS requires a phase code on voltage: a single-phase
            # line-to-neutral average is phase AN (129).
            Reading(uom=29, value=240, phase=129, data_qualifier=2),
            Reading(uom=65, value=0.98, kind=0),
            Reading(uom=61, value=10000),
            Reading(uom=5, value=11.9),
        ]

    async def fetch_nameplate(self) -> dict[str, Any]:
        return {
            "rtgMaxW": 15000,
            "rtgOverExcitedW": 15000,
            "rtgOverExcitedPF": 0.800,
            "rtgUnderExcitedW": 15000,
            "rtgUnderExcitedPF": 0.800,
            "rtgMaxVA": 15000,
            "rtgMaxVar": 4400,
            "rtgMaxVarNeg": 4400,
            "rtgMaxChargeRateW": 15000,
            "rtgMaxChargeRateVA": 15000,
            # Storage ratings. A 0 here would be reported as 0; only a missing
            # key is omitted.
            "rtgMaxDischargeRateW": 15000,
            "rtgMaxDischargeRateVA": 15000,
            "rtgMaxWh": 30000,
            "rtgVNom": 480,
            "rtgMaxV": 576,
            "rtgMinV": 384,
            "rtgReactiveSusceptance": 0,
            "rtgNormalCategory": 1,
            "rtgAbnormalCategory": 2,
            "modesSupported": 93323888,
        }

    async def fetch_configuration(self) -> dict[str, Any]:
        return {
            "setMaxW": 10000,
            "setMinPFOverExcited": 0.850,
            "setMinPFUnderExcited": 0.850,
            "setMaxVA": 10000,
            "setMaxVar": 4400,
            "setMaxVarNeg": 4400,
            "setMaxChargeRateW": 15000,
            "setMaxChargeRateVA": 15000,
            "setMaxDischargeRateW": 15000,
            "setMaxDischargeRateVA": 15000,
            "setVNom": 480,
            "setMaxV": 576,
            "setMinV": 384,
            "modesEnabled": 93323888,
        }

    async def fetch_availability(self) -> dict[str, Any]:
        return {
            "availabilityDuration": 86400,
            "maxChargeDuration": None,
            "readingTime": int(time.time()),
            "reserveChargePercent": None,
            "reservePercent": 8000,
            "statVarAvail": {"value": 5000, "multiplier": 0},
            "statWAvail": {"value": 10000, "multiplier": 0},
        }

    async def fetch_status(self) -> dict[str, Any]:
        now = int(time.time())
        return {
            "alarmStatus": 1,
            "connectStatus": {"dateTime": now, "value": 1},
            # The 2030.5-2018 bitmap for a 2018 server: bit 0, connected.
            "genConnectStatus": {"dateTime": now, "value": 1},
            # IEEE 2030.5 InverterStatusType 4: running.
            "inverterStatus": {"dateTime": now, "value": 4},
            "localControlModeStatus": None,
            "manufacturerStatus": {"dateTime": now, "value": "1000"},
            # OperationalModeStatusType 2: operational mode.
            "operationalModeStatus": {"dateTime": now, "value": 2},
            "readingTime": now,
            "stateOfChargeStatus": {"dateTime": now, "value": 0},
            "storageModeStatus": None,
            "storConnectStatus": None,
        }


def _recorder(method: str) -> Any:
    """Return a connector method that logs and records its parameters."""

    async def update(self: PrintDemoConnector, params: dict[str, Any]) -> ConnectorPayload:
        self._record(method, params)
        return None

    update.__name__ = method
    update.__doc__ = f"Log and record {method}."
    return update


# One recording method per control element, so the demo accepts every control.
for _element in ALL_ELEMENTS:
    setattr(PrintDemoConnector, _element.method, _recorder(_element.method))
