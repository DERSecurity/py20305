"""Record the MirrorUsagePoint release 0.16.x built for the full scenario inverter.

Run with a py20305 0.16.x install that has the ``sunspec`` extra, from the
repository root::

    python -m tests.scenario.record_sunspec_0_16

It serves :func:`build_full_der_image`, reads it with 0.16's SunSpec
connector, and writes each MirrorMeterReading's mRID and ReadingType, per
reading profile, to ``tests/fixtures/sunspec_0_16_mup.json``.
``test_sunspec_e2e.py`` compares the current connector's output with it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from tests.scenario.modbus_server import SunSpecModbusServer, build_full_der_image
from tests.scenario.support import free_port

LFDI = "ab" * 20
FIXTURE = Path(__file__).parent.parent / "fixtures" / "sunspec_0_16_mup.json"


def _reading_type(rt: Any) -> dict[str, Any]:
    def code(field: Any) -> int | None:
        return None if field is None else int(field.value)

    return {
        "uom": code(rt.uom),
        "phase": code(rt.phase),
        "kind": code(rt.kind),
        "data_qualifier": code(rt.data_qualifier),
        "accumulation_behaviour": code(rt.accumulation_behaviour),
        "flow_direction": code(rt.flow_direction),
        "multiplier": code(rt.power_of_ten_multiplier),
    }


async def main() -> None:
    from py20305.connectors.sunspec import ConnectorSunSpec  # 0.16.x
    from py20305.telemetry.mup import ReadingProfile, create_mup

    server = SunSpecModbusServer(build_full_der_image(), free_port())
    await server.start()
    try:
        connector = await asyncio.to_thread(
            ConnectorSunSpec, host="127.0.0.1", port=server.port, timeout=2, scan_retries=1
        )
        monitoring = await connector.fetch_monitoring()
    finally:
        await server.close()

    out: dict[str, Any] = {}
    for profile in ReadingProfile:
        mup = create_mup(LFDI, monitoring, 300, profile=profile)
        out[profile.value] = {
            mmr.description: {
                "mRID": mmr.m_rid.value.hex().upper(),
                **_reading_type(mmr.reading_type),
            }
            for mmr in mup.mirror_meter_reading
        }
    FIXTURE.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
