"""Layered validation of IEEE 2030.5 bodies that carry extension elements.

The IEEE 2030.5-2023 schema does not declare CSIP-AUS extension elements, and
utilities mix 2018 and 2023 elements with CSIP-AUS ones, so no single schema
accepts every valid body. ``validate_xml`` validates the IEEE 2030.5 part
against the IEEE 2030.5 schema and each extension element on its own against
its namespace's schema. Expected outcomes come from the schemas: CSIP-AUS
``doeModesSupported`` is a DOEControlType, hexBinary of at most one octet
(csipaus-ext.xsd); DERCapability requires ``modesSupported``, ``rtgMaxW`` and
``type`` (IEEE 2030.5-2023).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web

from py20305.client.http import Sep2Client
from py20305.xml.serialization import validate_xml, validate_xml_result

SEP = "urn:ieee:std:2030.5:ns"
CSIPAUS = "https://csipaus.org/ns"
CSIPAUS_13 = "https://csipaus.org/ns/v1.3"
SCHEMAS = Path(__file__).resolve().parents[1] / "src" / "py20305" / "schemas"

MODES = "<modesSupported>00000000</modesSupported>"
RATING = "<rtgMaxW><multiplier>0</multiplier><value>1000</value></rtgMaxW>"
TYPE = "<type>4</type>"


def _capability(*children: str, csipaus: str = CSIPAUS) -> bytes:
    """A DERCapability body with ``children`` in the order given."""
    return (
        f'<DERCapability xmlns="{SEP}" xmlns:csipaus="{csipaus}">'
        + "".join(children)
        + "</DERCapability>"
    ).encode()


def _doe(value: str = "03") -> str:
    return f"<csipaus:doeModesSupported>{value}</csipaus:doeModesSupported>"


@pytest.mark.parametrize(
    "children",
    [
        (_doe(), MODES, RATING, TYPE),  # extension first
        (MODES, RATING, _doe(), TYPE),  # in the middle
        (MODES, RATING, TYPE, _doe()),  # last
    ],
    ids=["start", "middle", "end"],
)
@pytest.mark.parametrize("namespace", [CSIPAUS, CSIPAUS_13])
def test_a_valid_csipaus_extension_is_valid_wherever_it_appears(
    children: tuple[str, ...], namespace: str
) -> None:
    assert validate_xml(_capability(*children, csipaus=namespace)) == []


@pytest.mark.parametrize("namespace", [CSIPAUS, CSIPAUS_13])
@pytest.mark.parametrize("value", ["0F0F", "zz"])
def test_a_bad_extension_value_is_an_error_naming_the_element(namespace: str, value: str) -> None:
    errors = validate_xml(_capability(MODES, RATING, TYPE, _doe(value), csipaus=namespace))

    assert len(errors) == 1
    assert errors[0].startswith("doeModesSupported: ")


def test_an_element_of_an_unknown_namespace_is_an_error_naming_both() -> None:
    body = _capability(
        MODES, RATING, TYPE, '<x:limit xmlns:x="https://example.com/ext">5</x:limit>'
    )

    assert validate_xml(body) == [
        "limit: no schema is known for namespace 'https://example.com/ext'"
    ]


def test_an_ieee_2030_5_error_is_reported_beside_a_valid_extension() -> None:
    # rtgMaxW is required by the IEEE 2030.5 schema.
    errors = validate_xml(_capability(_doe(), MODES, TYPE))

    assert len(errors) == 1
    assert "rtgMaxW" in errors[0] or "type" in errors[0]
    assert not errors[0].startswith("doeModesSupported")


def test_errors_in_both_layers_are_all_reported() -> None:
    errors = validate_xml(_capability(_doe("zz"), MODES, TYPE))

    assert len(errors) == 2
    assert errors[1].startswith("doeModesSupported: ")


def test_a_body_that_is_an_extension_resource_is_validated_against_its_schema() -> None:
    body = (
        f'<ConnectionPoint xmlns="{CSIPAUS}"><connectionPointId>NMI1</connectionPointId>'
        "</ConnectionPoint>"
    ).encode()

    assert validate_xml(body) == []


@pytest.mark.parametrize(("value", "valid"), [("5000", True), ("lots", False)])
def test_ieee_2030_5_elements_inside_an_extension_are_checked_with_it(
    value: str, valid: bool
) -> None:
    """opModExpLimW is an ActivePower: its multiplier and value are IEEE 2030.5
    elements, checked with the extension rather than set aside from it."""
    body = _capability(
        MODES,
        RATING,
        TYPE,
        f"<csipaus:opModExpLimW><multiplier>0</multiplier><value>{value}</value>"
        "</csipaus:opModExpLimW>",
    )

    errors = validate_xml(body)

    assert (errors == []) is valid
    assert all(error.startswith("opModExpLimW: ") for error in errors)


def test_an_extension_element_that_is_not_a_resource_cannot_be_the_root() -> None:
    body = f'<doeModesSupported xmlns="{CSIPAUS}">03</doeModesSupported>'.encode()

    errors = validate_xml(body)

    assert errors
    assert "doeModesSupported" in errors[0] and "IEEE 2030.5 namespace" in errors[0]


def test_a_root_in_an_unrelated_namespace_is_an_error() -> None:
    errors = validate_xml(b'<Time xmlns="urn:example:other"><currentTime>0</currentTime></Time>')

    assert any("IEEE 2030.5 namespace" in error for error in errors)


def test_a_schema_dir_with_only_the_extension_schema_uses_the_packaged_imports(
    tmp_path: Path,
) -> None:
    """csipaus-ext.xsd imports the 2018 schema by relative path; the directory lacks it."""
    (tmp_path / "csipaus-ext.xsd").write_bytes((SCHEMAS / "csipaus-ext.xsd").read_bytes())
    assert not (tmp_path / "sep2_schema_2018.xsd").exists()

    assert validate_xml(_capability(MODES, RATING, TYPE, _doe()), schema_dir=tmp_path) == []
    errors = validate_xml(_capability(MODES, RATING, TYPE, _doe("zz")), schema_dir=tmp_path)
    assert errors and all(error.startswith("doeModesSupported: ") for error in errors)


def test_stray_text_after_an_extension_is_an_error_not_a_parse_failure() -> None:
    errors = validate_xml(_capability(MODES, RATING, _doe() + "stray", TYPE))

    assert errors
    assert not any("parse error" in error for error in errors)


def test_whitespace_after_an_extension_is_valid() -> None:
    assert validate_xml(_capability(MODES, RATING, _doe() + "\n  ", TYPE)) == []


def test_an_explicit_schema_path_validates_against_that_schema_alone() -> None:
    """The single-schema mode is kept: the 2023 schema alone rejects the extension."""
    ok, error = validate_xml_result(
        _capability(MODES, RATING, TYPE, _doe()), SCHEMAS / "sep2_schema_2023.xsd"
    )

    assert ok is False
    assert error is not None and "doeModesSupported" in error


async def test_a_captured_csipaus_exchange_is_marked_valid(aiohttp_server) -> None:
    from py20305.observers import Observers

    async def accept(request: web.Request) -> web.Response:
        return web.Response(status=204)

    app = web.Application()
    app.router.add_put("/der/1/dercap", accept)
    server = await aiohttp_server(app)
    records: list = []

    async with Sep2Client(
        str(server.make_url("")).rstrip("/"), observers=Observers(exchange=records.append)
    ) as client:
        client.set_schema_validator(str(SCHEMAS))
        await client.put_bytes(
            "/der/1/dercap", _capability(MODES, RATING, TYPE, _doe(), csipaus=CSIPAUS_13)
        )

    assert [(r.request_valid, r.request_validation_error) for r in records] == [(True, None)]
