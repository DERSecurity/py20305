"""Tests for TariffProcessor: active-interval transitions -> price relays."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from typing import Any

import pytest

from py20305.events.tariff import TariffProcessor

HOUR = 3600


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Any]] = []

    async def relay_schedule_notification(self, lfdis: list[str], notification: Any) -> None:
        self.calls.append((lfdis, notification))


class _FakeHttp:
    def __init__(self, cti_pages: list[Any], fail_times: int = 0) -> None:
        self._pages = cti_pages
        self._fail_times = fail_times
        self.posts: list[tuple[str, Any]] = []

    async def get_list(self, path: str, model_type: type) -> list[Any]:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("transient fetch failure")
        return self._pages

    async def post(self, path: str, obj: Any) -> None:
        self.posts.append((path, obj))


class _FakeTimebase:
    def __init__(self, t: int, per_fsa: dict[str | None, int] | None = None) -> None:
        self.t = t
        self._per_fsa = per_fsa or {}
        self.scopes: list[str | None] = []

    def now(self, fsa_href: str | None = None) -> float:
        self.scopes.append(fsa_href)
        return float(self._per_fsa.get(fsa_href, self.t))


def _cti(price: int, start_value: int = 0, block: int = 0) -> NS:
    return NS(price=price, start_value=start_value, consumption_block=NS(value=block))


def _interval(
    mrid: bytes,
    start: int,
    duration: int = HOUR,
    *,
    creation: int = 0,
    tou: int = 0,
    response_required: bytes = b"\x00",
    reply_to: str | None = None,
) -> NS:
    return NS(
        m_rid=NS(value=mrid),
        interval=NS(start=NS(value=start), duration=duration),
        creation_time=NS(value=creation),
        event_status=NS(current_status=0),
        randomize_start=NS(value=0),
        randomize_duration=NS(value=0),
        tou_tier=NS(value=tou),
        consumption_tariff_interval_list_link=NS(href="/cti"),
        response_required=response_required,
        reply_to=reply_to,
    )


def _state(intervals: list[NS], lfdis: tuple[bytes, ...] = (b"\x11",)) -> NS:
    profile = NS(currency=NS(value=840), price_power_of_ten_multiplier=NS(value=-2))
    rc = NS(href="/tp/1/rc/1", time_tariff_intervals=intervals)
    tp = NS(
        profile=profile,
        href="/tp/1",
        primacy=1,
        rate_components=[rc],
        discovered_from_fsa_href=None,
    )
    end_devices = {str(i): NS(lfdi=lfdi) for i, lfdi in enumerate(lfdis)}
    return NS(end_devices=end_devices, tariff_profiles={"/tp/1": tp})


async def _drain(proc: TariffProcessor) -> None:
    if proc._relay_tasks:
        await asyncio.gather(*list(proc._relay_tasks))


def _make(
    state: NS, cti_pages: list[Any], now: int
) -> tuple[TariffProcessor, _FakeDispatcher, _FakeTimebase]:
    disp = _FakeDispatcher()
    tb = _FakeTimebase(now)
    proc = TariffProcessor(_FakeHttp(cti_pages), state, disp, timebase=tb)
    return proc, disp, tb


@pytest.mark.asyncio
async def test_emits_price_when_interval_active():
    state = _state([_interval(b"\xaa", 0, tou=0)])
    proc, disp, _ = _make(state, [NS(consumption_tariff_interval=[_cti(250000)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)

    assert len(disp.calls) == 1
    lfdis, note = disp.calls[0]
    assert lfdis == ["11"]
    assert note.stream == "price"
    assert note.mrid == "aa"
    assert note.payload["currency"] == 840
    assert note.payload["price_power_of_ten_multiplier"] == -2
    assert note.payload["prices"] == [{"price": 250000, "start_value": 0, "consumption_block": 0}]


@pytest.mark.asyncio
async def test_no_reemit_when_active_interval_unchanged():
    state = _state([_interval(b"\xaa", 0)])
    proc, disp, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)
    await proc.process_tariffs()  # same now, same active interval
    await _drain(proc)
    assert len(disp.calls) == 1


@pytest.mark.asyncio
async def test_reemit_on_transition_to_new_interval():
    state = _state([_interval(b"\xaa", 0), _interval(b"\xbb", HOUR)])
    proc, disp, tb = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)
    tb.t = HOUR + 100  # advance into the second interval
    await proc.process_tariffs()
    await _drain(proc)

    assert [n.mrid for _, n in disp.calls] == ["aa", "bb"]


@pytest.mark.asyncio
async def test_no_active_interval_no_emit():
    state = _state([_interval(b"\xaa", HOUR)])  # active only at [HOUR, 2*HOUR)
    proc, disp, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=0)
    await proc.process_tariffs()
    await _drain(proc)
    assert disp.calls == []


@pytest.mark.asyncio
async def test_broadcasts_to_all_end_device_lfdis():
    state = _state([_interval(b"\xaa", 0)], lfdis=(b"\x11", b"\x22", b"\x33"))
    proc, disp, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)
    assert disp.calls[0][0] == ["11", "22", "33"]


@pytest.mark.asyncio
async def test_no_relay_when_no_end_devices():
    state = _state([_interval(b"\xaa", 0)], lfdis=())
    proc, disp, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)
    assert disp.calls == []


@pytest.mark.asyncio
async def test_fetch_failure_does_not_relay_and_retries_next_poll():
    state = _state([_interval(b"\xaa", 0)])
    http = _FakeHttp([NS(consumption_tariff_interval=[_cti(5)])], fail_times=1)
    disp = _FakeDispatcher()
    proc = TariffProcessor(http, state, disp, timebase=_FakeTimebase(100))
    # First poll: price fetch fails -> no relay, transition NOT recorded.
    await proc.process_tariffs()
    await _drain(proc)
    assert disp.calls == []
    # Next poll: same active interval, fetch now succeeds -> relay fires.
    await proc.process_tariffs()
    await _drain(proc)
    assert len(disp.calls) == 1
    assert disp.calls[0][1].payload["prices"] == [
        {"price": 5, "start_value": 0, "consumption_block": 0}
    ]


@pytest.mark.asyncio
async def test_multiple_rate_components_each_relay():
    profile = NS(currency=NS(value=840), price_power_of_ten_multiplier=NS(value=-2))
    rc1 = NS(href="/tp/1/rc/1", time_tariff_intervals=[_interval(b"\xaa", 0)])
    rc2 = NS(href="/tp/1/rc/2", time_tariff_intervals=[_interval(b"\xbb", 0)])
    tp = NS(
        profile=profile,
        href="/tp/1",
        primacy=1,
        rate_components=[rc1, rc2],
        discovered_from_fsa_href=None,
    )
    state = NS(end_devices={"0": NS(lfdi=b"\x11")}, tariff_profiles={"/tp/1": tp})
    proc, disp, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)
    assert sorted(n.mrid for _, n in disp.calls) == ["aa", "bb"]


@pytest.mark.asyncio
async def test_classifies_against_profile_fsa_clock():
    # Interval is active [0, HOUR) on the global clock, but the profile's FSA
    # clock reads well past it -> classified against /fsa/9 -> not active.
    state = _state([_interval(b"\xaa", 0, HOUR)])
    state.tariff_profiles["/tp/1"].discovered_from_fsa_href = "/fsa/9"
    tb = _FakeTimebase(100, per_fsa={"/fsa/9": 10 * HOUR})
    disp = _FakeDispatcher()
    proc = TariffProcessor(
        _FakeHttp([NS(consumption_tariff_interval=[_cti(1)])]), state, disp, timebase=tb
    )
    await proc.process_tariffs()
    await _drain(proc)
    assert disp.calls == []
    assert "/fsa/9" in tb.scopes  # the profile's FSA scope drove classification


@pytest.mark.asyncio
async def test_no_price_response_when_not_requested():
    # responseRequired defaults to 0x00 -> no acknowledgement posted.
    state = _state([_interval(b"\xaa", 0, reply_to="/rsps")])
    proc, _, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)
    assert proc._http.posts == []


@pytest.mark.asyncio
async def test_price_response_posted_per_lfdi_when_requested():
    # Message-received bit set + replyTo present -> one PriceResponse per device.
    state = _state(
        [_interval(b"\xaa", 0, response_required=b"\x01", reply_to="/rsps")],
        lfdis=(b"\x11", b"\x22"),
    )
    proc, _, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)

    assert [p for p, _ in proc._http.posts] == ["/rsps", "/rsps"]
    responses = [obj for _, obj in proc._http.posts]
    assert {r.end_device_lfdi for r in responses} == {b"\x11", b"\x22"}
    assert all(r.status == 1 for r in responses)  # ACKNOWLEDGED / Event received
    assert all(r.subject.value == b"\xaa" for r in responses)
    assert all(r.created_date_time.value == 100 for r in responses)  # FSA-scoped now


@pytest.mark.asyncio
async def test_price_response_acked_on_receipt_before_activation():
    # A future interval (active only at [HOUR, 2*HOUR)) is acked as soon as it is
    # discovered -- the message-received ack is decoupled from activation -- while
    # no relay fires because it is not yet active.
    state = _state([_interval(b"\xaa", HOUR, response_required=b"\x01", reply_to="/rsps")])
    proc, disp, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=0)
    await proc.process_tariffs()
    await _drain(proc)
    assert [p for p, _ in proc._http.posts] == ["/rsps"]
    assert proc._http.posts[0][1].subject.value == b"\xaa"
    assert disp.calls == []  # not active -> nothing relayed


@pytest.mark.asyncio
async def test_price_response_acked_once_per_mrid():
    # Every discovered interval requesting an ack is acked with its own mRID,
    # regardless of which one is currently active.
    a = _interval(b"\xaa", 0, response_required=b"\x01", reply_to="/rsps")
    b = _interval(b"\xbb", HOUR, response_required=b"\x01", reply_to="/rsps")
    state = _state([a, b])
    proc, _, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)
    assert sorted(r.subject.value for _, r in proc._http.posts) == [b"\xaa", b"\xbb"]


@pytest.mark.asyncio
async def test_price_response_deduped_across_polls():
    # Re-scanning the same interval each poll hits ResponseTracker.already_sent,
    # so the ack is POSTed exactly once.
    state = _state([_interval(b"\xaa", 0, response_required=b"\x01", reply_to="/rsps")])
    proc, _, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)
    await proc.process_tariffs()  # same interval re-scanned -> tracker dedups
    await _drain(proc)
    assert len(proc._http.posts) == 1


@pytest.mark.asyncio
async def test_price_response_retried_after_transient_post_failure():
    # A failed ack POST leaves the tracker entry unset, so the next poll retries.
    state = _state([_interval(b"\xaa", 0, response_required=b"\x01", reply_to="/rsps")])
    proc, _, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)

    calls = {"n": 0}
    orig_post = proc._http.post

    async def flaky_post(path, obj):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient POST failure")
        await orig_post(path, obj)

    proc._http.post = flaky_post  # type: ignore[method-assign]
    await proc.process_tariffs()  # ack POST fails -> not marked sent
    await _drain(proc)
    assert proc._http.posts == []
    await proc.process_tariffs()  # retried, now succeeds
    await _drain(proc)
    assert [r.subject.value for _, r in proc._http.posts] == [b"\xaa"]


@pytest.mark.asyncio
async def test_no_price_response_without_reply_to():
    # responseRequired requests an ack but the server gave no replyTo -> skip.
    state = _state([_interval(b"\xaa", 0, response_required=b"\x01")])
    proc, _, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)
    assert proc._http.posts == []
    assert len(proc._dispatcher.calls) == 1  # relay still happened


@pytest.mark.asyncio
async def test_active_map_pruned_when_rate_component_disappears():
    # A RateComponent relayed once, then removed (e.g. tariff re-walk after server
    # edits), must not leave its href lingering in the transition map.
    state = _state([_interval(b"\xaa", 0)])
    proc, _, _ = _make(state, [NS(consumption_tariff_interval=[_cti(1)])], now=100)
    await proc.process_tariffs()
    await _drain(proc)
    assert "/tp/1/rc/1" in proc._active
    # Replace the profile's rate components with a different href.
    state.tariff_profiles["/tp/1"].rate_components = [
        NS(href="/tp/1/rc/2", time_tariff_intervals=[_interval(b"\xbb", 0)])
    ]
    await proc.process_tariffs()
    await _drain(proc)
    assert "/tp/1/rc/1" not in proc._active  # departed href pruned
    assert "/tp/1/rc/2" in proc._active


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_relay_tasks():
    class _BlockingDispatcher:
        async def relay_schedule_notification(self, lfdis: list[str], notification: Any) -> None:
            await asyncio.sleep(100)

    state = _state([_interval(b"\xaa", 0)])
    proc = TariffProcessor(
        _FakeHttp([NS(consumption_tariff_interval=[_cti(1)])]),
        state,
        _BlockingDispatcher(),
        timebase=_FakeTimebase(100),
    )
    await proc.process_tariffs()  # schedules a relay task that blocks
    assert proc._relay_tasks
    await proc.shutdown()
    assert not proc._relay_tasks
