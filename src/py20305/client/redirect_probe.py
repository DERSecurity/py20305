"""ERR-001: HTTP-to-HTTPS redirect probe.

Standalone async function extracted from APIService so it can be called
from both the management API and external bridges (e.g., an embedding application).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from py20305.client.http import Sep2Client

logger = logging.getLogger(__name__)


async def run_redirect_probe(host: str, http_client: Sep2Client, port: int = 80) -> dict[str, Any]:
    """Run an HTTP-to-HTTPS redirect probe against *host*.

    1. Plain HTTP GET to ``http://{host}:{port}/dcap`` (no TLS)
    2. Expect 301/302 with Location header
    3. Follow up with HTTPS GET via *http_client* (mTLS session)

    ``port`` defaults to 80 (the standard HTTP port the redirect probe
    historically used). Override when the upstream redirect server runs
    on a non-default port -- e.g. behind a reverse proxy that re-binds
    the public port, in a test harness, or in a lab setup. The HTTPS
    follow-up URL comes from the server's ``Location`` header, so this
    knob only affects the initial plain-HTTP request.

    Returns a result dict. ``http_url``, ``status``, and ``is_redirect``
    are always present. ``http_status`` and ``redirect_location`` are
    set when the plain-HTTP request returned a response (i.e. anything
    other than a step-1 connection failure). ``http_error`` is set when
    step 1 raised. ``https_status`` is set when the redirect was
    followed; ``https_error`` is set when the follow-up either returned
    a non-200 status (with a server-supplied error) or raised.
    """
    http_url = f"http://{host}:{port}/dcap"
    result: dict[str, Any] = {"http_url": http_url, "status": "pending"}

    # Step 1: plain HTTP GET (no TLS, no redirect follow)
    try:
        timeout = aiohttp.ClientTimeout(sock_connect=5, sock_read=10)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(http_url, allow_redirects=False) as resp,
        ):
            result["http_status"] = resp.status
            location = resp.headers.get("Location", "")
            result["redirect_location"] = location
            result["is_redirect"] = resp.status in (301, 302, 303, 307, 308)
    except Exception as exc:
        result["status"] = "error"
        result["http_error"] = str(exc)
        result["is_redirect"] = False
        return result

    # Step 2: if redirect received, follow up with HTTPS via existing client
    if result["is_redirect"] and result.get("redirect_location"):
        try:
            https_resp = await http_client.get_raw(result["redirect_location"])
            result["https_status"] = https_resp.get("status_code")
            if https_resp.get("error"):
                result["https_error"] = https_resp["error"]
            result["status"] = (
                "success" if https_resp.get("status_code") == 200 else "followup_error"
            )
        except Exception as exc:
            result["status"] = "followup_error"
            result["https_error"] = str(exc)
    elif not result["is_redirect"]:
        result["status"] = "no_redirect"

    return result
