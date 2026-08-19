"""A lightweight SunSpec 700-series Modbus TCP server, for wire tests.

Serves a register image built with sunspec2's own model definitions --
Common (1), DER measurements (701), nameplate (702) and controls (704) --
so what the connector scans and parses is packed by the same library it
reads with. Handles the three function codes a SunSpec exchange uses:
read holding registers, and single/multiple register writes. Every write
lands in the image and in a log the tests assert on, and any function
can be made to answer with a Modbus exception.

A test double, not a device simulator: it has no behavior beyond its
registers.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

import sunspec2.device as ss

BASE = 40000  # the standard SunSpec base address the client scans first


def point_address(image: bytes, model_id: int, point: str) -> int:
    """The absolute register address of ``point`` within ``model_id``.

    Walks the packed image for the model's position and asks sunspec2 for the
    point's offset inside it, so tests assert on real addresses instead of
    hand-counted ones.
    """
    off = 4  # past 'SunS'
    while True:
        mid, length = struct.unpack(">HH", image[off : off + 4])
        if mid == 0xFFFF:
            raise KeyError(f"model {model_id} not in image")
        if mid == model_id:
            # sunspec2 point offsets already count the ID/L header registers.
            model = ss.Model(model_id)
            return BASE + off // 2 + model.points[point].offset
        off += 4 + length * 2


def _pack_models(models: list[ss.Model]) -> bytes:
    """'SunS', each model's registers, then the end-of-map marker."""
    image = b"SunS"
    for model in models:
        image += model.get_mb()
    image += struct.pack(">HH", 0xFFFF, 0)
    return image


def build_der_image(
    *,
    watts: int = 5000,
    volts: int = 2405,
    hertz: int = 6001,
    w_max: int = 10000,
) -> bytes:
    """A plausible inverter: measurements in 701, ratings in 702, controls in 704.

    Scale factors are part of the wire contract, so they are deliberately
    non-zero where the standard commonly uses them: hertz carry -2
    (``6001`` -> 60.01 Hz), volts -1 (``2405`` -> 240.5 V).
    """
    common = ss.Model(1)
    common.points["ID"].value = 1
    common.points["L"].value = common.len - 2
    common.points["Mn"].value = "ScenarioWorks"
    common.points["Md"].value = "SW-700"
    common.points["SN"].value = "0001"

    m701 = ss.Model(701)
    m701.points["ID"].value = 701
    m701.points["L"].value = m701.len - 2
    m701.points["W_SF"].value = 0
    m701.points["V_SF"].value = -1
    m701.points["Hz_SF"].value = -2
    m701.points["W"].value = watts
    m701.points["LLV"].value = volts
    m701.points["Hz"].value = hertz

    m702 = ss.Model(702)
    m702.points["ID"].value = 702
    m702.points["L"].value = m702.len - 2
    m702.points["W_SF"].value = 0
    # Both flavors: the scan gate requires the rating, and
    # fetch_configuration reads the adjusted setting.
    m702.points["WMaxRtg"].value = w_max
    m702.points["WMax"].value = w_max

    m704 = ss.Model(704)
    m704.points["ID"].value = 704
    m704.points["L"].value = m704.len - 2
    # The limit's scale factor is part of the write contract: unimplemented,
    # the connector cannot know what its percent means on this device.
    m704.points["WMaxLimPct_SF"].value = 0
    m704.points["WMaxLimPctEna"].value = 0

    return _pack_models([common, m701, m702, m704])


@dataclass
class RegisterWrite:
    address: int
    values: list[int]


class SunSpecModbusServer:
    """Registers at BASE, three function codes, a write log, and faults."""

    def __init__(self, image: bytes, port: int) -> None:
        self.port = port
        self.registers: dict[int, int] = {
            BASE + i // 2: struct.unpack(">H", image[i : i + 2])[0]
            for i in range(0, len(image), 2)
        }
        self.writes: list[RegisterWrite] = []
        self._fail_next: int | None = None  # a Modbus exception code
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    def fail_next(self, exception_code: int) -> None:
        """The next request answers with this Modbus exception, once."""
        self._fail_next = exception_code

    def register(self, offset_in_map: int) -> int:
        """Read one register by its offset within the SunSpec map."""
        return self.registers[BASE + offset_in_map]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._session, "127.0.0.1", self.port)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            # wait_closed waits for live sessions too, and a Modbus client
            # holds its socket open between exchanges -- close them, or the
            # teardown waits on a connection nobody will ever finish.
            for writer in list(self._writers):
                writer.close()
            await self._server.wait_closed()

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            while True:
                header = await reader.readexactly(7)
                tid, pid, length, unit = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                reply = self._respond(pdu)
                writer.write(struct.pack(">HHHB", tid, pid, len(reply) + 1, unit) + reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def _respond(self, pdu: bytes) -> bytes:
        function = pdu[0]
        if self._fail_next is not None:
            code, self._fail_next = self._fail_next, None
            return struct.pack(">BB", function | 0x80, code)

        if function == 0x03:  # read holding registers
            addr, count = struct.unpack(">HH", pdu[1:5])
            try:
                words = [self.registers[addr + i] for i in range(count)]
            except KeyError:
                return struct.pack(">BB", function | 0x80, 2)  # illegal data address
            payload = b"".join(struct.pack(">H", w) for w in words)
            return struct.pack(">BB", function, len(payload)) + payload

        if function == 0x06:  # write single register
            addr, value = struct.unpack(">HH", pdu[1:5])
            self.registers[addr] = value
            self.writes.append(RegisterWrite(addr, [value]))
            return pdu[:5]

        if function == 0x10:  # write multiple registers
            addr, count, byte_count = struct.unpack(">HHB", pdu[1:6])
            values = [
                struct.unpack(">H", pdu[6 + i * 2 : 8 + i * 2])[0] for i in range(count)
            ]
            for i, value in enumerate(values):
                self.registers[addr + i] = value
            self.writes.append(RegisterWrite(addr, values))
            return pdu[:5]

        return struct.pack(">BB", function | 0x80, 1)  # illegal function
