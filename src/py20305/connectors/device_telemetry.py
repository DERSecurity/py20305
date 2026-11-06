"""Southbound device telemetry: what this client reads from and writes to a DER.

The exchange observer reports the client's IEEE 2030.5 traffic with the
server. The southbound half, the traffic between this client and the devices
it controls through connectors, is reported here, so a monitoring system sees
the commands the client issued to the equipment beside the ones it received.

A curtailment that arrives over IEEE 2030.5 and a curtailment that reaches the
inverter are different facts, and the gap between them is where a compromised
or misbehaving client shows up.

:class:`DeviceTelemetryEmitter` hands one
:class:`~py20305.observers.DeviceRecord` per read and per write to a device
observer. The reading source, the telemetry managers and the control
dispatcher share one emitter.
"""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any

from py20305.observers import (
    MAX_ERROR_CHARS,
    DeviceObserver,
    DeviceOperation,
    DeviceOutcome,
    DeviceRecord,
    ObserverGuard,
)

logger = logging.getLogger(__name__)


def mrid_hex(mrid: bytes | None) -> str | None:
    """Return an mRID as uppercase hex, the form the ``mRID`` element takes on the wire."""
    return mrid.hex().upper() if mrid is not None else None


def _bound_error(error: str | None) -> str | None:
    """Cut a device's error text to :data:`~py20305.observers.MAX_ERROR_CHARS`.

    The text originates outside this process, so its length is not the
    device's to choose.
    """
    if error is None or len(error) <= MAX_ERROR_CHARS:
        return error
    return error[:MAX_ERROR_CHARS] + f"... [truncated, {len(error)} chars]"


def device_protocol(connector: object) -> str:
    """Return what the connector speaks, as it declares it in ``telemetry_protocol``.

    A connector that declares nothing usable is reported as ``generic``.
    """
    declared = getattr(connector, "telemetry_protocol", None)
    if isinstance(declared, str) and declared:
        return declared.lower()
    return "generic"


def device_endpoint(connector: object) -> str | None:
    """Return the connector's device address, such as ``host:port``, or None.

    Read defensively: a custom connector may expose nothing at all.
    """
    endpoint_id = getattr(connector, "endpoint_id", None)
    return endpoint_id if isinstance(endpoint_id, str) and endpoint_id else None


class DeviceTelemetryEmitter:
    """Reports southbound device reads and writes to a device observer.

    One instance per client, shared by the reading source and the control
    dispatcher, the two places every southbound read and write passes through.

    Nothing here may raise into the data path: the observer's exceptions are
    caught and counted, and a record that cannot be built is counted in
    :attr:`emit_failures`.
    """

    def __init__(self, observer: DeviceObserver | None = None) -> None:
        """Create an emitter that reports to ``observer``; None reports nothing."""
        self._observer = observer
        self._guard = ObserverGuard("device observer")
        #: Records that could not be built.
        self.emit_failures = 0

    @property
    def enabled(self) -> bool:
        """Whether device reads are reported."""
        return self._observer is not None

    @property
    def writes_enabled(self) -> bool:
        """Whether device writes are reported."""
        return self._observer is not None

    @property
    def observer_failures(self) -> int:
        """How many times the device observer raised."""
        return self._guard.failures

    def attach_observer(self, observer: DeviceObserver | None) -> None:
        """Report to ``observer`` from now on, or stop with None."""
        self._observer = observer

    def record_read(
        self,
        device: str,
        values: dict[str, Any],
        *,
        connector: object = None,
        lfdi: str | None = None,
        resource: str = "monitoring",
    ) -> None:
        """Report one set of values read from a device.

        Args:
            device: Device identifier the client knows it by.
            values: The values read, as the connector returned them.
            connector: The connector the read went through.
            lfdi: The device's LFDI when one is known.
            resource: What was read: ``monitoring``, ``nameplate``,
                ``configuration``, ``status`` or ``availability``.
        """
        if not values or self._observer is None:
            # An empty record would be indistinguishable from a device
            # reporting all zero, which is a real reading.
            return
        self._emit(
            operation=DeviceOperation.READ,
            device=device,
            resource=resource,
            values=values,
            connector=connector,
            lfdi=lfdi,
        )

    def record_write(
        self,
        device: str,
        control: str,
        params: dict[str, Any],
        *,
        connector: object = None,
        lfdi: str | None = None,
        error: str | None = None,
        error_type: str | None = None,
        origin: str | None = None,
        applied_mrid: bytes | None = None,
        cause_mrid: bytes | None = None,
    ) -> None:
        """Report one control written to a device, or refused or failed.

        Args:
            device: Device identifier the client knows it by.
            control: Control name, in the same vocabulary the management API
                uses.
            params: The parameters written.
            connector: The connector the write went through.
            lfdi: The device's LFDI when one is known.
            error: The failure reason, when the write was refused or failed.
            error_type: The failure's exception class name, or ``refused``
                when the command authority refused the write.
            origin: Who issued the write, e.g. ``"ieee2030_5"``.
            applied_mrid: The mRID of the control written, when it was a
                DERControl or DefaultDERControl.
            cause_mrid: The event whose lifecycle produced the write: the event
                itself on activation, the event that ended on a fallback.
        """
        if self._observer is None:
            return
        if error is None:
            outcome = DeviceOutcome.OK
        elif error_type == "refused":
            outcome = DeviceOutcome.REFUSED
        else:
            outcome = DeviceOutcome.FAILED
        self._emit(
            operation=DeviceOperation.WRITE,
            device=device,
            resource=control,
            values=params,
            connector=connector,
            lfdi=lfdi,
            outcome=outcome,
            error=_bound_error(error),
            error_type=error_type,
            origin=str(origin) if origin is not None else None,
            applied_mrid=mrid_hex(applied_mrid),
            cause_mrid=mrid_hex(cause_mrid),
        )

    def _emit(
        self,
        *,
        operation: DeviceOperation,
        device: str,
        resource: str,
        values: dict[str, Any],
        connector: object,
        lfdi: str | None,
        **fields: Any,
    ) -> None:
        try:
            record = DeviceRecord(
                operation=operation,
                device=device,
                lfdi=lfdi,
                resource=resource,
                values=MappingProxyType(dict(values)),
                protocol=device_protocol(connector),
                endpoint=device_endpoint(connector),
                **fields,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            self.emit_failures += 1
            log = logger.warning if self.emit_failures == 1 else logger.debug
            log("Could not build a device record", exc_info=True)
            return
        self._guard.deliver(self._observer, record)
