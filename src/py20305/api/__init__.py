"""HTTP management API for the client.

Endpoints for observing what the client is doing against the utility server
and for nudging it -- refreshing a measurement, reconnecting, swapping a
certificate. Use :func:`create_app` for a ready FastAPI application, or
:func:`create_client_router` to mount the routes inside an app you already
have.
"""

from py20305.api.app import create_app
from py20305.api.client_routes import create_client_router
from py20305.api.service import ClientAPIService

__all__ = ["ClientAPIService", "create_app", "create_client_router"]
