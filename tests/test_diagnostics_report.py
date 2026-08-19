"""Tests for the module-level ``report()`` helper in :mod:`py20305.diagnostics`.

``report()`` is the canonical helper for emitting a diagnostic event:
it logs and stores in one call, reading from a process-wide module-level
store initialised by :func:`init_store`.
"""

from __future__ import annotations

import logging

import pytest

from py20305 import diagnostics
from py20305.diagnostics import DiagnosticsStore, get_store, init_store, report


@pytest.fixture
def diag_store(monkeypatch: pytest.MonkeyPatch) -> DiagnosticsStore:
    """Install a fresh module-level diagnostics store for the test."""
    fresh = DiagnosticsStore()
    monkeypatch.setattr(diagnostics, "_store", fresh)
    return fresh


def test_report_emits_log_and_store(
    diag_store: DiagnosticsStore, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="py20305.diagnostics"):
        report("warnings", "test message", source="unit-test", dedup_key="t1")

    # Logger fired with the right level + message.
    assert any(
        r.levelname == "WARNING" and "test message" in r.getMessage() for r in caplog.records
    )
    # Store has the entry with metadata preserved.
    snap = diag_store.snapshot()
    assert len(snap["warnings"]) == 1
    assert snap["warnings"][0]["message"] == "test message"
    assert snap["warnings"][0]["source"] == "unit-test"


def test_report_levels_map_to_log_levels(
    diag_store: DiagnosticsStore, caplog: pytest.LogCaptureFixture
) -> None:
    """errors log at ERROR; warnings at WARNING; info at INFO."""
    with caplog.at_level(logging.DEBUG, logger="py20305.diagnostics"):
        report("errors", "boom", dedup_key="e")
        report("warnings", "uh oh", dedup_key="w")
        report("info", "fyi", dedup_key="i")

    by_msg = {r.getMessage(): r.levelname for r in caplog.records}
    assert by_msg["boom"] == "ERROR"
    assert by_msg["uh oh"] == "WARNING"
    assert by_msg["fyi"] == "INFO"


def test_report_dedup_updates_count(diag_store: DiagnosticsStore) -> None:
    report("warnings", "repeated", dedup_key="rep")
    report("warnings", "repeated", dedup_key="rep")
    report("warnings", "repeated", dedup_key="rep")

    entries = diag_store.snapshot()["warnings"]
    assert len(entries) == 1
    assert entries[0]["count"] == 3


def test_report_does_not_crash_before_init(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Very-early-startup callers must not need a guard."""
    monkeypatch.setattr(diagnostics, "_store", None)
    with caplog.at_level(logging.WARNING, logger="py20305.diagnostics"):
        report("warnings", "early")  # must not raise

    assert any("early" in r.getMessage() for r in caplog.records)


def test_report_forwards_exc_info_to_logger(
    diag_store: DiagnosticsStore, caplog: pytest.LogCaptureFixture
) -> None:
    """``exc_info`` reaches the logger so tracebacks land in the log stream."""
    with caplog.at_level(logging.ERROR, logger="py20305.diagnostics"):
        try:
            raise RuntimeError("synthetic")
        except RuntimeError:
            report("errors", "caught", exc_info=True)

    err_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert any(r.exc_info is not None for r in err_records)
    # Store entry doesn't carry exc info; that's by design.
    assert diag_store.snapshot()["errors"][0]["message"] == "caught"


def test_init_store_is_idempotent_with_explicit_arg() -> None:
    """Passing an existing store in installs it as the module ref."""
    custom = DiagnosticsStore()
    returned = init_store(custom)
    assert returned is custom
    assert get_store() is custom


def test_init_store_default_creates_fresh_instance() -> None:
    init_store()
    first = get_store()
    init_store()
    second = get_store()
    assert first is not None
    assert second is not None
    assert first is not second
