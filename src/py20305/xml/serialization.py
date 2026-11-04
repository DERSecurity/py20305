"""XML serialization for IEEE 2030.5 Pydantic models."""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import TypeVar

from lxml import etree
from xsdata_pydantic.bindings import XmlContext, XmlParser, XmlSerializer

# The parser types an extension element (the CSIP-AUS limits in a
# DERControlBase) only when its class is already imported, and leaves it a
# generic element otherwise. Importing the models here makes that independent
# of which other module happened to load first. The same holds for the
# CSIP-AUS 1.3 tariff subclasses: a Notification's Resource is resolved by
# xsi:type, and only a registered subclass admits the 1.3 RateComponentLink.
from py20305.models.csipaus import csipaus_ext, tariff  # noqa: F401

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
    csipaus_namespace: str = NS_CSIPAUS,
    server_2018_compat: bool = False,
) -> bytes:
    """Serialize a Pydantic model to IEEE 2030.5 XML bytes.

    Args:
        model: Pydantic model to serialize.
        include_csipaus: If True, include the CSIP-AUS namespace declaration.
            Only pass True when connected to a CSIP-AUS server.
        csipaus_namespace: The namespace the CSIP-AUS extensions are written
            in, the one the server declares. CSIP-AUS 1.3 moved them from
            ``https://csipaus.org/ns`` to ``https://csipaus.org/ns/v1.3``, and a
            server reads only its own. Applies when ``include_csipaus`` is True.
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
    if include_csipaus and csipaus_namespace != NS_CSIPAUS:
        # The extension models are bound to the pre-1.3 URI; every extension
        # element carries the csipaus prefix, so moving the prefix's one
        # declaration moves them all.
        xml_str = xml_str.replace(
            f'xmlns:csipaus="{NS_CSIPAUS}"', f'xmlns:csipaus="{csipaus_namespace}"', 1
        )
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


#: The IEEE 2030.5 entry point in a schema directory, newest name first. The
#: pre-0.14 name ``sep.xsd`` was the same IEEE 2030.5-2023 schema.
_BASE_SCHEMA_NAMES = ("sep2_schema_2023.xsd", "sep.xsd")

#: Namespaces whose elements may extend an IEEE 2030.5 body, each with the
#: schema file that declares them and the namespace that file declares. CSIP-AUS
#: 1.3 moved its extensions to ``https://csipaus.org/ns/v1.3``; the package ships
#: no 1.3 extension schema, so those elements are checked against the pre-1.3
#: declarations, the same definitions the client writes under the 1.3 URI.
EXTENSION_SCHEMAS: dict[str, tuple[str, str]] = {
    "https://csipaus.org/ns": ("csipaus-ext.xsd", "https://csipaus.org/ns"),
    "https://csipaus.org/ns/v1.3": ("csipaus-ext.xsd", "https://csipaus.org/ns"),
    "https://csipaus.org/ns/v1.3-beta/storage": (
        "csipaus-ext-v1.3-beta_storage.xsd",
        "https://csipaus.org/ns/v1.3-beta/storage",
    ),
}


def _namespace_of(element: etree._Element) -> str | None:
    """Return the namespace URI of ``element``, or None for an unqualified one."""
    return etree.QName(element).namespace


def _schema_file(name: str, schema_dir: Path | None) -> Path:
    """Return ``name`` in ``schema_dir`` when it is there, else the packaged copy."""
    if schema_dir is not None and (schema_dir / name).exists():
        return schema_dir / name
    return _SCHEMA_DIR / name


def _base_schema(schema_dir: Path | None) -> Path:
    """Return the IEEE 2030.5 entry point to validate against."""
    if schema_dir is not None:
        for name in _BASE_SCHEMA_NAMES:
            if (schema_dir / name).exists():
                return schema_dir / name
    return _SCHEMA_DIR / _BASE_SCHEMA_NAMES[0]


def _schema_errors(schema: etree.XMLSchema, doc: etree._Element) -> list[str]:
    if schema.validate(doc):
        return []
    return [str(err) for err in schema.error_log]  # type: ignore[attr-defined]


def _set_aside_extensions(doc: etree._Element) -> list[etree._Element]:
    """Detach every element outside the IEEE 2030.5 namespace, and return them.

    Only the outermost such element is detached; anything inside it belongs to
    it and is checked with it.
    """
    found: list[etree._Element] = []
    for element in doc.iter():
        if not isinstance(element.tag, str) or _namespace_of(element) == NS_SEP:
            continue
        if all(_namespace_of(ancestor) == NS_SEP for ancestor in element.iterancestors()):
            found.append(element)
    for element in found:
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    return found


def _extension_errors(element: etree._Element, schema_dir: Path | None) -> list[str]:
    """Validate one extension element on its own against its namespace's schema."""
    namespace = _namespace_of(element)
    name = etree.QName(element).localname
    known = EXTENSION_SCHEMAS.get(namespace or "")
    if known is None:
        return [f"{name}: no schema is known for namespace {namespace!r}"]
    file_name, declared = known
    standalone = etree.fromstring(etree.tostring(element), parser=_SAFE_PARSER)
    if declared != namespace:
        for node in standalone.iter():
            if isinstance(node.tag, str) and _namespace_of(node) == namespace:
                node.tag = f"{{{declared}}}{etree.QName(node).localname}"
    schema = _load_schema(_schema_file(file_name, schema_dir))
    return [f"{name}: {error}" for error in _schema_errors(schema, standalone)]


def _validate_layered(doc: etree._Element, schema_dir: Path | None) -> list[str]:
    """Validate an IEEE 2030.5 body that may carry extension elements.

    The body less every element outside the IEEE 2030.5 namespace is checked
    against the IEEE 2030.5 schema, and each element set aside is checked on
    its own against the schema of its namespace (:data:`EXTENSION_SCHEMAS`).
    Utilities mix elements of the 2018 and 2023 editions with profile
    extensions, so no single schema accepts every valid body. Profile
    requirements, such as an extension a profile makes mandatory or where it
    may appear, are not checked.
    """
    extensions = _set_aside_extensions(doc)
    errors: list[str] = []
    if _namespace_of(doc) == NS_SEP:
        errors += _schema_errors(_load_schema(_base_schema(schema_dir)), doc)
    for element in extensions:
        errors += _extension_errors(element, schema_dir)
    return errors


def validate_xml(
    xml: bytes, schema_path: Path | None = None, *, schema_dir: Path | None = None
) -> list[str]:
    """Validate IEEE 2030.5 XML and return the errors found; empty when it is valid.

    Without ``schema_path`` the body is validated in layers: everything in
    the IEEE 2030.5 namespace against the IEEE 2030.5-2023 schema, and each
    element of another namespace (a CSIP-AUS extension, for example) on its
    own against that namespace's schema. An element of a namespace with no
    known schema is an error. ``schema_dir`` supplies the schema files,
    falling back to the packaged ones for any it does not hold.

    With ``schema_path`` the whole body is validated against that one schema,
    as before layered validation existed.
    """
    try:
        doc = etree.fromstring(xml, parser=_SAFE_PARSER)
    except etree.XMLSyntaxError as e:
        return [f"XML parse error: {e}"]
    if schema_path is not None:
        return _schema_errors(_load_schema(schema_path), doc)
    return _validate_layered(doc, schema_dir)


def validate_xml_result(
    xml: bytes | str, schema_path: Path | None = None, *, schema_dir: Path | None = None
) -> tuple[bool, str | None]:
    """Validate XML and return ``(is_valid, first_error_or_None)``.

    A convenience wrapper around :func:`validate_xml` that returns a
    ``(bool, str | None)`` tuple suitable for the message-forwarding pipeline.

    Args:
        xml: XML content as bytes or string.
        schema_path: Validate against this one schema only. When omitted, the
            body is validated in layers (see :func:`validate_xml`).
        schema_dir: Directory holding the schema files for layered
            validation; the packaged schemas fill in any it lacks.
    """
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    errors = validate_xml(xml, schema_path, schema_dir=schema_dir)
    if not errors:
        return True, None
    return False, errors[0]
