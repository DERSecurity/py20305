"""Demo connector that logs all calls and returns hardcoded telemetry.

Useful for testing and demonstration without real hardware.
Captures the last control params for each method for test inspection.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from py20305.connectors.base import BaseConnector, ConnectorPayload, ReadingOverride

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

    async def fetch_monitoring(self) -> dict[str, Any]:
        # The keys present here decide which MirrorMeterReadings get posted.
        # Drop a key (or set it to None) and that quantity is omitted from the
        # readings instead of posted as 0. Add per-line keys -- "ACType" (0/1/2)
        # plus "WL2"/"VarL2"/"VL2"/"PFL2"/"VAL2"/"AL2" (and L3) -- to surface
        # per-phase readings. See reading_overrides() below to tune the
        # ReadingType metadata / qualityFlags of any of these.
        return {
            "W": 10000,
            "Var": 0,
            "Hz": 60,
            "V": 240,
            "PF": 0.98,
            "VA": 10000,
            "A": 11.9,
        }

    def reading_overrides(self) -> dict[str, ReadingOverride] | None:
        """Demo of per-quantity ReadingType / qualityFlags overrides.

        Keyed by the same metric key as ``fetch_monitoring`` ("W", "V", ...;
        "WL2" etc. for per-line). Every ``ReadingOverride`` field is optional --
        unset fields keep the client's standard ReadingType defaults, so you
        only state the deltas you want. Return ``None`` (the BaseConnector
        default) for no overrides; edit or delete this method to change what the
        MUP advertises.

        Field values are IEEE 2030.5 codes:
          - accumulation_behaviour: 12=Instantaneous (default), 9=Summation
          - data_qualifier: 12=Normal (default), 2=Average, 8=Maximum, 9=Minimum
          - kind: 37=Power (default), 12=Energy
          - uom: 38=W, 63=var, 61=VA, 29=V, 5=A, 33=Hz, 72=Wh, ...
          - multiplier: powerOfTenMultiplier (e.g. -3, -1, 0)
          - quality_flags: 16-bit bitmap. bit0(0x01)=valid (default),
            bit2/3=estimated, bit4(0x10)=questionable, bit5(0x20)=derived
        """
        return {
            # Report Real Power as a Maximum rather than Normal, and flag its
            # readings "derived" (computed, not directly measured).
            "W": ReadingOverride(data_qualifier=8, quality_flags=0x20),
            # Mark Frequency readings "questionable".
            "Hz": ReadingOverride(quality_flags=0x10),
            # CSIP-AUS requires a phase code on voltage -- NOT_APPLICABLE (0) is
            # rejected. Tag the single-phase system voltage as A-N (129). (The
            # default leaves it unset for SunSpec-701 line-to-line-average
            # devices; a real per-phase source would set this per line.)
            "V": ReadingOverride(phase=129),
        }

    async def fetch_nameplate(self) -> dict[str, Any]:
        return {
            "WMaxRtg": 15000,
            "WOvrExtRtg": 15000,
            "WOvrExtRtgPF": 0.800,
            "WUndExtRtg": 15000,
            "WUndExtRtgPF": 0.800,
            "VAMaxRtg": 15000,
            "VarMaxInjRtg": 4400,
            "VarMaxAbsRtg": 4400,
            "WChaRteMaxRtg": 15000,
            "VAChaRteMaxRtg": 15000,
            # Storage ratings (rtgMaxDischargeRateW/VA, rtgMaxWh). Demonstrates a
            # combined PV+storage nameplate. A 0 here would be reported as 0; only
            # a missing key is omitted.
            "WDisChaRteMaxRtg": 15000,
            "VADisChaRteMaxRtg": 15000,
            "WhMaxRtg": 30000,
            "VNomRtg": 480,
            "VMaxRtg": 576,
            "VMinRtg": 384,
            "ReactSusceptRtg": 0,
            "NorOpCatRtg": 1,
            "AbnOpCatRtg": 2,
            "CtrlModes": 93323888,
        }

    async def fetch_configuration(self) -> dict[str, Any]:
        return {
            "WMax": 10000,
            "WMaxOvrExt": 8500,
            "WOvrExtPF": 0.850,
            "WMaxUndExt": 8500,
            "WUndExtPF": 0.850,
            "VAMax": 10000,
            "VarMaxInj": 4400,
            "VarMaxAbs": 4400,
            "WChaRteMax": 15000,
            "VAChaRteMax": 15000,
            # Storage settings (setMaxDischargeRateW/VA).
            "WDisChaRteMax": 15000,
            "VADisChaRteMax": 15000,
            "VNom": 480,
            "VMax": 576,
            "VMin": 384,
            "CtrlModes": 93323888,
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
            "inverterStatus": {"dateTime": now, "value": 3},
            "localControlModeStatus": None,
            "manufacturerStatus": {"dateTime": now, "value": "1000"},
            "operationalModeStatus": {"dateTime": now, "value": 1},
            "readingTime": now,
            "stateOfChargeStatus": {"dateTime": now, "value": 0},
            "storageModeStatus": None,
            "storConnectStatus": None,
        }

    # ------------------------------------------------------------------
    # Control modes -- log and record
    # ------------------------------------------------------------------

    async def update_qv(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_qv", params)
        return None

    async def update_pv(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_pv", params)
        return None

    async def update_qp(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_qp", params)
        return None

    async def update_p_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_p_lim", params)
        return None

    async def update_p_lim_inj(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_p_lim_inj", params)
        return None

    async def update_p_lim_abs(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_p_lim_abs", params)
        return None

    async def update_freq_watt(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_freq_watt", params)
        return None

    async def update_pf(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_pf", params)
        return None

    async def update_const_q(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_const_q", params)
        return None

    async def update_const_pf(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_const_pf", params)
        return None

    async def update_ov(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_ov", params)
        return None

    async def update_uv(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_uv", params)
        return None

    async def update_ov_mc(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_ov_mc", params)
        return None

    async def update_uv_mc(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_uv_mc", params)
        return None

    async def update_of(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_of", params)
        return None

    async def update_uf(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_uf", params)
        return None

    async def update_fixed_w(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_fixed_w", params)
        return None

    async def update_p_ramp(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_p_ramp", params)
        return None

    async def update_es_permit_service(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_es_permit_service", params)
        return None

    async def update_connect(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_connect", params)
        return None

    async def update_max_lim_pct_va_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_max_lim_pct_va_absorb", params)
        return None

    async def update_max_lim_pct_va_inject(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_max_lim_pct_va_inject", params)
        return None

    async def update_max_lim_pct_var_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_max_lim_pct_var_absorb", params)
        return None

    async def update_max_lim_pct_var_inject(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_max_lim_pct_var_inject", params)
        return None

    async def update_max_lim_pct_w_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_max_lim_pct_w_absorb", params)
        return None

    async def update_max_lim_var_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_max_lim_var_absorb", params)
        return None

    async def update_max_lim_var_inject(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_max_lim_var_inject", params)
        return None

    async def update_target_v(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_target_v", params)
        return None

    async def update_target_var(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_target_var", params)
        return None

    async def update_target_w(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_target_w", params)
        return None

    async def update_delta_w(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_delta_w", params)
        return None

    async def update_delta_var(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_delta_var", params)
        return None

    async def update_fixed_v(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_fixed_v", params)
        return None

    async def update_grid_connect_permit(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_grid_connect_permit", params)
        return None

    async def update_island_permit(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_island_permit", params)
        return None

    async def update_exp_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_exp_lim", params)
        return None

    async def update_imp_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_imp_lim", params)
        return None

    async def update_gen_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_gen_lim", params)
        return None

    async def update_load_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_load_lim", params)
        return None

    async def update_pricing_mode(self, params: dict[str, Any]) -> ConnectorPayload:
        self._record("update_pricing_mode", params)
        return None
