"""Tests for the DiagnosticsStore in-memory diagnostic log."""

from __future__ import annotations

import threading

import pytest

from py20305.diagnostics import LEVELS, DiagnosticsStore


def test_add_and_snapshot_categorizes_by_level() -> None:
    store = DiagnosticsStore()
    store.add("warnings", "env var deprecated", source="config")
    store.add("errors", "connection refused", details={"host": "x"})
    store.add("info", "started")

    snap = store.snapshot()
    assert set(snap) == set(LEVELS)
    assert len(snap["warnings"]) == 1
    assert snap["warnings"][0]["message"] == "env var deprecated"
    assert snap["warnings"][0]["source"] == "config"
    assert "timestamp" in snap["warnings"][0]
    assert snap["errors"][0]["details"] == {"host": "x"}


def test_entries_roll_over_past_max_entries() -> None:
    store = DiagnosticsStore(max_entries=3)
    for i in range(5):
        store.add("info", f"msg-{i}")

    snap = store.snapshot()
    assert len(snap["info"]) == 3
    assert [e["message"] for e in snap["info"]] == ["msg-2", "msg-3", "msg-4"]


def test_dedup_key_suppresses_duplicates() -> None:
    store = DiagnosticsStore()
    assert store.add("warnings", "CSIP_HOST is deprecated", dedup_key="csip_host") is True
    assert store.add("warnings", "CSIP_HOST is deprecated", dedup_key="csip_host") is False

    snap = store.snapshot()
    assert len(snap["warnings"]) == 1


def test_dedup_hit_updates_last_seen_and_count() -> None:
    store = DiagnosticsStore()
    store.add("warnings", "first", dedup_key="k")
    store.add("warnings", "first", dedup_key="k")
    store.add("warnings", "first", dedup_key="k")

    snap = store.snapshot()
    assert len(snap["warnings"]) == 1
    entry = snap["warnings"][0]
    assert entry["count"] == 3
    # `last_seen` is only emitted when it differs from `timestamp` (the dedup
    # hits may land in the same wall-clock second). When present it must not
    # be earlier than the original timestamp.
    if "last_seen" in entry:
        assert entry["last_seen"] >= entry["timestamp"]
    # Original message preserved -- dedup hits don't rewrite it.
    assert entry["message"] == "first"


def test_first_emission_omits_count_and_last_seen() -> None:
    """Non-deduped entries shouldn't carry count/last_seen in the JSON shape."""
    store = DiagnosticsStore()
    store.add("warnings", "single", dedup_key="solo")

    snap = store.snapshot()
    entry = snap["warnings"][0]
    assert "count" not in entry
    assert "last_seen" not in entry


def test_each_entry_has_a_unique_stable_id() -> None:
    store = DiagnosticsStore()
    store.add("warnings", "first")
    store.add("warnings", "second")
    store.add("errors", "boom")

    snap = store.snapshot()
    ids = [e["id"] for e in snap["warnings"]] + [e["id"] for e in snap["errors"]]
    assert all(isinstance(i, str) and len(i) > 0 for i in ids)
    assert len(set(ids)) == len(ids), "ids should be unique"


def test_dedup_hit_preserves_id() -> None:
    """A dedup hit must update last_seen + count without rotating the id --
    the UI dismisses by id, so a stable identifier is what makes the row
    addressable across refreshes."""
    store = DiagnosticsStore()
    store.add("warnings", "repeat", dedup_key="k")
    first_id = store.snapshot()["warnings"][0]["id"]
    store.add("warnings", "repeat", dedup_key="k")
    store.add("warnings", "repeat", dedup_key="k")

    snap = store.snapshot()
    assert len(snap["warnings"]) == 1
    assert snap["warnings"][0]["id"] == first_id


def test_dismiss_removes_entry_returns_true() -> None:
    store = DiagnosticsStore()
    store.add("warnings", "first")
    store.add("warnings", "second")
    target_id = store.snapshot()["warnings"][0]["id"]

    assert store.dismiss(target_id) is True
    remaining = store.snapshot()["warnings"]
    assert len(remaining) == 1
    assert remaining[0]["id"] != target_id


def test_dismiss_unknown_id_returns_false() -> None:
    store = DiagnosticsStore()
    store.add("warnings", "first")
    assert store.dismiss("not-a-real-id") is False
    # Existing entry untouched.
    assert len(store.snapshot()["warnings"]) == 1


def test_dismiss_finds_entry_in_correct_level() -> None:
    """Dismiss must search all levels -- the API doesn't get told which."""
    store = DiagnosticsStore()
    store.add("errors", "boom")
    store.add("warnings", "uh oh")
    err_id = store.snapshot()["errors"][0]["id"]

    assert store.dismiss(err_id) is True
    snap = store.snapshot()
    assert len(snap["errors"]) == 0
    assert len(snap["warnings"]) == 1


def test_dismiss_logs_audit_line(caplog: pytest.LogCaptureFixture) -> None:
    """Dismissals should leave an audit trail in the log stream so an operator
    looking back at the journal can tell what was hand-dismissed."""
    import logging

    store = DiagnosticsStore()
    store.add("warnings", "policy violation", source="audit-test")
    target_id = store.snapshot()["warnings"][0]["id"]

    with caplog.at_level(logging.INFO, logger="py20305.diagnostics"):
        store.dismiss(target_id)

    assert any(
        "Diagnostic dismissed" in r.getMessage() and "audit-test" in r.getMessage()
        for r in caplog.records
    )


def test_dedup_key_only_within_same_level() -> None:
    store = DiagnosticsStore()
    store.add("warnings", "m", dedup_key="k")
    store.add("errors", "m", dedup_key="k")

    snap = store.snapshot()
    assert len(snap["warnings"]) == 1
    assert len(snap["errors"]) == 1


def test_clear_empties_all_levels() -> None:
    store = DiagnosticsStore()
    store.add("warnings", "w")
    store.add("errors", "e")
    store.clear()

    snap = store.snapshot()
    assert all(len(snap[level]) == 0 for level in LEVELS)


def test_clear_level_scoped() -> None:
    store = DiagnosticsStore()
    store.add("warnings", "w")
    store.add("errors", "e")
    store.clear_level("warnings")

    snap = store.snapshot()
    assert len(snap["warnings"]) == 0
    assert len(snap["errors"]) == 1


def test_add_rejects_unknown_level() -> None:
    store = DiagnosticsStore()
    with pytest.raises(ValueError):
        store.add("nope", "m")  # type: ignore[arg-type]


def test_clear_level_rejects_unknown_level() -> None:
    store = DiagnosticsStore()
    with pytest.raises(ValueError):
        store.clear_level("nope")  # type: ignore[arg-type]


def test_concurrent_adds_do_not_corrupt_store() -> None:
    store = DiagnosticsStore(max_entries=10_000)
    n_threads = 8
    per_thread = 250

    def worker(tid: int) -> None:
        for i in range(per_thread):
            store.add("info", f"t{tid}-i{i}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = store.snapshot()
    assert len(snap["info"]) == n_threads * per_thread
