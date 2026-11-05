"""The schema profile of an IEEE 2030.5 server.

Servers differ in which edition of the standard they implement, and a server on
one edition sometimes adopts a function of the other, for example a 2018 server
that takes the import and export limits 2023 added. A :class:`ServerProfile`
holds that answer in one place, and every edition question in the client asks it
instead of testing a flag:

- ``base``: the edition the server implements, ``2018`` or ``2023``.
- ``features``: functions borrowed from another edition. Each is one entry in
  :data:`FEATURES`: the elements it borrows, the schema that defines them and
  the control mode bits it permits. A new blend is a new entry there.
- ``extensions``: schemas in other namespaces, such as CSIP-AUS.

Inbound, the client accepts any element either edition defines. Outbound, it
sends an element of the other edition only when the profile enables it.
"""

from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Any

from lxml import etree
from pydantic import BaseModel
from xsdata_pydantic.bindings import XmlContext

from py20305.schema_editions import Item, borrowed_items, new_item

logger = logging.getLogger(__name__)


class Edition(IntEnum):
    """An edition of IEEE 2030.5."""

    E2018 = 2018
    E2023 = 2023


@dataclass(frozen=True)
class Feature:
    """A function one edition defines that a server of the other edition adopts."""

    name: str
    summary: str
    #: The edition whose schema defines the elements.
    schema: Edition
    #: The elements the feature borrows, each as (parent type, element name).
    elements: frozenset[Item]
    #: DERControlType2 bits the feature permits in modesSupported2, modesEnabled2
    #: and modesResponded2. The bits are permitted, never set: the connector says
    #: which it supports.
    mode_bits2: frozenset[int] = frozenset()


#: Every blend of the editions a server can adopt.
FEATURES: dict[str, Feature] = {
    feature.name: feature
    for feature in (
        Feature(
            name="import_export_limits",
            summary=(
                "opModMaxLimWAbsorb and opModMaxLimWInject (IEEE 2030.5-2023 clause 10.10), "
                "with DERControlType2 bits 7 and 8 in DERCapability modesSupported2, "
                "DERSettings modesEnabled2 and DERControlResponse modesResponded2"
            ),
            schema=Edition.E2023,
            elements=frozenset(
                {
                    Item("DERControlBase", "opModMaxLimWAbsorb"),
                    Item("DERControlBase", "opModMaxLimWInject"),
                    Item("DERCapability", "modesSupported2"),
                    Item("DERSettings", "modesEnabled2"),
                    Item("DERControlResponse", "modesResponded2"),
                }
            ),
            mode_bits2=frozenset({7, 8}),
        ),
    )
}

#: Extension names accepted in ``extensions``, with the namespace each declares.
EXTENSIONS: dict[str, str] = {
    "csip-aus-1.2": "https://csipaus.org/ns",
    "csip-aus-1.3": "https://csipaus.org/ns/v1.3",
}

#: Response status 251 (Not Supported) is 2023 Table 31; 2018 Table 27 reserves it
#: and gives 252 (Rejected: control parameter not applicable) the nearest meaning.
_RESPONSE_CODES_2018 = {251: 252}

_SCHEMA_VER = re.compile(r"^\s*(\d+)\.(\d+)\s*$")
_CONTEXT = XmlContext()


def base_from_schema_ver(schema_ver: str | None) -> Edition:
    """Return the edition a DeviceCapability ``schemaVer`` stands for.

    2.2 or higher is 2023. Anything else, including no attribute, is 2018: the
    2023 text assumes an absent schemaVer means 2.1, and its schema makes the
    attribute optional, so absence is weak evidence and does not select 2023.
    """
    match = _SCHEMA_VER.match(schema_ver or "")
    if match and (int(match.group(1)), int(match.group(2))) >= (2, 2):
        return Edition.E2023
    return Edition.E2018


def declared_schema_ver(body: bytes) -> str | None:
    """Return the ``schemaVer`` attribute on the root element of ``body``, or None."""
    try:
        root = etree.fromstring(
            body, parser=etree.XMLParser(resolve_entities=False, no_network=True)
        )
    except etree.XMLSyntaxError:
        return None
    return root.get("schemaVer")


@dataclass(frozen=True)
class ServerProfile:
    """What a server implements, and so what the client may send it."""

    #: The server's edition, or None until it is known (``auto``).
    base: Edition | None = None
    features: frozenset[str] = frozenset()
    #: Extension schemas in use, or None to detect them from the server.
    extensions: frozenset[str] | None = None
    #: Accept a subscription the server created without a Location header, by
    #: giving it a synthetic URI. A workaround for non-conforming servers: both
    #: editions require the header on 201 Created.
    missing_location_fallback: bool = False
    #: Why ``base`` is what it is, for the log.
    evidence: str = ""

    def __post_init__(self) -> None:
        unknown = sorted(set(self.features) - set(FEATURES))
        if unknown:
            raise ValueError(
                f"unknown schema feature {', '.join(unknown)}; known: {', '.join(sorted(FEATURES))}"
            )
        bad = sorted(set(self.extensions or ()) - set(EXTENSIONS))
        if bad:
            raise ValueError(
                f"unknown schema extension {', '.join(bad)}; known: {', '.join(sorted(EXTENSIONS))}"
            )

    # -- Resolving ----------------------------------------------------------------

    @property
    def edition(self) -> Edition:
        """The edition to act on: the base, or 2023 until the server has been seen."""
        return self.base if self.base is not None else Edition.E2023

    @property
    def is_2018(self) -> bool:
        return self.edition is Edition.E2018

    @property
    def resolved(self) -> bool:
        return self.base is not None

    def with_base(self, base: Edition, evidence: str) -> ServerProfile:
        return replace(self, base=base, evidence=evidence)

    def resolve_from_device_capability(self, body: bytes) -> ServerProfile:
        """Decide an ``auto`` base from the DeviceCapability body; ``evidence`` says why."""
        if self.base is not None:
            return self
        schema_ver = declared_schema_ver(body)
        base = base_from_schema_ver(schema_ver)
        shown = f'schemaVer="{schema_ver}"' if schema_ver is not None else "no schemaVer"
        return self.with_base(base, f"DeviceCapability has {shown}")

    # -- Questions the client asks ------------------------------------------------

    @property
    def schema_ver(self) -> str | None:
        """The ``schemaVer`` to put on payloads: 2.2 for 2023, none for 2018."""
        return None if self.is_2018 else "2.2"

    @property
    def connect_status_fields(self) -> tuple[str, ...]:
        """The DERStatus connect fields the edition defines."""
        if self.is_2018:
            return ("genConnectStatus", "storConnectStatus")
        return ("connectStatus",)

    @property
    def allows_multi_der(self) -> bool:
        """Whether a DERList may hold several DER objects (2018 allowed it)."""
        return self.is_2018

    def wire_response_code(self, code: int) -> int:
        """Return the Response status to send for ``code``: Table 27 (2018) or Table 31."""
        return _RESPONSE_CODES_2018.get(code, code) if self.is_2018 else code

    @property
    def mode_bits2(self) -> frozenset[int]:
        """The DERControlType2 bits the profile permits."""
        if not self.is_2018:
            return frozenset(range(10))
        bits: set[int] = set()
        for name in self.features:
            bits |= FEATURES[name].mode_bits2
        return frozenset(bits)

    def mask_modes2(self, bits: int) -> int:
        """Return ``bits`` limited to the DERControlType2 bits the profile permits."""
        allowed = 0
        for bit in self.mode_bits2:
            allowed |= 1 << bit
        return bits & allowed

    def permits(self, parent: str, name: str) -> bool:
        """Return whether the profile lets the client send ``name`` inside ``parent``.

        ``parent`` is the type, ``name`` an element or ``@attribute``. Anything
        the 2018 type already has is permitted; a 2023 addition is permitted
        under a 2023 base or when an enabled feature covers it.
        """
        item = new_item(parent, name)
        return item is None or self.permits_item(item)

    def permits_item(self, item: Item) -> bool:
        """Return whether the profile lets the client send a 2023 ``item``."""
        if not self.is_2018:
            return True
        return any(item in FEATURES[f].elements for f in self.features)

    @property
    def sends_modes_responded(self) -> bool:
        """Whether DERControlResponse carries ``modesResponded`` (2023 defines it)."""
        return self.permits("DERControlResponse", "modesResponded")

    @property
    def sends_modes_responded2(self) -> bool:
        """Whether DERControlResponse carries ``modesResponded2``."""
        return self.permits("DERControlResponse", "modesResponded2")

    # -- Outbound strictness ------------------------------------------------------

    def restrict(self, model: Any) -> Any:
        """Return ``model`` without the elements and attributes the profile does not permit.

        Omission is on the model, so the serializer never sees them. A required
        field stays. The ``schemaVer`` attribute is left off for 2018.
        """
        if not self.is_2018:
            return model
        return self._restrict(model)

    def _restrict(self, node: Any) -> Any:
        if isinstance(node, list):
            return [self._restrict(item) for item in node]
        if not isinstance(node, BaseModel):
            return node
        cls = type(node)
        type_id = getattr(getattr(cls, "Meta", None), "name", None) or cls.__name__
        update: dict[str, Any] = {}
        for var in _CONTEXT.build(cls).get_all_vars():
            value = getattr(node, var.name, None)
            if value is None or var.is_wildcard or var.is_attributes:
                continue
            key = f"@{var.local_name}" if var.is_attribute else var.local_name
            if var.is_attribute and var.local_name == "schemaVer":
                update[var.name] = None
            elif not var.required and not self.permits(type_id, key):
                update[var.name] = [] if isinstance(value, list) else None
            else:
                restricted = self._restrict(value)
                if restricted is not value:
                    update[var.name] = restricted
        return node.model_copy(update=update) if update else node

    def audit(self, xml: bytes) -> list[Item]:
        """Return the items in ``xml`` that are outside the base edition and not enabled.

        Empty for a 2023 base. For 2018: an element or attribute 2023 added to
        its type that no enabled feature covers, and a ``schemaVer`` attribute.
        """
        if not self.is_2018:
            return []
        found = [item for item in borrowed_items(xml) if not self.permits_item(item)]
        root = declared_schema_ver(xml)
        if root is not None:
            found.append(Item("(root)", "@schemaVer"))
        return found

    # -- Inbound ------------------------------------------------------------------

    def borrowed_in(self, xml: bytes) -> list[tuple[Item, str | None]]:
        """Return the 2023 items in an inbound ``xml`` with the feature each belongs to.

        Used to log what the server actually sends; nothing is rejected.
        """
        found: list[tuple[Item, str | None]] = []
        for item in borrowed_items(xml):
            owner = next((f for f, feat in FEATURES.items() if item in feat.elements), None)
            found.append((item, owner))
        return found

    def describe(self) -> str:
        parts = [f"base {self.edition.value}" + ("" if self.resolved else " (not yet known)")]
        if self.features:
            parts.append("features " + ", ".join(sorted(self.features)))
        if self.extensions:
            parts.append("extensions " + ", ".join(sorted(self.extensions)))
        return "; ".join(parts)


def profile_from_legacy_flag(enabled: bool) -> ServerProfile:
    """Return the profile the deprecated ``server_2018_compat`` flag stands for.

    ``True`` is base 2018 with the missing-Location fallback. ``False`` is the
    flag's old default, which asked for no 2018 behavior: base 2023.
    """
    warnings.warn(
        "server_2018_compat is deprecated; use server.schema.base: 2018 and, for a server "
        "that omits the Location header on a created subscription, "
        "server.missing_location_fallback: true",
        DeprecationWarning,
        stacklevel=3,
    )
    if enabled:
        return ServerProfile(
            base=Edition.E2018,
            missing_location_fallback=True,
            evidence="server_2018_compat",
        )
    return ServerProfile(base=Edition.E2023, evidence="server_2018_compat")


def feature_names(features: Iterable[str]) -> frozenset[str]:
    """Return ``features`` as a set, checked against the registry."""
    return ServerProfile(features=frozenset(features)).features
