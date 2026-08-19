"""A scriptable IEEE 2030.5 server for integration-testing the client.

Not a conformance tool and not a reference server: a test double that speaks
just enough of the protocol, over real mutual TLS, for the client to walk a
resource tree, register, receive controls, and post responses -- while every
request it makes is recorded and every response can be scripted, delayed into
failure, or served malformed.

The unit suite proves the client against transports we mock; the e2e suite
proves it against a real server we cannot script. This sits between: a real
wire, with faults on demand.
"""

from __future__ import annotations

import contextlib
import datetime
import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

NS = 'xmlns="urn:ieee:std:2030.5:ns"'
SEP_XML = "application/sep+xml"


# ---------------------------------------------------------------------------
# Certificates: one CA the deployment trusts, and a second for rotation tests
# ---------------------------------------------------------------------------


@dataclass
class ScenarioCerts:
    ca_a: Path
    server_a_cert: Path
    server_a_key: Path
    client_cert: Path
    client_key: Path
    ca_b: Path
    server_b_cert: Path
    server_b_key: Path
    ca_bundle: Path  # A + B, for the rotation test's trust update


def _issue(
    subject: str,
    issuer_name: x509.Name,
    issuer_key: ec.EllipticCurvePrivateKey | None,
    *,
    is_ca: bool = False,
    san: bool = False,
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer_name if issuer_key else name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
    )
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
    cert = builder.sign(issuer_key or key, hashes.SHA256())
    return cert, key


def make_certs(tmp: Path) -> ScenarioCerts:
    """Two independent CAs, ECDSA throughout -- the client's cipher baseline."""

    def pem(cert: x509.Certificate) -> bytes:
        return cert.public_bytes(serialization.Encoding.PEM)

    def key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
        return key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    out: dict[str, Path] = {}
    chains = {}
    for tag in ("a", "b"):
        ca_cert, ca_key = _issue(f"Scenario CA {tag.upper()}", x509.Name([]), None, is_ca=True)
        srv_cert, srv_key = _issue(f"scenario-server-{tag}", ca_cert.subject, ca_key, san=True)
        chains[tag] = (ca_cert, ca_key, srv_cert, srv_key)
        out[f"ca_{tag}"] = tmp / f"ca_{tag}.pem"
        out[f"ca_{tag}"].write_bytes(pem(ca_cert))
        out[f"server_{tag}_cert"] = tmp / f"server_{tag}.pem"
        out[f"server_{tag}_cert"].write_bytes(pem(srv_cert))
        out[f"server_{tag}_key"] = tmp / f"server_{tag}.key"
        out[f"server_{tag}_key"].write_bytes(key_pem(srv_key))

    cli_cert, cli_key = _issue("scenario-client", chains["a"][0].subject, chains["a"][1])
    out["client_cert"] = tmp / "client.pem"
    out["client_cert"].write_bytes(pem(cli_cert))
    out["client_key"] = tmp / "client.key"
    out["client_key"].write_bytes(key_pem(cli_key))

    bundle = tmp / "ca_bundle.pem"
    bundle.write_bytes(out["ca_a"].read_bytes() + out["ca_b"].read_bytes())

    return ScenarioCerts(
        ca_a=out["ca_a"],
        server_a_cert=out["server_a_cert"],
        server_a_key=out["server_a_key"],
        client_cert=out["client_cert"],
        client_key=out["client_key"],
        ca_b=out["ca_b"],
        server_b_cert=out["server_b_cert"],
        server_b_key=out["server_b_key"],
        ca_bundle=bundle,
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ---------------------------------------------------------------------------
# The resource tree, shaped like the samples the client's own tests parse
# ---------------------------------------------------------------------------


def dcap_xml(poll_rate: int = 1) -> str:
    return (
        f'<DeviceCapability {NS} href="/dcap" pollRate="{poll_rate}">'
        '<TimeLink href="/tm"/>'
        '<EndDeviceListLink href="/edev" all="1"/>'
        "</DeviceCapability>"
    )


def time_xml() -> str:
    now = int(time.time())
    return (
        f'<Time {NS} href="/tm">'
        f"<currentTime>{now}</currentTime><dstEndTime>0</dstEndTime>"
        "<dstOffset>0</dstOffset><dstStartTime>0</dstStartTime>"
        "<localTime>0</localTime><quality>4</quality><tzOffset>0</tzOffset>"
        "</Time>"
    )


def edev_list_xml(lfdi: str | None) -> str:
    if lfdi is None:
        return f'<EndDeviceList {NS} href="/edev" all="0" results="0"/>'
    sfdi = 111111111111
    return (
        f'<EndDeviceList {NS} href="/edev" all="1" results="1">'
        '<EndDevice href="/edev/1">'
        '<FunctionSetAssignmentsListLink href="/edev/1/fsa" all="1"/>'
        f"<lFDI>{lfdi.upper()}</lFDI><sFDI>{sfdi}</sFDI>"
        "<changedTime>1</changedTime>"
        "</EndDevice></EndDeviceList>"
    )


def fsa_list_xml() -> str:
    return (
        f'<FunctionSetAssignmentsList {NS} href="/edev/1/fsa" all="1" results="1">'
        '<FunctionSetAssignments href="/edev/1/fsa/1">'
        '<DERProgramListLink href="/edev/1/fsa/1/derp" all="1"/>'
        "<mRID>AA00000000000000000000000000000001</mRID>"
        "</FunctionSetAssignments></FunctionSetAssignmentsList>"
    )


def derp_list_xml(poll_rate: int = 1) -> str:
    return (
        f'<DERProgramList {NS} href="/edev/1/fsa/1/derp" all="1" results="1" '
        f'pollRate="{poll_rate}">'
        '<DERProgram href="/derp/1">'
        '<DefaultDERControlLink href="/derp/1/dderc"/>'
        '<DERControlListLink href="/derp/1/derc" all="1"/>'
        "<mRID>BB00000000000000000000000000000001</mRID>"
        "<primacy>1</primacy>"
        "</DERProgram></DERProgramList>"
    )


def derc_list_xml(
    *,
    mrid: str = "CC00000000000000000000000000000001",
    start_offset: int = -5,
    duration: int = 3600,
    op_mod_max_lim_w: int = 80,
) -> str:
    start = int(time.time()) + start_offset
    return (
        f'<DERControlList {NS} href="/derp/1/derc" all="1" results="1">'
        '<DERControl href="/derp/1/derc/1" replyTo="/rsps" responseRequired="03">'
        f"<mRID>{mrid}</mRID><description>scenario control</description>"
        "<creationTime>1</creationTime>"
        "<EventStatus><currentStatus>1</currentStatus><dateTime>1</dateTime>"
        "<potentiallySuperseded>false</potentiallySuperseded></EventStatus>"
        f"<interval><duration>{duration}</duration><start>{start}</start></interval>"
        "<DERControlBase>"
        f"<opModMaxLimW>{op_mod_max_lim_w}</opModMaxLimW>"
        "</DERControlBase></DERControl></DERControlList>"
    )


def dderc_xml() -> str:
    return (
        f'<DefaultDERControl {NS} href="/derp/1/dderc">'
        "<mRID>DD00000000000000000000000000000001</mRID>"
        "<DERControlBase><opModMaxLimW>100</opModMaxLimW></DERControlBase>"
        "</DefaultDERControl>"
    )


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


@dataclass
class Recorded:
    method: str
    path: str
    body: str


@dataclass
class _Fault:
    remaining: int
    status: int
    method: str | None = None
    body: str = "scenario fault"


class ScenarioServer:
    """Serves a scripted resource map over mutual TLS, recording every request.

    ``responses`` maps a path to ``(status, content_type, body)`` or to a
    zero-argument callable returning that tuple, so time-sensitive resources
    stay fresh per request. ``fail_next`` arms a fault that consumes itself.
    """

    def __init__(self, certs: ScenarioCerts, port: int) -> None:
        self.certs = certs
        self.port = port
        self.base_url = f"https://127.0.0.1:{port}"
        self.responses: dict[str, Any] = {}
        self.log: list[Recorded] = []
        self._faults: dict[str, _Fault] = {}
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._http_runner: web.AppRunner | None = None
        self._server_cert = certs.server_a_cert
        self._server_key = certs.server_a_key

    # -- scripting ----------------------------------------------------------

    def seed_standard_tree(self, client_lfdi: str | None, *, poll_rate: int = 1) -> None:
        """The minimal tree a client walks: dcap -> tm -> edev -> fsa -> derp."""
        self.responses["/dcap"] = lambda: (200, SEP_XML, dcap_xml(poll_rate))
        self.responses["/tm"] = lambda: (200, SEP_XML, time_xml())
        self.responses["/edev"] = lambda: (200, SEP_XML, edev_list_xml(self._edev_lfdi))
        self.responses["/edev/1/fsa"] = (200, SEP_XML, fsa_list_xml())
        self.responses["/edev/1/fsa/1/derp"] = lambda: (200, SEP_XML, derp_list_xml(poll_rate))
        self.responses["/derp/1/derc"] = lambda: (200, SEP_XML, derc_list_xml())
        self.responses["/derp/1/dderc"] = (200, SEP_XML, dderc_xml())
        self._edev_lfdi = client_lfdi

    def fail_next(
        self, path: str, *, times: int = 1, status: int = 500, method: str | None = None
    ) -> None:
        """Arm a self-consuming fault; ``method`` narrows it to one verb."""
        self._faults[path] = _Fault(remaining=times, status=status, method=method)

    def requests_for(self, path: str, method: str | None = None) -> list[Recorded]:
        return [
            r
            for r in self.log
            if r.path == path and (method is None or r.method == method)
        ]

    # -- lifecycle ----------------------------------------------------------

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(self._server_cert), str(self._server_key))
        # Mutual TLS: the client must present a certificate under CA A --
        # server identity may rotate, client trust does not.
        ctx.load_verify_locations(str(self.certs.ca_a))
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner, "127.0.0.1", self.port, ssl_context=self._ssl_context()
        )
        await self._site.start()

    async def stop_listening(self) -> None:
        """Simulate the server going away.

        A full runner teardown, not a site stop: stopping only the listener
        leaves kept-alive connections answering, and a client with a pooled
        connection would never notice the outage.
        """
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def resume_listening(self) -> None:
        await self.start()

    async def rotate_server_cert(self, cert: Path, key: Path) -> None:
        """Present a different server certificate on the same port."""
        self._server_cert, self._server_key = cert, key
        await self.stop_listening()
        await self.resume_listening()

    async def start_http_redirector(self, http_port: int) -> None:
        """A plain-HTTP listener that answers every GET with a 301 to TLS."""

        async def redirect(request: web.Request) -> web.StreamResponse:
            raise web.HTTPMovedPermanently(location=f"{self.base_url}{request.path}")

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", redirect)
        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        await web.TCPSite(self._http_runner, "127.0.0.1", http_port).start()

    async def close(self) -> None:
        for runner in (self._runner, self._http_runner):
            if runner is not None:
                with contextlib.suppress(Exception):
                    await runner.cleanup()

    # -- request handling ----------------------------------------------------

    _edev_lfdi: str | None = None

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        body = await request.text()
        path = request.path
        self.log.append(Recorded(request.method, path, body))

        fault = self._faults.get(path)
        if (
            fault is not None
            and fault.remaining > 0
            and (fault.method is None or fault.method == request.method)
        ):
            fault.remaining -= 1
            return web.Response(status=fault.status, text=fault.body)

        if request.method == "POST":
            if path == "/edev":
                # In-band registration: the device now exists.
                m = re.search(r"<lFDI>([0-9A-Fa-f]+)</lFDI>", body)
                if m:
                    self._edev_lfdi = m.group(1)
                return web.Response(status=201, headers={"Location": "/edev/1"})
            if path == "/rsps":
                return web.Response(status=201, headers={"Location": "/rsps/1"})

        entry = self.responses.get(path)
        if entry is None:
            return web.Response(status=404, text=f"scenario server: {path} not scripted")
        status, content_type, payload = entry() if callable(entry) else entry
        return web.Response(status=status, content_type=content_type, text=payload)
