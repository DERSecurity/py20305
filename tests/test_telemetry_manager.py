"""Tests for TelemetryManager."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from py20305.client.errors import Sep2ConnectionError, Sep2ProtocolError
from py20305.client.timebase import ServerTimebase
from py20305.connectors.base import (
    BaseConnector,
    ConnectorConnectionError,
    ReadingOverride,
)
from py20305.models.sep import MirrorUsagePointList
from py20305.readings import DirectConnectorSource, Quality
from py20305.telemetry.manager import DeviceTelemetryState, TelemetryManager

SAMPLE_LFDI = "1234567890abcdef1234567890abcdef12345678"
MUP_LIST_HREF = "/mup"


@pytest.fixture
def mock_client():
    """Mock Sep2Client."""
    client = AsyncMock()
    client.post = AsyncMock(return_value="/mup/device1")
    client.post_bytes = AsyncMock(return_value="/mup/device1")
    client.put_bytes = AsyncMock(return_value=204)
    client.get_list = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_connector():
    """Mock connector with monitoring data."""
    connector = AsyncMock(spec=BaseConnector)
    connector.fetch_monitoring = AsyncMock(
        return_value={
            "W": 1000.0,
            "Var": 200.0,
            "Hz": 60.0,
            "V": 240.0,
            "PF": 0.98,
            "VA": 1020.0,
            "A": 4.25,
        }
    )
    connector.fetch_status = AsyncMock(
        return_value={
            "alarmStatus": 0,
            "genConnectStatus": {"dateTime": 1700000000, "value": 1},
            "inverterStatus": {"dateTime": 1700000000, "value": 3},
            "readingTime": 1700000000,
        }
    )
    connector.fetch_availability = AsyncMock(
        return_value={
            "availabilityDuration": 86400,
            "readingTime": 1700000000,
            "statWAvail": {"value": 10000, "multiplier": 0},
        }
    )
    return connector


@pytest.fixture
def connector_resolver(mock_connector):
    """Async connector resolver that returns the mock.

    Async signature is required: TelemetryManager offloads
    first-touch construction to a worker via ``aresolve``.
    """

    async def resolver(lfdi: str) -> BaseConnector:
        return mock_connector

    return resolver


@pytest.fixture
def manager(mock_client, connector_resolver):
    """TelemetryManager instance."""
    return TelemetryManager(mock_client, MUP_LIST_HREF, connector_resolver)


class TestDeviceTelemetryState:
    """Tests for DeviceTelemetryState dataclass."""

    def test_default_values(self):
        state = DeviceTelemetryState(lfdi=SAMPLE_LFDI)
        assert state.lfdi == SAMPLE_LFDI
        assert state.mup_posted is False
        assert state.mup_href is None
        assert state.post_rate == 300
        assert state.log_event_list_href is None
        assert state.der_availability_href is None
        assert state.log_event_id_counter == 0


class TestTelemetryManagerInit:
    """Tests for TelemetryManager initialization."""

    def test_init(self, mock_client, connector_resolver):
        manager = TelemetryManager(mock_client, MUP_LIST_HREF, connector_resolver)
        # Held behind a callable now, so a rediscovered path is picked up
        # rather than snapshotted at construction.
        assert manager._mup_list_href_source() == MUP_LIST_HREF
        assert manager.active_devices == []


class TestStartMetering:
    """Tests for start_metering."""

    @pytest.mark.asyncio
    async def test_starts_polling(self, manager):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        assert SAMPLE_LFDI.lower() in manager.active_devices
        assert f"metering_{SAMPLE_LFDI.lower()}" in manager._scheduler.active_keys

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_lfdi_normalized_to_lowercase(self, manager):
        manager.start_metering(SAMPLE_LFDI.upper(), post_rate=300)

        assert SAMPLE_LFDI.lower() in manager.active_devices

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_creates_device_state(self, manager):
        manager.start_metering(SAMPLE_LFDI, post_rate=600)

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state is not None
        assert state.post_rate == 600
        assert state.mup_posted is False

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_stores_hrefs(self, manager):
        manager.start_metering(
            SAMPLE_LFDI,
            post_rate=300,
            log_event_list_href="/edev/1/log",
            der_availability_href="/der/1/avail",
        )

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.log_event_list_href == "/edev/1/log"
        assert state.der_availability_href == "/der/1/avail"

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_cleanup_on_shutdown(self, manager):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager.shutdown()

        assert manager.active_devices == []


class TestMeteringCycle:
    """Tests for the metering cycle."""

    @pytest.mark.asyncio
    async def test_first_cycle_posts_mup(self, manager, mock_client, mock_connector):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        # Manually trigger the cycle
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # Should have posted to MUP list
        mock_client.post_bytes.assert_called_once()
        call_args = mock_client.post_bytes.call_args
        assert call_args[0][0] == MUP_LIST_HREF

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_first_cycle_does_not_post_readings(self, manager, mock_client, mock_connector):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # Only one call (MUP), not readings
        assert mock_client.post_bytes.call_count == 1

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_second_cycle_posts_readings(self, manager, mock_client, mock_connector):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        # First cycle: posts MUP
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # Second cycle: posts readings
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        assert mock_client.post_bytes.call_count == 2
        # Second call should be to the MUP location
        second_call = mock_client.post_bytes.call_args_list[1]
        assert second_call[0][0] == "/mup/device1"

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_stores_mup_location_from_response(self, manager, mock_client, mock_connector):
        mock_client.post_bytes.return_value = "/mup/my-device-123"
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_href == "/mup/my-device-123"

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_handles_no_location_header(self, manager, mock_client, mock_connector):
        mock_client.post_bytes.return_value = None
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_posted is True
        assert state.mup_href is None

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_readback_post_rate(self, manager, mock_client, mock_connector):
        """IEEE B.17.1: server-preferred postRate is adopted after MUP POST."""
        server_mup = MagicMock()
        server_mup.post_rate = 600
        mock_client.get = AsyncMock(return_value=server_mup)

        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.post_rate == 600

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_readback_post_rate_unchanged(self, manager, mock_client, mock_connector):
        """postRate not updated when server returns same value."""
        server_mup = MagicMock()
        server_mup.post_rate = 300
        mock_client.get = AsyncMock(return_value=server_mup)

        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.post_rate == 300

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_readback_post_rate_failure_ignored(self, manager, mock_client, mock_connector):
        """Readback failure should not prevent MUP from being recorded."""
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_posted is True
        assert state.post_rate == 300  # Unchanged

        await manager.shutdown()


class TestMupDiscovery:
    """Tests for MUP href discovery."""

    @pytest.mark.asyncio
    async def test_discovers_mup_from_server_list(self, manager, mock_client, mock_connector):
        # Setup: MUP was posted but no location returned
        mock_client.post_bytes.return_value = None
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # Setup discovery response
        mup = MagicMock()
        mup.device_lfdi = bytes.fromhex(SAMPLE_LFDI)
        mup.href = "/mup/discovered-123"
        mup_list = MagicMock(spec=MirrorUsagePointList)
        mup_list.mirror_usage_point = [mup]
        mock_client.get_list.return_value = [mup_list]

        # Second cycle should discover the href
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_href == "/mup/discovered-123"

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_discovery_handles_empty_list(self, manager, mock_client, mock_connector):
        mock_client.post_bytes.return_value = None
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        mup_list = MagicMock(spec=MirrorUsagePointList)
        mup_list.mirror_usage_point = []
        mock_client.get_list.return_value = [mup_list]

        # Should not raise
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        await manager.shutdown()


class Test404Recovery:
    """Tests for 404 response handling."""

    @pytest.mark.asyncio
    async def test_404_resets_mup_state(self, manager, mock_client, mock_connector):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        # First cycle: posts MUP
        await manager._metering_cycle(SAMPLE_LFDI.lower())
        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_posted is True
        assert state.mup_href == "/mup/device1"

        # Simulate 404 on readings POST
        mock_client.post_bytes.side_effect = Sep2ProtocolError("Not found", 404)
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # State should be reset
        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_posted is False
        assert state.mup_href is None

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_404_allows_mup_recreation(self, manager, mock_client, mock_connector):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        # First cycle: posts MUP
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # Second cycle: 404
        mock_client.post_bytes.side_effect = Sep2ProtocolError("Not found", 404)
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # Third cycle: should post MUP again (not readings)
        mock_client.post_bytes.side_effect = None
        mock_client.post_bytes.return_value = "/mup/new-device"
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_posted is True
        assert state.mup_href == "/mup/new-device"

        await manager.shutdown()


class Test400Recovery:
    """Tests for 400 Bad Request response handling.

    a server's IEEE 2030.5-2023 §10.11.3 Rule h.3 enforcement on
    POST /mup/{N} produces 400 in two recoverable scenarios:

    1. Version skew (server upgraded ahead of an aggregator still using
       unstable / timestamp-baked mRIDs).
    2. Server-side state loss where /upt/{N}/mr was wiped but /mup/{N}
       survived.

    Both recover via re-POST /mup -- the same recovery path as 404. So
    the 400 branch resets ``mup_posted`` / ``mup_href`` and the next
    cycle re-creates the MUP from scratch, just like the 404 branch.
    """

    @pytest.mark.asyncio
    async def test_400_resets_mup_state(self, manager, mock_client, mock_connector):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        # First cycle: posts MUP successfully.
        await manager._metering_cycle(SAMPLE_LFDI.lower())
        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_posted is True
        assert state.mup_href == "/mup/device1"

        # Second cycle: server rejects readings POST with the Rule h.3
        # 400 message a server emits on a non-matching mRID without
        # ReadingType.
        mock_client.post_bytes.side_effect = Sep2ProtocolError(
            "MirrorMeterReading <mrid> has no readingType and "
            "doesn't match a prior MirrorMeterReading",
            400,
        )
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_posted is False
        assert state.mup_href is None
        # And the device is NOT marked as blocked -- 400 is recoverable,
        # unlike 403 (Rule e creator mismatch). The next cycle will
        # re-POST /mup, not skip silently.
        assert state.telemetry_blocked is False

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_400_allows_mup_recreation(self, manager, mock_client, mock_connector):
        """After a 400 reset, the next cycle re-creates the MUP and the
        cycle after that posts readings again -- end-to-end recovery."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        # Cycle 1: posts MUP.
        await manager._metering_cycle(SAMPLE_LFDI.lower())
        # Cycle 2: 400 on readings -> resets state.
        mock_client.post_bytes.side_effect = Sep2ProtocolError(
            "doesn't match a prior MirrorMeterReading", 400
        )
        await manager._metering_cycle(SAMPLE_LFDI.lower())
        # Cycle 3: should POST MUP again (not readings).
        mock_client.post_bytes.side_effect = None
        mock_client.post_bytes.return_value = "/mup/recreated"
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_posted is True
        assert state.mup_href == "/mup/recreated"

        await manager.shutdown()


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_handles_connector_error(self, manager, mock_client, mock_connector):
        mock_connector.fetch_monitoring.side_effect = RuntimeError("Connection failed")
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        # Should not raise
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # MUP should not be posted
        mock_client.post_bytes.assert_not_called()

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_handles_post_error(self, manager, mock_client, mock_connector):
        mock_client.post_bytes.side_effect = Sep2ProtocolError("Server error", 500)
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        # Should not raise
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # State should not be marked as posted
        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_posted is False

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_handles_unknown_device(self, manager):
        # Cycle for device that was never started
        await manager._metering_cycle("unknown_device")
        # Should not raise

    @pytest.mark.asyncio
    async def test_2018_schema_400_carries_compat_hint(self, monkeypatch, manager, mock_client):
        """A 400 from a 2030.5-2018 server should append a "try
        server_2018_compat=true" suffix to the operator-facing message
        so the operator can act on it from the UI."""
        from py20305 import diagnostics
        from py20305.diagnostics import DiagnosticsStore

        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        mock_client.server_2018_compat = False
        mock_client.post_bytes.side_effect = Sep2ProtocolError(
            "POST /mup returned 400: 'schemaVer' attribute is not declared.",
            400,
        )
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        warnings = fresh.snapshot()["warnings"]
        mup_warnings = [w for w in warnings if w["details"].get("op") == "mup_post"]
        assert len(mup_warnings) == 1
        assert "server_2018_compat=true" in mup_warnings[0]["message"]

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_non_2018_400_omits_compat_hint(self, monkeypatch, manager, mock_client):
        """A generic 4xx that doesn't match the 2018 signature must NOT
        suggest server_2018_compat -- false positives would send the
        operator chasing the wrong fix."""
        from py20305 import diagnostics
        from py20305.diagnostics import DiagnosticsStore

        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        mock_client.server_2018_compat = False
        mock_client.post_bytes.side_effect = Sep2ProtocolError(
            "POST /mup returned 400: payload too large", 400
        )
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        warnings = fresh.snapshot()["warnings"]
        mup_warnings = [w for w in warnings if w["details"].get("op") == "mup_post"]
        assert len(mup_warnings) == 1
        assert "server_2018_compat" not in mup_warnings[0]["message"]

        await manager.shutdown()


class Test403RuleEBlocking:
    """A 403 from the server (enforcing IEEE 2030.5-2023
    §10.11.3 Rule e -- only the MUP creator may POST to it) is unrecoverable
    by retry. The aggregator must mark the device as blocked, surface the
    failure to the operator at error level, and stop retrying so the server
    isn't hammered every cycle."""

    @pytest.mark.asyncio
    async def test_403_on_readings_marks_state_blocked(
        self, monkeypatch, manager, mock_client, mock_connector
    ):
        from py20305 import diagnostics
        from py20305.diagnostics import DiagnosticsStore

        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        # First cycle creates the MUP successfully.
        await manager._metering_cycle(SAMPLE_LFDI.lower())
        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_posted is True
        assert state.telemetry_blocked is False

        # Second cycle: server enforces Rule e and rejects the readings POST.
        mock_client.post_bytes.side_effect = Sep2ProtocolError(
            "Only the creator of /mup/1 may POST to it", 403
        )
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.telemetry_blocked is True
        assert state.blocked_reason is not None
        assert "Rule e" in state.blocked_reason

        # The 403 surfaced as an error-level diagnostic, not a warning, so
        # operators see it as actionable rather than buried among transient
        # failures.
        errors = fresh.snapshot()["errors"]
        rule_e = [e for e in errors if e["details"].get("op") == "readings_post"]
        assert len(rule_e) == 1
        assert rule_e[0]["details"]["status_code"] == 403
        assert "Rule e" in rule_e[0]["message"]

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_blocked_state_skips_subsequent_cycles(
        self, monkeypatch, manager, mock_client, mock_connector
    ):
        """Once blocked, subsequent metering cycles must NOT attempt
        another POST. Otherwise the server gets hammered every cycle and
        the operator sees a flood of duplicate diagnostics."""
        from py20305 import diagnostics
        from py20305.diagnostics import DiagnosticsStore

        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        # Cycle 1: create MUP (success).
        await manager._metering_cycle(SAMPLE_LFDI.lower())
        # Cycle 2: server returns 403 -> blocked.
        mock_client.post_bytes.side_effect = Sep2ProtocolError("Forbidden", 403)
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # Reset the post counter; cycles after the block must not call it.
        mock_client.post_bytes.reset_mock()
        mock_client.post_bytes.side_effect = None

        # Cycles 3 and 4: still blocked.
        await manager._metering_cycle(SAMPLE_LFDI.lower())
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        mock_client.post_bytes.assert_not_called()

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_403_on_initial_mup_post_blocks_too(
        self, monkeypatch, manager, mock_client, mock_connector
    ):
        """If the very first POST /mup returns 403 (the body's mRID is
        already owned by a different LFDI on the server), the same blocked
        semantics apply -- retry can't help."""
        from py20305 import diagnostics
        from py20305.diagnostics import DiagnosticsStore

        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        mock_client.post_bytes.side_effect = Sep2ProtocolError(
            "mRID already owned by a different LFDI", 403
        )
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.telemetry_blocked is True
        assert state.mup_posted is False  # never reached "posted" state

        errors = fresh.snapshot()["errors"]
        rule_e = [e for e in errors if e["details"].get("op") == "mup_post"]
        assert len(rule_e) == 1
        assert rule_e[0]["details"]["status_code"] == 403

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_restart_metering_clears_blocked_state(
        self, monkeypatch, manager, mock_client, mock_connector
    ):
        """``stop_metering`` followed by ``start_metering`` constructs a
        fresh DeviceTelemetryState, which clears the blocked flag. This
        is the documented way for an operator to recover after fixing
        the server-side identity issue."""
        from py20305 import diagnostics
        from py20305.diagnostics import DiagnosticsStore

        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI.lower())  # create MUP
        mock_client.post_bytes.side_effect = Sep2ProtocolError("Forbidden", 403)
        await manager._metering_cycle(SAMPLE_LFDI.lower())  # 403 -> blocked
        assert manager.get_device_state(SAMPLE_LFDI).telemetry_blocked is True

        # Operator clears the server-side issue and restarts metering.
        manager.stop_metering(SAMPLE_LFDI)
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.telemetry_blocked is False
        assert state.blocked_reason is None

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_idempotent_start_metering_preserves_block(
        self, monkeypatch, manager, mock_client, mock_connector
    ):
        """A bare ``start_metering`` re-call (no ``stop_metering`` first)
        must NOT silently clear the block -- otherwise a config reload
        or an idempotent ``Aggregator.start_device_telemetry`` re-run for
        an already-started device would resume hammering the unaccepting
        server. The carry-over in ``start_metering`` mirrors the existing
        treatment of ``mup_posted`` / ``mup_href``."""
        from py20305 import diagnostics
        from py20305.diagnostics import DiagnosticsStore

        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI.lower())  # create MUP
        mock_client.post_bytes.side_effect = Sep2ProtocolError("Forbidden", 403)
        await manager._metering_cycle(SAMPLE_LFDI.lower())  # 403 -> blocked

        blocked_before = manager.get_device_state(SAMPLE_LFDI)
        assert blocked_before.telemetry_blocked is True
        assert blocked_before.blocked_reason is not None
        original_reason = blocked_before.blocked_reason

        # Re-call start_metering for the same device (no stop_metering first).
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        state_after = manager.get_device_state(SAMPLE_LFDI)
        assert state_after.telemetry_blocked is True
        assert state_after.blocked_reason == original_reason

        # And the next cycle still skips the POST (the block is honoured).
        mock_client.post_bytes.reset_mock()
        mock_client.post_bytes.side_effect = None
        await manager._metering_cycle(SAMPLE_LFDI.lower())
        mock_client.post_bytes.assert_not_called()

        await manager.shutdown()


class TestShutdown:
    """Tests for shutdown behavior."""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_all_tasks(self, manager):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        manager.start_metering("abcd" * 10, post_rate=300)

        await manager.shutdown()

        assert manager.active_devices == []
        assert manager._scheduler.active_keys == []

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self, manager):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        await manager.shutdown()
        await manager.shutdown()  # Should not raise


class TestStopMetering:
    """Tests for stop_metering."""

    @pytest.mark.asyncio
    async def test_removes_device(self, manager):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        manager.stop_metering(SAMPLE_LFDI)

        assert manager.get_device_state(SAMPLE_LFDI) is None

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_stop_nonexistent_device_no_error(self, manager):
        # Should not raise
        manager.stop_metering("nonexistent")

        await manager.shutdown()


class TestLogEventPosting:
    """Tests for LogEvent POST in metering cycle."""

    @pytest.mark.asyncio
    async def test_posts_log_event_when_alarm_nonzero(self, manager, mock_client, mock_connector):
        mock_connector.fetch_status.return_value = {"alarmStatus": 1}

        manager.start_metering(
            SAMPLE_LFDI,
            post_rate=300,
            log_event_list_href="/edev/1/log",
        )

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # Should have posted MUP + LogEvent = 2 post_bytes calls
        assert mock_client.post_bytes.call_count == 2
        log_call = mock_client.post_bytes.call_args_list[1]
        assert log_call[0][0] == "/edev/1/log"

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_no_log_event_when_alarm_zero(self, manager, mock_client, mock_connector):
        mock_connector.fetch_status.return_value = {"alarmStatus": 0}

        manager.start_metering(
            SAMPLE_LFDI,
            post_rate=300,
            log_event_list_href="/edev/1/log",
        )

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # Only MUP post
        assert mock_client.post_bytes.call_count == 1

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_no_log_event_when_href_none(self, manager, mock_client, mock_connector):
        mock_connector.fetch_status.return_value = {"alarmStatus": 5}

        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # Only MUP post (no log event since no href)
        assert mock_client.post_bytes.call_count == 1
        mock_connector.fetch_status.assert_not_called()

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_log_event_id_increments(self, manager, mock_client, mock_connector):
        mock_connector.fetch_status.return_value = {"alarmStatus": 3}

        manager.start_metering(
            SAMPLE_LFDI,
            post_rate=300,
            log_event_list_href="/edev/1/log",
        )

        await manager._metering_cycle(SAMPLE_LFDI.lower())
        await manager._metering_cycle(SAMPLE_LFDI.lower())

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.log_event_id_counter == 2

        await manager.shutdown()


class TestDerAvailabilityPut:
    """Tests for DERAvailability PUT in metering cycle."""

    @pytest.mark.asyncio
    async def test_puts_availability_when_href_present(self, manager, mock_client, mock_connector):
        manager.start_metering(
            SAMPLE_LFDI,
            post_rate=300,
            der_availability_href="/der/1/avail",
        )

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        mock_client.put_bytes.assert_called_once()
        put_call = mock_client.put_bytes.call_args
        assert put_call[0][0] == "/der/1/avail"
        assert isinstance(put_call[0][1], bytes)

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_skips_availability_when_no_href(self, manager, mock_client, mock_connector):
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        mock_client.put_bytes.assert_not_called()

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_availability_calls_fetch_availability(
        self, manager, mock_client, mock_connector
    ):
        manager.start_metering(
            SAMPLE_LFDI,
            post_rate=300,
            der_availability_href="/der/1/avail",
        )

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        mock_connector.fetch_availability.assert_called_once()

        await manager.shutdown()


class TestErrorIsolation:
    """Tests that failures in one posting stage don't affect others."""

    @pytest.mark.asyncio
    async def test_log_event_failure_does_not_break_availability(
        self, manager, mock_client, mock_connector
    ):
        mock_connector.fetch_status.side_effect = RuntimeError("Status fetch failed")

        manager.start_metering(
            SAMPLE_LFDI,
            post_rate=300,
            log_event_list_href="/edev/1/log",
            der_availability_href="/der/1/avail",
        )

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # MUP should still be posted
        assert mock_client.post_bytes.call_count == 1
        # DERAvailability should still be PUT
        mock_client.put_bytes.assert_called_once()

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_availability_failure_does_not_break_mup(
        self, manager, mock_client, mock_connector
    ):
        mock_connector.fetch_availability.side_effect = RuntimeError("Avail fetch failed")

        manager.start_metering(
            SAMPLE_LFDI,
            post_rate=300,
            der_availability_href="/der/1/avail",
        )

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # MUP should still be posted
        assert mock_client.post_bytes.call_count == 1

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.mup_posted is True

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_all_three_stages_execute_independently(
        self, manager, mock_client, mock_connector
    ):
        mock_connector.fetch_status.return_value = {"alarmStatus": 5}

        manager.start_metering(
            SAMPLE_LFDI,
            post_rate=300,
            log_event_list_href="/edev/1/log",
            der_availability_href="/der/1/avail",
        )

        await manager._metering_cycle(SAMPLE_LFDI.lower())

        # MUP POST + one LogEvent POST per set alarm bit. alarmStatus=5 is
        # bits 0 and 2 (Over Current + Under Voltage), which CSIP s5.2.5.3
        # reports as two separate LogEvents -> 1 + 2 = 3 post_bytes calls.
        assert mock_client.post_bytes.call_count == 3
        # DERAvailability PUT = 1 put_bytes call
        mock_client.put_bytes.assert_called_once()

        await manager.shutdown()


class TestLogEventBurst:
    """Tests for post_log_event_burst and find_device_with_log_events."""

    @pytest.mark.asyncio
    async def test_posts_five_events(self, manager, mock_client):
        """Burst posts 5 events and increments counter by 5."""
        mock_client.server_2018_compat = False
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/lel")

        posted = await manager.post_log_event_burst(SAMPLE_LFDI, alarm_status=1, interval=0)

        assert posted == 5
        assert mock_client.post_bytes.await_count == 5
        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.log_event_id_counter == 5

    @pytest.mark.asyncio
    async def test_burst_increments_counter_across_calls(self, manager, mock_client):
        """Counter persists across multiple burst calls."""
        mock_client.server_2018_compat = False
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/lel")

        await manager.post_log_event_burst(SAMPLE_LFDI, interval=0)
        await manager.post_log_event_burst(SAMPLE_LFDI, interval=0)

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.log_event_id_counter == 10

    @pytest.mark.asyncio
    async def test_burst_with_interval_spacing(self, manager, mock_client):
        """Events are spaced by the interval parameter."""
        import asyncio

        mock_client.server_2018_compat = False
        # Insert device state directly to avoid metering scheduler interference
        manager._devices[SAMPLE_LFDI.lower()] = DeviceTelemetryState(
            lfdi=SAMPLE_LFDI.lower(), post_rate=300, log_event_list_href="/edev/1/lel"
        )

        post_times = []

        async def record_time(*args, **kwargs):
            post_times.append(asyncio.get_event_loop().time())

        mock_client.post_bytes = AsyncMock(side_effect=record_time)

        await manager.post_log_event_burst(SAMPLE_LFDI, interval=0.05)

        assert len(post_times) == 5
        for i in range(1, len(post_times)):
            gap = post_times[i] - post_times[i - 1]
            assert gap >= 0.04, f"Gap between POST {i - 1} and {i} was {gap:.3f}s"

    @pytest.mark.asyncio
    async def test_burst_stops_on_post_failure(self, manager, mock_client):
        """Burst stops after first failed POST and returns partial count."""
        mock_client.server_2018_compat = False
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/lel")

        call_count = 0

        async def fail_on_third(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise Exception("POST failed")
            return "/edev/1/lel/1"

        mock_client.post_bytes = AsyncMock(side_effect=fail_on_third)

        posted = await manager.post_log_event_burst(SAMPLE_LFDI, interval=0)
        assert posted == 2

    @pytest.mark.asyncio
    async def test_burst_unknown_device_raises(self, manager):
        """ValueError raised for unknown LFDI."""
        with pytest.raises(ValueError, match="not found"):
            await manager.post_log_event_burst("unknown_lfdi")

    @pytest.mark.asyncio
    async def test_burst_no_href_raises(self, manager):
        """ValueError raised when device has no log_event_list_href."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href=None)

        with pytest.raises(ValueError, match="no log_event_list_href"):
            await manager.post_log_event_burst(SAMPLE_LFDI)

    @pytest.mark.asyncio
    async def test_find_device_with_log_events(self, manager):
        """Returns LFDI of first device with log_event_list_href."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/lel")

        result = manager.find_device_with_log_events()
        assert result == SAMPLE_LFDI.lower()

    @pytest.mark.asyncio
    async def test_find_device_with_log_events_none(self, manager):
        """Returns None when no device has log_event_list_href."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href=None)

        result = manager.find_device_with_log_events()
        assert result is None

    def test_find_device_with_log_events_empty(self, manager):
        """Returns None when no devices registered."""
        result = manager.find_device_with_log_events()
        assert result is None


class TestOnlyMirrorDiscoveredDevices:
    """``is_provisioned`` gate (only_mirror_discovered_devices).

    When set, the metering cycle mirrors a device only if the gate returns True
    (e.g. the LFDI is in the server's EndDeviceList). The check runs each cycle,
    so mirroring starts/stops as the device is provisioned/removed.
    """

    @pytest.mark.asyncio
    async def test_gate_skips_unprovisioned_device(
        self, mock_client, connector_resolver, mock_connector
    ):
        manager = TelemetryManager(
            mock_client, MUP_LIST_HREF, connector_resolver, is_provisioned=lambda _: False
        )
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI)
        # Gate returns before reading the device or posting the MUP.
        mock_connector.fetch_monitoring.assert_not_awaited()
        mock_client.post_bytes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gate_allows_provisioned_device(
        self, mock_client, connector_resolver, mock_connector
    ):
        manager = TelemetryManager(
            mock_client, MUP_LIST_HREF, connector_resolver, is_provisioned=lambda _: True
        )
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI)
        mock_connector.fetch_monitoring.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_gate_mirrors_every_device(
        self, mock_client, connector_resolver, mock_connector
    ):
        # Default (is_provisioned=None) preserves prior behavior: mirror always.
        manager = TelemetryManager(mock_client, MUP_LIST_HREF, connector_resolver)
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI)
        mock_connector.fetch_monitoring.assert_awaited()

    @pytest.mark.asyncio
    async def test_gate_is_dynamic_across_cycles(
        self, mock_client, connector_resolver, mock_connector
    ):
        # A device absent at first, then provisioned -> starts mirroring without restart.
        provisioned = {"v": False}
        manager = TelemetryManager(
            mock_client,
            MUP_LIST_HREF,
            connector_resolver,
            is_provisioned=lambda _: provisioned["v"],
        )
        manager.start_metering(SAMPLE_LFDI, post_rate=300)

        await manager._metering_cycle(SAMPLE_LFDI)
        mock_connector.fetch_monitoring.assert_not_awaited()

        provisioned["v"] = True
        await manager._metering_cycle(SAMPLE_LFDI)
        mock_connector.fetch_monitoring.assert_awaited()


class TestConnectorReadingOverrides:
    """The connector's reading_overrides() flow through to the MUP/readings builders."""

    @pytest.mark.asyncio
    async def test_overrides_reach_mup_builder(self, manager, mock_connector):
        from unittest.mock import patch

        from py20305.connectors.base import ReadingOverride
        from py20305.telemetry import manager as mgr_mod

        ov = {"W": ReadingOverride(accumulation_behaviour=9)}
        mock_connector.reading_overrides.return_value = ov
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        with patch.object(mgr_mod, "create_mup", wraps=mgr_mod.create_mup) as spy:
            await manager._metering_cycle(SAMPLE_LFDI)
        # create_mup(lfdi, monitoring, post_rate, overrides). The container is
        # a copy -- the store does not alias connector-owned dicts -- so assert
        # on what carries the semantics: the same override under the same key.
        delivered = spy.call_args.args[3]
        assert delivered == ov
        assert delivered["W"] is ov["W"]
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_overrides_reach_readings_builder(self, manager, mock_connector):
        from unittest.mock import patch

        from py20305.connectors.base import ReadingOverride
        from py20305.telemetry import manager as mgr_mod

        ov = {"W": ReadingOverride(quality_flags=0x10)}
        mock_connector.reading_overrides.return_value = ov
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI)  # cycle 1: MUP
        with patch.object(
            mgr_mod, "create_meter_reading_list", wraps=mgr_mod.create_meter_reading_list
        ) as spy:
            await manager._metering_cycle(SAMPLE_LFDI)  # cycle 2: readings
        delivered = spy.call_args.kwargs["overrides"]
        assert delivered == ov
        assert delivered["W"] is ov["W"]
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_non_dict_overrides_coerced_to_none(self, manager, mock_client, mock_connector):
        # A connector returning a non-dict (or a bare mock) must not break the cycle.
        mock_connector.reading_overrides.return_value = "nonsense"
        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI)
        mock_client.post_bytes.assert_called_once()  # MUP still posted
        await manager.shutdown()


class TestTimebaseAdoption:
    def test_manager_adopts_real_client_timebase(self):
        client = AsyncMock()
        client.timebase = ServerTimebase()
        mgr = TelemetryManager(client, MUP_LIST_HREF, lambda lfdi: MagicMock())
        assert mgr._timebase is client.timebase

    def test_mock_client_falls_back_to_identity_timebase(self, mock_client):
        mgr = TelemetryManager(mock_client, MUP_LIST_HREF, lambda lfdi: MagicMock())
        assert isinstance(mgr._timebase, ServerTimebase)
        assert mgr._timebase is not mock_client.timebase  # Mock attr, not adopted


class TestAlarmLogEventTransitions:
    """CSIP s4.6.3 / s5.2.5.3: alarms and their return-to-normal messages are
    reported as they occur, each with its IEEE-assigned LogEvent code."""

    @staticmethod
    def _codes_posted(mock_client) -> list[int]:
        """LogEvent codes from the POSTed bodies, in order."""
        codes = []
        for call in mock_client.post_bytes.call_args_list:
            body = call.args[1] if len(call.args) > 1 else call.kwargs.get("body", b"")
            text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
            m = re.search(r"<logEventCode>(\d+)</logEventCode>", text)
            if m:
                codes.append(int(m.group(1)))
        return codes

    async def _cycle(self, manager, mock_connector, alarm: int) -> None:
        mock_connector.fetch_status.return_value = {"alarmStatus": alarm}
        await manager._metering_cycle(SAMPLE_LFDI.lower())

    @pytest.mark.asyncio
    async def test_local_emergency_bit_reports_code_14(self, manager, mock_client, mock_connector):
        """The reported case: SunSpec bit 7 (0x80) must report Local Emergency
        as logEventCode 14 -- not the raw bitmap 128."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        await self._cycle(manager, mock_connector, 0x80)

        assert 14 in self._codes_posted(mock_client)
        assert 128 not in self._codes_posted(mock_client)
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_clearing_alarm_reports_rtn_code(self, manager, mock_client, mock_connector):
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        await self._cycle(manager, mock_connector, 0x80)
        mock_client.post_bytes.reset_mock()

        await self._cycle(manager, mock_connector, 0)

        # Local Emergency RTN = 15
        assert self._codes_posted(mock_client) == [15]
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_persisting_alarm_is_not_reposted(self, manager, mock_client, mock_connector):
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        await self._cycle(manager, mock_connector, 0x80)
        mock_client.post_bytes.reset_mock()

        await self._cycle(manager, mock_connector, 0x80)

        assert self._codes_posted(mock_client) == []
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_multi_bit_alarm_fans_out(self, manager, mock_client, mock_connector):
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        # bits 7 and 8 -> Local Emergency (14) + Remote Emergency (16)
        await self._cycle(manager, mock_connector, 0x180)

        assert self._codes_posted(mock_client) == [14, 16]
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_reserved_bits_emit_no_log_event(self, manager, mock_client, mock_connector):
        """SunSpec populates bits 11+, which IEEE reserves; they ride in
        DERStatus alarmStatus but have no LogEvent code."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        await self._cycle(manager, mock_connector, 1 << 11)

        assert self._codes_posted(mock_client) == []
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_posted_events_recorded_for_api(self, manager, mock_client, mock_connector):
        """Automatically-posted alarms must show up in GET /api/v1/logevents --
        previously only the manual trigger path recorded them."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        await self._cycle(manager, mock_connector, 0x80)

        recorded = manager.get_all_posted_log_events()
        assert [e["logEventCode"] for e in recorded] == [14]
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_alarm_state_survives_idempotent_restart(
        self, manager, mock_client, mock_connector
    ):
        """A config-reload style re-start_metering must not re-announce an
        already-active alarm."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        await self._cycle(manager, mock_connector, 0x80)
        mock_client.post_bytes.reset_mock()

        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        await self._cycle(manager, mock_connector, 0x80)

        assert self._codes_posted(mock_client) == []
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_failed_post_leaves_transition_pending_for_retry(
        self, manager, mock_client, mock_connector
    ):
        """A POST failure must not advance the baseline past the undelivered
        transition -- CSIP s4.6.3 requires the alarm to be reported, so the next
        cycle has to retry it."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        mock_client.post_bytes.side_effect = Sep2ConnectionError("server down")

        await self._cycle(manager, mock_connector, 0x80)

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.last_alarm_status == 0, "baseline advanced despite failed POST"

        # Server recovers: the same alarm is still active and must now be sent.
        mock_client.post_bytes.side_effect = None
        mock_client.post_bytes.reset_mock()
        await self._cycle(manager, mock_connector, 0x80)

        assert self._codes_posted(mock_client) == [14]
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_partial_failure_retries_only_undelivered_bit(
        self, manager, mock_client, mock_connector
    ):
        """Bits already delivered stay synced; only the failed one retries."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        # side_effect ordering for this first metering cycle: call 1 is stage
        # 1's MUP POST (mup_posted is False, so readings don't start until the
        # next cycle -- manager.py stage 1), then one LogEvent POST per alarm
        # bit. So call 2 = code 14 (succeeds), call 3 = code 16 (fails).
        mock_client.post_bytes.side_effect = [None, None, Sep2ConnectionError("boom")]

        # bits 7 and 8 -> codes 14 then 16; 16 fails.
        await self._cycle(manager, mock_connector, 0x180)

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.last_alarm_status == 0x80, "delivered bit 7 should be synced, bit 8 not"

        mock_client.post_bytes.side_effect = None
        mock_client.post_bytes.reset_mock()
        await self._cycle(manager, mock_connector, 0x180)

        assert self._codes_posted(mock_client) == [16], "only the undelivered bit retries"
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_reserved_bit_change_does_not_wedge_the_diff(
        self, manager, mock_client, mock_connector
    ):
        """Reserved bits produce no LogEvent, so they sync into the baseline
        immediately rather than re-appearing in the diff forever."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        await self._cycle(manager, mock_connector, 1 << 11)

        state = manager.get_device_state(SAMPLE_LFDI)
        assert state.last_alarm_status == 1 << 11
        await manager.shutdown()


class TestBurstPostCountAccuracy:
    """The burst's return value feeds the management API's ``posted`` count and
    its ``log_event_ids`` range, so it must equal the number of POSTs actually
    made -- and BASIC-027 wants exactly _LOG_EVENT_BURST_COUNT events."""

    @pytest.mark.asyncio
    async def test_single_bit_posts_exactly_five_with_one_code(
        self, manager, mock_client, mock_connector
    ):
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")

        posted = await manager.post_log_event_burst(SAMPLE_LFDI, alarm_status=0x80, interval=0)

        assert posted == 5
        assert mock_client.post_bytes.await_count == 5, "posted count must equal real POSTs"
        codes = TestAlarmLogEventTransitions._codes_posted(mock_client)
        assert codes == [14] * 5
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_multi_bit_rejected(self, manager, mock_client, mock_connector):
        """Previously this posted 5x2 events while reporting 5, so the API's
        log_event_ids range was short by half."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")

        with pytest.raises(ValueError, match="exactly one IEEE-mapped alarm bit"):
            await manager.post_log_event_burst(SAMPLE_LFDI, alarm_status=0x180, interval=0)

        assert mock_client.post_bytes.await_count == 0
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_zero_alarm_rejected(self, manager, mock_client, mock_connector):
        """alarm_status=0 has no mapped bit: it used to report 5 posted while
        sending nothing."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")

        with pytest.raises(ValueError, match="exactly one IEEE-mapped alarm bit"):
            await manager.post_log_event_burst(SAMPLE_LFDI, alarm_status=0, interval=0)

        assert mock_client.post_bytes.await_count == 0
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_reserved_only_alarm_rejected(self, manager, mock_client, mock_connector):
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")

        with pytest.raises(ValueError, match="exactly one IEEE-mapped alarm bit"):
            await manager.post_log_event_burst(SAMPLE_LFDI, alarm_status=1 << 11, interval=0)

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_log_event_ids_match_posted_events(self, manager, mock_client, mock_connector):
        """The API derives log_event_ids as range(first_id, first_id + posted);
        that range must line up with the IDs actually sent."""
        manager.start_metering(SAMPLE_LFDI, post_rate=300, log_event_list_href="/edev/1/log")
        state = manager.get_device_state(SAMPLE_LFDI)
        first_id = state.log_event_id_counter

        posted = await manager.post_log_event_burst(SAMPLE_LFDI, alarm_status=0x80, interval=0)

        recorded = [e["logEventID"] for e in manager.get_all_posted_log_events()]
        assert recorded == list(range(first_id, first_id + posted))
        await manager.shutdown()


class TestRunsWithoutAStore:
    """The telemetry path must work with no point store behind it.

    A deployment with one upstream interface has nothing to coalesce: one
    consumer, one cadence, no second reader to share a cached value with. The
    manager must therefore be constructible and drivable without the store,
    demand registry or planner existing at all -- that is what keeps the
    fan-out machinery an aggregator concern rather than a telemetry dependency.
    """

    async def test_metering_cycle_needs_no_store(self) -> None:
        """A default-constructed manager reads its connector and posts."""
        connector = AsyncMock(spec=BaseConnector)
        connector.fetch_monitoring = AsyncMock(return_value={"W": 1000, "W__quality": 0})
        connector.reading_overrides = MagicMock(return_value={})

        client = AsyncMock()
        client.get_bytes = AsyncMock(return_value=b"")
        client.post_bytes = AsyncMock(return_value="/mup/1")
        client.put_bytes = AsyncMock()

        manager = TelemetryManager(
            client=client,
            mup_list_href=MUP_LIST_HREF,
            connector_resolver=lambda _lfdi: connector,
        )
        # No store, no acquisition service, no demand registry were supplied,
        # and none is reachable from the manager.
        assert not hasattr(manager, "_store")
        assert not hasattr(manager, "_acquisition")
        assert not hasattr(manager, "_demand")

        manager.start_metering(SAMPLE_LFDI, post_rate=300)
        await manager._metering_cycle(SAMPLE_LFDI)  # creates the MirrorUsagePoint
        await manager._metering_cycle(SAMPLE_LFDI)  # posts a reading

        assert connector.fetch_monitoring.await_count >= 1
        assert client.post_bytes.await_count >= 1
        await manager.shutdown()

    async def test_default_source_reads_every_cycle(self) -> None:
        """Caching is the store's job, so the direct source must not do it."""
        connector = AsyncMock(spec=BaseConnector)
        connector.fetch_monitoring = AsyncMock(return_value={"W": 1000})
        connector.reading_overrides = MagicMock(return_value={})

        source = DirectConnectorSource(lambda _lfdi: connector)
        source.declare(SAMPLE_LFDI, 300.0)  # inert, but part of the contract

        first = await source.read(SAMPLE_LFDI)
        second = await source.read(SAMPLE_LFDI)

        assert connector.fetch_monitoring.await_count == 2
        assert second.sequence > first.sequence

    async def test_direct_source_reports_comm_lost_against_history(self) -> None:
        """A failed read keeps the last known values rather than emptying out."""
        connector = AsyncMock(spec=BaseConnector)
        connector.fetch_monitoring = AsyncMock(return_value={"W": 1000})
        connector.reading_overrides = MagicMock(return_value={})
        source = DirectConnectorSource(lambda _lfdi: connector)

        good = await source.read(SAMPLE_LFDI)
        assert good.quality is Quality.GOOD

        connector.fetch_monitoring = AsyncMock(side_effect=ConnectorConnectionError("down"))
        lost = await source.read(SAMPLE_LFDI)

        assert lost.quality is Quality.COMM_LOST
        assert lost.entries["W"].value == 1000
        assert lost.last_success == good.last_success
        assert isinstance(lost.error, ConnectorConnectionError)

    async def test_comm_lost_entries_carry_comm_lost_quality(self) -> None:
        """A retained value must not still claim GOOD under a COMM_LOST snapshot.

        Matches PointStore.record_failure: value and source_timestamp are kept
        so the age grows through the outage, but the per-entry verdict moves
        with the snapshot's.
        """
        connector = AsyncMock(spec=BaseConnector)
        connector.fetch_monitoring = AsyncMock(return_value={"W": 1000})
        connector.reading_overrides = MagicMock(return_value={})
        source = DirectConnectorSource(lambda _lfdi: connector)

        good = await source.read(SAMPLE_LFDI)
        connector.fetch_monitoring = AsyncMock(side_effect=ConnectorConnectionError("down"))
        lost = await source.read(SAMPLE_LFDI)

        assert lost.quality is Quality.COMM_LOST
        assert lost.entries["W"].quality is Quality.COMM_LOST
        assert lost.entries["W"].value == 1000
        assert lost.entries["W"].source_timestamp == good.entries["W"].source_timestamp

    async def test_timestamp_excludes_connector_resolution(self) -> None:
        """First-touch construction must not be counted against the sample's age.

        A real connector's first resolution runs a device scan and readiness
        retries; charging that to source_timestamp would report the reading as
        seconds older than it is.
        """
        clock = iter([100.0, 200.0, 300.0])
        connector = AsyncMock(spec=BaseConnector)
        connector.fetch_monitoring = AsyncMock(return_value={"W": 1})
        connector.reading_overrides = MagicMock(return_value={})

        async def slow_resolve(_lfdi: str) -> BaseConnector:
            return connector

        source = DirectConnectorSource(slow_resolve, clock=lambda: next(clock))
        snapshot = await source.read(SAMPLE_LFDI)

        # The first clock read happens after resolution, not before it.
        assert snapshot.last_success == 100.0

    async def test_direct_source_drops_non_int_protocol_quality(self) -> None:
        """A malformed "<key>__quality" must not reach the reading.

        The MUP path validates qualityFlags again downstream, so carrying a bad
        value through would log the same complaint once per cycle forever. The
        store path drops it; this path has to agree, or a deployment running
        telemetry without a store gets different behavior from the same
        connector.
        """
        connector = AsyncMock(spec=BaseConnector)
        connector.fetch_monitoring = AsyncMock(
            return_value={
                "W": 1000,
                "W__quality": "not-an-int",
                "V": 240,
                "V__quality": True,  # bool is an int subclass; would post as 1
                "Hz": 60,
                "Hz__quality": 8,
            }
        )
        connector.reading_overrides = MagicMock(return_value={})
        source = DirectConnectorSource(lambda _lfdi: connector)

        snapshot = await source.read(SAMPLE_LFDI)

        assert snapshot.entries["W"].protocol_quality is None
        assert snapshot.entries["V"].protocol_quality is None
        assert snapshot.entries["Hz"].protocol_quality == 8
        # The quality companions are not themselves measured quantities.
        assert set(snapshot.entries) == {"W", "V", "Hz"}

    async def test_direct_source_drops_malformed_override_values(self) -> None:
        """A non-ReadingOverride value never reaches the snapshot.

        ``DeviceSnapshot.reading_overrides`` is typed
        ``Mapping[str, ReadingOverride]`` and the values come from third-party
        code through an ``Any``, so the filter is what keeps that annotation
        honest. Both acquisition paths share it; this pins the direct one.
        """
        connector = MagicMock(spec=BaseConnector)
        connector.fetch_monitoring = AsyncMock(return_value={"W": 1000})
        good = ReadingOverride(uom=38)
        connector.reading_overrides = MagicMock(
            return_value={"W": good, "V": "not-an-override", "Hz": None}
        )
        source = DirectConnectorSource(lambda _lfdi: connector)

        snapshot = await source.read(SAMPLE_LFDI)

        assert dict(snapshot.reading_overrides) == {"W": good}

    async def test_direct_source_survives_a_non_dict_override_map(self) -> None:
        """A connector returning the wrong type entirely yields no overrides."""
        connector = MagicMock(spec=BaseConnector)
        connector.fetch_monitoring = AsyncMock(return_value={"W": 1000})
        connector.reading_overrides = MagicMock(return_value=["not", "a", "dict"])
        source = DirectConnectorSource(lambda _lfdi: connector)

        snapshot = await source.read(SAMPLE_LFDI)

        assert dict(snapshot.reading_overrides) == {}
        assert snapshot.quality is Quality.GOOD
