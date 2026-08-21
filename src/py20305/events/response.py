"""DER response building and posting to the IEEE 2030.5 server."""

from __future__ import annotations

import enum
import logging
import time
from typing import TYPE_CHECKING

from py20305.events.modes_bitmask import build_modes_responded

if TYPE_CHECKING:
    from py20305.client.http import Sep2Client
from py20305.connectors.base import ConnectorValueError
from py20305.connectors.control_errors import (
    DeviceNotConfiguredError,
    DeviceOfflinePermanentError,
    DeviceOfflineTransientError,
    InvalidControlValueError,
    ModeNotSupportedError,
    OptOutError,
)
from py20305.models.sep.sep import (
    Dercontrol1,
    DercontrolResponse,
    MRidtype,
    PriceResponse,
    TimeTariffInterval1,
    TimeType,
)

logger = logging.getLogger(__name__)


class ResponseCode(enum.IntEnum):
    """IEEE 2030.5 Table 31 response status codes for DER function set."""

    ACKNOWLEDGED = 1
    ACTIVE = 2
    COMPLETED = 3
    OPT_OUT = 4
    OPT_IN = 5
    CANCELLED = 6
    SUPERSEDED = 7
    PARTIAL_OPT_OUT = 8
    PARTIAL_OPT_IN = 9
    COMPLETE_NO_PARTICIPATION = 10
    SUPERSEDED_ALTERNATE_SERVER = 12
    SUPERSEDED_ALTERNATE_PROGRAM = 13
    RESUMED = 14
    NOT_SUPPORTED = 251
    NOT_APPLICABLE = 252
    INVALID = 253
    EXPIRED = 254


# IEEE 2030.5 §8.10.3.1 responseRequired bitfield. A response is only POSTed
# when the bit governing its status code is set in the event's responseRequired
# attribute. CSIP servers set 0x07 (all bits) to request a response at every
# state; an explicit 0x00 (or absent attribute, which xsdata defaults to 0x00)
# means no responses are requested.
_RR_MESSAGE_RECEIVED = 0x01  # bit 0: indicate the message was received
_RR_SPECIFIC_RESPONSE = 0x02  # bit 1: indicate specific responses (state changes)
_RR_END_USER_RESPONSE = 0x04  # bit 2: end-user / customer response (opt in/out)

_END_USER_CODES = frozenset(
    {
        ResponseCode.OPT_OUT,
        ResponseCode.OPT_IN,
        ResponseCode.PARTIAL_OPT_OUT,
        ResponseCode.PARTIAL_OPT_IN,
    }
)


def _required_bit_for(code: ResponseCode) -> int:
    """Return the responseRequired bit that governs *code* (§8.10.3.1)."""
    if code == ResponseCode.ACKNOWLEDGED:
        return _RR_MESSAGE_RECEIVED
    if code in _END_USER_CODES:
        return _RR_END_USER_RESPONSE
    # Event-lifecycle codes (Active, Completed, Cancelled, Superseded, Expired,
    # ...) are "specific responses".
    return _RR_SPECIFIC_RESPONSE


def response_required_allows(response_required: bytes | None, code: ResponseCode) -> bool:
    """Whether the event's responseRequired bitfield requests a *code* response.

    IEEE 2030.5 §8.10.3.1: a client SHALL only POST a response when the
    corresponding bit is set. ``None``/empty/``0x00`` all mean "no bits set" ->
    no response requested.
    """
    flags = int.from_bytes(response_required or b"\x00", "big")
    return bool(flags & _required_bit_for(code))


#: Control-dispatch outcome -> IEEE 2030.5 Table 31 code.
#:
#: ``OptOutError`` is the odd one out: it reports a *decision* the connector made
#: on the customer's behalf, so it maps to status 4 rather than to a capability
#: limit. The rest report an inability, and all but ``DeviceNotConfiguredError``
#: and ``ConnectorValueError`` collapse onto "not supported" because Table 31 has
#: no code for a device that is merely unreachable right now -- a transient outage
#: therefore reports the same code as a permanent one.
#:
#: ``ConnectorValueError`` is the one entry that blames the *event* rather than
#: the device: a control parameter outside the range the profile permits could
#: not have been applied by any device, so it reports 253 (Invalid) instead of
#: defaulting to the 251 fall-through below. It is raised by the in-process
#: connectors; ``InvalidControlValueError`` is the same condition arriving from a
#: an out-of-process connector, and both must map to the same code.
#:
#: A rejection says the client cannot claim the event started, not that it
#: gave up applying the control: a dispatch that merely outran the activation
#: ceiling is left running and can still reach the device.
_DISPATCH_ERROR_CODES: tuple[tuple[type[BaseException], ResponseCode], ...] = (
    (OptOutError, ResponseCode.OPT_OUT),
    (DeviceNotConfiguredError, ResponseCode.NOT_APPLICABLE),
    (ConnectorValueError, ResponseCode.INVALID),
    (InvalidControlValueError, ResponseCode.INVALID),
    (ModeNotSupportedError, ResponseCode.NOT_SUPPORTED),
    (DeviceOfflinePermanentError, ResponseCode.NOT_SUPPORTED),
    (DeviceOfflineTransientError, ResponseCode.NOT_SUPPORTED),
)


def response_code_for_dispatch_error(exc: BaseException) -> ResponseCode:
    """Map an unsuccessful control dispatch onto the code to report.

    An unclassified failure falls through to ``NOT_SUPPORTED``: the client
    lacks a precise reason, but the control still was not applied,
    and reporting ACTIVE for it would assert something that did not happen. The
    in-process connector path does not raise the typed control errors, so its
    failures land here until those connectors are migrated.

    Note the fall-through is deliberately a capability limit, never status 4: an
    opt-out has to be an explicit ``OptOutError``, so no unrecognized error can
    be misreported to the head-end as a customer decision.
    """
    for exc_type, code in _DISPATCH_ERROR_CODES:
        if isinstance(exc, exc_type):
            return code
    return ResponseCode.NOT_SUPPORTED


#: IEEE 2030.5-2018 reserves 251 and gives 252 the meaning that 2030.5-2023
#: assigns to 251, so a rejection bound for a 2018 server has to go out as
#: 252. ``NOT_APPLICABLE`` is already 252 and passes through unchanged, which
#: collapses the two codes onto one wire value -- the Response resource carries
#: no reason field in either revision, so nothing can preserve the distinction.
#: The original code stays in the logs.
#:
#: ``INVALID`` (253) is deliberately absent: 2030.5-2018 assigns 253 the same
#: meaning, so it needs no translation and a 2018 head-end can still tell
#: invalid-event-data apart from a capability limit. Like the rejection code
#: itself, this records
#: the standard's semantics as supplied by the team -- both bundled schemas type
#: ``Response.status`` as a bare ``UInt8``, so the repository cannot settle it.
_CODE_2018_OVERRIDES = {ResponseCode.NOT_SUPPORTED: ResponseCode.NOT_APPLICABLE}


def translate_code_for_revision(code: ResponseCode, *, server_2018_compat: bool) -> ResponseCode:
    """Map *code* onto the IEEE 2030.5 revision the server speaks."""
    if not server_2018_compat:
        return code
    return _CODE_2018_OVERRIDES.get(code, code)


def _resolve_wire_code(
    derc: Dercontrol1, code: ResponseCode, *, server_2018_compat: bool, mrid: bytes
) -> ResponseCode:
    """Resolve the status code that actually goes on the wire.

    Two adjustments, applied in order:

    * An opt-out the event's ``responseRequired`` does not request would
      be dropped by the caller's gate, leaving the head-end with a Received and
      then silence for an event the client declined to perform. Report it as
      a capability limit instead -- that rides the specific-response bit -- and
      log that the reason was downgraded. Misreporting the reason beats
      reporting nothing; the true reason stays in the log.
    * 2030.5-2018 reserves 251, so rejections translate to 252 there.
      Applied second, so a downgraded opt-out is translated too.
    """
    if code == ResponseCode.OPT_OUT and not response_required_allows(
        derc.response_required, ResponseCode.OPT_OUT
    ):
        logger.info(
            "responseRequired=%s does not request end-user responses; reporting "
            "opt-out as %s instead for mrid=%s",
            (derc.response_required or b"\x00").hex(),
            ResponseCode.NOT_SUPPORTED.name,
            mrid.hex()[:8],
        )
        code = ResponseCode.NOT_SUPPORTED
    wire = translate_code_for_revision(code, server_2018_compat=server_2018_compat)
    if wire is not code:
        logger.debug("2018 compat: %s -> %s for mrid=%s", code.name, wire.name, mrid.hex()[:8])
    return wire


class ResponseTracker:
    """Deduplicates DER response posts and provides expiry-based pruning.

    Dedup key is ``(mrid, code, lfdi)`` so that the same event can produce
    separate responses for different devices (required by multi-device AGG
    conformance tests).
    """

    def __init__(self) -> None:
        self._sent: dict[tuple[bytes, ResponseCode, bytes], int] = {}
        #: Keys claimed by a POST that has not returned. Held apart from
        #: ``_sent`` so an age prune cannot evict a response still in flight.
        self._in_flight: set[tuple[bytes, ResponseCode, bytes]] = set()

    def already_sent(self, mrid: bytes, code: ResponseCode, lfdi: bytes) -> bool:
        key = (mrid, code, lfdi)
        return key in self._sent or key in self._in_flight

    def reserve(self, mrid: bytes, code: ResponseCode, lfdi: bytes) -> bool:
        """Claim this key for a POST, or report that someone else holds it.

        The check and the claim happen with no await between them, so on one
        event loop this is atomic. Without it, a caller that checks, posts and
        marks lets a second caller through the window the POST opens -- which
        is exactly what the per-device response fan-out does when two targets
        resolve to one LFDI.

        Returns True when the caller may post, False when it must not.
        """
        key = (mrid, code, lfdi)
        if key in self._sent or key in self._in_flight:
            return False
        self._in_flight.add(key)
        return True

    def release(self, mrid: bytes, code: ResponseCode, lfdi: bytes) -> None:
        """Give up a claim whose POST never landed, so a retry can take it."""
        self._in_flight.discard((mrid, code, lfdi))

    def has_responded(self, mrid: bytes) -> bool:
        """True if any response (any code, any device) was already sent for *mrid*.

        Used to suppress a spurious EXPIRED on an event we already handled (e.g.
        ran to COMPLETED) that the event store pruned and a later poll
        re-discovered -- IEEE 2030.5 §10.2.2.3 rule j scopes EXPIRED to events
        already past their end *when first received*.
        """
        return any(sent_mrid == mrid for sent_mrid, _code, _lfdi in self._sent)

    def mark_sent(self, mrid: bytes, code: ResponseCode, lfdi: bytes, now: int) -> None:
        key = (mrid, code, lfdi)
        self._in_flight.discard(key)
        self._sent[key] = now

    def prune(self, now: int, max_age: int = 7200) -> None:
        """Remove entries older than max_age seconds."""
        expired = [k for k, ts in self._sent.items() if now - ts > max_age]
        for k in expired:
            del self._sent[k]

    def retain_mrids(self, live_mrids: set[bytes]) -> None:
        """Drop dedup entries whose event mRID is not in *live_mrids*.

        Liveness-scoped eviction (vs :meth:`prune`'s age-based one) suits callers
        that re-scan a known set of events each cycle and must never re-post for a
        still-present event: an age prune could drop a long-lived event's entry
        and cause a duplicate post, whereas this only forgets events that are gone.
        """
        stale = [key for key in self._sent if key[0] not in live_mrids]
        for key in stale:
            del self._sent[key]

    def __len__(self) -> int:
        return len(self._sent)


async def post_der_response(
    http: Sep2Client,
    derc: Dercontrol1,
    code: ResponseCode,
    lfdi: bytes,
    tracker: ResponseTracker,
    *,
    now_ts: int | None = None,
) -> None:
    """Post a DERControlResponse to the server.

    Uses replyTo from the DERControl.  If replyTo is absent the response is
    skipped (the server failed to provide a destination, per IEEE 2030.5
    Section 8.10.3.1).

    ``now_ts`` sets ``createdDateTime`` (and the tracker stamp); the event
    processor passes server-timebase time so the server sees its own clock.
    Defaults to the local clock.
    """
    mrid = derc.m_rid.value
    # Resolve before gating and dedup so both act on the value that actually
    # goes on the wire: under 2018 two codes collapse onto one, and the server
    # must not receive it twice.
    code = _resolve_wire_code(derc, code, server_2018_compat=http.server_2018_compat, mrid=mrid)
    if not response_required_allows(derc.response_required, code):
        logger.debug(
            "responseRequired=%s does not request %s; skipping response mrid=%s",
            (derc.response_required or b"\x00").hex(),
            code.name,
            mrid.hex()[:8],
        )
        return

    path = derc.reply_to
    if not path:
        logger.warning(
            "No replyTo on control mrid=%s; skipping %s response", mrid.hex()[:8], code.name
        )
        return

    # Reserved rather than checked: the per-device fan-out posts concurrently,
    # and a check that the POST separates from its mark lets two responses out
    # for one (mrid, code, lfdi).
    if not tracker.reserve(mrid, code, lfdi):
        logger.debug("Skipping duplicate response mrid=%s code=%s", mrid.hex(), code.name)
        return

    now_ts = now_ts if now_ts is not None else int(time.time())
    # modesResponded was added in IEEE 2030.5-2023; omit for 2018 servers.
    modes = None if http.server_2018_compat else build_modes_responded(derc.dercontrol_base)
    response = DercontrolResponse(
        end_device_lfdi=lfdi,
        status=code.value,
        subject=MRidtype(value=mrid),
        created_date_time=TimeType(value=now_ts),
        modes_responded=modes,
    )

    try:
        await http.post(path, response)
        tracker.mark_sent(mrid, code, lfdi, now_ts)
        logger.info(
            "Response posted: mrid=%s lfdi=%s %s -> %s",
            mrid.hex()[:8],
            lfdi.hex()[:8].upper(),
            code.name,
            path,
        )
    except Exception:
        # The response did not land, so the claim must not outlive it or the
        # next cycle would treat this as already answered.
        tracker.release(mrid, code, lfdi)
        logger.warning(
            "Failed to post response mrid=%s code=%s", mrid.hex()[:8], code.name, exc_info=True
        )


async def post_price_response(
    http: Sep2Client,
    interval: TimeTariffInterval1,
    code: ResponseCode,
    lfdi: bytes,
    tracker: ResponseTracker,
    *,
    now_ts: int | None = None,
) -> None:
    """Post a PriceResponse for a consumed TimeTariffInterval.

    IEEE 2030.5 Price function set: when a TimeTariffInterval's responseRequired
    bitfield requests it, the price-responsive client acknowledges receipt.
    Reuses the DER response machinery -- the responseRequired bit gating
    (:func:`response_required_allows`) and the per-(mrid, code, lfdi) dedup
    tracker -- but builds a ``PriceResponse`` (no DER modesResponded).

    ``now_ts`` sets ``createdDateTime`` (and the tracker stamp); the tariff
    processor passes FSA-scoped server-timebase time so the server sees its own
    clock (Response.createdDateTime is relative to the event's Time server).
    Defaults to the local clock.
    """
    mrid = interval.m_rid.value
    if not response_required_allows(interval.response_required, code):
        logger.debug(
            "responseRequired=%s does not request %s; skipping price response mrid=%s",
            (interval.response_required or b"\x00").hex(),
            code.name,
            mrid.hex()[:8],
        )
        return

    path = interval.reply_to
    if not path:
        logger.warning(
            "No replyTo on tariff interval mrid=%s; skipping %s response",
            mrid.hex()[:8],
            code.name,
        )
        return

    if not tracker.reserve(mrid, code, lfdi):
        logger.debug("Skipping duplicate price response mrid=%s code=%s", mrid.hex(), code.name)
        return

    now_ts = now_ts if now_ts is not None else int(time.time())
    response = PriceResponse(
        end_device_lfdi=lfdi,
        status=code.value,
        subject=MRidtype(value=mrid),
        created_date_time=TimeType(value=now_ts),
    )

    try:
        await http.post(path, response)
        tracker.mark_sent(mrid, code, lfdi, now_ts)
        logger.info(
            "Price response posted: mrid=%s lfdi=%s %s -> %s",
            mrid.hex()[:8],
            lfdi.hex()[:8].upper(),
            code.name,
            path,
        )
    except Exception:
        tracker.release(mrid, code, lfdi)
        logger.warning(
            "Failed to post price response mrid=%s code=%s",
            mrid.hex()[:8],
            code.name,
            exc_info=True,
        )
