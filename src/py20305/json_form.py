"""The JSON projection of the IEEE 2030.5 models.

Turns the generated bindings -- dataclasses full of ``value``/``multiplier``
pairs, ``bytes`` identifiers and codegen placeholder fields -- into plain
dicts a consumer can read without knowing xsdata. This is the counterpart to
:mod:`py20305.xml.serialization`, which handles the wire direction;
here the destination is JSON.

Two callers need it and neither is the other's client: the management API
shapes responses from it, and the event engine builds the payload of a
:class:`~py20305.connectors.base.ScheduleNotification` from it. It
therefore sits below both rather than inside either.

The projection is deliberately lossy in one direction: fields the schema
codegen invents (``other_element``, ``any_attributes``, ``*_r2_3`` superclass
references) carry no IEEE 2030.5 content and are dropped. CSIP-AUS DOE limits
are the exception that proves it -- they ride in the ``xs:any`` slot that
filtering removes, so :func:`_merge_doe_controls` puts them back.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Field names that are generateDS / XSD-binding codegen artifacts rather
# than IEEE 2030.5 spec content.
#
# The codegen tool emits placeholder fields named ``other_element`` and
# ``any_attributes`` for ``xs:any`` / ``xs:anyAttribute`` slots, plus
# ``*_r2_3`` superclass references for the IEEE 2030.5 R2.3 schema
# revision. None of these carry user-visible content; filtering them out
# of serialized dicts keeps the UI focused on real spec fields.
_XSD_INTERNAL_LITERAL_FIELDS = frozenset(
    {
        "other_element",
        "any_attributes",
    }
)
_XSD_INTERNAL_SUFFIXES: tuple[str, ...] = ("_r2_3",)


def _is_xsd_internal(key: str) -> bool:
    k = key.lower()
    if k in _XSD_INTERNAL_LITERAL_FIELDS:
        return True
    return any(k.endswith(suffix) for suffix in _XSD_INTERNAL_SUFFIXES)


def _filter_xsd_internals(d: dict[str, Any]) -> dict[str, Any]:
    """Drop generateDS-codegen artifacts and Python-private fields from a dict."""
    return {
        k: v for k, v in d.items() if not _is_xsd_internal(str(k)) and not str(k).startswith("_")
    }


def _merge_doe_controls(obj: Any, result: Any) -> None:
    """Merge CSIP-AUS DOE limit controls into a serialized dict.

    They ride in ``obj.other_element`` (an ``xs:any`` slot that
    ``_filter_xsd_internals`` strips), so without this they'd vanish everywhere a
    DERControlBase is serialized via ``safe_serialize`` -- including the UI's
    ``dercontrol_base`` view. No-op for any object without DOE controls.
    """
    if isinstance(result, dict):
        doe = _serialize_doe_controls(obj)
        if doe:
            result.update(doe)


def safe_serialize(obj: Any, seen: set[int] | None = None) -> Any:
    """Recursively convert an object to a JSON-serializable form.

    Handles:
    - dataclasses (IEEE 2030.5 models)
    - bytes (LFDIs, etc.) -> hex strings
    - dicts, lists, tuples
    - circular references (returns "<circular>")
    - objects with __dict__

    Args:
        obj: The object to serialize
        seen: Set of object ids already visited (for cycle detection)

    Returns:
        JSON-serializable representation
    """
    if seen is None:
        seen = set()

    # Handle None and primitives
    if obj is None:
        return None
    if isinstance(obj, bool | int | float | str):
        return obj

    # Check for circular reference
    obj_id = id(obj)
    if obj_id in seen:
        return "<circular>"
    seen.add(obj_id)

    try:
        # Handle bytes (LFDIs, etc.)
        if isinstance(obj, bytes):
            return obj.hex()

        # Handle dataclasses
        if is_dataclass(obj) and not isinstance(obj, type):
            try:
                data = asdict(obj)
                result = safe_serialize(_filter_xsd_internals(data), seen)
            except Exception:
                # Fall back to __dict__ if asdict fails
                if not hasattr(obj, "__dict__"):
                    return str(obj)
                result = safe_serialize(_filter_xsd_internals(obj.__dict__), seen)
            _merge_doe_controls(obj, result)
            return result

        # Handle dicts
        if isinstance(obj, dict):
            filtered = _filter_xsd_internals(obj)
            return {str(k): safe_serialize(v, seen) for k, v in filtered.items()}

        # Handle lists and tuples
        if isinstance(obj, list | tuple):
            return [safe_serialize(item, seen) for item in obj]

        # Handle sets
        if isinstance(obj, set):
            return [safe_serialize(item, seen) for item in sorted(obj, key=str)]

        # Handle objects with __dict__
        if hasattr(obj, "__dict__"):
            result = safe_serialize(_filter_xsd_internals(obj.__dict__), seen)
            _merge_doe_controls(obj, result)
            return result

        # Last resort: string representation
        return str(obj)

    finally:
        seen.discard(obj_id)


def _try_flatten_value_multiplier(d: dict[str, Any]) -> float | dict[str, Any] | None:
    """Try to flatten a dict as a value/multiplier pair.

    Returns the flattened float if it's a valid pair, None if values are null,
    or the original dict if it's not a value/multiplier pair.
    """
    if "value" in d and "multiplier" in d and len(d) == 2:
        v = d.get("value")
        m = d.get("multiplier")
        if v is not None and m is not None:
            try:
                result: float = float(v) * (10**m)
                return result
            except (TypeError, ValueError):
                return d
        return None
    return d


def _flatten_item(item: Any) -> Any:
    """Flatten a single item (used for list elements)."""
    if isinstance(item, dict):
        d: dict[str, Any] = item
        result = _try_flatten_value_multiplier(d)
        if result is item:  # Not a value/multiplier pair
            return flatten_multiplier_fields(d)
        return result
    return item


def flatten_multiplier_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten IEEE 2030.5 value/multiplier pairs to floats.

    IEEE 2030.5 uses {value: int, multiplier: int} pairs where the
    actual value is value * 10^multiplier. This function flattens
    these to simple floats for the UI.

    Args:
        data: Dict potentially containing value/multiplier pairs

    Returns:
        Dict with value/multiplier pairs replaced by floats
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            nested: dict[str, Any] = value
            flattened = _try_flatten_value_multiplier(nested)
            if flattened is value:  # Not a value/multiplier pair
                result[key] = flatten_multiplier_fields(nested)
            else:
                result[key] = flattened
        elif isinstance(value, list):
            result[key] = [_flatten_item(item) for item in value]
        else:
            result[key] = value
    return result

def unwrap_value(obj: Any) -> Any:
    """Return ``obj.value`` if ``obj`` is a SEP wrapper (``TimeType``,
    ``MRidtype``, etc.); otherwise return ``obj`` unchanged.

    Every numeric/identity field on an IEEE 2030.5 resource the spec
    declares as ``Time``, ``mRIDType``, ``UInt8``... is bound to a
    Pydantic wrapper with the real payload on ``.value``. Defensive
    ``hasattr`` rather than ``isinstance`` so the helper stays
    independent of the model classes and tolerates plain primitives
    (which is what callers + tests sometimes hand in).
    """
    if obj is None:
        return None
    if hasattr(obj, "value"):
        return obj.value
    return obj


def serialize_mrid(mrid: Any) -> str | None:
    """``MRidtype.value`` is bytes; return the hex string the response
    documents. Tolerates a bare ``bytes`` or ``str`` for tests."""
    raw = unwrap_value(mrid)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.hex()
    return str(raw)


def _serialize_epoch(value: Any) -> int | None:
    """Unwrap a ``TimeType`` (or accept a bare int) and return epoch
    seconds. Returns ``None`` for missing / sentinel-zero values."""
    raw = unwrap_value(value)
    if raw is None:
        return None
    try:
        as_int = int(raw)
    except (TypeError, ValueError):
        return None
    return as_int or None


def _serialize_interval(interval: Any) -> dict[str, Any] | None:
    """``DateTimeInterval`` -> ``{start, duration}`` (epoch seconds).

    ``start`` is a ``TimeType`` wrapper in production; ``duration`` is
    a plain int. Both branches tolerate bare primitives for tests.
    """
    if interval is None:
        return None
    start = _serialize_epoch(getattr(interval, "start", None))
    duration_raw = unwrap_value(getattr(interval, "duration", None))
    duration = int(duration_raw) if duration_raw is not None else None
    return {"start": start, "duration": duration}


def _serialize_event_status(status: Any) -> dict[str, Any] | None:
    """``EventStatus`` -> dict with the fields a scheduler cares about.

    See IEEE 2030.5-2018 §11.2.4 for the ``currentStatus`` enum:
      0 = Scheduled, 1 = Active, 2 = Cancelled,
      3 = Cancelled-with-Randomization, 4 = Superseded, 5 = Complete.
    """
    if status is None:
        return None
    current = unwrap_value(getattr(status, "current_status", None))
    return {
        "current_status": int(current) if current is not None else None,
        "date_time": _serialize_epoch(getattr(status, "date_time", None)),
        "potentially_superseded": bool(getattr(status, "potentially_superseded", False)),
        "potentially_superseded_time": _serialize_epoch(
            getattr(status, "potentially_superseded_time", None)
        ),
        "reason": getattr(status, "reason", None),
    }


# CSIP-AUS DOE limit controls ride in DERControlBase.other_element (the xs:any
# slot that safe_serialize strips as a codegen artefact). Map each to a
# snake_cased key consistent with the other op_mod_* fields so the UI's
# control_base view shows export / import / generation / load limits when a
# CSIP-AUS server sets them.
_DOE_CONTROL_KEYS = {
    "opModExpLimW": "op_mod_exp_lim_w",
    "opModImpLimW": "op_mod_imp_lim_w",
    "opModGenLimW": "op_mod_gen_lim_w",
    "opModLoadLimW": "op_mod_load_lim_w",
}


def _local_name(qname: Any) -> str | None:
    """Local part of a Clark-notation qname ('{ns}local' -> 'local')."""
    if not isinstance(qname, str):
        return None
    return qname.rsplit("}", 1)[-1]


def _doe_element_name(elem: Any) -> str | None:
    """CSIP-AUS element name for a DOE control, whether it parsed as a typed
    model (``Meta.name``) or -- when the csipaus types aren't registered in the
    parser context -- a generic ``AnyElement`` (Clark-notation ``qname``)."""
    meta = getattr(elem, "Meta", None) or getattr(getattr(elem, "__class__", None), "Meta", None)
    name = getattr(meta, "name", None)
    if isinstance(name, str):
        return name
    return _local_name(getattr(elem, "qname", None))


def _safe_int(text: Any) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _doe_value_multiplier(elem: Any) -> dict[str, Any]:
    """``{value, multiplier}`` for a DOE control. A typed ActivePower-style model
    exposes them as attributes; a generic ``AnyElement`` carries them as child
    elements with text."""
    if getattr(elem, "value", None) is not None:
        serialized = safe_serialize(elem)
        if isinstance(serialized, dict):
            return serialized
    out: dict[str, Any] = {}
    for child in getattr(elem, "children", None) or []:
        cname = _local_name(getattr(child, "qname", None))
        val = _safe_int(getattr(child, "text", None))
        if val is None:
            continue
        if cname == "value":
            out["value"] = val
        elif cname == "multiplier":
            out["multiplier"] = {"value": val}
    return out


def _serialize_doe_controls(base: Any) -> dict[str, Any]:
    """Extract the four CSIP-AUS DOE limit controls from a DERControlBase's
    ``other_element`` wildcard, keyed/serialized like the other op_mod fields.

    Handles both a typed model (when the csipaus types are registered in the
    xsdata parser context) and a generic ``AnyElement`` (when they are not --
    which is the case in a bare API/serializer context)."""
    out: dict[str, Any] = {}
    for elem in getattr(base, "other_element", None) or []:
        key = _DOE_CONTROL_KEYS.get(_doe_element_name(elem) or "")
        if key is None:
            continue
        vm = _doe_value_multiplier(elem)
        # Omit the key entirely when no usable value parsed (e.g. an unexpected
        # AnyElement shape / non-numeric text) -- an empty {} is harder for the
        # UI/clients to handle than an absent field.
        if "value" in vm:
            out[key] = vm
    return out


def serialize_der_control(derc: Any) -> dict[str, Any]:
    """Serialize a ``Dercontrol1`` to the runtime-cache JSON shape used by
    ``GET /api/v1/devices/{device_id}/dercontrols`` -- ``device_id`` is
    either a 40-hex LFDI or an EndDevice href; both resolve through
    ``APIService._find_device``.

    The fields surfaced here are the ones an external optimizer needs to
    schedule against the upcoming event list: identity (mRID), timing
    (interval + randomization window), supersession ordering (primacy
    is on the parent DERProgram, not the control itself, so the caller
    overlays it), and the spec event-status enum so the consumer can
    distinguish Scheduled / Active / Cancelled / Superseded / Complete
    without re-walking the supersession graph. The full
    ``DERControlBase`` rides through via ``safe_serialize`` so every
    opMod field the spec defines is reachable.

    Spec fields the serializer intentionally drops:
      * ``device_category`` -- aggregator-side filtering happens
        before the consumer sees the event, so the per-event category
        bitmap is noise here.
      * Codegen artefacts (``other_element``, ``*_r2_3``) -- already
        stripped by ``safe_serialize``.
    """
    base = getattr(derc, "dercontrol_base", None)
    # DOE limit controls in base.other_element are merged by safe_serialize.
    return {
        "mrid": serialize_mrid(getattr(derc, "m_rid", None)),
        "creation_time": _serialize_epoch(getattr(derc, "creation_time", None)),
        "interval": _serialize_interval(getattr(derc, "interval", None)),
        "randomize_start": unwrap_value(getattr(derc, "randomize_start", None)),
        "randomize_duration": unwrap_value(getattr(derc, "randomize_duration", None)),
        "event_status": _serialize_event_status(getattr(derc, "event_status", None)),
        "control_base": safe_serialize(base) if base is not None else None,
    }


def serialize_default_der_control(dderc: Any) -> dict[str, Any] | None:
    """Serialize a ``DefaultDercontrol`` to the runtime-cache JSON shape.

    DefaultDERControls have no interval (they apply continuously until
    an event supersedes them) and no event_status -- just identity and
    the underlying DERControlBase. Returns ``None`` if no DDERC is
    cached for the program (operator hasn't set one).
    """
    if dderc is None:
        return None
    base = getattr(dderc, "dercontrol_base", None)
    return {
        "mrid": serialize_mrid(getattr(dderc, "m_rid", None)),
        "control_base": safe_serialize(base) if base is not None else None,
    }
