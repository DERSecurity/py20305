"""Connector system for DER device communication.

Provides the BaseConnector interface, control mode translation, the
connector registry and the dispatcher. Connectors that drive a device's own
protocol belong to the application; the package ships only the print connector.
"""

from py20305.connectors.base import (
    BaseConnector,
    ConnectorConnectionError,
    ConnectorError,
    ConnectorPayload,
    ConnectorTimeoutError,
    ConnectorValueError,
    ConnectorWriteError,
)
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.connectors.registry import (
    ConnectorConfigRegistry,
    ConnectorRegistryError,
    LazyConnectorProxy,
)

__all__ = [
    "BaseConnector",
    "ConnectorConfigRegistry",
    "ConnectorConnectionError",
    "ConnectorDispatcher",
    "ConnectorError",
    "ConnectorPayload",
    "ConnectorRegistryError",
    "ConnectorTimeoutError",
    "ConnectorValueError",
    "ConnectorWriteError",
    "LazyConnectorProxy",
]
