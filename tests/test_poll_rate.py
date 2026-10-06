"""Tests for poll rate normalization."""

import logging

from py20305.client.poll_rate import (
    DEFAULT_POLL_RATE,
    MIN_POLL_RATE,
    normalize_poll_rate,
)


def test_none_returns_default():
    assert normalize_poll_rate(None) == DEFAULT_POLL_RATE


def test_none_with_custom_default():
    assert normalize_poll_rate(None, default=60) == 60


def test_zero_returns_none():
    assert normalize_poll_rate(0) is None


def test_negative_returns_none():
    assert normalize_poll_rate(-5) is None


def test_below_min_clamps_up():
    assert normalize_poll_rate(1) == MIN_POLL_RATE


def test_slow_rate_is_not_capped():
    """A server may ask for slow polling; the client does not poll faster."""
    assert normalize_poll_rate(86400) == 86400


def test_in_range_unchanged():
    assert normalize_poll_rate(300) == 300


def test_at_min_boundary():
    assert normalize_poll_rate(MIN_POLL_RATE) == MIN_POLL_RATE


def test_rate_over_a_day_is_honored_with_a_warning(caplog):
    """No ceiling, but a resource polled less than daily should not go quiet unexplained."""
    with caplog.at_level(logging.WARNING, logger="py20305.client.poll_rate"):
        assert normalize_poll_rate(4_294_967_295, resource_key="derc") == 4_294_967_295
    assert any("derc" in r.getMessage() and "4294967295" in r.getMessage() for r in caplog.records)


def test_daily_rate_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="py20305.client.poll_rate"):
        assert normalize_poll_rate(86_400, resource_key="time") == 86_400
    assert not caplog.records
