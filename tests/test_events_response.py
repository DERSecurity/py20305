"""Tests for DER response building and posting."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from py20305.client.errors import Sep2ConnectionError
from py20305.connectors.base import ConnectorValueError
from py20305.connectors.control_errors import (
    DeviceNotConfiguredError,
    DeviceOfflinePermanentError,
    DeviceOfflineTransientError,
    ModeNotSupportedError,
    OptOutError,
)
from py20305.events.response import (
    ResponseCode,
    ResponseTracker,
    post_der_response,
    response_code_for_dispatch_error,
    response_required_allows,
    translate_code_for_revision,
)
from py20305.models.sep.sep import (
    DateTimeInterval,
    Dercontrol1,
    DercontrolBase,
    EventStatus,
    MRidtype,
    TimeType,
)


def _make_derc(
    mrid_byte: int = 0x01,
    reply_to: str | None = None,
    response_required: bytes = b"\x07",
) -> Dercontrol1:
    return Dercontrol1(
        m_rid=MRidtype(value=bytes([mrid_byte]) * 16),
        creation_time=TimeType(value=900),
        event_status=EventStatus(
            current_status=0,
            date_time=TimeType(value=950),
            potentially_superseded=False,
        ),
        interval=DateTimeInterval(duration=3600, start=TimeType(value=1000)),
        dercontrol_base=DercontrolBase(),
        reply_to=reply_to,
        response_required=response_required,
    )


class TestResponseCodeValues:
    """Verify all IEEE 2030.5 Table 31 response status codes."""

    def test_expired_code(self):
        assert ResponseCode.EXPIRED == 254

    def test_superseded_code(self):
        assert ResponseCode.SUPERSEDED == 7

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("ACKNOWLEDGED", 1),
            ("ACTIVE", 2),
            ("COMPLETED", 3),
            ("OPT_OUT", 4),
            ("OPT_IN", 5),
            ("CANCELLED", 6),
            ("SUPERSEDED", 7),
            ("PARTIAL_OPT_OUT", 8),
            ("PARTIAL_OPT_IN", 9),
            ("COMPLETE_NO_PARTICIPATION", 10),
            ("SUPERSEDED_ALTERNATE_SERVER", 12),
            ("SUPERSEDED_ALTERNATE_PROGRAM", 13),
            ("RESUMED", 14),
            ("NOT_SUPPORTED", 251),
            ("NOT_APPLICABLE", 252),
            ("INVALID", 253),
            ("EXPIRED", 254),
        ],
    )
    def test_table_31_codes(self, name: str, value: int):
        assert ResponseCode[name] == value


_LFDI_A = b"\xaa" * 20
_LFDI_B = b"\xbb" * 20


class TestResponseTracker:
    def test_not_sent_initially(self):
        t = ResponseTracker()
        assert not t.already_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A)

    def test_mark_sent(self):
        t = ResponseTracker()
        t.mark_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A, 1000)
        assert t.already_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A)

    def test_different_codes_independent(self):
        t = ResponseTracker()
        t.mark_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A, 1000)
        assert not t.already_sent(b"\x01" * 16, ResponseCode.ACTIVE, _LFDI_A)

    def test_different_lfdis_independent(self):
        """Same (mrid, code) with different LFDIs are tracked independently."""
        t = ResponseTracker()
        t.mark_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A, 1000)
        assert t.already_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A)
        assert not t.already_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_B)

    def test_prune(self):
        t = ResponseTracker()
        t.mark_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A, 1000)
        t.mark_sent(b"\x02" * 16, ResponseCode.ACTIVE, _LFDI_A, 5000)
        t.prune(8201, max_age=7200)
        assert not t.already_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A)
        assert t.already_sent(b"\x02" * 16, ResponseCode.ACTIVE, _LFDI_A)

    def test_len(self):
        t = ResponseTracker()
        assert len(t) == 0
        t.mark_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A, 1000)
        assert len(t) == 1

    def test_retain_mrids_drops_departed_events(self):
        t = ResponseTracker()
        t.mark_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A, 1000)
        t.mark_sent(b"\x02" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A, 1000)
        # Only the first mRID is still live; the second is dropped regardless of age.
        t.retain_mrids({b"\x01" * 16})
        assert t.already_sent(b"\x01" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A)
        assert not t.already_sent(b"\x02" * 16, ResponseCode.ACKNOWLEDGED, _LFDI_A)


class TestHasResponded:
    def test_false_when_nothing_sent(self):
        assert ResponseTracker().has_responded(b"\x01" * 16) is False

    def test_true_after_any_response_for_mrid(self):
        tracker = ResponseTracker()
        mrid = b"\x01" * 16
        tracker.mark_sent(mrid, ResponseCode.COMPLETED, b"\xaa" * 20, 1000)
        assert tracker.has_responded(mrid) is True
        # different mrid is unaffected
        assert tracker.has_responded(b"\x02" * 16) is False


class TestResponseRequiredAllows:
    """IEEE 2030.5 §8.10.3.1: responses are gated on the responseRequired bits."""

    def test_zero_suppresses_everything(self):
        for code in (
            ResponseCode.ACKNOWLEDGED,
            ResponseCode.ACTIVE,
            ResponseCode.COMPLETED,
            ResponseCode.CANCELLED,
            ResponseCode.SUPERSEDED,
            ResponseCode.EXPIRED,
            ResponseCode.OPT_IN,
        ):
            assert response_required_allows(b"\x00", code) is False

    def test_none_suppresses_everything(self):
        assert response_required_allows(None, ResponseCode.ACTIVE) is False

    def test_message_received_bit_gates_acknowledged_only(self):
        rr = b"\x01"  # bit 0
        assert response_required_allows(rr, ResponseCode.ACKNOWLEDGED) is True
        assert response_required_allows(rr, ResponseCode.ACTIVE) is False
        assert response_required_allows(rr, ResponseCode.COMPLETED) is False
        assert response_required_allows(rr, ResponseCode.EXPIRED) is False

    def test_specific_response_bit_gates_lifecycle_codes(self):
        rr = b"\x02"  # bit 1
        assert response_required_allows(rr, ResponseCode.ACKNOWLEDGED) is False
        for code in (
            ResponseCode.ACTIVE,
            ResponseCode.COMPLETED,
            ResponseCode.CANCELLED,
            ResponseCode.SUPERSEDED,
            ResponseCode.EXPIRED,
        ):
            assert response_required_allows(rr, code) is True

    def test_end_user_bit_gates_opt_codes(self):
        rr = b"\x04"  # bit 2
        assert response_required_allows(rr, ResponseCode.OPT_IN) is True
        assert response_required_allows(rr, ResponseCode.OPT_OUT) is True
        assert response_required_allows(rr, ResponseCode.ACKNOWLEDGED) is False
        assert response_required_allows(rr, ResponseCode.ACTIVE) is False

    def test_csip_seven_allows_all_lifecycle(self):
        rr = b"\x07"  # CSIP: respond at every state
        for code in (
            ResponseCode.ACKNOWLEDGED,
            ResponseCode.ACTIVE,
            ResponseCode.COMPLETED,
            ResponseCode.CANCELLED,
            ResponseCode.SUPERSEDED,
            ResponseCode.EXPIRED,
            ResponseCode.OPT_IN,
        ):
            assert response_required_allows(rr, code) is True


class TestPostDerResponseRespectsResponseRequired:
    """post_der_response honours the responseRequired bitfield end-to-end."""

    @pytest.mark.asyncio
    async def test_zero_skips_post(self):
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        derc = _make_derc(reply_to="/rsps", response_required=b"\x00")
        await post_der_response(http, derc, ResponseCode.ACTIVE, b"\xaa" * 20, ResponseTracker())
        http.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_received_bit_posts_ack_skips_active(self):
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        derc = _make_derc(reply_to="/rsps", response_required=b"\x01")
        tracker = ResponseTracker()
        await post_der_response(http, derc, ResponseCode.ACKNOWLEDGED, b"\xaa" * 20, tracker)
        await post_der_response(http, derc, ResponseCode.ACTIVE, b"\xaa" * 20, tracker)
        assert http.post.await_count == 1  # only ACKNOWLEDGED

    @pytest.mark.asyncio
    async def test_specific_bit_posts_active_skips_ack(self):
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        derc = _make_derc(reply_to="/rsps", response_required=b"\x02")
        tracker = ResponseTracker()
        await post_der_response(http, derc, ResponseCode.ACKNOWLEDGED, b"\xaa" * 20, tracker)
        await post_der_response(http, derc, ResponseCode.ACTIVE, b"\xaa" * 20, tracker)
        assert http.post.await_count == 1  # only ACTIVE

    @pytest.mark.asyncio
    async def test_seven_posts_all(self):
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        derc = _make_derc(reply_to="/rsps", response_required=b"\x07")
        tracker = ResponseTracker()
        for code in (ResponseCode.ACKNOWLEDGED, ResponseCode.ACTIVE, ResponseCode.COMPLETED):
            await post_der_response(http, derc, code, b"\xaa" * 20, tracker)
        assert http.post.await_count == 3


class TestPostDerResponse:
    @pytest.mark.asyncio
    async def test_skips_when_no_reply_to(self):
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        derc = _make_derc()  # no reply_to
        tracker = ResponseTracker()

        await post_der_response(http, derc, ResponseCode.ACKNOWLEDGED, b"\xaa" * 20, tracker)

        http.post.assert_not_awaited()
        assert not tracker.already_sent(derc.m_rid.value, ResponseCode.ACKNOWLEDGED, b"\xaa" * 20)

    @pytest.mark.asyncio
    async def test_posts_to_reply_to(self):
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        await post_der_response(http, derc, ResponseCode.ACTIVE, b"\xaa" * 20, tracker)

        http.post.assert_awaited_once()
        call_args = http.post.call_args
        assert call_args[0][0] == "/rsps"

    @pytest.mark.asyncio
    async def test_duplicate_suppressed(self):
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        await post_der_response(http, derc, ResponseCode.ACKNOWLEDGED, b"\xaa" * 20, tracker)
        await post_der_response(http, derc, ResponseCode.ACKNOWLEDGED, b"\xaa" * 20, tracker)

        assert http.post.await_count == 1

    @pytest.mark.asyncio
    async def test_different_codes_not_suppressed(self):
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        await post_der_response(http, derc, ResponseCode.ACKNOWLEDGED, b"\xaa" * 20, tracker)
        await post_der_response(http, derc, ResponseCode.ACTIVE, b"\xaa" * 20, tracker)

        assert http.post.await_count == 2

    @pytest.mark.asyncio
    async def test_modes_responded_set(self):
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        # Set a mode to verify modesResponded is populated
        from py20305.models.sep.sep import DercontrolBase

        derc.dercontrol_base = DercontrolBase(op_mod_connect=True)
        tracker = ResponseTracker()

        await post_der_response(http, derc, ResponseCode.ACTIVE, b"\xaa" * 20, tracker)

        call_args = http.post.call_args
        response_obj = call_args[0][1]
        assert response_obj.modes_responded is not None
        bitmask = int.from_bytes(response_obj.modes_responded.value, "big")
        assert bitmask & (1 << 2) != 0  # bit 2 = opModConnect

    @pytest.mark.asyncio
    async def test_post_failure_logged(self):
        http = AsyncMock()
        http.post = AsyncMock(side_effect=RuntimeError("connection failed"))
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        # Should not raise
        await post_der_response(http, derc, ResponseCode.ACKNOWLEDGED, b"\xaa" * 20, tracker)
        # And should not be marked as sent
        assert not tracker.already_sent(derc.m_rid.value, ResponseCode.ACKNOWLEDGED, b"\xaa" * 20)

    @pytest.mark.asyncio
    async def test_different_lfdis_post_separately(self):
        """Same (mrid, code) with different LFDIs produces separate posts."""
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        await post_der_response(http, derc, ResponseCode.ACKNOWLEDGED, _LFDI_A, tracker)
        await post_der_response(http, derc, ResponseCode.ACKNOWLEDGED, _LFDI_B, tracker)

        assert http.post.await_count == 2
        # Both are now tracked
        assert tracker.already_sent(derc.m_rid.value, ResponseCode.ACKNOWLEDGED, _LFDI_A)
        assert tracker.already_sent(derc.m_rid.value, ResponseCode.ACKNOWLEDGED, _LFDI_B)


class TestDispatchErrorMapping:
    """Control-dispatch failures map onto Table 31 rejection codes (D2/D3)."""

    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (DeviceNotConfiguredError("nope"), ResponseCode.NOT_APPLICABLE),
            (ModeNotSupportedError("nope"), ResponseCode.NOT_SUPPORTED),
            (DeviceOfflinePermanentError("nope"), ResponseCode.NOT_SUPPORTED),
            (DeviceOfflineTransientError("nope"), ResponseCode.NOT_SUPPORTED),
            (ConnectorValueError("pf 1.1"), ResponseCode.INVALID),
        ],
    )
    def test_typed_errors(self, exc: Exception, expected: ResponseCode):
        assert response_code_for_dispatch_error(exc) == expected

    def test_invalid_value_blames_the_event_not_the_device(self):
        """A parameter outside the profile's range is invalid event data, so it
        reports 253 rather than defaulting to the 251 capability-limit code. No
        device could have applied it, so "not supported" would misplace the
        fault."""
        code = response_code_for_dispatch_error(ConnectorValueError("displacement 1.1"))
        assert code is ResponseCode.INVALID
        assert code != ResponseCode.NOT_SUPPORTED

    def test_untyped_error_falls_through_to_not_supported(self):
        """D2: an unclassified failure is still a failure, not a silent success."""
        assert response_code_for_dispatch_error(RuntimeError("boom")) == ResponseCode.NOT_SUPPORTED

    def test_timeout_is_a_rejection(self):
        """D1: a device that blew the activation ceiling is rejected."""
        assert response_code_for_dispatch_error(TimeoutError()) == ResponseCode.NOT_SUPPORTED


class TestRevisionTranslation:
    """2030.5-2018 reserves 251; 252 carries the 2023 meaning of 251 (D8)."""

    def test_not_supported_becomes_not_applicable_under_2018(self):
        assert (
            translate_code_for_revision(ResponseCode.NOT_SUPPORTED, server_2018_compat=True)
            == ResponseCode.NOT_APPLICABLE
        )

    def test_not_applicable_passes_through_under_2018(self):
        """The two codes collapse onto one wire value; nothing preserves the reason."""
        assert (
            translate_code_for_revision(ResponseCode.NOT_APPLICABLE, server_2018_compat=True)
            == ResponseCode.NOT_APPLICABLE
        )

    def test_no_translation_without_the_flag(self):
        assert (
            translate_code_for_revision(ResponseCode.NOT_SUPPORTED, server_2018_compat=False)
            == ResponseCode.NOT_SUPPORTED
        )

    def test_invalid_passes_through_under_2018(self):
        """253 carries the same meaning in both revisions, so it is exempt from
        the 251 -> 252 collapse. A 2018 head-end can still tell invalid event
        data apart from a capability limit."""
        assert (
            translate_code_for_revision(ResponseCode.INVALID, server_2018_compat=True)
            == ResponseCode.INVALID
        )

    @pytest.mark.parametrize(
        "code",
        [
            ResponseCode.ACKNOWLEDGED,
            ResponseCode.ACTIVE,
            ResponseCode.COMPLETED,
            ResponseCode.OPT_OUT,
            ResponseCode.CANCELLED,
            ResponseCode.SUPERSEDED,
            ResponseCode.EXPIRED,
        ],
    )
    def test_lifecycle_codes_untouched_under_2018(self, code: ResponseCode):
        assert translate_code_for_revision(code, server_2018_compat=True) == code

    @pytest.mark.asyncio
    async def test_post_translates_on_the_wire(self):
        """The translation reaches the posted resource, not just the helper."""
        derc = _make_derc(reply_to="/rsps/1")
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        http.server_2018_compat = True
        tracker = ResponseTracker()

        await post_der_response(
            http, derc, ResponseCode.NOT_SUPPORTED, b"\xaa" * 20, tracker, now_ts=1000
        )

        assert http.post.await_count == 1
        assert http.post.call_args[0][1].status == ResponseCode.NOT_APPLICABLE

    @pytest.mark.asyncio
    async def test_collapsed_codes_dedup_against_each_other_under_2018(self):
        """Both codes share a wire value under 2018, so the server must see it once."""
        derc = _make_derc(reply_to="/rsps/1")
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        http.server_2018_compat = True
        tracker = ResponseTracker()
        lfdi = b"\xaa" * 20

        await post_der_response(http, derc, ResponseCode.NOT_SUPPORTED, lfdi, tracker, now_ts=1000)
        await post_der_response(http, derc, ResponseCode.NOT_APPLICABLE, lfdi, tracker, now_ts=1000)

        assert http.post.await_count == 1


class TestPluginOptOut:
    """A plugin declining an event reports status 4, not a capability limit."""

    def test_opt_out_error_maps_to_status_4(self):
        assert response_code_for_dispatch_error(OptOutError("customer declined")) == (
            ResponseCode.OPT_OUT
        )

    def test_unrecognized_errors_never_become_an_opt_out(self):
        """Only an explicit OptOutError may be reported as a customer decision."""
        for exc in (RuntimeError("boom"), TimeoutError(), ValueError("nope")):
            assert response_code_for_dispatch_error(exc) != ResponseCode.OPT_OUT

    @pytest.mark.asyncio
    async def test_posted_as_status_4_when_the_end_user_bit_is_set(self):
        derc = _make_derc(reply_to="/rsps/1", response_required=b"\x07")
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        http.server_2018_compat = False

        await post_der_response(
            http, derc, ResponseCode.OPT_OUT, b"\xaa" * 20, ResponseTracker(), now_ts=1000
        )

        assert http.post.call_args[0][1].status == ResponseCode.OPT_OUT

    @pytest.mark.asyncio
    async def test_downgrades_to_251_when_the_end_user_bit_is_clear(self):
        """D4: a suppressed opt-out would leave the head-end with silence."""
        derc = _make_derc(reply_to="/rsps/1", response_required=b"\x03")
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        http.server_2018_compat = False

        await post_der_response(
            http, derc, ResponseCode.OPT_OUT, b"\xaa" * 20, ResponseTracker(), now_ts=1000
        )

        assert http.post.await_count == 1
        assert http.post.call_args[0][1].status == ResponseCode.NOT_SUPPORTED

    @pytest.mark.asyncio
    async def test_downgrade_is_itself_translated_under_2018(self):
        """The D4 downgrade target passes through the D8 translation."""
        derc = _make_derc(reply_to="/rsps/1", response_required=b"\x03")
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        http.server_2018_compat = True

        await post_der_response(
            http, derc, ResponseCode.OPT_OUT, b"\xaa" * 20, ResponseTracker(), now_ts=1000
        )

        assert http.post.call_args[0][1].status == ResponseCode.NOT_APPLICABLE

    @pytest.mark.asyncio
    async def test_status_4_is_not_translated_under_2018(self):
        """2018 reserves 251, but status 4 is unchanged between revisions."""
        derc = _make_derc(reply_to="/rsps/1", response_required=b"\x07")
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        http.server_2018_compat = True

        await post_der_response(
            http, derc, ResponseCode.OPT_OUT, b"\xaa" * 20, ResponseTracker(), now_ts=1000
        )

        assert http.post.call_args[0][1].status == ResponseCode.OPT_OUT

    @pytest.mark.asyncio
    async def test_stays_silent_when_no_response_bits_are_set(self):
        """Nothing is requested at all, so the downgrade has nothing to ride."""
        derc = _make_derc(reply_to="/rsps/1", response_required=b"\x00")
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        http.server_2018_compat = False

        await post_der_response(
            http, derc, ResponseCode.OPT_OUT, b"\xaa" * 20, ResponseTracker(), now_ts=1000
        )

        assert http.post.await_count == 0


class TestConcurrentPostsCollapse:
    """The dedup key exists to hold under the fan-out that actually uses it.

    Status 2 is posted per device as each device's apply settles, concurrently.
    Two targets resolving to one LFDI therefore reach the same key at once, and
    a check that is separated from its mark by the POST lets both through --
    the server sees two EventStarted responses for one event.
    """

    @pytest.mark.asyncio
    async def test_two_concurrent_posts_send_one_response(self):
        posted: list[int] = []

        async def post(path, body=None, *a, **kw):
            await asyncio.sleep(0)  # a real POST suspends; that is the window
            posted.append(body.status)

        http = AsyncMock()
        http.post = AsyncMock(side_effect=post)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        await asyncio.gather(
            *[
                post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000)
                for _ in range(2)
            ]
        )

        assert posted == [ResponseCode.ACTIVE.value]

    @pytest.mark.asyncio
    async def test_a_failed_post_can_be_retried(self):
        """A reservation must not outlive a POST that never landed."""
        attempts: list[int] = []

        async def post(path, body=None, *a, **kw):
            attempts.append(body.status)
            if len(attempts) == 1:
                raise Sep2ConnectionError("broker of bad news")

        http = AsyncMock()
        http.post = AsyncMock(side_effect=post)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        await post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000)
        await post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1001)

        assert len(attempts) == 2
        assert tracker.already_sent(derc.m_rid.value, ResponseCode.ACTIVE, _LFDI_A)


class TestCancellationDoesNotLeakAClaim:
    """A claim outliving its POST would silence the response for good.

    ``_in_flight`` is never pruned -- deliberately, since a slow POST must not
    have its key handed to a second caller -- so a claim released only on
    ``Exception`` survives cancellation and every later retry is skipped.
    Shutdown and task cancellation both raise ``CancelledError``, which is not
    an ``Exception``.
    """

    @pytest.mark.asyncio
    async def test_a_cancelled_post_leaves_the_key_claimable(self):
        started = asyncio.Event()

        async def post(path, body=None, *a, **kw):
            started.set()
            await asyncio.Event().wait()  # never returns; the caller cancels us

        http = AsyncMock()
        http.post = AsyncMock(side_effect=post)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        task = asyncio.ensure_future(
            post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not tracker.already_sent(derc.m_rid.value, ResponseCode.ACTIVE, _LFDI_A)

    @pytest.mark.asyncio
    async def test_the_response_still_goes_out_after_a_cancellation(self):
        """The point of not leaking: the next cycle must be able to post."""
        started = asyncio.Event()
        posted: list[int] = []

        async def hang(path, body=None, *a, **kw):
            started.set()
            await asyncio.Event().wait()

        async def succeed(path, body=None, *a, **kw):
            posted.append(body.status)

        http = AsyncMock()
        http.post = AsyncMock(side_effect=hang)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        task = asyncio.ensure_future(
            post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        http.post = AsyncMock(side_effect=succeed)
        await post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1001)

        assert posted == [ResponseCode.ACTIVE.value]


class TestTheLoserCoversAFailedWinner:
    """Suppressing the second post must not mean nobody posts.

    Two targets resolving to one LFDI is a legitimate topology, and the DER
    status-2 path has no retry driver: ``_apply_and_respond`` runs once per
    SCHEDULED -> ACTIVE transition. If the first POST fails and the second
    caller has already been turned away, the server never learns the event
    started -- a worse conformance outcome than the duplicate this replaced.
    """

    @pytest.mark.asyncio
    async def test_the_response_lands_once_when_the_first_post_fails(self):
        attempts: list[int] = []
        landed: list[int] = []

        async def post(path, body=None, *a, **kw):
            attempts.append(body.status)
            if len(attempts) == 1:
                await asyncio.sleep(0)  # let the other caller reach the claim
                raise Sep2ConnectionError("server went away")
            landed.append(body.status)

        http = AsyncMock()
        http.post = AsyncMock(side_effect=post)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        await asyncio.gather(
            *[
                post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000)
                for _ in range(2)
            ]
        )

        assert landed == [ResponseCode.ACTIVE.value], "the event's start must reach the server"
        assert len(attempts) == 2, "the second caller has to take over, not give up"
        assert tracker.already_sent(derc.m_rid.value, ResponseCode.ACTIVE, _LFDI_A)

    @pytest.mark.asyncio
    async def test_a_successful_winner_still_silences_the_loser(self):
        """The whole point of the reservation: one response, not two."""
        posted: list[int] = []

        async def post(path, body=None, *a, **kw):
            await asyncio.sleep(0)
            posted.append(body.status)

        http = AsyncMock()
        http.post = AsyncMock(side_effect=post)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        await asyncio.gather(
            *[
                post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000)
                for _ in range(3)
            ]
        )

        assert posted == [ResponseCode.ACTIVE.value]

    @pytest.mark.asyncio
    async def test_the_winner_does_not_strand_its_losers_on_success(self):
        """A winner that lands still has to wake whoever queued behind it.

        Separated from the test above because the failure is different in kind:
        that one reports a duplicate, this one never returns at all. Bounded so
        the regression is a failure rather than a hung suite.
        """
        posted: list[int] = []

        async def post(path, body=None, *a, **kw):
            await asyncio.sleep(0)
            posted.append(body.status)

        http = AsyncMock()
        http.post = AsyncMock(side_effect=post)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        await asyncio.wait_for(
            asyncio.gather(
                *[
                    post_der_response(
                        http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000
                    )
                    for _ in range(3)
                ]
            ),
            timeout=5.0,
        )

        assert posted == [ResponseCode.ACTIVE.value]
        assert tracker._in_flight == {}


class TestAClaimSurvivesNothing:
    """Everything between the claim and the POST has to be inside the release."""

    @pytest.mark.asyncio
    async def test_a_raise_while_building_the_response_frees_the_claim(self, monkeypatch):
        posted: list[int] = []

        async def post(path, body=None, *a, **kw):
            posted.append(body.status)

        http = AsyncMock()
        http.post = AsyncMock(side_effect=post)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        def explode(*a, **kw):
            raise RuntimeError("modes bitmask")

        monkeypatch.setattr("py20305.events.response.build_modes_responded", explode)
        await post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000)
        assert posted == []

        monkeypatch.undo()
        await post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1001)

        assert posted == [ResponseCode.ACTIVE.value]


class TestTheHandoffUnderCancellation:
    """Cancelling a POST that another caller is parked behind.

    ``TestCancellationDoesNotLeakAClaim`` cancels a post with nobody waiting on
    it and checks the key is claimable afterwards. These cover the case where a
    waiter is already suspended on the winner's future when the cancellation
    arrives, which is the sequence a shutdown produces: the fan-out is mid-flight
    and one of its coroutines is waiting on another. The claim being freed is not
    enough there -- the parked waiter has to be woken, or it waits on a future
    nobody will ever resolve.
    """

    @pytest.mark.asyncio
    async def test_a_cancelled_winner_wakes_the_waiter_parked_behind_it(self):
        started = asyncio.Event()
        landed: list[int] = []

        async def hang(path, body=None, *a, **kw):
            started.set()
            await asyncio.Event().wait()  # never returns; the caller cancels us

        async def succeed(path, body=None, *a, **kw):
            landed.append(body.status)

        http = AsyncMock()
        http.post = AsyncMock(side_effect=hang)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        winner = asyncio.ensure_future(
            post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000)
        )
        await started.wait()
        waiter = asyncio.ensure_future(
            post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000)
        )
        await asyncio.sleep(0)  # let the waiter reach its await on the claim

        http.post = AsyncMock(side_effect=succeed)
        winner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await winner

        # The timeout is the assertion: without the wake-up this never returns.
        await asyncio.wait_for(waiter, timeout=2.0)
        assert landed == [ResponseCode.ACTIVE.value]

    @pytest.mark.asyncio
    async def test_a_cancelled_waiter_leaves_the_winner_alone(self):
        """Cancelling the waiter must not take the in-flight POST down with it."""
        started = asyncio.Event()
        finish = asyncio.Event()
        landed: list[int] = []

        async def slow(path, body=None, *a, **kw):
            started.set()
            await finish.wait()
            landed.append(body.status)

        http = AsyncMock()
        http.post = AsyncMock(side_effect=slow)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        winner = asyncio.ensure_future(
            post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000)
        )
        await started.wait()
        waiter = asyncio.ensure_future(
            post_der_response(http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000)
        )
        await asyncio.sleep(0)

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        finish.set()
        await asyncio.wait_for(winner, timeout=2.0)
        assert landed == [ResponseCode.ACTIVE.value]


class TestOnlyOneWaiterTakesOver:
    """A failed winner promotes one successor, not all of them.

    ``TestTheLoserCoversAFailedWinner`` proves a single waiter takes over. With
    several parked, the handshake has to hand the key to exactly one: waking them
    all to post would reintroduce the duplicate the reservation exists to stop,
    on the very path where the first attempt already failed.
    """

    @pytest.mark.asyncio
    async def test_four_waiters_produce_one_retry_between_them(self):
        attempts: list[int] = []
        landed: list[int] = []

        async def post(path, body=None, *a, **kw):
            attempts.append(body.status)
            await asyncio.sleep(0)  # let the others reach the claim
            if len(attempts) == 1:
                raise Sep2ConnectionError("server went away")
            landed.append(body.status)

        http = AsyncMock()
        http.post = AsyncMock(side_effect=post)
        http.server_2018_compat = False
        derc = _make_derc(reply_to="/rsps")
        tracker = ResponseTracker()

        # Bounded: a handshake that frees the claim without waking the parked
        # waiters would hang here rather than fail, and a hung test tells CI
        # much less than a failed one.
        await asyncio.wait_for(
            asyncio.gather(
                *[
                    post_der_response(
                        http, derc, ResponseCode.ACTIVE, _LFDI_A, tracker, now_ts=1000
                    )
                    for _ in range(5)
                ]
            ),
            timeout=5.0,
        )

        assert len(attempts) == 2, f"one retry expected, got {len(attempts) - 1}"
        assert landed == [ResponseCode.ACTIVE.value]
        assert tracker._in_flight == {}, "a claim outliving the exchange is never pruned"
