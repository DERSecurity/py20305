"""In-memory Live Traffic recorder for the management API.

Captures the client's HTTP conversation with the upstream IEEE 2030.5 server
-- outbound requests it sends and the responses it gets back -- plus inbound
notifications POSTed to its ``/notify`` endpoint, so the web UI's Live Traffic
view (``GET /api/v1/live-traffic``) can show full payloads for troubleshooting.

A bounded, thread-safe ring buffer: always-on, evicts oldest past the cap, and
truncates each body at a per-entry limit so a large resource page can't bloat
memory, so a running client's conversation can be inspected live.
"""

from __future__ import annotations

import datetime
import threading
from collections import deque
from typing import Any

LIVE_TRAFFIC_MAX_ENTRIES = 500
LIVE_TRAFFIC_PREVIEW_LENGTH = 200
#: Per-entry body cap (~64 KiB). Full enough to see a resource page; bounded so
#: the ring buffer's footprint stays predictable.
LIVE_TRAFFIC_BODY_LIMIT = 65_536


class TrafficRecorder:
    """Thread-safe ring buffer of recent HTTP exchanges for the Live Traffic UI."""

    def __init__(self, max_entries: int = LIVE_TRAFFIC_MAX_ENTRIES) -> None:
        self._lock = threading.Lock()
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._total = 0

    def _append(
        self,
        *,
        direction: str,
        method: str,
        url: str,
        status: int | None,
        body: object,
        error: str | None = None,
    ) -> None:
        payload = _prepare_payload(body)
        entry = {
            "timestamp": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "direction": direction,
            "method": (method or "").upper(),
            "url": url or "",
            "status": status,
            "error": error,
            "preview": payload["preview"],
            "body": payload["full"],
            "truncated": payload["truncated"],
        }
        with self._lock:
            self._entries.append(entry)
            self._total += 1

    def record_request(self, *, method: str, url: str, body: object = None) -> None:
        """An outbound request the client sent to the upstream server.

        Used for writes (POST/PUT) where the request body is the payload of
        interest. GETs are recorded via :meth:`record_response`.
        """
        self._append(direction="request", method=method, url=url, status=None, body=body)

    def record_response(
        self,
        *,
        method: str,
        url: str,
        status: int | None,
        body: object = None,
        error: str | None = None,
    ) -> None:
        """A response the client received from the upstream server."""
        self._append(
            direction="response", method=method, url=url, status=status, body=body, error=error
        )

    def record_notification(
        self,
        *,
        path: str,
        body: object,
        source_ip: str | None = None,
        status: int | None = 201,
    ) -> None:
        """An inbound notification POSTed to the client's ``/notify`` endpoint."""
        url = f"{source_ip} {path}" if source_ip else path
        self._append(direction="notification", method="POST", url=url, status=status, body=body)

    def get_snapshot(self, limit: int = 200) -> dict[str, Any]:
        """Return the most recent entries (newest first) plus counters."""
        limit = max(1, min(limit or 1, LIVE_TRAFFIC_MAX_ENTRIES))
        with self._lock:
            entries = [dict(e) for e in list(self._entries)[-limit:]]
            total = self._total
            buffered = len(self._entries)
        entries.reverse()  # newest first
        return {"entries": entries, "total": total, "buffered": buffered, "returned": len(entries)}


def _prepare_payload(payload: object) -> dict[str, Any]:
    """Normalize a body to {preview, full, truncated} for display."""
    if payload in (None, b"", ""):
        return {"preview": "", "full": "", "truncated": False}
    if isinstance(payload, bytes):
        # Decode only up to the cap so a multi-MB response doesn't allocate a huge
        # intermediate string in the always-on recorder; judge truncation on the
        # original byte length. errors="replace" tolerates a split multibyte char
        # at the cap boundary.
        truncated = len(payload) > LIVE_TRAFFIC_BODY_LIMIT
        full = payload[:LIVE_TRAFFIC_BODY_LIMIT].decode("utf-8", errors="replace")
    else:
        text = str(payload)
        truncated = len(text) > LIVE_TRAFFIC_BODY_LIMIT
        full = text[:LIVE_TRAFFIC_BODY_LIMIT]
    # Preserve the raw body (incl. leading/trailing whitespace) so the view
    # matches the bytes on the wire; only use strip() for the empty check.
    if not full.strip():
        return {"preview": "", "full": "", "truncated": False}
    return {
        "preview": full[:LIVE_TRAFFIC_PREVIEW_LENGTH],
        "full": full,
        "truncated": truncated,
    }
