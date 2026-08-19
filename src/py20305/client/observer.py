"""Connection-outcome observer seam for :class:`~py20305.client.http.Sep2Client`.

A passive capture beside the client cannot recover the client's own connection
outcomes from inside TLS, so the client reports them itself to whoever asks.
An embedder implements :class:`ConnectionObserver` and attaches it at
construction (``Sep2Client(connection_observer=...)``) or afterwards through
the ``connection_observer`` property; the client then reports each logical
request's outcome and each established TCP connection's addresses.

Every callback is invoked from the client's own request path, so
implementations must never raise and never block: an observer that throws
would fail the request it is describing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from py20305.client.connector import SocketPair


@runtime_checkable
class ConnectionObserver(Protocol):
    """What :class:`~py20305.client.http.Sep2Client` reports connection outcomes to.

    Outcomes are reported per logical request, not per retry attempt: the
    retry wrapper collapses exhausted transport attempts into one error, so
    individual attempts are not distinguishable at this seam. A 204 No
    Content counts as a success — it is a validated contact that happens to
    signal itself by raising.
    """

    def begin_request(self) -> None:
        """A logical request is starting.

        Called before the first attempt, so the observer can scope any
        per-request attribution (for example, tying an ``on_connect`` that
        fires during the request to that request).
        """

    def record_success(self) -> None:
        """The logical request reached the server and completed."""

    def record_failure(self, exc: BaseException) -> None:
        """The logical request failed with ``exc`` after retries."""

    def on_connect(self, pair: SocketPair) -> None:
        """A TCP connection was established and passed the handshake audit.

        The local port is only knowable at this moment — a pooled connection
        carries many requests on one local port, and the response object has
        already released its connection by the time a caller could ask.
        """

    def flush(self) -> None:
        """The client is closing; emit anything the observer has buffered."""
