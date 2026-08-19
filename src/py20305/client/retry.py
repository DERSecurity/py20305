"""Retry logic with exponential backoff for the SEP2 client."""

from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from py20305.client.errors import (
    Sep2ConnectionError,
    Sep2RateLimitError,
    Sep2RedirectError,
    Sep2TlsError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


_HOSTNAME_MISMATCH_HINT = (
    "Server cert's SAN/CN does not match the address the client "
    "connected to. If this peer is identified by LFDI rather than DNS "
    "(common for IEEE 2030.5 devices that ship a LFDI-bearing cert and "
    "have no DNS A record), set tls.check_hostname: false in the client "
    "config to keep chain validation while disabling hostname matching."
)


def _is_hostname_mismatch(exc: ssl.SSLError) -> bool:
    """True iff *exc* is a 'server cert hostname does not match' SSL failure.

    Python's ssl module sets ``verify_message`` to ``"Hostname mismatch"`` on
    ``SSLCertVerificationError`` for this specific case. Falls back to a
    substring check on ``str(exc)`` so the detection survives slight wording
    changes across CPython / OpenSSL versions.
    """
    if not isinstance(exc, ssl.SSLCertVerificationError):
        return False
    verify_msg = (getattr(exc, "verify_message", "") or "").lower()
    if "hostname mismatch" in verify_msg:
        return True
    return "hostname mismatch" in str(exc).lower()


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration for retry behavior."""

    max_transient: int = 3
    max_tls: int = 8
    base_delay: float = 0.25
    backoff_factor: float = 2.0


async def with_retry(
    policy: RetryPolicy,
    operation: Callable[..., Awaitable[T]],
    *args: object,
    peer: str | None = None,
    **kwargs: object,
) -> T:
    """Execute an async operation with retry on transient/TLS errors.

    ``peer`` is used to tag the diagnostic surfaced when transient retries
    are exhausted (``Sep2ConnectionError``). Callers that have it (e.g.
    the SEP2 HTTP client) should pass it so the UI dedup-collapses one
    flapping host to a single entry; callers without context can omit it
    and only the log line will fire.
    """
    transient_attempts = 0
    tls_attempts = 0

    while True:
        try:
            return await operation(*args, **kwargs)
        except ssl.SSLError as exc:
            tls_attempts += 1
            hostname_mismatch = _is_hostname_mismatch(exc)
            if hostname_mismatch and tls_attempts == 1:
                # Log the remediation hint once on first encounter; subsequent
                # retries fall through to the generic per-attempt log so the
                # noise stays bounded. By construction, getting this error at
                # all means check_hostname is currently True -- Python's ssl
                # layer doesn't run hostname matching otherwise.
                logger.warning(
                    "TLS hostname mismatch%s: %s. %s",
                    f" to {peer}" if peer else "",
                    exc,
                    _HOSTNAME_MISMATCH_HINT,
                )
            if tls_attempts >= policy.max_tls:
                if peer is not None:
                    from py20305.diagnostics import report

                    if hostname_mismatch:
                        message = (
                            f"TLS hostname mismatch to {peer} after "
                            f"{tls_attempts} attempts: {exc}. "
                            f"{_HOSTNAME_MISMATCH_HINT}"
                        )
                        dedup_key = f"tls_hostname_mismatch:{peer}"
                    else:
                        message = (
                            f"TLS handshake failure to {peer} after {tls_attempts} attempts: {exc}"
                        )
                        dedup_key = f"tls_handshake:{peer}:{type(exc).__name__}"
                    details: dict[str, object] = {
                        "peer": peer,
                        "error_kind": type(exc).__name__,
                        "error": str(exc),
                    }
                    if hostname_mismatch:
                        details["kind"] = "hostname_mismatch"
                    report(
                        "warnings",
                        message,
                        source="client",
                        dedup_key=dedup_key,
                        details=details,
                    )
                raise Sep2TlsError(f"TLS error after {tls_attempts} attempts: {exc}") from exc
            delay = policy.base_delay * (policy.backoff_factor ** (tls_attempts - 1))
            logger.warning(
                "TLS error (attempt %d/%d), retrying in %.2fs: %s",
                tls_attempts,
                policy.max_tls,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
        except Sep2RateLimitError as exc:
            transient_attempts += 1
            if transient_attempts >= policy.max_transient:
                raise
            delay = (
                float(exc.retry_after)
                if exc.retry_after
                else (policy.base_delay * (policy.backoff_factor ** (transient_attempts - 1)))
            )
            logger.warning(
                "Rate limited (attempt %d/%d), retrying in %.2fs",
                transient_attempts,
                policy.max_transient,
                delay,
            )
            await asyncio.sleep(delay)
        except Sep2RedirectError as exc:
            # IEEE 5.5.2.7 says clients SHOULD re-discover on a redirect, but in
            # practice these (301/302/307/308) are usually transient (e.g. a
            # proxy returning `/Error/?errorCode=4&404` because of a malformed
            # upstream request). Retrying with backoff lets a transient cause
            # clear without taking the whole process down. After max_transient
            # attempts the original error propagates so an operator who is
            # actually facing a permanent move sees it; we don't follow the
            # Location blindly because the target may be a cross-origin
            # error page. Following + re-discovery is tracked separately.
            transient_attempts += 1
            if transient_attempts >= policy.max_transient:
                raise
            delay = policy.base_delay * (policy.backoff_factor ** (transient_attempts - 1))
            location = exc.location or "(no Location header)"
            if peer is not None:
                from py20305.diagnostics import report

                # Surfaces the redirect status + target so the operator sees
                # *what* and *where* the upstream is sending us (often the actual
                # diagnostic). Dedup per (peer, status, location) so a stable
                # redirect collapses to one entry; a fresh one still produces a
                # new entry.
                report(
                    "warnings",
                    f"HTTP {exc.status_code} redirect from {peer} -> {location} "
                    f"(attempt {transient_attempts}/{policy.max_transient})",
                    source="client",
                    dedup_key=f"redirect:{peer}:{exc.status_code}:{location}",
                    details={
                        "peer": peer,
                        "status_code": exc.status_code,
                        "location": location,
                        "attempt": transient_attempts,
                    },
                )
            else:
                logger.warning(
                    "HTTP %d redirect (attempt %d/%d) -> %s, retrying in %.2fs",
                    exc.status_code,
                    transient_attempts,
                    policy.max_transient,
                    location,
                    delay,
                )
            await asyncio.sleep(delay)
        except (TimeoutError, OSError) as exc:
            transient_attempts += 1
            if transient_attempts >= policy.max_transient:
                msg = f"Connection error after {transient_attempts} attempts: {exc}"
                if peer is not None:
                    from py20305.diagnostics import report

                    report(
                        "errors",
                        f"Cannot reach {peer}: {exc}",
                        source="client",
                        dedup_key=f"connection:{peer}",
                        details={"peer": peer, "error": str(exc)},
                    )
                raise Sep2ConnectionError(msg) from exc
            delay = policy.base_delay * (policy.backoff_factor ** (transient_attempts - 1))
            logger.warning(
                "Transient error (attempt %d/%d), retrying in %.2fs: %s",
                transient_attempts,
                policy.max_transient,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
