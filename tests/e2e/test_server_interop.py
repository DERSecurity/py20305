"""End-to-end tests against a real IEEE 2030.5 server.

What these cover that the unit tests cannot: the unit suite asserts this client
behaves as we believe the standard requires, against transports we wrote
ourselves. These assert that belief survives contact with an implementation
nobody here wrote -- one whose XML, headers, status codes and resource layout
were decided by someone reading the same specification independently.

That is the only place a shared misreading shows up.
"""

from __future__ import annotations

import pytest

from py20305.client import CsipClient
from py20305.client.errors import Sep2ProtocolError
from py20305.xml.serialization import validate_xml_result

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class TestTransport:
    async def test_serves_device_capability_over_mutual_tls(
        self, connected_client: CsipClient
    ) -> None:
        """The handshake completes and the entry-point resource comes back.

        The client presents a certificate and the server accepts it; anything
        wrong with the cipher list, the chain or the client's TLS setup fails
        here rather than somewhere less obvious.
        """
        dcap = connected_client.state.dcap
        assert dcap is not None, "no DeviceCapability discovered"

    async def test_server_response_validates_against_the_bundled_schema(
        self, connected_client: CsipClient, server_url: str
    ) -> None:
        """XML from a foreign implementation validates against our shipped XSD.

        A schema we ship that only accepts XML we generate is worth very
        little. This is the assertion that says otherwise.
        """
        response = await connected_client.http.get_raw(f"{server_url}/dcap")
        assert response["status_code"] == 200, response
        ok, error = validate_xml_result(response["body"])
        assert ok, f"server DeviceCapability failed schema validation: {error}"


class TestDiscovery:
    async def test_discovers_the_resources_the_server_advertises(
        self, connected_client: CsipClient
    ) -> None:
        """Discovery walks the server's links rather than assuming a layout."""
        state = connected_client.state
        assert state.mup_list_href, "MirrorUsagePointList was not discovered"

    async def test_detects_the_server_profile(self, connected_client: CsipClient) -> None:
        """The client works out the profile from the server's own response.

        The server under test is CSIP-AUS, and the client should determine
        that from the namespaces in the DeviceCapability rather than from
        configuration -- the whole point being that a client pointed at an
        unfamiliar server adapts to it.
        """
        assert connected_client.state.csip_aus_mode, (
            "client did not detect CSIP-AUS from the server's DeviceCapability"
        )


class TestRegistration:
    async def test_registered_client_has_an_end_device(
        self, registered_client: CsipClient, client_lfdi: str
    ) -> None:
        """In-band registration produces an EndDevice the server serves back.

        The path that makes a client deployable without an out-of-band
        provisioning step, and the one most likely to be rejected by a server
        that disagrees about the EndDevice payload.
        """
        registered = {
            (ed.lfdi.hex() if isinstance(ed.lfdi, bytes) else str(ed.lfdi)).lower()
            for ed in registered_client.state.end_devices.values()
        }
        assert client_lfdi.lower() in registered, (
            f"expected {client_lfdi} registered, discovery found {sorted(registered)}"
        )

    async def test_registering_twice_is_refused(
        self, registered_client: CsipClient, client_lfdi: str
    ) -> None:
        """A device already known to the server is not registered again.

        The client checks the server's EndDeviceList before posting. Without
        that, a restart would create a second EndDevice for the same physical
        device, and the utility would see one device as two.
        """
        with pytest.raises(ValueError, match="already registered"):
            await registered_client.register_end_device(lfdi=client_lfdi, device_category=0)

    async def test_registering_another_identity_is_refused(
        self, connected_client: CsipClient, other_device_lfdi: str
    ) -> None:
        """A device certificate may only register its own identity.

        IEEE 2030.5 ties an EndDevice to the certificate presenting it: the
        SFDI in the body has to match the SFDI derived from the client
        certificate, and registering some other device requires an aggregator
        certificate instead. A server that let this through would let any
        device impersonate any other, so the refusal is the correct behavior
        and worth pinning -- if a future change to how the client builds the
        EndDevice body broke the match, this is where it would show.
        """
        with pytest.raises(Sep2ProtocolError) as caught:
            await connected_client.register_end_device(lfdi=other_device_lfdi, device_category=0)

        assert caught.value.status_code == 403, (
            f"expected the server to refuse a mismatched identity, got {caught.value}"
        )


class TestControlPath:
    async def test_registration_yields_a_der_program(self, registered_client: CsipClient) -> None:
        """A registered device is given a DERProgram to follow.

        The join between registration and control: without a program, there is
        nothing for the event engine to run.
        """
        assert registered_client.state.der_programs, (
            "no DERProgram discovered for the registered device"
        )

    async def test_polling_a_registered_device_does_not_error(
        self, registered_client: CsipClient
    ) -> None:
        """A full poll cycle completes against the live server.

        Exercises the request paths discovery does not: the per-resource polls
        the client runs on a schedule, where a disagreement about a query
        parameter or list pagination shows up.
        """
        polled = await registered_client.poll_now()
        assert polled >= 0
