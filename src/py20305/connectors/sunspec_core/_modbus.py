"""Transport-neutral SunSpec Modbus connector for physical DER devices.

All Modbus I/O is wrapped in asyncio.to_thread() to avoid blocking the
event loop. Supports TCP, RTU (serial), and TCP/TLS transports via pysunspec2.

This module depends on nothing else in this package; callers
inject an optional ``report`` callback for operational diagnostics.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
import time
from collections.abc import Callable
from typing import Any, Literal

from py20305.connectors.errors import (
    ConnectorConnectionError,
    ConnectorTimeoutError,
    ConnectorValueError,
    ConnectorWriteError,
)

#: Method-return alias matching the connector contract (an optional dict).
ConnectorPayload = dict[str, Any] | None

#: Optional diagnostics sink. Matches the client's ``diagnostics.report``
#: signature ``(level, message, *, source, dedup_key, details, ...)``; the
#: an out-of-process connector leaves it unset and relies on logging.
ReportCallback = Callable[..., None]

logger = logging.getLogger(__name__)

# Readback delay when enable verification fails on first attempt (seconds)
_MB_READBACK_DELAY = 0.5

# Modbus protocol exception codes (MODBUS Application Protocol, 7 Exception
# Responses) the server device emits when a request is malformed or it can't
# satisfy the read. We treat the following as PERMANENT for the lifetime of
# the connection -- no point retrying with back-off because the device's
# answer won't change between attempts:
#
#   1  ILLEGAL FUNCTION         -- function code not supported on this server
#   2  ILLEGAL DATA ADDRESS     -- requested registers not in the server's map
#   3  ILLEGAL DATA VALUE       -- value out of range for the server
#   4  SLAVE DEVICE FAILURE     -- non-recoverable processing error (commonly
#                                  returned by SunSpec servers when the model
#                                  block we're scanning isn't present at all)
#
# Codes 5 (ACKNOWLEDGE), 6 (SLAVE DEVICE BUSY), 8 (MEMORY PARITY ERROR), 10
# (GATEWAY PATH UNAVAILABLE), and 11 (GATEWAY TARGET FAILED TO RESPOND) are
# transient -- those keep the existing 3-attempt back-off.
#
# Detection: pysunspec2's ``sunspec2.modbus.modbus.ModbusClientException``
# is the actual emitter on every transport. As of pysunspec2 1.3.5 it
# raises with three distinct format strings depending on call site:
#
#   * TCP read  (modbus.py:682):  "Modbus exception N: addr: A count: C"
#   * RTU read  (modbus.py:313):  "Modbus exception N"
#   * TCP/RTU write
#     (modbus.py:412/472/784/837): "Modbus exception: N"
#
# A regex covers all three and absorbs minor format tweaks. We don't import
# the exception type directly because pysunspec2 may eventually swap
# transports and we want classification to track the message contract, not
# the exception class.
_PERMANENT_MODBUS_CODES: frozenset[int] = frozenset({1, 2, 3, 4})
_MODBUS_EXCEPTION_RE = re.compile(r"Modbus exception:?\s*(\d+)")


def _is_permanent_modbus_error(exc: BaseException) -> bool:
    """``True`` if ``exc`` is a Modbus protocol exception that won't change
    on retry. See ``_PERMANENT_MODBUS_CODES`` for the codes."""
    match = _MODBUS_EXCEPTION_RE.search(str(exc))
    return match is not None and int(match.group(1)) in _PERMANENT_MODBUS_CODES


def _cvalue_or_none(model: Any, attr: str) -> Any:
    """Return ``model.<attr>.cvalue`` if the point exists on this model
    and is populated; ``None`` otherwise.

    Per-line points (``WL1``, ``VL2``, ...) are defined in model 701
    but a device may not expose every register. Treat both "attribute
    missing" and "raw register reads None" as no-data so the caller
    can drop that line block instead of advertising a zero reading.
    """
    point = getattr(model, attr, None)
    if point is None:
        return None
    return getattr(point, "cvalue", None)


#: Inclusive bounds on a power-factor displacement. IEEE 2030.5 requires the
#: displacement of opModFixedPFInjectW / opModFixedPFAbsorbW to be "a positive
#: value between 0.0 ... and 1.0, inclusive" (sep2_schema_2023.xsd, setMinPFOverExcited and
#: the opModFixedPF* documentation), so the lower bound is 0.0.
#:
#: Not -1.0: the reactive direction rides on the separate ``excitation`` flag, not
#: on the sign of the displacement. Admitting a negative would defeat this guard
#: on both connectors -- model 704's PF points are ``uint16``, so a negative
#: passes the range test and then dies inside pysunspec2's encoder *after* the
#: mode-enable point has been written, which is the stranded-lever failure this
#: refuses in order to avoid; and the 1xx core derives ``OutPFSet``'s sign from
#: ``excitation`` (``pf if excitation else -pf``), so a negative input silently
#: inverts the requested direction.
_PF_MIN = 0.0
_PF_MAX = 1.0


#: Inclusive bounds on an ``opModMaxLimW`` percent. IEEE 2030.5 types it as
#: PerCentControlType -- a UInt16 in hundredths of a percent, "0 - 10000. (10000
#: = 100%)" -- so the percent the translator derives spans 0..100.
_PCT_MIN = 0.0
_PCT_MAX = 100.0


#: Inclusive bounds on the raw value of a ``uint16`` SunSpec register, after the
#: model's scale factor has been applied.
_U16_MIN = 0
_U16_MAX = 65535


#: The watts-typed active-power limits, keyed by the slot name used throughout
#: this module: the IEEE 2030.5 control and the model 702 rate setting it maps
#: onto. ``opModMaxLimWInject`` limits generation, which is the discharge
#: direction; ``opModMaxLimWAbsorb`` limits absorption, which is the charge
#: direction.
_P_LIM_W_CONTROLS: dict[str, tuple[str, str]] = {
    "inj": ("opModMaxLimWInject", "WDisChaRteMax"),
    "abs": ("opModMaxLimWAbsorb", "WChaRteMax"),
}

#: The nameplate rating that declares whether the device can limit that
#: direction at all. A device advertising a maximum discharge rate of zero is
#: telling us it does not do discharge-rate limiting, whatever its setting
#: register happens to read.
_P_LIM_W_RATINGS: dict[str, str] = {
    "inj": "WDisChaRteMaxRtg",
    "abs": "WChaRteMaxRtg",
}


def _resolve_point(model: Any, dotted: str) -> Any:
    """Return the point at ``dotted`` (e.g. ``"PFWInj.PF"``).

    Plain attribute access, as the nameplate and configuration reads use: a
    pysunspec2 model exposes every point its definition declares, implemented or
    not, and an unimplemented one is signalled by ``value`` rather than by
    absence. Tolerating a missing attribute here would turn a mistyped point
    name into every control silently reporting unsupported, where an
    ``AttributeError`` names the typo.
    """
    node: Any = model
    for part in dotted.split("."):
        node = getattr(node, part)
    return node


def _unimplemented_points(model: Any, names: tuple[str, ...]) -> list[str]:
    """Which of ``names`` this device does not implement.

    Almost every setpoint in model 704 is optional, so a device can implement
    the model and none of the individual registers. An unimplemented point reads
    back as ``None``, and writing to one goes wrong in two different ways: where
    its scale factor is implemented the write is accepted and the head-end is
    told a control was applied to a register the device never advertised, and
    where the scale factor is unimplemented too pysunspec2 raises out of the
    ``cvalue`` setter -- after the enable point beside it has already been
    written, leaving an enable raised over nothing.

    Checking the target first is what avoids both. It is the same question the
    model 702 rate settings ask before choosing their route.
    """
    return [name for name in names if _resolve_point(model, name).value is None]


def _rate_setting_is_supported(model_702: Any, slot: str, point: Any) -> bool:
    """Whether this device can actually be limited through its rate setting.

    An unimplemented point is the easy case: it reads back as ``None`` and the
    caller takes the percent-of-WMax fallback. The case this exists for is the
    device that *maps* the register -- so it reads as a number, typically 0 --
    without supporting it. Keying on ``value is None`` alone sends a limit to
    such a device, which answers the write with an exception and leaves the
    control unapplied, when the fallback route would have worked. That is worse
    than the behaviour the fallback was written to preserve.

    So the question is put to the nameplate instead: model 702 publishes a
    maximum rate rating per direction, and a device that can limit discharge has
    a non-zero ``WDisChaRteMaxRtg`` to say so. Zero, absent, or a model with no
    rating point at all means no declared capability, and the fallback is taken.

    Erring towards the fallback is deliberate. It is the route every device
    handled before rate settings were used at all, so a device misjudged this
    way keeps working exactly as it used to; a device misjudged the other way
    silently fails to enforce a limit the head-end believes is in force.
    """
    if point.value is None:
        return False
    rating = getattr(model_702, _P_LIM_W_RATINGS[slot])
    return bool(rating.value)


def _as_number(value: object, label: str, quantity: str) -> float:
    """Return *value* as a finite float, or raise ``ConnectorValueError``.

    ``bool`` is rejected explicitly: it is an ``int`` subclass, so a stray
    ``True`` would otherwise sail through as 1.0 and be written as a setpoint.

    The conversion is guarded because the model layer types these fields as bare
    ``int``: an unbounded integer from a non-conformant head-end (the profile caps
    them at ``UInt16``) overflows ``float()``. Left unguarded that surfaces as an
    ``OverflowError``, which is untyped and so reports 251 instead of the 253 this
    validation exists to produce.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConnectorValueError(
            f"{label} {quantity} {value!r} is not a usable number; refusing to write."
        )
    try:
        number = float(value)
    except OverflowError as exc:
        raise ConnectorValueError(
            f"{label} {quantity} is too large to represent; refusing to write."
        ) from exc
    # Catches NaN and both infinities. The range checks below would reject all
    # three anyway (every comparison against NaN is False), but a "not a usable
    # number" message reads better than claiming it fell outside the bounds.
    if not math.isfinite(number):
        raise ConnectorValueError(
            f"{label} {quantity} {value!r} is not a usable number; refusing to write."
        )
    return number


def _require_power_factor(pf: object, label: str) -> float:
    """Return *pf* as a float, or raise if it is not a usable displacement.

    Neither the wire nor the model layer bounds this value: the generated
    ``PowerFactorWithExcitation`` types the displacement as a plain int, and
    inbound XML is not schema-validated, so a non-conformant head-end can hand
    the connector a displacement above unity. Both SunSpec registers that carry
    it (704 ``PFWInj``/``PFWAbs``, 123 ``OutPFSet``) are scaled integers wide
    enough to encode such a value, so the write would succeed on the wire and
    the device would silently clamp or discard it -- invisible to the connector,
    which reads back only the mode-enable point.

    Refuse instead, so the event is reported as rejected (Table 31 status 253)
    rather than started. Raising before any register write also keeps the
    mode-enable point down; a lever must not be raised for a write that will not
    happen.
    """
    value = _as_number(pf, label, "displacement")
    if not _PF_MIN <= value <= _PF_MAX:
        raise ConnectorValueError(
            f"{label} displacement {pf} outside [{_PF_MIN}, {_PF_MAX}]; IEEE 2030.5 "
            f"requires a displacement of at most unity. Refusing to write."
        )
    return value


def _require_percent(pct: object, label: str) -> float:
    """Return *pct* as a float percent, or raise if it is outside 0..100.

    Same unbounded-input problem as :func:`_require_power_factor`: ``PerCent`` is
    a ``UInt16`` in the profile but a bare ``int`` in the generated model, and
    the percent lands on the unsigned ``WMaxLimPct`` register. A negative value
    dies inside pysunspec2's encoder (``'H' format requires 0 <= number``) *after*
    the enable bit was raised, stranding the lever over a stale setpoint; a value
    above 100 encodes cleanly and installs a nonsense limit.

    Deliberately a refusal rather than a clamp, unlike the watts-to-percent
    conversion in ``_apply_p_lim_w_without_register``. There the *input* is a
    valid active-power limit and only the derived percent overflows, so
    reducing it to 100 still expresses the operator's limit. Here the input
    itself is outside the range the profile declares, so there is nothing to
    honour -- and clamping a negative to 0 would command full curtailment that
    nobody asked for.
    """
    value = _as_number(pct, label, "percent")
    if not _PCT_MIN <= value <= _PCT_MAX:
        raise ConnectorValueError(
            f"{label} percent {pct} outside [{_PCT_MIN}, {_PCT_MAX}]; IEEE 2030.5 types "
            f"it as a PerCent (0-10000 hundredths). Refusing to write."
        )
    return value


def _require_watts(watts: object, label: str) -> float:
    """Return *watts* as a finite, non-negative float, or raise.

    Route-independent validation, applied before the connector decides whether a
    limit lands on a model 702 rate setting or on the percent fallback. Both
    routes need a usable non-negative magnitude, and neither can be trusted to
    notice a bad one on its own: the register path would hand it to pysunspec2's
    encoder, and the fallback would divide it into a percent, where a negative
    silently clamps to 0 and commands the full curtailment nobody asked for.

    Negative is a refusal rather than a clamp because IEEE 2030.5 types these
    controls as ``UnsignedActivePower``: a negative is not a limit the profile
    can express, so there is nothing to honour.
    """
    value = _as_number(watts, label, "watts")
    if value < 0:
        raise ConnectorValueError(
            f"{label} {watts} W is negative; IEEE 2030.5 types it as an "
            f"UnsignedActivePower. Refusing to write."
        )
    return value


def _require_uint16_watts(watts: float, w_sf: int, label: str) -> float:
    """Return *watts*, or raise if it will not encode into the register.

    Takes the device-specific upper bound that :func:`_require_watts` cannot:
    the model 702 rate settings are ``uint16`` scaled by ``W_SF``, so the value
    that actually reaches the wire is ``watts / 10**W_SF``. Nothing upstream
    bounds the watts against *this* device's ``W_SF``, and an out-of-range
    result dies inside pysunspec2's encoder (``'H' format requires
    0 <= number <= 65535``) partway through the write.

    Deliberately a refusal rather than a clamp. Clamping a derived percent to 100
    still expresses the operator's limit, because the percent is only a
    representation of it; here an overflowing value is one this device cannot
    represent at all, so there is nothing faithful to write.
    """
    raw = round(watts / (10**w_sf))
    if not _U16_MIN <= raw <= _U16_MAX:
        raise ConnectorValueError(
            f"{label} {watts} W does not fit the model 702 uint16 register at "
            f"W_SF={w_sf} (encodes to {raw}, outside [{_U16_MIN}, {_U16_MAX}]). "
            f"Refusing to write."
        )
    return watts


class SunSpecModbusConnector:
    """SunSpec Modbus connector for physical DER devices.

    Requires pysunspec2 to be installed. All Modbus operations are
    synchronous and wrapped in asyncio.to_thread().

    Transport-neutral: this class is the shared core behind both the
    in-process connector and an out-of-process one. It takes an
    optional ``report`` callback for operational diagnostics; when unset
    it falls back to logging and raised errors.
    """

    def __init__(
        self,
        *,
        transport: Literal["tcp", "rtu", "tcp+tls"] = "tcp",
        # TCP parameters (used by "tcp" and "tcp+tls")
        host: str = "127.0.0.1",
        port: int = 8502,
        # RTU parameters (used by "rtu")
        serial_port: str = "/dev/ttyUSB0",
        baudrate: int = 9600,
        parity: str = "N",
        # TLS parameters (used by "tcp+tls")
        ca_path: str | None = None,
        cert_path: str | None = None,
        key_path: str | None = None,
        insecure: bool = False,
        # Common parameters
        unit_id: int = 1,
        timeout: int = 5,
        scan_retries: int = 3,
        scan_retry_delay: float = 2.0,
        der_type: int = 83,
        report: ReportCallback | None = None,
    ) -> None:
        import sunspec2.modbus.client as ss_client  # type: ignore[import-untyped]

        self._report = report
        self.der_type = der_type
        # Lazily allocate the lock against the loop that's actually running
        # when fetch_*/update_* is first awaited, and rebind if the running
        # loop changes. asyncio.Lock instances bind to whichever loop first
        # awaited them and raise RuntimeError if reused on another loop --
        # which happens whenever a long-lived connector is shared across
        # event loops.
        self._lock: asyncio.Lock | None = None
        # State for the 704 WMaxLimPct register, which is a percent of WMax.
        # ``"any"`` is opModMaxLimW, its only routine contributor. ``"inj"`` is
        # opModMaxLimWInject, which normally writes model 702 WDisChaRteMax
        # instead and populates this slot only on the fallback route taken when
        # a device does not implement that register. Tracking them separately
        # keeps a write to one from clobbering the other;
        # ``_compute_merged_inject_pct`` collapses them to the effective
        # register value. Every value here is a percent -- nothing else may be
        # stored in these slots, since the merge compares them directly.
        self._p_lim_slots: dict[str, tuple[bool, float | None]] = {
            "any": (False, None),
            "inj": (False, None),
        }
        # Last watts written to each model 702 rate setting (keyed as in
        # ``_P_LIM_W_CONTROLS``), for diagnostics. Held apart from
        # ``_p_lim_slots`` precisely because the units differ: these are raw
        # watts and must never reach the percent merge.
        self._p_lim_w_settings: dict[str, float | None] = {"inj": None, "abs": None}
        # The device's own WMax, captured on the first mirror write of an inject
        # event and written back when the control clears. None means nothing is
        # mirrored, so there is nothing to restore.
        self._wmax_baseline: float | None = None
        # Suppresses log noise after the first enable per device on the one
        # combination that cannot be applied at all: opModMaxLimWAbsorb on a
        # device implementing neither WChaRteMax nor any absorb-direction limit.
        self._p_lim_abs_warned: bool = False
        #: Controls already reported as unsupported by this device. A
        #: head-end re-sends a control every event, and an operator needs
        #: to see the gap once, not on every dispatch.
        self._unsupported_reported: set[str] = set()
        self._lock_loop: asyncio.AbstractEventLoop | None = None

        if transport == "tcp":
            self._target = ss_client.SunSpecModbusClientDeviceTCP(
                slave_id=unit_id,
                ipaddr=host,
                ipport=port,
                timeout=timeout,
            )
            self._endpoint_id = f"{host}:{port}"
        elif transport == "rtu":
            self._target = ss_client.SunSpecModbusClientDeviceRTU(
                slave_id=unit_id,
                name=serial_port,
                baudrate=baudrate,
                parity=parity,
                timeout=timeout,
            )
            self._endpoint_id = f"rtu:{serial_port}"
        elif transport == "tcp+tls":
            self._target = ss_client.SunSpecModbusClientDeviceTCP(
                slave_id=unit_id,
                ipaddr=host,
                ipport=port,
                timeout=timeout,
                tls=True,
                cafile=ca_path,
                certfile=cert_path,
                keyfile=key_path,
                insecure_skip_tls_verify=insecure,
            )
            self._endpoint_id = f"{host}:{port}"
        else:
            msg = f"Unknown transport {transport!r}; expected 'tcp', 'rtu', or 'tcp+tls'"
            raise ValueError(msg)

        self._scan_with_readiness_check(scan_retries, scan_retry_delay)

    def _scan_with_readiness_check(self, retries: int, retry_delay: float) -> None:
        """Scan SunSpec device and verify critical registers are readable.

        After a successful scan, reads model 702 to confirm that WMaxRtg
        is populated (not None).  If the device is still initializing its
        register map, the scan may succeed but reads return None; retrying
        handles that race.

        Permanent Modbus protocol exceptions (codes 1-4 -- illegal function /
        address / value, server failure) bail out after a single attempt
        without back-off. The server's answer won't change on retry, and the
        per-cycle 3 x ``retry_delay`` overhead can starve the asyncio event
        loop.
        """
        last_err: Exception | None = None
        attempts_made = 0
        for attempt in range(1, retries + 1):
            attempts_made = attempt
            try:
                self._target.scan()
                self._verify_nameplate_readable()
                return
            except Exception as exc:
                last_err = exc
                if _is_permanent_modbus_error(exc):
                    # Permanent server-side error; further attempts will get
                    # the same response. Don't burn the retry budget or block
                    # the event loop on back-off sleeps. Logged at WARNING
                    # because a configured device missing models the
                    # the connector was told to scan is an operator-visible
                    # misconfiguration, not informational chatter.
                    logger.warning(
                        "SunSpec scan of %s failed with permanent Modbus error (no retry): %s",
                        self._endpoint_id,
                        exc,
                    )
                    break
                if attempt < retries:
                    logger.warning(
                        "SunSpec scan failed (attempt %d/%d): %s — retrying in %.0fs",
                        attempt,
                        retries,
                        exc,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
        # Match the per-cycle ``_get_model`` convention: use a separate
        # dedup key for permanent vs transient errors so the warnings
        # panel doesn't collapse two operationally-distinct failure
        # modes (configured-but-missing-model vs unreachable-device)
        # into one entry.
        is_permanent = last_err is not None and _is_permanent_modbus_error(last_err)
        dedup_suffix = "permanent" if is_permanent else "transient"
        # Report the actual attempts executed -- permanent errors break out
        # after one attempt, so saying "failed after 3 attempts" when only
        # one was made misleads operators reading the diagnostics panel.
        attempt_phrase = (
            "after 1 attempt (permanent error, no retry)"
            if is_permanent
            else f"after {attempts_made} attempts"
        )
        if self._report is not None:
            self._report(
                "warnings",
                f"SunSpec scan of {self._endpoint_id} failed {attempt_phrase}: {last_err}",
                source="connector",
                dedup_key=f"sunspec_scan_{dedup_suffix}:{self._endpoint_id}",
                details={"endpoint": self._endpoint_id, "error": str(last_err)},
            )
        raise ConnectorConnectionError(
            f"SunSpec scan failed {attempt_phrase}: {last_err}",
            permanent=is_permanent,
        ) from last_err

    def _verify_nameplate_readable(self) -> None:
        """Read model 702 and check that WMaxRtg and W_SF are not None.

        Raises ConnectorConnectionError if model 702 is missing or the
        critical registers have not been populated yet.
        """
        model_instances = self._target.models.get(702)
        if not model_instances:
            raise ConnectorConnectionError("Model 702 not found after scan")
        model = model_instances[0]
        model.read()
        if model.WMaxRtg.value is None:
            raise ConnectorConnectionError("Model 702 scanned but WMaxRtg is not yet populated")
        if model.W_SF.value is None:
            raise ConnectorConnectionError("Model 702 scanned but W_SF is not yet populated")

    # ------------------------------------------------------------------
    # Internal helpers (all synchronous, called via to_thread)
    # ------------------------------------------------------------------

    def _reconnect(self) -> None:
        """Reset the Modbus TCP connection to recover from broken pipes."""
        with contextlib.suppress(Exception):
            self._target.disconnect()
        try:
            self._target.connect()
            logger.info("Modbus reconnection successful")
        except Exception as exc:
            raise ConnectorConnectionError(f"Modbus reconnection failed: {exc}") from exc

    def _get_model(self, mid: int) -> Any:
        """Read and return a SunSpec model by ID.

        On a transient failure (broken pipe, timeout), reconnects and retries
        once before raising.

        Permanent Modbus protocol exceptions (codes 1-4: illegal function /
        address / value, server failure) skip the reconnect-and-retry: the
        server's answer won't change, and the per-cycle reconnect handshake
        adds latency the polling loop pays on every cycle. Same root cause
        as scan-time fail-fast -- a device that lost a model block at
        runtime (firmware update, register-map change) would otherwise
        burn a reconnect on every fetch.
        """
        model_instances = self._target.models.get(mid)
        if not model_instances:
            raise ConnectorConnectionError(f"Device model {mid} not found")

        model = model_instances[0]
        try:
            model.read()
        except Exception as exc:
            if _is_permanent_modbus_error(exc):
                if self._report is not None:
                    self._report(
                        "warnings",
                        f"Modbus read failed for {self._endpoint_id} model {mid} "
                        f"with permanent error (no reconnect): {exc}",
                        source="connector",
                        dedup_key=f"sunspec_read_permanent:{self._endpoint_id}:{mid}",
                        details={
                            "endpoint": self._endpoint_id,
                            "model_id": mid,
                            "error": str(exc),
                        },
                    )
                raise ConnectorConnectionError(
                    f"Permanent Modbus error reading model {mid}: {exc}",
                    permanent=True,
                ) from exc

            if self._report is not None:
                self._report(
                    "warnings",
                    f"Modbus read failed for {self._endpoint_id} model {mid}: {exc} — reconnecting",
                    source="connector",
                    dedup_key=f"sunspec_read:{self._endpoint_id}:{mid}",
                    details={
                        "endpoint": self._endpoint_id,
                        "model_id": mid,
                        "error": str(exc),
                    },
                )
            self._reconnect()
            try:
                model.read()
            except Exception as retry_exc:
                raise ConnectorConnectionError(
                    f"Error reading model {mid} after reconnect: {retry_exc}"
                ) from retry_exc
        return model

    def _update_enable(self, point: Any, value: int, label: str) -> None:
        """Write an enable point and verify with readback."""
        point.cvalue = value
        point.write()
        point.read()
        if point.cvalue != value:
            time.sleep(_MB_READBACK_DELAY)
            point.read()
            if point.cvalue != value:
                raise ConnectorWriteError(
                    f"{label} enable verification failed; expected {value}, read {point.cvalue}"
                )

    def _adopt_curve(self, model: Any, index: int = 2, timeout: int = 5) -> bool:
        """Request curve adoption and poll for result."""
        model.AdptCrvReq.value = index
        model.AdptCrvReq.write()
        elapsed = 0
        while elapsed < timeout:
            time.sleep(1)
            elapsed += 1
            model.AdptCrvRslt.read()
            result = model.AdptCrvRslt.value
            if result == 2:
                raise ConnectorWriteError(f"Adopt curve failed for curve {index}")
            if result == 1:
                return True
        raise ConnectorTimeoutError(f"Adopt curve timed out after {timeout}s for curve {index}")

    @staticmethod
    def _require_curve_capacity(crv: Any, n_points: int, mode: str) -> None:
        """Fail loudly when a control curve has more points than the device
        model's curve block can hold (NPt).

        The per-point write loops index ``crv.Pt[i]``; that block is a
        fixed-size SunSpec repeating group sized when the device instantiates
        the model. Writing more points than it holds otherwise dies with an
        opaque ``IndexError: list index out of range`` mid-dispatch. Surface
        an actionable error instead -- the device must be reconfigured with a
        larger NPt (the client can't grow a fixed-size device curve).
        """
        capacity = len(crv.Pt)
        if n_points > capacity:
            raise ConnectorWriteError(
                f"{mode} curve has {n_points} points but the device model holds only "
                f"{capacity} (NPt). Reduce the curve points or reconfigure the device "
                f"with a larger curve."
            )

    def _adopt_control(self, model: Any, index: int = 2, timeout: int = 5) -> bool:
        """Request control adoption and poll for result."""
        model.AdptCtlReq.value = index
        model.AdptCtlReq.write()
        elapsed = 0
        while elapsed < timeout:
            time.sleep(1)
            elapsed += 1
            model.AdptCtlRslt.read()
            result = model.AdptCtlRslt.value
            if result == 2:
                raise ConnectorWriteError(f"Adopt control failed for control {index}")
            if result == 1:
                return True
        raise ConnectorTimeoutError(f"Adopt control timed out after {timeout}s for control {index}")

    # ------------------------------------------------------------------
    # Synchronous implementations (wrapped by async methods)
    # ------------------------------------------------------------------

    def _fetch_monitoring_sync(self) -> dict[str, Any]:
        default = {"W": None, "Var": None, "Hz": None, "V": None, "PF": None, "VA": None, "A": None}
        try:
            model = self._get_model(701)
        except ConnectorConnectionError:
            return default

        result: dict[str, Any] = {
            "W": model.W.cvalue,
            "Var": model.Var.cvalue,
            "Hz": model.Hz.cvalue,
            "V": model.LLV.cvalue,
            "PF": model.PF.cvalue,
            "VA": model.VA.cvalue,
            "A": model.A.cvalue,
        }
        # ACType drives how many per-line reading sets a consumer
        # publishes. Model 701 declares this field mandatory but real
        # devices sometimes leave it unpopulated; treat a missing value
        # as "system readings only".
        ac_type = getattr(getattr(model, "ACType", None), "value", None)
        if ac_type is not None:
            result["ACType"] = ac_type

        # Per-line points (model 701 lines L1/L2/L3). Each line block is
        # only emitted if at least one of its points has a real value,
        # which keeps a single-phase inverter from advertising fake
        # zero-valued L2/L3 readings just because the SunSpec register
        # bank happens to exist in the model definition.
        for line in (1, 2, 3):
            line_points = {
                f"WL{line}": _cvalue_or_none(model, f"WL{line}"),
                f"VarL{line}": _cvalue_or_none(model, f"VarL{line}"),
                f"VL{line}": _cvalue_or_none(model, f"VL{line}"),
                f"PFL{line}": _cvalue_or_none(model, f"PFL{line}"),
                f"VAL{line}": _cvalue_or_none(model, f"VAL{line}"),
                f"AL{line}": _cvalue_or_none(model, f"AL{line}"),
            }
            if any(v is not None for v in line_points.values()):
                result.update(line_points)
        return result

    def _fetch_nameplate_sync(self) -> dict[str, Any]:
        model = self._get_model(702)

        # WMaxRtg and W_SF are required by IEEE 2030.5 DERCapability.  If
        # either reads as None the device registers haven't finished
        # initialising yet; raise so _capability_cycle logs a clear message
        # and retries on the next cycle.
        if model.WMaxRtg.value is None:
            raise ConnectorConnectionError(
                "Model 702 WMaxRtg is not yet populated (device still initialising)"
            )
        if model.W_SF.value is None:
            raise ConnectorConnectionError(
                "Model 702 W_SF is not yet populated (device still initialising)"
            )

        result: dict[str, Any] = {}
        # Scale factor fields -- only include when the point value is not None
        # (unimplemented SunSpec registers return None).
        _sf_entries: list[tuple[str, str, Any, Any]] = [
            ("WMaxRtg", "value", model.WMaxRtg.value, model.W_SF.value),
            ("WOvrExtRtg", "value", model.WOvrExtRtg.value, model.W_SF.value),
            ("WOvrExtRtgPF", "displacement", model.WOvrExtRtgPF.value, model.PF_SF.value),
            ("WUndExtRtg", "value", model.WUndExtRtg.value, model.W_SF.value),
            ("WUndExtRtgPF", "displacement", model.WUndExtRtgPF.value, model.PF_SF.value),
            ("VAMaxRtg", "value", model.VAMaxRtg.value, model.VA_SF.value),
            ("VarMaxInjRtg", "value", model.VarMaxInjRtg.value, model.Var_SF.value),
            ("VarMaxAbsRtg", "value", model.VarMaxAbsRtg.value, model.Var_SF.value),
            ("WChaRteMaxRtg", "value", model.WChaRteMaxRtg.value, model.W_SF.value),
            ("WDisChaRteMaxRtg", "value", model.WDisChaRteMaxRtg.value, model.W_SF.value),
            ("VAChaRteMaxRtg", "value", model.VAChaRteMaxRtg.value, model.VA_SF.value),
            ("VNomRtg", "value", model.VNomRtg.value, model.V_SF.value),
            ("VMaxRtg", "value", model.VMaxRtg.value, model.V_SF.value),
            ("VMinRtg", "value", model.VMinRtg.value, model.V_SF.value),
            ("ReactSusceptRtg", "value", model.ReactSusceptRtg.value, model.S_SF.value),
        ]
        for key, val_key, val, mult in _sf_entries:
            if val is not None and mult is not None:
                result[key] = {val_key: val, "multiplier": mult}
        # Direct integer fields
        result["NorOpCatRtg"] = model.NorOpCatRtg.cvalue
        result["AbnOpCatRtg"] = model.AbnOpCatRtg.cvalue
        result["CtrlModes"] = model.CtrlModes.cvalue
        # CSIP-AUS doeModesSupported: advertise export only (opModExpLimW, 0x01)
        # rather than the all-four default. This connector inherits
        # ``update_exp_lim`` / ``update_imp_lim`` as no-ops from the base class,
        # so no DoE mode is actually enforced; 0x01 is the narrowest honest
        # claim. Widening it is gated on implementing those two methods, not on
        # which registers the opModMaxLimW* controls reach. Only used when the
        # client is in CSIP-AUS mode.
        result["DoeModesSupported"] = 0x01
        return result

    def _fetch_status_sync(self) -> dict[str, Any]:
        try:
            model = self._get_model(701)
        except ConnectorConnectionError:
            return {}

        now = int(time.time())
        result: dict[str, Any] = {
            "connectStatus": {"dateTime": now, "value": model.ConnSt.cvalue},
            "inverterStatus": {"dateTime": now, "value": model.InvSt.cvalue},
            "manufacturerStatus": {"dateTime": now, "value": "NoErr"},
            "operationalModeStatus": {"dateTime": now, "value": model.St.cvalue},
            "alarmStatus": model.Alrm.cvalue,
            "readingTime": now,
        }
        # Only include optional fields when the connector has a real value
        # (emitting value=0 for unknown fields causes the server to reject
        # the DERStatus XML with 400).
        # localControlModeStatus, storageModeStatus, storConnectStatus are
        # not available from SunSpec model 701.
        return result

    def _fetch_configuration_sync(self) -> dict[str, Any]:
        model = self._get_model(702)

        result: dict[str, Any] = {}
        _sf_entries: list[tuple[str, str, Any, Any]] = [
            ("WMax", "value", model.WMax.value, model.W_SF.value),
            ("WMaxOvrExt", "value", model.WMaxOvrExt.value, model.W_SF.value),
            ("WOvrExtPF", "displacement", model.WOvrExtPF.value, model.PF_SF.value),
            ("WMaxUndExt", "value", model.WMaxUndExt.value, model.W_SF.value),
            ("WUndExtPF", "displacement", model.WUndExtPF.value, model.PF_SF.value),
            ("VAMax", "value", model.VAMax.value, model.VA_SF.value),
            ("VarMaxInj", "value", model.VarMaxInj.value, model.Var_SF.value),
            ("VarMaxAbs", "value", model.VarMaxAbs.value, model.Var_SF.value),
            ("WChaRteMax", "value", model.WChaRteMax.value, model.W_SF.value),
            ("WDisChaRteMax", "value", model.WDisChaRteMax.value, model.W_SF.value),
            ("VAChaRteMax", "value", model.VAChaRteMax.value, model.VA_SF.value),
            ("VNom", "value", model.VNom.value, model.V_SF.value),
            ("VMax", "value", model.VMax.value, model.V_SF.value),
            ("VMin", "value", model.VMin.value, model.V_SF.value),
        ]
        for key, val_key, val, mult in _sf_entries:
            if val is not None and mult is not None:
                result[key] = {val_key: val, "multiplier": mult}
        result["CtrlModes"] = model.CtrlModes.cvalue
        # CSIP-AUS doeModesEnabled: keep consistent with doeModesSupported
        # (enabled must not exceed supported), so export only (0x01), not the
        # 0x03 default. See the rationale in ``_fetch_nameplate_sync``.
        result["DoeModesEnabled"] = 0x01
        return result

    def _update_qv_sync(self, params: dict[str, Any]) -> None:
        model = self._get_model(705)
        self._update_enable(model.Ena, params.get("qv_mode_enable", 0), "Voltage-Reactive Power")

        if params.get("qv_mode_enable") != 1:
            return

        auto_vref = 0 if not params.get("qv_vref_auto_ena") else 1
        v_pts = params.get("qv_curve_v_pts", [])
        q_pts = params.get("qv_curve_q_pts", [])

        crv = model.Crv[1]
        self._require_curve_capacity(crv, len(v_pts), "Volt-Var (Q(V))")
        crv.ActPt.value = len(v_pts)
        crv.DeptRef.value = 3  # % VA Max
        crv.Pri.value = 1  # Reactive power priority
        # Engineering cvalues -- pysunspec2 handles scale-factor rounding.
        crv.VRef.cvalue = params.get("qv_vref", 0)
        crv.VRefAutoEna.cvalue = auto_vref
        crv.VRefAutoTms.cvalue = params.get("qv_vref_olrt") or 0
        crv.RspTms.cvalue = params.get("qv_olrt") or 0

        for i, (v, q) in enumerate(zip(v_pts, q_pts, strict=True)):
            crv.Pt[i].V.cvalue = v
            crv.Pt[i].Var.cvalue = q

        crv.write()
        self._adopt_curve(model)

    def _update_pv_sync(self, params: dict[str, Any]) -> None:
        """Apply the P(V) volt-watt curve (uses .W.cvalue for curve points)."""
        model = self._get_model(706)
        self._update_enable(model.Ena, params.get("pv_mode_enable", 0), "Voltage-Active Power")

        if params.get("pv_mode_enable") != 1:
            return

        v_pts = params.get("pv_curve_v_pts", [])
        p_pts = params.get("pv_curve_p_pts", [])

        crv = model.Crv[1]
        self._require_curve_capacity(crv, len(v_pts), "Volt-Watt (P(V))")
        crv.ActPt.value = len(v_pts)
        crv.DeptRef.value = 0  # % W Max
        crv.RspTms.cvalue = params.get("pv_olrt") or 0

        for i, (v, p) in enumerate(zip(v_pts, p_pts, strict=True)):
            crv.Pt[i].V.cvalue = v
            crv.Pt[i].W.cvalue = p

        crv.write()
        self._adopt_curve(model)

    def _update_qp_sync(self, params: dict[str, Any]) -> None:
        model = self._get_model(712)
        self._update_enable(
            model.Ena, params.get("qp_mode_enable", 0), "Active Power-Reactive Power"
        )

        if params.get("qp_mode_enable") != 1:
            return

        p_pts = params.get("qp_curve_p_pts", [])
        q_pts = params.get("qp_curve_q_pts", [])

        crv = model.Crv[1]
        self._require_curve_capacity(crv, len(p_pts), "Watt-Var (Q(P))")
        crv.ActPt.value = len(p_pts)
        crv.DeptRef.value = 3  # % VA Max

        for i, (p, q) in enumerate(zip(p_pts, q_pts, strict=True)):
            crv.Pt[i].W.cvalue = p
            crv.Pt[i].Var.cvalue = q

        crv.write()
        self._adopt_curve(model)

    def _compute_merged_inject_pct(self) -> tuple[bool, float | None]:
        """Collapse the active inject-direction p-limit slots into the
        single value that goes onto WMaxLimPct.

        ``opModMaxLimW`` ("any") and ``opModMaxLimWInject`` ("inj") are
        independent constraints on the same physical quantity. When both
        are active we honour the *more restrictive* of the two (the
        operator can't loosen one limit by sending a higher value via
        the other field). When neither slot is active, the inject-
        direction cap is disabled and WMaxLimPctEna gets driven low.

        ``"inj"`` only participates on the fallback route: opModMaxLimWInject
        normally writes model 702 WDisChaRteMax and leaves this slot empty. The
        merge stays because both slots can still be populated at once on a
        device that does not implement that register.
        """
        candidates: list[float] = []
        for slot in ("any", "inj"):
            enabled, value = self._p_lim_slots[slot]
            if enabled and value is not None:
                candidates.append(value)
        if not candidates:
            return (False, None)
        return (True, min(candidates))

    def _apply_inject_pct_slot(self, slot: str, enabled: bool, pct: float | None) -> None:
        """Store an inject-direction p-limit slot and (re)write WMaxLimPct.

        ``slot`` is ``"any"`` (opModMaxLimW) or ``"inj"`` (opModMaxLimWInject on
        the WDisChaRteMax-unavailable fallback route). Both express a percent of
        WMax; ``_compute_merged_inject_pct`` collapses the active ones to the
        most-restrictive value that goes onto the single SunSpec WMaxLimPct
        register. When no inject-direction slot is active the enable bit is
        driven low.
        """
        self._p_lim_slots[slot] = (enabled, pct)

        model = self._get_model(704)
        merged_enabled, merged_value = self._compute_merged_inject_pct()
        # Checked before the enable: raising it over a register this device does
        # not implement asserts a limit nothing is holding. A teardown still
        # runs -- driving an enable low is safe whether or not the setpoint
        # beside it exists, and refusing to lower one would be worse.
        if merged_enabled:
            missing = _unimplemented_points(model, ("WMaxLimPct",))
            if missing:
                self._control_unsupported("opModMaxLimW", missing)
                return
        self._update_enable(
            model.WMaxLimPctEna,
            1 if merged_enabled else 0,
            "Limit Maximum Active Power",
        )

        if not merged_enabled:
            return

        # ``merged_value`` is guaranteed non-None when ``merged_enabled``
        # is True (the merge helper only flips the bool when at least
        # one populated slot contributed a value).
        assert merged_value is not None
        model.WMaxLimPct.cvalue = merged_value
        model.write()

    def _update_p_lim_sync(self, params: dict[str, Any]) -> None:
        """Apply opModMaxLimW (the ``"any"`` slot).

        opModMaxLimW is PerCentControlType -- ``p_lim_w`` is already a percent
        of WMax and goes straight onto WMaxLimPct. The watts-typed
        opModMaxLimWInject / opModMaxLimWAbsorb controls are handled by
        ``_update_p_lim_w_sync``.
        """
        enabled = params.get("p_lim_mode_enable", 0) == 1
        pct = params.get("p_lim_w")
        # Only an enabled control carries a meaningful percent; a teardown's
        # stale or absent value must not turn into a rejection. ``None`` on an
        # enabled control stays permitted -- the merge helper drops an
        # unpopulated slot, which is the existing "enabled but no value"
        # behaviour shared with the inject path.
        if enabled and pct is not None:
            pct = _require_percent(pct, "opModMaxLimW")
        self._apply_inject_pct_slot("any", enabled, pct)

    def _update_p_lim_w_sync(self, params: dict[str, Any], slot: str) -> None:
        """Apply opModMaxLimWInject (``"inj"``) / opModMaxLimWAbsorb (``"abs"``).

        Both are UnsignedActivePowerControlType -- an absolute active-power limit
        in *watts* (``p_lim_watts``), not a percent -- so each maps straight onto
        the matching model 702 rate setting: generation (``"inj"``) onto
        ``WDisChaRteMax``, absorption (``"abs"``) onto ``WChaRteMax``.

        Inject additionally mirrors its value onto ``WMax`` -- see
        ``_mirror_inject_onto_wmax`` for why.

        Neither rate setting carries an IEEE 1547 standards tag in model 702, so
        a device need not implement either; ``_apply_p_lim_w_without_register``
        handles that case.

        Clearing a control writes nothing to model 702. These are settings
        registers with no enable bit, and the post-event state is governed by the
        default DERControl rather than by restoring a captured baseline.
        """
        if slot not in _P_LIM_W_CONTROLS:
            msg = f"_update_p_lim_w_sync expects 'inj' or 'abs', got {slot!r}"
            raise ValueError(msg)

        control, point_name = _P_LIM_W_CONTROLS[slot]
        enabled = params.get("p_lim_mode_enable", 0) == 1
        watts = params.get("p_lim_watts")

        if enabled and watts is None:
            logger.warning("%s enabled but carried no value; nothing to apply", control)

        if not enabled or watts is None:
            # The rate setting keeps its last written value -- see the
            # docstring. What the connector put there on its own initiative it
            # does take back: the mirrored WMax, and an inject cap the fallback
            # route installed on 704 WMaxLimPct, which would otherwise outlive
            # the event that imposed it.
            self._p_lim_w_settings[slot] = None
            if slot == "inj":
                self._restore_mirrored_wmax()
                # Guarded: only the fallback route populates this slot. On the
                # direct route model 704 belongs to opModMaxLimW alone, and
                # re-entering the merge would rewrite that control's register
                # for a teardown that has nothing to do with it.
                if self._p_lim_slots["inj"] != (False, None):
                    self._apply_inject_pct_slot("inj", enabled, None)
            elif not enabled:
                # Re-arm the not-applied warning so a future enable is surfaced
                # once more rather than silently swallowed.
                self._p_lim_abs_warned = False
            return

        # Validated before the route is chosen: the fallback divides this value
        # into a percent, where a negative would silently clamp to full
        # curtailment and a non-number would raise something untyped.
        value = _require_watts(watts, control)

        model_702 = self._get_model(702)
        point = getattr(model_702, point_name)
        if not _rate_setting_is_supported(model_702, slot, point):
            self._apply_p_lim_w_without_register(slot, value, model_702)
            return

        _require_uint16_watts(value, model_702.W_SF.value, control)
        point.cvalue = value
        if slot == "inj":
            self._mirror_inject_onto_wmax(model_702, value)
        model_702.write()
        self._p_lim_w_settings[slot] = value

    def _mirror_inject_onto_wmax(self, model_702: Any, watts: float) -> None:
        """Write an inject limit to WMax as well as WDisChaRteMax.

        Some PV systems do not implement the charge / discharge rate points at
        all and honour only WMax, so an inject limit that went solely to
        WDisChaRteMax would not be enforced on them. Writing both to the same
        value costs one register in the same transaction and covers both kinds
        of device; where both are honoured the two agree, so the extra write is
        inert.

        WMax is itself optional -- no mandatory flag and no IEEE 1547 standards
        tag in model 702, exactly like the two rate settings -- so a device may
        implement WDisChaRteMax and not WMax. Writing it unguarded would hand
        such a device a register it never advertised: at best ignored while the
        head-end is told the limit was applied, at worst rejected, which fails
        the whole model write and takes WDisChaRteMax down with it. That is the
        same failure the WMaxRtg clamp below exists to prevent.

        Clamped to the nameplate rating when it is known. A limit above WMaxRtg
        is a valid request -- it simply does not bind -- but a WMax *above*
        nameplate is meaningless.

        The device's own WMax is captured on the *first* mirror write and put
        back by ``_restore_mirrored_wmax`` when the control clears, so a second
        inject update within one event records what the device had rather than
        what the first update wrote. The capture is in memory, so a connector
        restart mid-event forfeits the restore; the alternative is a register
        read on every teardown to guess at a baseline, which is worse.
        """
        wmax_point = model_702.WMax
        if wmax_point.value is None:  # register not implemented by this device
            return

        if self._wmax_baseline is None:
            self._wmax_baseline = wmax_point.cvalue

        wmax_rtg = model_702.WMaxRtg.cvalue
        target = min(watts, wmax_rtg) if wmax_rtg else watts
        wmax_point.cvalue = target

    def _restore_mirrored_wmax(self) -> None:
        """Put WMax back where the device had it before an inject limit lowered it.

        The rate settings themselves stay where the event left them: an operator
        limit outliving its event is the DefaultDERControl's problem, and those
        registers are where the head-end's control actually landed. WMax is
        different in kind. No head-end control writes it; the connector lowered
        it on its own initiative to cover devices that honour only WMax, so the
        connector owes the restore -- the same reasoning that has the fallback
        route drive WMaxLimPctEna low on teardown rather than leave a lever it
        raised itself.

        Leaving it lowered would also size a later opModMaxLimW to an event that
        has ended, since WMax is the base WMaxLimPct is a percent of.

        A no-op when nothing was mirrored, which covers both a device that does
        not implement WMax and the fallback route, where the mirror never runs.
        """
        if self._wmax_baseline is None:
            return

        model_702 = self._get_model(702)
        model_702.WMax.cvalue = self._wmax_baseline
        model_702.write()
        self._wmax_baseline = None

    def _watts_as_pct_of_wmax(self, watts: float, control: str) -> float | None:
        """Express ``watts`` as a percent of the device's max active power.

        ``None`` when no base is available, in which case nothing is written and
        the caller returns. WMax is the settable maximum; a device that does not
        implement it falls back to the nameplate WMaxRtg, which the
        scan-readiness check guarantees. That is an approximation -- it treats
        nameplate as the effective maximum -- and it holds precisely when there
        is no settable WMax to be lower than it.
        """
        model_702 = self._get_model(702)
        wmax = model_702.WMax.cvalue
        if not wmax:  # None or 0
            wmax = model_702.WMaxRtg.cvalue
            if wmax:
                logger.info(
                    "model 702 WMax unavailable; using WMaxRtg (%s W) as the percent "
                    "base for %s",
                    wmax,
                    control,
                )
        if not wmax:
            logger.warning(
                "%s received (%s W) but neither model 702 WMax nor WMaxRtg is "
                "available; cannot convert to a percent, ignoring",
                control,
                watts,
            )
            return None
        pct = watts / wmax * 100
        if pct > 100:
            logger.warning(
                "%s %s W exceeds device WMax %s W; clamping to 100%%",
                control,
                watts,
                wmax,
            )
            pct = 100.0
        return pct

    def _control_unsupported(self, control: str, missing: list[str]) -> None:
        """Report, once per device, a control this device cannot accept.

        Reported rather than written. The alternative -- assigning to a register
        the device never advertised -- either tells the head-end a limit is in
        force when nothing is holding it, or raises out of pysunspec2 partway
        through, and neither is something an operator can act on.
        """
        if control in self._unsupported_reported:
            return
        self._unsupported_reported.add(control)
        logger.warning(
            "%s not applied: this device does not implement %s (model 704). "
            "The control was reported rather than written, so no register was "
            "changed and no enable point was raised.",
            control,
            ", ".join(missing),
        )

    def _apply_p_lim_w_without_register(
        self, slot: str, watts: float, model_702: Any
    ) -> None:
        """Apply a watts-typed limit on a device lacking its model 702 setting.

        ``"inj"`` falls back to the indirect route: convert the watts to a
        percent of WMax and merge into 704 WMaxLimPct, which a conformant device
        does implement. ``"abs"`` has nowhere to go -- model 704 has no
        absorb-direction active-power limit -- so nothing is applied and a
        per-device warning is logged the first time an operator enables it,
        surfacing the gap rather than swallowing it.
        """
        # Nothing reaches model 702 on either branch below.
        self._p_lim_w_settings[slot] = None

        if slot == "abs":
            if not self._p_lim_abs_warned:
                logger.warning(
                    "opModMaxLimWAbsorb received (%s W) but this device declares no "
                    "absorb-direction limiting capability -- model 702 WChaRteMax is "
                    "unimplemented or its WChaRteMaxRtg rating is zero -- and model "
                    "704 carries no absorb-direction active-power limit to fall back "
                    "on; not applied",
                    watts,
                )
                self._p_lim_abs_warned = True
            return

        # WMaxLimPct is a percent of WMax (the settable max active power).
        # Some devices don't implement WMax; fall back to the nameplate rating
        # WMaxRtg (guaranteed present -- the scan-readiness check refuses to
        # initialise without it). The fallback is an approximation: it treats
        # nameplate as the effective max, which holds when there is no settable
        # WMax to be lower than it.
        wmax = model_702.WMax.cvalue
        if not wmax:  # None or 0
            wmax = model_702.WMaxRtg.cvalue
            if wmax:
                logger.info(
                    "model 702 WMax unavailable; using WMaxRtg (%s W) as the "
                    "WMaxLimPct percent base for opModMaxLimWInject",
                    wmax,
                )
        if not wmax:  # neither WMax nor WMaxRtg available -- can't form a percent
            logger.warning(
                "opModMaxLimWInject received (%s W) but neither model 702 WMax "
                "nor WMaxRtg is available; cannot convert to a WMaxLimPct percent, ignoring",
                watts,
            )
            return

        pct = watts / wmax * 100
        if pct > 100:
            logger.warning(
                "opModMaxLimWInject %s W exceeds device WMax %s W; clamping to 100%%",
                watts,
                wmax,
            )
        pct = max(0.0, min(100.0, pct))
        self._apply_inject_pct_slot("inj", True, pct)

    def _update_pf_sync(self, params: dict[str, Any]) -> None:
        model = self._get_model(711)
        self._update_enable(model.Ena, params.get("pf_mode_enable", 0), "Frequency Droop")

        if params.get("pf_mode_enable") != 1:
            return

        ctl = model.Ctl[1]
        # Fractional engineering values (e.g. KOf=0.05 = 5%/Hz droop) must
        # not be int()-cast -- pysunspec2's cvalue setter applies the scale
        # factor and rounds to the wire-type raw int itself.
        ctl.RspTms.cvalue = params.get("pf_olrt", 0)
        ctl.DbOf.cvalue = params.get("pf_dbof", 0)
        ctl.DbUf.cvalue = params.get("pf_dbuf", 0)
        ctl.KOf.cvalue = params.get("pf_kof", 0)
        ctl.KUf.cvalue = params.get("pf_kuf", 0)
        if params.get("pf_pmin") is not None:
            ctl.PMin.cvalue = params["pf_pmin"]
        ctl.write()
        self._adopt_control(model)

    def _update_const_q_sync(self, params: dict[str, Any]) -> None:
        model = self._get_model(704)

        if not params.get("const_q_mode_enable"):
            self._update_enable(model.VarSetEna, 0, "Constant Reactive Power")
            return

        # opModFixedVar.value is a percent of a reference base; the percent
        # passes through to VarSetPct unchanged and the *base* is conveyed by
        # VarSetMod. Only the var-relevant DERUnitRefTypes have a SunSpec
        # equivalent. Reject an unmapped refType rather than silently applying
        # the percent against the wrong base, so a misconfigured server is
        # surfaced instead of quietly delivering the wrong vars.
        #   IEEE DERUnitRefType: 2=%setMaxVar, 3=%statVarAvail, 8=%setMaxVA
        #   SunSpec 704 VarSetMod (model_704.json): 1=VAR_MAX_PCT,
        #     2=VAR_AVAIL_PCT, 3=VA_MAX_PCT
        ieee_to_sunspec_var_mod = {2: 1, 3: 2, 8: 3}
        ieee_ref = params.get("ref_type")
        var_set_mod = ieee_to_sunspec_var_mod.get(ieee_ref) if isinstance(ieee_ref, int) else None
        if var_set_mod is None:
            logger.warning(
                "opModFixedVar has unsupported DERUnitRefType %r (expected 2=%%setMaxVar, "
                "3=%%statVarAvail, or 8=%%setMaxVA); not applied",
                ieee_ref,
            )
            return

        missing = _unimplemented_points(model, ("VarSetPct", "VarSetMod", "VarSetPri"))
        if missing:
            self._control_unsupported("opModFixedVar", missing)
            return

        self._update_enable(model.VarSetEna, 1, "Constant Reactive Power")
        # VarSetPct is a scaled percent (model 704). Set the engineering cvalue
        # and let pysunspec2 apply the scale factor / round, rather than
        # stripping precision before the cvalue setter.
        model.VarSetPct.cvalue = params.get("const_q_pct", 0)
        model.VarSetMod.value = var_set_mod
        model.VarSetPri.value = 1  # Reactive power priority
        model.write()

    def _update_fixed_w_sync(self, params: dict[str, Any]) -> None:
        model = self._get_model(704)
        if params.get("WSetEna") == 1:
            missing = _unimplemented_points(model, ("WSetMod", "WSetPct"))
            if missing:
                self._control_unsupported("opModFixedW", missing)
                return

        self._update_enable(model.WSetEna, params.get("WSetEna", 0), "Fixed Active Power")

        if params.get("WSetEna") != 1:
            return

        # ``WSetMod`` is an enum16. Per SunSpec model 704 its symbols are
        # ``W_MAX_PCT=0`` (WSet expressed as a percent of WMax, via WSetPct)
        # and ``WATTS=1`` (absolute watts, via WSet). opModFixedW is a percent
        # of setMaxW, so the translator selects W_MAX_PCT (0) and we write the
        # percent to WSetPct. Use ``.value`` (the raw setter) for the enum,
        # matching the other enum writes in this file (DeptRef.value, etc.).
        model.WSetMod.value = params.get("WSetMod", 0)
        # ``WSet`` is already a percent of WMax (e.g. 50.0 == 50%), matching
        # the convention the translator emits for every percent control
        # (p_lim, const_q). Write it straight to WSetPct.cvalue -- the earlier
        # ``* 100`` drove a 50% setpoint to 5000% on the wire.
        model.WSetPct.cvalue = params.get("WSet", 0)
        model.write()

    def _update_target_w_sync(self, params: dict[str, Any]) -> None:
        """Apply opModTargetW -- absolute active-power setpoint in watts.

        The translator (``connectors.modes.translate_target_w``) emits
        ``{"mode_enable": 1, "watts": <scaled>}`` after applying
        ``value * 10**multiplier`` from the IEEE 2030.5 ``ActivePower``
        struct.

        Targets SunSpec model 704 in the absolute-watts mode:
          ``WSetEna``  -> enable / disable the setpoint
          ``WSetMod``  = 1 (WATTS; vs 0 = W_MAX_PCT used by ``opModFixedW``)
          ``WSet``     -> the absolute watts value

        Without this override the base no-op in ``Connector``
        (``connectors.base``) silently drops the control, so
        ``DefaultDERControl``s carrying only ``opModTargetW`` never
        touch the device. See ``_update_fixed_w_sync`` above for the
        sibling percent-mode handler.
        """
        model = self._get_model(704)
        enable = params.get("mode_enable", 0)
        watts = params.get("watts")

        # ``mode_enable=1`` with ``watts`` missing is a malformed
        # payload -- ``translate_target_w`` only builds the tuple when
        # the ``ActivePower`` struct's ``.value`` is present. Skip the
        # entire update (including the enable point) so we never land
        # in a state where ``WSetEna=1`` sits on top of a stale ``WSet``
        # from a prior setpoint -- the inverter would then treat the
        # old value as live. The log surfaces the upstream translation
        # bug for operator follow-up.
        if enable == 1 and watts is None:
            logger.warning(
                "_update_target_w_sync: mode_enable=1 but watts missing; "
                "skipping update to avoid leaking stale WSet behind an "
                "enabled WSetEna"
            )
            return

        # Which form of the setpoint this device implements decides the route,
        # and it is settled before the enable: raising WSetEna over a register
        # the device does not implement leaves an enable standing on nothing,
        # which is the state the missing-watts branch above already refuses to
        # create.
        route: str | None = None
        if enable == 1:
            if not _unimplemented_points(model, ("WSetMod", "WSet")):
                route = "watts"
            elif not _unimplemented_points(model, ("WSetMod", "WSetPct")):
                # Absolute watts expressed as a percent of WMax. Less direct,
                # but a device implementing only the percent form can still hold
                # the setpoint, and declining outright would give up a control
                # this device can actually honour.
                route = "percent"
            else:
                self._control_unsupported(
                    "opModTargetW",
                    _unimplemented_points(model, ("WSetMod", "WSet", "WSetPct")),
                )
                return

        self._update_enable(model.WSetEna, enable, "Target Active Power")

        if enable != 1:
            return

        if route == "watts":
            # WSetMod is the same enum16 as in fixed_w above. WATTS selects
            # the absolute-value branch (WSet); writing to WSetPct in this
            # mode would have no effect on the inverter setpoint.
            model.WSetMod.value = 1
            model.WSet.cvalue = watts
            model.write()
            return

        # Narrowing only: the malformed-payload branch above returns when an
        # enabled control carries no watts.
        assert watts is not None
        pct = self._watts_as_pct_of_wmax(watts, "opModTargetW")
        if pct is None:
            return
        model.WSetMod.value = 0  # W_MAX_PCT
        model.WSetPct.cvalue = pct
        model.write()

    def _update_const_pf_sync(self, params: dict[str, Any]) -> None:
        model = self._get_model(704)

        inj = params.get("inj", {})
        abs_ = params.get("abs", {})

        if inj.get("mode"):
            # Validate before _update_enable: an out-of-range displacement must
            # not leave PFWInjEna raised over a setpoint we never wrote.
            pf = _require_power_factor(inj["pf"], "opModFixedPFInjectW")
            exc = 0 if inj.get("excitation") else 1
            missing = _unimplemented_points(model, ("PFWInj.Ext", "PFWInj.PF"))
            if missing:
                self._control_unsupported("opModFixedPFInjectW", missing)
                return
            self._update_enable(model.PFWInjEna, 1, "Power factor (inject)")
            model.PFWInj.Ext.cvalue = exc
            model.PFWInj.PF.cvalue = pf
            model.write()
        elif abs_.get("mode"):
            pf = _require_power_factor(abs_["pf"], "opModFixedPFAbsorbW")
            exc = 1 if abs_.get("excitation") else 0
            missing = _unimplemented_points(model, ("PFWAbs.Ext", "PFWAbs.PF"))
            if missing:
                self._control_unsupported("opModFixedPFAbsorbW", missing)
                return
            self._update_enable(model.PFWAbsEna, 1, "Power factor (absorb)")
            model.PFWAbs.Ext.cvalue = exc
            model.PFWAbs.PF.cvalue = pf
            model.write()

    # ------------------------------------------------------------------
    # Async public interface
    # ------------------------------------------------------------------

    @property
    def endpoint_id(self) -> str:
        """Address of the device this connector talks to.

        ``host:port`` for TCP, ``rtu:<line>`` for serial. Exposed so telemetry
        can name the device it read from or wrote to without reaching into
        private state.
        """
        return self._endpoint_id

    def _get_lock(self) -> asyncio.Lock:
        """Return an asyncio.Lock bound to the *currently* running loop.

        Lazily allocate on first call and rebind if the running loop has
        changed since the last allocation -- see the construction-time
        comment for why this matters.
        """
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def fetch_monitoring(self) -> dict[str, Any]:
        async with self._get_lock():
            return await asyncio.to_thread(self._fetch_monitoring_sync)

    async def fetch_nameplate(self) -> dict[str, Any]:
        async with self._get_lock():
            return await asyncio.to_thread(self._fetch_nameplate_sync)

    async def fetch_status(self) -> dict[str, Any]:
        async with self._get_lock():
            return await asyncio.to_thread(self._fetch_status_sync)

    async def fetch_configuration(self) -> dict[str, Any]:
        async with self._get_lock():
            return await asyncio.to_thread(self._fetch_configuration_sync)

    async def update_qv(self, params: dict[str, Any]) -> ConnectorPayload:
        async with self._get_lock():
            await asyncio.to_thread(self._update_qv_sync, params)
        return None

    async def update_pv(self, params: dict[str, Any]) -> ConnectorPayload:
        async with self._get_lock():
            await asyncio.to_thread(self._update_pv_sync, params)
        return None

    async def update_qp(self, params: dict[str, Any]) -> ConnectorPayload:
        async with self._get_lock():
            await asyncio.to_thread(self._update_qp_sync, params)
        return None

    async def update_p_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        async with self._get_lock():
            await asyncio.to_thread(self._update_p_lim_sync, params)
        return None

    async def update_p_lim_inj(self, params: dict[str, Any]) -> ConnectorPayload:
        async with self._get_lock():
            await asyncio.to_thread(self._update_p_lim_w_sync, params, "inj")
        return None

    async def update_p_lim_abs(self, params: dict[str, Any]) -> ConnectorPayload:
        async with self._get_lock():
            await asyncio.to_thread(self._update_p_lim_w_sync, params, "abs")
        return None

    async def update_pf(self, params: dict[str, Any]) -> ConnectorPayload:
        async with self._get_lock():
            await asyncio.to_thread(self._update_pf_sync, params)
        return None

    async def update_const_q(self, params: dict[str, Any]) -> ConnectorPayload:
        async with self._get_lock():
            await asyncio.to_thread(self._update_const_q_sync, params)
        return None

    async def update_fixed_w(self, params: dict[str, Any]) -> ConnectorPayload:
        async with self._get_lock():
            await asyncio.to_thread(self._update_fixed_w_sync, params)
        return None

    async def update_target_w(self, params: dict[str, Any]) -> ConnectorPayload:
        async with self._get_lock():
            await asyncio.to_thread(self._update_target_w_sync, params)
        return None

    async def update_const_pf(self, params: dict[str, Any]) -> ConnectorPayload:
        async with self._get_lock():
            await asyncio.to_thread(self._update_const_pf_sync, params)
        return None
