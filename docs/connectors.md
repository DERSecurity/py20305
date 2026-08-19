# Connecting devices

A **connector** is the thing that turns an IEEE 2030.5 control into whatever
your device actually speaks. The client resolves a device's LFDI to its
connector, then calls methods on it.

## SunSpec Modbus

The connector that ships with the package. It speaks the SunSpec 700-series
models over three transports:

```python
from py20305.connectors.config import SunSpecDeviceConfig

# Modbus TCP
SunSpecDeviceConfig(lfdi=LFDI, host="192.168.1.50", port=502, unit_id=1)

# Modbus RTU over serial
SunSpecDeviceConfig(lfdi=LFDI, transport="rtu", serial_port="/dev/ttyUSB0", baudrate=9600)

# Modbus TCP wrapped in TLS
SunSpecDeviceConfig(
    lfdi=LFDI, transport="tcp+tls", host="192.168.1.50", port=802,
    ca_path="certs/ca.pem", cert_path="certs/client.pem", key_path="certs/client.key",
)
```

Install it with the `sunspec` extra.

## The print connector

Logs what it would have done instead of doing it. Needs no hardware, which
makes it the right thing to point at when bringing up a server connection for
the first time.

```python
from py20305.connectors.config import PrintDemoDeviceConfig

PrintDemoDeviceConfig(lfdi=LFDI, description="bench test")
```

## Writing your own

Subclass `BaseConnector` and override only the modes your device supports.
Everything you don't override is a silent no-op, which matches the physical
reality that not every device does every IEEE 2030.5 mode.

```python
from typing import Any
from py20305.connectors.base import BaseConnector, ConnectorPayload

class MyInverter(BaseConnector):
    connector_name = "MyInverter"
    der_type = 83  # combined PV + storage

    async def fetch_monitoring(self) -> dict[str, Any]:
        return {"W": await self._read_power(), "Hz": await self._read_freq()}

    async def update_p_lim_w(self, params: ConnectorPayload) -> None:
        await self._write_limit(params["value"])
```

Point a device at it by import path — no registration needed:

```python
from py20305.connectors.config import CustomDeviceConfig

CustomDeviceConfig(
    lfdi=LFDI,
    class_path="myproject.connectors.MyInverter",
    init_kwargs={"address": "192.168.1.50"},
)
```

## Connectors that need shared state

`class_path` constructs your connector with plain keyword arguments. When that
is not enough — a connection pool shared across devices, a client for a
transport this package knows nothing about, a configuration model of your own —
supply a `factory_resolver` instead.

It is consulted for every device before the built-in resolution runs. Return a
factory to claim the device, or `None` to let the package handle it:

```python
from py20305.connectors.registry import ConnectorConfigRegistry

pool = MyConnectionPool()

def resolve(device):
    if getattr(device, "type", None) == "my-transport":
        return lambda: MyConnector(pool, device.lfdi)
    return None

registry = ConnectorConfigRegistry(devices, factory_resolver=resolve)
```

The factory is invoked lazily, exactly like a bundled connector's — so it may
close over expensive state without constructing it up front, and it inherits
the same permanent-failure caching and single-flight resolution.

## Errors

Two vocabularies, and the distinction matters because IEEE 2030.5 makes the
head-end care about it:

- `py20305.connectors.errors` — the transport broke.
  `ConnectorConnectionError` (with a `permanent` flag), `ConnectorTimeoutError`.
- `py20305.connectors.control_errors` — the transport was fine and
  the control still did not happen. The device is offline, the mode is
  unsupported, the value is out of range, or the site declined.

Each maps to a specific IEEE 2030.5 Table 31 response status. Raise the one
that describes what actually happened and the right code reaches the server.
