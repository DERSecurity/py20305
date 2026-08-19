"""Tests for LFDI/SFDI computation."""

from pathlib import Path

import pytest

from py20305.security.identity import (
    compute_cert_fingerprint,
    compute_lfdi,
    compute_sfdi,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def test_cert_pem() -> str:
    return (FIXTURES / "test_client.pem").read_text()


def test_compute_lfdi(test_cert_pem: str):
    lfdi = compute_lfdi(test_cert_pem)
    assert lfdi == "fe9d4315af233c2e9bfa89e3f5f9b645e5b157f2"


def test_compute_sfdi(test_cert_pem: str):
    lfdi = compute_lfdi(test_cert_pem)
    sfdi = compute_sfdi(lfdi)
    assert sfdi == 683475070343


def test_lfdi_is_40_hex_chars(test_cert_pem: str):
    lfdi = compute_lfdi(test_cert_pem)
    assert len(lfdi) == 40
    int(lfdi, 16)  # should not raise


def test_lfdi_is_lowercase(test_cert_pem: str):
    lfdi = compute_lfdi(test_cert_pem)
    assert lfdi == lfdi.lower()


def test_compute_lfdi_invalid_pem():
    with pytest.raises(ValueError, match="Failed to parse"):
        compute_lfdi("not a certificate")


def test_compute_cert_fingerprint_is_64_upper_hex(test_cert_pem: str):
    fp = compute_cert_fingerprint(test_cert_pem)
    assert len(fp) == 64
    assert fp == fp.upper()
    int(fp, 16)  # should not raise


def test_fingerprint_prefix_is_the_lfdi(test_cert_pem: str):
    """The LFDI is the leftmost 40 hex of the same SHA-256 digest."""
    fp = compute_cert_fingerprint(test_cert_pem)
    assert fp[:40].lower() == compute_lfdi(test_cert_pem)


def test_compute_cert_fingerprint_invalid_pem():
    with pytest.raises(ValueError, match="Failed to parse"):
        compute_cert_fingerprint("not a certificate")


def test_compute_sfdi_short_lfdi():
    with pytest.raises(ValueError, match="at least 9"):
        compute_sfdi("abcd")


@pytest.mark.parametrize(
    ("lfdi", "expected"),
    [
        # IEEE 2030.5 §6.3.3 sum-of-digits checksum, verified against a live
        # server's EndDeviceList. These LFDIs are cases where sum-of-digits and
        # Luhn DIVERGE (the old Luhn code produced ...397 / ...391 respectively),
        # so they guard against a checksum-algorithm regression.
        ("3e4f45ab31edfe5b67e343e5e4562e3100000000", 167261211391),  # Luhn -> ...397
        ("30a13b2277d008c891e06e1d34ee728cc3a6db2b", 130539648399),
    ],
)
def test_compute_sfdi_sum_of_digits_checksum(lfdi: str, expected: int):
    assert compute_sfdi(lfdi) == expected
