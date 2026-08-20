"""A minimal OCSF Network Activity (class 4001) event, for connection telemetry.

The Open Cybersecurity Schema Framework is a public, vendor-neutral schema
(https://schema.ocsf.io/). This module carries just enough of it to emit one
class -- Network Activity, ``class_uid`` 4001 -- which is how this client
reports its own connection outcomes to a security-monitoring platform:
``src_endpoint`` / ``dst_endpoint`` / ``status_id`` / ``status_detail`` map
onto what a connection log needs, and a consumer can ingest an established
schema without a translation layer.

Vendored rather than depended on: the emitting side needs four factories and a
serializer, not a schema toolkit, and a library client should not carry a
platform dependency to publish a public format.

Two rules here come from the compliance argument behind connection logging
rather than from the schema, and are enforced rather than documented:

- A failure event must carry ``status_detail``. The reason is the entire value
  of an error record; a failure without one satisfies nobody.
- Failures are never aggregated. Successful connections may be coalesced into
  a window carrying a count -- a polling client would otherwise emit more
  connection events than the passive capture beside it -- but collapsing
  distinct failure reasons into a count destroys the record's only value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any, ClassVar

# The released OCSF version the events in this module conform to.
OCSF_SCHEMA_VERSION = "1.9.0"

# Recorded in ``metadata.product.vendor_name``: who publishes this client.
DEFAULT_VENDOR_NAME = "DER Security"

# What a producer may hand a factory as a time. Events store epoch
# milliseconds; this is the input side, normalized by ``to_epoch_ms``.
EventTime = datetime | int | float | str


class SeverityId(IntEnum):
    """OCSF ``severity_id`` -- how consequential the event is."""

    UNKNOWN = 0
    INFORMATIONAL = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    CRITICAL = 5
    FATAL = 6
    OTHER = 99


class StatusId(IntEnum):
    """OCSF ``status_id`` -- the outcome of the activity the event describes."""

    UNKNOWN = 0
    SUCCESS = 1
    FAILURE = 2
    OTHER = 99


class NetworkActivityId(IntEnum):
    """OCSF 4001 ``activity_id``."""

    UNKNOWN = 0
    OPEN = 1
    CLOSE = 2
    RESET = 3
    FAIL = 4
    REFUSE = 5
    TRAFFIC = 6
    LISTEN = 7
    OTHER = 99


class ConnectionDirectionId(IntEnum):
    """OCSF ``connection_info.direction_id`` -- which way the connection runs."""

    UNKNOWN = 0
    INBOUND = 1
    OUTBOUND = 2
    LATERAL = 3
    LOCAL = 4
    OTHER = 99


# Captions OCSF defines for each enum member. OCSF's rule is that the sibling
# string attribute (``severity``, ``status``, ``activity_name``, ``direction``)
# carries the caption of the ``_id``, so consumers reading either see the same
# word.
SEVERITY_CAPTIONS: dict[int, str] = {
    SeverityId.UNKNOWN: "Unknown",
    SeverityId.INFORMATIONAL: "Informational",
    SeverityId.LOW: "Low",
    SeverityId.MEDIUM: "Medium",
    SeverityId.HIGH: "High",
    SeverityId.CRITICAL: "Critical",
    SeverityId.FATAL: "Fatal",
    SeverityId.OTHER: "Other",
}

STATUS_CAPTIONS: dict[int, str] = {
    StatusId.UNKNOWN: "Unknown",
    StatusId.SUCCESS: "Success",
    StatusId.FAILURE: "Failure",
    StatusId.OTHER: "Other",
}

ACTIVITY_CAPTIONS: dict[int, str] = {
    NetworkActivityId.UNKNOWN: "Unknown",
    NetworkActivityId.OPEN: "Open",
    NetworkActivityId.CLOSE: "Close",
    NetworkActivityId.RESET: "Reset",
    NetworkActivityId.FAIL: "Fail",
    NetworkActivityId.REFUSE: "Refuse",
    NetworkActivityId.TRAFFIC: "Traffic",
    NetworkActivityId.LISTEN: "Listen",
    NetworkActivityId.OTHER: "Other",
}

DIRECTION_CAPTIONS: dict[int, str] = {
    ConnectionDirectionId.UNKNOWN: "Unknown",
    ConnectionDirectionId.INBOUND: "Inbound",
    ConnectionDirectionId.OUTBOUND: "Outbound",
    ConnectionDirectionId.LATERAL: "Lateral",
    ConnectionDirectionId.LOCAL: "Local",
    ConnectionDirectionId.OTHER: "Other",
}

# The transport-level failure activities: the connection itself is what went
# wrong, whether it never opened (FAIL, REFUSE) or was torn down after opening
# (RESET). Each is reported with status Failure and must carry a reason.
_FAILURE_ACTIVITIES = frozenset(
    {
        NetworkActivityId.RESET,
        NetworkActivityId.FAIL,
        NetworkActivityId.REFUSE,
    }
)


def to_epoch_ms(value: EventTime) -> int:
    """Normalize a time value to an OCSF ``timestamp_t``.

    OCSF timestamps are milliseconds since the Unix epoch. This accepts a
    ``datetime`` (naive values are read as UTC), an ISO 8601 string, or a
    number already in epoch milliseconds.

    Raises:
        TypeError: If ``value`` is not a datetime, number, or string.
        ValueError: If a string is not parseable as ISO 8601.
    """
    if isinstance(value, bool):
        raise TypeError("timestamp must be a datetime, number, or ISO 8601 string, got bool")
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return int(moment.timestamp() * 1000)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)
    raise TypeError(
        f"timestamp must be a datetime, number, or ISO 8601 string, got {type(value).__name__}"
    )


def now_epoch_ms() -> int:
    """Current time as an OCSF ``timestamp_t``."""
    return int(datetime.now(UTC).timestamp() * 1000)


@dataclass(kw_only=True)
class Endpoint:
    """An OCSF ``network_endpoint`` -- one end of a connection.

    ``ip`` and ``hostname`` are distinct OCSF attributes with typed meanings:
    ``ip`` must hold an IP address, and a DNS name goes in ``hostname``. A
    configured server URL usually names a host, while an established socket
    reports addresses, so either alone identifies an endpoint -- but at least
    one must be present, or the endpoint identifies nothing.

    ``port`` may be ``None``: an application-layer client frequently cannot
    determine the ephemeral local port its socket was assigned, and an unknown
    port is reported as unknown rather than invented. (This is why the OCSF
    path does not reuse :class:`~py20305.forwarders.types.NetworkEndpoint`,
    whose wire contract requires a port.)
    """

    ip: str | None = None
    hostname: str | None = None
    port: int | None = None

    def __post_init__(self) -> None:
        """Reject an endpoint that names nothing."""
        if self.ip is None and self.hostname is None:
            raise ValueError("an endpoint needs an ip or a hostname")

    def to_dict(self) -> dict[str, Any]:
        """Serialize; unset attributes are omitted."""
        result: dict[str, Any] = {}
        if self.ip is not None:
            result["ip"] = self.ip
        if self.hostname is not None:
            result["hostname"] = self.hostname
        if self.port is not None:
            result["port"] = self.port
        return result


@dataclass(kw_only=True)
class Product:
    """OCSF ``product`` object -- the software that produced the event."""

    name: str
    vendor_name: str = DEFAULT_VENDOR_NAME
    version: str | None = None

    def __post_init__(self) -> None:
        """Reject an empty product name -- it is what identifies the emitter."""
        if not self.name or not self.name.strip():
            raise ValueError("product name must be a non-empty string")
        self.name = self.name.strip()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to an OCSF ``product`` object; unset fields are omitted."""
        result: dict[str, Any] = {"name": self.name, "vendor_name": self.vendor_name}
        if self.version is not None:
            result["version"] = self.version
        return result


@dataclass(kw_only=True)
class Metadata:
    """OCSF ``metadata`` object -- required on every OCSF event."""

    product: Product
    version: str = OCSF_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialize to an OCSF ``metadata`` object."""
        return {"product": self.product.to_dict(), "version": self.version}


@dataclass(kw_only=True)
class NetworkActivity:
    """An OCSF Network Activity (4001) event describing one connection outcome.

    Prefer the factories -- :meth:`for_connection_success`,
    :meth:`for_connection_failure`, :meth:`for_exchange_failure`, and
    :meth:`for_coalesced_successes` -- over the constructor. They set the
    ``activity_id`` / ``status_id`` pair together, which is the part of OCSF
    conformance no validator can check: emitting ``Open`` / ``Success`` for a
    connection that actually failed is structurally valid and semantically
    wrong.

    The two failure factories split on which layer failed, because that is
    what a reader needs to know first. :meth:`for_connection_failure` covers
    transport-level failures -- the connection never opened (``Fail``,
    ``Refuse``) or was torn down after it did (``Reset``).
    :meth:`for_exchange_failure` covers a connection that stayed up and whose
    exchange over it failed, so the activity remains ``Open`` and only the
    status is ``Failure``.

    Attributes:
        activity_id: What happened to the connection.
        metadata: The producing software and the OCSF version emitted.
        time: Event time in epoch milliseconds. For an aggregate this is
            ``start_time``, per OCSF, so aggregates and discrete events order
            consistently on one timeline.
        severity_id: OCSF severity.
        status_id: Outcome of the activity.
        status_detail: Why it happened. Required on every failure.
        status_code: Optional producer-specific code, e.g. an HTTP status.
        message: Optional human-readable description.
        count: Number of connections aggregated into this record. Unset for
            discrete events; ``> 1`` requires the aggregation window.
        start_time: First event in the aggregation window, epoch ms.
        end_time: Last event in the aggregation window, epoch ms.
        duration: Window length in ms. Derived from the bounds when not
            supplied; an explicit value that disagrees with them is rejected.
        src_endpoint: The initiator of the connection.
        dst_endpoint: The responder. At least one endpoint must be present,
            per the OCSF constraint on the network category.
        service: Service label, e.g. ``"ieee2030.5"``. Serialized as
            ``dst_endpoint.svc_name``, where OCSF carries the responder's
            service identity.
        connection_direction: Direction of the connection. Defaults to
            outbound, the case a client-side emitter reports.
        protocol_name: Transport protocol name, e.g. ``"tcp"``.
        url: Optional URL the connection targeted; on a failure it is what
            identifies which interface was unreachable.
    """

    CLASS_UID: ClassVar[int] = 4001
    CATEGORY_UID: ClassVar[int] = 4
    CLASS_NAME: ClassVar[str] = "Network Activity"
    CATEGORY_NAME: ClassVar[str] = "Network Activity"

    activity_id: NetworkActivityId
    metadata: Metadata
    time: int = field(default_factory=now_epoch_ms)
    severity_id: SeverityId = SeverityId.INFORMATIONAL
    status_id: StatusId = StatusId.UNKNOWN
    status_detail: str | None = None
    status_code: str | None = None
    message: str | None = None
    count: int | None = None
    start_time: int | None = None
    end_time: int | None = None
    duration: int | None = None
    src_endpoint: Endpoint | None = None
    dst_endpoint: Endpoint | None = None
    service: str | None = None
    connection_direction: ConnectionDirectionId = ConnectionDirectionId.OUTBOUND
    protocol_name: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        """Validate the OCSF constraints and the two compliance rules."""
        self.activity_id = NetworkActivityId(self.activity_id)
        self.severity_id = SeverityId(self.severity_id)
        self.status_id = StatusId(self.status_id)
        self.connection_direction = ConnectionDirectionId(self.connection_direction)
        self.time = to_epoch_ms(self.time)
        if self.start_time is not None:
            self.start_time = to_epoch_ms(self.start_time)
        if self.end_time is not None:
            self.end_time = to_epoch_ms(self.end_time)

        if self.count is not None and self.count < 1:
            raise ValueError(f"count must be at least 1 when set, got {self.count}")

        is_aggregate = self.count is not None and self.count > 1

        # OCSF scopes the window attributes to aggregates in both directions:
        # an aggregate must say which interval it covers, and a discrete event
        # uses `time` alone.
        if is_aggregate and (self.start_time is None or self.end_time is None):
            raise ValueError(
                "an aggregated event (count > 1) must carry both start_time and end_time"
            )
        if not is_aggregate and (self.start_time is not None or self.end_time is not None):
            raise ValueError(
                "start_time and end_time describe an aggregation window; "
                "a discrete event uses time alone"
            )

        if self.start_time is not None and self.end_time is not None:
            if self.end_time < self.start_time:
                raise ValueError(
                    f"end_time ({self.end_time}) must not precede start_time ({self.start_time})"
                )
            # OCSF defines duration as the window's length, so an explicit
            # value that disagrees with the bounds is a producer bug rather
            # than a more precise measurement to preserve.
            window = self.end_time - self.start_time
            if self.duration is None:
                self.duration = window
            elif self.duration != window:
                raise ValueError(
                    f"duration ({self.duration}) must equal end_time - start_time ({window})"
                )
        elif self.duration is not None:
            raise ValueError(
                "duration is the aggregation window's length; set start_time and end_time with it"
            )

        # OCSF constrains the network category to at least one endpoint. An
        # event with neither says nothing about a connection.
        if self.src_endpoint is None and self.dst_endpoint is None:
            raise ValueError("at least one of src_endpoint or dst_endpoint must be set")

        # The service label serializes onto the responder, so without one it
        # would be dropped on the way to the wire. Silently losing a documented
        # attribute is worse than refusing the combination.
        if self.service is not None and self.dst_endpoint is None:
            raise ValueError(
                "service is carried on dst_endpoint.svc_name, so it requires a dst_endpoint"
            )

        # A failure record whose reason is missing is the one record the
        # error-logging requirement exists to produce.
        if self.status_id is StatusId.FAILURE and not (
            self.status_detail and self.status_detail.strip()
        ):
            raise ValueError(
                "a failed connection event must carry status_detail explaining the failure"
            )

        # Successful connections may be coalesced into a window; failures keep
        # one event each, because a count of failures with the reasons
        # collapsed cannot answer why any of them failed.
        if is_aggregate and self.status_id is StatusId.FAILURE:
            raise ValueError(
                "failed connections must not be aggregated -- emit one event per failure "
                "so each reason survives"
            )

    @property
    def type_uid(self) -> int:
        """OCSF ``type_uid`` -- ``class_uid * 100 + activity_id``."""
        return self.CLASS_UID * 100 + int(self.activity_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to an OCSF 4001 event dictionary."""
        result: dict[str, Any] = {
            "activity_id": int(self.activity_id),
            "activity_name": ACTIVITY_CAPTIONS[self.activity_id],
            "category_uid": self.CATEGORY_UID,
            "category_name": self.CATEGORY_NAME,
            "class_uid": self.CLASS_UID,
            "class_name": self.CLASS_NAME,
            "type_uid": self.type_uid,
            "time": self.time,
            "severity_id": int(self.severity_id),
            "severity": SEVERITY_CAPTIONS[self.severity_id],
            "status_id": int(self.status_id),
            "status": STATUS_CAPTIONS[self.status_id],
            "metadata": self.metadata.to_dict(),
        }

        if self.status_detail is not None:
            result["status_detail"] = self.status_detail
        if self.status_code is not None:
            result["status_code"] = self.status_code
        if self.message is not None:
            result["message"] = self.message
        if self.count is not None:
            result["count"] = self.count
        if self.start_time is not None:
            result["start_time"] = self.start_time
        if self.end_time is not None:
            result["end_time"] = self.end_time
        if self.duration is not None:
            result["duration"] = self.duration

        if self.src_endpoint is not None:
            result["src_endpoint"] = self.src_endpoint.to_dict()
        if self.dst_endpoint is not None:
            dst = self.dst_endpoint.to_dict()
            # The service label rides on the responder, per OCSF.
            if self.service is not None:
                dst["svc_name"] = self.service
            result["dst_endpoint"] = dst

        connection_info: dict[str, Any] = {
            "direction_id": int(self.connection_direction),
            "direction": DIRECTION_CAPTIONS[self.connection_direction],
        }
        if self.protocol_name is not None:
            connection_info["protocol_name"] = self.protocol_name
        result["connection_info"] = connection_info

        if self.url is not None:
            result["url"] = {"url_string": self.url}

        return result

    # Factories -- each sets the activity/status pair together.

    @classmethod
    def for_connection_success(
        cls,
        *,
        metadata: Metadata,
        dst_endpoint: Endpoint,
        src_endpoint: Endpoint | None = None,
        service: str | None = None,
        url: str | None = None,
        time: EventTime | None = None,
        protocol_name: str | None = "tcp",
        connection_direction: ConnectionDirectionId = ConnectionDirectionId.OUTBOUND,
        severity_id: SeverityId = SeverityId.INFORMATIONAL,
        message: str | None = None,
    ) -> NetworkActivity:
        """Report one connection that was established: ``Open`` / ``Success``."""
        return cls(
            activity_id=NetworkActivityId.OPEN,
            status_id=StatusId.SUCCESS,
            severity_id=severity_id,
            metadata=metadata,
            src_endpoint=src_endpoint,
            dst_endpoint=dst_endpoint,
            service=service,
            url=url,
            protocol_name=protocol_name,
            connection_direction=connection_direction,
            message=message,
            time=to_epoch_ms(time) if time is not None else now_epoch_ms(),
        )

    @classmethod
    def for_connection_failure(
        cls,
        *,
        metadata: Metadata,
        dst_endpoint: Endpoint,
        status_detail: str,
        activity_id: NetworkActivityId = NetworkActivityId.FAIL,
        src_endpoint: Endpoint | None = None,
        service: str | None = None,
        url: str | None = None,
        time: EventTime | None = None,
        status_code: str | None = None,
        severity_id: SeverityId = SeverityId.MEDIUM,
        protocol_name: str | None = "tcp",
        connection_direction: ConnectionDirectionId = ConnectionDirectionId.OUTBOUND,
        message: str | None = None,
    ) -> NetworkActivity:
        """Report a transport-level connection failure, and why.

        ``status_detail`` is required: it is the attribute the error record
        exists to carry. A status the peer *returned* is an exchange failure,
        not this -- see :meth:`for_exchange_failure`.

        Raises:
            ValueError: If ``activity_id`` is not a failure activity, or
                ``status_detail`` is empty.
        """
        # Normalize first: the constructor accepts a raw int, so this factory
        # has to as well, and the message below reads `.name`.
        activity_id = NetworkActivityId(activity_id)
        if activity_id not in _FAILURE_ACTIVITIES:
            hint = (
                " -- a connection that opened and whose exchange then "
                "failed is for_exchange_failure()"
                if activity_id is NetworkActivityId.OPEN
                else ""
            )
            raise ValueError(
                f"activity_id for a failure must be one of "
                f"{', '.join(sorted(a.name for a in _FAILURE_ACTIVITIES))}, "
                f"got {activity_id.name}{hint}"
            )

        return cls(
            activity_id=activity_id,
            status_id=StatusId.FAILURE,
            status_detail=status_detail,
            status_code=status_code,
            severity_id=severity_id,
            metadata=metadata,
            src_endpoint=src_endpoint,
            dst_endpoint=dst_endpoint,
            service=service,
            url=url,
            protocol_name=protocol_name,
            connection_direction=connection_direction,
            message=message,
            time=to_epoch_ms(time) if time is not None else now_epoch_ms(),
        )

    @classmethod
    def for_exchange_failure(
        cls,
        *,
        metadata: Metadata,
        dst_endpoint: Endpoint,
        status_detail: str,
        src_endpoint: Endpoint | None = None,
        service: str | None = None,
        url: str | None = None,
        time: EventTime | None = None,
        status_code: str | None = None,
        severity_id: SeverityId = SeverityId.MEDIUM,
        protocol_name: str | None = "tcp",
        connection_direction: ConnectionDirectionId = ConnectionDirectionId.OUTBOUND,
        message: str | None = None,
    ) -> NetworkActivity:
        """Report a connection that opened and whose exchange over it failed.

        The counterpart to :meth:`for_connection_failure`. Here the connection
        worked and stayed up -- a server answered 500, rate-limited the client,
        or sent it round a redirect loop -- so the activity is ``Open`` and
        only the status is ``Failure``. Reporting a 500 as ``Fail`` would tell
        whoever reads the log that the client never reached the peer, sending
        an investigation after a network problem that did not happen;
        reporting it as ``Open`` / ``Success`` because the socket worked would
        lose the failure entirely.

        Raises:
            ValueError: If ``status_detail`` is empty.
        """
        return cls(
            activity_id=NetworkActivityId.OPEN,
            status_id=StatusId.FAILURE,
            status_detail=status_detail,
            status_code=status_code,
            severity_id=severity_id,
            metadata=metadata,
            src_endpoint=src_endpoint,
            dst_endpoint=dst_endpoint,
            service=service,
            url=url,
            protocol_name=protocol_name,
            connection_direction=connection_direction,
            message=message,
            time=to_epoch_ms(time) if time is not None else now_epoch_ms(),
        )

    @classmethod
    def for_coalesced_successes(
        cls,
        *,
        metadata: Metadata,
        dst_endpoint: Endpoint,
        count: int,
        start_time: EventTime,
        end_time: EventTime,
        src_endpoint: Endpoint | None = None,
        service: str | None = None,
        url: str | None = None,
        protocol_name: str | None = "tcp",
        connection_direction: ConnectionDirectionId = ConnectionDirectionId.OUTBOUND,
        severity_id: SeverityId = SeverityId.INFORMATIONAL,
        time: EventTime | None = None,
        message: str | None = None,
    ) -> NetworkActivity:
        """Report a window of successful connections that shared an endpoint.

        A polling client opens connections continuously, and one event per
        connection would put more traffic on the collector than the passive
        capture beside it. A coalesced event stands in for the whole window,
        so it preserves what a per-attempt record would have answered: the
        window's bounds, how many attempts it represents, and the endpoint
        and service they shared. It is not a representative sample.

        ``time`` defaults to ``start_time``: OCSF sets an aggregate's ``time``
        to the earliest event it covers, so aggregates and discrete events
        order consistently on one timeline.

        Raises:
            ValueError: If ``count`` is below 2 or the window is inverted.
        """
        if count < 2:
            raise ValueError(
                f"a coalesced event represents more than one connection, got count={count}; "
                "report a single success with for_connection_success()"
            )

        return cls(
            activity_id=NetworkActivityId.OPEN,
            status_id=StatusId.SUCCESS,
            severity_id=severity_id,
            metadata=metadata,
            src_endpoint=src_endpoint,
            dst_endpoint=dst_endpoint,
            service=service,
            url=url,
            protocol_name=protocol_name,
            connection_direction=connection_direction,
            message=message,
            count=count,
            start_time=to_epoch_ms(start_time),
            end_time=to_epoch_ms(end_time),
            time=to_epoch_ms(time) if time is not None else to_epoch_ms(start_time),
        )
