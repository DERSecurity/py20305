"""Errors a connector raises when talking to a device.

Transport-neutral: a connector speaking Modbus, MQTT, SQL or anything else
raises these same types, so the code above it -- the dispatcher, the event
engine, the API -- reasons about failure without knowing the protocol
underneath. :mod:`py20305.connectors.control_errors` covers the
other half: a control the device declined rather than a transport that broke.
"""

from __future__ import annotations


class ConnectorError(Exception):
    """Base exception for connector operations."""


class ConnectorConnectionError(ConnectorError):
    """Raised when the connector cannot reach the target device.

    The optional ``permanent`` flag signals that the failure won't change
    on retry without operator intervention -- e.g. Modbus protocol
    exception codes 1-4 (illegal function/address/value, server failure),
    which typically mean the configured device is missing models the
    connector was told to scan for.
    """

    def __init__(self, *args: object, permanent: bool = False) -> None:
        super().__init__(*args)
        self.permanent = permanent


class ConnectorTimeoutError(ConnectorError):
    """Raised when a connector operation exceeds its deadline."""


class ConnectorWriteError(ConnectorError):
    """Raised when a control write fails or is rejected by the device."""


class ConnectorValueError(ConnectorError):
    """Raised when a control parameter is outside the range the profile allows.

    Distinct from :class:`ConnectorWriteError`: nothing was attempted. The
    parameter itself is invalid, so no device could have accepted it and a retry
    with the same value cannot succeed. The register is left untouched, including
    any mode-enable point -- a lever must not be raised for a write that will not
    happen.

    Reported to the head-end as IEEE 2030.5 Table 31 status 253 (Invalid),
    because the fault is in the event's data rather than in what the device can
    do. Adapters translate accordingly at their own boundary.
    """
