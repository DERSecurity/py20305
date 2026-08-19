"""LFDI/SFDI identity computation per IEEE 2030.5."""

from __future__ import annotations

import hashlib
import secrets

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding


def compute_lfdi(cert_pem: str) -> str:
    """Compute the LFDI from a PEM-encoded certificate.

    The leftmost 40 hex of the certificate's SHA-256 fingerprint, lowercase
    (per IEEE 2030.5). Derived from :func:`compute_cert_fingerprint` so the two
    can never disagree about the underlying digest. Raises ValueError if the
    certificate cannot be parsed.
    """
    return compute_cert_fingerprint(cert_pem)[:40].lower()


def compute_cert_fingerprint(cert_pem: str) -> str:
    """Compute the full SHA-256 fingerprint of a PEM-encoded certificate.

    SHA-256 of the DER-encoded certificate, all 64 hex characters, uppercase.
    This is the value some servers key device/aggregator enrollment on (the
    LFDI is the leftmost 40 hex of the same digest). Raises ValueError if the
    certificate cannot be parsed.
    """
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to parse PEM certificate: {exc}") from exc
    der_bytes = cert.public_bytes(Encoding.DER)
    return hashlib.sha256(der_bytes).hexdigest().upper()


def compute_sfdi(lfdi: str) -> int:
    """Compute the SFDI from an LFDI string.

    Per IEEE 2030.5 §6.3.3: the SFDI is the 36 most-significant bits of the LFDI
    (its first 9 hex characters) as a decimal, with a **sum-of-digits** checksum
    digit appended so the whole SFDI's digit sum is a multiple of 10. This is a
    sum-of-digits checksum, NOT Luhn -- the two coincide for many values but
    diverge for others (e.g. LFDI ``3e4f45ab3...`` -> ``...391``, not ``...397``),
    and the server rejects the wrong one.
    """
    if len(lfdi) < 9:
        raise ValueError(f"LFDI must be at least 9 characters, got {len(lfdi)}")
    base = int(lfdi[:9], 16)
    check = (10 - sum(int(d) for d in str(base)) % 10) % 10
    return base * 10 + check


#: IANA Private Enterprise Number stamped into a generated LFDI when the
#: caller does not supply one. A placeholder so the function works out of
#: the box; deployments should pass their own organization's PEN.
_DEFAULT_PEN = 53630


def generate_proxied_lfdi(pen: int = _DEFAULT_PEN) -> str:
    """Generate an LFDI for a proxied device that has no certificate of its own.

    A device behind a gateway still needs an LFDI to be addressed by, but has
    no certificate to derive one from. This builds one instead: 128 random
    bits concatenated with the PEN, SHA-256 hashed, truncated to 40 hex
    characters. The PEN makes collisions across vendors improbable, and the
    randomness makes them improbable within one.

    Args:
        pen: IANA Private Enterprise Number to attribute the generated
            identity to. Pass your own organization's PEN; the default is
            only a placeholder so the function is callable out of the box.

    Returns:
        40-character lowercase hex LFDI string.
    """
    random_bytes = secrets.token_bytes(16)
    pen_bytes = pen.to_bytes(4, byteorder="big")
    combined = random_bytes + pen_bytes
    digest = hashlib.sha256(combined).hexdigest()
    return digest[:40]
