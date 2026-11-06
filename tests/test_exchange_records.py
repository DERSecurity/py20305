"""Exchange, connection and anomaly records from the client and its listener."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from py20305.client.errors import Sep2PayloadError, Sep2ProtocolError
from py20305.client.http import Sep2Client
from py20305.client.retry import RetryPolicy
from py20305.models.sep.sep import Time
from py20305.observers import (
    REDACTED,
    AnomalyCode,
    ConnectionEvent,
    ConnectionOutcome,
    ErrorKind,
    ExchangeDirection,
    ExchangeRecord,
    Observers,
    ProtocolAnomaly,
)
from py20305.subscription.notification_server import NotificationServer
from py20305.xml.serialization import to_xml
from tests.conftest import make_time

DEVICE = "aa00000000000000000000000000000000000001"
OTHER = "cc00000000000000000000000000000000000003"
CLIENT = "bb00000000000000000000000000000000000002"

NOTIFICATION = (
    b'<Notification xmlns="urn:ieee:std:2030.5:ns">'
    b"<subscribedResource>/edev/1/fsa</subscribedResource>"
    b"<status>0</status>"
    b"<subscriptionURI>/edev/1/sub/1</subscriptionURI>"
    b"</Notification>"
)


class Recorder:
    def __init__(self) -> None:
        self.exchanges: list[ExchangeRecord] = []
        self.connections: list[ConnectionEvent] = []
        self.anomalies: list[ProtocolAnomaly] = []

    def observers(self, **options: object) -> Observers:
        return Observers(
            exchange=self.exchanges.append,
            connection=self.connections.append,
            anomaly=self.anomalies.append,
            **options,  # type: ignore[arg-type]
        )


async def _serve(aiohttp_server, app: web.Application) -> str:
    server = await aiohttp_server(app)
    return str(server.make_url("")).rstrip("/")


def _app() -> web.Application:
    time_xml = to_xml(make_time(999, 3))

    async def tm(_request: web.Request) -> web.Response:
        return web.Response(body=time_xml, content_type="application/sep+xml")

    async def broken(_request: web.Request) -> web.Response:
        return web.Response(status=500, text="boom")

    async def garbage(_request: web.Request) -> web.Response:
        return web.Response(body=b"<Time", content_type="application/sep+xml")

    async def create(_request: web.Request) -> web.Response:
        return web.Response(status=201, headers={"Location": "/mup/3"})

    async def readings(_request: web.Request) -> web.Response:
        return web.Response(status=201)

    app = web.Application()
    app.router.add_get("/tm", tm)
    app.router.add_get("/broken", broken)
    app.router.add_get("/garbage", garbage)
    app.router.add_post("/mup", create)
    app.router.add_post("/mup/3", readings)
    return app


def _mirror_usage_point(device_lfdi: str) -> bytes:
    return (
        '<MirrorUsagePoint xmlns="urn:ieee:std:2030.5:ns">'
        "<mRID>0488671834564123456789000000D17E</mRID>"
        f"<deviceLFDI>{device_lfdi.upper()}</deviceLFDI>"
        "</MirrorUsagePoint>"
    ).encode()


def _client(base_url: str, recorder: Recorder, **options: object) -> Sep2Client:
    return Sep2Client(
        base_url,
        retry=RetryPolicy(max_transient=1, max_tls=1, base_delay=0.01),
        observers=recorder.observers(**options),
    )


class TestClientRequests:
    async def test_a_successful_get_is_one_record_with_its_bodies(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        async with _client(base_url, recorder) as client:
            await client.get("/tm", Time)

        (record,) = recorder.exchanges
        assert record.direction is ExchangeDirection.CLIENT_REQUEST
        assert (record.method, record.path, record.status) == ("GET", "/tm", 200)
        assert record.url == f"{base_url}/tm"
        assert record.response_body == to_xml(make_time(999, 3))
        assert record.resource_type == "Time"
        assert record.attempt == 1
        assert record.error is None and record.error_kind is None
        assert record.received_at is not None and record.received_at >= record.sent_at
        assert record.received_monotonic is not None
        assert record.duration is not None and record.duration >= 0
        assert record.remote is not None and record.local is not None
        assert record.tls_version is None, "plain HTTP has no TLS facts"
        assert record.response_valid is True
        assert recorder.anomalies == []

    async def test_a_post_reports_the_request_body_as_sent(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        body = _mirror_usage_point(DEVICE)
        async with _client(base_url, recorder) as client:
            await client.post_bytes("/mup", body)

        (record,) = recorder.exchanges
        assert record.request_body == body
        assert record.status == 201
        assert record.resource_type == "MirrorUsagePoint"
        assert ("Location", "/mup/3") in record.response_headers

    async def test_an_unexpected_status_is_an_anomaly(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        async with _client(base_url, recorder) as client:
            with pytest.raises(Sep2ProtocolError):
                await client.get("/broken", Time)

        assert recorder.exchanges and all(r.status == 500 for r in recorder.exchanges)
        codes = {a.code for a in recorder.anomalies}
        assert AnomalyCode.UNEXPECTED_STATUS in codes
        anomaly = next(a for a in recorder.anomalies if a.code is AnomalyCode.UNEXPECTED_STATUS)
        assert anomaly.exchange_id in {r.exchange_id for r in recorder.exchanges}

    async def test_an_unparseable_body_is_an_anomaly(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        async with _client(base_url, recorder) as client:
            with pytest.raises(Sep2PayloadError):
                await client.get("/garbage", Time)

        assert any(a.code is AnomalyCode.UNPARSEABLE_BODY for a in recorder.anomalies)
        assert recorder.exchanges[0].response_body == b"<Time"

    async def test_an_attempt_with_no_response_is_a_record_and_a_failed_connection(self):
        from tests.scenario.support import free_port

        recorder = Recorder()
        async with _client(f"http://127.0.0.1:{free_port()}", recorder) as client:
            with pytest.raises(Exception):  # noqa: B017 - any client error
                await client.get("/tm", Time)

        assert recorder.exchanges
        assert all(r.status is None for r in recorder.exchanges)
        assert all(r.error_kind is ErrorKind.CONNECT and r.error for r in recorder.exchanges)
        assert [r.attempt for r in recorder.exchanges] == list(
            range(1, len(recorder.exchanges) + 1)
        )
        assert any(e.outcome is ConnectionOutcome.FAILED for e in recorder.connections)

    async def test_a_body_cut_short_after_the_status_is_a_lost_connection(self):
        async def cut_short(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/sep+xml\r\n"
                b"Content-Length: 500\r\n\r\n<Time>abc"
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(cut_short, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        recorder = Recorder()
        try:
            async with _client(f"http://127.0.0.1:{port}", recorder) as client:
                with pytest.raises(Exception):  # noqa: B017 - any client error
                    await client.get("/tm", Time)
        finally:
            server.close()
            await server.wait_closed()

        assert recorder.exchanges
        for record in recorder.exchanges:
            assert record.status == 200
            assert record.error_kind is ErrorKind.DISCONNECTED
            assert record.error
        lost = [e for e in recorder.connections if e.outcome is ConnectionOutcome.LOST]
        assert len(lost) == len(recorder.exchanges)

    async def test_a_protocol_error_with_a_status_has_no_error_kind(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        async with _client(base_url, recorder) as client:
            with pytest.raises(Sep2ProtocolError):
                await client.get("/broken", Time)

        assert all(r.error_kind is None and r.error is None for r in recorder.exchanges)
        assert all(e.outcome is ConnectionOutcome.CONNECTED for e in recorder.connections)

    async def test_body_capture_can_be_turned_off(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        async with _client(base_url, recorder, capture_bodies=False) as client:
            await client.post_bytes("/mup", _mirror_usage_point(DEVICE))

        (record,) = recorder.exchanges
        assert record.request_body is None and record.response_body is None
        assert record.resource_type == "MirrorUsagePoint", "the record still names the body"

    async def test_credentials_in_headers_are_redacted(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        client = Sep2Client(
            base_url,
            request_headers={"Authorization": "Bearer secret"},
            observers=recorder.observers(),
        )
        async with client:
            await client.get("/tm", Time)

        headers = dict(recorder.exchanges[0].request_headers)
        assert headers["Authorization"] == REDACTED
        assert "secret" not in repr(recorder.exchanges[0])

    async def test_validation_can_be_turned_off(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        async with _client(base_url, recorder, validate=False) as client:
            await client.get("/tm", Time)

        assert recorder.exchanges[0].response_valid is None

    async def test_an_observer_that_raises_does_not_change_the_result(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())

        def broken(_record: object) -> None:
            raise RuntimeError("observer bug")

        client = Sep2Client(base_url, observers=Observers(exchange=broken))
        async with client:
            result = await client.get("/tm", Time)

        assert result.current_time.value == 999
        assert client.observer_failures == 1

    async def test_without_observers_nothing_is_built(self, aiohttp_server, monkeypatch):
        import py20305.client.http as http_module

        def fail(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("an exchange scope was opened with no observer attached")

        monkeypatch.setattr(http_module, "ExchangeScope", fail)
        base_url = await _serve(aiohttp_server, _app())
        async with Sep2Client(base_url) as client:
            await client.get("/tm", Time)


class TestDeviceAttribution:
    async def test_mirror_usage_point_exchanges_carry_the_mirrored_devices_lfdi(
        self, aiohttp_server
    ):
        """A MirrorUsagePoint POST, and the readings POSTed to the location it
        returned, are attributed to the device its deviceLFDI names when that
        device is registered (IEEE 2030.5-2023 10.11)."""
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        async with _client(base_url, recorder) as client:
            client.update_device_lfdi_prefixes({"/edev/2": DEVICE})
            location = await client.post_bytes("/mup", _mirror_usage_point(DEVICE))
            await client.post_bytes(location, b"<MirrorMeterReadingList/>")
            # Rediscovery replaces the EndDevice prefixes but keeps the mapping.
            client.update_device_lfdi_prefixes({"/edev/2": DEVICE, "/edev/4": OTHER})
            await client.post_bytes(location, b"<MirrorMeterReadingList/>")

        assert [(r.path, r.device_lfdi) for r in recorder.exchanges] == [
            ("/mup", DEVICE),
            ("/mup/3", DEVICE),
            ("/mup/3", DEVICE),
        ]
        assert all(r.end_device_href == "/edev/2" for r in recorder.exchanges)

    async def test_a_mirror_usage_point_for_an_unknown_device_names_no_device(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        async with _client(base_url, recorder) as client:
            client._client_lfdi = CLIENT
            client.update_device_lfdi_prefixes({"/edev/2": DEVICE})
            location = await client.post_bytes("/mup", _mirror_usage_point(OTHER))
            await client.post_bytes(location, b"<MirrorMeterReadingList/>")

        assert [r.device_lfdi for r in recorder.exchanges] == [None, None]
        assert all(r.client_lfdi == CLIENT for r in recorder.exchanges)

    async def test_readings_stop_naming_a_device_once_it_is_gone(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        async with _client(base_url, recorder) as client:
            client.update_device_lfdi_prefixes({"/edev/2": DEVICE})
            location = await client.post_bytes("/mup", _mirror_usage_point(DEVICE))
            client.update_device_lfdi_prefixes({"/edev/4": OTHER})
            await client.post_bytes(location, b"<MirrorMeterReadingList/>")

        assert recorder.exchanges[-1].device_lfdi is None

    async def test_a_mirrored_device_is_matched_without_regard_to_case(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _app())
        recorder = Recorder()
        async with _client(base_url, recorder) as client:
            client.update_device_lfdi_prefixes({"/edev/2": DEVICE.upper()})
            location = await client.post_bytes("/mup", _mirror_usage_point(DEVICE))
            await client.post_bytes(location, b"<MirrorMeterReadingList/>")

        assert [r.device_lfdi for r in recorder.exchanges] == [DEVICE.upper()] * 2


class TestNotifications:
    @pytest.fixture
    async def listener(self):
        recorder = Recorder()
        server = NotificationServer(host="127.0.0.1", port=0, tls=None)
        server.observers = recorder.observers()
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        client = TestClient(TestServer(app))
        await client.start_server()
        yield recorder, client
        await client.close()

    async def test_a_notification_is_one_record_with_the_response_sent(self, listener):
        recorder, client = listener
        resp = await client.post(
            "/notify", data=NOTIFICATION, headers={"Content-Type": "application/sep+xml"}
        )
        assert resp.status == 201
        await asyncio.sleep(0)

        (record,) = recorder.exchanges
        assert record.direction is ExchangeDirection.NOTIFICATION
        assert (record.method, record.path, record.status) == ("POST", "/notify", 201)
        assert record.request_body == NOTIFICATION
        assert record.resource_type == "Notification"
        assert record.remote is not None
        accepted = [e for e in recorder.connections if e.outcome is ConnectionOutcome.ACCEPTED]
        assert accepted

    async def test_an_unparseable_notification_is_refused_and_an_anomaly(self, listener):
        recorder, client = listener
        resp = await client.post("/notify", data=b"<Notification")
        assert resp.status == 400

        (record,) = recorder.exchanges
        assert record.status == 400
        assert any(a.code is AnomalyCode.REJECTED_NOTIFICATION for a in recorder.anomalies)
