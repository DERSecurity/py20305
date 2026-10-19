"""Tests for the operations history.

Four layers. The part tests drive each store directly and read back what an
operator would query. The wiring tests run the event processor, the dispatcher
and the telemetry manager with the history attached, because the history is
only useful if the pieces feed it the identifiers that join them. The API
tests read the history the way a dashboard does. The configuration and runner
tests pin that every part is on by default, can be switched off, and that a
history that cannot be opened never stops the client.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from py20305.api.client_routes import create_client_router
from py20305.api.service import ClientAPIService
from py20305.cli import _attach_history
from py20305.config import ClientConfig
from py20305.connectors.base import BaseConnector
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.history import MEMORY, History, HistoryConfig, build_history
from py20305.history.database import HistoryDatabase
from py20305.history.events import EventHistory
from py20305.history.intervals import IntervalHistory
from py20305.history.writes import WriteHistory
from py20305.readings import DirectConnectorSource
from py20305.telemetry.manager import TelemetryManager
from tests.test_audit_trail import (
    HEX_1,
    LFDI_1,
    LFDI_2,
    Site,
    _dderc,
    _derc,
    _device,
    _state,
)

#: Wednesday 2026-10-14 12:00 UTC. A weekday, on an interval boundary.
T0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC).timestamp()
DAY = 86400.0
DEV = "aa" * 20


def _memory(**parts: Any) -> History:
    return History.open(MEMORY, **parts)


def _feed(
    intervals: IntervalHistory,
    start: float,
    end: float,
    w: float,
    *,
    lfdi: str = DEV,
    step: float = 60.0,
    var: float | None = None,
) -> None:
    """Readings every ``step`` seconds from ``start`` to ``end`` inclusive."""
    t = start
    while t <= end:
        values: dict[str, Any] = {"W": w}
        if var is not None:
            values["Var"] = var
        intervals.observe(lfdi, t, values)
        t += step


def _interval(history: History, start: float, lfdi: str = DEV) -> dict[str, Any]:
    assert history.intervals is not None
    (row,) = [
        r for r in history.intervals.query(lfdi=lfdi, since=start, until=start + 1)
        if r["start"] == start
    ]
    return row


# -- Interval history ------------------------------------------------------------


class TestIntervalIntegration:
    def test_a_steady_reading_fills_the_interval_with_its_energy(self):
        history = _memory()
        assert history.intervals is not None
        _feed(history.intervals, T0, T0 + 900, 1000.0, var=-200.0)

        row = _interval(history, T0)
        assert row["covered_seconds"] == 900
        assert row["samples"] == 15
        assert row["w_avg"] == pytest.approx(1000.0)
        assert row["wh_positive"] == pytest.approx(250.0)
        assert row["wh_negative"] == 0
        assert row["varh_negative"] == pytest.approx(-50.0)
        assert row["var_avg"] == pytest.approx(-200.0)

    def test_a_reading_held_across_an_interval_end_is_split_between_intervals(self):
        history = _memory()
        assert history.intervals is not None
        history.intervals.observe(DEV, T0 + 850, {"W": 1200})
        history.intervals.observe(DEV, T0 + 950, {"W": 1200})

        assert _interval(history, T0)["covered_seconds"] == pytest.approx(50)
        assert _interval(history, T0 + 900)["covered_seconds"] == pytest.approx(50)

    def test_a_gap_longer_than_the_limit_counts_as_unmeasured(self):
        history = History.open(MEMORY, max_gap_seconds=300)
        assert history.intervals is not None
        history.intervals.observe(DEV, T0, {"W": 1000})
        history.intervals.observe(DEV, T0 + 400, {"W": 1000})

        row = _interval(history, T0)
        assert row["covered_seconds"] == 0
        assert row["w_avg"] is None
        assert row["samples"] == 2

    def test_a_reading_offered_twice_counts_once(self):
        history = _memory()
        assert history.intervals is not None
        history.intervals.observe(DEV, T0, {"W": 1000})
        history.intervals.observe(DEV, T0, {"W": 1000})

        assert _interval(history, T0)["samples"] == 1

    def test_negative_power_is_kept_apart_from_positive_power(self):
        history = _memory()
        assert history.intervals is not None
        _feed(history.intervals, T0, T0 + 400, 800.0, step=50)
        _feed(history.intervals, T0 + 450, T0 + 900, -400.0, step=50)

        row = _interval(history, T0)
        assert row["wh_positive"] == pytest.approx(100.0)
        assert row["wh_negative"] == pytest.approx(-50.0)
        assert row["w_min"] == -400.0
        assert row["w_max"] == 800.0

    def test_curtailed_energy_is_available_power_minus_output(self):
        history = _memory()
        assert history.intervals is not None
        history.intervals.observe_availability(DEV, T0, {"value": 20, "multiplier": 2})
        _feed(history.intervals, T0, T0 + 900, 1500.0)

        row = _interval(history, T0)
        assert row["w_avail_avg"] == pytest.approx(2000.0)
        assert row["wh_curtailed"] == pytest.approx(125.0)

    def test_curtailment_is_unknown_without_available_power(self):
        history = _memory()
        assert history.intervals is not None
        _feed(history.intervals, T0, T0 + 900, 1500.0)

        assert _interval(history, T0)["wh_curtailed"] is None

    def test_an_interrupted_device_does_not_hold_its_last_reading(self):
        history = _memory()
        assert history.intervals is not None
        history.intervals.observe(DEV, T0, {"W": 1000})
        history.intervals.interrupt(DEV)
        history.intervals.observe(DEV, T0 + 60, {"W": 1000})
        history.intervals.observe(DEV, T0 + 120, {"W": 1000})

        assert _interval(history, T0)["covered_seconds"] == pytest.approx(60)

    def test_an_interval_written_before_a_restart_is_added_to_after_it(self, tmp_path: Path):
        path = tmp_path / "history.sqlite3"
        first = History.open(path)
        assert first.intervals is not None
        _feed(first.intervals, T0, T0 + 420, 1000.0)
        first.close()

        second = History.open(path)
        assert second.intervals is not None
        _feed(second.intervals, T0 + 480, T0 + 900, 1000.0)
        second.close()

        third = History.open(path)
        row = _interval(third, T0)
        # 0-420 before the restart, 480-900 after it; the restart gap is unmeasured.
        assert row["covered_seconds"] == pytest.approx(840)
        assert row["wh_positive"] == pytest.approx(1000 * 840 / 3600)
        third.close()

    @pytest.mark.parametrize("seconds", [0, 7, 7000])
    def test_an_interval_that_does_not_divide_a_day_is_rejected(self, seconds):
        with pytest.raises(ValueError, match="divide a day"):
            IntervalHistory(HistoryDatabase(MEMORY), interval_seconds=seconds)


class TestBaseline:
    def _days(self, history: History, days: range, w: float, *, end: float = 900) -> None:
        assert history.intervals is not None
        # Oldest first, as telemetry arrives: a reading older than the last
        # one is ignored.
        for back in sorted(days, reverse=True):
            _feed(history.intervals, T0 - back * DAY, T0 - back * DAY + end, w)

    def test_the_baseline_is_the_same_interval_on_recent_weekdays(self):
        history = _memory()
        self._days(history, range(1, 22), 1000.0)
        assert history.intervals is not None
        _feed(history.intervals, T0, T0 + 900, 400.0)

        result = history.baseline(T0, T0 + 900)

        assert len(result["days_used"]) == 10
        assert all(
            datetime.fromisoformat(d).weekday() < 5 for d in result["days_used"]
        ), "weekends are left out"
        assert result["days_used"][0] == "2026-10-13", "most recent day first"
        (slot,) = result["slots"]
        assert slot["baseline_w"] == pytest.approx(1000.0)
        assert slot["actual_w"] == pytest.approx(400.0)
        assert slot["delta_w"] == pytest.approx(-600.0)
        assert result["delta_wh"] == pytest.approx(-150.0)

    def test_a_day_with_an_event_under_control_is_left_out(self):
        history = _memory()
        self._days(history, range(1, 22), 1000.0)
        assert history.events is not None
        yesterday = T0 - DAY
        for from_state, to_state, at in (
            (None, "scheduled", yesterday - 600),
            ("scheduled", "active", yesterday),
            ("active", "completed", yesterday + 3600),
        ):
            history.events.record(_transition(HEX_1, from_state, to_state, at, yesterday))

        result = history.baseline(T0, T0 + 900)

        assert "2026-10-13" not in result["days_used"]
        assert result["days_used"][0] == "2026-10-12"

    def test_a_day_measured_for_less_than_the_minimum_is_left_out(self):
        history = _memory()
        self._days(history, range(2, 22), 1000.0)
        self._days(history, range(1, 2), 1000.0, end=300)

        result = history.baseline(T0, T0 + 900)

        assert "2026-10-13" not in result["days_used"]

    def test_without_comparable_days_there_is_no_baseline(self):
        history = _memory()
        assert history.intervals is not None
        _feed(history.intervals, T0, T0 + 900, 400.0)

        result = history.baseline(T0, T0 + 900)

        assert result["days_used"] == []
        assert result["slots"][0]["baseline_w"] is None
        assert result["delta_wh"] is None

    def test_omitting_the_device_sums_every_device(self):
        history = _memory()
        assert history.intervals is not None
        other = "bb" * 20
        for lfdi in (DEV, other):
            for back in range(14, 0, -1):
                _feed(history.intervals, T0 - back * DAY, T0 - back * DAY + 900, 500.0, lfdi=lfdi)
            _feed(history.intervals, T0, T0 + 900, 100.0, lfdi=lfdi)

        result = history.baseline(T0, T0 + 900)

        assert result["devices"] == [DEV, other]
        assert result["slots"][0]["baseline_w"] == pytest.approx(1000.0)
        assert result["slots"][0]["actual_w"] == pytest.approx(200.0)

    def test_a_baseline_needs_the_interval_history(self):
        history = _memory(intervals=False)
        with pytest.raises(RuntimeError, match="switched off"):
            history.baseline(T0, T0 + 900)


# -- Event history -----------------------------------------------------------------


def _transition(
    mrid: str,
    from_state: str | None,
    to_state: str,
    at: float,
    start: float,
    duration: int = 3600,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "kind": "der_event",
        "event_mrid": mrid,
        "program_href": "/derp/1",
        "from_state": from_state,
        "to_state": to_state,
        "effective_start": start,
        "effective_duration": duration,
        "primacy": 1,
        "lfdis": [LFDI_1.hex(), LFDI_2.hex()],
        "at": at,
        **extra,
    }


class TestEventHistory:
    def test_an_event_summary_says_when_it_ran_and_on_which_devices(self):
        history = _memory()
        events = history.events
        assert events is not None
        control = {"description": "Export limit 50%", "control_base": {"opModExpLimW": 5000}}
        events.record(_transition(HEX_1, None, "scheduled", T0 - 600, T0), control=control)
        events.record(
            _transition(
                HEX_1,
                "scheduled",
                "active",
                T0,
                T0,
                applied_lfdis=[LFDI_1.hex()],
                rejected_lfdis=[LFDI_2.hex()],
            )
        )
        events.record(_transition(HEX_1, "active", "completed", T0 + 3600, T0))

        summary = events.event(HEX_1)

        assert summary is not None
        assert summary["state"] == "completed"
        assert summary["control"] == control
        assert summary["active_at"] == T0
        assert summary["ended_at"] == T0 + 3600
        assert summary["ran_seconds"] == 3600
        assert summary["applied_lfdis"] == [LFDI_1.hex()]
        assert summary["rejected_lfdis"] == [LFDI_2.hex()]
        assert [t["to_state"] for t in summary["transitions"]] == [
            "scheduled",
            "active",
            "completed",
        ]

    def test_an_event_still_running_counts_time_up_to_now(self):
        events = EventHistory(HistoryDatabase(MEMORY), clock=lambda: T0 + 600)
        events.record(_transition(HEX_1, None, "active", T0, T0))

        summary = events.event(HEX_1)

        assert summary is not None
        assert summary["ended_at"] is None
        assert summary["ran_seconds"] == 600

    def test_responses_are_counted_by_status_and_failures_apart(self):
        history = _memory()
        events = history.events
        assert events is not None
        events.record(_transition(HEX_1, None, "active", T0, T0))
        for lfdi, status, name, posted in (
            (LFDI_1.hex(), 2, "ACTIVE", True),
            (LFDI_1.hex(), 3, "COMPLETED", True),
            (LFDI_2.hex(), 2, "ACTIVE", False),
        ):
            events.record_response(
                event_mrid=HEX_1,
                lfdi=lfdi,
                status=status,
                status_name=name,
                at=T0,
                posted=posted,
                error=None if posted else "server returned 503",
            )

        summary = events.event(HEX_1)

        assert summary is not None
        assert summary["responses_posted"] == {"ACTIVE": 1, "COMPLETED": 1}
        assert summary["responses_failed"] == 1

    def test_events_are_listed_when_their_time_under_control_overlaps_the_window(self):
        history = _memory()
        events = history.events
        assert events is not None
        other = "02" * 16
        events.record(_transition(HEX_1, None, "scheduled", T0 - 600, T0))
        events.record(_transition(other.upper(), None, "scheduled", T0, T0 + DAY))

        listed = events.events(since=T0 + 1800, until=T0 + 7200)

        assert [e["event_mrid"] for e in listed] == [HEX_1]
        assert [e["event_mrid"] for e in events.events()] == [other.upper(), HEX_1], "newest first"

    def test_rows_older_than_the_retention_period_are_pruned(self):
        database = HistoryDatabase(MEMORY, retention_days=30, clock=lambda: T0)
        events = EventHistory(database)
        events.record(_transition("02" * 16, None, "scheduled", T0 - DAY, T0 - DAY))
        events.record(_transition(HEX_1, None, "scheduled", T0 - 31 * DAY, T0 - 31 * DAY))
        database.prune()

        assert [r["event_mrid"] for r in events.records()] == ["02" * 16]


# -- Write history -----------------------------------------------------------------


class TestWriteHistory:
    def test_writes_are_found_by_the_control_or_the_event_that_caused_them(self):
        history = _memory()
        writes = history.writes
        assert writes is not None
        mrid = b"\x01" * 16
        fallback = b"\x20" * 16
        writes.record("dev1", "p_lim", {"value": 50}, outcome="applied", at=T0,
                      lfdi=LFDI_1.hex().upper(), applied_mrid=mrid, cause_mrid=mrid)
        writes.record("dev1", "p_lim", {"value": 100}, outcome="applied", at=T0 + 3600,
                      lfdi=LFDI_1.hex(), applied_mrid=fallback, cause_mrid=mrid)
        writes.record("dev1", "p_lim", {"value": 70}, outcome="applied", at=T0 + 7200,
                      lfdi=LFDI_1.hex(), origin="local_api")

        found = writes.query(event_mrid=HEX_1)

        assert [w["params"]["value"] for w in found] == [50, 100]
        assert found[0]["lfdi"] == LFDI_1.hex(), "LFDIs are stored in one case"
        assert found[1]["applied_mrid"] == fallback.hex().upper()

    def test_write_times_are_moved_onto_the_server_timebase(self):
        database = HistoryDatabase(MEMORY)
        writes = WriteHistory(database, offset=lambda: 5.0)
        writes.record("dev1", "p_lim", {"value": 1}, outcome="applied", at=T0)

        assert writes.query()[0]["at"] == T0 + 5


class TestDatabaseFailures:
    def test_a_failed_write_is_counted_and_does_not_raise(self):
        database = HistoryDatabase(MEMORY)

        assert database.write("missing", "INSERT INTO missing VALUES (?)", (1,)) is False
        assert database.write_failures == {"missing": 1}

    def test_a_closed_history_accepts_writes_without_storing_them(self):
        history = _memory()
        history.close()
        assert history.events is not None

        assert history.events.record(_transition(HEX_1, None, "scheduled", T0, T0)) is False
        assert history.events.records() == []


# -- Wiring: processor, dispatcher, telemetry ------------------------------------------


class TestEventProcessorFeedsTheHistory:
    def _site(self, *, audit: bool, failing: set[bytes] | None = None) -> tuple[Site, History]:
        now = int(time.time())
        state = _state([_derc(0x01, start=now + 100)], dderc=_dderc())
        _device(state, "/edev/2", LFDI_2)
        state.device_mapping.add("/derp/1", "/edev/2")
        site = Site(state, audit=audit, failing=failing)
        history = _memory()
        site.audit.attach_history(history.events)
        site.processor.attach_event_history(history.events)
        site.dispatcher.attach_write_history(history.writes)
        return site, history

    async def _run(self, site: Site) -> None:
        await site.processor.process_controls("/derp/1")
        record = site.processor._store.get(b"\x01" * 16)
        await site.processor._on_activation(record)
        await site.processor._on_completion(record)

    @pytest.mark.asyncio
    async def test_the_lifecycle_is_kept_without_publishing_anything(self):
        site, history = self._site(audit=False)
        await self._run(site)

        assert site.forwarder.audit_records() == [], "the audit trail is not published"
        assert history.events is not None
        summary = history.events.event(HEX_1)
        assert summary is not None
        assert [t["to_state"] for t in summary["transitions"]] == [
            "scheduled",
            "active",
            "completed",
        ]
        assert summary["control"]["control_base"]["op_mod_max_lim_w"] is not None
        assert sorted(summary["applied_lfdis"]) == sorted([LFDI_1.hex(), LFDI_2.hex()])
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_published_records_keep_their_shape_when_the_history_is_on(self):
        site, history = self._site(audit=True)
        await self._run(site)

        published = site.forwarder.audit_records("der_event")
        assert published and all("control" not in r for r in published)
        assert history.events is not None
        stored = history.events.records(event_mrid=HEX_1)
        assert "control" in stored[0], "the first stored record describes the control"
        assert all("control" not in r for r in stored[1:])
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_each_posted_response_is_kept_per_device(self):
        site, history = self._site(audit=False)
        await self._run(site)

        assert history.events is not None
        kept = {(r["lfdi"], r["status"]) for r in history.events.responses(event_mrid=HEX_1)}
        assert kept == {
            (lfdi.hex(), status) for lfdi in (LFDI_1, LFDI_2) for status in (1, 2, 3)
        }, "ACKNOWLEDGED, ACTIVE and COMPLETED for each device"
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_response_that_failed_to_post_is_kept_with_its_error(self):
        site, history = self._site(audit=False)
        site.http.post = AsyncMock(side_effect=RuntimeError("server returned 503"))
        await self._run(site)

        assert history.events is not None
        failed = [r for r in history.events.responses(event_mrid=HEX_1) if not r["posted"]]
        assert failed
        assert all(r["error"] == "server returned 503" for r in failed)
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_device_writes_name_the_event_including_a_rejected_one(self):
        site, history = self._site(audit=False, failing={LFDI_2})
        await self._run(site)

        assert history.writes is not None
        writes = history.writes.query(event_mrid=HEX_1)
        outcomes = {(w["lfdi"], w["outcome"]) for w in writes}
        assert (LFDI_1.hex(), "applied") in outcomes
        assert (LFDI_2.hex(), "failed") in outcomes
        rejected = next(w for w in writes if w["outcome"] == "failed")
        assert rejected["error"] == "register locked"
        assert rejected["applied_mrid"] == HEX_1
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_write_the_gate_refused_is_kept_as_refused(self):
        class Deny:
            def may_command(self, lfdi: str, origin: str) -> bool:
                return False

        now = int(time.time())
        site = Site(_state([_derc(0x01, start=now - 10)]), audit=False, gate=Deny())
        history = _memory()
        site.dispatcher.attach_write_history(history.writes)

        await site.processor.process_controls("/derp/1")

        assert history.writes is not None
        (write,) = history.writes.query(event_mrid=HEX_1)
        assert write["outcome"] == "refused"
        assert write["error_type"] == "refused"
        await site.processor.shutdown()


class TestTelemetryFeedsTheIntervals:
    @pytest.mark.asyncio
    async def test_each_metering_cycle_adds_a_reading_and_available_power(self):
        base = time.time()
        times = iter([base, base + 60])
        connector = AsyncMock(spec=BaseConnector)
        connector.fetch_monitoring = AsyncMock(return_value={"W": 1000.0, "Var": 0.0})
        connector.fetch_status = AsyncMock(return_value={"alarmStatus": 0})
        connector.fetch_availability = AsyncMock(
            return_value={"statWAvail": {"value": 4000, "multiplier": 0}}
        )

        async def resolve(lfdi: str) -> BaseConnector:
            return connector

        client = AsyncMock()
        client.post_bytes = AsyncMock(return_value="/mup/1")
        client.put_bytes = AsyncMock(return_value=204)
        history = _memory()
        manager = TelemetryManager(
            client,
            "/mup",
            resolve,
            source=DirectConnectorSource(resolve, clock=lambda: next(times)),
            interval_history=history.intervals,
        )
        manager.start_metering(DEV, post_rate=300)
        manager._devices[DEV].der_availability_href = "/dera/1"

        await manager._metering_cycle(DEV)
        await manager._metering_cycle(DEV)
        await manager.shutdown()

        assert history.intervals is not None
        rows = history.intervals.query(lfdi=DEV)
        assert sum(r["samples"] for r in rows) == 2
        assert sum(r["covered_seconds"] for r in rows) == pytest.approx(60)
        assert sum(r["wh_curtailed"] or 0 for r in rows) == pytest.approx(3000 * 60 / 3600)


# -- API -------------------------------------------------------------------------------


def _api(history: History | None) -> TestClient:
    client = MagicMock()
    client.history = history
    service = ClientAPIService(client=client)
    app = FastAPI()
    app.include_router(create_client_router(lambda: service), prefix="/api/v1")
    return TestClient(app)


class TestHistoryApi:
    def test_status_lists_the_parts_that_are_on(self):
        api = _api(_memory(writes=False))

        body = api.get("/api/v1/history").json()

        assert body["enabled"] is True
        assert (body["events"], body["writes"], body["intervals"]) == (True, False, True)

    def test_every_endpoint_says_when_there_is_no_history(self):
        api = _api(None)

        assert api.get("/api/v1/history").json() == {"enabled": False}
        assert api.get("/api/v1/history/events").json() == {"enabled": False, "events": []}
        assert api.get("/api/v1/history/writes").json() == {"enabled": False, "writes": []}
        assert api.get("/api/v1/history/intervals").json()["enabled"] is False

    def test_an_event_is_returned_with_its_records_responses_and_writes(self):
        history = _memory()
        assert history.events is not None and history.writes is not None
        history.events.record(_transition(HEX_1, None, "active", T0, T0))
        history.writes.record(
            "dev1", "p_lim", {"value": 1}, outcome="applied", at=T0, applied_mrid=b"\x01" * 16
        )
        api = _api(history)

        body = api.get(f"/api/v1/history/events/{HEX_1.lower()}").json()

        assert body["event"]["event_mrid"] == HEX_1
        assert len(body["records"]) == 1
        assert len(body["writes"]) == 1

    def test_an_unknown_event_is_not_found(self):
        response = _api(_memory()).get("/api/v1/history/events/" + "0F" * 16)

        assert response.status_code == 404

    def test_a_baseline_with_an_unknown_time_zone_is_a_bad_request(self):
        response = _api(_memory()).get(
            "/api/v1/history/baseline",
            params={"start": T0, "end": T0 + 900, "tz": "Mars/Olympus_Mons"},
        )

        assert response.status_code == 400

    def test_a_baseline_that_ends_before_it_starts_is_a_bad_request(self):
        response = _api(_memory()).get(
            "/api/v1/history/baseline", params={"start": T0, "end": T0 - 900}
        )

        assert response.status_code == 400


# -- Configuration and runner ------------------------------------------------------------


class TestHistoryConfig:
    def test_every_part_is_on_by_default(self):
        config = HistoryConfig()

        assert config.enabled and config.any_part_enabled
        assert config.events.enabled and config.writes.enabled and config.intervals.enabled
        assert config.intervals.interval_seconds == 900

    def test_switching_the_history_off_opens_nothing(self, tmp_path: Path):
        config = HistoryConfig(enabled=False, path=tmp_path / "h.sqlite3")

        assert build_history(config) is None
        assert not (tmp_path / "h.sqlite3").exists()

    def test_each_part_can_be_switched_off_on_its_own(self, tmp_path: Path):
        config = HistoryConfig(
            path=tmp_path / "h.sqlite3", events={"enabled": False}, intervals={"enabled": False}
        )

        history = build_history(config)

        assert history is not None
        assert history.events is None and history.intervals is None
        assert history.writes is not None
        history.close()

    def test_switching_every_part_off_is_the_same_as_switching_the_history_off(self):
        config = HistoryConfig(
            events={"enabled": False}, writes={"enabled": False}, intervals={"enabled": False}
        )

        assert build_history(config) is None

    @pytest.mark.parametrize("seconds", [30, 7, 1000])
    def test_an_interval_that_does_not_divide_a_day_is_a_configuration_error(self, seconds):
        with pytest.raises(ValidationError):
            HistoryConfig(intervals={"interval_seconds": seconds})

    def test_a_misspelled_key_is_a_configuration_error(self):
        with pytest.raises(ValidationError):
            HistoryConfig.model_validate({"retention_dayz": 10})

    def test_the_client_configuration_carries_the_history_block(self, tmp_path: Path):
        config = ClientConfig.model_validate(
            {
                "server": {"url": "https://server.example.com:8443"},
                "tls": {
                    "client_cert": str(tmp_path / "c.pem"),
                    "client_key": str(tmp_path / "c.key"),
                    "ca_cert": str(tmp_path / "ca.pem"),
                },
                "history": {"writes": {"enabled": False}, "retention_days": 90},
            }
        )

        assert config.history.writes.enabled is False
        assert config.history.retention_days == 90


class _Client:
    """Records what the runner attaches."""

    def __init__(self) -> None:
        self.dispatcher = ConnectorDispatcher(MagicMock(), lambda href: None)
        self.history: History | None = None

    def attach_history(self, history: History | None) -> None:
        self.history = history


class TestRunnerAttachesTheHistory:
    def _config(self, tmp_path: Path, history: dict[str, Any]) -> ClientConfig:
        return ClientConfig.model_validate(
            {
                "server": {"url": "https://server.example.com:8443"},
                "tls": {
                    "client_cert": str(tmp_path / "c.pem"),
                    "client_key": str(tmp_path / "c.key"),
                    "ca_cert": str(tmp_path / "ca.pem"),
                },
                "history": history,
            }
        )

    def test_the_default_history_feeds_the_client_and_the_dispatcher(self, tmp_path: Path):
        client = _Client()
        _attach_history(client, self._config(tmp_path, {"path": str(tmp_path / "h.sqlite3")}))  # type: ignore[arg-type]

        assert client.history is not None
        assert client.dispatcher._write_history is client.history.writes
        assert (tmp_path / "h.sqlite3").exists()
        client.history.close()

    def test_a_history_that_cannot_be_opened_is_reported_and_left_out(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        client = _Client()
        # A directory where the file should be: SQLite cannot open it.
        config = self._config(tmp_path, {"path": str(tmp_path)})

        with caplog.at_level(logging.ERROR, logger="py20305.cli"):
            _attach_history(client, config)  # type: ignore[arg-type]

        assert client.history is None
        assert "history.enabled: false" in caplog.text

    def test_a_disabled_history_attaches_nothing(self, tmp_path: Path):
        client = _Client()
        _attach_history(  # type: ignore[arg-type]
            client, self._config(tmp_path, {"enabled": False, "path": str(tmp_path / "h.db")})
        )

        assert client.history is None
        assert client.dispatcher._write_history is None
        assert not (tmp_path / "h.db").exists()


class TestRunnerClosesTheHistory:
    @pytest.mark.asyncio
    async def test_the_runner_closes_the_history_when_it_stops(self, tmp_path: Path):
        from py20305 import cli as cli_module
        from tests.test_device_telemetry import LFDI_A, TestRunnerWiring

        client = MagicMock()
        client.http.forwarder = None
        client.connect = AsyncMock()
        client.run = AsyncMock(side_effect=lambda: asyncio.sleep(0))
        client.shutdown = AsyncMock()
        client.poll_now = AsyncMock()
        client.history = None
        client.attach_history = lambda history: setattr(client, "history", history)
        config = TestRunnerWiring._config(tmp_path, None).model_copy(
            update={"history": HistoryConfig(path=tmp_path / "h.sqlite3")}
        )

        with (
            patch.object(cli_module, "build_client", return_value=(client, LFDI_A)),
            patch.object(cli_module, "_start_telemetry", return_value=None),
            patch.object(cli_module, "_register_if_needed", new=AsyncMock()),
            patch.object(cli_module, "_install_signal_handlers"),
        ):
            await cli_module.run(config)

        history = client.history
        assert isinstance(history, History)
        assert history.events is not None
        assert history.events.record(_transition(HEX_1, None, "scheduled", T0, T0)) is False, (
            "the file is closed once the runner stops"
        )
