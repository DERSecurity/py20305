"""Connection events for every link: roles, direction, producer, per-attempt mode, holding.

``test_connection_telemetry.py`` covers the IEEE 2030.5 client's own path.
This file covers what a host application builds on top of it: the role label
that tells a consumer which link an event describes, the producer name the
host sets, per-link coalescing, the per-attempt setting, events held while
the transport cannot deliver them, and the broker and notification-listener
hooks.
"""

from __future__ import annotations

import json
import logging

import pytest

from py20305.client.errors import Sep2ConnectionError
from py20305.forwarders import connection_telemetry as ct
from py20305.forwarders.base import EventFrame
from py20305.forwarders.config import ConnectionTelemetryConfig
from py20305.forwarders.connection_telemetry import (
    ROLE_LABEL_PREFIX,
    SERVICE_MODBUS,
    ConnectionRole,
    ConnectionTelemetryEmitter,
    build_metadata,
    link_outcome,
    role_label,
)
from py20305.forwarders.manager import ForwarderManager
from py20305.forwarders.ocsf import ConnectionDirectionId, Endpoint, NetworkActivityId
from tests.connection_event_examples import FIXTURE, build_examples

GATEWAY = Endpoint(ip="192.0.2.10", port=8443)
DEVICE_A = Endpoint(ip="192.0.2.50", port=502)
DEVICE_B = Endpoint(ip="192.0.2.51", port=502)


class Sink:
    """Stands in for the forwarder manager and keeps what was queued."""

    def __init__(self, *, ready: bool = True) -> None:
        self.events: list[EventFrame] = []
        self.can_deliver_events = ready

    def queue_event(self, event: EventFrame) -> None:
        self.events.append(event)

    @property
    def payloads(self) -> list[dict]:
        return [e.payload for e in self.events]


def make(
    *,
    coalesce: bool = True,
    window: float = 60.0,
    sink: Sink | None = None,
    **kwargs: object,
) -> tuple[ConnectionTelemetryEmitter, Sink]:
    target = sink if sink is not None else Sink()
    config = ConnectionTelemetryConfig(
        enabled=True, coalesce_successes=coalesce, coalesce_window_seconds=window
    )
    emitter = ConnectionTelemetryEmitter(target, config, product_version="9.9", **kwargs)  # type: ignore[arg-type]
    emitter.set_server("10.0.0.9", 8443, base_url="https://10.0.0.9:8443")
    return emitter, target


def southbound_success(emitter: ConnectionTelemetryEmitter, dst: Endpoint, now_ms: int) -> None:
    emitter.record_connection(
        role=ConnectionRole.SOUTHBOUND, service=SERVICE_MODBUS, dst=dst, now_ms=now_ms
    )


class TestRoleLabels:
    def test_role_label_strings_are_the_documented_contract(self):
        assert [role_label(r) for r in ConnectionRole] == [
            "role:northbound",
            "role:southbound",
            "role:management",
            "role:server",
            "role:telemetry",
        ]
        assert ROLE_LABEL_PREFIX == "role:"

    def test_role_label_rejects_an_unknown_role(self):
        with pytest.raises(ValueError):
            role_label("utility")

    def test_northbound_client_events_carry_the_northbound_role(self):
        emitter, sink = make(coalesce=False)
        emitter.record_success(now_ms=1_000)
        emitter.record_failure(Sep2ConnectionError("down"))
        assert [p["metadata"]["labels"] for p in sink.payloads] == [
            ["role:northbound"],
            ["role:northbound"],
        ]
        assert all(p["connection_info"]["direction_id"] == 2 for p in sink.payloads)

    def test_a_reported_link_carries_its_role_direction_and_service(self):
        emitter, sink = make(coalesce=False)
        emitter.record_connection(
            role=ConnectionRole.MANAGEMENT,
            service="https",
            direction=ConnectionDirectionId.INBOUND,
            src=Endpoint(ip="192.0.2.77", port=61002),
            dst=GATEWAY,
            now_ms=5_000,
        )
        (event,) = sink.payloads
        assert event["metadata"]["labels"] == ["role:management"]
        assert event["connection_info"]["direction"] == "Inbound"
        assert event["dst_endpoint"] == {"ip": "192.0.2.10", "port": 8443, "svc_name": "https"}
        assert event["src_endpoint"] == {"ip": "192.0.2.77", "port": 61002}
        assert event["time"] == 5_000


class TestProducerName:
    def test_the_default_producer_is_this_library(self):
        product = build_metadata("1.0").to_dict()["product"]
        assert product["name"] == "py20305"
        assert product["vendor_name"] == "DER Security"

    def test_a_host_application_names_itself_as_the_producer(self):
        emitter, sink = make(coalesce=False, product_name="DERSync", vendor_name="Acme")
        emitter.record_success(now_ms=1_000)
        southbound_success(emitter, DEVICE_A, 2_000)
        for payload in sink.payloads:
            assert payload["metadata"]["product"] == {
                "name": "DERSync",
                "vendor_name": "Acme",
                "version": "9.9",
            }


class TestPerAttemptMode:
    def test_coalescing_is_on_by_default(self):
        assert ConnectionTelemetryConfig().coalesce_successes is True

    def test_per_attempt_mode_emits_every_success_despite_a_window(self):
        emitter, sink = make(coalesce=False, window=60.0)
        assert not emitter.coalescing
        for t in (1_000, 2_000, 3_000):
            emitter.record_success(now_ms=t)
            southbound_success(emitter, DEVICE_A, t)
        assert len(sink.payloads) == 6
        assert all("count" not in p for p in sink.payloads)
        assert sorted(p["time"] for p in sink.payloads) == [
            1_000,
            1_000,
            2_000,
            2_000,
            3_000,
            3_000,
        ]

    def test_coalescing_mode_holds_successes_until_the_window_closes(self):
        emitter, sink = make(coalesce=True, window=60.0)
        for t in (1_000, 2_000, 3_000):
            southbound_success(emitter, DEVICE_A, t)
        assert sink.payloads == []
        emitter.flush()
        (event,) = sink.payloads
        assert (event["count"], event["start_time"], event["end_time"]) == (3, 1_000, 3_000)


class TestPerLinkWindows:
    def test_two_devices_get_separate_windows(self):
        emitter, sink = make()
        southbound_success(emitter, DEVICE_A, 1_000)
        southbound_success(emitter, DEVICE_B, 1_500)
        southbound_success(emitter, DEVICE_A, 2_000)
        emitter.flush()
        by_ip = {p["dst_endpoint"]["ip"]: p for p in sink.payloads}
        assert by_ip["192.0.2.50"]["count"] == 2
        assert "count" not in by_ip["192.0.2.51"]

    def test_roles_never_share_a_window(self):
        emitter, sink = make()
        emitter.record_success(now_ms=1_000)
        emitter.record_connection(
            role=ConnectionRole.TELEMETRY,
            service="mqtt",
            dst=Endpoint(ip="10.0.0.9", port=8443),
            now_ms=1_100,
        )
        emitter.flush()
        assert sorted(p["metadata"]["labels"][0] for p in sink.payloads) == [
            "role:northbound",
            "role:telemetry",
        ]

    def test_inbound_connections_from_different_clients_are_not_merged(self):
        emitter, sink = make()
        for t, client in ((1_000, "192.0.2.77"), (2_000, "192.0.2.78"), (3_000, "192.0.2.77")):
            emitter.record_connection(
                role=ConnectionRole.MANAGEMENT,
                service="http",
                direction=ConnectionDirectionId.INBOUND,
                src=Endpoint(ip=client, port=40_000 + t),
                dst=GATEWAY,
                now_ms=t,
            )
        emitter.flush()
        counts = {p["src_endpoint"]["ip"]: p.get("count", 1) for p in sink.payloads}
        assert counts == {"192.0.2.77": 2, "192.0.2.78": 1}

    def test_a_window_reports_the_source_port_of_its_first_connection(self):
        emitter, sink = make()
        for t, port in ((1_000, 50_001), (2_000, 50_002)):
            emitter.record_connection(
                role=ConnectionRole.SOUTHBOUND,
                service=SERVICE_MODBUS,
                src=Endpoint(ip="192.0.2.10", port=port),
                dst=DEVICE_A,
                now_ms=t,
            )
        emitter.flush()
        (event,) = sink.payloads
        assert event["src_endpoint"]["port"] == 50_001

    def test_activity_on_one_link_closes_another_links_expired_window(self):
        emitter, sink = make(window=60.0)
        southbound_success(emitter, DEVICE_A, 1_000)
        southbound_success(emitter, DEVICE_A, 2_000)
        southbound_success(emitter, DEVICE_B, 70_000)
        (event,) = sink.payloads
        assert event["dst_endpoint"]["ip"] == "192.0.2.50"
        assert event["count"] == 2

    def test_flush_expired_reports_a_link_that_went_quiet(self):
        emitter, sink = make(window=60.0)
        southbound_success(emitter, DEVICE_A, 1_000)
        emitter.flush_expired(now_ms=30_000)
        assert sink.payloads == []
        emitter.flush_expired(now_ms=61_000)
        assert len(sink.payloads) == 1

    def test_the_oldest_window_is_reported_early_at_the_open_window_cap(self, monkeypatch):
        monkeypatch.setattr(ct, "MAX_OPEN_WINDOWS", 2)
        emitter, sink = make()
        southbound_success(emitter, DEVICE_A, 1_000)
        southbound_success(emitter, DEVICE_B, 1_100)
        assert sink.payloads == []
        southbound_success(emitter, Endpoint(ip="192.0.2.52", port=502), 1_200)
        (event,) = sink.payloads
        assert event["dst_endpoint"]["ip"] == "192.0.2.50"


class TestLinkFailures:
    def test_failures_are_never_coalesced(self):
        emitter, sink = make(coalesce=True)
        for t in (1_000, 2_000):
            emitter.record_connection_failure(
                role=ConnectionRole.SOUTHBOUND,
                service=SERVICE_MODBUS,
                reason=ConnectionRefusedError(111, "Connection refused"),
                dst=DEVICE_A,
                now_ms=t,
            )
        assert [p["time"] for p in sink.payloads] == [1_000, 2_000]
        assert all(p["activity_name"] == "Refuse" for p in sink.payloads)
        assert all(p["status"] == "Failure" for p in sink.payloads)

    def test_a_wrapped_socket_error_is_classified_through_its_context(self):
        class LibraryError(Exception):
            pass

        try:
            try:
                raise TimeoutError("timed out")
            except TimeoutError as inner:
                raise LibraryError(f"Connection error: {inner}") from None
        except LibraryError as exc:
            outcome = link_outcome(exc)
        assert outcome.activity_id is NetworkActivityId.FAIL
        assert outcome.detail.startswith("Connection timed out")

    def test_an_unknown_exception_is_reported_with_its_type(self):
        outcome = link_outcome(ValueError("bad frame"))
        assert outcome.activity_id is NetworkActivityId.FAIL
        assert outcome.detail == "Connection failure: ValueError: bad frame"

    def test_credentials_in_a_reason_never_reach_the_wire(self):
        emitter, sink = make()
        emitter.record_connection_failure(
            role=ConnectionRole.TELEMETRY,
            service="mqtt",
            reason=RuntimeError("cannot reach mqtts://user:s3cret@broker.example.com:8883/x"),
            dst=Endpoint(hostname="broker.example.com", port=8883),
        )
        text = json.dumps(sink.payloads)
        assert "s3cret" not in text
        assert "mqtts://broker.example.com:8883/x" in text

    def test_an_exchange_failure_keeps_activity_open(self):
        emitter, sink = make()
        emitter.record_connection_failure(
            role=ConnectionRole.SERVER,
            service="ieee2030.5",
            direction=ConnectionDirectionId.INBOUND,
            reason="No client certificate presented",
            activity_id=NetworkActivityId.OPEN,
            status_code="401",
            src=Endpoint(ip="203.0.113.5", port=40_000),
            dst=GATEWAY,
        )
        (event,) = sink.payloads
        assert (event["activity_name"], event["status"], event["status_code"]) == (
            "Open",
            "Failure",
            "401",
        )

    def test_an_activity_that_is_not_a_failure_is_refused_without_raising(self, caplog):
        emitter, sink = make()
        with caplog.at_level(logging.WARNING):
            emitter.record_connection_failure(
                role=ConnectionRole.SOUTHBOUND,
                service=SERVICE_MODBUS,
                reason="x",
                activity_id=NetworkActivityId.LISTEN,
                dst=DEVICE_A,
            )
        assert sink.payloads == []
        assert emitter.emit_failures == 1

    def test_a_failure_without_a_destination_is_not_reported(self):
        emitter, sink = make()
        emitter.record_connection_failure(
            role=ConnectionRole.SOUTHBOUND, service=SERVICE_MODBUS, reason="down"
        )
        assert sink.payloads == []
        assert emitter.emit_failures == 0


class TestHeldEvents:
    def test_events_are_held_while_the_transport_cannot_deliver(self):
        sink = Sink(ready=False)
        emitter, _ = make(coalesce=False, sink=sink)
        southbound_success(emitter, DEVICE_A, 1_000)
        assert sink.events == []
        assert emitter.held_events == 1
        sink.can_deliver_events = True
        emitter.release_held()
        assert [p["time"] for p in sink.payloads] == [1_000]
        assert emitter.held_events == 0

    def test_held_events_go_out_before_the_next_event(self):
        sink = Sink(ready=False)
        emitter, _ = make(coalesce=False, sink=sink)
        southbound_success(emitter, DEVICE_A, 1_000)
        sink.can_deliver_events = True
        southbound_success(emitter, DEVICE_A, 2_000)
        assert [p["time"] for p in sink.payloads] == [1_000, 2_000]

    def test_the_hold_drops_its_oldest_event_at_the_bound(self):
        sink = Sink(ready=False)
        emitter, _ = make(coalesce=False, sink=sink, max_held_events=2)
        for t in (1_000, 2_000, 3_000):
            southbound_success(emitter, DEVICE_A, t)
        assert emitter.held_events_dropped == 1
        sink.can_deliver_events = True
        emitter.release_held()
        assert [p["time"] for p in sink.payloads] == [2_000, 3_000]


class TestForwarderManagerSupport:
    class Forwarder:
        name = "fake"

        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail
            self.connection_observer: object = None

        async def start(self) -> None:
            if self.fail:
                raise ConnectionRefusedError("down")

        async def stop(self) -> None:
            pass

        def queue_event(self, event: EventFrame) -> None:
            pass

    async def test_can_deliver_events_requires_a_started_forwarder(self):
        manager = ForwarderManager()
        failing = self.Forwarder(fail=True)
        manager.add_forwarder(failing)  # type: ignore[arg-type]
        assert manager.can_deliver_events is False
        await manager.start()
        assert manager.can_deliver_events is False
        failing.fail = False
        await manager.retry_failed()
        assert manager.can_deliver_events is True

    def test_the_observer_reaches_forwarders_added_before_and_after(self):
        manager = ForwarderManager()
        before, after = self.Forwarder(), self.Forwarder()
        manager.add_forwarder(before)  # type: ignore[arg-type]
        observer = object()
        manager.connection_observer = observer  # type: ignore[assignment]
        manager.add_forwarder(after)  # type: ignore[arg-type]
        assert before.connection_observer is observer
        assert after.connection_observer is observer


class TestGoldenFixture:
    def test_the_fixture_matches_what_the_emitter_produces(self):
        """Regenerate with ``python -m tests.connection_event_examples`` after a shape change."""
        assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(
            json.dumps(build_examples())
        )

    def test_the_fixture_has_an_event_for_every_role(self):
        examples = json.loads(FIXTURE.read_text(encoding="utf-8"))
        roles = {label for e in examples.values() for label in e["metadata"]["labels"]}
        assert roles == {role_label(r) for r in ConnectionRole}
