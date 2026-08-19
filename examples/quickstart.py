"""Connect to an IEEE 2030.5 server and drive one device.

Run it against a utility server or a test server:

    python examples/quickstart.py --url https://server.example.com:8443 \\
        --cert certs/client.pem --key certs/client.key --ca certs/ca.pem

The device it drives is the print connector, which needs no hardware: it logs
the controls it would have applied. That makes this script safe to point at a
real server before any inverter is wired up -- you see the full discovery,
event and telemetry path, and nothing moves.

To drive real hardware instead, swap PrintDemoDeviceConfig for
SunSpecDeviceConfig with your device's address:

    SunSpecDeviceConfig(lfdi=DEVICE_LFDI, host="192.168.1.50", port=502)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from pathlib import Path

from py20305.client import CsipClient, TlsConfig
from py20305.connectors.config import PrintDemoDeviceConfig
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.security import compute_lfdi


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True, help="Base URL of the IEEE 2030.5 server")
    p.add_argument("--cert", required=True, type=Path, help="Client certificate (PEM)")
    p.add_argument("--key", required=True, type=Path, help="Client private key (PEM)")
    p.add_argument("--ca", required=True, type=Path, help="CA bundle that signed the server")
    p.add_argument(
        "--insecure-skip-hostname",
        action="store_true",
        help="Skip server hostname verification. For test servers reached by IP.",
    )
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log = logging.getLogger("quickstart")

    tls = TlsConfig(
        client_cert=args.cert,
        client_key=args.key,
        ca_cert=args.ca,
        check_hostname=not args.insecure_skip_hostname,
    )

    # The server identifies this client by its certificate, so the LFDI is
    # derived rather than configured. Register this value with the utility.
    lfdi = compute_lfdi(args.cert.read_text())
    log.info("client LFDI: %s", lfdi)

    # One device, addressed by the same LFDI. The client maps one certificate
    # identity to one end device, so the device it drives is itself.
    registry = ConnectorConfigRegistry([PrintDemoDeviceConfig(lfdi=lfdi, description="demo")])

    client = CsipClient(
        args.url,
        tls=tls,
        dispatcher=ConnectorDispatcher(registry, lfdi_resolver=lambda _href: lfdi),
    )

    await client.connect()
    log.info("connected; discovered %s", client.state)

    # run() polls the server and applies events until cancelled. Ctrl-C is the
    # intended way out; shutdown() below drains in-flight work.
    try:
        await client.run()
    except asyncio.CancelledError:
        log.info("stopping")
    finally:
        await client.shutdown()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
