"""Fixtures for the end-to-end tests.

These tests need a real IEEE 2030.5 server. They are skipped unless one is
configured, so the unit suite is unaffected by their absence:

- ``PY20305_E2E_SERVER_URL`` -- base URL of the server
- ``PY20305_E2E_CERT_DIR`` -- directory holding ``testca.crt`` plus a client
  certificate and key

``scripts/e2e_server.py up`` brings up the server CI uses and prints both.

Nothing here is specific to that server. The variables exist so the same tests
can be pointed at a different implementation, or at a utility's own server
during an interop exercise -- which is the situation these tests are most
valuable in, and the one a vendored fake cannot reproduce.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

from py20305.client import CsipClient, TlsConfig
from py20305.connectors.config import PrintDemoDeviceConfig
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.security import compute_lfdi

_SKIP_REASON = (
    "no IEEE 2030.5 server configured; set PY20305_E2E_SERVER_URL and "
    "PY20305_E2E_CERT_DIR, or run `python scripts/e2e_server.py up`"
)


@pytest.fixture(scope="session")
def server_url() -> str:
    url = os.environ.get("PY20305_E2E_SERVER_URL")
    if not url:
        pytest.skip(_SKIP_REASON)
    return url


@pytest.fixture(scope="session")
def cert_dir() -> Path:
    raw = os.environ.get("PY20305_E2E_CERT_DIR")
    if not raw:
        pytest.skip(_SKIP_REASON)
    path = Path(raw)
    if not path.is_dir():
        pytest.skip(f"PY20305_E2E_CERT_DIR does not exist: {path}")
    return path


@pytest.fixture(scope="session")
def client_cert(cert_dir: Path) -> Path:
    """The client certificate to present.

    Defaults to the demo server's ``testdevice1``; override with
    ``PY20305_E2E_CLIENT_CERT`` (a stem, resolved against the certificate
    directory) when pointing at a different server.
    """
    stem = os.environ.get("PY20305_E2E_CLIENT_CERT", "testdevice1")
    cert = cert_dir / f"{stem}.crt"
    if not cert.is_file():
        pytest.skip(f"client certificate not found: {cert}")
    return cert


@pytest.fixture(scope="session")
def tls(cert_dir: Path, client_cert: Path) -> TlsConfig:
    return TlsConfig(
        client_cert=client_cert,
        client_key=client_cert.with_suffix(".key"),
        ca_cert=cert_dir / os.environ.get("PY20305_E2E_CA", "testca.crt"),
        # On, and left on against the local server too: its certificate
        # carries SANs for localhost and 127.0.0.1, so there is no reason to
        # weaken this for convenience. The escape hatch exists for a server
        # reached by an address its certificate does not name -- turning it
        # off accepts a certificate issued for any other host that chains to
        # the same CA, so it should be a deliberate act.
        check_hostname=os.environ.get("PY20305_E2E_CHECK_HOSTNAME", "1") == "1",
    )


@pytest.fixture(scope="session")
def client_lfdi(client_cert: Path) -> str:
    """The LFDI the server will know this client by, derived from its certificate."""
    return compute_lfdi(client_cert.read_text())


@pytest.fixture
def other_device_lfdi() -> str:
    """A well-formed LFDI belonging to some device other than this client.

    An IEEE 2030.5 LFDI is 40 hex characters; a UUID's hex is only 32, so it
    has to be padded rather than sliced. The distinction matters for the test
    that uses this: a malformed identifier could be refused for being
    malformed, which would prove nothing about whether the server enforces
    that a certificate may only register its own identity.
    """
    return (uuid.uuid4().hex + uuid.uuid4().hex)[:40]


@pytest_asyncio.fixture
async def connected_client(
    server_url: str, tls: TlsConfig, client_lfdi: str
) -> AsyncIterator[CsipClient]:
    """A client connected to the server, driving a connector that needs no hardware."""
    registry = ConnectorConfigRegistry([PrintDemoDeviceConfig(lfdi=client_lfdi)])
    client = CsipClient(
        server_url,
        tls=tls,
        dispatcher=ConnectorDispatcher(registry, lfdi_resolver=lambda _href: client_lfdi),
    )
    await client.connect()
    try:
        yield client
    finally:
        await client.shutdown()


@pytest_asyncio.fixture
async def registered_client(connected_client: CsipClient, client_lfdi: str) -> CsipClient:
    """A client whose EndDevice exists on the server, registering it if needed.

    Deliberately idempotent. A server keeps its devices, so the second run of
    this suite against the same instance would otherwise fail on a duplicate --
    and "register only if the server does not already know me" is what a client
    does on every restart anyway. Registering unconditionally would be the
    unrealistic choice, not the strict one.
    """
    if not _knows(connected_client, client_lfdi):
        await connected_client.register_end_device(lfdi=client_lfdi, device_category=0)
    await connected_client.trigger_rediscovery()
    return connected_client


def _knows(client: CsipClient, lfdi: str) -> bool:
    """Whether the server has already told us about this LFDI."""
    return lfdi.lower() in {
        (ed.lfdi.hex() if isinstance(ed.lfdi, bytes) else str(ed.lfdi)).lower()
        for ed in client.state.end_devices.values()
    }


@pytest.fixture(scope="session", autouse=True)
def _announce(server_url: str) -> Iterator[None]:
    print(f"\nend-to-end tests running against {server_url}")
    yield
