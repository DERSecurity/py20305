"""Poll rate normalization for IEEE 2030.5 resource polling."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MIN_POLL_RATE = 10
#: A server's rate is honored however long it is, but one above this is
#: reported: a resource polled less than daily otherwise goes quiet with
#: nothing saying why.
LONG_POLL_RATE_WARNING = 86_400
DEFAULT_POLL_RATE = 900


def normalize_poll_rate(
    raw_value: int | None,
    *,
    resource_key: str = "",
    default: int = DEFAULT_POLL_RATE,
) -> int | None:
    """Normalize a server-specified poll rate to a safe range.

    Returns:
        Clamped poll rate in seconds, or None if polling is disabled (value <= 0).
    """
    if raw_value is None:
        return default

    if raw_value <= 0:
        if resource_key:
            logger.info("Polling disabled for %s (server value: %d)", resource_key, raw_value)
        return None

    # No upper bound: a server that asks for slow polling is not polled faster.
    clamped = max(MIN_POLL_RATE, raw_value)
    if clamped != raw_value and resource_key:
        logger.warning("Poll rate for %s clamped from %d to %d", resource_key, raw_value, clamped)
    if clamped > LONG_POLL_RATE_WARNING and resource_key:
        logger.warning(
            "Poll rate for %s is %d s, over a day; honoring it, so %s refreshes only "
            "that often unless rediscovery runs sooner",
            resource_key,
            clamped,
            resource_key,
        )
    return clamped
