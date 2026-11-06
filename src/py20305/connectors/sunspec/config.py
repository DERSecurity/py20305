"""Configuration of a SunSpec Modbus device.

Importing this module needs only pydantic, so a configuration that names a
SunSpec device loads without the ``sunspec`` extra installed.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from py20305.connectors.config_base import _DeviceConfigBase

__all__ = ["SunSpecDeviceConfig"]


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
    power_sign: Literal["auto", "import_positive", "export_positive"] = Field(
        default="auto",
        description=(
            "Meters only: which sign of the meter's real power is import. 'auto' "
            "learns it from which of the meter's import and export energy totals "
            "rises while real power is not zero, and reports no real or reactive "
            "power until it has."
        ),
    )

    @model_validator(mode="after")
    def _power_sign_needs_a_meter(self) -> Self:
        """Reject ``power_sign`` on a device that is not a meter."""
        if "power_sign" in self.model_fields_set and self.role == "der":
            raise ValueError("power_sign applies to role site_meter or der_meter only")
        return self

    def connector_kwargs(self) -> dict[str, Any]:
        """Return the connector's keyword arguments, with the role it reads the device as."""
        return {**super().connector_kwargs(), "role": self.role}
