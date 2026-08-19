"""FastAPI application factory for the management API.

Builds an app exposing the client's management endpoints under ``/api/v1``,
with OpenAPI docs at ``/docs`` and ``/redoc``. The API is read-mostly: it
reports what the client is doing against the utility server and lets an
operator nudge it (refresh a measurement, reconnect, swap a certificate).

The factory takes a service rather than constructing one, so an application
embedding this client passes its own -- including a
:class:`~py20305.api.service.ClientAPIService` subclass with
endpoints of its own. Passing ``None`` is valid and yields an app whose
routes report ``not_connected`` until a service appears, which is what lets
the API stay up while the client is still trying to reach the server.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import FastAPI

if TYPE_CHECKING:
    from py20305.api.service import ClientAPIService

logger = logging.getLogger(__name__)


def create_app(
    service: ClientAPIService | Callable[[], ClientAPIService | None] | None = None,
    title: str = "py20305",
    version: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        service: The :class:`~py20305.api.service.ClientAPIService`
            to serve, or a zero-argument callable returning one. A callable is
            the right choice when the service is built after the app -- it is
            consulted per request, so routes start answering as soon as it
            returns something. ``None`` yields an app whose routes report
            ``not_connected``.
        title: Application title, shown in the OpenAPI docs.
        version: Application version for the OpenAPI docs. Defaults to the
            installed package version.

    Returns:
        A configured :class:`~fastapi.FastAPI` application.
    """
    if version is None:
        from py20305.version_info import get_package_version

        version = get_package_version()

    app = FastAPI(
        title=title,
        version=version,
        description=(
            "Management API for an IEEE 2030.5 / CSIP client. "
            "Use **Try it out** on any endpoint to send a live request."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Cert-derived client identity, for a caller to fill in after create_app()
    # and for its own routes to read: the LFDI a utility registers, and the
    # full fingerprint it is the leftmost 40 hex of, since registration
    # usually means handing over the whole value. Left None here because this
    # factory never sees the certificate.
    #
    # The title and version are not mirrored onto state -- FastAPI already
    # exposes them as ``app.title`` and ``app.version``.
    app.state.lfdi = None
    app.state.fingerprint = None

    # Upstream-connection status and manual-retry signal. Populated by a
    # caller running a reconnect loop, so /status can report a "disconnected,
    # retrying" phase and /reconnect can shortcut the backoff while the
    # management API stays up across connection failures.
    app.state.connection = None
    app.state.reconnect_event = None

    # Bounded in-memory log of errors, warnings and faults surfaced via
    # /api/v1/diagnostics/messages. Reuses a process-wide store if one was
    # initialized before create_app() ran, so events captured during startup
    # (TLS path failures, environment checks) are visible; otherwise starts
    # a fresh one.
    from py20305.diagnostics import get_store, init_store

    existing = get_store()
    app.state.diagnostics = existing if existing is not None else init_store()

    from py20305.api.client_routes import create_client_router

    if service is None or callable(service):
        service_getter: Callable[[], ClientAPIService | None] = (
            service if callable(service) else lambda: None
        )
    else:
        service_getter = lambda: service  # noqa: E731

    app.include_router(create_client_router(service_getter), prefix="/api/v1")

    return app
