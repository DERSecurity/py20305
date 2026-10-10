"""Read the CSIP-AUS dynamic operating envelope limits from a DERControlBase.

The four limits (``opModExpLimW``, ``opModImpLimW``, ``opModGenLimW``,
``opModLoadLimW``) are extension elements: they arrive in
``DERControlBase.other_element``, the ``xs:any`` slot, not as fields. The parser
types them as :class:`OpModExpLimW` and friends when those classes are
registered, and as generic ``AnyElement`` objects (a qualified name, child
elements with text) when they are not. Both shapes carry the same two numbers,
and everything that reads a limit goes through here so both are read alike.
"""

from __future__ import annotations

from typing import Any

#: The limit element names, as they appear in the CSIP-AUS schema.
DOE_LIMIT_NAMES: frozenset[str] = frozenset(
    {"opModExpLimW", "opModImpLimW", "opModGenLimW", "opModLoadLimW"}
)


def _local_name(qname: Any) -> str | None:
    """Local part of a Clark-notation qualified name (``{ns}local`` to ``local``)."""
    if not isinstance(qname, str):
        return None
    return qname.rsplit("}", 1)[-1]


def doe_element_name(elem: Any) -> str | None:
    """The element name of a limit, from a typed model or a generic element.

    A typed model names itself in ``Meta.name``; a generic element in its
    qualified name. ``None`` when the object is neither.
    """
    meta = getattr(elem, "Meta", None) or getattr(getattr(elem, "__class__", None), "Meta", None)
    name = getattr(meta, "name", None)
    if isinstance(name, str):
        return name
    return _local_name(getattr(elem, "qname", None))


def _int_or_none(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def doe_value_multiplier(elem: Any) -> tuple[int, int] | None:
    """The ``(value, multiplier)`` pair of a limit, or ``None`` when it has none.

    A typed model carries them as attributes (the multiplier wrapped in its own
    type); a generic element carries them as child elements with text. A missing
    multiplier reads as zero, a missing value as no limit.
    """
    value = getattr(elem, "value", None)
    if value is not None and not isinstance(value, bool):
        multiplier = getattr(elem, "multiplier", 0)
        if hasattr(multiplier, "value"):
            multiplier = multiplier.value
        parsed_value = _int_or_none(value)
        parsed_multiplier = _int_or_none(multiplier)
        if parsed_value is None:
            return None
        return parsed_value, parsed_multiplier if parsed_multiplier is not None else 0

    found_value: int | None = None
    found_multiplier = 0
    for child in getattr(elem, "children", None) or []:
        child_name = _local_name(getattr(child, "qname", None))
        number = _int_or_none(getattr(child, "text", None))
        if number is None:
            continue
        if child_name == "value":
            found_value = number
        elif child_name == "multiplier":
            found_multiplier = number
    if found_value is None:
        return None
    return found_value, found_multiplier


def doe_limits(base: Any) -> dict[str, tuple[int, int]]:
    """Every limit a DERControlBase carries, by element name, as ``(value, multiplier)``."""
    out: dict[str, tuple[int, int]] = {}
    for elem in getattr(base, "other_element", None) or []:
        name = doe_element_name(elem)
        if name not in DOE_LIMIT_NAMES:
            continue
        pair = doe_value_multiplier(elem)
        if pair is not None:
            out[name] = pair
    return out
