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

from typing import Annotated, Any, Literal

from pydantic import Field

from py20305.connectors.config_base import _DeviceConfigBase
from py20305.connectors.sunspec.config import SunSpecDeviceConfig


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
