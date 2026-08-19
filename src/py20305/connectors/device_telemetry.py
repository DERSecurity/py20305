"""Southbound device telemetry -- what this client reads from and writes to a DER.

The forwarder already carries the client's *northbound* IEEE 2030.5 exchanges to
a monitoring system. The southbound half -- the Modbus traffic between this
client and the device it controls -- was not reported at all, so a monitoring
system watching the client saw the commands it received from the utility and
never the commands it issued to the equipment.

That asymmetry matters more than it sounds. A curtailment that arrives over
2030.5 and a curtailment that reaches the inverter are different facts, and the
gap between them is where a compromised or misbehaving client shows up.
Reporting both halves on the same channel lets a consumer compare them.

The wire format is :class:`~py20305.forwarders.types.ProtocolMessage`, the same
envelope the northbound path uses, with ``protocol`` set to ``modbus``. It goes
to the same topic as the rest of the captured traffic, so a collector needs no
new subscription and no new parser: a consumer already reading these envelopes
tells the two apart by the ``protocol`` field. An operator who wants them
separated sets ``topic_suffix``.

Direction follows the data, not the initiator: a reading pulled off a device is
``upstream``, a control written to one is ``downstream``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from py20305.forwarders.base import EventFrame
from py20305.forwarders.mqtt_forwarder import PROTOCOL_MESSAGE_TOPIC_SUFFIX
from py20305.forwarders.types import (
    NetworkEndpoint,
    Protocol,
    ProtocolMessage,
    ProtocolMetadata,
    WireDirection,
)

if TYPE_CHECKING:
    from py20305.forwarders.config import DeviceTelemetryConfig
    from py20305.forwarders.manager import ForwarderManager

logger = logging.getLogger(__name__)

# Modbus TCP's registered port. Used only when a connector reports an address
# with no port of its own -- an RTU device has no TCP port at all, and inventing
# one would be worse than reporting the serial line it actually uses.
_DEFAULT_MODBUS_PORT = 502

# The unspecified address, used when a connector exposes none. This is the same
# placeholder the protocol-message path already uses for an endpoint it cannot
# resolve. It reads as "unknown" to anyone looking at the record, which a
# device's logical name in an `ip` field does not -- that looks like an address.
_UNKNOWN_IP = "0.0.0.0"


def _unknown_endpoint() -> NetworkEndpoint:
    """An endpoint standing for "not known", never for a real address.

    Used only where the envelope requires one. ``ProtocolMessage`` types
    ``source`` as a required ``NetworkEndpoint`` and ``destination`` as
    optional, so an unknown source has to be *represented* while an unknown
    destination can simply be omitted. That is why the two sides are handled
    differently for the same underlying condition: it is the shared envelope's
    shape, not a judgment about the two directions.
    """
    return NetworkEndpoint(ip=_UNKNOWN_IP, port=0)

# Upper bound on a device's error text. A Modbus exception message is normally
# short, but it originates outside this process and lands on a topic an operator
# may have scoped for telemetry rather than arbitrary device output.
MAX_ERROR_CHARS = 512


def _split_endpoint(endpoint_id: str | None) -> NetworkEndpoint | None:
    """Turn a connector's endpoint identifier into a :class:`NetworkEndpoint`.

    Connectors identify a device as ``"host:port"`` for TCP or
    ``"rtu:/dev/ttyUSB0"`` for a serial line. A serial device has no IP address,
    so it is reported by its line rather than being forced into an address shape
    it does not have.

    Args:
        endpoint_id: The connector's endpoint identifier, if it has one.

    Returns:
        The endpoint, or ``None`` when there is nothing usable to report.
    """
    if not endpoint_id:
        return None
    if endpoint_id.startswith("rtu:"):
        # No address for a serial line; the port field carries no meaning here,
        # so the line itself goes in `ip` and the port stays at the Modbus
        # default rather than being invented per-device.
        return NetworkEndpoint(ip=endpoint_id, port=_DEFAULT_MODBUS_PORT)
    host, _, port = endpoint_id.rpartition(":")
    if not host:
        return NetworkEndpoint(ip=endpoint_id, port=_DEFAULT_MODBUS_PORT)
    try:
        return NetworkEndpoint(ip=host, port=int(port))
    except ValueError:
        return NetworkEndpoint(ip=endpoint_id, port=_DEFAULT_MODBUS_PORT)


def _bound_error(error: str | None) -> str | None:
    """Cap a device's error text, noting the original length when it is cut.

    The text originates outside this process and lands on a topic an operator
    may have scoped for telemetry rather than arbitrary device output, so its
    length is not the device's to choose.
    """
    if error is None or len(error) <= MAX_ERROR_CHARS:
        return error
    return error[:MAX_ERROR_CHARS] + f"... [truncated, {len(error)} chars]"


def device_protocol(connector: object) -> Protocol:
    """What the connector speaks, for the envelope's ``protocol`` field.

    Read off the connector rather than assumed, because the same emitter sits
    behind every connector the dispatcher and the reading source drive -- a
    custom one, or the hardware-free demo. Labelling those `modbus` would put
    a false claim about the wire on the monitoring channel, and a consumer
    filtering by protocol would act on it.

    Unknown or malformed values fall back to ``GENERIC`` rather than raising:
    this runs inside the data path, and a connector's mislabelling is not
    worth failing a control write over.
    """
    declared = getattr(connector, "telemetry_protocol", None)
    if isinstance(declared, Protocol):
        return declared
    if isinstance(declared, str):
        try:
            return Protocol.from_string(declared)
        except ValueError:
            logger.debug("Connector declared unknown protocol %r; reporting generic", declared)
    return Protocol.GENERIC


def device_endpoint(connector: object) -> NetworkEndpoint | None:
    """Best-effort address of the device behind a connector.

    Read defensively: a connector is third-party-shaped code and a custom one
    may expose nothing at all. An event with no device address is still worth
    sending -- the device identifier carries the attribution -- so this returns
    ``None`` rather than raising.
    """
    endpoint_id = getattr(connector, "endpoint_id", None)
    if not isinstance(endpoint_id, str):
        return None
    return _split_endpoint(endpoint_id)


class DeviceTelemetryEmitter:
    """Publishes southbound device reads and writes to the monitoring system.

    One instance per client, shared by the reading source and the control
    dispatcher -- the two places every southbound read and write already funnels
    through, so emission lives in two places rather than in each of the
    connector's sixteen operations.

    Nothing here may raise into the data path. Telemetry that can break a
    control write is worse than absent telemetry, so every entry point swallows
    its own errors and logs them.
    """

    def __init__(
        self,
        forwarder: ForwarderManager | None,
        config: DeviceTelemetryConfig,
        *,
        client_id: str | None = None,
    ) -> None:
        """Create an emitter.

        Args:
            forwarder: Where events go. ``None`` disables emission.
            config: Whether telemetry is on, and its topic.
            client_id: Identifier recorded as the forwarding system.
        """
        self._forwarder = forwarder
        self._config = config
        self._forwarder_id = client_id or ""
        #: The client's own advertised host, used as the endpoint on
        #: whichever side of the exchange it sits. Empty until configured.
        self._source_host = ""
        #: Emission failures since start. Telemetry that stopped working must
        #: not look the same as a client with nothing to report.
        self.emit_failures = 0

    @property
    def enabled(self) -> bool:
        """Whether events will actually be emitted."""
        return self._config.enabled and self._forwarder is not None

    def attach_forwarder(self, forwarder: ForwarderManager | None) -> None:
        """Point the emitter at the transport once one exists.

        The forwarder is built from configuration after the client's components
        are constructed, so an emitter that captured it at ``__init__`` would
        hold ``None`` for the process's lifetime and emit nothing.
        """
        self._forwarder = forwarder

    def configure(
        self,
        config: DeviceTelemetryConfig,
        *,
        client_id: str | None = None,
        source_host: str | None = None,
    ) -> None:
        """Apply operator configuration after construction.

        Args:
            config: Whether telemetry is on, and its topic.
            client_id: Identifier recorded as the forwarding system.
            source_host: The client's own advertised host, reported as its
                endpoint on whichever side of the exchange it sits.
        """
        self._config = config
        if client_id is not None:
            self._forwarder_id = client_id
        if source_host is not None:
            self._source_host = source_host

    def record_read(
        self,
        device: str,
        values: dict[str, Any],
        *,
        connector: object = None,
        lfdi: str | None = None,
    ) -> None:
        """Report one set of readings pulled off a device.

        Args:
            device: Device identifier the client knows it by.
            values: The points read. Sent verbatim so the collector sees what
                the device actually reported, not a re-derived summary.
            connector: The connector the read went through, for its address.
            lfdi: The device's LFDI when one is known.
        """
        if not values:
            # Nothing was read. An empty envelope would be indistinguishable
            # from a device reporting all-zero, which is a real reading.
            return
        self._emit(
            device=device,
            body={"points": dict(values), "operation": "read"},
            direction=WireDirection.UPSTREAM,
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
    ) -> None:
        """Report one control written to a device.

        A rejected write is reported too, with its error. A command that was
        attempted and failed is exactly what an audit trail should retain -- the
        northbound side may still believe it succeeded.

        Args:
            device: Device identifier the client knows it by.
            control: Control name, e.g. ``"p_lim"``, in the same vocabulary the
                management API uses.
            params: The parameters written.
            connector: The connector the write went through, for its address.
            lfdi: The device's LFDI when one is known.
            error: The failure reason, when the write was rejected.
        """
        body: dict[str, Any] = {
            "control": control,
            "params": dict(params),
            "operation": "write",
        }
        # Bounded once, then used everywhere the text appears. The envelope
        # carries it in two places, and capping only one of them leaves the
        # device able to put an arbitrarily long string on the wire through the
        # other -- which is the whole thing the cap exists to prevent.
        bounded = _bound_error(error)
        if bounded is not None:
            body["error"] = bounded
        self._emit(
            device=device,
            body=body,
            direction=WireDirection.DOWNSTREAM,
            connector=connector,
            lfdi=lfdi,
            is_valid=error is None,
            validation_error=bounded,
        )

    def _client_side(self) -> NetworkEndpoint:
        """This client's end of a southbound exchange.

        Reported by its advertised host once one is configured. Before that,
        the unspecified address -- what the 2030.5 side of this transport
        already emits when an endpoint is unknown, so a collector reading both
        halves sees one convention rather than two.
        """
        return NetworkEndpoint(ip=self._source_host or _UNKNOWN_IP, port=0)

    def _emit(
        self,
        *,
        device: str,
        body: dict[str, Any],
        direction: WireDirection,
        connector: object,
        lfdi: str | None,
        is_valid: bool = True,
        validation_error: str | None = None,
    ) -> None:
        """Build the envelope and hand it to the transport.

        Source is where the exchange came from and destination where it went,
        which is the contract the 2030.5 side of this transport already
        follows. A reading is pulled off the device, so the device is the
        source; a control is written to it, so the device is the destination.
        Putting the device in ``source`` for both would tell a collector that
        the inverter issued the command.
        """
        if not self.enabled or self._forwarder is None:
            return
        try:
            device_ep = device_endpoint(connector)

            if direction is WireDirection.UPSTREAM:
                # The device initiated nothing, but the data came from it. The
                # envelope requires a source, so an unknown one is represented
                # by the unspecified address rather than by the device's
                # logical name in an `ip` field -- that looks like an address.
                source = device_ep or _unknown_endpoint()
                destination: NetworkEndpoint | None = self._client_side()
            else:
                source = self._client_side()
                # Omitted rather than invented when the connector has no
                # address; the device identifier carries the attribution.
                destination = device_ep
            message = ProtocolMessage(
                protocol=device_protocol(connector),
                direction=direction,
                client_id=lfdi or device,
                payload=body,
                source=source,
                destination=destination,
                forwarder_id=self._forwarder_id,
                protocol_data=ProtocolMetadata(
                    lfdi=lfdi,
                    message_type=body.get("operation"),
                    extra={"device": device},
                ),
                is_valid=is_valid,
                validation_error=validation_error,
            )
            self._forwarder.queue_event(
                EventFrame(
                    payload=message.to_dict(),
                    topic_suffix=self._config.topic_suffix or PROTOCOL_MESSAGE_TOPIC_SUFFIX,
                    kind="device-telemetry",
                )
            )
        except Exception:
            self.emit_failures += 1
            if self.emit_failures == 1:
                logger.warning(
                    "Device telemetry failed to record an exchange; further failures log at debug",
                    exc_info=True,
                )
            else:
                logger.debug("Device telemetry failed to record an exchange", exc_info=True)
