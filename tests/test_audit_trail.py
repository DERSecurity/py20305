"""Tests for the control audit trail and the device write records.

The record tests drive the processor, the dispatcher and the client end to end
and read back what reached the observers, because the point of the trail is
that the pieces join: a lifecycle record and the writes it caused must carry
the same identifiers. The isolation tests prove a failing observer cannot stop
a control.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from py20305.audit import AuditEmitter, AuditSequence, mrid_hex
from py20305.client.csip_client import CsipClient
from py20305.client.state import DerProgramState, DiscoveredState, EndDeviceState
from py20305.commands import CommandOrigin
from py20305.connectors.device_telemetry import DeviceTelemetryEmitter
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.connectors.print_demo import PrintDemoConnector
from py20305.events import processor as processor_mod
from py20305.events.comms_loss import CommsLossState
from py20305.events.processor import EventProcessor
from py20305.events.response import ResponseCode
from py20305.events.state_machine import EventState
from py20305.models.sep.sep import (
    DateTimeInterval,
    DefaultDercontrol,
    Dercontrol1,
    DercontrolBase,
    Derprogram1,
    EndDevice1,
    EventStatus,
    MRidtype,
    PerCentControlType,
    PrimacyType,
    Sfditype,
    TimeType,
)
from py20305.observers import AuditRecord, DeviceOperation, DeviceRecord

LFDI_1 = b"\xaa" * 20
LFDI_2 = b"\xbb" * 20
DDERC_MRID = b"\x20" * 16
AUDIT_TOPIC = "out/der-events"


class RecordingForwarder:
    """The audit and device observers of a test, keeping every record in order."""

    def __init__(self) -> None:
        self.events: list[AuditRecord | DeviceRecord] = []

    def audit(self, record: AuditRecord) -> None:
        self.events.append(record)

    def device(self, record: DeviceRecord) -> None:
        self.events.append(record)

    def audit_records(self, kind: str | None = None) -> list[dict[str, Any]]:
        out = [dict(e.data) for e in self.events if isinstance(e, AuditRecord)]
        return [r for r in out if kind is None or r["kind"] == kind]

    def writes(self) -> list[dict[str, Any]]:
        """Each write's cause fields and body, in the order they were reported."""
        out = []
        for event in self.events:
            if not isinstance(event, DeviceRecord) or event.operation != DeviceOperation.WRITE:
                continue
            fields = {
                "origin": event.origin,
                "applied_mrid": event.applied_mrid,
                "cause_mrid": event.cause_mrid,
                "outcome": str(event.outcome),
            }
            out.append(
                {
                    **{k: v for k, v in fields.items() if v is not None},
                    "body": {
                        "control": event.resource,
                        "params": dict(event.values),
                        "error": event.error,
                        "error_type": event.error_type,
                    },
                }
            )
        return out


# -- Fixtures ----------------------------------------------------------------


def _limit(value: int = 5000) -> DercontrolBase:
    """A base that translates to a write, so the fallback is observable."""
    return DercontrolBase(op_mod_max_lim_w=PerCentControlType(value=value))


def _derc(mrid_byte: int, start: int, duration: int = 3600, current_status: int = 0) -> Dercontrol1:
    return Dercontrol1(
        m_rid=MRidtype(value=bytes([mrid_byte]) * 16),
        creation_time=TimeType(value=900),
        event_status=EventStatus(
            current_status=current_status,
            date_time=TimeType(value=1000),
            potentially_superseded=False,
        ),
        interval=DateTimeInterval(duration=duration, start=TimeType(value=start)),
        dercontrol_base=_limit(),
        reply_to="/rsps",
        response_required=b"\x07",
    )


def _dderc() -> DefaultDercontrol:
    return DefaultDercontrol(m_rid=MRidtype(value=DDERC_MRID), dercontrol_base=_limit(10000))


def _program(href: str, primacy: int, controls: list[Dercontrol1], dderc=None) -> DerProgramState:
    return DerProgramState(
        program=Derprogram1(m_rid=MRidtype(value=b"\x10" * 16), primacy=PrimacyType(value=primacy)),
        href=href,
        primacy=primacy,
        default_dercontrol=dderc,
        der_controls=controls,
    )


def _device(state: DiscoveredState, href: str, lfdi: bytes) -> None:
    state.end_devices[href] = EndDeviceState(
        device=EndDevice1(
            m_rid=MRidtype(value=b"\x30" * 16),
            s_fdi=Sfditype(value=0),
            changed_time=TimeType(value=0),
        ),
        href=href,
        lfdi=lfdi,
    )


def _state(
    controls: list[Dercontrol1], *, dderc: DefaultDercontrol | None = None
) -> DiscoveredState:
    state = DiscoveredState()
    state.der_programs["/derp/1"] = _program("/derp/1", 0, controls, dderc)
    _device(state, "/edev/1", LFDI_1)
    state.device_mapping.add("/derp/1", "/edev/1")
    return state


class Site:
    """A processor wired to a real dispatcher, emitters and demo devices."""

    def __init__(
        self,
        state: DiscoveredState,
        *,
        audit: bool = True,
        device_telemetry: bool = False,
        comms_loss: CommsLossState | None = None,
        failing: set[bytes] | None = None,
        group_lookup: Any = None,
        local: list[bytes] | None = None,
        gate: Any = None,
    ) -> None:
        self.forwarder = RecordingForwarder()
        self.sequence = AuditSequence()
        self.connectors: dict[str, PrintDemoConnector] = {}
        # ``local`` are devices behind a group lookup: the server never names
        # them, and only the dispatcher's by-LFDI path reaches them.
        for lfdi in [edev.lfdi for edev in state.end_devices.values()] + (local or []):
            connector = PrintDemoConnector()
            if lfdi in (failing or set()):
                connector.update_op_mod_max_lim_w = AsyncMock(
                    side_effect=RuntimeError("register locked")
                )  # type: ignore[method-assign]
            self.connectors[lfdi.hex()] = connector
        hrefs = {href: edev.lfdi.hex() for href, edev in state.end_devices.items()}

        registry = Mock()
        registry.get_connector.side_effect = lambda lfdi: (
            Mock(aresolve=AsyncMock(return_value=self.connectors[lfdi]))
            if lfdi in self.connectors
            else None
        )
        self.telemetry = DeviceTelemetryEmitter(self.forwarder.device)
        self.dispatcher = ConnectorDispatcher(
            registry, hrefs.get, command_gate=gate, telemetry=self.telemetry
        )
        self.audit = AuditEmitter(
            self.forwarder.audit if audit else None,
            client_id="site-a",
            sequence=self.sequence,
        )
        self.http = AsyncMock()
        self.http.post = AsyncMock(return_value=None)
        self.processor = EventProcessor(
            self.http,
            state,
            self.dispatcher,
            asyncio.Event(),
            comms_loss=comms_loss,
            audit=self.audit,
            group_lookup=group_lookup,
        )

    def transitions(self) -> list[tuple[str, str | None, str]]:
        return [
            (r["event_mrid"], r["from_state"], r["to_state"])
            for r in self.forwarder.audit_records("der_event")
        ]


HEX_1 = mrid_hex(b"\x01" * 16)
HEX_2 = mrid_hex(b"\x02" * 16)
HEX_DDERC = mrid_hex(DDERC_MRID)


# -- What each write says caused it ----------------------------------------------


class TestWritesNameTheirCause:
    @pytest.mark.asyncio
    async def test_activation_then_completion(self):
        now = int(time.time())
        derc = _derc(0x01, start=now - 10)
        site = Site(_state([derc], dderc=_dderc()))

        await site.processor.process_controls("/derp/1")
        record = site.processor._store.get(derc.m_rid.value)
        await site.processor._on_completion(record)

        activation, fallback = site.forwarder.writes()
        assert activation["origin"] == CommandOrigin.IEEE2030_5
        assert activation["applied_mrid"] == activation["cause_mrid"] == HEX_1
        assert fallback["origin"] == CommandOrigin.DDERC_REAPPLY
        assert fallback["applied_mrid"] == HEX_DDERC
        assert fallback["cause_mrid"] == HEX_1, "the event that ended, not the default"
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_completion_fanned_out_by_lfdi(self):
        """A group lookup sends the fallback down the by-LFDI path instead."""
        now = int(time.time())
        derc = _derc(0x01, start=now - 10)
        site = Site(_state([derc], dderc=_dderc()), group_lookup=lambda _href: [LFDI_1.hex()])

        await site.processor.process_controls("/derp/1")
        await site.processor._on_completion(site.processor._store.get(derc.m_rid.value))

        activation, fallback = site.forwarder.writes()
        assert activation["applied_mrid"] == activation["cause_mrid"] == HEX_1
        assert (fallback["applied_mrid"], fallback["cause_mrid"]) == (HEX_DDERC, HEX_1)
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_comms_loss_clear_fanned_out_by_lfdi(self):
        now = int(time.time())
        comms = CommsLossState()
        site = Site(
            _state([_derc(0x01, start=now - 10)]),
            comms_loss=comms,
            group_lookup=lambda _href: [LFDI_1.hex()],
        )
        await site.processor.process_controls("/derp/1")

        comms.active = True
        await site.processor.enter_comms_loss()

        clears = site.forwarder.writes()[1:]
        assert clears and all(c["cause_mrid"] == HEX_1 for c in clears)
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_cancellation_fallback_names_the_cancelled_event(self):
        now = int(time.time())
        derc = _derc(0x01, start=now - 10)
        state = _state([derc], dderc=_dderc())
        site = Site(state)
        await site.processor.process_controls("/derp/1")

        state.der_programs["/derp/1"].der_controls = [_derc(0x01, start=now - 10, current_status=2)]
        await site.processor.process_controls("/derp/1")

        fallbacks = [
            w for w in site.forwarder.writes() if w["origin"] == CommandOrigin.DDERC_REAPPLY
        ]
        assert fallbacks[0]["applied_mrid"] == HEX_DDERC
        assert fallbacks[0]["cause_mrid"] == HEX_1
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_superseding_event_names_itself(self):
        """A superseded event leaves its superseded devices to the event that
        took them over, so there is no fallback write; the superseding event's
        own activation is the write that moves the device."""
        now = int(time.time())
        state = _state([_derc(0x02, start=now - 10)], dderc=_dderc())
        state.der_programs["/derp/1"].primacy = 5
        site = Site(state)
        await site.processor.process_controls("/derp/1")

        state.der_programs["/derp/0"] = _program("/derp/0", 0, [_derc(0x01, start=now - 5)])
        state.device_mapping.add("/derp/0", "/edev/1")
        await site.processor.process_controls("/derp/0")

        last = site.forwarder.writes()[-1]
        assert last["applied_mrid"] == last["cause_mrid"] == HEX_1
        assert not [w for w in site.forwarder.writes() if w.get("cause_mrid") == HEX_2][1:]
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_comms_loss_fallback_names_the_opted_out_event(self):
        now = int(time.time())
        derc = _derc(0x01, start=now - 10)
        comms = CommsLossState()
        site = Site(_state([derc], dderc=_dderc()), comms_loss=comms)
        await site.processor.process_controls("/derp/1")

        comms.active = True
        await site.processor.enter_comms_loss()

        fallback = site.forwarder.writes()[-1]
        assert fallback["origin"] == CommandOrigin.COMMS_LOSS
        assert (fallback["applied_mrid"], fallback["cause_mrid"]) == (HEX_DDERC, HEX_1)
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_comms_loss_clear_names_the_opted_out_event(self):
        """No default to fall back to: a clear, which writes no control's mRID."""
        now = int(time.time())
        derc = _derc(0x01, start=now - 10)
        comms = CommsLossState()
        site = Site(_state([derc]), comms_loss=comms)
        await site.processor.process_controls("/derp/1")

        comms.active = True
        await site.processor.enter_comms_loss()

        clears = site.forwarder.writes()[1:]
        assert clears, "the clear fans out to the modes the device implements"
        for clear in clears:
            assert clear["origin"] == CommandOrigin.COMMS_LOSS
            assert clear["cause_mrid"] == HEX_1
            assert "applied_mrid" not in clear
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_named_operation_carries_its_origin_and_no_mrids(self):
        site = Site(_state([]))

        await site.dispatcher.apply_operation(
            LFDI_1.hex(), "op_mod_max_lim_w", {"value": 50}, origin="local_api"
        )

        (write,) = site.forwarder.writes()
        assert write["origin"] == "local_api"
        assert "applied_mrid" not in write and "cause_mrid" not in write

    @pytest.mark.asyncio
    async def test_a_rejected_write_keeps_its_cause_and_says_why(self):
        now = int(time.time())
        derc = _derc(0x01, start=now - 10)
        site = Site(_state([derc]), failing={LFDI_1})

        await site.processor.process_controls("/derp/1")

        (write,) = site.forwarder.writes()
        assert write["body"]["error"] == "register locked"
        assert write["body"]["error_type"] == "RuntimeError"
        assert write["origin"] == CommandOrigin.IEEE2030_5
        assert write["applied_mrid"] == write["cause_mrid"] == HEX_1
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_gate_refusal_is_a_write_record_that_did_not_happen(self):
        """The trail keeps the refused write beside the rejection the server is sent."""

        class Deny:
            def may_command(self, lfdi, origin):
                return False

        now = int(time.time())
        site = Site(_state([_derc(0x01, start=now - 10)]), gate=Deny())

        await site.processor.process_controls("/derp/1")

        (write,) = site.forwarder.writes()
        assert write["body"]["error_type"] == "refused"
        assert write["origin"] == CommandOrigin.IEEE2030_5
        assert write["applied_mrid"] == write["cause_mrid"] == HEX_1
        assert site.connectors[LFDI_1.hex()].last_control == {}, "nothing was written"
        active = site.forwarder.audit_records("der_event")[-1]
        assert active["applied_lfdis"] == []
        assert active["rejected_lfdis"] == [LFDI_1.hex()], "what the server was told"
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_gate_refusal_is_reported_as_not_applicable_not_active(self):
        """IEEE 2030.5-2023 Table 31: status 2 says the event started on the device,
        which a refused write did not do. 252 says the control does not apply to the
        device as configured."""

        class Deny:
            def may_command(self, lfdi, origin):
                return False

        now = int(time.time())
        site = Site(_state([_derc(0x01, start=now - 10)]), gate=Deny())

        await site.processor.process_controls("/derp/1")

        statuses = [call.args[1].status for call in site.http.post.call_args_list]
        assert ResponseCode.NOT_APPLICABLE in statuses
        assert ResponseCode.ACTIVE not in statuses
        record = site.processor._store.get(b"\x01" * 16)
        assert record is not None and record.rejected_devices == {"/edev/1"}
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_cancelled_event_names_itself_after_the_wind_down(self):
        now = int(time.time())
        state = _state([_derc(0x01, start=now - 10)], dderc=_dderc())
        site = Site(state)
        await site.processor.process_controls("/derp/1")

        # The wind-down timer counts whole seconds; the callback it was handed
        # is fired here instead of waiting on the clock.
        scheduled: list[Any] = []
        with (
            patch.object(
                processor_mod.EventProcessor, "_cancellation_wind_down", staticmethod(lambda _r: 5)
            ),
            patch.object(
                site.processor._timer_mgr,
                "schedule_delayed_callback",
                side_effect=lambda record, _delay, callback, _label: scheduled.append(
                    (record, callback)
                ),
            ),
            patch.object(
                site.processor._timer_mgr, "has_pending", side_effect=lambda _m: bool(scheduled)
            ),
        ):
            state.der_programs["/derp/1"].der_controls = [
                _derc(0x01, start=now - 10, current_status=2)
            ]
            await site.processor.process_controls("/derp/1")
            assert not [w for w in site.forwarder.writes() if w["origin"] == "dderc_reapply"], (
                "the default waits for the wind-down"
            )
            ((record, callback),) = scheduled
            await callback(record)

        fallback = next(w for w in site.forwarder.writes()[1:] if w.get("cause_mrid") == HEX_1)
        assert fallback["origin"] == CommandOrigin.DDERC_REAPPLY
        assert fallback["applied_mrid"] == HEX_DDERC
        await site.processor.shutdown()

    def test_an_href_keeps_its_case_and_an_lfdi_is_lowered(self):
        """An href is a case-sensitive URI; lowering it would stop it joining
        back to the server's own href."""
        forwarder = RecordingForwarder()
        emitter = AuditEmitter(forwarder.audit)

        emitter.event_transition(
            event_mrid=bytes([1]) * 16,
            program_href="/derp/1",
            from_state=None,
            to_state="scheduled",
            effective_start=0,
            effective_duration=1,
            primacy=0,
            lfdis=["/edev/ABC", "AB" * 20],
            at=0.0,
        )
        emitter.late_dispatch(event_mrid=bytes([1]) * 16, lfdi="/edev/ABC", applied=True, at=0.0)

        event, late = forwarder.audit_records()
        assert event["lfdis"] == sorted(["/edev/ABC", "ab" * 20])
        assert late["lfdi"] == "/edev/ABC"

    def test_mrids_are_uppercase_hex(self):
        assert mrid_hex(bytes.fromhex("0a1b2c")) == "0A1B2C"


# -- Lifecycle records -----------------------------------------------------------


class TestLifecycleRecords:
    @pytest.mark.asyncio
    async def test_scheduled_active_completed(self):
        now = int(time.time())
        derc = _derc(0x01, start=now + 100)
        site = Site(_state([derc], dderc=_dderc()))

        await site.processor.process_controls("/derp/1")
        record = site.processor._store.get(derc.m_rid.value)
        await site.processor._on_activation(record)
        await site.processor._on_completion(record)

        assert site.transitions() == [
            (HEX_1, None, "scheduled"),
            (HEX_1, "scheduled", "active"),
            (HEX_1, "active", "completed"),
        ]
        scheduled = site.forwarder.audit_records("der_event")[0]
        assert scheduled == {
            "kind": "der_event",
            "schema": 1,
            "event_mrid": HEX_1,
            "program_href": "/derp/1",
            "from_state": None,
            "to_state": "scheduled",
            "effective_start": record.start,
            "effective_duration": record.duration,
            "primacy": 0,
            "lfdis": [LFDI_1.hex()],
            "at": scheduled["at"],
            "client_id": "site-a",
            "boot_id": site.sequence.boot_id,
            "seq": scheduled["seq"],
        }
        assert not {"points", "raw_message"} & scheduled.keys(), "keys an indexer reshapes"
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_the_active_record_lists_final_outcomes(self):
        now = int(time.time())
        derc = _derc(0x01, start=now + 100)
        state = _state([derc])
        _device(state, "/edev/2", LFDI_2)
        state.device_mapping.add("/derp/1", "/edev/2")
        site = Site(state, failing={LFDI_2})

        await site.processor.process_controls("/derp/1")
        await site.processor._on_activation(site.processor._store.get(derc.m_rid.value))

        active = site.forwarder.audit_records("der_event")[-1]
        assert active["to_state"] == "active"
        assert active["applied_lfdis"] == [LFDI_1.hex()]
        assert active["rejected_lfdis"] == [LFDI_2.hex()]
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_late_discovery_is_one_active_record(self):
        now = int(time.time())
        site = Site(_state([_derc(0x01, start=now - 10)]))

        await site.processor.process_controls("/derp/1")

        assert site.transitions() == [(HEX_1, None, "active")]
        assert site.forwarder.audit_records("der_event")[0]["applied_lfdis"] == [LFDI_1.hex()]
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_cancelled(self):
        now = int(time.time())
        state = _state([_derc(0x01, start=now + 100)])
        site = Site(state)
        await site.processor.process_controls("/derp/1")

        state.der_programs["/derp/1"].der_controls = [
            _derc(0x01, start=now + 100, current_status=2)
        ]
        await site.processor.process_controls("/derp/1")

        assert site.transitions()[-1] == (HEX_1, "scheduled", "cancelled")
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_cancelled_before_first_seen(self):
        now = int(time.time())
        site = Site(_state([_derc(0x01, start=now + 100, current_status=2)]))

        await site.processor.process_controls("/derp/1")

        assert site.transitions() == [(HEX_1, None, "cancelled")]
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_cancelled_by_program_removal(self):
        now = int(time.time())
        site = Site(_state([_derc(0x01, start=now + 100)]))
        await site.processor.process_controls("/derp/1")

        site.processor.cancel_program("/derp/1")

        assert site.transitions()[-1] == (HEX_1, "scheduled", "cancelled")
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_wholly_superseded(self):
        now = int(time.time())
        state = _state([_derc(0x02, start=now - 10)])
        state.der_programs["/derp/1"].primacy = 5
        site = Site(state)
        await site.processor.process_controls("/derp/1")

        state.der_programs["/derp/0"] = _program("/derp/0", 0, [_derc(0x01, start=now - 5)])
        state.device_mapping.add("/derp/0", "/edev/1")
        await site.processor.process_controls("/derp/0")

        superseded = [
            r for r in site.forwarder.audit_records("der_event") if r["to_state"] == "superseded"
        ]
        assert len(superseded) == 1
        assert superseded[0]["event_mrid"] == HEX_2
        assert superseded[0]["from_state"] == "active"
        assert superseded[0]["superseded_by"] == HEX_1
        assert "superseded_lfdis" not in superseded[0], "the partial lists mark a partial one"
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_partly_superseded(self):
        """The event keeps running on its other device, and the record says which."""
        now = int(time.time())
        state = _state([_derc(0x02, start=now - 10)])
        state.der_programs["/derp/1"].primacy = 5
        _device(state, "/edev/2", LFDI_2)
        state.device_mapping.add("/derp/1", "/edev/2")
        site = Site(state)
        await site.processor.process_controls("/derp/1")

        state.der_programs["/derp/0"] = _program("/derp/0", 0, [_derc(0x01, start=now - 5)])
        state.device_mapping.add("/derp/0", "/edev/1")
        await site.processor.process_controls("/derp/0")

        (partial,) = [
            r for r in site.forwarder.audit_records("der_event") if "superseded_lfdis" in r
        ]
        assert (partial["from_state"], partial["to_state"]) == ("active", "active"), (
            "a partial supersession is not a transition"
        )
        assert partial["event_mrid"] == HEX_2
        assert partial["superseded_by"] == HEX_1
        assert partial["superseded_lfdis"] == [LFDI_1.hex()]
        assert partial["superseded_modes"] == {LFDI_1.hex(): ["opModMaxLimW"]}, (
            "modes by their IEEE 2030.5 element name, as every other identifier"
        )
        assert site.processor._store.get(b"\x02" * 16).state == EventState.ACTIVE
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_opted_out_at_activation(self):
        now = int(time.time())
        derc = _derc(0x01, start=now + 100)
        comms = CommsLossState()
        site = Site(_state([derc], dderc=_dderc()), comms_loss=comms)
        await site.processor.process_controls("/derp/1")

        comms.active = True
        await site.processor._on_activation(site.processor._store.get(derc.m_rid.value))

        assert site.transitions()[-1] == (HEX_1, "scheduled", "opted_out")
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_opted_out_on_entering_comms_loss(self):
        now = int(time.time())
        comms = CommsLossState()
        site = Site(_state([_derc(0x01, start=now - 10)], dderc=_dderc()), comms_loss=comms)
        await site.processor.process_controls("/derp/1")

        comms.active = True
        await site.processor.enter_comms_loss()

        assert site.transitions()[-1] == (HEX_1, "active", "opted_out")
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_grouped_devices_are_reported_one_by_one(self):
        """Under a group lookup the server sees one EndDevice and one outcome. The
        record names the devices actually written to, and a partial failure
        stays visible though the server is told the event started."""
        now = int(time.time())
        local_ok, local_bad = bytes([0x0C]) * 20, bytes([0x0D]) * 20
        site = Site(
            _state([_derc(0x01, start=now - 10)]),
            local=[local_ok, local_bad],
            failing={local_bad},
            group_lookup=lambda _href: [local_ok.hex(), local_bad.hex()],
        )

        await site.processor.process_controls("/derp/1")

        active = site.forwarder.audit_records("der_event")[-1]
        assert active["to_state"] == "active"
        assert active["applied_lfdis"] == [local_ok.hex()]
        assert active["rejected_lfdis"] == [local_bad.hex()]
        assert LFDI_1.hex() not in active["applied_lfdis"], (
            "the aggregate is not a device written to"
        )
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_an_opted_out_event_leaves_from_opted_out(self):
        """Its stored state stays active, but its trail does not."""
        now = int(time.time())
        comms = CommsLossState()
        state = _state([_derc(0x01, start=now - 10)], dderc=_dderc())
        site = Site(state, comms_loss=comms)
        await site.processor.process_controls("/derp/1")
        comms.active = True
        await site.processor.enter_comms_loss()

        state.der_programs["/derp/1"].der_controls = [_derc(0x01, start=now - 10, current_status=2)]
        await site.processor.process_controls("/derp/1")

        assert site.transitions()[-2:] == [
            (HEX_1, "active", "opted_out"),
            (HEX_1, "opted_out", "cancelled"),
        ]
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_at_is_on_the_server_timebase(self):
        now = int(time.time())
        site = Site(_state([_derc(0x01, start=now + 100)]))
        site.processor._timebase.now = lambda *_a, **_k: 12345.0  # type: ignore[method-assign]

        await site.processor.process_controls("/derp/1")

        assert site.forwarder.audit_records("der_event")[0]["at"] == 12345.0
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_with_no_audit_observer_no_lifecycle_record_is_made(self):
        now = int(time.time())
        derc = _derc(0x01, start=now - 10)
        site = Site(_state([derc]), audit=False)

        await site.processor.process_controls("/derp/1")

        assert site.forwarder.audit_records() == []
        assert "update_op_mod_max_lim_w" in site.connectors[LFDI_1.hex()].last_control
        assert site.http.post.await_count == 2
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_an_event_already_over_is_recorded_once_as_expired(self):
        now = int(time.time())
        site = Site(_state([_derc(0x01, start=now - 10000, duration=100)]))

        for _ in range(3):
            await site.processor.process_controls("/derp/1")

        assert site.transitions() == [(HEX_1, None, "expired")]
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_an_expired_event_with_no_response_requested_is_recorded_once(self):
        """With no EXPIRED posted there is no response to remember it by, and the
        event is past its end, so only the trail's own memory stops a repeat."""
        now = int(time.time())
        derc = _derc(0x01, start=now - 10000, duration=100)
        derc.response_required = b"\x00"
        state = _state([derc])
        state.der_programs["/derp/1"].der_controls_complete = True
        site = Site(state)

        for _ in range(3):
            await site.processor.process_controls("/derp/1")

        assert site.transitions() == [(HEX_1, None, "expired")]
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_skipped_event_already_over_is_recorded_once(self):
        now = int(time.time())
        comms = CommsLossState(resume_after_epoch=now + 10000)
        state = _state([_derc(0x01, start=now - 500, duration=100)])
        state.der_programs["/derp/1"].der_controls_complete = True
        site = Site(state, comms_loss=comms)

        for _ in range(3):
            await site.processor.process_controls("/derp/1")

        assert site.transitions() == [(HEX_1, None, "skipped")]
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_an_event_the_program_stops_serving_is_forgotten(self):
        """Its memory goes once the server drops it, so the set stays bounded."""
        now = int(time.time())
        derc = _derc(0x01, start=now - 10000, duration=100)
        derc.response_required = b"\x00"
        state = _state([derc])
        state.der_programs["/derp/1"].der_controls_complete = True
        site = Site(state)
        await site.processor.process_controls("/derp/1")
        assert site.processor._audit_unstored_seen

        state.der_programs["/derp/1"].der_controls = []
        await site.processor.process_controls("/derp/1")

        assert site.processor._audit_unstored_seen == {}
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_an_event_in_the_opted_out_window_is_recorded_once_as_skipped(self):
        now = int(time.time())
        comms = CommsLossState(resume_after_epoch=now + 10000)
        site = Site(_state([_derc(0x01, start=now + 100)]), comms_loss=comms)

        for _ in range(3):
            await site.processor.process_controls("/derp/1")

        assert site.transitions() == [(HEX_1, None, "skipped")]
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_dispatch_past_the_deadline_records_how_it_ended(self):
        """Listed as rejected, which the server was told; then the apply lands."""
        now = int(time.time())
        site = Site(_state([_derc(0x01, start=now - 10)]))

        async def slow(params):
            await asyncio.sleep(0.2)

        site.connectors[LFDI_1.hex()].update_op_mod_max_lim_w = slow  # type: ignore[method-assign]
        with patch.object(processor_mod, "ACTIVATION_DISPATCH_DEADLINE", 0.05):
            await site.processor.process_controls("/derp/1")
            active = site.forwarder.audit_records("der_event")[-1]
            assert active["rejected_lfdis"] == [LFDI_1.hex()]
            for _ in range(100):
                if site.forwarder.audit_records("late_dispatch"):
                    break
                await asyncio.sleep(0.02)

        (late,) = site.forwarder.audit_records("late_dispatch")
        assert (late["event_mrid"], late["lfdi"], late["applied"]) == (HEX_1, LFDI_1.hex(), True)
        assert late["seq"] > active["seq"]
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_failing_device_lookup_does_not_stop_the_responses(self):
        """Resolving devices for the record runs inside the guard, not on the
        control path, so a lookup that raises costs only the record."""
        now = int(time.time())
        derc = _derc(0x01, start=now - 10)
        site = Site(_state([derc]))

        with patch.object(site.processor, "_lfdi_or_href", side_effect=TypeError("bad lfdi")):
            await site.processor.process_controls("/derp/1")

        assert site.processor._store.get(derc.m_rid.value).state == EventState.ACTIVE
        assert site.http.post.await_count == 2, "ACK and ACTIVE still posted"
        assert "update_op_mod_max_lim_w" in site.connectors[LFDI_1.hex()].last_control
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_grouped_supersession_names_the_local_devices_in_lowercase(self):
        now = int(time.time())
        local_a, local_b = "CC" * 20, "DD" * 20
        state = _state([_derc(0x02, start=now - 10)])
        state.der_programs["/derp/1"].primacy = 5
        _device(state, "/edev/2", LFDI_2)
        state.device_mapping.add("/derp/1", "/edev/2")
        site = Site(
            state,
            local=[bytes.fromhex(local_a), bytes.fromhex(local_b)],
            group_lookup=lambda href: [local_a, local_b] if href == "/derp/1" else None,
        )
        await site.processor.process_controls("/derp/1")

        state.der_programs["/derp/0"] = _program("/derp/0", 0, [_derc(0x01, start=now - 5)])
        state.device_mapping.add("/derp/0", "/edev/1")
        await site.processor.process_controls("/derp/0")

        (partial,) = [
            r for r in site.forwarder.audit_records("der_event") if "superseded_lfdis" in r
        ]
        assert partial["superseded_lfdis"] == [local_a.lower(), local_b.lower()]
        assert partial["superseded_modes"] == {
            local_a.lower(): ["opModMaxLimW"],
            local_b.lower(): ["opModMaxLimW"],
        }
        active = next(
            r
            for r in site.forwarder.audit_records("der_event")
            if r["to_state"] == "active" and "superseded_lfdis" not in r
        )
        assert active["lfdis"] == [local_a.lower(), local_b.lower()]
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_repoll_of_an_unchanged_event_emits_nothing(self):
        now = int(time.time())
        site = Site(_state([_derc(0x01, start=now + 100)]))
        await site.processor.process_controls("/derp/1")
        before = len(site.forwarder.audit_records())

        await site.processor.process_controls("/derp/1")
        await site.processor.process_controls("/derp/1")

        assert len(site.forwarder.audit_records()) == before == 1
        await site.processor.shutdown()


# -- Loss-of-communications records ----------------------------------------------


class TestCommsLossRecords:
    def _client(self) -> tuple[CsipClient, RecordingForwarder]:
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        forwarder = RecordingForwarder()
        client.audit.attach_observer(forwarder.audit)
        client.audit.set_client_id("site-a")
        return client, forwarder

    @pytest.mark.asyncio
    async def test_entry_is_recorded_before_any_event_is_opted_out(self):
        client, forwarder = self._client()
        seen_at_entry: list[int] = []

        async def opt_out() -> None:
            seen_at_entry.append(len(forwarder.audit_records("comms_loss")))

        with patch.object(client._event_processor, "enter_comms_loss", side_effect=opt_out):
            await client._enter_comms_loss(1000)

        assert seen_at_entry == [1], "entered must already be out when the opt-outs start"
        (record,) = forwarder.audit_records("comms_loss")
        assert record["transition"] == "entered"
        assert (record["elapsed_seconds"], record["threshold"]) == (1000, 900)
        assert record["simulated"] is False
        assert "opted_out_mrids" not in record, "each opted_out record names its own event"

    @pytest.mark.asyncio
    async def test_entry_is_recorded_even_if_opting_out_fails(self):
        client, forwarder = self._client()

        with (
            patch.object(
                client._event_processor, "enter_comms_loss", side_effect=RuntimeError("fleet")
            ),
            pytest.raises(RuntimeError),
        ):
            await client._enter_comms_loss(1000)

        assert [r["transition"] for r in forwarder.audit_records("comms_loss")] == ["entered"]

    @pytest.mark.asyncio
    async def test_entry_is_timed_when_flagged_not_when_the_fallbacks_finish(self):
        client, forwarder = self._client()
        clock = [5000.0]
        client._timebase.now = lambda *_a, **_k: clock[0]  # type: ignore[method-assign]

        async def slow_fleet() -> None:
            clock[0] += 120.0

        with patch.object(client._event_processor, "enter_comms_loss", side_effect=slow_fleet):
            await client._enter_comms_loss(1000)

        (record,) = forwarder.audit_records("comms_loss")
        assert record["at"] == 5000.0

    @pytest.mark.asyncio
    async def test_recovery_is_recorded_before_the_repoll_and_cleared_after(self):
        client, forwarder = self._client()
        client._comms_loss.active = True
        boundary = int(time.time()) + 3600
        client._comms_loss.resume_after_epoch = boundary
        client._http._last_contact_epoch = int(time.time())
        seen_at_repoll: list[list[str]] = []

        async def repoll() -> bool:
            seen_at_repoll.append([r["transition"] for r in forwarder.audit_records()])
            return True

        with (
            patch.object(
                client, "_server_lists_end_device", new_callable=AsyncMock, return_value=True
            ),
            patch.object(client, "trigger_rediscovery", side_effect=repoll),
        ):
            await client._recover_from_comms_loss()

        assert seen_at_repoll == [["recovering"]]
        records = forwarder.audit_records("comms_loss")
        assert [r["transition"] for r in records] == ["recovering", "cleared"]
        assert records[1]["resume_after"] == boundary
        assert "duration_seconds" in records[1] and "elapsed_seconds" not in records[1]

    @pytest.mark.asyncio
    async def test_a_recovery_that_does_not_complete_is_not_cleared(self):
        client, forwarder = self._client()
        client._comms_loss.active = True
        with patch.object(
            client, "trigger_rediscovery", new_callable=AsyncMock, return_value=False
        ):
            await client._recover_from_comms_loss()

        assert [r["transition"] for r in forwarder.audit_records()] == ["recovering"]

    @pytest.mark.asyncio
    async def test_the_trail_reads_in_the_order_things_happened(self):
        """Through a real client and its event engine: the mode, then the opt-out
        and the write it caused; recovery, then what the re-poll found, then the
        clear."""
        now = int(time.time())
        state = _state([_derc(0x01, start=now - 10)], dderc=_dderc())
        site = Site(state)
        client = CsipClient(
            "https://example.com", dispatcher=site.dispatcher, comms_loss_seconds=900
        )
        client.audit.attach_observer(site.forwarder.audit)
        client._state.der_programs.update(state.der_programs)
        client._state.end_devices.update(state.end_devices)
        for program, devices in state.device_mapping.program_to_devices.items():
            for device in devices:
                client._state.device_mapping.add(program, device)
        processor = client._event_processor

        await processor.process_controls("/derp/1")
        await client._enter_comms_loss(1000)
        client._state.der_programs["/derp/1"].der_controls.append(_derc(0x03, start=now + 500))

        async def repoll() -> bool:
            await processor.process_controls("/derp/1")
            return True

        client._http._last_contact_epoch = int(time.time())
        with (
            patch.object(
                client, "_server_lists_end_device", new_callable=AsyncMock, return_value=True
            ),
            patch.object(client, "trigger_rediscovery", side_effect=repoll),
        ):
            await client._recover_from_comms_loss()

        def label(event: AuditRecord | DeviceRecord) -> str:
            if isinstance(event, DeviceRecord):
                return f"write:{event.origin}"
            if event.kind == "comms_loss":
                return str(event.data["transition"])
            return f"{str(event.data['event_mrid'])[:2]}:{event.data['to_state']}"

        # The observers receive every record as it is made, so arrival order
        # is the order things happened.
        order = [label(e) for e in site.forwarder.events]
        # The re-poll may also write the default again; that is the re-poll's
        # own business and must simply fall inside the recovery.
        repoll_writes = [n for n, item in enumerate(order) if item == "write:dderc_reapply"]
        assert all(order.index("recovering") < n < order.index("cleared") for n in repoll_writes), (
            " | ".join(order)
        )
        assert [item for item in order if item != "write:dderc_reapply"] == [
            "write:ieee2030_5",
            "01:active",
            "entered",
            "01:opted_out",
            "write:comms_loss",
            "recovering",
            "03:skipped",
            "cleared",
        ], " | ".join(order)
        audit_seq = [r["seq"] for r in site.forwarder.audit_records()]
        assert audit_seq == list(range(1, len(audit_seq) + 1))
        await processor.shutdown()


# -- Detecting loss -------------------------------------------------------------


class TestSequence:
    @pytest.mark.asyncio
    async def test_the_lifecycle_records_are_numbered_without_a_gap(self):
        now = int(time.time())
        derc = _derc(0x01, start=now + 100)
        site = Site(_state([derc], dderc=_dderc()))

        await site.processor.process_controls("/derp/1")
        record = site.processor._store.get(derc.m_rid.value)
        await site.processor._on_activation(record)
        await site.processor._on_completion(record)

        stamped = [r["seq"] for r in site.forwarder.audit_records()]
        assert stamped == list(range(1, len(stamped) + 1)), "no gap, no repeat"
        assert len(site.forwarder.writes()) >= 2 and len(stamped) >= 3
        await site.processor.shutdown()

    def test_a_new_process_gets_a_new_boot_id(self):
        assert AuditSequence().boot_id != AuditSequence().boot_id

    def test_each_client_numbers_its_own_stream(self):
        """Two clients in one process must not share a counter: each stream would
        show a gap at every other record and read as loss."""
        forwarder = RecordingForwarder()
        clients = [CsipClient("https://example.com") for _ in range(2)]
        for client in clients:
            client.audit.attach_observer(forwarder.audit)

        for _ in range(3):
            for client in clients:
                client.audit.comms_loss(
                    transition="entered", threshold=1, simulated=False, at=0.0, elapsed_seconds=1
                )

        streams: dict[str, list[int]] = {}
        for record in forwarder.audit_records():
            streams.setdefault(record["boot_id"], []).append(record["seq"])
        assert sorted(streams.values()) == [[1, 2, 3], [1, 2, 3]]

    def test_with_no_observer_and_no_history_nothing_is_built(self):
        emitter = AuditEmitter()
        assert not emitter.enabled
        emitter.comms_loss(
            transition="entered", elapsed_seconds=1, threshold=1, simulated=False, at=0.0
        )
        assert emitter.emit_failures == {}


# -- A failing trail cannot stop a control --------------------------------------


class TestIsolation:
    @pytest.mark.asyncio
    async def test_a_failing_observer_does_not_stop_dispatch_or_responses(self):
        now = int(time.time())
        derc = _derc(0x01, start=now - 10)
        site = Site(_state([derc]))
        site.audit.attach_observer(Mock(side_effect=RuntimeError("observer bug")))
        site.telemetry.attach_observer(Mock(side_effect=RuntimeError("observer bug")))

        await site.processor.process_controls("/derp/1")

        assert site.processor._store.get(derc.m_rid.value).state == EventState.ACTIVE
        assert "update_op_mod_max_lim_w" in site.connectors[LFDI_1.hex()].last_control
        assert site.http.post.await_count == 2, "ACK and ACTIVE still posted"
        assert site.audit.observer_failures >= 1
        assert site.telemetry.observer_failures >= 1
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_failing_audit_emitter_does_not_stop_processing(self):
        now = int(time.time())
        derc = _derc(0x01, start=now - 10)
        site = Site(_state([derc]))
        broken = MagicMock(enabled=True)
        broken.event_transition.side_effect = RuntimeError("observer bug")
        site.processor._audit = broken

        await site.processor.process_controls("/derp/1")

        broken.event_transition.assert_called()
        assert site.processor._store.get(derc.m_rid.value).state == EventState.ACTIVE
        assert site.http.post.await_count == 2
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_failing_comms_loss_record_does_not_stop_entry(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client.audit.attach_observer(Mock(side_effect=RuntimeError("observer bug")))
        with patch.object(
            client._event_processor, "enter_comms_loss", new_callable=AsyncMock, return_value=[]
        ) as enter:
            await client._enter_comms_loss(1000)

        enter.assert_awaited_once()
        assert client._comms_loss.active is True
