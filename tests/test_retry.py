"""Tests for retry logic."""

import ssl
from unittest.mock import AsyncMock, patch

import pytest

from py20305 import diagnostics
from py20305.client.errors import Sep2ConnectionError, Sep2RedirectError, Sep2TlsError
from py20305.client.retry import RetryPolicy, _is_hostname_mismatch, with_retry
from py20305.diagnostics import DiagnosticsStore

_DEFAULT_HM_DETAIL = "Hostname mismatch, certificate is not valid for '10.0.0.5'."


def _hostname_mismatch_exc(detail: str = _DEFAULT_HM_DETAIL) -> ssl.SSLCertVerificationError:
    """Build an SSLCertVerificationError that looks like a hostname-mismatch
    failure from Python's ssl layer (verify_message attr set, str(exc) also
    carries the marker so the fallback path can be exercised independently)."""
    exc = ssl.SSLCertVerificationError(detail)
    exc.verify_message = "Hostname mismatch"
    return exc


async def test_success_no_retry():
    op = AsyncMock(return_value="ok")
    result = await with_retry(RetryPolicy(), op)
    assert result == "ok"
    assert op.call_count == 1


async def test_transient_retry_then_success():
    op = AsyncMock(side_effect=[OSError("fail"), "ok"])
    with patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await with_retry(RetryPolicy(max_transient=3), op)
    assert result == "ok"
    assert op.call_count == 2


async def test_transient_retry_exhausted():
    op = AsyncMock(side_effect=OSError("fail"))
    with (
        patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(Sep2ConnectionError, match="3 attempts"),
    ):
        await with_retry(RetryPolicy(max_transient=3), op)
    assert op.call_count == 3


async def test_tls_retry_exhausted():
    op = AsyncMock(side_effect=ssl.SSLError("tls fail"))
    with (
        patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(Sep2TlsError, match="2 attempts"),
    ):
        await with_retry(RetryPolicy(max_tls=2), op)
    assert op.call_count == 2


async def test_timeout_retry():
    op = AsyncMock(side_effect=[TimeoutError(), "ok"])
    with patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await with_retry(RetryPolicy(max_transient=3), op)
    assert result == "ok"


async def test_redirect_retry_then_success():
    """A 301 from upstream is retry-eligible -- transient causes (e.g. a
    proxy briefly returning an error redirect) clear without taking the
    aggregator process down."""
    op = AsyncMock(
        side_effect=[
            Sep2RedirectError("GET /dcap returned 301", location="/Error/?errorCode=4&404"),
            "ok",
        ]
    )
    with patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await with_retry(RetryPolicy(max_transient=3), op)
    assert result == "ok"
    assert op.call_count == 2


async def test_redirect_retry_exhausted_propagates_original_error():
    """If 301s persist past max_transient, the original Sep2RedirectError
    propagates so the operator sees the actual symptom (and the Location
    target) rather than a wrapped error."""
    op = AsyncMock(side_effect=Sep2RedirectError("GET /dcap returned 301", location="/elsewhere"))
    with (
        patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(Sep2RedirectError) as exc_info,
    ):
        await with_retry(RetryPolicy(max_transient=2), op)
    assert exc_info.value.location == "/elsewhere"
    assert op.call_count == 2


async def test_redirect_logs_location(caplog: pytest.LogCaptureFixture):
    """The retry warning must surface the Location header so an operator
    triaging logs can see *where* the server is redirecting them (often
    the actual diagnostic, e.g. an /Error/?errorCode=... page)."""
    op = AsyncMock(
        side_effect=[
            Sep2RedirectError("GET /dcap returned 301", location="/Error/?errorCode=4&404"),
            "ok",
        ]
    )
    with (
        patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock),
        caplog.at_level("WARNING"),
    ):
        await with_retry(RetryPolicy(max_transient=3), op)
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("/Error/?errorCode=4&404" in m and "301" in m for m in warnings)


async def test_redirect_without_location_does_not_crash_logger():
    """A 301 with an empty Location header still retries -- the warning
    string must not blow up on `None`/empty (regression for a log-format
    crash that would mask the underlying retry behavior)."""
    op = AsyncMock(side_effect=[Sep2RedirectError("301", location=""), "ok"])
    with patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await with_retry(RetryPolicy(max_transient=3), op)
    assert result == "ok"


async def test_backoff_delays():
    op = AsyncMock(side_effect=[OSError("1"), OSError("2"), "ok"])
    sleep_mock = AsyncMock()
    with patch("py20305.client.retry.asyncio.sleep", sleep_mock):
        await with_retry(RetryPolicy(max_transient=3, base_delay=1.0, backoff_factor=2.0), op)
    assert sleep_mock.call_count == 2
    assert sleep_mock.call_args_list[0].args[0] == 1.0  # 1.0 * 2^0
    assert sleep_mock.call_args_list[1].args[0] == 2.0  # 1.0 * 2^1


async def test_exhausted_transient_emits_diagnostic_when_peer_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C6: with peer set, exhausting transient retries surfaces a UI error."""
    fresh = DiagnosticsStore()
    monkeypatch.setattr(diagnostics, "_store", fresh)

    op = AsyncMock(side_effect=OSError("connection refused"))
    with (
        patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(Sep2ConnectionError),
    ):
        await with_retry(RetryPolicy(max_transient=2), op, peer="server.example")

    errors = fresh.snapshot()["errors"]
    assert len(errors) == 1
    assert "server.example" in errors[0]["message"]
    assert errors[0]["source"] == "client"
    assert errors[0]["details"]["peer"] == "server.example"


async def test_exhausted_transient_silent_when_peer_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backwards compatibility: callers without peer get only the existing log line."""
    fresh = DiagnosticsStore()
    monkeypatch.setattr(diagnostics, "_store", fresh)

    op = AsyncMock(side_effect=OSError("fail"))
    with (
        patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(Sep2ConnectionError),
    ):
        await with_retry(RetryPolicy(max_transient=2), op)

    assert fresh.snapshot()["errors"] == []


def test_is_hostname_mismatch_matches_verify_message():
    """The verify_message attribute is Python's official signal for the
    hostname-mismatch failure mode; the helper must catch it."""
    exc = ssl.SSLCertVerificationError("anything")
    exc.verify_message = "Hostname mismatch"
    assert _is_hostname_mismatch(exc) is True


def test_is_hostname_mismatch_falls_back_to_str():
    """Older CPython builds don't set verify_message reliably; the helper
    must still detect the failure from the rendered exception."""
    exc = ssl.SSLCertVerificationError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] hostname mismatch, "
        "certificate is not valid for '10.0.0.5'."
    )
    # No verify_message attribute at all -- exercises the fallback branch.
    assert _is_hostname_mismatch(exc) is True


def test_is_hostname_mismatch_false_for_generic_ssl_error():
    """Generic SSLError (e.g. handshake failure) must not be misclassified."""
    assert _is_hostname_mismatch(ssl.SSLError("handshake failed")) is False


def test_is_hostname_mismatch_false_for_unrelated_cert_error():
    """An SSLCertVerificationError that isn't hostname-mismatch (e.g. expired
    cert) must not trip the hint -- the remediation doesn't apply."""
    exc = ssl.SSLCertVerificationError("certificate has expired")
    exc.verify_message = "certificate has expired"
    assert _is_hostname_mismatch(exc) is False


async def test_hostname_mismatch_logs_remediation_hint_on_first_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The operator-facing 'set check_hostname: false if peer is LFDI-identified'
    hint must appear in logs on first encounter, since the bare Python SSL
    error gives no clue that this is a config option."""
    op = AsyncMock(side_effect=_hostname_mismatch_exc())
    with (
        patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock),
        caplog.at_level("WARNING"),
        pytest.raises(Sep2TlsError),
    ):
        await with_retry(RetryPolicy(max_tls=2), op, peer="10.0.0.5")

    # Filter to retry.py's own log records; the diagnostics report also
    # writes a WARNING with the same hint at exhaustion, but that's a
    # different code path (py20305.diagnostics logger).
    hint_lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "py20305.client.retry" and "check_hostname" in r.getMessage()
    ]
    assert len(hint_lines) == 1, f"expected 1 hint log, got {len(hint_lines)}"
    assert "10.0.0.5" in hint_lines[0]


async def test_hostname_mismatch_hint_does_not_repeat_across_retries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The hint logs exactly once per call regardless of retry depth; the
    per-attempt 'TLS error (N/M)' line carries the raw exception for the
    rest, so the hint doesn't spam the log on every backoff."""
    op = AsyncMock(side_effect=_hostname_mismatch_exc())
    with (
        patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock),
        caplog.at_level("WARNING"),
        pytest.raises(Sep2TlsError),
    ):
        await with_retry(RetryPolicy(max_tls=5), op, peer="10.0.0.5")

    hint_from_retry = [
        r
        for r in caplog.records
        if r.name == "py20305.client.retry" and "check_hostname" in r.getMessage()
    ]
    assert len(hint_from_retry) == 1


async def test_generic_ssl_error_does_not_log_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-hostname-mismatch TLS failures (handshake, expired cert, chain
    validation, ...) must not get the hint -- the remediation doesn't apply."""
    op = AsyncMock(side_effect=ssl.SSLError("handshake failed"))
    with (
        patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock),
        caplog.at_level("WARNING"),
        pytest.raises(Sep2TlsError),
    ):
        await with_retry(RetryPolicy(max_tls=2), op, peer="server.example")

    assert not any("check_hostname" in r.getMessage() for r in caplog.records)


async def test_hostname_mismatch_diagnostic_at_exhaustion_carries_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When TLS retries exhaust on a hostname mismatch, the UI-surfaced
    diagnostic must include the remediation hint and use a distinct
    dedup_key (so it doesn't collapse with unrelated TLS handshake failures
    to the same peer)."""
    fresh = DiagnosticsStore()
    monkeypatch.setattr(diagnostics, "_store", fresh)

    op = AsyncMock(side_effect=_hostname_mismatch_exc())
    with (
        patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(Sep2TlsError),
    ):
        await with_retry(RetryPolicy(max_tls=2), op, peer="10.0.0.5")

    warnings = fresh.snapshot()["warnings"]
    assert len(warnings) == 1
    entry = warnings[0]
    assert "10.0.0.5" in entry["message"]
    assert "check_hostname" in entry["message"]
    assert entry["details"]["kind"] == "hostname_mismatch"


async def test_generic_tls_failure_diagnostic_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generic TLS failures keep their original diagnostic shape -- no hint,
    no hostname_mismatch kind, same dedup_key as before. Locks in
    backwards compatibility for the existing handshake-failure path."""
    fresh = DiagnosticsStore()
    monkeypatch.setattr(diagnostics, "_store", fresh)

    op = AsyncMock(side_effect=ssl.SSLError("handshake failed"))
    with (
        patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(Sep2TlsError),
    ):
        await with_retry(RetryPolicy(max_tls=2), op, peer="server.example")

    warnings = fresh.snapshot()["warnings"]
    assert len(warnings) == 1
    entry = warnings[0]
    assert "check_hostname" not in entry["message"]
    assert "kind" not in entry["details"]


async def test_redirect_emits_diagnostic_when_peer_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1: a 301 retry surfaces in the UI when peer is supplied; dedup keys
    redirect:{peer}:{location} so a stable redirect collapses to one entry."""
    fresh = DiagnosticsStore()
    monkeypatch.setattr(diagnostics, "_store", fresh)

    op = AsyncMock(
        side_effect=[
            Sep2RedirectError("GET /dcap returned 301", location="/Error/?errorCode=4&404"),
            "ok",
        ]
    )
    with patch("py20305.client.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await with_retry(RetryPolicy(max_transient=3), op, peer="server.example")
    assert result == "ok"

    warnings = fresh.snapshot()["warnings"]
    assert len(warnings) == 1
    assert "/Error/?errorCode=4&404" in warnings[0]["message"]
    assert warnings[0]["source"] == "client"
    assert warnings[0]["details"]["peer"] == "server.example"
    assert warnings[0]["details"]["location"] == "/Error/?errorCode=4&404"
