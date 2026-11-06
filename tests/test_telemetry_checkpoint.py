"""Registered readings survive a restart through a checkpoint.

IEEE 2030.5-2023 section 10.10.3 rule n) forbids changing the ReadingType of a
MirrorMeterReading once created. Under the default reading profile a power
reading registers with the flowDirection its sign gives at that moment, so a
client that forgets the registration at a restart can register the same mRID
again with the opposite flowDirection.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from py20305.connectors.base import BaseConnector
from py20305.connectors.reading import Reading
from py20305.models.sep import MirrorUsagePoint
from py20305.telemetry.checkpoint import load_checkpoint, save_checkpoint
from py20305.telemetry.manager import TelemetryManager
from py20305.telemetry.mup import ReadingProfile
from py20305.xml.serialization import from_xml

LFDI = "1234567890abcdef1234567890abcdef12345678"
MUP_LIST_HREF = "/mup"
FORWARD = 1
REVERSE = 19


def _client() -> AsyncMock:
    client = AsyncMock()
    client.post_bytes = AsyncMock(return_value="/mup/device1")
    client.get_list = AsyncMock(return_value=[])
    client.get = AsyncMock(return_value=SimpleNamespace(post_rate=None))
    return client


def _manager(
    watts: float, *, profile: ReadingProfile = ReadingProfile.DEFAULT
) -> tuple[TelemetryManager, AsyncMock]:
    client = _client()
    connector = AsyncMock(spec=BaseConnector)
    connector.fetch_monitoring = AsyncMock(return_value=[Reading(uom=38, value=watts)])

    async def resolver(_lfdi: str) -> BaseConnector:
        return connector

    return TelemetryManager(client, MUP_LIST_HREF, resolver, reading_profile=profile), client


async def _registered_flow(manager: TelemetryManager, client: AsyncMock) -> dict[bytes, int | None]:
    """Run a cycle and return the flowDirection of each reading the MUP POST registered."""
    manager.start_metering(LFDI, post_rate=300)
    await manager._metering_cycle(LFDI)
    mups = [
        from_xml(call.args[1], MirrorUsagePoint)
        for call in client.post_bytes.call_args_list
        if call.args[0] == MUP_LIST_HREF
    ]
    assert mups, "no MirrorUsagePoint was posted"
    flows: dict[bytes, int | None] = {}
    for reading in mups[-1].mirror_meter_reading:
        flow = reading.reading_type.flow_direction
        flows[reading.m_rid.value] = flow.value if flow else None
    return flows


async def test_a_restored_registration_keeps_its_flow_direction() -> None:
    first, first_client = _manager(1000.0)
    registered = await _registered_flow(first, first_client)
    assert set(registered.values()) == {FORWARD}
    checkpoint = json.loads(json.dumps(first.to_checkpoint()))
    await first.shutdown()

    # The device now absorbs power. Without the checkpoint it would register Reverse.
    second, second_client = _manager(-1000.0)
    assert second.restore_from_checkpoint(checkpoint) == 1
    assert await _registered_flow(second, second_client) == registered
    await second.shutdown()


async def test_without_a_checkpoint_the_current_sign_decides(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager, client = _manager(-1000.0)

    with caplog.at_level(logging.WARNING):
        registered = await _registered_flow(manager, client)

    assert set(registered.values()) == {REVERSE}
    warnings = [r.getMessage() for r in caplog.records if "No saved registration" in r.getMessage()]
    assert len(warnings) == 1 and LFDI[:8] in warnings[0]
    await manager.shutdown()


async def test_a_restored_device_does_not_get_the_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first, first_client = _manager(1000.0)
    await _registered_flow(first, first_client)
    checkpoint = first.to_checkpoint()
    second, second_client = _manager(1000.0)
    second.restore_from_checkpoint(checkpoint)
    caplog.clear()

    with caplog.at_level(logging.WARNING):
        await _registered_flow(second, second_client)

    assert not [r for r in caplog.records if "No saved registration" in r.getMessage()]
    await first.shutdown()
    await second.shutdown()


async def test_a_registration_change_calls_the_callback() -> None:
    manager, client = _manager(1000.0)
    calls: list[dict] = []
    manager.set_on_registration_change(lambda: calls.append(manager.to_checkpoint()))

    await _registered_flow(manager, client)

    assert len(calls) == 1
    assert [d["lfdi"] for d in calls[0]["devices"]] == [LFDI]
    await manager.shutdown()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(version=0),
        lambda c: c.update(version=2),
        lambda c: c.pop("version"),
        lambda c: c.update(devices="nope"),
        lambda c: c.update(reading_profile="signed_load_convention"),
    ],
    ids=["old", "new", "unversioned", "devices-not-a-list", "other-profile"],
)
async def test_an_unusable_checkpoint_is_ignored_with_a_warning(
    mutate, caplog: pytest.LogCaptureFixture
) -> None:
    first, first_client = _manager(1000.0)
    await _registered_flow(first, first_client)
    checkpoint = first.to_checkpoint()
    mutate(checkpoint)
    second, second_client = _manager(-1000.0)

    with caplog.at_level(logging.WARNING):
        assert second.restore_from_checkpoint(checkpoint) == 0
        registered = await _registered_flow(second, second_client)

    assert any("Ignoring the telemetry checkpoint" in r.getMessage() for r in caplog.records)
    assert set(registered.values()) == {REVERSE}
    await first.shutdown()
    await second.shutdown()


@pytest.mark.parametrize("data", [None, [], "text", 3])
def test_data_of_the_wrong_type_is_ignored(data: object) -> None:
    manager, _client_mock = _manager(1000.0)
    assert manager.restore_from_checkpoint(data) == 0


async def test_an_entry_that_cannot_be_parsed_is_skipped_and_the_rest_restored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first, first_client = _manager(1000.0)
    await _registered_flow(first, first_client)
    checkpoint = first.to_checkpoint()
    good = checkpoint["devices"][0]
    other = "ab" * 20
    checkpoint["devices"] = [
        "not an object",
        {**good, "lfdi": other},  # the mRID belongs to another device
        {"lfdi": LFDI, "mup_mrid": good["mup_mrid"], "readings": [{"mrid": "zz"}]},
        {**good, "readings": [good["readings"][0], {"mrid": "00"}, 7]},
    ]
    second, _client2 = _manager(1000.0)

    with caplog.at_level(logging.WARNING):
        assert second.restore_from_checkpoint(checkpoint) == 1

    assert len(caplog.records) >= 4
    assert [d["lfdi"] for d in second.to_checkpoint()["devices"]] == [LFDI]
    assert len(second.to_checkpoint()["devices"][0]["readings"]) == 1
    await first.shutdown()


def test_the_state_file_is_written_atomically_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "state" / "telemetry.json"

    save_checkpoint(path, {"version": 1, "devices": []})
    save_checkpoint(path, {"version": 1, "devices": [], "again": True})

    assert load_checkpoint(path) == {"version": 1, "devices": [], "again": True}
    assert [p.name for p in path.parent.iterdir()] == ["telemetry.json"], "no temporary file left"


def test_a_missing_or_corrupt_state_file_reads_as_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert load_checkpoint(tmp_path / "absent.json") is None
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert load_checkpoint(corrupt) is None

    assert any("not valid JSON" in r.getMessage() for r in caplog.records)
