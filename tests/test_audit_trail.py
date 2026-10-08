"""Tests for the control audit trail.

Three layers. The configuration tests pin what an operator can switch on. The
record tests drive the processor, the dispatcher and the client end to end and
read back what reached the transport, because the point of the trail is that
the pieces join: a lifecycle record and the writes it caused must carry the same
identifiers. The isolation tests prove a failing trail cannot stop a control.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from pydantic import ValidationError

from py20305.client.csip_client import CsipClient
from py20305.client.state import DerProgramState, DiscoveredState, EndDeviceState
from py20305.commands import CommandOrigin
from py20305.connectors.device_telemetry import DeviceTelemetryEmitter
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.connectors.print_demo import PrintDemoConnector
from py20305.events.comms_loss import CommsLossState
from py20305.events.processor import EventProcessor
from py20305.events.state_machine import EventState
from py20305.forwarders.audit import AuditEmitter, AuditSequence, mrid_hex
from py20305.forwarders.base import EventFrame
from py20305.forwarders.config import (
    AuditConfig,
    DeviceTelemetryConfig,
    ForwarderConfig,
    MQTTForwarderConfig,
)
from py20305.forwarders.manager import ForwarderManager
from py20305.forwarders.mqtt_forwarder import MQTTForwarder
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

LFDI_1 = b"\xaa" * 20
LFDI_2 = b"\xbb" * 20
DDERC_MRID = b"\x20" * 16
AUDIT_TOPIC = "out/der-events"


class RecordingForwarder:
    """Stands in for the forwarder manager, keeping what was queued."""

    name = "recording"

    def __init__(self) -> None:
        self.events: list[EventFrame] = []

    def queue_event(self, event: EventFrame) -> None:
        self.events.append(event)

    def get_statistics(self) -> dict[str, Any]:
        return {}

    def audit_records(self, kind: str | None = None) -> list[dict[str, Any]]:
        out = [e.payload for e in self.events if e.topic_suffix == AUDIT_TOPIC]
        return [r for r in out if kind is None or r["kind"] == kind]

    def writes(self) -> list[dict[str, Any]]:
        """Each write's indexed fields and decoded body, in publication order."""
        out = []
        for event in self.events:
            if event.topic_suffix == AUDIT_TOPIC or event.kind != "device-telemetry":
                continue
            if event.payload["direction"] != "downstream":
                continue
            out.append(
                {
                    **event.payload["protocol_data"]["extra"],
                    "body": json.loads(event.payload["payload"]["data"]),
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
    ) -> None:
        self.forwarder = RecordingForwarder()
        self.sequence = AuditSequence()
        self.connectors: dict[str, PrintDemoConnector] = {}
        # ``local`` are devices behind a group lookup: the server never names
        # them, and only the dispatcher's by-LFDI path reaches them.
        for lfdi in [edev.lfdi for edev in state.end_devices.values()] + (local or []):
            connector = PrintDemoConnector()
            if lfdi in (failing or set()):
                connector.update_p_lim = AsyncMock(side_effect=RuntimeError("register locked"))  # type: ignore[method-assign]
            self.connectors[lfdi.hex()] = connector
        hrefs = {href: edev.lfdi.hex() for href, edev in state.end_devices.items()}

        registry = Mock()
        registry.get_connector.side_effect = lambda lfdi: (
            Mock(aresolve=AsyncMock(return_value=self.connectors[lfdi]))
            if lfdi in self.connectors
            else None
        )
        audit_config = AuditConfig(enabled=audit)
        self.telemetry = DeviceTelemetryEmitter(
            self.forwarder,  # type: ignore[arg-type]
            DeviceTelemetryConfig(enabled=device_telemetry),
            client_id="site-a",
            audit=audit_config,
            sequence=self.sequence,
        )
        self.dispatcher = ConnectorDispatcher(registry, hrefs.get, telemetry=self.telemetry)
        self.audit = AuditEmitter(
            self.forwarder,  # type: ignore[arg-type]
            audit_config,
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


# -- Configuration -----------------------------------------------------------


class TestAuditConfig:
    def test_off_by_default(self):
        assert ForwarderConfig().audit.enabled is False
        assert ForwarderConfig().audit.topic_suffix == AUDIT_TOPIC

    def test_a_custom_topic_under_out_is_accepted(self):
        assert AuditConfig(topic_suffix="/out/audit/").topic_suffix == "out/audit"

    @pytest.mark.parametrize("suffix", ["audit", "in/der-events", "out/", "out/+/events", "out/#"])
    def test_a_topic_outside_out_or_with_a_wildcard_is_rejected(self, suffix):
        with pytest.raises(ValidationError):
            AuditConfig(topic_suffix=suffix)

    @pytest.mark.parametrize(
        "other",
        [
            {},
            {"device_telemetry": {"topic_suffix": "out/devices"}},
            {"connection_telemetry": {"topic_suffix": "out/devices"}},
        ],
        ids=["protocol-messages", "device-telemetry", "connection-telemetry"],
    )
    def test_a_topic_shared_with_another_stream_is_rejected(self, other):
        suffix = "out/2030-5-raw" if not other else "out/devices"
        with pytest.raises(ValidationError, match="topic of their own"):
            ForwarderConfig(audit={"topic_suffix": suffix}, **other)


# -- Which switch publishes what (D3) ---------------------------------------


class TestWhatEachSwitchPublishes:
    @pytest.mark.parametrize(
        ("audit", "device_telemetry", "reads", "writes", "events"),
        [
            (False, False, 0, 0, 0),
            (False, True, 1, 1, 0),
            (True, False, 0, 1, 1),
            (True, True, 1, 1, 1),
        ],
    )
    def test_the_table(self, audit, device_telemetry, reads, writes, events):
        forwarder = RecordingForwarder()
        audit_config = AuditConfig(enabled=audit)
        telemetry = DeviceTelemetryEmitter(
            forwarder,  # type: ignore[arg-type]
            DeviceTelemetryConfig(enabled=device_telemetry),
            audit=audit_config,
        )
        emitter = AuditEmitter(forwarder, audit_config)  # type: ignore[arg-type]

        telemetry.record_read("dev1", {"W": 100})
        telemetry.record_write("dev1", "p_lim", {"value": 1}, origin="local_api")
        emitter.comms_loss(
            transition="entered", elapsed_seconds=900, threshold=900, simulated=False, at=0.0
        )

        directions = [
            e.payload.get("direction") for e in forwarder.events if e.topic_suffix != AUDIT_TOPIC
        ]
        assert directions.count("upstream") == reads
        assert directions.count("downstream") == writes, "each write is published at most once"
        assert len(forwarder.audit_records()) == events

    def test_the_sequence_rides_on_writes_only_with_the_audit_switch(self):
        forwarder = RecordingForwarder()
        telemetry = DeviceTelemetryEmitter(
            forwarder,  # type: ignore[arg-type]
            DeviceTelemetryConfig(enabled=True),
        )
        telemetry.record_write("dev1", "p_lim", {"value": 1}, origin="local_api")

        extra = forwarder.writes()[0]
        assert extra["origin"] == "local_api", "the cause fields ride on every published write"
        assert "seq" not in extra and "boot_id" not in extra


# -- What each write says caused it (D5) -------------------------------------


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
            LFDI_1.hex(), "p_lim", {"value": 50}, origin="local_api"
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

    def test_mrids_are_uppercase_hex(self):
        assert mrid_hex(bytes.fromhex("0a1b2c")) == "0A1B2C"


# -- Lifecycle records (D6) --------------------------------------------------


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
            r for r in site.forwarder.audit_records("der_event") if r["to_state"] == "superseded"
        ]
        assert partial["event_mrid"] == HEX_2
        assert partial["superseded_by"] == HEX_1
        assert partial["superseded_lfdis"] == [LFDI_1.hex()]
        assert LFDI_1.hex() in partial["superseded_modes"]
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
        opted_out = await site.processor.enter_comms_loss()

        assert opted_out == [b"\x01" * 16]
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
    async def test_a_repoll_of_an_unchanged_event_emits_nothing(self):
        now = int(time.time())
        site = Site(_state([_derc(0x01, start=now + 100)]))
        await site.processor.process_controls("/derp/1")
        before = len(site.forwarder.audit_records())

        await site.processor.process_controls("/derp/1")
        await site.processor.process_controls("/derp/1")

        assert len(site.forwarder.audit_records()) == before == 1
        await site.processor.shutdown()


# -- Loss-of-communications records (D7) -------------------------------------


class TestCommsLossRecords:
    def _client(self) -> tuple[CsipClient, RecordingForwarder]:
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        forwarder = RecordingForwarder()
        client.audit.attach_forwarder(forwarder)  # type: ignore[arg-type]
        client.audit.configure(AuditConfig(enabled=True), client_id="site-a")
        return client, forwarder

    @pytest.mark.asyncio
    async def test_entry_lists_the_opted_out_events(self):
        client, forwarder = self._client()
        with patch.object(
            client._event_processor,
            "enter_comms_loss",
            new_callable=AsyncMock,
            return_value=[b"\x01" * 16],
        ):
            await client._enter_comms_loss(1000)

        (record,) = forwarder.audit_records("comms_loss")
        assert record["transition"] == "entered"
        assert (record["elapsed_seconds"], record["threshold"]) == (1000, 900)
        assert record["simulated"] is False
        assert record["opted_out_mrids"] == [HEX_1]

    @pytest.mark.asyncio
    async def test_entry_is_timed_when_flagged_not_when_the_fallbacks_finish(self):
        client, forwarder = self._client()
        clock = [5000.0]
        client._timebase.now = lambda *_a, **_k: clock[0]  # type: ignore[method-assign]

        async def slow_fleet() -> list[bytes]:
            clock[0] += 120.0
            return []

        with patch.object(client._event_processor, "enter_comms_loss", side_effect=slow_fleet):
            await client._enter_comms_loss(1000)

        (record,) = forwarder.audit_records("comms_loss")
        assert record["at"] == 5000.0

    @pytest.mark.asyncio
    async def test_clear_reports_the_resume_boundary(self):
        client, forwarder = self._client()
        client._comms_loss.active = True
        boundary = int(time.time()) + 3600
        client._comms_loss.resume_after_epoch = boundary
        client._http._last_contact_epoch = int(time.time())
        with (
            patch.object(
                client, "_server_lists_end_device", new_callable=AsyncMock, return_value=True
            ),
            patch.object(client, "trigger_rediscovery", new_callable=AsyncMock, return_value=True),
        ):
            await client._recover_from_comms_loss()

        (record,) = forwarder.audit_records("comms_loss")
        assert record["transition"] == "cleared"
        assert record["resume_after"] == boundary
        assert "opted_out_mrids" not in record

    @pytest.mark.asyncio
    async def test_a_recovery_that_does_not_complete_records_nothing(self):
        client, forwarder = self._client()
        client._comms_loss.active = True
        with patch.object(
            client, "trigger_rediscovery", new_callable=AsyncMock, return_value=False
        ):
            await client._recover_from_comms_loss()

        assert forwarder.audit_records() == []


# -- Detecting loss (D10) ----------------------------------------------------


class TestSequence:
    @pytest.mark.asyncio
    async def test_one_counter_across_writes_and_events(self):
        now = int(time.time())
        derc = _derc(0x01, start=now + 100)
        site = Site(_state([derc], dderc=_dderc()))

        await site.processor.process_controls("/derp/1")
        record = site.processor._store.get(derc.m_rid.value)
        await site.processor._on_activation(record)
        await site.processor._on_completion(record)

        stamped = [
            (e.payload.get("seq") or e.payload["protocol_data"]["extra"]["seq"])
            for e in site.forwarder.events
        ]
        assert stamped == list(range(1, len(stamped) + 1)), "no gap, no repeat, both streams"
        assert len(site.forwarder.writes()) >= 2 and len(site.forwarder.audit_records()) >= 3
        await site.processor.shutdown()

    def test_a_new_process_gets_a_new_boot_id(self):
        assert AuditSequence().boot_id != AuditSequence().boot_id

    def test_a_stopped_transport_drops_and_counts_and_leaves_a_gap(self):
        manager = ForwarderManager()
        recording = RecordingForwarder()
        manager.add_forwarder(recording)  # type: ignore[arg-type]
        emitter = AuditEmitter(manager, AuditConfig(enabled=True), sequence=AuditSequence())

        def entered() -> None:
            emitter.comms_loss(
                transition="entered", elapsed_seconds=1, threshold=1, simulated=False, at=0.0
            )

        entered()  # not running: dropped
        manager._running = True
        entered()

        assert manager.get_statistics()["events_dropped_not_running"] == 1
        assert [r["seq"] for r in recording.audit_records()] == [2]

    @pytest.mark.asyncio
    async def test_a_forwarder_whose_broker_was_down_counts_what_it_missed(self):
        """The manager runs while a forwarder it holds failed to start. What
        that forwarder would have silently dropped is counted against it."""

        class Unreachable(RecordingForwarder):
            name = "unreachable"
            fail = True

            async def start(self) -> None:
                if self.fail:
                    raise ConnectionRefusedError("broker down")

            async def stop(self) -> None:
                pass

        down = Unreachable()
        manager = ForwarderManager()
        manager.add_forwarder(down)  # type: ignore[arg-type]
        await manager.start()
        emitter = AuditEmitter(manager, AuditConfig(enabled=True), sequence=AuditSequence())

        def entered() -> None:
            emitter.comms_loss(
                transition="entered", elapsed_seconds=1, threshold=1, simulated=False, at=0.0
            )

        entered()
        down.fail = False
        await manager.retry_failed()
        entered()

        stats = manager.get_statistics()
        assert stats["events_dropped_by_failed_forwarder"] == {"unreachable": 1}
        assert [r["seq"] for r in down.audit_records()] == [2]

    def test_a_full_queue_drops_and_counts_and_leaves_a_gap(self):
        forwarder = MQTTForwarder(MQTTForwarderConfig(endpoint="broker", port=1883), queue_size=2)
        forwarder._running = True
        emitter = AuditEmitter(forwarder, AuditConfig(enabled=True), sequence=AuditSequence())  # type: ignore[arg-type]

        for _ in range(3):
            emitter.comms_loss(
                transition="entered", elapsed_seconds=1, threshold=1, simulated=False, at=0.0
            )

        kept = []
        while not forwarder._capture_queue.empty():
            kept.append(forwarder._capture_queue.get_nowait().payload["seq"])
        assert kept == [2, 3], "the oldest went, and the gap at 1 shows it"
        assert forwarder.get_statistics()["messages_dropped"] == 1


# -- A failing trail cannot stop a control (D11) -----------------------------


class TestIsolation:
    @pytest.mark.asyncio
    async def test_a_failing_transport_does_not_stop_dispatch_or_responses(self):
        now = int(time.time())
        derc = _derc(0x01, start=now - 10)
        site = Site(_state([derc]))
        site.forwarder.queue_event = Mock(side_effect=RuntimeError("broker gone"))  # type: ignore[method-assign]

        await site.processor.process_controls("/derp/1")

        assert site.processor._store.get(derc.m_rid.value).state == EventState.ACTIVE
        assert "update_p_lim" in site.connectors[LFDI_1.hex()].last_control
        assert site.http.post.await_count == 2, "ACK and ACTIVE still posted"
        assert site.audit.emit_failures["der_event"] >= 1
        await site.processor.shutdown()

    @pytest.mark.asyncio
    async def test_a_failing_observer_does_not_stop_processing(self):
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
        broken = Mock()
        broken.queue_event.side_effect = RuntimeError("broker gone")
        client.audit.attach_forwarder(broken)
        client.audit.configure(AuditConfig(enabled=True))
        with patch.object(
            client._event_processor, "enter_comms_loss", new_callable=AsyncMock, return_value=[]
        ) as enter:
            await client._enter_comms_loss(1000)

        enter.assert_awaited_once()
        assert client._comms_loss.active is True
