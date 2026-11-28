"""Set a client's last-contact time in tests."""

from __future__ import annotations

import time

from py20305.client.csip_client import CsipClient


def set_last_contact(client: CsipClient, seconds_ago: float | None) -> None:
    """Set the client's last reachable contact to ``seconds_ago`` seconds in the past.

    ``None`` clears it, as if the server was never reached.
    """
    http = client._http
    if seconds_ago is None:
        http._last_contact_epoch = None
        http._last_validated_epoch = None
        http._last_contact_monotonic = None
        return
    http._last_contact_epoch = int(time.time() - seconds_ago)
    http._last_validated_epoch = http._last_contact_epoch
    http._last_contact_monotonic = time.monotonic() - seconds_ago
