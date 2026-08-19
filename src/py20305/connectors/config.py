"""Typed configuration for the devices a connector talks to.

One model per connector type, discriminated on ``type``, so a configuration
file naming a connector gets that connector's fields validated and nothing
else. :data:`DeviceConfig` is the union to annotate with;
:class:`~py20305.connectors.registry.ConnectorConfigRegistry`
consumes it.

To add a connector type, subclass :class:`_DeviceConfigBase` with a literal
``type`` and add it to the union below and to the registry's class map.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: An IEEE 2030.5 LFDI is a 160-bit value, written here as 40 hex characters.
_LFDI_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class _DeviceConfigBase(BaseModel):
    """Fields every device configuration carries, whatever its connector.

    Unknown keys are rejected rather than ignored: a misspelled setting that
    validates and then does nothing is the hardest kind of configuration
    mistake to find, because nothing reports it.
    """

    model_config = ConfigDict(extra="forbid")

    lfdi: str = Field(description="Device LFDI (40 hex characters)")
    description: str | None = None
    pin: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Expected IEEE 2030.5 registration PIN for this device. When set, "
            "the client verifies it against the EndDevice's Registration "
            "resource on the server during discovery and logs a warning on "
            "mismatch. Omit to skip verification."
        ),
    )

    @field_validator("lfdi")
    @classmethod
    def _validate_lfdi(cls, v: str) -> str:
        """Reject a malformed LFDI at config load rather than at first poll.

        Without this, a typo -- an extra character, a stray quote, a
        non-hex digit -- loads fine and only fails later inside
        ``bytes.fromhex(lfdi)``, so the operator sees a
        ``non-hexadecimal number found`` warning every poll cycle with
        nothing pointing at the cause. Failing at load with a
        length-aware message is the actionable shape.

        The value is lowercased on return so downstream lookups have one
        normalization to rely on; certificate tools differ on hex case.
        """
        if not _LFDI_RE.fullmatch(v):
            # Tease apart the common failure modes for a useful message:
            # too short, too long, contains non-hex.
            if len(v) != 40:
                detail = f"length is {len(v)}, must be 40"
            else:
                bad = next((c for c in v if c not in "0123456789abcdefABCDEF"), "")
                detail = f"contains non-hex character {bad!r}" if bad else "not 40 hex chars"
            raise ValueError(f"lfdi must be 40 hex characters ({detail}). Got {v[:8]}...")
        return v.lower()


class SunSpecDeviceConfig(_DeviceConfigBase):
    """SunSpec Modbus device configuration.

    Covers the three transports the connector speaks: plain Modbus TCP,
    Modbus RTU over a serial port, and Modbus TCP wrapped in TLS. Fields for
    the transports not in use are ignored, so one shape covers all three.
    """

    type: Literal["sunspec"] = "sunspec"
    transport: Literal["tcp", "rtu", "tcp+tls"] = "tcp"
    host: str = "127.0.0.1"
    port: int = 8502
    serial_port: str = "/dev/ttyUSB0"
    baudrate: int = 9600
    parity: str = "N"
    ca_path: str | None = None
    cert_path: str | None = None
    key_path: str | None = None
    insecure: bool = False
    unit_id: int = 1
    timeout: int = 5
    scan_retries: int = 3
    scan_retry_delay: float = 2.0
    der_type: int = 83


class PrintDemoDeviceConfig(_DeviceConfigBase):
    """A device that logs what it would have done instead of doing it.

    Needs no hardware, which makes it the connector to point at when trying
    the client against a utility server for the first time.
    """

    type: Literal["print_demo"] = "print_demo"


class CustomDeviceConfig(_DeviceConfigBase):
    """A connector supplied by import path.

    The escape hatch for a device this package has no connector for: point
    ``class_path`` at your own
    :class:`~py20305.connectors.base.BaseConnector` subclass and it
    is constructed with ``init_kwargs``.
    """

    type: Literal["custom"] = "custom"
    class_path: str = Field(description="Full Python import path for connector class")
    init_kwargs: dict[str, Any] = Field(default_factory=dict)


DeviceConfig = Annotated[
    SunSpecDeviceConfig | PrintDemoDeviceConfig | CustomDeviceConfig,
    Field(discriminator="type"),
]

__all__ = [
    "CustomDeviceConfig",
    "DeviceConfig",
    "PrintDemoDeviceConfig",
    "SunSpecDeviceConfig",
]
