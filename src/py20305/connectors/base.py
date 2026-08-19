"""Base connector class with async no-op defaults for all DER control modes.

Connectors inherit from BaseConnector and selectively override the methods
they support. Unsupported modes are silent no-ops, matching the physical
reality that not every device supports every IEEE 2030.5 control mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The error hierarchy lives in its own module so a connector can raise these
# without importing this one, which drags in the whole connector base. They
# are re-exported here because this is where a connector author looks for
# them: ``from py20305.connectors.base import ConnectorError``
# reads better than reaching two modules deep for the base class and its
# errors separately.
#
# ``LazyConnectorProxy`` keys its permanent-failure cache off
# ``ConnectorConnectionError.permanent``, so a connector signalling a
# device-side rejection should set that flag.
from py20305.connectors.errors import (
    ConnectorConnectionError,
    ConnectorError,
    ConnectorTimeoutError,
    ConnectorValueError,
    ConnectorWriteError,
)

ConnectorPayload = dict[str, Any] | None


@dataclass(frozen=True)
class ScheduleNotification:
    """A relayed, forward-looking schedule event for a connector.

    Pushed by the event processor on every *change* to a control, so a
    downstream optimizer stays current without polling for it. Informational
    only: it does not apply setpoints, which remains the job of the
    ``update_*`` methods.
    """

    #: "control" | "default_baseline" | "doe".
    #: "price" | "drlc" | "flow_reservation" are reserved for later.
    stream: str
    #: event streams: "scheduled"|"updated"|"active"|"superseded"|"cancelled"|"completed";
    #: default_baseline: "default_added"|"default_updated".
    transition: str
    #: EventState value (scheduled/active/superseded/cancelled/completed); None for baseline.
    status: str | None
    #: Raw IEEE 2030.5-2018 §11.2.4 EventStatus.currentStatus (0-5); None for baseline.
    current_status: int | None
    #: DERControl / DefaultDERControl mRID, hex.
    mrid: str
    program_href: str
    #: Program primacy.
    primacy: int
    #: Effective (post-randomization) epoch seconds / seconds; None for baseline.
    start: int | None
    duration: int | None
    end: int | None
    #: Hex LFDIs this event scopes to, resolved by the core.
    affected_lfdis: list[str]
    #: (randomizeStart, randomizeDuration) seconds -- carried so optimizers time
    #: dispatch correctly (the update_* path drops it). None when unset.
    randomization: tuple[int | None, int | None]
    #: Stream-specific typed body (DERControlBase / DefaultDERControl / envelope limits).
    payload: dict[str, Any]


@dataclass(frozen=True)
class ReadingOverride:
    """Per-quantity overrides for a connector's MirrorMeterReadings.

    Returned (keyed by monitoring key, e.g. ``"W"``, ``"VL2"``) from
    :meth:`BaseConnector.reading_overrides` to tailor ReadingType metadata and
    the default ``qualityFlags`` to a connector's use case -- e.g. report a
    quantity as a Maximum (``data_qualifier=8``) or a Summation
    (``accumulation_behaviour=9``), or mark its readings ``derived``.

    Every field is optional; ``None`` leaves the client default in place, so
    a connector only states the deltas it cares about. The ReadingType fields map
    onto ``ReadingTypeSpec``. ``quality_flags`` is a 16-bit value applied to the
    ``Reading`` (not the ReadingType). ``flow_direction`` is intentionally not
    overridable -- it stays derived from the value's sign.
    """

    accumulation_behaviour: int | None = None
    data_qualifier: int | None = None
    kind: int | None = None
    commodity: int | None = None
    multiplier: int | None = None  # powerOfTenMultiplier
    uom: int | None = None
    phase: int | None = None
    quality_flags: int | None = None


__all__ = [
    "BaseConnector",
    "ConnectorConnectionError",
    "ConnectorError",
    "ConnectorPayload",
    "ConnectorTimeoutError",
    "ConnectorValueError",
    "ConnectorWriteError",
    "ReadingOverride",
    "ScheduleNotification",
]


class BaseConnector:
    """Base connector with async no-op defaults for all DER methods.

    Subclasses override only the methods their device supports.
    All methods are async to allow blocking I/O to be wrapped in executors.
    """

    connector_name: str = "BaseConnector"

    #: IEEE 2030.5 DER type code (see sep.Dertype).
    #: Common values: 4=PV, 81=EV, 82=EVSE, 83=Combined PV+Storage.
    der_type: int = 83

    #: What this connector speaks to its device, as reported in southbound
    #: telemetry. Defaults to "other" because the base class makes no claim
    #: about a wire: a connector that talks Modbus says so, and one that
    #: prints to a log should not be recorded as though it reached hardware.
    telemetry_protocol: str = "other"

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    async def fetch_monitoring(self) -> dict[str, Any]:
        """Return raw, unscaled power telemetry."""
        return {
            "W": None,
            "Var": None,
            "Hz": None,
            "V": None,
            "PF": None,
            "VA": None,
            "A": None,
        }

    def reading_overrides(self) -> dict[str, ReadingOverride] | None:
        """Optional per-quantity ReadingType / qualityFlags overrides.

        Keyed by the same monitoring key as ``fetch_monitoring`` (``"W"``,
        ``"VL2"``, ...). Return ``None`` (the default) to use the standard
        ReadingType metadata and ``qualityFlags=valid`` for every reading. A
        connector overrides only the deltas it needs; see :class:`ReadingOverride`.

        For a *static* per-key ``qualityFlags`` default, set it here. For a
        *per-cycle* value that varies with each sample (e.g. ``questionable`` on a
        stale read), put it in ``fetch_monitoring`` under ``"<key>__quality"``
        (e.g. ``{"W": 1500, "W__quality": 0x10}``); it takes precedence over the
        static default for that cycle.
        """
        return None

    async def fetch_nameplate(self) -> dict[str, Any]:
        """Return DER nameplate ratings."""
        return {
            "WMaxRtg": None,
            "WOvrExtRtg": None,
            "WOvrExtRtgPF": None,
            "WUndExtRtg": None,
            "WUndExtRtgPF": None,
            "VAMaxRtg": None,
            "VarMaxInjRtg": None,
            "VarMaxAbsRtg": None,
            "WChaRteMaxRtg": None,
            "VAChaRteMaxRtg": None,
            # Storage ratings (rtgMaxDischargeRateW/VA, rtgMaxWh, rtgMaxAh).
            # Omitted when None; a connector that reports them -- including an
            # explicit 0 -- has them serialized as-is.
            "WDisChaRteMaxRtg": None,
            "VADisChaRteMaxRtg": None,
            "WhMaxRtg": None,
            "AhMaxRtg": None,
            "VNomRtg": None,
            "VMaxRtg": None,
            "VMinRtg": None,
            "ReactSusceptRtg": None,
            "NorOpCatRtg": None,
            "AbnOpCatRtg": None,
            "CtrlModes": None,
            # CSIP-AUS only: DOE control bitmap (doeModesSupported). Connector
            # provides an int (4-bit bitmap); omitted/None defaults to all four
            # DOE limits (0x0F).
            "DoeModesSupported": None,
        }

    async def fetch_configuration(self) -> dict[str, Any]:
        """Return DER configuration settings."""
        return {
            "WMax": None,
            "WMaxOvrExt": None,
            "WOvrExtPF": None,
            "WMaxUndExt": None,
            "WUndExtPF": None,
            "VAMax": None,
            "VarMaxInj": None,
            "VarMaxAbs": None,
            "WChaRteMax": None,
            "VAChaRteMax": None,
            # Storage settings (setMaxDischargeRateW/VA, setMaxWh, setMaxAh).
            # Omitted when None; an explicit 0 is serialized as-is.
            "WDisChaRteMax": None,
            "VADisChaRteMax": None,
            "WhMax": None,
            "AhMax": None,
            "VNom": None,
            "VMax": None,
            "VMin": None,
            "CtrlModes": None,
            # CSIP-AUS only: enabled DOE control bitmap (doeModesEnabled).
            # Connector provides an int (4-bit bitmap); omitted/None -> export +
            # import limits (0x03).
            "DoeModesEnabled": None,
        }

    async def fetch_availability(self) -> dict[str, Any]:
        """Return DER availability / reserve status."""
        return {
            "availabilityDuration": None,
            "maxChargeDuration": None,
            "readingTime": None,
            "reserveChargePercent": None,
            "reservePercent": None,
            "statVarAvail": None,
            "statWAvail": None,
        }

    async def fetch_status(self) -> dict[str, Any]:
        """Return DER operational status."""
        return {
            "alarmStatus": 0,
            "connectStatus": {"dateTime": None, "value": None},
            "genConnectStatus": {"dateTime": None, "value": None},
            "inverterStatus": {"dateTime": None, "value": None},
            "manufacturerStatus": {"dateTime": None, "value": None},
            "operationalModeStatus": {"dateTime": None, "value": None},
            "stateOfChargeStatus": {"dateTime": None, "value": None},
            "localControlModeStatus": None,
            "storageModeStatus": None,
            "storConnectStatus": None,
            "readingTime": None,
        }

    # ------------------------------------------------------------------
    # Curve-based control modes
    # ------------------------------------------------------------------

    async def update_qv(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set Q(V) volt-var curve."""
        return None

    async def update_pv(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set P(V) volt-watt curve."""
        return None

    async def update_qp(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set Q(P) watt-var curve."""
        return None

    # ------------------------------------------------------------------
    # Power limits
    # ------------------------------------------------------------------

    async def update_p_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set active power limit."""
        return None

    async def update_p_lim_inj(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set active power limit (injection)."""
        return None

    async def update_p_lim_abs(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set active power limit (absorption)."""
        return None

    # ------------------------------------------------------------------
    # Frequency response
    # ------------------------------------------------------------------

    async def update_freq_watt(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set curve-based frequency-watt mode."""
        return None

    async def update_pf(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set frequency droop parameters."""
        return None

    # ------------------------------------------------------------------
    # Reactive power
    # ------------------------------------------------------------------

    async def update_const_q(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set constant reactive power."""
        return None

    async def update_const_pf(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set constant power factor."""
        return None

    # ------------------------------------------------------------------
    # Voltage ride-through
    # ------------------------------------------------------------------

    async def update_ov(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set over-voltage ride-through (must trip)."""
        return None

    async def update_uv(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set under-voltage ride-through (must trip)."""
        return None

    async def update_ov_mc(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set over-voltage momentary cessation."""
        return None

    async def update_uv_mc(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set under-voltage momentary cessation."""
        return None

    # ------------------------------------------------------------------
    # Frequency ride-through
    # ------------------------------------------------------------------

    async def update_of(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set over-frequency ride-through (must trip)."""
        return None

    async def update_uf(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set under-frequency ride-through (must trip)."""
        return None

    # ------------------------------------------------------------------
    # Fixed output and ramp
    # ------------------------------------------------------------------

    async def update_fixed_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set fixed active power output."""
        return None

    async def update_p_ramp(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set active power ramp rate."""
        return None

    async def update_es_permit_service(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set enter service permission."""
        return None

    # ------------------------------------------------------------------
    # IEEE 2030.5-2023 new modes (Gaps 16-19)
    # ------------------------------------------------------------------

    async def update_connect(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set connect/energize state (AND logic)."""
        return None

    async def update_max_lim_pct_va_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set max VA absorption as percent."""
        return None

    async def update_max_lim_pct_va_inject(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set max VA injection as percent."""
        return None

    async def update_max_lim_pct_var_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set max var absorption as percent."""
        return None

    async def update_max_lim_pct_var_inject(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set max var injection as percent."""
        return None

    async def update_max_lim_pct_w_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set max W absorption as percent."""
        return None

    async def update_max_lim_var_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set max var absorption (absolute)."""
        return None

    async def update_max_lim_var_inject(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set max var injection (absolute)."""
        return None

    async def update_target_v(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set target voltage output."""
        return None

    async def update_target_var(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set target reactive power output."""
        return None

    async def update_target_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set target active power output."""
        return None

    async def update_delta_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set delta active power change."""
        return None

    async def update_delta_var(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set delta reactive power change."""
        return None

    async def update_fixed_v(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set fixed voltage setpoint."""
        return None

    async def update_grid_connect_permit(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set grid connect permission."""
        return None

    async def update_island_permit(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set island operation permission."""
        return None

    # ------------------------------------------------------------------
    # CSIP-AUS extension modes
    # ------------------------------------------------------------------

    async def update_exp_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set export power limit."""
        return None

    async def update_imp_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set import power limit."""
        return None

    async def update_gen_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set generation power limit."""
        return None

    async def update_load_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set load power limit."""
        return None

    # ------------------------------------------------------------------
    # Pricing
    # ------------------------------------------------------------------

    async def update_pricing_mode(self, params: dict[str, Any]) -> ConnectorPayload:
        """Set pricing mode data."""
        return None

    # ------------------------------------------------------------------
    # Schedule notifications (informational push; see ScheduleNotification)
    # ------------------------------------------------------------------

    async def on_schedule_notification(self, notification: ScheduleNotification) -> None:
        """Single transport for all relayed schedule streams.

        Called by the core on every change to a control/baseline affecting this
        connector, so downstream logic has current schedule state without
        polling. The default fans out to the per-stream ``notification_*``
        convenience methods; override either this transport or the specific
        stream methods. Informational only -- exceptions are logged and
        swallowed by the core, never affecting dispatch.
        """
        handler = getattr(self, f"notification_{notification.stream}", None)
        if handler is not None:
            await handler(notification)

    async def notification_control(self, notification: ScheduleNotification) -> None:
        """A scheduled DERControl was added/updated/activated/superseded/etc."""
        return None

    async def notification_default_baseline(self, notification: ScheduleNotification) -> None:
        """The program's DefaultDERControl baseline was added or changed."""
        return None

    async def notification_doe(self, notification: ScheduleNotification) -> None:
        """A Dynamic Operating Envelope (export/import/gen/load W-limit) changed."""
        return None

    async def notification_price(self, notification: ScheduleNotification) -> None:
        """A pricing/TimeTariffInterval schedule changed (future stream)."""
        return None

    async def notification_drlc(self, notification: ScheduleNotification) -> None:
        """A demand-response (EndDeviceControl) schedule changed (future stream)."""
        return None

    async def notification_flow_reservation(self, notification: ScheduleNotification) -> None:
        """A flow-reservation window changed (future stream)."""
        return None
