"""Errors a connector raises when it will not perform a control.

Distinct from :mod:`py20305.connectors.errors`, which covers a
transport that broke. These say the transport was fine and the control still
did not happen -- the device is offline, the mode is unsupported, the value is
out of range, or the site declined on the customer's behalf.

The distinction is load-bearing because IEEE 2030.5 makes the head-end care
about it. Each type below carries the retry behavior the caller should apply
and the Table 31 response status the head-end should see; see
:func:`py20305.events.response.response_code_for_dispatch_error`
for the mapping.
"""

from __future__ import annotations


class ControlError(Exception):
    """Base for a control a connector declined to perform."""


class DeviceOfflineTransientError(ControlError):
    """The device is unreachable, but may return on its own.

    Caller logs and retries on the next polling cycle.
    """


class DeviceOfflinePermanentError(ControlError):
    """The device is unreachable and will stay that way without intervention.

    Caller suppresses further attempts for this device rather than retrying
    every cycle.
    """


class DeviceNotConfiguredError(ControlError):
    """The connector has no configuration for the addressed device.

    Caller re-sends its device configuration once and retries.
    """


class ModeNotSupportedError(ControlError):
    """The device cannot perform the requested control mode.

    Permanent for this device and mode. Callers that pre-filter by a device's
    advertised modes should never see this; if one does, the advertised set and
    the device disagree.
    """


class OptOutError(ControlError):
    """The control was declined on the customer's behalf.

    Reports a decision, not a failure, and is surfaced to the head-end as
    IEEE 2030.5 Table 31 status 4 rather than a capability-limit code.
    """


class InvalidControlValueError(ControlError):
    """A control parameter is outside the range the IEEE 2030.5 profile allows.

    Like :class:`OptOutError` this is not a device fault, but unlike it the
    event is not performed: surfaced to the head-end as Table 31 status 253
    (Invalid) rather than a capability-limit code.

    Callers must not latch this against the device and mode. A corrected value
    arriving on the next event has to be allowed through -- the value is at
    fault, not the pairing of device and mode.
    """


__all__ = [
    "ControlError",
    "DeviceNotConfiguredError",
    "DeviceOfflinePermanentError",
    "DeviceOfflineTransientError",
    "InvalidControlValueError",
    "ModeNotSupportedError",
    "OptOutError",
]
