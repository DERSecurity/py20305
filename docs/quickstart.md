# Quickstart

The goal here is to be talking to a real IEEE 2030.5 server in a few minutes,
before any hardware is involved.

## Install

```bash
pip install py20305
```

The base install is the protocol client and nothing else. Extras add the parts
you may not need:

| Extra | Adds | For |
|---|---|---|
| `sunspec` | `pysunspec2`, `pyserial` | Talking to a SunSpec Modbus device |
| `api` | `fastapi`, `uvicorn` | The HTTP management API |
| `mqtt` | `aiomqtt` | Forwarding captured traffic to a monitoring system |
| `all` | all of the above | |

```bash
pip install "py20305[all]"
```

## You will need certificates

IEEE 2030.5 is mutual TLS throughout, and the server identifies your client by
its certificate — not by anything you configure. Three files:

- a client certificate and its private key, issued by a CA the server trusts
- the CA bundle that signed the *server*, so your client can verify it

Your LFDI, the 40-hex identifier the utility registers, is derived from the
client certificate:

```python
from pathlib import Path
from py20305.security import compute_lfdi

print(compute_lfdi(Path("certs/client.pem").read_text()))
```

Give that value to whoever operates the server. Nothing will work until they
have registered it.

## Run it

The [`examples/quickstart.py`](https://github.com/DERSecurity/py20305/blob/main/examples/quickstart.py)
script connects, discovers, and runs the event loop against a device that needs
no hardware — the print connector logs the controls it would have applied.

```bash
python examples/quickstart.py \
  --url https://server.example.com:8443 \
  --cert certs/client.pem \
  --key certs/client.key \
  --ca certs/ca.pem
```

You should see the derived LFDI, then the discovered resources, then polling.
When the server issues a DERControl, the print connector logs it.

!!! tip "Test servers reached by IP"
    Add `--insecure-skip-hostname` when the server has no DNS name matching its
    certificate. Don't use it against production.

## The same thing, in your own code

```python
import asyncio
from pathlib import Path

from py20305.client import CsipClient, TlsConfig
from py20305.connectors.config import PrintDemoDeviceConfig
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.security import compute_lfdi

async def main() -> None:
    cert = Path("certs/client.pem")
    lfdi = compute_lfdi(cert.read_text())

    tls = TlsConfig(
        client_cert=cert,
        client_key=Path("certs/client.key"),
        ca_cert=Path("certs/ca.pem"),
    )
    registry = ConnectorConfigRegistry([PrintDemoDeviceConfig(lfdi=lfdi)])

    client = CsipClient(
        "https://server.example.com:8443",
        tls=tls,
        dispatcher=ConnectorDispatcher(registry, lfdi_resolver=lambda _href: lfdi),
    )

    await client.connect()
    try:
        await client.run()
    finally:
        await client.shutdown()

asyncio.run(main())
```

`connect()` performs discovery and registration. `run()` polls and applies
events until cancelled. `shutdown()` drains in-flight work.

## Point it at real hardware

Swap the device configuration. Everything else is unchanged:

```python
from py20305.connectors.config import SunSpecDeviceConfig

registry = ConnectorConfigRegistry([
    SunSpecDeviceConfig(lfdi=lfdi, host="192.168.1.50", port=502, unit_id=1),
])
```

See [Connecting devices](connectors.md) for serial, TLS-wrapped Modbus, and
writing a connector of your own.

## Next steps

- [Connecting devices](connectors.md) — connectors, and how to write one
- [Management API](api.md) — observing a running client over HTTP
- [Forwarding traffic](forwarding.md) — publishing exchanges to a monitor
