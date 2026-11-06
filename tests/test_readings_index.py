"""Indexing a connector's readings keeps readings that differ only in multiplier."""

from __future__ import annotations

import logging

import pytest

from py20305.connectors.reading import Reading
from py20305.readings import index_readings


def test_readings_that_differ_only_in_multiplier_are_both_kept() -> None:
    tenths = Reading(uom=29, value=2400.0, phase=224, multiplier=-1)
    hundredths = Reading(uom=29, value=24000.0, phase=224, multiplier=-2)

    indexed = index_readings([tenths, hundredths])

    assert list(indexed.values()) == [tenths, hundredths]
    assert len(indexed) == 2


def test_a_reading_with_the_same_identity_is_dropped_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = Reading(uom=29, value=2400.0, phase=224, multiplier=-1)
    again = Reading(uom=29, value=2401.0, phase=224, multiplier=-1)

    with caplog.at_level(logging.WARNING):
        indexed = index_readings([first, again])

    assert list(indexed.values()) == [first]
    assert any("second reading" in r.getMessage() for r in caplog.records)


def test_a_reading_that_sets_the_default_multiplier_matches_one_that_leaves_it_unset() -> None:
    unset = Reading(uom=29, value=2400.0, phase=224)
    explicit = Reading(uom=29, value=2401.0, phase=224, multiplier=-1)

    assert list(index_readings([unset, explicit]).values()) == [unset]
