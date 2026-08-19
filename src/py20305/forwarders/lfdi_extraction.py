"""LFDI (Long-Form Device Identifier) extraction utilities.

A captured exchange has to be attributed to a device before it can be
published, and the identifier is not always in the same place: the transport
records it in the frame's metadata when it could resolve one, and otherwise it
has to be recovered from the message body. This module is that fallback chain,
ending at the client's own identity and then at ``"Unknown"`` rather than
raising -- a message that cannot be attributed is still worth forwarding.

The metadata it reads is populated in this process by the transport, not parsed
off the wire, so the chain describes where this client puts things rather than
a format other producers must satisfy.
"""

from __future__ import annotations

import re
from typing import Any

# Regex for LFDI-like hex strings (32-64 hex characters)
_LFDI_REGEX = re.compile(r"\b[0-9A-Fa-f]{32,64}\b")


def extract_lfdi(
    metadata: dict[str, Any],
    payload: Any,
    client_lfdi: str | None = None,
) -> str:
    """Extract LFDI from metadata, payload, or fallback sources.

    Fallback chain (in order of priority):
    1. Direct metadata fields: lfdi, LFDI, client_lfdi, client_id, clientId
    2. Nested metadata: metadata.client.lfdi
    3. Payload fields: lfdi, LFDI, mrid, mRID
    4. Regex match for hex strings in payload
    5. This client's own LFDI
    6. "Unknown" default

    Args:
        metadata: Frame metadata dict (may contain LFDI, client info, etc.)
        payload: Message content (may be Pydantic model, dict, or other)
        client_lfdi: This client's own LFDI, used when the message names no device

    Returns:
        Extracted LFDI string, or "Unknown" if not found
    """
    # 1. Check direct metadata fields
    direct_keys = ["lfdi", "LFDI", "client_lfdi", "client_id", "clientId"]
    for key in direct_keys:
        value = _normalize_identifier(metadata.get(key))
        if value:
            return value

    # 2. Check nested metadata structures
    client_meta = metadata.get("client")
    if isinstance(client_meta, dict):
        value = _normalize_identifier(client_meta.get("lfdi"))
        if value:
            return value

    # 3 & 4. Search payload
    payload_lfdi = _extract_from_payload(payload)
    if payload_lfdi:
        return payload_lfdi

    # 5. Client LFDI fallback
    if client_lfdi:
        value = _normalize_identifier(client_lfdi)
        if value:
            return value

    # 6. Default
    return "Unknown"


def extract_client_id(
    metadata: dict[str, Any],
    payload: Any,
    client_lfdi: str | None = None,
) -> str:
    """Extract client identifier, preferring LFDI.

    Similar to extract_lfdi but used for the client_id field in ProtocolMessage.

    Args:
        metadata: Frame metadata dict
        payload: Message content
        client_lfdi: Fallback LFDI for this client's own identity

    Returns:
        Client identifier string, or "Unknown" if not found
    """
    # Try direct metadata fields first
    candidates = [
        metadata.get("lfdi"),
        metadata.get("client_id"),
        metadata.get("clientId"),
    ]
    for candidate in candidates:
        value = _normalize_identifier(candidate)
        if value:
            return value

    # Search payload
    payload_lfdi = _extract_from_payload(payload)
    if payload_lfdi:
        return payload_lfdi

    # Client LFDI fallback
    if client_lfdi:
        value = _normalize_identifier(client_lfdi)
        if value:
            return value

    return "Unknown"


def _extract_from_payload(payload: Any) -> str | None:
    """Recursively search payload for LFDI-like identifiers.

    Args:
        payload: Message content to search

    Returns:
        First LFDI found, or None
    """
    if payload is None:
        return None

    # Handle Pydantic v2 models
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()
    # Handle Pydantic v1 models
    elif hasattr(payload, "dict") and callable(payload.dict):
        payload = payload.dict()

    if isinstance(payload, dict):
        # Check known LFDI field names first
        lfdi_keys = ["lfdi", "LFDI", "mrid", "mRID", "client_id", "clientId"]
        for key in lfdi_keys:
            if key in payload:
                value = _normalize_identifier(payload[key])
                if value:
                    return value

        # Recursively search nested values
        for value in payload.values():
            result = _extract_from_payload(value)
            if result:
                return result

    elif isinstance(payload, list | tuple):
        for item in payload:
            result = _extract_from_payload(item)
            if result:
                return result

    elif isinstance(payload, str):
        # Try regex match for hex strings
        match = _LFDI_REGEX.search(payload)
        if match:
            return match.group(0)

    elif isinstance(payload, bytes):
        # Convert bytes to hex and check if it looks like LFDI
        hex_str = payload.hex()
        if 32 <= len(hex_str) <= 64:
            return hex_str

    return None


def _normalize_identifier(value: Any) -> str | None:
    """Normalize a potential identifier value.

    Args:
        value: Raw value to normalize

    Returns:
        Normalized string, or None if invalid/empty
    """
    if value is None:
        return None

    # Handle bytes
    if isinstance(value, bytes):
        value = value.hex()

    candidate = str(value).strip()

    # Reject empty or "unknown" values
    if not candidate or candidate.lower() == "unknown":
        return None

    return candidate
