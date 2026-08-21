"""Management API routes.

A router factory returning an ``APIRouter`` with the management endpoints,
which need only a :class:`~py20305.client.csip_client.CsipClient`
and a :class:`~py20305.telemetry.manager.TelemetryManager` behind
them.

:func:`~py20305.api.app.create_app` includes this router, but it
is exported separately on purpose: an application embedding this client and
running its own FastAPI app can ``include_router`` it directly, mounting the
client's endpoints wherever it wants them alongside its own.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from py20305.api.service import ClientAPIService, unavailable_time_body

logger = logging.getLogger(__name__)


def create_client_router(
    service_getter: Callable[[], ClientAPIService | None],
) -> APIRouter:
    """Create a router with client-level management API endpoints.

    Args:
        service_getter: Callable that returns the current ``ClientAPIService``
            (or subclass).  May return ``None`` when the client is not yet
            connected — routes handle this gracefully.

    Returns:
        A FastAPI ``APIRouter`` ready to be included via ``include_router``.
    """
    router = APIRouter()

    # -----------------------------------------------------------------
    # Status and State
    # -----------------------------------------------------------------

    @router.get("/status")
    async def get_status(request: Request) -> dict[str, Any]:
        """Return current client status.

        Includes an upstream ``connection`` block (phase / detail / attempts /
        retry_in_seconds) when the client is running, so the dashboard can
        show a "disconnected, retrying" banner while the management API stays
        up during a connection failure.
        """
        service = service_getter()
        status: dict[str, Any] = (
            {"status": "not_connected", "server_alive": False}
            if service is None
            else service.get_status()
        )
        conn = getattr(request.app.state, "connection", None)
        if conn is not None:
            status["connection"] = conn.to_dict()
        return status

    # -----------------------------------------------------------------
    # Time
    # -----------------------------------------------------------------

    @router.get(
        "/time",
        response_model=None,
        summary="Head-end time, corrected for local clock drift",
        responses={
            200: {"description": "A Time resource has been observed; the reading is server-true."},
            503: {"description": "No Time resource observed yet; no server-true reading exists."},
        },
    )
    async def get_time(
        request: Request,
        fmt: str | None = Query(
            default=None,
            alias="format",
            description=(
                "Set to 'text' for a bare epoch-seconds integer instead of JSON. "
                "Equivalent to sending 'Accept: text/plain'."
            ),
        ),
    ) -> Response:
        """Return the current time as reported by the head-end.

        Answers "what time is it really" for a client whose own clock cannot be
        trusted -- the ordinary case for a field device on a network where the
        head-end is the only reachable host and NTP is not available.

        The ``format=text`` variant returns the epoch seconds and nothing else,
        with no JSON to walk, because the consumers that need this most are
        often the ones least equipped to parse a document.

        Availability is carried by the status code as well as the body, so a
        consumer that checks only the code cannot mistake an unsynchronized
        reading for a synchronized one: 503 means no Time resource has been
        observed, and no bare integer is emitted on that path.
        """
        wants_text = fmt == "text" or "text/plain" in request.headers.get("accept", "")

        service = service_getter()
        payload = unavailable_time_body() if service is None else service.get_time()
        available = payload.get("source") == "server"
        status_code = 200 if available else 503

        if wants_text:
            body = str(payload["current_time"]) if available else "unavailable"
            return PlainTextResponse(body, status_code=status_code)
        return JSONResponse(payload, status_code=status_code)

    # -----------------------------------------------------------------
    # Devices
    # -----------------------------------------------------------------

    @router.get("/devices")
    async def get_devices() -> dict[str, Any]:
        """Return list of discovered devices."""
        service = service_getter()
        if service is None:
            return {"devices": []}
        return service.get_devices()

    # -----------------------------------------------------------------
    # Controls
    # -----------------------------------------------------------------

    @router.get("/derc_controls")
    async def get_derc_controls() -> dict[str, Any]:
        """Return DER control information."""
        service = service_getter()
        if service is None:
            return {
                "derc_controls": {
                    "dderc_dict": {},
                    "default_controls": {},
                    "active_events": {},
                    "scheduled_events": {},
                }
            }
        return service.get_derc_controls()

    @router.get("/events")
    async def get_events() -> dict[str, Any]:
        """Return DER control events."""
        service = service_getter()
        if service is None:
            return {
                "events": {
                    "active": {},
                    "scheduled": {},
                    "completed": {},
                    "cancelled": {},
                    "superseded": {},
                }
            }
        return service.get_events()

    @router.get("/responses")
    async def get_responses() -> dict[str, Any]:
        """Return all posted DER responses, grouped by mRID."""
        service = service_getter()
        if service is None:
            return {"responses": {}}
        return service.get_responses()

    @router.get("/tariffs")
    async def get_tariffs() -> dict[str, Any]:
        """Return discovered Pricing tariff profiles with per-interval prices."""
        service = service_getter()
        if service is None:
            return {"profiles": []}
        return await service.get_tariffs()

    @router.get("/programs")
    async def get_programs() -> dict[str, Any]:
        """Return DER programs."""
        service = service_getter()
        if service is None:
            return {"programs": []}
        return service.get_der_programs()

    # -----------------------------------------------------------------
    # Log Events
    # -----------------------------------------------------------------

    @router.get("/logevents")
    async def get_log_events() -> dict[str, Any]:
        """Return log events."""
        service = service_getter()
        if service is None:
            return {"events": [], "enabled": False}
        return service.get_log_events()

    @router.post("/logevents")
    async def trigger_log_event(request: Request) -> dict[str, Any]:
        """Trigger a LogEvent POST to the IEEE 2030.5 server."""
        service = service_getter()
        if service is None:
            return {"status": "error", "detail": "not_connected"}
        body = (
            await request.json()
            if request.headers.get("content-type") == "application/json"
            else {}
        )
        alarm_status = body.get("alarm_status", 1) if isinstance(body, dict) else 1
        details = body.get("details") if isinstance(body, dict) else None
        return await service.trigger_log_event(alarm_status=alarm_status, details=details)

    # -----------------------------------------------------------------
    # HTTP Message Logging / Redirect Probe
    # -----------------------------------------------------------------

    @router.get("/httpmsg")
    async def get_http_messages() -> dict[str, Any]:
        """Return HTTP message logging state."""
        service = service_getter()
        if service is None:
            return {"enabled": False}
        return service.get_http_msg()

    @router.post("/httpmsg")
    async def set_http_messages(request: Request) -> dict[str, Any]:
        """Enable/disable HTTP message logging. Runs redirect probe when enabling."""
        service = service_getter()
        if service is None:
            return {"status": "error", "detail": "not_connected"}
        body = await request.json()
        enabled = int(body.get("enabled", 0)) if isinstance(body, dict) else 0
        return await service.set_http_msg(enabled)

    # -----------------------------------------------------------------
    # Certificates
    # -----------------------------------------------------------------

    @router.get("/certtype")
    async def get_cert_type() -> dict[str, Any]:
        """Return certificate type information."""
        service = service_getter()
        if service is None:
            return {"cert_type": "client", "lfdi": None}
        return service.get_cert_type()

    @router.post("/certtype")
    async def set_cert_type(request: Request) -> dict[str, Any]:
        """Set certificate type for COMM-004 testing."""
        service = service_getter()
        if service is None:
            return {"status": "error", "detail": "not_connected"}
        body = (
            await request.json()
            if request.headers.get("content-type") == "application/json"
            else {}
        )
        cert_path = body.get("cert_path", "") if isinstance(body, dict) else ""
        key_path = body.get("key_path", "") if isinstance(body, dict) else ""
        if not cert_path or not key_path:
            return {"status": "ok"}
        return await service.set_cert_type(cert_path, key_path)

    # -----------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------

    @router.get("/diagnostics/messages")
    async def get_diagnostic_messages(request: Request) -> dict[str, Any]:
        """Return diagnostic messages from the in-memory store."""
        from py20305.diagnostics import DiagnosticsStore

        store: DiagnosticsStore | None = getattr(request.app.state, "diagnostics", None)
        if store is None:
            return {"errors": [], "warnings": [], "info": []}
        return store.snapshot()

    @router.delete("/diagnostics/messages")
    async def clear_diagnostic_messages(request: Request) -> dict[str, Any]:
        """Clear all diagnostic messages in the store."""
        from py20305.diagnostics import DiagnosticsStore

        store: DiagnosticsStore | None = getattr(request.app.state, "diagnostics", None)
        if store is not None:
            store.clear()
        return {"status": "ok"}

    @router.delete("/diagnostics/messages/{entry_id}")
    async def dismiss_diagnostic_message(request: Request, entry_id: str) -> dict[str, Any]:
        """Dismiss a single diagnostic by id.

        Supports the per-row Dismiss button on the management UI's
        Diagnostics tab. ``entry_id`` is the ``id`` field returned by the
        ``GET /diagnostics/messages`` snapshot.

        Returns 404 if the id is unknown (already dismissed by another
        operator, or never existed).
        """
        from py20305.diagnostics import DiagnosticsStore

        store: DiagnosticsStore | None = getattr(request.app.state, "diagnostics", None)
        if store is None or not store.dismiss(entry_id):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Unknown diagnostic id")
        return {"status": "dismissed", "id": entry_id}

    @router.get("/alarms")
    async def get_alarms() -> list[dict[str, Any]]:
        """Return active alarms."""
        return []

    @router.post("/alarms")
    async def post_alarm() -> dict[str, Any]:
        """Post a new alarm."""
        return {"status": "ok"}

    # -----------------------------------------------------------------
    # System Operations
    # -----------------------------------------------------------------

    @router.post("/proxy/http-probe")
    async def proxy_http_probe(request: Request) -> dict[str, Any]:
        """Probe the server's HTTP-to-HTTPS redirect, as one call.

        Body: ``{"path": "/dcap", "http_port": 80}``, both optional.
        """
        service = service_getter()
        if service is None:
            return {"error": "not_connected"}
        try:
            body = await request.json() if await request.body() else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        path = str(body.get("path") or "/dcap")
        try:
            http_port = int(body.get("http_port") or 80)
        except (TypeError, ValueError):
            http_port = 80
        return await service.http_probe(path=path, http_port=http_port)

    @router.get("/subscriptions")
    async def get_subscriptions() -> dict[str, Any]:
        """Return the client's active subscriptions."""
        service = service_getter()
        if service is None:
            return {"subscriptions": []}
        return service.get_subscriptions()

    @router.get("/notifications")
    async def get_notifications() -> dict[str, Any]:
        """Return the notifications the client has received."""
        service = service_getter()
        if service is None:
            return {"notifications": []}
        return service.get_notifications()

    @router.post("/reconnect")
    async def reconnect(request: Request) -> dict[str, Any]:
        """Retry the upstream server connection immediately.

        While the client can't reach the IEEE 2030.5 server it keeps the
        management API up and retries with exponential backoff. This shortcuts
        the backoff so an operator's "Retry now" click (e.g. after fixing the
        config, uploading a CA, or registering the LFDI) attempts a fresh
        connection at once instead of waiting out the current interval. It is a
        no-op once connected -- there is no backoff to shortcut.
        """
        logger.info("Manual reconnect requested via API")
        event = getattr(request.app.state, "reconnect_event", None)
        conn = getattr(request.app.state, "connection", None)
        # No-op once connected -- there is no backoff to shortcut, so report
        # that nothing was triggered rather than implying an action was taken.
        already_connected = conn is not None and conn.phase == "connected"
        triggered = event is not None and not already_connected
        if triggered:
            assert event is not None  # narrowed by `triggered`
            event.set()
        return {"status": "ok", "triggered": triggered}

    @router.post("/rediscover")
    async def rediscover() -> dict[str, Any]:
        """Trigger resource rediscovery."""
        logger.info("Rediscovery requested via API")
        service = service_getter()
        if service is None:
            return {"status": "error", "detail": "not_connected"}
        return await service.trigger_rediscovery()

    @router.post("/poll-now")
    async def poll_now() -> dict[str, Any]:
        """Trigger an immediate DER control poll."""
        logger.info("Immediate poll requested via API")
        service = service_getter()
        if service is None:
            return {"status": "error", "detail": "not_connected"}
        return await service.poll_now()

    @router.post("/tls-reset")
    async def tls_reset() -> dict[str, Any]:
        """Reset the TLS session."""
        logger.info("TLS reset requested via API")
        service = service_getter()
        if service is None:
            return {"status": "error", "detail": "not_connected"}
        return await service.reset_tls_session()

    @router.post("/tls-ca")
    async def tls_ca(request: Request) -> dict[str, Any]:
        """Update the CA trust store and reset TLS session."""
        logger.info("TLS CA update requested via API")
        service = service_getter()
        if service is None:
            return {"status": "error", "detail": "not_connected"}
        body = await request.json()
        ca_cert = body.get("ca_cert")
        if not ca_cert:
            return {"status": "error", "detail": "ca_cert is required"}
        return await service.update_ca_trust(ca_cert)

    return router
