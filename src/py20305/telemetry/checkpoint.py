"""Serialize the readings a MirrorUsagePoint registered, so a restart can repeat them.

IEEE 2030.5-2023 section 10.10.3 rule n) says that once created, the ReadingType
of a MirrorMeterReading SHALL NOT be modified. Under the default reading profile
the flowDirection of a power reading comes from the sign of its value when the
reading is registered. A client that forgets its registrations at a restart
registers again from whatever sign the value has then, which can change the
ReadingType the server holds. This module turns the registrations into plain data
a caller can store, and back.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from py20305.models.sep.sep import (
    AccumulationBehaviourType,
    CommodityType,
    DataQualifierType,
    FlowDirectionType,
    KindType,
    PhaseCode,
    PowerOfTenMultiplierType,
    ReadingType1,
    UomType,
)
from py20305.telemetry.mup import RegisteredReading

logger = logging.getLogger(__name__)

#: The checkpoint layout this module writes and reads.
CHECKPOINT_VERSION = 1

_FIELDS = (
    "accumulation_behaviour",
    "commodity",
    "data_qualifier",
    "flow_direction",
    "kind",
    "power_of_ten_multiplier",
    "uom",
    "phase",
)


def reading_to_dict(reading: RegisteredReading) -> dict[str, Any]:
    """Return a registered reading as JSON-safe data."""
    reading_type = reading.reading_type
    return {
        "mrid": reading.mrid.hex(),
        "description": reading.description,
        "order": list(reading.order),
        "reading_type": {
            name: getattr(getattr(reading_type, name, None), "value", None) for name in _FIELDS
        },
    }


def _code(value: Any) -> int | None:
    """Return ``value`` as a code, None when absent; raise ValueError for anything else."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected an integer code, got {value!r}")
    return value


def _required(types: dict[str, Any], name: str) -> int:
    value = _code(types.get(name))
    if value is None:
        raise ValueError(f"reading_type.{name} is required")
    return value


def reading_from_dict(data: Any) -> RegisteredReading:
    """Return the registered reading ``data`` describes.

    Raises:
        ValueError: ``data`` is not what :func:`reading_to_dict` writes.
    """
    if not isinstance(data, dict):
        raise ValueError("a registered reading must be an object")
    try:
        mrid = bytes.fromhex(data["mrid"])
        description = data["description"]
        order = tuple(data["order"])
        types = data["reading_type"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"missing or malformed field: {exc!r}") from exc
    if len(mrid) != 16:
        raise ValueError("an mRID is 16 bytes")
    if not isinstance(description, str):
        raise ValueError("description must be text")
    if len(order) != 3 or not all(isinstance(n, int) and not isinstance(n, bool) for n in order):
        raise ValueError("order must be three integers")
    if not isinstance(types, dict):
        raise ValueError("reading_type must be an object")
    flow = _code(types.get("flow_direction"))
    phase = _code(types.get("phase"))
    reading_type = ReadingType1(
        accumulation_behaviour=AccumulationBehaviourType(
            value=_required(types, "accumulation_behaviour")
        ),
        commodity=CommodityType(value=_required(types, "commodity")),
        data_qualifier=DataQualifierType(value=_required(types, "data_qualifier")),
        flow_direction=FlowDirectionType(value=flow) if flow is not None else None,
        kind=KindType(value=_required(types, "kind")),
        power_of_ten_multiplier=PowerOfTenMultiplierType(
            value=_required(types, "power_of_ten_multiplier")
        ),
        uom=UomType(value=_required(types, "uom")),
        phase=PhaseCode(value=phase) if phase else None,
    )
    return RegisteredReading(
        mrid=mrid,
        description=description,
        reading_type=reading_type,
        order=(order[0], order[1], order[2]),
    )


def load_checkpoint(path: Path) -> Any:
    """Return the data stored at ``path``, or None when there is none or it is unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Cannot read the telemetry state file %s: %s", path, exc)
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        logger.warning("Ignoring the telemetry state file %s: not valid JSON (%s)", path, exc)
        return None


def save_checkpoint(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` to ``path`` through a temporary file, so a crash leaves the old file.

    Raises:
        OSError: the directory or file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


__all__ = [
    "CHECKPOINT_VERSION",
    "load_checkpoint",
    "reading_from_dict",
    "reading_to_dict",
    "save_checkpoint",
]
