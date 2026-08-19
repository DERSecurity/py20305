"""Tests for poll rate normalization."""

from py20305.client.poll_rate import (
    DEFAULT_POLL_RATE,
    MAX_POLL_RATE,
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


def test_above_max_clamps_down():
    assert normalize_poll_rate(10000) == MAX_POLL_RATE


def test_in_range_unchanged():
    assert normalize_poll_rate(300) == 300


def test_at_min_boundary():
    assert normalize_poll_rate(MIN_POLL_RATE) == MIN_POLL_RATE


def test_at_max_boundary():
    assert normalize_poll_rate(MAX_POLL_RATE) == MAX_POLL_RATE
