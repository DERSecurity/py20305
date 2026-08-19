"""Integration tests: the client against a scriptable server, over real mTLS.

Each test drives the real client -- transport, discovery, event engine --
against the scenario server and asserts on what actually crossed the wire.
These are the behaviors a utility's interoperability and error handling
expectations turn on, exercised end to end rather than against mocks.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from py20305.api.service import ClientAPIService
from py20305.client import CsipClient, TlsConfig
from py20305.client.retry import RetryPolicy
from py20305.connectors.base import BaseConnector
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.security import compute_lfdi
from tests.scenario.support import ScenarioServer, free_port, make_certs

pytestmark = pytest.mark.asyncio


class RecordingConnector(BaseConnector):
    """Remembers every control applied to it."""

    def __init__(self) -> None:
        self.controls: list[dict[str, Any]] = []

    async def update_p_lim(self, params: dict[str, Any]) -> None:
        self.controls.append(params)


def _registry_for(lfdi: str, connector: BaseConnector) -> Mock:
    registry = Mock()

    def get_connector(key: str):
        if key.lower() != lfdi.lower():
            return None
        proxy = Mock()
        proxy.aresolve = AsyncMock(return_value=connector)
        return proxy

    registry.get_connector.side_effect = get_connector
    return registry


@pytest.fixture
async def scenario(tmp_path):
    """A started server, the client's TLS material, and a client factory."""
    certs = make_certs(tmp_path)
    server = ScenarioServer(certs, free_port())
    await server.start()

    lfdi = compute_lfdi(certs.client_cert.read_text())
    connector = RecordingConnector()
    clients: list[CsipClient] = []

    def make_client(**kwargs) -> CsipClient:
        client = CsipClient(
            server.base_url,
            tls=TlsConfig(
                client_cert=certs.client_cert,
                client_key=certs.client_key,
                ca_cert=certs.ca_a,
            ),
            # Fault tests wait out every retry; production backoff would turn
            # each deliberate failure into half a minute of sleeping.
            retry=kwargs.pop(
                "retry",
                RetryPolicy(max_transient=1, max_tls=1, base_delay=0.05),
            ),
            dispatcher=ConnectorDispatcher(
                _registry_for(lfdi, connector), lfdi_resolver=lambda _href: lfdi
            ),
            **kwargs,
        )
        clients.append(client)
        return client

    yield type(
        "Scenario",
        (),
        {
            "server": server,
            "certs": certs,
            "lfdi": lfdi,
            "connector": connector,
            "make_client": staticmethod(make_client),
        },
    )

    for client in clients:
        await client.shutdown()
    await server.close()


async def _wait_for(predicate, *, timeout: float = 15.0, interval: float = 0.1) -> None:
    """Poll until true; CI runners are slow, so the ceiling is generous."""
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_full_lifecycle_applies_a_control_and_responds(scenario):
    """Discovery walk, event activation, dispatch, and the Response POST.

    The whole chain over one wire: the client walks dcap -> tm -> edev ->
    fsa -> derp -> derc, activates the already-started control, applies it
    through the connector, and -- because the control demands responses --
    POSTs a Response to the server's replyTo.
    """
    scenario.server.seed_standard_tree(scenario.lfdi)
    client = scenario.make_client()

    await client.connect()
    await client.poll_now()
    await _wait_for(lambda: scenario.connector.controls)

    walked = [r.path for r in scenario.server.log if r.method == "GET"]
    for path in ("/dcap", "/tm", "/edev", "/edev/1/fsa", "/edev/1/fsa/1/derp", "/derp/1/derc"):
        assert path in walked, f"client never fetched {path}; walk was {walked}"

    # opModMaxLimW=8000 hundredths of a percent arrives as 80 percent.
    assert scenario.connector.controls[0].get("p_lim_w") == pytest.approx(80.0)

    await _wait_for(lambda: scenario.server.requests_for("/rsps", "POST"))
    response_xml = scenario.server.requests_for("/rsps", "POST")[0].body
    assert "Response" in response_xml
    assert "CC00000000000000000000000000000001".lower() in response_xml.lower()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


async def test_registers_when_the_server_does_not_know_it(scenario):
    scenario.server.seed_standard_tree(None)  # empty EndDeviceList
    client = scenario.make_client()
    await client.connect()

    href = await client.register_end_device(lfdi=scenario.lfdi)

    assert href == "/edev/1"
    assert len(scenario.server.requests_for("/edev", "POST")) == 1
    posted = scenario.server.requests_for("/edev", "POST")[0].body
    assert scenario.lfdi.upper() in posted.upper()


async def test_does_not_duplicate_an_existing_registration(scenario):
    """Registering unconditionally would make one device look like several."""
    scenario.server.seed_standard_tree(scenario.lfdi)  # server already has it
    client = scenario.make_client()
    await client.connect()

    with pytest.raises(ValueError):
        await client.register_end_device(lfdi=scenario.lfdi)

    assert scenario.server.requests_for("/edev", "POST") == []


async def test_a_server_refusing_registration_is_survivable(scenario):
    """Many servers provision out of band; a 403 must not end the session."""
    scenario.server.seed_standard_tree(None)
    scenario.server.fail_next("/edev", status=403, method="POST")
    client = scenario.make_client()
    await client.connect()

    from py20305.client.errors import Sep2ProtocolError

    with pytest.raises(Sep2ProtocolError):
        await client.register_end_device(lfdi=scenario.lfdi)

    # The session is still good: the next poll succeeds.
    assert await client.poll_now() >= 0
    assert client.http.server_alive


# ---------------------------------------------------------------------------
# Faults on the wire
# ---------------------------------------------------------------------------


async def test_an_error_burst_does_not_end_polling(scenario):
    """A 500 is the server's problem for that request, not the session's."""
    scenario.server.seed_standard_tree(scenario.lfdi)
    client = scenario.make_client()
    await client.connect()

    scenario.server.fail_next("/derp/1/derc", times=2, status=500)
    from py20305.client.errors import Sep2Error

    for _ in range(2):
        with pytest.raises(Sep2Error):
            await client.poll_now()

    # The burst is over; the same session recovers without intervention.
    assert await client.poll_now() >= 1
    assert client.http.server_alive


async def test_malformed_xml_is_skipped_not_fatal(scenario):
    """A malformed DERControl is skipped; the session and schedule survive.

    Raising here would let one corrupt payload take down the whole poll,
    so the client deliberately records the failure and moves on -- and a
    later clean poll picks the control up.
    """
    scenario.server.seed_standard_tree(scenario.lfdi)
    scenario.server.responses["/derp/1/derc"] = (
        200,
        "application/sep+xml",
        "<DERControlList <<<",
    )
    client = scenario.make_client()
    await client.connect()
    await client.poll_now()

    # With no readable control list, the DefaultDERControl governs: the
    # connector holds the default's 100%, never the unreadable control.
    assert client.http.server_alive
    applied = [c.get("p_lim_w") for c in scenario.connector.controls]
    assert 80.0 not in applied, "an unparseable control must not dispatch"

    from tests.scenario.support import SEP_XML, derc_list_xml

    scenario.server.responses["/derp/1/derc"] = lambda: (200, SEP_XML, derc_list_xml())
    await client.poll_now()
    await _wait_for(
        lambda: any(
            c.get("p_lim_w") == pytest.approx(80.0) for c in scenario.connector.controls
        )
    )


# ---------------------------------------------------------------------------
# The HTTP-to-HTTPS redirect, over a real wire
# ---------------------------------------------------------------------------


async def test_redirect_probe_follows_a_real_301_to_tls(scenario):
    """The probe's mocked unit tests pin the fields; this pins the wire.

    A plain-HTTP listener answers 301 with a Location on the TLS port, and
    the follow-up must complete a real mutual-TLS handshake to fetch the
    DeviceCapability.
    """
    scenario.server.seed_standard_tree(scenario.lfdi)
    http_port = free_port()
    await scenario.server.start_http_redirector(http_port)

    client = scenario.make_client()
    await client.connect()
    service = ClientAPIService(client)

    # The probe builds its URL from the client's host; ours serves HTTP on a
    # test port rather than 80.
    result = await service.http_probe(path="/dcap", http_port=http_port)

    assert result["http_response"]["status_code"] == 301
    assert result["http_response"]["location"].startswith("https://")
    assert result["redirect_followed"] is True
    assert result["https_response"]["status_code"] == 200
    assert "DeviceCapability" in result["https_response"]["body_excerpt"]


# ---------------------------------------------------------------------------
# Outage and certificate rotation
# ---------------------------------------------------------------------------


async def test_an_outage_is_survived_and_recovered_from(scenario):
    scenario.server.seed_standard_tree(scenario.lfdi)
    client = scenario.make_client()
    await client.connect()
    assert await client.poll_now() >= 1

    await scenario.server.stop_listening()
    from py20305.client.errors import Sep2Error

    with pytest.raises(Sep2Error):
        await client.poll_now()
    assert client.http.server_alive is False

    await scenario.server.resume_listening()
    assert await client.poll_now() >= 1
    assert client.http.server_alive is True


async def test_ca_rotation_needs_a_trust_update_and_then_works(scenario):
    """The server re-keys under a new CA; trust is the operator's move.

    Until the client's trust store carries the new CA, the handshake must
    fail -- succeeding would mean it never verified the server. After the
    update (the same call the management API's tls-ca endpoint makes), the
    same session works again.
    """
    scenario.server.seed_standard_tree(scenario.lfdi)
    client = scenario.make_client()
    await client.connect()
    assert await client.poll_now() >= 1

    await scenario.server.rotate_server_cert(
        scenario.certs.server_b_cert, scenario.certs.server_b_key
    )
    from py20305.client.errors import Sep2Error

    with pytest.raises(Sep2Error):
        await client.poll_now()

    service = ClientAPIService(client)
    result = await service.update_ca_trust(str(scenario.certs.ca_bundle))
    assert result.get("status") == "ok"

    assert await client.poll_now() >= 1


# ---------------------------------------------------------------------------
# Auto-registration -- the flow the runner performs on start
# ---------------------------------------------------------------------------


async def test_auto_registration_registers_once_and_only_once(scenario):
    """The runner's startup flow: register if absent, never duplicate.

    Same helper the packaged command runs. First pass finds no EndDevice and
    POSTs one; after rediscovery the server lists it, and a second pass --
    a restart, in effect -- must not POST again.
    """
    from py20305.cli import _register_if_needed

    scenario.server.seed_standard_tree(None)
    client = scenario.make_client()
    await client.connect()

    await _register_if_needed(client, scenario.lfdi)
    assert len(scenario.server.requests_for("/edev", "POST")) == 1

    await client.trigger_rediscovery()
    await _register_if_needed(client, scenario.lfdi)

    assert len(scenario.server.requests_for("/edev", "POST")) == 1, (
        "a second startup against a server that lists the device must not re-POST"
    )


async def test_registration_pin_is_fetched_and_verified(scenario, caplog):
    """With a PIN configured, discovery reads the Registration resource."""
    scenario.server.seed_standard_tree(scenario.lfdi, registration_pin=111115)
    client = scenario.make_client(registration_pins={scenario.lfdi.lower(): 111115})

    import logging

    with caplog.at_level(logging.WARNING, logger="py20305.client.discovery"):
        await client.connect()

    assert scenario.server.requests_for("/edev/1/rg", "GET"), (
        "a configured PIN must make discovery fetch the Registration resource"
    )
    assert not [r for r in caplog.records if "PIN" in r.message.upper()]


async def test_registration_pin_mismatch_is_reported_not_fatal(scenario, caplog):
    """A wrong PIN is loudly reported; the session continues regardless.

    The PIN check exists to catch a mis-provisioned device, and an operator
    reads the warning -- but refusing to run would take a working device off
    a program over a bookkeeping mismatch.
    """
    scenario.server.seed_standard_tree(scenario.lfdi, registration_pin=999999)
    client = scenario.make_client(registration_pins={scenario.lfdi.lower(): 111115})

    import logging

    with caplog.at_level(logging.WARNING, logger="py20305.client.discovery"):
        await client.connect()

    assert [r for r in caplog.records if "PIN" in r.message.upper()], (
        "a PIN mismatch must be reported"
    )
    assert await client.poll_now() >= 1
