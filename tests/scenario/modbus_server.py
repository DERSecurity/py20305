"""A lightweight SunSpec 700-series Modbus TCP server, for wire tests.

Serves a register image built with sunspec2's own model definitions --
Common (1), DER measurements (701), nameplate (702), enter service (703),
controls (704) and volt-var (705) -- so what the connector scans and parses
is packed by the same library it reads with. Handles the three function codes
a SunSpec exchange uses: read holding registers, and single/multiple register
writes. Every write lands in the image and in a log the tests assert on, and
any function can be made to answer with a Modbus exception.

A test double, not a device simulator. Its one behavior beyond its registers
is the curve-adoption handshake: a write of a model's ``AdptCrvReq`` sets its
``AdptCrvRslt`` to completed, as a device does once it has taken the curve.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

import sunspec2.device as ss
import sunspec2.mdef as mdef

BASE = 40000  # the standard SunSpec base address the client scans first


def point_address(image: bytes, model_id: int, point: str) -> int:
    """The absolute register address of ``point`` within ``model_id``.

    Walks the packed image for the model's position and adds the point's
    offset from sunspec2's model definition, so tests assert on real addresses
    instead of hand-counted ones. Only a model's fixed points are addressed
    here, which keeps it working for curve models, whose repeating groups
    follow them.
    """
    off = 4  # past 'SunS'
    while True:
        mid, length = struct.unpack(">HH", image[off : off + 4])
        if mid == 0xFFFF:
            raise KeyError(f"model {model_id} not in image")
        if mid == model_id:
            offset = 0
            for p in ss.get_model_def(model_id)[mdef.GROUP][mdef.POINTS]:
                if p[mdef.NAME] == point:
                    return BASE + off // 2 + offset
                offset += p[mdef.SIZE]
            raise KeyError(f"{point} is not a fixed point of model {model_id}")
        off += 4 + length * 2


def _curve_model(model_id: int, *, points: int = 4, curves: int = 3) -> ss.Model:
    """A curve model (705, 706, ...) with ``curves`` curves of ``points`` points each.

    The repeating groups are sized by count points, so the register block is
    built with the counts set before the model is parsed from it.
    """
    group = ss.get_model_def(model_id)[mdef.GROUP]
    fixed = sum(p[mdef.SIZE] for p in group[mdef.POINTS])
    curve = group[mdef.GROUPS][0]
    point = curve[mdef.GROUPS][0]
    curve_len = sum(p[mdef.SIZE] for p in curve[mdef.POINTS]) + points * sum(
        p[mdef.SIZE] for p in point[mdef.POINTS]
    )
    words = [0] * (fixed + curves * curve_len)
    offset = 0
    for p in group[mdef.POINTS]:
        if p[mdef.NAME] == "ID":
            words[offset] = model_id
        elif p[mdef.NAME] == "L":
            words[offset] = len(words) - 2
        elif p[mdef.NAME] == "NPt":
            words[offset] = points
        elif p[mdef.NAME] == "NCrv":
            words[offset] = curves
        offset += p[mdef.SIZE]
    data = b"".join(struct.pack(">H", w) for w in words)
    return ss.Model(model_id, data=data)


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
    rate_settings: bool = True,
    w_sf: int = 0,
    limit_setpoint: bool = True,
) -> bytes:
    """A plausible inverter: measurements in 701, ratings in 702, controls in 704.

    Scale factors are part of the wire contract, so they are deliberately
    non-zero where the standard commonly uses them: hertz carry -2
    (``6001`` -> 60.01 Hz), volts -1 (``2405`` -> 240.5 V).

    ``rate_settings`` controls whether model 702 implements the charge /
    discharge rate settings that the watts-typed active-power limits write.
    Neither carries an IEEE 1547 standards tag, so a device may omit them;
    pass ``False`` to model one that does and exercise the fallback route.

    ``w_sf`` is model 702's active-power scale factor. It defaults to 0, where
    raw registers and watts coincide and a scaling bug is invisible; pass a
    non-zero value (raw = watts / 10**w_sf) to make the encoding observable.
    """
    # Watt-valued points in 702 are stored raw and scaled by W_SF.
    def w_raw(watts: int) -> int:
        return round(watts / (10**w_sf))

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
    m702.points["W_SF"].value = w_sf
    # Both flavors: the scan gate requires the rating, and
    # fetch_configuration reads the adjusted setting.
    m702.points["WMaxRtg"].value = w_raw(w_max)
    m702.points["WMax"].value = w_raw(w_max)
    if rate_settings:
        # Targets of opModMaxLimWAbsorb (charge) and opModMaxLimWInject
        # (discharge). Seeded at the rating so they read back as implemented;
        # a control then adjusts them downward.
        m702.points["WChaRteMaxRtg"].value = w_raw(w_max)
        m702.points["WDisChaRteMaxRtg"].value = w_raw(w_max)
        m702.points["WChaRteMax"].value = w_raw(w_max)
        m702.points["WDisChaRteMax"].value = w_raw(w_max)

    m704 = ss.Model(704)
    m704.points["ID"].value = 704
    m704.points["L"].value = m704.len - 2
    # The limit's scale factor is part of the write contract: unimplemented,
    # the connector cannot know what its percent means on this device.
    m704.points["WMaxLimPct_SF"].value = 0
    m704.points["WMaxLimPctEna"].value = 0
    if limit_setpoint:
        # The setpoint itself, at "no limit". Almost every model 704 setpoint is
        # optional, so implementing it has to be stated: a device that leaves it
        # out is a device the connector must decline rather than write to, and
        # `limit_setpoint=False` models exactly that.
        m704.points["WMaxLimPct"].value = 100

    return _pack_models([common, m701, m702, m704])


#: The three-phase inverter :func:`build_full_der_image` serves, in engineering units.
FULL_DER = {
    "W": 4500,
    "VA": 4600,
    "Var": 300,
    "PF": 0.97,
    "A": 6.5,
    "LLV": 480.0,
    "Hz": 60.0,
    "WL": (1500, 1500, 1500),
    "VAL": (1530, 1535, 1535),
    "VarL": (100, 100, 100),
    "PFL": (0.97, 0.98, 0.97),
    "VL": (277.0, 277.5, 278.0),
    "AL": (2.2, 2.1, 2.2),
    "VL1L2": 480.0,
    "VL2L3": 481.0,
    "VL3L1": 479.0,
}


def build_full_der_image() -> bytes:
    """A running three-phase inverter with every model the connector reads and writes.

    Model 701 reports a three-phase device (``ACType`` 2) that is on
    (``St`` 1), running (``InvSt`` 3) and connected (``ConnSt`` 1), with an
    AC over-voltage alarm (``Alrm`` bit 10) and per-line readings. Model 702
    carries the ratings and settings with a current rating, 703 the enter
    service permit, 704 the controls and 705 three volt-var curves.
    """
    common = ss.Model(1)
    common.points["ID"].value = 1
    common.points["L"].value = common.len - 2
    common.points["Mn"].value = "ScenarioWorks"
    common.points["Md"].value = "SW-700-3"
    common.points["SN"].value = "0002"

    m701 = ss.Model(701)
    p = m701.points
    p["ID"].value = 701
    p["L"].value = m701.len - 2
    for sf in ("W_SF", "VA_SF", "Var_SF"):
        p[sf].value = 0
    p["PF_SF"].value = -2
    p["A_SF"].value = -1
    p["V_SF"].value = -1
    p["Hz_SF"].value = -2
    p["ACType"].value = 2
    p["St"].value = 1
    p["InvSt"].value = 3
    p["ConnSt"].value = 1
    p["Alrm"].value = 1 << 10
    p["W"].value = FULL_DER["W"]
    p["VA"].value = FULL_DER["VA"]
    p["Var"].value = FULL_DER["Var"]
    p["PF"].value = round(FULL_DER["PF"] * 100)
    p["A"].value = round(FULL_DER["A"] * 10)
    p["LLV"].value = round(FULL_DER["LLV"] * 10)
    p["Hz"].value = round(FULL_DER["Hz"] * 100)
    for line in (1, 2, 3):
        p[f"WL{line}"].value = FULL_DER["WL"][line - 1]
        p[f"VAL{line}"].value = FULL_DER["VAL"][line - 1]
        p[f"VarL{line}"].value = FULL_DER["VarL"][line - 1]
        p[f"PFL{line}"].value = round(FULL_DER["PFL"][line - 1] * 100)
        p[f"VL{line}"].value = round(FULL_DER["VL"][line - 1] * 10)
        p[f"AL{line}"].value = round(FULL_DER["AL"][line - 1] * 10)
    for key in ("VL1L2", "VL2L3", "VL3L1"):
        p[key].value = round(FULL_DER[key] * 10)

    m702 = ss.Model(702)
    q = m702.points
    q["ID"].value = 702
    q["L"].value = m702.len - 2
    for sf in ("W_SF", "VA_SF", "Var_SF"):
        q[sf].value = 0
    q["V_SF"].value = 0
    q["A_SF"].value = -1
    q["PF_SF"].value = -2
    q["WMaxRtg"].value = 10000
    q["VAMaxRtg"].value = 10000
    q["VarMaxInjRtg"].value = 4400
    q["VarMaxAbsRtg"].value = 4400
    q["VNomRtg"].value = 480
    q["AMaxRtg"].value = 140
    q["WMax"].value = 9000
    q["VAMax"].value = 10000
    q["VNom"].value = 480

    m703 = ss.Model(703)
    m703.points["ID"].value = 703
    m703.points["L"].value = m703.len - 2
    m703.points["ES"].value = 1

    m704 = ss.Model(704)
    m704.points["ID"].value = 704
    m704.points["L"].value = m704.len - 2
    for name in ("WMaxLimPct_SF", "WSet_SF", "WSetPct_SF", "PF_SF", "VarSet_SF", "VarSetPct_SF"):
        m704.points[name].value = 0
    m704.points["WMaxLimPctEna"].value = 0
    m704.points["WMaxLimPct"].value = 100
    m704.points["WSetEna"].value = 0
    m704.points["WSetMod"].value = 0
    m704.points["WSetPct"].value = 0

    m705 = _curve_model(705)
    m705.points["V_SF"].value = 0
    m705.points["DeptRef_SF"].value = 0
    m705.points["Ena"].value = 0

    return _pack_models([common, m701, m702, m703, m704, m705])


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
        #: AdptCrvReq register -> the AdptCrvRslt register a write of it completes.
        self._adoption: dict[int, int] = _curve_adoption(image)
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
            self._adopt(addr, 1)
            return pdu[:5]

        if function == 0x10:  # write multiple registers
            addr, count, byte_count = struct.unpack(">HHB", pdu[1:6])
            values = [
                struct.unpack(">H", pdu[6 + i * 2 : 8 + i * 2])[0] for i in range(count)
            ]
            for i, value in enumerate(values):
                self.registers[addr + i] = value
            self.writes.append(RegisterWrite(addr, values))
            self._adopt(addr, count)
            return pdu[:5]

        return struct.pack(">BB", function | 0x80, 1)  # illegal function

    def _adopt(self, addr: int, count: int) -> None:
        """Complete a curve adoption the write requested."""
        for request, result in self._adoption.items():
            if addr <= request < addr + count and self.registers[request]:
                self.registers[result] = 1  # completed


def _curve_adoption(image: bytes) -> dict[int, int]:
    """Return AdptCrvReq address -> AdptCrvRslt address for each curve model in ``image``."""
    out: dict[int, int] = {}
    off = 4
    while True:
        mid, length = struct.unpack(">HH", image[off : off + 4])
        if mid == 0xFFFF:
            return out
        if mid in (705, 706, 707, 708, 709, 710, 711, 712):
            group = ss.get_model_def(mid)[mdef.GROUP]
            names, pos = {}, 0
            for p in group[mdef.POINTS]:
                names[p[mdef.NAME]] = pos
                pos += p[mdef.SIZE]
            base = BASE + off // 2
            out[base + names["AdptCrvReq"]] = base + names["AdptCrvRslt"]
        off += 4 + length * 2
