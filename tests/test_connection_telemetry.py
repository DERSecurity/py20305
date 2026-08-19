"""Connection telemetry: classification, coalescing, the emitter, and its wiring.

The scenario suite proves the assembled path over real sockets
(``tests/scenario/test_intrusion_detection.py``); this file pins the module's
internal contracts -- which exception becomes which OCSF outcome, how the
coalescing window counts, what the emitter refuses to let escape into the
request path, and that the client's observer seam calls each hook when it
says it does.
"""

from __future__ import annotations

import logging
import ssl

import pytest
from pydantic import ValidationError

from py20305.client.connector import Address, SocketPair
from py20305.client.errors import (
    Sep2ConnectionError,
    Sep2NoContentError,
    Sep2PayloadError,
    Sep2ProtocolError,
    Sep2RateLimitError,
    Sep2RedirectError,
    Sep2TlsError,
)
from py20305.client.observer import ConnectionObserver
from py20305.forwarders.base import EventFrame
from py20305.forwarders.config import (
    PROTOCOL_MESSAGE_TOPIC_SUFFIX,
    ConnectionTelemetryConfig,
    DeviceTelemetryConfig,
    ForwarderConfig,
    MQTTForwarderConfig,
)
from py20305.forwarders.connection_telemetry import (
    MAX_STATUS_DETAIL_CHARS,
    CoalescingWindow,
    ConnectionTelemetryEmitter,
    Outcome,
    build_coalesced_success_event,
    build_failure_event,
    build_metadata,
    classify,
)
from py20305.forwarders.ocsf import Endpoint, NetworkActivityId


class RecordingForwarder:
    """Stands in for the forwarder manager, keeping what was queued."""

    def __init__(self) -> None:
        self.events: list[EventFrame] = []

    def queue_event(self, event: EventFrame) -> None:
        self.events.append(event)


class RaisingForwarder:
    def queue_event(self, event: EventFrame) -> None:
        raise RuntimeError("broker exploded")


def make_emitter(
    *,
    enabled: bool = True,
    window: float = 0.0,
    forwarder: object = None,
) -> tuple[ConnectionTelemetryEmitter, RecordingForwarder]:
    fw = forwarder if forwarder is not None else RecordingForwarder()
    config = ConnectionTelemetryConfig(enabled=enabled, coalesce_window_seconds=window)
    emitter = ConnectionTelemetryEmitter(fw, config, product_version="test")
    emitter.set_server("10.0.0.9", 8443, base_url="https://10.0.0.9:8443")
    return emitter, fw


SOCKET = SocketPair(
    local=Address(ip="10.0.0.2", port=52511), remote=Address(ip="10.0.0.9", port=8443)
)


# -- Classification -----------------------------------------------------------


class TestClassify:
    def test_a_204_is_not_a_connection_outcome(self):
        """It subclasses the protocol error, so order of checks matters."""
        assert classify(Sep2NoContentError("GET /x returned 204")) is None

    def test_a_tls_error_is_a_transport_failure(self):
        outcome = classify(Sep2TlsError("handshake failed"))
        assert outcome.activity_id is NetworkActivityId.FAIL
        assert "TLS" in outcome.detail

    def test_a_refusal_is_distinguished_through_the_cause_chain(self):
        """The retry wrapper buries the errno one or more links down."""
        exc = Sep2ConnectionError("gave up")
        exc.__cause__ = OSError("wrapped")
        exc.__cause__.__cause__ = ConnectionRefusedError("refused")
        assert classify(exc).activity_id is NetworkActivityId.REFUSE

    def test_a_plain_connection_error_reports_fail(self):
        assert classify(Sep2ConnectionError("timeout")).activity_id is NetworkActivityId.FAIL

    def test_a_server_disconnect_after_the_handshake_reports_reset(self):
        """The connection opened and was torn down; Fail would say it never did."""
        import aiohttp

        exc = Sep2ConnectionError("gave up")
        exc.__cause__ = OSError("Server disconnected")
        exc.__cause__.__cause__ = aiohttp.ServerDisconnectedError()
        assert classify(exc).activity_id is NetworkActivityId.RESET

    def test_a_raw_connection_reset_reports_reset(self):
        assert classify(ConnectionResetError()).activity_id is NetworkActivityId.RESET

    def test_a_rate_limit_is_an_exchange_failure_with_the_peers_code(self):
        outcome = classify(Sep2RateLimitError("slow down"))
        assert outcome.activity_id is NetworkActivityId.OPEN
        assert outcome.status_code == "429"

    def test_a_redirect_is_an_exchange_failure(self):
        outcome = classify(Sep2RedirectError("moved", "https://elsewhere/dcap", 301))
        assert outcome.activity_id is NetworkActivityId.OPEN

    def test_a_payload_error_is_an_exchange_failure(self):
        outcome = classify(Sep2PayloadError("bad xml", path="/dcap", body_length=10))
        assert outcome.activity_id is NetworkActivityId.OPEN

    def test_a_protocol_error_is_an_exchange_failure(self):
        assert classify(Sep2ProtocolError("500", 500)).activity_id is NetworkActivityId.OPEN

    def test_raw_transport_exceptions_are_classified_too(self):
        assert classify(ssl.SSLError("bad cert")).activity_id is NetworkActivityId.FAIL
        assert classify(ConnectionRefusedError()).activity_id is NetworkActivityId.REFUSE
        assert classify(TimeoutError()).activity_id is NetworkActivityId.FAIL
        assert classify(OSError("no route")).activity_id is NetworkActivityId.FAIL

    def test_an_unrelated_exception_reports_nothing(self):
        assert classify(ValueError("not a connection thing")) is None

    def test_a_circular_cause_chain_terminates(self):
        a = Sep2ConnectionError("a")
        b = OSError("b")
        a.__cause__ = b
        b.__cause__ = a
        assert classify(a).activity_id is NetworkActivityId.FAIL  # and returns at all


class TestOutcomeBounds:
    def test_reason_text_is_clipped_at_the_boundary(self):
        """Clipped in the dataclass, so no caller can route around it."""
        outcome = Outcome(NetworkActivityId.FAIL, "x" * (MAX_STATUS_DETAIL_CHARS + 100))
        assert "truncated" in outcome.detail

    def test_the_cap_is_the_total_length_marker_included(self):
        """The documented bound is what lands on the wire, not a prefix of it."""
        outcome = Outcome(NetworkActivityId.FAIL, "x" * (MAX_STATUS_DETAIL_CHARS * 3))
        assert len(outcome.detail) == MAX_STATUS_DETAIL_CHARS

    def test_short_reasons_pass_through_untouched(self):
        assert Outcome(NetworkActivityId.FAIL, "short").detail == "short"


# -- Coalescing ----------------------------------------------------------------


class TestCoalescingWindow:
    def test_zero_window_emits_every_success(self):
        window = CoalescingWindow(0)
        assert window.record(1000).count == 1
        assert window.record(1001).count == 1

    def test_first_success_opens_a_window_and_emits_nothing(self):
        window = CoalescingWindow(60)
        assert window.record(1000) is None
        assert window.pending == 1

    def test_successes_inside_the_window_accumulate(self):
        window = CoalescingWindow(60)
        window.record(1000)
        assert window.record(2000) is None
        assert window.record(3000) is None
        assert window.pending == 3

    def test_a_success_past_the_window_closes_it_and_opens_the_next(self):
        """The closing success is not in the closed window -- counted once, in the next."""
        window = CoalescingWindow(60)
        window.record(1_000)
        window.record(2_000)
        closed = window.record(61_500)
        assert closed.count == 2
        assert (closed.start_ms, closed.end_ms) == (1_000, 2_000)
        assert window.pending == 1  # the closer opened the next window

    def test_flush_reports_the_pending_window_and_resets(self):
        window = CoalescingWindow(60)
        window.record(1000)
        window.record(2000)
        flushed = window.flush()
        assert flushed.count == 2
        assert window.flush() is None

    def test_the_window_keeps_the_socket_it_opened_with(self):
        """The close-time socket belongs to a different request's connection."""
        opener = SOCKET
        closer = SocketPair(local=Address(ip="10.0.0.2", port=59999), remote=None)
        window = CoalescingWindow(60)
        window.record(1_000, opener)
        closed = window.record(61_500, closer)
        assert closed.socket is opener

    def test_a_window_opened_blind_adopts_the_first_socket_inside_it(self):
        window = CoalescingWindow(60)
        window.record(1_000, None)
        window.record(2_000, SOCKET)
        assert window.flush().socket is SOCKET

    def test_a_negative_window_is_rejected(self):
        with pytest.raises(ValueError):
            CoalescingWindow(-1)


# -- Event builders --------------------------------------------------------------


class TestBuilders:
    METADATA = build_metadata("test")

    def test_no_destination_at_all_builds_nothing(self):
        outcome = Outcome(NetworkActivityId.FAIL, "why")
        assert (
            build_failure_event(
                outcome, metadata=self.METADATA, socket_pair=None, server_endpoint=None
            )
            is None
        )

    def test_a_transport_failure_uses_the_connection_factory(self):
        outcome = Outcome(NetworkActivityId.REFUSE, "refused")
        event = build_failure_event(
            outcome,
            metadata=self.METADATA,
            socket_pair=None,
            server_endpoint=Endpoint(ip="10.0.0.9", port=8443),
        )
        data = event.to_dict()
        assert data["activity_name"] == "Refuse"
        assert data["status"] == "Failure"
        assert data["dst_endpoint"]["ip"] == "10.0.0.9"

    def test_an_exchange_failure_stays_open(self):
        """A 500 reported as Fail sends readers after a network problem that never happened."""
        outcome = Outcome(NetworkActivityId.OPEN, "500", "500")
        event = build_failure_event(
            outcome,
            metadata=self.METADATA,
            socket_pair=SOCKET,
            server_endpoint=None,
        )
        data = event.to_dict()
        assert data["activity_name"] == "Open"
        assert data["status"] == "Failure"
        assert data["status_code"] == "500"

    def test_the_live_socket_beats_the_configured_fallback(self):
        outcome = Outcome(NetworkActivityId.OPEN, "500")
        event = build_failure_event(
            outcome,
            metadata=self.METADATA,
            socket_pair=SOCKET,
            server_endpoint=Endpoint(ip="203.0.113.1", port=1),
        )
        data = event.to_dict()
        assert data["src_endpoint"] == {"ip": "10.0.0.2", "port": 52511}
        assert data["dst_endpoint"]["ip"] == "10.0.0.9"

    def test_a_window_of_one_is_a_discrete_event(self):
        from py20305.forwarders.connection_telemetry import Window

        event = build_coalesced_success_event(
            Window(1, 5_000, 5_000),
            metadata=self.METADATA,
            server_endpoint=Endpoint(ip="10.0.0.9", port=8443),
        )
        data = event.to_dict()
        assert "count" not in data
        assert data["time"] == 5_000

    def test_a_window_of_many_is_an_aggregate_with_bounds(self):
        from py20305.forwarders.connection_telemetry import Window

        event = build_coalesced_success_event(
            Window(4, 5_000, 65_000),
            metadata=self.METADATA,
            server_endpoint=Endpoint(ip="10.0.0.9", port=8443),
        )
        data = event.to_dict()
        assert data["count"] == 4
        assert (data["start_time"], data["end_time"]) == (5_000, 65_000)
        assert data["duration"] == 60_000


# -- The emitter ------------------------------------------------------------------


class TestServerEndpoint:
    """OCSF types `ip` as an IP address; a DNS name is a different attribute."""

    def test_an_ip_literal_lands_in_ip(self):
        emitter, fw = make_emitter()
        emitter.record_failure(Sep2TlsError("bad chain"))
        assert fw.events[0].payload["dst_endpoint"] == {
            "ip": "10.0.0.9",
            "port": 8443,
            "svc_name": "ieee2030.5",
        }

    def test_a_dns_name_lands_in_hostname_not_ip(self):
        """A hostname in an `ip` field is a schema violation a consumer may reject."""
        emitter, fw = make_emitter()
        emitter.set_server("utility.example.com", 8443, base_url="https://utility.example.com:8443")
        emitter.record_failure(Sep2TlsError("bad chain"))
        dst = fw.events[0].payload["dst_endpoint"]
        assert dst["hostname"] == "utility.example.com"
        assert "ip" not in dst

    def test_an_endpoint_naming_nothing_is_rejected(self):
        with pytest.raises(ValueError):
            Endpoint(port=443)

    def test_credentials_in_the_server_url_never_reach_the_wire(self):
        """A user:password@ URL published to the broker is a credential leak."""
        emitter, fw = make_emitter()
        emitter.set_server(
            "utility.example.com", 8443, base_url="https://user:hunter2@utility.example.com:8443"
        )
        emitter.record_failure(Sep2TlsError("bad chain"))
        url = fw.events[0].payload["url"]["url_string"]
        assert "hunter2" not in url and "user" not in url
        assert url == "https://utility.example.com:8443"


class TestEmitter:
    def test_it_satisfies_the_clients_observer_seam(self):
        emitter, _ = make_emitter()
        assert isinstance(emitter, ConnectionObserver)

    def test_disabled_records_nothing(self):
        emitter, fw = make_emitter(enabled=False)
        emitter.record_success()
        emitter.record_failure(Sep2TlsError("x"))
        emitter.flush()
        assert fw.events == []

    def test_no_forwarder_means_disabled(self):
        config = ConnectionTelemetryConfig(enabled=True)
        assert not ConnectionTelemetryEmitter(None, config).enabled

    def test_a_success_becomes_an_event_on_the_configured_topic(self):
        emitter, fw = make_emitter(window=0.0)
        emitter.record_success(now_ms=1_000)
        assert len(fw.events) == 1
        frame = fw.events[0]
        assert frame.topic_suffix == "out/connection-events"
        assert frame.kind == "connection-event"
        assert frame.payload["class_uid"] == 4001
        assert frame.payload["status"] == "Success"

    def test_a_failure_becomes_an_event_with_its_reason(self):
        emitter, fw = make_emitter()
        emitter.record_failure(Sep2TlsError("bad chain"))
        assert len(fw.events) == 1
        payload = fw.events[0].payload
        assert payload["status"] == "Failure"
        assert "bad chain" in payload["status_detail"]

    def test_an_unclassifiable_exception_emits_nothing(self):
        emitter, fw = make_emitter()
        emitter.record_failure(ValueError("not a connection outcome"))
        assert fw.events == []

    def test_socket_attribution_is_scoped_to_the_request(self):
        """A pooled request must not inherit the previous request's port."""
        emitter, fw = make_emitter(window=0.0)
        emitter.begin_request()
        emitter.on_connect(SOCKET)
        emitter.record_success(now_ms=1_000)
        assert fw.events[0].payload["src_endpoint"]["port"] == 52511

        emitter.begin_request()  # next request reuses the pool: no connect fires
        emitter.record_success(now_ms=2_000)
        assert "src_endpoint" not in fw.events[1].payload

    def test_flush_reports_the_open_window(self):
        emitter, fw = make_emitter(window=3600.0)
        emitter.record_success(now_ms=1_000)
        emitter.record_success(now_ms=2_000)
        assert fw.events == []
        emitter.flush()
        assert len(fw.events) == 1
        assert fw.events[0].payload["count"] == 2

    def test_emit_failures_never_reach_the_request_path(self, caplog):
        """Swallowed, counted, and said out loud exactly once at WARNING."""
        emitter, _ = make_emitter(forwarder=RaisingForwarder())
        with caplog.at_level(logging.WARNING):
            emitter.record_success(now_ms=1_000)  # must not raise
            emitter.record_success(now_ms=2_000)
        assert emitter.emit_failures == 2
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, "the first failure warns; the rest stay at debug"


# -- Configuration ------------------------------------------------------------------


class TestConnectionTelemetryConfig:
    def test_off_by_default(self):
        config = ConnectionTelemetryConfig()
        assert config.enabled is False
        assert config.topic_suffix == "out/connection-events"
        assert config.coalesce_window_seconds == 60.0

    def test_the_protocol_message_topic_is_rejected(self):
        """OCSF envelopes on the capture topic is a mix nothing downstream flags."""
        with pytest.raises(ValidationError, match="protocol messages"):
            ConnectionTelemetryConfig(topic_suffix=PROTOCOL_MESSAGE_TOPIC_SUFFIX)

    def test_mqtt_wildcards_are_rejected(self):
        for bad in ("out/+/events", "out/#"):
            with pytest.raises(ValidationError, match="wildcards"):
                ConnectionTelemetryConfig(topic_suffix=bad)

    def test_an_empty_topic_is_rejected(self):
        with pytest.raises(ValidationError):
            ConnectionTelemetryConfig(topic_suffix="  / ")

    def test_a_negative_window_is_rejected(self):
        with pytest.raises(ValidationError):
            ConnectionTelemetryConfig(coalesce_window_seconds=-0.1)

    def test_non_finite_windows_are_rejected(self):
        """NaN and infinity pass a bare `< 0` check and crash at startup instead."""
        for bad in (float("nan"), float("inf")):
            with pytest.raises(ValidationError, match="finite"):
                ConnectionTelemetryConfig(coalesce_window_seconds=bad)

    def test_surrounding_slashes_are_normalized(self):
        assert ConnectionTelemetryConfig(topic_suffix="/out/events/").topic_suffix == "out/events"


class TestTelemetryTopicsDiffer:
    MQTT = MQTTForwarderConfig(endpoint="127.0.0.1", port=1883)

    def test_both_enabled_on_one_topic_is_rejected(self):
        with pytest.raises(ValidationError, match="different topics"):
            ForwarderConfig(
                mqtt=self.MQTT,
                connection_telemetry=ConnectionTelemetryConfig(
                    enabled=True, topic_suffix="out/shared"
                ),
                device_telemetry=DeviceTelemetryConfig(enabled=True, topic_suffix="out/shared"),
            )

    def test_device_telemetrys_empty_default_means_the_protocol_topic(self):
        """The comparison is on effective topics, not raw strings."""
        with pytest.raises(ValidationError, match="different topics"):
            ForwarderConfig(
                mqtt=self.MQTT,
                connection_telemetry=ConnectionTelemetryConfig(
                    # The connection validator itself rejects the constant, so
                    # reach the model validator through the device side.
                    enabled=True,
                    topic_suffix="out/device-telemetry",
                ),
                device_telemetry=DeviceTelemetryConfig(
                    enabled=True, topic_suffix="out/device-telemetry"
                ),
            )

    def test_the_defaults_coexist(self):
        config = ForwarderConfig(
            mqtt=self.MQTT,
            connection_telemetry=ConnectionTelemetryConfig(enabled=True),
            device_telemetry=DeviceTelemetryConfig(enabled=True),
        )
        assert config.connection_telemetry.topic_suffix == "out/connection-events"

    def test_disabled_channels_are_not_checked(self):
        ForwarderConfig(
            mqtt=self.MQTT,
            connection_telemetry=ConnectionTelemetryConfig(topic_suffix="out/shared"),
            device_telemetry=DeviceTelemetryConfig(topic_suffix="out/shared"),
        )

    def test_device_telemetry_rejects_wildcards_too(self):
        with pytest.raises(ValidationError, match="wildcards"):
            DeviceTelemetryConfig(topic_suffix="out/#")


# -- Runner wiring ------------------------------------------------------------------


class TestCliWiring:
    """The emitter must arrive at the client's observer seam, not merely exist.

    Each layer of connection telemetry works when handed its collaborators
    directly; these tests pin the assembly the runner actually performs, which
    is where a wiring gap would silently disable the feature with every unit
    test still green.
    """

    @staticmethod
    def _config(tmp_path, forwarders: dict | None):
        from py20305.config import ClientConfig
        from tests.test_device_telemetry import _write_client_cert

        cert = _write_client_cert(tmp_path)
        raw: dict = {
            "server": {"url": "https://server.example.com:8443"},
            "tls": {
                "client_cert": str(cert),
                "client_key": str(cert),
                "ca_cert": str(cert),
            },
        }
        if forwarders is not None:
            raw["forwarders"] = forwarders
        return ClientConfig.model_validate(raw)

    def test_enabled_telemetry_reaches_the_observer_seam(self, tmp_path):
        from py20305.cli import build_client

        config = self._config(
            tmp_path,
            {
                "mqtt": {"endpoint": "broker.example.com"},
                "connection_telemetry": {"enabled": True},
            },
        )
        client, _ = build_client(config)
        observer = client.http.connection_observer
        assert isinstance(observer, ConnectionTelemetryEmitter)
        assert observer.enabled, "attached but disabled is the same silent failure"

    def test_the_emitter_knows_the_configured_server(self, tmp_path):
        """A connection that never establishes must still name its target."""
        from py20305.cli import build_client

        config = self._config(
            tmp_path,
            {
                "mqtt": {"endpoint": "broker.example.com"},
                "connection_telemetry": {"enabled": True},
            },
        )
        client, _ = build_client(config)
        emitter = client.http.connection_observer
        endpoint = emitter._server_endpoint
        assert endpoint is not None
        # A DNS name is carried as `hostname`; `ip` is reserved for addresses.
        assert (endpoint.hostname, endpoint.port) == ("server.example.com", 8443)

    def test_disabled_telemetry_attaches_nothing(self, tmp_path):
        from py20305.cli import build_client

        config = self._config(tmp_path, {"mqtt": {"endpoint": "broker.example.com"}})
        client, _ = build_client(config)
        assert client.http.connection_observer is None

    def test_enabled_without_a_forwarder_warns_and_attaches_nothing(self, tmp_path, caplog):
        """An enabled channel with no transport looks like a client that never connects."""
        from py20305.cli import build_client

        config = self._config(tmp_path, {"connection_telemetry": {"enabled": True}})
        with caplog.at_level(logging.WARNING):
            client, _ = build_client(config)
        assert client.http.connection_observer is None
        assert any(
            "connection telemetry" in r.message and "no forwarder" in r.message
            for r in caplog.records
        )
