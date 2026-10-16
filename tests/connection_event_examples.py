"""Build one example connection event per role, for the golden fixture.

The events are produced by the real emitter with fixed times and addresses,
so ``fixtures/connection_events.json`` is exactly what a consumer receives.
Consumers of the events can load that file to test their parser against this
library's output without depending on this library.

Regenerate the fixture after an intended change to the event shape::

    python -m tests.connection_event_examples
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from py20305.client.connector import Address, SocketPair
from py20305.client.errors import Sep2ProtocolError
from py20305.forwarders.base import EventFrame
from py20305.forwarders.config import ConnectionTelemetryConfig
from py20305.forwarders.connection_telemetry import (
    SERVICE_HTTPS,
    SERVICE_MODBUS,
    SERVICE_MQTT,
    ConnectionRole,
    ConnectionTelemetryEmitter,
)
from py20305.forwarders.ocsf import ConnectionDirectionId, Endpoint

FIXTURE = Path(__file__).parent / "fixtures" / "connection_events.json"

PRODUCT = {"product_name": "example-gateway", "vendor_name": "Example Vendor"}
VERSION = "1.2.3"
T0 = 1_760_000_000_000

GATEWAY = "192.0.2.10"


class _Collector:
    """Keep each queued event's payload."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def queue_event(self, event: EventFrame) -> None:
        self.payloads.append(event.payload)


def _emitter(*, coalesce: bool) -> tuple[ConnectionTelemetryEmitter, _Collector]:
    sink = _Collector()
    config = ConnectionTelemetryConfig(enabled=True, coalesce_successes=coalesce)
    emitter = ConnectionTelemetryEmitter(
        sink,  # type: ignore[arg-type]
        config,
        product_version=VERSION,
        **PRODUCT,
    )
    emitter.set_server("utility.example.com", 8443, base_url="https://utility.example.com:8443")
    return emitter, sink


def build_examples() -> dict[str, dict[str, Any]]:
    """Return the example events, keyed by what each one shows."""
    examples: dict[str, dict[str, Any]] = {}

    # Northbound: a coalesced window of three exchanges, then a 500.
    emitter, sink = _emitter(coalesce=True)
    emitter.begin_request()
    emitter.on_connect(
        SocketPair(
            local=Address(ip=GATEWAY, port=50412), remote=Address(ip="203.0.113.5", port=8443)
        )
    )
    emitter.record_success(now_ms=T0)
    emitter.begin_request()
    emitter.record_success(now_ms=T0 + 5_000)
    emitter.record_success(now_ms=T0 + 10_000)
    emitter.flush()
    examples["northbound_coalesced_success"] = sink.payloads.pop()
    emitter.begin_request()
    emitter.record_failure(Sep2ProtocolError("body", status_code=500))
    failure = sink.payloads.pop()
    failure["time"] = T0 + 15_000
    examples["northbound_exchange_failure"] = failure

    # Southbound: one device poll per attempt, and a refused connection.
    emitter, sink = _emitter(coalesce=False)
    device = Endpoint(ip="192.0.2.50", port=502)
    emitter.record_connection(
        role=ConnectionRole.SOUTHBOUND,
        service=SERVICE_MODBUS,
        src=Endpoint(ip=GATEWAY, port=49822),
        dst=device,
        now_ms=T0 + 20_000,
    )
    examples["southbound_success"] = sink.payloads.pop()
    emitter.record_connection_failure(
        role=ConnectionRole.SOUTHBOUND,
        service=SERVICE_MODBUS,
        reason=ConnectionRefusedError(111, "Connection refused"),
        dst=device,
        now_ms=T0 + 21_000,
    )
    examples["southbound_failure"] = sink.payloads.pop()

    # Inbound: the management console and a protocol listener.
    emitter.record_connection(
        role=ConnectionRole.MANAGEMENT,
        service=SERVICE_HTTPS,
        direction=ConnectionDirectionId.INBOUND,
        src=Endpoint(ip="192.0.2.77", port=61002),
        dst=Endpoint(ip=GATEWAY, port=8443),
        now_ms=T0 + 30_000,
    )
    examples["management_inbound_success"] = sink.payloads.pop()
    emitter.record_connection(
        role=ConnectionRole.SERVER,
        service=SERVICE_MODBUS,
        direction=ConnectionDirectionId.INBOUND,
        src=Endpoint(ip="192.0.2.80", port=53114),
        dst=Endpoint(ip=GATEWAY, port=502),
        now_ms=T0 + 31_000,
    )
    examples["server_inbound_success"] = sink.payloads.pop()

    # Telemetry: the broker connection, up and refused.
    broker = Endpoint(ip="198.51.100.20", hostname="broker.example.com", port=8883)
    emitter.record_connection(
        role=ConnectionRole.TELEMETRY,
        service=SERVICE_MQTT,
        src=Endpoint(ip=GATEWAY, port=40001),
        dst=broker,
        now_ms=T0 + 40_000,
    )
    examples["telemetry_success"] = sink.payloads.pop()
    emitter.record_connection_failure(
        role=ConnectionRole.TELEMETRY,
        service=SERVICE_MQTT,
        reason=TimeoutError("timed out"),
        dst=Endpoint(hostname="broker.example.com", port=8883),
        now_ms=T0 + 41_000,
    )
    examples["telemetry_failure"] = sink.payloads.pop()
    return examples


def write_fixture() -> None:
    """Write the examples to the fixture file."""
    text = json.dumps(build_examples(), indent=2, sort_keys=True) + "\n"
    FIXTURE.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_fixture()
