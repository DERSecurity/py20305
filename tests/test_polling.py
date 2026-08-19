"""Tests for the async poll scheduler."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from py20305 import diagnostics
from py20305.client.errors import Sep2ConnectionError, Sep2ProtocolError
from py20305.client.polling import PollScheduler
from py20305.diagnostics import DiagnosticsStore


@pytest.mark.asyncio
async def test_basic_poll_cycle():
    """Callback is invoked at least once within the poll interval."""
    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)

    scheduler = PollScheduler()
    scheduler.schedule("test", interval=1, callback=cb)

    await asyncio.sleep(0.1)
    await scheduler.cancel_all(timeout=2.0)

    assert len(calls) >= 1


@pytest.mark.asyncio
async def test_cancel_all_stops_polling():
    """After cancel_all, no more callbacks are invoked."""
    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)

    scheduler = PollScheduler()
    scheduler.schedule("test", interval=1, callback=cb)

    await asyncio.sleep(0.05)
    await scheduler.cancel_all()

    count_at_stop = len(calls)
    await asyncio.sleep(0.1)
    assert len(calls) == count_at_stop


@pytest.mark.asyncio
async def test_active_keys():
    scheduler = PollScheduler()

    async def cb() -> None:
        pass

    scheduler.schedule("a", interval=60, callback=cb)
    scheduler.schedule("b", interval=60, callback=cb)

    assert sorted(scheduler.active_keys) == ["a", "b"]
    await scheduler.cancel_all()


@pytest.mark.asyncio
async def test_replace_key():
    """Scheduling the same key replaces the previous task."""
    first_calls: list[int] = []
    second_calls: list[int] = []

    async def cb1() -> None:
        first_calls.append(1)

    async def cb2() -> None:
        second_calls.append(1)

    scheduler = PollScheduler()
    scheduler.schedule("x", interval=60, callback=cb1)
    await asyncio.sleep(0.05)

    scheduler.schedule("x", interval=1, callback=cb2)
    await asyncio.sleep(0.05)
    await scheduler.cancel_all()

    # Second callback should have been called
    assert len(second_calls) >= 1


@pytest.mark.asyncio
async def test_callback_exception_does_not_stop_polling():
    """An exception in a callback doesn't kill the poll loop."""
    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    scheduler = PollScheduler()
    scheduler.schedule("test", interval=1, callback=cb)

    # Wait long enough for at least 2 invocations
    await asyncio.sleep(1.5)
    await scheduler.cancel_all()

    assert len(calls) >= 2


@pytest.mark.asyncio
async def test_connection_error_logs_warning_without_traceback(
    caplog: pytest.LogCaptureFixture,
):
    """Server-unreachable during a poll should log a one-line warning,
    not a full traceback — operators shouldn't see a scary stack every
    tick when the server is briefly down."""

    async def cb() -> None:
        raise Sep2ConnectionError("Connection error after 3 attempts: ...")

    scheduler = PollScheduler()
    with caplog.at_level("WARNING"):
        scheduler.schedule("fsa", interval=1, callback=cb)
        await asyncio.sleep(0.1)
        await scheduler.cancel_all()

    records = [r for r in caplog.records if "server unreachable" in r.getMessage()]
    assert len(records) >= 1
    assert records[0].exc_info is None
    assert "Poll fsa" in records[0].getMessage()


@pytest.mark.asyncio
async def test_protocol_error_logs_warning_without_traceback(
    caplog: pytest.LogCaptureFixture,
):
    """A non-success HTTP response during a poll should log concisely."""

    async def cb() -> None:
        raise Sep2ProtocolError("GET /edev/2/fsa returned 404: ", status_code=404)

    scheduler = PollScheduler()
    with caplog.at_level("WARNING"):
        scheduler.schedule("fsa", interval=1, callback=cb)
        await asyncio.sleep(0.1)
        await scheduler.cancel_all()

    records = [r for r in caplog.records if "HTTP 404" in r.getMessage()]
    assert len(records) >= 1
    assert records[0].exc_info is None


@pytest.mark.asyncio
async def test_unexpected_exception_still_logs_traceback(
    caplog: pytest.LogCaptureFixture,
):
    """Non-Sep2 exceptions still attach a full traceback — those are real bugs.

    The severity is 'warnings' (matches DIAGNOSTICS_AUDIT B1) but ``exc_info``
    is preserved so the operator gets the stack to debug from.
    """

    async def cb() -> None:
        raise RuntimeError("unexpected bug")

    scheduler = PollScheduler()
    with caplog.at_level("WARNING"):
        scheduler.schedule("fsa", interval=1, callback=cb)
        await asyncio.sleep(0.1)
        await scheduler.cancel_all()

    records = [r for r in caplog.records if "callback failed" in r.getMessage()]
    assert len(records) >= 1
    assert records[0].exc_info is not None


@pytest.mark.asyncio
async def test_repeated_poll_failure_dedups_to_single_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
):
    """B1: repeated failures of the same poll/status collapse to one entry.

    Same dedup_key across N failures → 1 errors entry with count == N.
    """
    fresh = DiagnosticsStore()
    monkeypatch.setattr(diagnostics, "_store", fresh)

    async def cb() -> None:
        raise Sep2ProtocolError("404", status_code=404)

    scheduler = PollScheduler()
    scheduler.schedule("fsa", interval=1, callback=cb)
    # Three poll ticks worth of time — the loop sleeps interval=1 between ticks,
    # but cancel_all() interrupts, so we trigger ~1-2 iterations regardless.
    await asyncio.sleep(0.2)
    await scheduler.cancel_all()

    warnings = fresh.snapshot()["warnings"]
    poll_warnings = [w for w in warnings if w["details"].get("poll_key") == "fsa"]
    # Exactly one entry across all retries (count may be > 1 from extra ticks).
    assert len(poll_warnings) == 1
    assert poll_warnings[0]["details"]["status_code"] == 404


@pytest.mark.asyncio
async def test_recovery_after_failure_emits_info(caplog: pytest.LogCaptureFixture):
    """First successful poll after a logged failure emits an info log so the
    operator's last log line reflects the current state, not a stale failure."""
    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise Sep2ConnectionError("blip")
        # subsequent calls succeed

    scheduler = PollScheduler()
    with caplog.at_level("INFO"):
        scheduler.schedule("fsa", interval=1, callback=cb)
        # Wait for at least: failure tick + interval + recovery tick.
        await asyncio.sleep(1.3)
        await scheduler.cancel_all()

    info_recovery = [
        r for r in caplog.records if r.levelname == "INFO" and "recovered" in r.getMessage()
    ]
    assert len(info_recovery) == 1
    assert "Poll fsa: recovered" in info_recovery[0].getMessage()


@pytest.mark.asyncio
async def test_no_recovery_info_when_first_call_succeeds(caplog: pytest.LogCaptureFixture):
    """A poll that's been healthy from the start should not emit any
    recovery log — there was nothing to recover from."""

    async def cb() -> None:
        pass

    scheduler = PollScheduler()
    with caplog.at_level("INFO"):
        scheduler.schedule("fsa", interval=1, callback=cb)
        await asyncio.sleep(0.2)
        await scheduler.cancel_all()

    assert not any("recovered" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_recovery_after_protocol_error(caplog: pytest.LogCaptureFixture):
    """Recovery log fires after a 4xx outcome too, not just connection refused."""
    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise Sep2ProtocolError("not yet provisioned", status_code=404)

    scheduler = PollScheduler()
    with caplog.at_level("INFO"):
        scheduler.schedule("fsa", interval=1, callback=cb)
        await asyncio.sleep(1.3)
        await scheduler.cancel_all()

    info_recovery = [
        r for r in caplog.records if r.levelname == "INFO" and "recovered" in r.getMessage()
    ]
    assert len(info_recovery) == 1


@pytest.mark.asyncio
async def test_recovery_log_only_fires_once_until_next_failure(
    caplog: pytest.LogCaptureFixture,
):
    """After recovery, subsequent successful polls should be silent. Only a
    new failure followed by another success should fire another recovery log."""
    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise Sep2ConnectionError("first blip")

    scheduler = PollScheduler()
    with caplog.at_level("INFO"):
        scheduler.schedule("fsa", interval=1, callback=cb)
        # Failure on call 1, recovery on call 2, then keep succeeding.
        await asyncio.sleep(2.5)
        await scheduler.cancel_all()

    info_recovery = [
        r for r in caplog.records if r.levelname == "INFO" and "recovered" in r.getMessage()
    ]
    assert len(info_recovery) == 1, (
        f"expected exactly one recovery log, got {len(info_recovery)}: "
        f"{[r.getMessage() for r in info_recovery]}"
    )


@pytest.mark.asyncio
async def test_shutdown_interrupts_sleep():
    """cancel_all wakes sleeping polls immediately."""
    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)

    scheduler = PollScheduler()
    scheduler.schedule("test", interval=3600, callback=cb)

    await asyncio.sleep(0.05)  # let first poll run
    await scheduler.cancel_all(timeout=1.0)

    # Should finish quickly despite 1-hour interval
    assert scheduler.active_keys == []


@pytest.mark.asyncio
async def test_suppressed_key_callback_skipped():
    """A suppressed key's callback should not be invoked at the regular cadence
    (the subscription covers it). The heartbeat baseline poll does
    eventually fire as a safety net, but on a much longer timescale than
    this test's 1.5s window -- with interval=1s and CEILING=900s, the
    heartbeat threshold is max(900, 1) = 900s (15 min)."""
    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)

    scheduler = PollScheduler()
    scheduler.schedule("test", interval=1, callback=cb)
    scheduler.suppress("test")

    await asyncio.sleep(1.5)
    await scheduler.cancel_all()

    assert len(calls) == 0
    assert scheduler.suppressed_keys == {"test"}


@pytest.mark.asyncio
async def test_suppressed_key_heartbeat_fires_after_threshold():
    """Even when a subscription suppresses regular polling,
    the heartbeat baseline poll runs as a safety net for missed
    notifications. Patch the ceiling to a small value so the test runs
    in seconds; in production the ceiling is max(900s, configured)."""
    import py20305.client.polling as polling_mod

    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)

    with patch.object(polling_mod, "_HEARTBEAT_CEILING_S", 1.0):
        scheduler = PollScheduler()
        scheduler.schedule("test", interval=1, callback=cb)
        scheduler.suppress("test")
        # ceiling=1s, interval=1s -> threshold = max(1, 1) = 1s.
        # After ~2.5s we should have seen the heartbeat fire at least once.
        await asyncio.sleep(2.5)
        await scheduler.cancel_all()

    assert len(calls) >= 1, f"expected at least one heartbeat poll within 2.5s, got {len(calls)}"
    assert scheduler.suppressed_keys == {"test"}, (
        "key should remain suppressed after heartbeat fires"
    )


@pytest.mark.asyncio
async def test_suppressed_key_heartbeat_does_not_fire_before_threshold():
    """A suppressed key just-recently-polled shouldn't immediately
    heartbeat again. The threshold must be respected."""
    import py20305.client.polling as polling_mod

    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)

    # Ceiling of 10s; with interval=1s the threshold is max(10, 1) = 10s.
    with patch.object(polling_mod, "_HEARTBEAT_CEILING_S", 10.0):
        scheduler = PollScheduler()
        scheduler.schedule("test", interval=1, callback=cb)
        # Let the initial poll run (not yet suppressed).
        await asyncio.sleep(1.2)
        assert len(calls) >= 1
        baseline = len(calls)

        scheduler.suppress("test")
        # 2 more seconds -- well under the 10s heartbeat threshold.
        await asyncio.sleep(2.0)
        await scheduler.cancel_all()

    # No new calls beyond the pre-suppression baseline.
    assert len(calls) == baseline, (
        f"expected no heartbeat firings within 2s (threshold 10s), "
        f"got {len(calls) - baseline} extra calls"
    )


def test_heartbeat_never_polls_faster_than_configured_interval():
    """Invariant: heartbeat must never fire more often than the configured
    poll interval. The ceiling formula max(CEILING, interval) guarantees
    this by clamping at `interval` for any configured rate longer than the
    ceiling. Pin the property with synchronous _heartbeat_due calls so the
    test runs in milliseconds rather than minutes.

    Production: CEILING=900s. A configured interval of 1h means the
    heartbeat must wait the full 1h, not fire every 15 min."""
    import py20305.client.polling as polling_mod

    scheduler = PollScheduler()
    interval = 3600  # 1h -- well past the production 15-min ceiling.

    # Age = CEILING + 1s. Less than `interval`, so heartbeat must NOT fire
    # (configured-rate clamp wins). Without the clamp, the ceiling alone
    # would say it's due.
    scheduler._last_run["k"] = polling_mod.time.monotonic() - polling_mod._HEARTBEAT_CEILING_S - 1
    assert not scheduler._heartbeat_due("k", interval), (
        "heartbeat fired with age=ceiling+1s for a key configured at "
        "interval=3600s -- the configured-rate clamp is missing"
    )

    # Age past the interval itself -- now it's legitimately due.
    scheduler._last_run["k"] = polling_mod.time.monotonic() - interval - 1
    assert scheduler._heartbeat_due("k", interval)


@pytest.mark.asyncio
async def test_heartbeat_disabled_by_config_suppresses_callback_entirely():
    """heartbeat_enabled=False restores strict IEEE 2030.5 §8.9.3.4
    rule (r) behaviour: no polling of subscribed resources at all,
    regardless of how long suppression lasts."""
    import py20305.client.polling as polling_mod

    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)

    # Ceiling of 1s -- normally the heartbeat would fire within 1.5s.
    # With heartbeat_enabled=False it must NOT fire.
    with patch.object(polling_mod, "_HEARTBEAT_CEILING_S", 1.0):
        scheduler = PollScheduler(heartbeat_enabled=False)
        scheduler.schedule("test", interval=1, callback=cb)
        scheduler.suppress("test")
        await asyncio.sleep(2.5)
        await scheduler.cancel_all()

    assert len(calls) == 0, f"expected zero polls with heartbeat_enabled=False, got {len(calls)}"


@pytest.mark.asyncio
async def test_unsuppressed_key_resets_heartbeat_tracker():
    """Verify the poll loop updates _last_run on every successful run.
    Capture the seeded value from schedule(), then assert it actually
    advanced after the loop fired -- otherwise this test would pass even
    if the loop never touched _last_run (since schedule() seeds it)."""
    started = asyncio.Event()

    async def cb() -> None:
        started.set()

    scheduler = PollScheduler()
    scheduler.schedule("test", interval=1, callback=cb)

    # Backdate the seed rather than comparing two reads of the real clock taken
    # microseconds apart. That comparison only resolves where the clock is
    # fine-grained: `time.monotonic()` has ~15.6 ms granularity on Windows, so
    # both reads land in the same tick, return byte-identical floats, and the
    # assertion fails against a scheduler that is behaving correctly. An hour of
    # separation is unambiguous on any clock.
    #
    # Assigning here is not a race: `schedule()` creates the poll task, but that
    # task cannot run until this coroutine awaits, which happens below.
    seeded = scheduler._last_run["test"] - 3600.0
    scheduler._last_run["test"] = seeded

    # Wait for at least one poll iteration to actually fire.
    await asyncio.wait_for(started.wait(), timeout=2.0)
    # The poll loop sets _last_run BEFORE awaiting the callback, so by
    # the time the callback runs, the timestamp has been updated.
    advanced = scheduler._last_run["test"]
    await scheduler.cancel_all()

    assert advanced > seeded, (
        f"poll loop did not advance _last_run (seeded={seeded}, advanced={advanced})"
    )


@pytest.mark.asyncio
async def test_unsuppress_resumes_callback():
    """Unsuppressing a key should let the callback run again."""
    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)

    scheduler = PollScheduler()
    scheduler.schedule("test", interval=1, callback=cb)
    scheduler.suppress("test")

    await asyncio.sleep(1.2)
    assert len(calls) == 0

    scheduler.unsuppress("test")
    await asyncio.sleep(1.2)
    await scheduler.cancel_all()

    assert len(calls) >= 1
    assert scheduler.suppressed_keys == set()


@pytest.mark.asyncio
async def test_cancel_all_empty():
    """cancel_all on empty scheduler is a no-op."""
    scheduler = PollScheduler()
    await scheduler.cancel_all()
