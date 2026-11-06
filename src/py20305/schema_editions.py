"""What IEEE 2030.5-2023 added to each type of the 2018 schema.

The two editions share the namespace ``urn:ieee:std:2030.5:ns``, so a payload
cannot be sorted into an edition by namespace. This module derives, from the two
bundled schemas, which child elements and attributes each complex type gained in
2023. An element is new in one type and not in another: ``UsagePointLink`` is a
2018 element, but new inside ``MirrorUsagePoint``; ``updatedTime`` is old, but
new inside ``DefaultDERControl``. So the check is per parent type, and nothing
is listed by hand, which keeps it from drifting from the schemas.

An item is attributed to the type that declares it, not to a type that inherits
it, so ``modesSupported2`` is always ``DERCapability`` whichever element of that
type a payload has.

:func:`borrowed_items` walks an XML payload against the 2023 schema and returns
each (declaring type, element or ``@attribute``) in it that the 2018 type lacks.

The 2023 schema also declares attributes on the global elements, not only on
types: each global element wraps its complexType in an inline extension that
adds ``schemaVer``. Those inline types are registered as ``<Name>``, which a
lookup by type name never reaches, so :func:`new_item` does not report them.
:func:`root_attributes` returns them, and the server profile handles them on
the payload root. Today the 2023 set is ``{"schemaVer"}`` and the 2018 set is
empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import NamedTuple

from lxml import etree

SCHEMA_DIR = Path(__file__).parent / "schemas"
_XS = "{http://www.w3.org/2001/XMLSchema}"
_SEP_NS = "urn:ieee:std:2030.5:ns"


class Item(NamedTuple):
    """An element or attribute of a type: ``name`` is ``@name`` for an attribute."""

    parent: str
    name: str


@dataclass
class _Type:
    base: str | None = None
    elements: dict[str, str] = field(default_factory=dict)
    attributes: set[str] = field(default_factory=set)


class _Schema:
    """The complex types and global elements of one edition."""

    def __init__(self, path: Path) -> None:
        self.types: dict[str, _Type] = {}
        self.roots: dict[str, str] = {}
        root = etree.parse(str(path)).getroot()
        for node in root.findall(f"{_XS}complexType"):
            self._add_type(node.get("name", ""), node)
        for node in root.findall(f"{_XS}element"):
            name = node.get("name", "")
            declared = node.get("type")
            if declared:
                self.roots[name] = declared
                continue
            inline = node.find(f"{_XS}complexType")
            if inline is not None:
                self.roots[name] = self._add_type(f"<{name}>", inline)

    def _add_type(self, type_id: str, node: etree._Element) -> str:
        info = _Type()
        self.types[type_id] = info
        for content in node.iter(f"{_XS}extension", f"{_XS}restriction"):
            info.base = content.get("base")
            break
        for child in node.iter(f"{_XS}element"):
            if child.getparent() is None:
                continue
            if self._owner(child, node) is not node:
                continue
            name = child.get("name")
            if not name:
                continue
            declared = child.get("type")
            inline = child.find(f"{_XS}complexType")
            if declared:
                info.elements[name] = declared
            elif inline is not None:
                info.elements[name] = self._add_type(f"<{type_id}/{name}>", inline)
            else:
                info.elements[name] = ""
        for attribute in node.iter(f"{_XS}attribute"):
            if self._owner(attribute, node) is not node:
                continue
            name = attribute.get("name")
            if name:
                info.attributes.add(name)
        return type_id

    @staticmethod
    def _owner(item: etree._Element, top: etree._Element) -> etree._Element:
        """Return the complexType node that directly holds ``item``."""
        node = item.getparent()
        while node is not None and node.tag != f"{_XS}complexType":
            node = node.getparent()
        return node if node is not None else top

    def declarer(self, type_id: str, key: str) -> str | None:
        """Return the type in the chain of ``type_id`` that declares ``key``.

        ``@name`` is an attribute.
        """
        seen: set[str] = set()
        current: str | None = type_id
        while current and current in self.types and current not in seen:
            seen.add(current)
            info = self.types[current]
            if (key[1:] in info.attributes) if key.startswith("@") else (key in info.elements):
                return current
            current = info.base
        return None

    def effective(self, type_id: str) -> tuple[dict[str, str], set[str]]:
        """Return the elements and attributes of a type, inherited ones included."""
        elements: dict[str, str] = {}
        attributes: set[str] = set()
        seen: set[str] = set()
        current: str | None = type_id
        while current and current in self.types and current not in seen:
            seen.add(current)
            info = self.types[current]
            for name, declared in info.elements.items():
                elements.setdefault(name, declared)
            attributes |= info.attributes
            current = info.base
        return elements, attributes


@cache
def _schema(edition: int) -> _Schema:
    return _Schema(SCHEMA_DIR / f"sep2_schema_{edition}.xsd")


@cache
def new_item(type_id: str, key: str) -> Item | None:
    """Return the item for ``key`` in ``type_id`` when 2023 added it, else None.

    ``key`` is an element name or ``@attribute``. The item names the type that
    declares it. None when the 2023 schema has no such item, or the 2018 schema
    already has it on that type.
    """
    declarer = _schema(2023).declarer(type_id, key)
    if declarer is None:
        return None
    elements, attributes = _schema(2018).effective(declarer)
    present = key[1:] in attributes if key.startswith("@") else key in elements
    return None if present else Item(declarer, key)


@cache
def root_attributes(edition: int) -> frozenset[str]:
    """Return the attributes ``edition`` declares on its global elements, not on a type."""
    schema = _schema(edition)
    names: set[str] = set()
    for name, type_id in schema.roots.items():
        if type_id == f"<{name}>":
            names |= schema.types[type_id].attributes
    return frozenset(names)


def borrowed_items(xml: bytes) -> list[Item]:
    """Return the items in ``xml`` that exist in the 2023 schema but not the 2018 one.

    Each is a (parent type, element name or ``@attribute``). Elements in other
    namespaces, which belong to an extension, are ignored, and so is anything the
    2023 schema does not know: schema validation reports those.
    """
    new = _schema(2023)
    root = etree.fromstring(xml)
    local = etree.QName(root).localname
    type_id = new.roots.get(local)
    found: list[Item] = []
    if type_id is None:
        return found
    _walk(root, type_id, new, found)
    return found


def _walk(node: etree._Element, type_id: str, schema: _Schema, found: list[Item]) -> None:
    elements, attributes = schema.effective(type_id)
    for name in node.attrib:
        if "}" not in name and name in attributes:
            item = new_item(type_id, f"@{name}")
            if item is not None:
                found.append(item)
    for child in node:
        if not isinstance(child.tag, str):
            continue
        qname = etree.QName(child)
        if qname.namespace not in (None, _SEP_NS) or qname.localname not in elements:
            continue
        item = new_item(type_id, qname.localname)
        if item is not None:
            # Only the outermost borrowed element: what is inside it is its own.
            found.append(item)
            continue
        child_type = elements[qname.localname]
        if child_type:
            _walk(child, child_type, schema, found)
