"""The message format this client publishes captured traffic in.

:class:`ProtocolMessage` wraps one IEEE 2030.5 exchange -- request or
response, with its endpoints, timing and payload -- in a protocol-agnostic
envelope. The forwarder publishes these; a monitoring system subscribes to
them. Nothing in the client's own operation depends on this module, so a
deployment that does not forward anything never touches it.

The envelope is deliberately more general than 2030.5 needs, because it is
consumed by systems that also ingest other protocols and want one shape for
all of them. That is why :class:`ProtocolMetadata` carries a free-form
``extra`` dict and why every optional field is omitted from the serialized
form rather than emitted as null: consumers match on presence, and adding a
field is a compatible change.

Serialization contract, as emitted by :meth:`ProtocolMessage.to_dict`:

- ``version``, ``protocol``, ``direction``, ``timestamp``, ``client_id``,
  ``forwarder_id``, ``payload``, ``source``, ``hash`` and ``is_valid`` are
  always present.
- ``destination``, ``protocol_data`` and ``validation_error`` appear only
  when set.
- ``timestamp`` is ISO 8601 with an offset. A trailing ``Z`` is rewritten to
  ``+00:00`` on construction.
- ``hash`` is a deterministic UUIDv3 over protocol, client id, timestamp and
  payload, so a consumer can deduplicate replays without coordinating with
  the producer.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from py20305.version_info import get_package_version


#: Value stamped into every envelope's ``version`` field.
#:
#: Consumers constrain this to a bare semantic version, or the literal "2.0"
#: from the original wire format -- it is a validated field, not free text, so
#: a descriptive producer string here makes every message fail validation at
#: the consumer. Provenance belongs in ``forwarder_id``, which is free-form
#: and exists for exactly that.
def _wire_version() -> str:
    """The value for the envelope's ``version`` field.

    Consumers constrain this to a bare semantic version or the literal "2.0",
    so the installed version cannot be passed through unchecked:
    :func:`~py20305.version_info.get_package_version` returns
    ``"unknown"` when running from a source tree, and a development build
    carries a PEP 440 suffix such as ``0.1.0.dev1``. Both are rejected.

    The release segment is taken when there is one, and "2.0" -- the original
    wire-format value, which the constraint still accepts -- is the fallback
    when there is not. Emitting something invalid would be worse: the field is
    required, so the whole message is rejected.
    """
    release = _RELEASE_RE.match(get_package_version())
    return release.group(0) if release else "2.0"


#: The leading ``X.Y.Z`` of a version string, ignoring any PEP 440 suffix.
_RELEASE_RE = re.compile(r"\d+\.\d+\.\d+")

_VERSION = _wire_version()

#: Six hex pairs, colon- or hyphen-separated, as the format defines a MAC.
_MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


class WireDirection(StrEnum):
    """Which way the message was travelling, as carried on the wire.

    ``UPSTREAM`` is device toward server; ``DOWNSTREAM`` is server toward
    device.

    Deliberately not named ``MessageDirection``:
    :mod:`py20305.forwarders.base` defines a capture-side enum by
    that name with the same members, and two same-named enums compare unequal
    across modules without any error to say so. Distinct names make the
    conversion at the boundary explicit.
    """

    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"


class Protocol(StrEnum):
    """The application protocol a message was captured from."""

    IEEE_2030_5 = "2030.5"
    #: Southbound device traffic. A consumer reading this channel tells the
    #: two apart by this field, which is why device telemetry rides the same
    #: envelope rather than defining a second one.
    MODBUS = "modbus"
    #: A southbound exchange over something this vocabulary does not name --
    #: a custom or in-process connector. Recording it as `modbus` would be a
    #: claim about the wire that is simply untrue, and a consumer filtering on
    #: protocol would then act on a device that speaks something else.
    OTHER = "other"

    @classmethod
    def from_string(cls, value: str) -> Protocol:
        """Parse a protocol from its wire value."""
        normalized = value.lower().strip()
        for member in cls:
            if member.value.lower() == normalized:
                return member
        raise ValueError(f"Unknown protocol: {value}")


@dataclass
class PayloadEnvelope:
    """A payload together with what it is, so a consumer need not guess.

    2030.5 bodies are XML, but a forwarded message may also carry JSON or an
    opaque blob, and the consumer has to tell them apart without sniffing.
    Construct through the ``from_*`` factories rather than directly.
    """

    #: Common content types, as class constants so callers don't retype them.
    JSON = "application/json"
    XML = "application/xml"
    TEXT = "text/plain"
    BINARY = "application/octet-stream"

    content_type: str
    data: str
    encoding: str = "utf-8"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PayloadEnvelope:
        """Wrap a Python dict as a JSON payload."""
        return cls(content_type=cls.JSON, data=json.dumps(data), encoding="utf-8")

    @classmethod
    def from_xml(cls, xml_string: str) -> PayloadEnvelope:
        """Wrap an XML string. The usual case for 2030.5."""
        return cls(content_type=cls.XML, data=xml_string, encoding="utf-8")

    @classmethod
    def from_binary(cls, binary_data: bytes) -> PayloadEnvelope:
        """Wrap raw bytes, base64-encoded so the envelope stays JSON-safe."""
        return cls(
            content_type=cls.BINARY,
            data=base64.b64encode(binary_data).decode("ascii"),
            encoding="base64",
        )

    @classmethod
    def from_text(cls, text: str, content_type: str = "text/plain") -> PayloadEnvelope:
        """Wrap plain text, or text of a caller-specified content type."""
        return cls(content_type=content_type, data=text, encoding="utf-8")

    @classmethod
    def infer_from_string(cls, data: str) -> PayloadEnvelope:
        """Guess the content type of a string payload.

        Tries JSON, then XML, then falls back to plain text. Only for callers
        holding a string with no type information; prefer a ``from_*``
        factory whenever the type is known.
        """
        stripped = data.strip()

        if stripped.startswith(("{", "[")):
            try:
                json.loads(stripped)
                return cls(content_type=cls.JSON, data=data, encoding="utf-8")
            except json.JSONDecodeError:
                pass

        if stripped.startswith("<?xml") or (stripped.startswith("<") and ">" in stripped):
            return cls(content_type=cls.XML, data=data, encoding="utf-8")

        return cls(content_type=cls.TEXT, data=data, encoding="utf-8")

    def to_dict(self) -> dict[str, Any] | None:
        """Parse a JSON payload back to a dict, or ``None`` if it isn't one."""
        if self.content_type == self.JSON:
            result = json.loads(self.data)
            if isinstance(result, dict):
                return result
        return None

    def to_bytes(self) -> bytes:
        """Return the payload as bytes, decoding base64 when that's the encoding."""
        if self.encoding == "base64":
            return base64.b64decode(self.data)
        return self.data.encode(self.encoding)

    def to_string(self) -> str:
        """Return the payload's raw string form."""
        return self.data

    @property
    def is_json(self) -> bool:
        """Whether this payload is JSON."""
        return self.content_type == self.JSON

    @property
    def is_xml(self) -> bool:
        """Whether this payload is XML."""
        return self.content_type == self.XML

    @property
    def is_binary(self) -> bool:
        """Whether this payload is an opaque blob."""
        return self.content_type == self.BINARY

    @property
    def is_text(self) -> bool:
        """Whether this payload is plain text."""
        return self.content_type == self.TEXT

    def serialize(self) -> dict[str, str]:
        """Serialize for transport."""
        return {
            "content_type": self.content_type,
            "data": self.data,
            "encoding": self.encoding,
        }

    @classmethod
    def deserialize(cls, envelope_dict: dict[str, str]) -> PayloadEnvelope:
        """Rebuild from a :meth:`serialize` result."""
        return cls(
            content_type=envelope_dict["content_type"],
            data=envelope_dict["data"],
            encoding=envelope_dict.get("encoding", "utf-8"),
        )


@dataclass
class NetworkEndpoint:
    """One end of the connection a message was seen on.

    ``mac`` is carried because the format defines it and a capture-side
    producer populates it from the L2 frame. This client sees TCP sessions
    rather than frames, so it leaves it unset -- but a message read back from
    the wire has to preserve it rather than silently drop it.
    """

    ip: str
    port: int
    mac: str | None = None

    def __post_init__(self) -> None:
        """Normalize the loosely-typed fields.

        A port may arrive as a string, since configuration often supplies one.
        A MAC is normalized to lowercase colon-separated form, accepting either
        separator, because the format defines one spelling and a consumer
        comparing addresses should not have to guess which it received.

        Raises:
            ValueError: If ``mac`` is set and is not six hex pairs.
        """
        if isinstance(self.port, str):
            self.port = int(self.port)
        if self.mac is not None:
            if not isinstance(self.mac, str) or not _MAC_RE.match(self.mac):
                raise ValueError(
                    f"mac must be six hex pairs separated by ':' or '-', got {self.mac!r}"
                )
            self.mac = self.mac.lower().replace("-", ":")

    def to_dict(self) -> dict[str, Any]:
        """Serialize for transport, omitting ``mac`` when unset."""
        result: dict[str, Any] = {"ip": self.ip, "port": self.port}
        if self.mac is not None:
            result["mac"] = self.mac
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NetworkEndpoint:
        """Rebuild from a :meth:`to_dict` result."""
        return cls(ip=data["ip"], port=data["port"], mac=data.get("mac"))


@dataclass
class ProtocolMetadata:
    """Protocol-level detail about a message, beyond its bytes.

    Every field is optional and omitted from the serialized form when unset,
    so a consumer reads what the producer knew rather than a wall of nulls.
    ``extra`` carries anything this class does not name.
    """

    lfdi: str | None = None
    message_type: str | None = None
    http_method: str | None = None
    uri: str | None = None

    #: The format's own free-form bag, round-tripped nested under ``extra``.
    extra: dict[str, Any] = field(default_factory=dict)

    #: Top-level keys this class does not model, kept where they were found.
    #:
    #: The format defines many fields for protocols this client does not
    #: speak, and modelling them here would be dead weight. But a message read
    #: from the wire and written back out has to keep its shape: folding those
    #: keys into ``extra`` would move them a level deeper and quietly rewrite
    #: another producer's message.
    passthrough: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize, omitting unset fields and empty containers."""
        result: dict[str, Any] = {}
        for name in self.__dataclass_fields__:
            if name in {"extra", "passthrough"}:
                continue
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        result.update(self.passthrough)
        if self.extra:
            result["extra"] = self.extra
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProtocolMetadata:
        """Rebuild from a :meth:`to_dict` result.

        Raises:
            TypeError: If ``extra`` is present and is not a mapping. Silently
                discarding it would lose data that the sender considered part
                of the message.
        """
        known = {f for f in cls.__dataclass_fields__ if f not in {"extra", "passthrough"}}
        raw_extra = data.get("extra")
        if raw_extra is not None and not isinstance(raw_extra, dict):
            raise TypeError(
                f"protocol_data 'extra' must be a mapping, got {type(raw_extra).__name__}"
            )

        kwargs: dict[str, Any] = {}
        passthrough: dict[str, Any] = {}
        for key, value in data.items():
            if key == "extra":
                continue
            if key in known:
                kwargs[key] = value
            else:
                passthrough[key] = value
        return cls(**kwargs, extra=dict(raw_extra or {}), passthrough=passthrough)


@dataclass
class ProtocolMessage:
    """One captured protocol exchange, ready to publish.

    Construct it with whatever you have -- ``payload`` accepts a
    :class:`PayloadEnvelope`, a dict, a list or a string, and is normalized
    to an envelope on construction -- then call :meth:`to_dict` or
    :meth:`to_json`.
    """

    protocol: Protocol
    direction: WireDirection
    client_id: str
    payload: PayloadEnvelope | dict[str, Any] | list[Any] | str

    source: NetworkEndpoint
    destination: NetworkEndpoint | None = None

    #: Identifies the system that produced or relayed this message, so a
    #: consumer aggregating several sites can tell them apart.
    forwarder_id: str = ""

    protocol_data: ProtocolMetadata = field(default_factory=ProtocolMetadata)

    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    hash: str = ""

    is_valid: bool = True
    validation_error: str | None = None

    def __post_init__(self) -> None:
        """Normalize the loosely-typed fields and compute the hash.

        Raises:
            ValueError: If ``client_id`` is empty or ``timestamp`` is not
                ISO 8601. Both are caught here rather than at the consumer,
                where the message would be undiagnosable.
        """
        if isinstance(self.protocol, str):
            self.protocol = Protocol.from_string(self.protocol)

        if isinstance(self.direction, str):
            self.direction = WireDirection(self.direction)

        if isinstance(self.payload, dict):
            self.payload = PayloadEnvelope.from_dict(self.payload)
        elif isinstance(self.payload, list):
            self.payload = PayloadEnvelope(
                content_type=PayloadEnvelope.JSON,
                data=json.dumps(self.payload),
                encoding="utf-8",
            )
        elif isinstance(self.payload, str):
            self.payload = PayloadEnvelope.infer_from_string(self.payload)

        if self.timestamp.endswith("Z"):
            self.timestamp = self.timestamp[:-1] + "+00:00"

        try:
            datetime.fromisoformat(self.timestamp)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "timestamp must be ISO 8601 format "
                f"(e.g. '2024-01-15T12:00:00+00:00'): {exc}"
            ) from exc

        if not self.client_id or not self.client_id.strip():
            raise ValueError("client_id must be a non-empty string")

        if self.forwarder_id:
            self.forwarder_id = self.forwarder_id.strip()

        if not self.hash:
            self.hash = self._generate_hash()

    def _generate_hash(self) -> str:
        """Derive a deduplication key from the message's identifying fields."""
        payload_str = (
            self.payload.data
            if isinstance(self.payload, PayloadEnvelope)
            else str(self.payload)
        )
        unique = f"{self.protocol.value}:{self.client_id}:{self.timestamp}:{payload_str}"
        return str(uuid.uuid3(uuid.NAMESPACE_DNS, unique))

    def to_dict(self) -> dict[str, Any]:
        """Serialize for transport. See the module docstring for the contract."""
        payload_data: dict[str, str] | dict[str, Any] | list[Any] | str
        if isinstance(self.payload, PayloadEnvelope):
            payload_data = self.payload.serialize()
        else:  # pragma: no cover -- __post_init__ normalizes every other shape
            payload_data = self.payload

        result: dict[str, Any] = {
            "version": _VERSION,
            "protocol": self.protocol.value,
            "direction": self.direction.value,
            "timestamp": self.timestamp,
            "client_id": self.client_id,
            "forwarder_id": self.forwarder_id,
            "payload": payload_data,
            "source": self.source.to_dict(),
            "hash": self.hash,
            "is_valid": self.is_valid,
        }

        if self.destination:
            result["destination"] = self.destination.to_dict()

        protocol_data = self.protocol_data.to_dict()
        if protocol_data:
            result["protocol_data"] = protocol_data

        if self.validation_error:
            result["validation_error"] = self.validation_error

        return result

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProtocolMessage:
        """Rebuild a message from a :meth:`to_dict` result.

        The inverse of the serialization contract, so a consumer can read
        what a producer wrote without hand-unpacking the envelope. Unknown
        keys under ``protocol_data`` survive the round trip via
        :attr:`ProtocolMetadata.extra`, which is what lets a consumer built
        against this version read a message from a later one.

        Metadata keys this class does not model are preserved in
        :attr:`ProtocolMetadata.passthrough` and re-emitted where they were
        found, so a message from a producer modelling more fields keeps its
        shape rather than having them folded a level deeper.

        ``hash`` is taken from the input rather than recomputed. It is the
        producer's deduplication key, and recomputing it here would silently
        change it whenever a field this class does not model was dropped.

        Raises:
            KeyError: If a key the contract says is always present is missing.
        """
        payload = data["payload"]
        if isinstance(payload, dict) and {"content_type", "data"} <= payload.keys():
            payload = PayloadEnvelope.deserialize(payload)

        destination = data.get("destination")
        protocol_data = data.get("protocol_data")

        return cls(
            protocol=Protocol.from_string(data["protocol"]),
            direction=WireDirection(data["direction"]),
            client_id=data["client_id"],
            payload=payload,
            source=NetworkEndpoint.from_dict(data["source"]),
            destination=NetworkEndpoint.from_dict(destination) if destination else None,
            forwarder_id=data.get("forwarder_id", ""),
            protocol_data=(
                ProtocolMetadata.from_dict(protocol_data)
                if protocol_data
                else ProtocolMetadata()
            ),
            timestamp=data["timestamp"],
            hash=data.get("hash", ""),
            is_valid=data.get("is_valid", True),
            validation_error=data.get("validation_error"),
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> ProtocolMessage:
        """Rebuild a message from a :meth:`to_json` result."""
        return cls.from_dict(json.loads(raw))


__all__ = [
    "NetworkEndpoint",
    "PayloadEnvelope",
    "Protocol",
    "ProtocolMessage",
    "ProtocolMetadata",
    "WireDirection",
]
