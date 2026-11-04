"""Tests for the observer records, the guard and the queued observer."""

from __future__ import annotations

import dataclasses
import threading
import time

import pytest

from py20305.observers import (
    DEFAULT_REDACTED_HEADERS,
    NO_OBSERVERS,
    RECORD_VERSION,
    REDACTED,
    SEVERITY,
    AnomalyCode,
    AuditRecord,
    ConnectionDirection,
    ConnectionEvent,
    ConnectionOutcome,
    ConnectionRole,
    ConnectionTransport,
    DeviceOperation,
    DeviceRecord,
    Endpoint,
    ExchangeDirection,
    ExchangeRecord,
    ObserverGuard,
    Observers,
    ProtocolAnomaly,
    QueuedObserver,
    redact_headers,
)


def _exchange(**fields: object) -> ExchangeRecord:
    values: dict[str, object] = {
        "exchange_id": "x1",
        "direction": ExchangeDirection.CLIENT_REQUEST,
        "method": "GET",
        "url": "https://server.example/dcap",
        "path": "/dcap",
        "sent_at": 1.0,
        "sent_monotonic": 1.0,
    }
    values.update(fields)
    return ExchangeRecord(**values)  # type: ignore[arg-type]


class TestRecords:
    @pytest.mark.parametrize(
        "record",
        [
            _exchange(),
            ConnectionEvent(
                role=ConnectionRole.CLIENT,
                direction=ConnectionDirection.OUTBOUND,
                outcome=ConnectionOutcome.CONNECTED,
                transport=ConnectionTransport.TLS,
                remote=Endpoint("192.0.2.1", 443),
            ),
            ProtocolAnomaly.of(AnomalyCode.UNPARSEABLE_BODY, "bad body"),
            DeviceRecord(operation=DeviceOperation.READ, device="d1", resource="monitoring"),
            AuditRecord(kind="der_event", data={}),
        ],
    )
    def test_every_record_carries_the_record_version(self, record: object) -> None:
        assert record.record_version == RECORD_VERSION  # type: ignore[attr-defined]

    def test_records_are_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            _exchange().status = 200  # type: ignore[misc]

    def test_records_are_built_by_keyword(self) -> None:
        with pytest.raises(TypeError):
            ExchangeRecord("x1")  # type: ignore[call-arg,misc]

    def test_an_anomaly_takes_the_severity_of_its_code(self) -> None:
        for code in AnomalyCode:
            assert ProtocolAnomaly.of(code, "m").severity is SEVERITY[code]

    def test_endpoint_of_a_socket_name(self) -> None:
        assert Endpoint.of(("192.0.2.1", 443)) == Endpoint("192.0.2.1", 443)
        assert Endpoint.of(("::1", 8443, 0, 0)) == Endpoint("::1", 8443)
        assert Endpoint.of(None) is None
        assert str(Endpoint("::1", 8443)) == "[::1]:8443"


class TestRedaction:
    def test_credentials_are_redacted_by_default(self) -> None:
        headers = (
            ("Authorization", "Bearer secret"),
            ("cookie", "a=b"),
            ("Set-Cookie", "c=d"),
            ("Accept", "application/sep+xml"),
        )
        assert redact_headers(headers) == (
            ("Authorization", REDACTED),
            ("cookie", REDACTED),
            ("Set-Cookie", REDACTED),
            ("Accept", "application/sep+xml"),
        )

    def test_order_and_repeats_are_kept(self) -> None:
        headers = (("X-A", "1"), ("Cookie", "a"), ("X-A", "2"))
        assert [name for name, _ in redact_headers(headers)] == ["X-A", "Cookie", "X-A"]

    def test_a_custom_list_replaces_the_default(self) -> None:
        headers = (("Authorization", "secret"), ("X-Api-Key", "k"))
        assert redact_headers(headers, {"x-api-key"}) == (
            ("Authorization", "secret"),
            ("X-Api-Key", REDACTED),
        )

    def test_the_default_list(self) -> None:
        assert {"authorization", "cookie", "set-cookie"} == DEFAULT_REDACTED_HEADERS


class TestObservers:
    def test_no_observers_attaches_nothing(self) -> None:
        assert not NO_OBSERVERS.any
        assert NO_OBSERVERS.capture_bodies is True

    def test_any_is_true_with_one_observer(self) -> None:
        assert Observers(anomaly=lambda _record: None).any


class TestObserverGuard:
    def test_an_exception_is_counted_and_not_raised(self) -> None:
        guard = ObserverGuard("test observer")

        def broken(_record: object) -> None:
            raise RuntimeError("boom")

        guard.deliver(broken, _exchange())
        guard.deliver(broken, _exchange())

        assert guard.failures == 2

    def test_no_observer_is_a_no_op(self) -> None:
        guard = ObserverGuard("test observer")
        guard.deliver(None, _exchange())
        assert guard.failures == 0

    def test_the_record_reaches_the_observer(self) -> None:
        seen: list[object] = []
        record = _exchange()
        ObserverGuard("test observer").deliver(seen.append, record)
        assert seen == [record]


class TestQueuedObserver:
    def test_records_arrive_in_order_on_another_thread(self) -> None:
        seen: list[tuple[str, str]] = []
        queued: QueuedObserver[ExchangeRecord] = QueuedObserver(
            lambda r: seen.append((r.exchange_id, threading.current_thread().name)),
            name="exchanges",
        )
        for i in range(5):
            queued(_exchange(exchange_id=str(i)))
        queued.close()

        assert [exchange_id for exchange_id, _ in seen] == ["0", "1", "2", "3", "4"]
        assert {thread for _, thread in seen} == {"exchanges"}

    def test_a_full_queue_drops_and_counts(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow(_record: object) -> None:
            started.set()
            release.wait(5)

        queued: QueuedObserver[ExchangeRecord] = QueuedObserver(slow, maxsize=1)
        queued(_exchange(exchange_id="held"))
        started.wait(5)
        queued(_exchange(exchange_id="queued"))
        queued(_exchange(exchange_id="dropped"))

        assert queued.dropped == 1
        release.set()
        queued.close()

    def test_target_exceptions_are_counted(self) -> None:
        def broken(_record: object) -> None:
            raise ValueError("bad")

        queued: QueuedObserver[ExchangeRecord] = QueuedObserver(broken)
        queued(_exchange())
        queued.close()

        assert queued.failures == 1

    def test_a_record_after_close_is_dropped(self) -> None:
        queued: QueuedObserver[ExchangeRecord] = QueuedObserver(lambda _r: None)
        queued.close()
        queued(_exchange())
        assert queued.dropped == 1

    def test_calling_returns_without_waiting_for_the_target(self) -> None:
        release = threading.Event()
        queued: QueuedObserver[ExchangeRecord] = QueuedObserver(lambda _r: release.wait(5))
        start = time.monotonic()
        queued(_exchange())
        assert time.monotonic() - start < 1
        release.set()
        queued.close()

    def test_maxsize_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            QueuedObserver(lambda _r: None, maxsize=0)
