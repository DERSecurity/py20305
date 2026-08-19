"""Poll rate normalization for IEEE 2030.5 resource polling."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MIN_POLL_RATE = 10
MAX_POLL_RATE = 7200
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

    clamped = max(MIN_POLL_RATE, min(MAX_POLL_RATE, raw_value))
    if clamped != raw_value and resource_key:
        logger.warning("Poll rate for %s clamped from %d to %d", resource_key, raw_value, clamped)
    return clamped
