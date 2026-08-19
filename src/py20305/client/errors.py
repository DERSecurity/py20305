"""Error types for the IEEE 2030.5 client."""

from __future__ import annotations

# Substrings in a server's 400 response body that indicate the server is
# validating against the IEEE 2030.5-2018 schema and rejecting attributes /
# child elements introduced in 2030.5-2023. When we see one of these,
# surfacing a "try server_2018_compat=true" hint to the operator turns a
# generic protocol failure into an actionable next step.
_2018_SCHEMA_SIGNATURES: tuple[str, ...] = (
    "'subscribable' attribute is not declared",
    "'schemaVer' attribute is not declared",
    "invalid child element 'connectStatus'",
)

_2018_COMPAT_HINT = (
    "this typically indicates an IEEE 2030.5-2018 server; "
    "try setting server_2018_compat=true in the client configuration"
)


class Sep2Error(Exception):
    """Base exception for all SEP2 client errors."""


class Sep2ConnectionError(Sep2Error):
    """Raised on connection failures (network, DNS, timeout)."""


class Sep2ProtocolError(Sep2Error):
    """Raised on unexpected HTTP status codes."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class Sep2NoContentError(Sep2ProtocolError):
    """Raised when a GET returns 204 No Content -- the request succeeded but the
    server returned no representation.

    A first-class signal distinct from a real protocol error: for an optional
    resource it means "present but empty" (treat as absent); for a list it means
    "no items". Subclasses Sep2ProtocolError (with ``status_code == 204``) so
    existing ``except Sep2ProtocolError`` handlers remain backward-compatible.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, 204)


class Sep2TlsError(Sep2Error):
    """Raised on TLS handshake or certificate errors."""


class Sep2RedirectError(Sep2Error):
    """Raised on an HTTP redirect (301/302/307/308) to trigger re-discovery.

    IEEE 5.5.2.7: clients SHOULD perform resource discovery to determine
    which resources have changed location. ``status_code`` carries the actual
    redirect status so logs/diagnostics name the right code (defaults to 301,
    the canonical case, for callers that don't supply it).
    """

    def __init__(self, message: str, location: str, status_code: int = 301) -> None:
        super().__init__(message)
        self.location = location
        self.status_code = status_code


class Sep2RateLimitError(Sep2Error):
    """Raised on 429 Too Many Requests.

    IEEE 5.5.2.17: the server indicates the client is sending too many requests.
    """

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Sep2PayloadError(Sep2Error):
    """Raised when an HTTP 200 response body can't be parsed as the expected
    IEEE 2030.5 resource (malformed XML, empty body, wrong root element).

    Distinct from :class:`Sep2ProtocolError` (HTTP-level failure) because
    the server replied successfully but the body is unusable. The polling
    loop logs this with WARNING + no traceback so the operator sees a
    clean one-line diagnostic instead of an xsdata stack frame.
    """

    def __init__(self, message: str, *, path: str, body_length: int) -> None:
        super().__init__(message)
        self.path = path
        self.body_length = body_length


def is_2018_schema_validation_error(exc: BaseException) -> bool:
    """Return True if *exc* looks like a 400 from an IEEE 2030.5-2018 server.

    The 2018 schema lacks several attributes (``subscribable``, ``schemaVer``)
    and child elements (``connectStatus``) added in 2030.5-2023. A 2018-only
    server validates against its older schema and returns 400 with a message
    naming the offending attribute or element.

    Predicate is intentionally conservative: a non-``Sep2ProtocolError`` or a
    non-400 always returns False so callers can drop a hint into telemetry
    diagnostics without false positives on unrelated failures.
    """
    if not isinstance(exc, Sep2ProtocolError) or exc.status_code != 400:
        return False
    message = str(exc)
    return any(sig in message for sig in _2018_SCHEMA_SIGNATURES)


def compat_hint_suffix(exc: BaseException, server_2018_compat: bool) -> str:
    """Return ``" -- {hint}"`` when *exc* matches a 2018-server signature
    and the client is not already in 2018-compat mode; otherwise ``""``.

    Designed for appending to operator-facing diagnostic messages so the
    UI footer entry tells the operator exactly which config flag would
    likely make the failure go away. Returns empty string in every other
    case so callers can do ``f"{base_msg}{compat_hint_suffix(...)}"``
    unconditionally.
    """
    if server_2018_compat or not is_2018_schema_validation_error(exc):
        return ""
    return f" -- {_2018_COMPAT_HINT}"
