"""Reading SunSpec meter models (201-204, 211-214 and 220).

A meter's monitoring is reported in the frame of the role it is configured
with, so telemetry encodes it like any other device:

- ``site_meter``: positive real power is import, from the grid to the premises.
- ``der_meter``: positive real power is the DER's output.

Reactive power takes the same orientation as real power.

The SunSpec meter models do not say which sign of ``W`` is import, and vendors
differ. The energy totals do say which way energy flowed: ``TotWhImp`` is
"Total Real Energy Imported" and ``TotWhExp`` "Total Real Energy Exported".
:class:`PowerSignCalibrator` learns the sign by watching which total rises
while ``W`` is not zero. Until it has, real and reactive power are left out of
the monitoring rather than guessed. An operator can set the sign instead.

For a meter on a DER's circuit, export is taken to be the DER's output, which
is how such a meter is normally installed.

This module depends on nothing else in this package, like the rest of the
SunSpec core.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: SunSpec meter model IDs, in the order a device's models are searched: the
#: integer models with scale factors, then the float models, then the secure
#: AC meter.
METER_MODEL_IDS: tuple[int, ...] = (201, 202, 203, 204, 211, 212, 213, 214, 220)

#: The SunSpec model 701 ACType each meter model corresponds to, which tells
#: telemetry how many lines to report: 0 single phase, 1 split phase, 2 three
#: phase. Model 220 carries totals only.
METER_AC_TYPE: dict[int, int] = {
    201: 0,
    202: 1,
    203: 2,
    204: 2,
    211: 0,
    212: 1,
    213: 2,
    214: 2,
}

#: Agreeing observations needed before an automatic calibration is accepted.
#: One observation can straddle a change of direction between two reads.
CALIBRATION_OBSERVATIONS = 2

#: Consecutive contradicting observations before the sign in use is reported
#: as doubtful.
CONTRADICTION_LIMIT = 3

#: Power-sign settings and the import sign each fixes.
_FIXED_SIGNS: dict[str, int] = {"import_positive": 1, "export_positive": -1}

#: Per-phase meter points, the per-line monitoring keys they fill, and how the
#: value is reported: oriented like real power, as a magnitude, or as read.
_PHASE_POINTS: tuple[tuple[str, str, str], ...] = (
    ("Wph", "WL", "oriented"),
    ("VARph", "VarL", "oriented"),
    ("PhVph", "VL", "value"),
    ("PFph", "PFL", "magnitude"),
    ("VAph", "VAL", "value"),
    ("Aph", "AL", "value"),
)

_LINE_TO_LINE_POINTS: tuple[tuple[str, str], ...] = (
    ("PPVphAB", "VL1L2"),
    ("PPVphBC", "VL2L3"),
    ("PPVphCA", "VL3L1"),
)


def _point(model: Any, name: str) -> Any:
    """Return the scaled value of point ``name``, or None when absent or unimplemented."""
    point = getattr(model, name, None)
    if point is None:
        return None
    return getattr(point, "cvalue", None)


def _oriented(model: Any, name: str, factor: int | None) -> Any:
    """Return point ``name`` multiplied by ``factor``, or None when either is unknown."""
    value = _point(model, name)
    if value is None or factor is None:
        return None
    return value * factor


def _magnitude(model: Any, name: str) -> Any:
    """Return the magnitude of point ``name``, or None when it is absent."""
    value = _point(model, name)
    return abs(value) if value is not None else None


def _phase_values(model: Any, factor: int | None) -> dict[str, Any]:
    """Return the per-line and line-to-line values the meter model has."""
    out: dict[str, Any] = {}
    for line, phase in enumerate("ABC", start=1):
        block: dict[str, Any] = {}
        for point, key, kind in _PHASE_POINTS:
            name = f"{point}{phase}"
            if kind == "oriented":
                block[f"{key}{line}"] = _oriented(model, name, factor)
            elif kind == "magnitude":
                block[f"{key}{line}"] = _magnitude(model, name)
            else:
                block[f"{key}{line}"] = _point(model, name)
        if any(value is not None for value in block.values()):
            out.update(block)
    for point, key in _LINE_TO_LINE_POINTS:
        value = _point(model, point)
        if value is not None:
            out[key] = value
    return out


class PowerSignCalibrator:
    """Learn which sign of a meter's real power is import, from its energy totals.

    Args:
        power_sign: ``"auto"`` to learn the sign, or ``"import_positive"`` or
            ``"export_positive"`` to fix it.
        label: Names the meter in log messages.
    """

    def __init__(self, power_sign: str = "auto", *, label: str = "meter") -> None:
        if power_sign != "auto" and power_sign not in _FIXED_SIGNS:
            raise ValueError(
                f"power_sign must be 'auto', 'import_positive' or 'export_positive'; "
                f"got {power_sign!r}"
            )
        self._label = label
        self._sign: int | None = _FIXED_SIGNS.get(power_sign)
        self._candidate: int | None = None
        self._agreements = 0
        self._contradictions = 0
        self._warned = False
        self._totals: tuple[float, float] | None = None

    @property
    def import_sign(self) -> int | None:
        """Return 1 when positive W is import, -1 when it is export, None until known."""
        return self._sign

    def observe(self, w: Any, imported: Any, exported: Any) -> int | None:
        """Take one reading of W and the two energy totals. Return the import sign."""
        evidence = self._evidence(w, imported, exported)
        if evidence is None:
            return self._sign
        if self._sign is None:
            self._learn(evidence)
        elif evidence == self._sign:
            self._contradictions = 0
        else:
            self._contradictions += 1
            if self._contradictions >= CONTRADICTION_LIMIT and not self._warned:
                self._warned = True
                logger.warning(
                    "SunSpec meter %s: the energy totals contradicted the %s power sign "
                    "on %d consecutive readings; check the meter's power_sign",
                    self._label,
                    self._describe(self._sign),
                    self._contradictions,
                )
        return self._sign

    def _learn(self, evidence: int) -> None:
        """Count one automatic observation and accept the sign once enough agree."""
        if evidence == self._candidate:
            self._agreements += 1
        else:
            self._candidate = evidence
            self._agreements = 1
        if self._agreements >= CALIBRATION_OBSERVATIONS:
            self._sign = evidence
            logger.info(
                "SunSpec meter %s: power sign calibrated from the energy totals; %s",
                self._label,
                self._describe(evidence),
            )

    def _evidence(self, w: Any, imported: Any, exported: Any) -> int | None:
        """Return the import sign this reading shows, or None when it shows none.

        A reading shows a sign when exactly one total rose since the previous
        reading, the other is unchanged, and W is not zero. A total that fell
        (a reset or a rollover) therefore shows nothing.
        """
        if not _is_number(imported) or not _is_number(exported):
            self._totals = None
            return None
        previous, self._totals = self._totals, (float(imported), float(exported))
        if previous is None or not _is_number(w) or w == 0:
            return None
        rose_imp = self._totals[0] - previous[0]
        rose_exp = self._totals[1] - previous[1]
        positive = 1 if w > 0 else -1
        if rose_imp > 0 and rose_exp == 0:
            return positive
        if rose_exp > 0 and rose_imp == 0:
            return -positive
        return None

    @staticmethod
    def _describe(sign: int) -> str:
        return "positive W is import" if sign > 0 else "positive W is export"


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float)


def read_meter(
    model: Any, model_id: int, role: str, calibrator: PowerSignCalibrator
) -> dict[str, Any]:
    """Return monitoring data from a read meter model, in the frame of ``role``.

    ``role`` is ``"site_meter"`` (import positive) or ``"der_meter"`` (DER
    output positive). Real and reactive power are omitted until the
    calibrator knows the import sign. ``WhFwd`` and ``WhRev`` are the energy
    totals in the role's forward and reverse directions.
    """
    imported = _point(model, "TotWhImp")
    exported = _point(model, "TotWhExp")
    import_sign = calibrator.observe(_point(model, "W"), imported, exported)
    # Multiplying by ``factor`` turns the meter's own W into the role's frame.
    factor: int | None = None
    if import_sign is not None:
        factor = import_sign if role == "site_meter" else -import_sign

    # Average phase voltage, line to neutral. A delta meter may report only the
    # line-to-line average.
    voltage = _point(model, "PhV")
    result: dict[str, Any] = {
        "W": _oriented(model, "W", factor),
        "Var": _oriented(model, "VAR", factor),
        "Hz": _point(model, "Hz"),
        "V": voltage if voltage is not None else _point(model, "PPV"),
        # The sign of a meter's power factor follows a vendor convention, so
        # only its magnitude is reported.
        "PF": _magnitude(model, "PF"),
        "VA": _point(model, "VA"),
        "A": _point(model, "A"),
    }
    if model_id in METER_AC_TYPE:
        result["ACType"] = METER_AC_TYPE[model_id]
    result.update(_phase_values(model, factor))
    forward, reverse = (imported, exported) if role == "site_meter" else (exported, imported)
    if forward is not None:
        result["WhFwd"] = forward
    if reverse is not None:
        result["WhRev"] = reverse
    return result


__all__ = [
    "CALIBRATION_OBSERVATIONS",
    "CONTRADICTION_LIMIT",
    "METER_AC_TYPE",
    "METER_MODEL_IDS",
    "PowerSignCalibrator",
    "read_meter",
]
