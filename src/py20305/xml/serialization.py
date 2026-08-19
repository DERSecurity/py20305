"""XML serialization for IEEE 2030.5 Pydantic models."""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import TypeVar

from lxml import etree
from xsdata_pydantic.bindings import XmlContext, XmlParser, XmlSerializer

APPLICATION_SEP_XML = "application/sep+xml"


class XmlParseError(ValueError):
    """Raised by :func:`from_xml` when bytes cannot be deserialized.

    Wraps xsdata's low-level parser exceptions in a typed error with
    operator-facing context (target model, body length, snippet) so log
    output is a one-line diagnostic instead of an opaque traceback. The
    original xsdata error is preserved as ``__cause__`` for callers that
    want to inspect it.
    """

    def __init__(self, message: str, *, model_name: str, body_length: int) -> None:
        super().__init__(message)
        self.model_name = model_name
        self.body_length = body_length


NS_SEP = "urn:ieee:std:2030.5:ns"
NS_CSIPAUS = "https://csipaus.org/ns"

T = TypeVar("T")

_context = XmlContext()
_serializer = XmlSerializer(context=_context)
_parser = XmlParser(context=_context)

# Attributes to strip for IEEE 2030.5-2018 server compatibility.
# schemaVer: not defined in the 2018 edition.
# subscribable: not present on MirrorUsagePoint in 2018.
_2018_COMPAT_RE = re.compile(r' (?:schemaVer|subscribable)="[^"]*"')


def to_xml(
    model: object,
    *,
    include_csipaus: bool = False,
    server_2018_compat: bool = False,
) -> bytes:
    """Serialize a Pydantic model to IEEE 2030.5 XML bytes.

    Args:
        model: Pydantic model to serialize.
        include_csipaus: If True, include the CSIP-AUS namespace declaration.
            Only pass True when connected to a CSIP-AUS server.
        server_2018_compat: If True, strip attributes from the root element
            that are not present in the IEEE 2030.5-2018 schema (schemaVer,
            subscribable on MirrorUsagePoint).
    """
    ns_map: dict[str | None, str] = {None: NS_SEP}
    if include_csipaus:
        ns_map["csipaus"] = NS_CSIPAUS
    xml_str = _serializer.render(model, ns_map=ns_map)
    if include_csipaus and "<csipaus:" in xml_str:
        xml_str = _reorder_csipaus_extensions(xml_str)
    if server_2018_compat:
        # Strip 2023-only attributes from the root element only.
        # Skip past the XML declaration (ends with "?>") to find the
        # root element, then substitute within the opening tag.
        decl_end = xml_str.index("?>") + 2
        rest = xml_str[decl_end:]
        # Find end of root opening tag (either "/>" or ">")
        close = rest.index(">")
        root_tag = _2018_COMPAT_RE.sub("", rest[:close])
        xml_str = xml_str[:decl_end] + root_tag + rest[close:]
    return xml_str.encode("utf-8")


_CSIPAUS_TAG_PREFIX = "{" + NS_CSIPAUS + "}"


def _reorder_csipaus_extensions(xml_str: str) -> str:
    """Move CSIP-AUS extension elements to the end of their parent element.

    The generated sep models carry CSIP-AUS extensions (doeModesEnabled,
    doeModesSupported, ...) in the ``other_element`` wildcard, which is inherited
    from the base ``Resource`` type. xsdata renders inherited fields before the
    derived type's own elements, so the extension is emitted first. But the
    CSIP-AUS schema appends these via ``xs:extension``, so a valid document must
    place them *after* the whole base sequence (i.e. as the last children). A
    server validating against the CSIP-AUS schema otherwise rejects the payload
    (e.g. "doeModesEnabled: This element is not expected"). Reorder to match.
    """
    root = etree.fromstring(xml_str.encode("utf-8"), parser=_SAFE_PARSER)
    for parent in list(root.iter()):
        extensions = [
            child
            for child in parent
            if isinstance(child.tag, str) and child.tag.startswith(_CSIPAUS_TAG_PREFIX)
        ]
        for child in extensions:
            parent.remove(child)
            parent.append(child)
    body = etree.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


def from_xml(xml: bytes | str, model_type: type[T]) -> T:
    """Deserialize XML bytes to a typed Pydantic model.

    Raises :class:`XmlParseError` on malformed XML, empty bodies, or
    payloads that fail xsdata's structural / type-conversion checks. The
    error message includes the target model, body length, and a snippet
    of the body so a caller's WARNING-level log is enough to diagnose
    the failure without re-running with a traceback.
    """
    if isinstance(xml, bytes):
        body_length = len(xml)
        text = xml.decode("utf-8", errors="replace")
    else:
        text = xml
        body_length = len(text.encode("utf-8"))
    try:
        return _parser.from_string(text, model_type)
    except XmlParseError:
        raise
    except ValueError as exc:
        # All known parse failure modes derive from ValueError:
        # ``xsdata.exceptions.ParserError`` (lxml-level XML errors),
        # ``ConverterError`` (xsdata type conversion failures), and
        # ``pydantic.ValidationError`` (model field validation, which
        # fires when the root element is wrong or required attributes
        # are absent -- this is the path the user's "empty 200" and
        # "wrong root element" bugs travelled before being wrapped).
        snippet = " ".join(text[:200].split())
        if body_length == 0:
            detail = "empty body"
        else:
            detail = f"{exc.__class__.__name__}: {exc}; snippet: {snippet!r}"
        raise XmlParseError(
            f"Failed to parse {body_length}-byte body as <{model_type.__name__}>: {detail}",
            model_name=model_type.__name__,
            body_length=body_length,
        ) from exc


_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"

# Hardened parser for validating XML from the network (mitigates XXE/entity expansion).
_SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


@functools.cache
def _load_schema(schema_path: Path) -> etree.XMLSchema:
    schema_doc = etree.parse(str(schema_path))
    return etree.XMLSchema(schema_doc)


def validate_xml(xml: bytes, schema_path: Path | None = None) -> list[str]:
    """Validate XML bytes against the IEEE 2030.5 XSD schema.

    Returns an empty list if valid, or a list of error strings.
    """
    if schema_path is None:
        schema_path = _SCHEMA_DIR / "sep2_schema_2023.xsd"
    try:
        schema = _load_schema(schema_path)
        doc = etree.fromstring(xml, parser=_SAFE_PARSER)
        if schema.validate(doc):
            return []
        return [str(err) for err in schema.error_log]  # type: ignore[attr-defined]
    except etree.XMLSyntaxError as e:
        return [f"XML parse error: {e}"]


def validate_xml_result(
    xml: bytes | str, schema_path: Path | None = None
) -> tuple[bool, str | None]:
    """Validate XML and return ``(is_valid, first_error_or_None)``.

    A convenience wrapper around :func:`validate_xml` that returns a
    ``(bool, str | None)`` tuple suitable for the message-forwarding pipeline.

    Args:
        xml: XML content as bytes or string.
        schema_path: Optional path to XSD file.  Defaults to
            ``sep2_schema_2023.xsd`` inside the repository ``schemas/``
            directory -- the IEEE 2030.5-2023 base schema that the generated
            models in ``py20305.models.sep`` were produced from.
    """
    if schema_path is None:
        schema_path = _SCHEMA_DIR / "sep2_schema_2023.xsd"
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    errors = validate_xml(xml, schema_path)
    if not errors:
        return True, None
    return False, errors[0]
