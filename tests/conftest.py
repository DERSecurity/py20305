"""Shared test fixtures."""


import pytest

from py20305 import diagnostics as _diagnostics
from py20305.models.sep.sep import Time, TimeOffsetType, TimeType

# Register xml_samples fixtures (valid_xml_sample, invalid_xml_sample, captured_xml_files)
pytest_plugins = ["tests.fixtures.xml_samples"]

# Make CSIP server code importable in tests.

@pytest.fixture(autouse=True)  # type: ignore[misc]
def _reset_diagnostics_store() -> None:
    """Drop the process-wide diagnostics store before every test.

    The module-level ``_store`` would otherwise leak state across tests
    (a previous test's ``report(...)`` call sticking around in the next
    test's snapshot). Tests that need a known store still install one
    explicitly via ``monkeypatch.setattr`` or ``init_store()``; the
    create_app() factory creates a fresh one when none is initialised.
    """
    _diagnostics._store = None


def make_time(current: int = 0, quality: int = 0) -> Time:
    """Build a minimal Time instance for tests."""
    tt = TimeType(value=current)
    zero_offset = TimeOffsetType(value=0)
    return Time(
        current_time=tt,
        dst_end_time=tt,
        dst_offset=zero_offset,
        dst_start_time=tt,
        quality=quality,
        tz_offset=zero_offset,
    )


@pytest.fixture  # type: ignore[misc]
def sample_time() -> Time:
    return make_time(1234567890, 5)
