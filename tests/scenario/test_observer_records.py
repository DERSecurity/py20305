"""Observer records from a real client, end to end.

A monitoring system watching this client consumes three kinds of record, all
from observers the embedding application attaches:

- **Exchanges**: the client's IEEE 2030.5 requests and responses, as
  :class:`~py20305.observers.ExchangeRecord`, bodies exactly as sent and
  received.
- **Connections**: the client's own connection outcomes, as
  :class:`~py20305.observers.ConnectionEvent`. A passive capture cannot produce
  these: a TLS failure or a refused connection is known only to the client.
- **Device traffic**: the reads and writes the client's device connector
  performs, as :class:`~py20305.observers.DeviceRecord`.

These tests run the real client over TLS against a scriptable IEEE 2030.5
server and a recording device connector, so a regression anywhere between the
request path and the observer fails here.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from py20305.client import CsipClient, Sep2Error, TlsConfig
from py20305.client.retry import RetryPolicy
from py20305.connectors.base import BaseConnector
from py20305.connectors.config import CustomDeviceConfig
from py20305.connectors.device_telemetry import DeviceTelemetryEmitter
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.observers import (
    ConnectionEvent,
    ConnectionOutcome,
    DeviceRecord,
    ErrorKind,
    ExchangeDirection,
    ExchangeRecord,
    Observers,
)
from py20305.security import compute_lfdi
from tests.scenario.support import ScenarioServer, free_port, make_certs

pytestmark = pytest.mark.asyncio


class RecordingInverter(BaseConnector):
    """A device connector that records each control it is given."""

    telemetry_protocol = "modbus"
    writes: list[dict] = []

    async def update_op_mod_max_lim_w(self, params: dict) -> None:
        RecordingInverter.writes.append(params)


class Recorder:
    """Every record the observers received, in order."""

    def __init__(self) -> None:
        self.exchanges: list[ExchangeRecord] = []
        self.connections: list[ConnectionEvent] = []
        self.devices: list[DeviceRecord] = []

    def observers(self) -> Observers:
        return Observers(exchange=self.exchanges.append, connection=self.connections.append)


async def build_stack(
    tmp_path, *, with_device: bool = False, server_port: int | None = None
) -> SimpleNamespace:
    """Assemble a client over real sockets, with every observer recording."""
    certs = make_certs(tmp_path)
    client_lfdi = compute_lfdi(certs.client_cert.read_text())
    recorder = Recorder()

    csip: ScenarioServer | None = None
    if server_port is None:
        csip = ScenarioServer(certs, free_port())
        await csip.start()
        csip.seed_standard_tree(client_lfdi)
        server_port = csip.port

    dispatcher = None
    if with_device:
        RecordingInverter.writes = []
        registry = ConnectorConfigRegistry(
            [CustomDeviceConfig(lfdi=client_lfdi, class_path=f"{__name__}.RecordingInverter")]
        )
        dispatcher = ConnectorDispatcher(
            registry,
            lfdi_resolver=lambda _href: client_lfdi,
            telemetry=DeviceTelemetryEmitter(recorder.devices.append),
        )

    base_url = f"https://127.0.0.1:{server_port}"
    client = CsipClient(
        base_url,
        tls=TlsConfig(
            client_cert=certs.client_cert, client_key=certs.client_key, ca_cert=certs.ca_a
        ),
        retry=RetryPolicy(max_transient=1, max_tls=1, base_delay=0.05),
        dispatcher=dispatcher,
    )
    client.attach_observers(recorder.observers())

    async def close() -> None:
        await client.shutdown()
        if csip is not None:
            await csip.close()

    return SimpleNamespace(
        recorder=recorder,
        client=client,
        client_lfdi=client_lfdi,
        certs=certs,
        base_url=base_url,
        csip=csip,
        close=close,
    )


async def test_each_exchange_is_reported_with_its_bodies_and_tls_facts(tmp_path):
    stack = await build_stack(tmp_path)
    try:
        await stack.client.connect()
        await stack.client.poll_now()

        exchanges = stack.recorder.exchanges
        assert exchanges, "no exchange record was made"
        dcap = next(r for r in exchanges if r.path == "/dcap")
        assert dcap.direction is ExchangeDirection.CLIENT_REQUEST
        assert dcap.method == "GET"
        assert dcap.status == 200
        assert dcap.response_body and dcap.response_body.lstrip().startswith(b"<")
        assert dcap.resource_type == "DeviceCapability"
        assert dcap.url == f"{stack.base_url}/dcap"
        assert dcap.client_lfdi == stack.client_lfdi.lower()
        assert dcap.client_sfdi is not None
        assert dcap.tls_version and dcap.tls_version.startswith("TLS")
        assert dcap.tls_cipher
        assert dcap.server_cert_sha256 and len(dcap.server_cert_sha256) == 64
        assert dcap.remote is not None and dcap.remote.port == stack.csip.port
        assert dcap.local is not None and dcap.local.port > 0
        assert dcap.received_at is not None and dcap.received_at >= dcap.sent_at
        assert dcap.duration is not None and dcap.duration >= 0
        assert dcap.response_valid is not None
        assert len({r.exchange_id for r in exchanges}) == len(exchanges)
    finally:
        await stack.close()


async def test_a_validated_connection_is_reported_with_its_local_port(tmp_path):
    stack = await build_stack(tmp_path)
    try:
        await stack.client.connect()
        await stack.client.poll_now()

        connected = [
            e for e in stack.recorder.connections if e.outcome is ConnectionOutcome.CONNECTED
        ]
        assert connected, "a healthy poll cycle must report a connection"
        event = connected[0]
        assert event.remote is not None and event.remote.port == stack.csip.port
        assert event.local is not None and event.local.port > 0
        assert event.tls_version and event.peer_cert_sha256
    finally:
        await stack.close()


async def test_a_refused_connection_is_reported_with_its_reason(tmp_path):
    stack = await build_stack(tmp_path, server_port=free_port())  # nothing listens
    try:
        with pytest.raises(Sep2Error):
            await stack.client.connect()

        failed = [e for e in stack.recorder.connections if e.outcome is ConnectionOutcome.FAILED]
        assert failed, "a refused connection must be reported"
        assert all(e.reason.strip() for e in failed), "a failure needs its reason"
        assert all(e.error_kind is ErrorKind.CONNECT for e in failed)

        unanswered = [r for r in stack.recorder.exchanges if r.status is None]
        assert unanswered, "an attempt with no response is still an exchange record"
        assert all(r.error_kind is ErrorKind.CONNECT and r.error for r in unanswered)
    finally:
        await stack.close()


async def test_a_tls_rejection_is_reported_as_a_tls_failure(tmp_path):
    """Only the client knows the handshake failed on a certificate check."""
    stack = await build_stack(tmp_path)
    recorder = Recorder()
    untrusting = CsipClient(
        stack.base_url,
        tls=TlsConfig(
            client_cert=stack.certs.client_cert,
            client_key=stack.certs.client_key,
            ca_cert=stack.certs.ca_b,  # trusts nothing the server presents
        ),
        retry=RetryPolicy(max_transient=1, max_tls=1, base_delay=0.05),
    )
    untrusting.attach_observers(recorder.observers())
    try:
        with pytest.raises(Sep2Error):
            await untrusting.connect()

        tls_failures = [
            e
            for e in recorder.connections
            if e.outcome is ConnectionOutcome.FAILED and e.error_kind is ErrorKind.TLS
        ]
        assert tls_failures, "a TLS rejection must be reported as one"
    finally:
        await untrusting.shutdown()
        await stack.close()


async def test_a_control_reaching_the_device_is_a_device_write_record(tmp_path):
    """The command that reached the equipment, beside the one that arrived over 2030.5."""
    stack = await build_stack(tmp_path, with_device=True)
    try:
        await stack.client.connect()
        await stack.client.poll_now()

        deadline = asyncio.get_event_loop().time() + 20
        while not RecordingInverter.writes:
            if asyncio.get_event_loop().time() > deadline:
                raise AssertionError("the control never reached the device")
            await asyncio.sleep(0.1)

        writes = [r for r in stack.recorder.devices if r.operation == "write"]
        assert writes, "the write reached the device but no record was made"
        assert any(r.resource == "op_mod_max_lim_w" for r in writes)
        assert all(r.protocol == "modbus" for r in writes)
        derc = [r for r in stack.recorder.exchanges if r.resource_type == "DERControlList"]
        assert derc, "the control's own exchange is reported too"
    finally:
        await stack.close()


async def test_an_observer_that_raises_does_not_stop_the_client(tmp_path):
    stack = await build_stack(tmp_path)

    def broken(_record: object) -> None:
        raise RuntimeError("observer bug")

    stack.client.attach_observers(Observers(exchange=broken, connection=broken))
    try:
        await stack.client.connect()
        await stack.client.poll_now()
        assert stack.client.http.observer_failures > 0
    finally:
        await stack.close()
