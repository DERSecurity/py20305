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
from py20305.connectors.reading import Reading, ReadingIdentity

ConnectorPayload = dict[str, Any] | None


@dataclass(frozen=True)
class ScheduleNotification:
    """A relayed, forward-looking schedule event for a connector.

    Pushed by the core on every *change* to a relayed schedule -- a control,
    baseline, DOE or price -- so a downstream optimizer stays current without
    polling for it. Informational only: it does not apply setpoints, which
    remains the job of the ``update_*`` methods.
    """

    #: "control" | "default_baseline" | "doe" | "price".
    #: "drlc" | "flow_reservation" are reserved for later.
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


__all__ = [
    "BaseConnector",
    "ConnectorConnectionError",
    "ConnectorError",
    "ConnectorPayload",
    "ConnectorTimeoutError",
    "ConnectorValueError",
    "ConnectorWriteError",
    "Reading",
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
    #: telemetry. Defaults to "generic" — the consumer contract's catch-all —
    #: because the base class makes no claim about a wire: a connector that
    #: talks Modbus says so, and one that prints to a log should not be
    #: recorded as though it reached hardware.
    telemetry_protocol: str = "generic"

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    async def fetch_monitoring(self) -> list[Reading]:
        """Return the device's measurements, one :class:`Reading` each.

        Each reading states its IEEE 2030.5 ReadingType (``uom``, ``phase``,
        ``kind``, ``data_qualifier``, ...) and its value in engineering units.
        Real and reactive power are signed in the frame of the device's role
        (see :class:`~py20305.connectors.reading.Reading`); a reading profile
        that posts another convention converts them. A quantity the device does
        not measure is left out. Give each reading a distinct ReadingType,
        multiplier included: a second reading with the same one is dropped and
        a warning is logged.
        """
        return []

    def supported_modes2(self) -> int | None:
        """Optional: the DERControlType2 bits this device can apply.

        DERCapability ``modesSupported2`` and DERControlResponse
        ``modesResponded2`` report these, limited to what the server profile
        permits. Return ``None`` (the default) to derive them from the controls
        the connector implements: ``update_op_mod_max_lim_w_absorb`` sets bit 7,
        ``update_op_mod_max_lim_w_inject`` bit 8, and so on. Return an integer
        for a connector that implements a control method it cannot honor on this
        device, so the device never advertises a mode it cannot apply.
        """
        return None

    def declared_readings(self) -> frozenset[ReadingIdentity] | None:
        """Optional: the readings this device provides, fixed for its life.

        Each is a :class:`~py20305.connectors.reading.ReadingIdentity` (uom,
        phase, kind, data qualifier, accumulation behaviour, flow direction and
        multiplier), or a :class:`~py20305.connectors.reading.Reading` standing
        for its identity. Under the ``SIGNED_AS_REPORTED`` reading profile the
        MirrorUsagePoint registers exactly these readings, from the first
        cycle, even one with no values (a charger with no vehicle plugged in),
        so it is posted once and never re-posted for a reading that appears
        later. A reading returned by ``fetch_monitoring`` but not declared is
        not posted. Return ``None`` (the default) to register the readings as
        they appear. Other profiles ignore it.
        """
        return None

    async def fetch_nameplate(self) -> dict[str, Any]:
        """Return DER ratings keyed by IEEE 2030.5 DERCapability element name.

        A value is a number, ``{"value": ..., "multiplier": ...}``, or for a
        power factor ``{"displacement": ..., "multiplier": ...}``. ``rtgMaxW``
        is required. ``modesSupported`` is the DERControlType bitmap;
        ``doeModesSupported`` is the CSIP-AUS extension bitmap and defaults to
        all four limits. An absent or ``None`` key is not reported; an explicit
        0 is.
        """
        return {
            "rtgMaxW": None,
            "rtgMaxVA": None,
            "rtgMaxVar": None,
            "rtgMaxVarNeg": None,
            "rtgMaxA": None,
            "rtgOverExcitedW": None,
            "rtgOverExcitedPF": None,
            "rtgUnderExcitedW": None,
            "rtgUnderExcitedPF": None,
            "rtgMaxChargeRateW": None,
            "rtgMaxChargeRateVA": None,
            "rtgMaxDischargeRateW": None,
            "rtgMaxDischargeRateVA": None,
            "rtgMaxWh": None,
            "rtgMaxAh": None,
            "rtgVNom": None,
            "rtgMaxV": None,
            "rtgMinV": None,
            "rtgReactiveSusceptance": None,
            "rtgNormalCategory": None,
            "rtgAbnormalCategory": None,
            "modesSupported": None,
            "doeModesSupported": None,
        }

    async def fetch_configuration(self) -> dict[str, Any]:
        """Return DER settings keyed by IEEE 2030.5 DERSettings element name.

        Values take the shapes :meth:`fetch_nameplate` describes. ``setMaxW``
        is required. ``modesEnabled`` is the DERControlType bitmap;
        ``doeModesEnabled`` is the CSIP-AUS extension bitmap and defaults to
        the export and import limits.
        """
        return {
            "setMaxW": None,
            "setMaxVA": None,
            "setMaxVar": None,
            "setMaxVarNeg": None,
            "setMinPFOverExcited": None,
            "setMinPFUnderExcited": None,
            "setMaxChargeRateW": None,
            "setMaxChargeRateVA": None,
            "setMaxDischargeRateW": None,
            "setMaxDischargeRateVA": None,
            "setMaxWh": None,
            "setMaxAh": None,
            "setVNom": None,
            "setMaxV": None,
            "setMinV": None,
            "setGradW": None,
            "modesEnabled": None,
            "doeModesEnabled": None,
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

    # Controls. Each method receives one IEEE 2030.5 control element; see
    # py20305.connectors.modes for the parameter dict. A connector overrides
    # the methods for the functions its device has.

    # ------------------------------------------------------------------
    # DERControlBase elements
    # ------------------------------------------------------------------

    async def update_op_mod_connect(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModConnect."""
        return None

    async def update_op_mod_energize(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModEnergize."""
        return None

    async def update_op_mod_delta_var(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModDeltaVar."""
        return None

    async def update_op_mod_delta_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModDeltaW."""
        return None

    async def update_op_mod_fixed_pf_absorb_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModFixedPFAbsorbW."""
        return None

    async def update_op_mod_fixed_pf_inject_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModFixedPFInjectW."""
        return None

    async def update_op_mod_fixed_v(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModFixedV."""
        return None

    async def update_op_mod_fixed_var(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModFixedVar."""
        return None

    async def update_op_mod_fixed_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModFixedW."""
        return None

    async def update_op_mod_freq_droop(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModFreqDroop."""
        return None

    async def update_op_mod_freq_watt(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModFreqWatt."""
        return None

    async def update_op_mod_grid_connect_permit(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModGridConnectPermit."""
        return None

    async def update_op_mod_hfrt_may_trip(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModHFRTMayTrip."""
        return None

    async def update_op_mod_hfrt_must_trip(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModHFRTMustTrip."""
        return None

    async def update_op_mod_hvrt_may_trip(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModHVRTMayTrip."""
        return None

    async def update_op_mod_hvrt_momentary_cessation(
        self, params: dict[str, Any]
    ) -> ConnectorPayload:
        """Apply or withdraw opModHVRTMomentaryCessation."""
        return None

    async def update_op_mod_hvrt_must_trip(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModHVRTMustTrip."""
        return None

    async def update_op_mod_island_permit(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModIslandPermit."""
        return None

    async def update_op_mod_lfrt_may_trip(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModLFRTMayTrip."""
        return None

    async def update_op_mod_lfrt_must_trip(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModLFRTMustTrip."""
        return None

    async def update_op_mod_lvrt_may_trip(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModLVRTMayTrip."""
        return None

    async def update_op_mod_lvrt_momentary_cessation(
        self, params: dict[str, Any]
    ) -> ConnectorPayload:
        """Apply or withdraw opModLVRTMomentaryCessation."""
        return None

    async def update_op_mod_lvrt_must_trip(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModLVRTMustTrip."""
        return None

    async def update_op_mod_max_lim_pct_va_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModMaxLimPctVAAbsorb."""
        return None

    async def update_op_mod_max_lim_pct_va_inject(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModMaxLimPctVAInject."""
        return None

    async def update_op_mod_max_lim_pct_var_absorb(
        self, params: dict[str, Any]
    ) -> ConnectorPayload:
        """Apply or withdraw opModMaxLimPctVarAbsorb."""
        return None

    async def update_op_mod_max_lim_pct_var_inject(
        self, params: dict[str, Any]
    ) -> ConnectorPayload:
        """Apply or withdraw opModMaxLimPctVarInject."""
        return None

    async def update_op_mod_max_lim_pct_w_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModMaxLimPctWAbsorb."""
        return None

    async def update_op_mod_max_lim_var_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModMaxLimVarAbsorb."""
        return None

    async def update_op_mod_max_lim_var_inject(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModMaxLimVarInject."""
        return None

    async def update_op_mod_max_lim_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModMaxLimW."""
        return None

    async def update_op_mod_max_lim_w_absorb(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModMaxLimWAbsorb."""
        return None

    async def update_op_mod_max_lim_w_inject(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModMaxLimWInject."""
        return None

    async def update_op_mod_target_v(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModTargetV."""
        return None

    async def update_op_mod_target_var(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModTargetVar."""
        return None

    async def update_op_mod_target_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModTargetW."""
        return None

    async def update_op_mod_volt_var(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModVoltVar."""
        return None

    async def update_op_mod_volt_watt(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModVoltWatt."""
        return None

    async def update_op_mod_watt_pf(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModWattPF."""
        return None

    async def update_op_mod_watt_var(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModWattVar."""
        return None

    # ------------------------------------------------------------------
    # CSIP-AUS dynamic operating envelope limits
    # ------------------------------------------------------------------

    async def update_op_mod_exp_lim_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModExpLimW."""
        return None

    async def update_op_mod_imp_lim_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModImpLimW."""
        return None

    async def update_op_mod_gen_lim_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModGenLimW."""
        return None

    async def update_op_mod_load_lim_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply or withdraw opModLoadLimW."""
        return None

    # ------------------------------------------------------------------
    # DefaultDERControl settings
    # ------------------------------------------------------------------

    async def update_set_es_delay(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply setESDelay. IEEE 2030.5 updates the setting; it is never withdrawn."""
        return None

    async def update_set_es_high_freq(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply setESHighFreq. IEEE 2030.5 updates the setting; it is never withdrawn."""
        return None

    async def update_set_es_high_volt(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply setESHighVolt. IEEE 2030.5 updates the setting; it is never withdrawn."""
        return None

    async def update_set_es_low_freq(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply setESLowFreq. IEEE 2030.5 updates the setting; it is never withdrawn."""
        return None

    async def update_set_es_low_volt(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply setESLowVolt. IEEE 2030.5 updates the setting; it is never withdrawn."""
        return None

    async def update_set_es_ramp_tms(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply setESRampTms. IEEE 2030.5 updates the setting; it is never withdrawn."""
        return None

    async def update_set_es_random_delay(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply setESRandomDelay. IEEE 2030.5 updates the setting; it is never withdrawn."""
        return None

    async def update_set_grad_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply setGradW. IEEE 2030.5 updates the setting; it is never withdrawn."""
        return None

    async def update_set_soft_grad_w(self, params: dict[str, Any]) -> ConnectorPayload:
        """Apply setSoftGradW. IEEE 2030.5 updates the setting; it is never withdrawn."""
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
        """The active pricing interval (TimeTariffInterval) changed. Relay-only:
        the connector decides whether to act on the price."""
        return None

    async def notification_drlc(self, notification: ScheduleNotification) -> None:
        """A demand-response (EndDeviceControl) schedule changed (future stream)."""
        return None

    async def notification_flow_reservation(self, notification: ScheduleNotification) -> None:
        """A flow-reservation window changed (future stream)."""
        return None
