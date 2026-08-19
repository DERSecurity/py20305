"""TimeTariffInterval (Pricing) active-interval selection.

A parallel path to the DERControl event processor (see the D-EVENT decision in
the Pricing plan): it reuses the shared randomization primitive
(:class:`RandomizationCache`) but has its own selection rule. Unlike a DERControl
-- which carries setpoint modes and is superseded per control mode -- a tariff
schedule is a time series where exactly one interval is in effect at any moment.
The active interval is the one whose effective time window contains ``now``;
overlaps (should not normally occur within one RateComponent) are resolved by
``creationTime`` (newest wins). The live DERControl processor is untouched.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from py20305.connectors.base import ScheduleNotification
from py20305.events.randomization import RandomizationCache
from py20305.events.response import (
    ResponseCode,
    ResponseTracker,
    post_price_response,
    response_required_allows,
)
from py20305.models.sep.sep import ConsumptionTariffIntervalList, TimeTariffInterval1

# Type-only imports from py20305.client.* are deferred: importing any
# client submodule runs client/__init__, which eagerly imports CsipClient, which
# imports this module -> a cycle when events.tariff is imported first. Keeping
# these under TYPE_CHECKING (and lazy-importing ServerTimebase in __init__) lets
# events.tariff import standalone.
if TYPE_CHECKING:
    from py20305.client.http import Sep2Client
    from py20305.client.state import (
        DiscoveredState,
        TariffProfileState,
        TariffRateComponentState,
    )
    from py20305.client.timebase import ServerTimebase
    from py20305.events.dispatch import ControlDispatcher

logger = logging.getLogger(__name__)

# EventStatus.currentStatus values that mean the interval is not in effect:
# 2 = Cancelled, 3 = Cancelled with randomization, 4 = Superseded.
_INACTIVE_STATUSES = frozenset({2, 3, 4})


def _scalar(value: Any) -> Any:
    """Return ``value.value`` for wrapped scalar model types, else ``value``.

    None-safe. Lets the price payload carry plain ints/enums whether the field
    is a wrapped type (CurrencyCode, PowerOfTenMultiplierType, ...) or a bare int.
    """
    if value is None:
        return None
    return getattr(value, "value", value)


def effective_window(interval: TimeTariffInterval1, cache: RandomizationCache) -> tuple[int, int]:
    """Return the ``(start, end)`` epoch window of *interval* after randomization."""
    raw_start = interval.interval.start.value
    raw_duration = interval.interval.duration
    start_offset, dur_offset = cache.get_offsets(interval, raw_start, raw_duration)
    start = raw_start + start_offset
    end = start + raw_duration + dur_offset
    return start, end


def _is_cancelled(interval: TimeTariffInterval1) -> bool:
    status = interval.event_status
    if status is None:
        return False
    return status.current_status in _INACTIVE_STATUSES


def active_interval(
    intervals: Iterable[TimeTariffInterval1], now: int, cache: RandomizationCache
) -> TimeTariffInterval1 | None:
    """Return the TimeTariffInterval active at *now*, or ``None``.

    An interval is active when its effective (post-randomization) window contains
    *now* and it is not cancelled/superseded. Overlapping active intervals are
    resolved by ``creationTime`` (newest wins), then by later effective start.
    """
    best: TimeTariffInterval1 | None = None
    best_key: tuple[int, int] | None = None
    for interval in intervals:
        if _is_cancelled(interval):
            continue
        start, end = effective_window(interval, cache)
        if not (start <= now < end):
            continue
        creation = interval.creation_time.value if interval.creation_time is not None else 0
        key = (creation, start)
        if best_key is None or key > best_key:
            best, best_key = interval, key
    return best


class TariffProcessor:
    """Emit price relays as the active TimeTariffInterval changes.

    Parallel to the DERControl EventProcessor (D-EVENT): it runs the
    active-interval selection per RateComponent and, on a transition, fetches the
    interval's ConsumptionTariffInterval prices and relays a
    ``ScheduleNotification(stream="price")``. Pricing is global, so the relay
    broadcasts to every discovered EndDevice LFDI. Relay-only -- it does not
    actuate; the connector's ``notification_price`` hook decides what to do.

    Driven by the poll loop (wired in a later increment). The live DERControl
    processor is untouched.
    """

    def __init__(
        self,
        http: Sep2Client,
        state: DiscoveredState,
        dispatcher: ControlDispatcher,
        *,
        timebase: ServerTimebase | None = None,
    ) -> None:
        self._http = http
        self._state = state
        self._dispatcher = dispatcher
        if timebase is None:
            # Lazy import to avoid the client/__init__ import cycle (see top).
            from py20305.client.timebase import ServerTimebase

            timebase = ServerTimebase()
        self._timebase = timebase
        self._randomization = RandomizationCache()
        #: rate-component href -> mRID of the last-relayed active interval (or
        #: None). Used to fire the relay only on a transition.
        self._active: dict[str, bytes | None] = {}
        self._relay_tasks: set[asyncio.Task[None]] = set()
        #: Dedups PriceResponse posts across polls (per mRID/code/LFDI), shared
        #: with the DER response path's tracker semantics.
        self._response_tracker = ResponseTracker()

    async def process_tariffs(self) -> None:
        """Ack discovered intervals and relay the active interval per RateComponent."""
        # Bound the randomization cache to live events (mirrors the DERControl
        # processor). Prune against the MINIMUM clock across all tariff scopes so
        # an interval still active under a lagging per-FSA clock isn't evicted --
        # eviction would recompute its randomization offset mid-interval, breaking
        # session determinism.
        scopes = {tp.discovered_from_fsa_href for tp in self._state.tariff_profiles.values()} or {
            None
        }
        self._randomization.prune(min(int(self._timebase.now(scope)) for scope in scopes))
        all_lfdis = [ed.lfdi.hex() for ed in self._state.end_devices.values()]
        if not all_lfdis:
            # No downstream devices yet: no LFDI to ack or relay to. Don't record
            # transitions, so once devices are present the active price is relayed.
            return
        live_mrids: set[bytes] = set()
        live_rc_hrefs: set[str] = set()
        for tp_state in self._state.tariff_profiles.values():
            # IEEE 9.2.3: classify a tariff's events against its discovering FSA's
            # clock (per-FSA Time), falling back to the global timebase when the
            # profile has no FSA attribution -- matching the DERControl processor.
            now = int(self._timebase.now(tp_state.discovered_from_fsa_href))
            for rc_state in tp_state.rate_components:
                live_rc_hrefs.add(rc_state.href)
                for interval in rc_state.time_tariff_intervals:
                    live_mrids.add(interval.m_rid.value)
                    # Ack-on-receipt (IEEE 2030.5 responseRequired bit 0): a
                    # message-received response is due for every discovered
                    # interval that requests one, independent of whether/when it
                    # becomes active -- mirrors the DERControl discovery ack.
                    await self._ack_received(interval, now)
                active = active_interval(rc_state.time_tariff_intervals, now, self._randomization)
                await self._relay_if_changed(tp_state, rc_state, active, all_lfdis)
        # Dedup is scoped to live intervals: forget acks for intervals the server
        # no longer serves (so a recreated mRID re-acks), while a still-present
        # long interval is never re-acked (unlike an age-based prune).
        self._response_tracker.retain_mrids(live_mrids)
        # Likewise bound the transition map to live rate components so a schedule
        # whose RateComponents changed (server edits, re-walk) doesn't leak hrefs.
        self._active = {href: mrid for href, mrid in self._active.items() if href in live_rc_hrefs}

    async def _relay_if_changed(
        self,
        tp_state: TariffProfileState,
        rc_state: TariffRateComponentState,
        interval: TimeTariffInterval1 | None,
        all_lfdis: list[str],
    ) -> None:
        prev = self._active.get(rc_state.href)
        current = interval.m_rid.value if interval is not None else None
        if current == prev:
            return
        if interval is None:
            # Active window ended with no successor (a schedule gap) -- nothing to
            # price. Record the transition so the next active interval relays.
            self._active[rc_state.href] = None
            return
        prices = await self._fetch_prices(interval)
        if prices is None:
            # Transient price fetch failure: do NOT record the transition, so the
            # next poll retries rather than leaving a permanent price-less relay.
            return
        self._active[rc_state.href] = current
        self._emit(self._build_notification(tp_state, rc_state, interval, prices, all_lfdis))

    async def _ack_received(self, interval: TimeTariffInterval1, now: int) -> None:
        """Post a message-received PriceResponse per EndDevice for a discovered interval.

        Fires when the interval's responseRequired requests a message-received
        acknowledgement (IEEE 2030.5 responseRequired bit 0; Table "Response types
        by function set": Price -> Event received). One response per downstream
        LFDI mirrors the DER multi-device pattern; the tracker dedups repeats, so
        re-scanning each poll acks a given interval exactly once. A transiently-
        failed POST leaves its tracker entry unset and is retried next poll.
        """
        if not response_required_allows(interval.response_required, ResponseCode.ACKNOWLEDGED):
            return
        for ed in self._state.end_devices.values():
            await post_price_response(
                self._http,
                interval,
                ResponseCode.ACKNOWLEDGED,
                ed.lfdi,
                self._response_tracker,
                now_ts=now,
            )

    async def _fetch_prices(self, interval: TimeTariffInterval1) -> list[dict[str, Any]] | None:
        """Return the priced consumption blocks, ``[]`` if the interval has none,
        or ``None`` if the fetch failed (so the caller can retry next poll)."""
        link = interval.consumption_tariff_interval_list_link
        cti_href = link.href if link is not None else None
        if not cti_href:
            return []
        try:
            pages = await self._http.get_list(cti_href, ConsumptionTariffIntervalList)
        except Exception:
            logger.warning(
                "Failed to fetch ConsumptionTariffInterval prices at %s", cti_href, exc_info=True
            )
            return None
        blocks: list[dict[str, Any]] = []
        for page in pages:
            for cti in page.consumption_tariff_interval:
                blocks.append(
                    {
                        "price": cti.price,
                        "start_value": cti.start_value,
                        "consumption_block": _scalar(cti.consumption_block),
                    }
                )
        return blocks

    def _build_notification(
        self,
        tp_state: TariffProfileState,
        rc_state: TariffRateComponentState,
        interval: TimeTariffInterval1,
        prices: list[dict[str, Any]],
        all_lfdis: list[str],
    ) -> ScheduleNotification:
        start, end = effective_window(interval, self._randomization)
        profile = tp_state.profile
        current_status = (
            interval.event_status.current_status if interval.event_status is not None else None
        )
        randomization = (
            interval.randomize_start.value if interval.randomize_start is not None else None,
            interval.randomize_duration.value if interval.randomize_duration is not None else None,
        )
        payload: dict[str, Any] = {
            "tou_tier": _scalar(interval.tou_tier),
            "currency": _scalar(profile.currency),
            "price_power_of_ten_multiplier": _scalar(profile.price_power_of_ten_multiplier),
            "rate_component_href": rc_state.href,
            "prices": prices,
        }
        return ScheduleNotification(
            stream="price",
            transition="active",
            status="active",
            current_status=current_status,
            mrid=interval.m_rid.value.hex(),
            program_href=tp_state.href,
            primacy=tp_state.primacy,
            start=start,
            duration=end - start,
            end=end,
            affected_lfdis=all_lfdis,
            randomization=randomization,
            payload=payload,
        )

    def _emit(self, notification: ScheduleNotification) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - no running loop
            return
        task = loop.create_task(
            self._dispatcher.relay_schedule_notification(
                list(notification.affected_lfdis), notification
            )
        )
        self._relay_tasks.add(task)
        task.add_done_callback(self._relay_tasks.discard)

    async def shutdown(self) -> None:
        """Cancel in-flight relay tasks so teardown is clean.

        Mirrors ``EventProcessor.shutdown`` -- avoids "Task was destroyed but it
        is pending" warnings when the poll loop tears down.
        """
        for task in list(self._relay_tasks):
            task.cancel()
        if self._relay_tasks:
            await asyncio.gather(*self._relay_tasks, return_exceptions=True)
