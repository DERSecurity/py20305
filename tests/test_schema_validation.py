"""IEEE 2030.5 XSD schema validation tests.

Tests validate_xml_result() against both generated and captured XML samples.
Covers valid messages for all resource types, intentionally invalid messages,
and real captured traffic.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from py20305.xml import serialization
from py20305.xml.serialization import validate_xml_result

# The schemas the runtime loads, not a second copy kept beside them: a test
# validating against a different file than production uses would pass while
# the shipped package was broken.
SCHEMA_DIR = Path(os.environ.get("IEEE2030_5_SCHEMA_DIR", str(serialization._SCHEMA_DIR)))
SCHEMA_PATH = SCHEMA_DIR / "sep2_schema_2023.xsd"


@pytest.fixture(scope="module", autouse=True)  # type: ignore[misc]
def _require_schema() -> None:
    """Assert the XSD ships with the package rather than only with the repo."""
    assert SCHEMA_PATH.exists(), f"XSD schema not found at {SCHEMA_PATH}"


# ============================================================================
# VALID SAMPLES — all should pass
# ============================================================================


class TestValidXml:
    """Every valid IEEE 2030.5 resource type should pass XSD validation."""

    def test_valid_sample_passes(
        self,
        valid_xml_sample: tuple[str, bytes],
    ) -> None:
        resource_type, xml_bytes = valid_xml_sample
        is_valid, error = validate_xml_result(xml_bytes, SCHEMA_PATH)
        assert is_valid, f"{resource_type} failed validation: {error}"
        assert error is None


# ============================================================================
# INVALID SAMPLES — all should fail
# ============================================================================


class TestInvalidXml:
    """Intentionally broken XML should be caught by XSD validation."""

    def test_invalid_sample_fails(
        self,
        invalid_xml_sample: tuple[str, bytes],
    ) -> None:
        label, xml_bytes = invalid_xml_sample
        is_valid, error = validate_xml_result(xml_bytes, SCHEMA_PATH)
        assert not is_valid, f"Expected {label!r} to fail validation but it passed"
        assert error is not None

    def test_empty_bytes(self) -> None:
        is_valid, error = validate_xml_result(b"", SCHEMA_PATH)
        assert not is_valid
        assert error is not None

    def test_not_xml(self) -> None:
        is_valid, error = validate_xml_result(b"this is not xml at all", SCHEMA_PATH)
        assert not is_valid
        assert error is not None

    def test_valid_xml_wrong_schema(self) -> None:
        """Well-formed XML that isn't IEEE 2030.5."""
        xml = b'<?xml version="1.0"?><html><body>hello</body></html>'
        is_valid, _error = validate_xml_result(xml, SCHEMA_PATH)
        assert not is_valid


# ============================================================================
# CAPTURED TRAFFIC — validate real-world messages
# ============================================================================


class TestCapturedTraffic:
    """Validate captured XML from a live aggregator session."""

    def test_captured_file_validates(
        self,
        captured_xml_files: list[Path],
    ) -> None:
        """Every captured XML file should pass XSD validation."""
        assert len(captured_xml_files) > 0, (
            "No captured XML files (run: python scripts/capture_messages.py)"
        )
        failures: list[str] = []
        for xml_file in captured_xml_files:
            xml_bytes = xml_file.read_bytes()
            is_valid, error = validate_xml_result(xml_bytes, SCHEMA_PATH)
            if not is_valid:
                failures.append(f"{xml_file.name}: {error}")

        assert not failures, f"{len(failures)} captured files failed validation:\n" + "\n".join(
            failures[:10]
        )

    def test_captured_corpus_coverage(
        self,
        captured_xml_files: list[Path],
    ) -> None:
        """Captured corpus should include multiple resource types."""
        assert len(captured_xml_files) > 0, (
            "No captured XML files (run: python scripts/capture_messages.py)"
        )
        root_elements: set[str] = set()
        for xml_file in captured_xml_files:
            content = xml_file.read_bytes()
            # Extract root element name (crude but sufficient)
            for line in content.decode("utf-8", errors="replace").split("\n"):
                line = line.strip()
                if line.startswith("<") and not line.startswith("<?"):
                    tag = line.split()[0].lstrip("<").split(">")[0]
                    root_elements.add(tag)
                    break

        # Expect at least these common resource types
        expected = {"DeviceCapability", "EndDeviceList", "DERControlList", "Time"}
        missing = expected - root_elements
        assert not missing, (
            f"Captured corpus missing resource types: {missing}. "
            f"Found: {root_elements}. Run more poll cycles."
        )


# ============================================================================
# validate_xml_result() BEHAVIOR
# ============================================================================


class TestValidateXmlResult:
    def test_returns_tuple_for_valid(self) -> None:
        from tests.fixtures.xml_samples import VALID_TIME

        is_valid, error = validate_xml_result(VALID_TIME, SCHEMA_PATH)
        assert is_valid is True
        assert error is None

    def test_returns_tuple_for_invalid(self) -> None:
        from tests.fixtures.xml_samples import INVALID_BAD_NAMESPACE

        is_valid, error = validate_xml_result(INVALID_BAD_NAMESPACE, SCHEMA_PATH)
        assert is_valid is False
        assert error is not None

    def test_handles_missing_schema(self) -> None:
        with pytest.raises((FileNotFoundError, OSError)):
            validate_xml_result(b"<x/>", Path("/nonexistent/schema.xsd"))

    def test_accepts_string_input(self) -> None:
        from tests.fixtures.xml_samples import VALID_TIME

        is_valid, _error = validate_xml_result(VALID_TIME.decode("utf-8"), SCHEMA_PATH)
        assert is_valid is True
