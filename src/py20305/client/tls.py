"""TLS configuration for IEEE 2030.5 connections."""

from __future__ import annotations

import functools
import logging
import ssl
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import ExtensionOID

logger = logging.getLogger(__name__)

# IEEE 2030.5 §6.11 mandates ECDSA-only cipher suites. Peers that present RSA
# server certs (e.g., utility test endpoints fronted by enterprise PKI) cannot
# complete a handshake against this list alone — `additional_ciphers` lets the
# operator opt in to extra suites for those endpoints.
_IEEE_2030_5_CIPHERS = "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES128-CCM8:@SECLEVEL=0"


@functools.cache
def _known_tls12_suites() -> frozenset[str]:
    """Every TLS 1.2 suite name this OpenSSL build offers that actually encrypts.

    Asked of OpenSSL once, then used as an allowlist, for two reasons.

    The resolved set is the only reliable way to tell a suite name from a
    group alias. OpenSSL's aliases are spelled exactly like suite names, so no
    pattern separates them -- ``RSA`` selects 13 suites, ``AES`` 50, ``PSK``
    24 -- and a denylist of "group keywords" is only ever a list of the ones
    someone thought of. Membership of this set has no such gap.

    Resolving once rather than per call also keeps validation independent of
    the ``ssl`` module at call time, which matters because callers
    legitimately patch ``ssl.SSLContext`` when testing context construction.

    Suites reporting zero key strength are excluded: ``NULL-SHA256`` is a
    genuine, singular TLS 1.2 suite name, and it encrypts nothing.
    """
    probe = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    probe.minimum_version = ssl.TLSVersion.TLSv1_2
    probe.maximum_version = ssl.TLSVersion.TLSv1_2
    probe.set_ciphers("ALL:COMPLEMENTOFALL")
    return frozenset(
        cipher["name"]
        for cipher in probe.get_ciphers()
        if cipher.get("protocol") == "TLSv1.2"
        and cipher.get("strength_bits")
        and cipher.get("alg_bits")
    )

@dataclass(frozen=True)
class TlsConfig:
    """TLS client configuration for IEEE 2030.5."""

    client_cert: Path
    client_key: Path
    ca_cert: Path
    check_hostname: bool = True
    ciphers: str = _IEEE_2030_5_CIPHERS
    additional_ciphers: tuple[str, ...] = ()
    # Send the client's cert-derived LFDI as an HTTP header on every outbound
    # request. Off by default: IEEE 2030.5 §6.11.7.2 says servers SHOULD
    # derive the LFDI from the client cert, so the header is non-spec and
    # can trip strict-validation WAFs. Enable for utility deployments
    # fronted by a TLS-terminating proxy that strips the client cert before
    # the backend sees it.
    send_lfdi_header: bool = False
    # Header name to carry the LFDI under. IEEE 2030.5 doesn't standardize
    # this -- known peer conventions vary, so the name is configurable. The
    # default ("LFDI") matches the only peer convention we have evidence for;
    # override per peer (e.g. "X-LFDI") when a peer expects a different header.
    # Only honored when send_lfdi_header is True.
    lfdi_header_name: str = "LFDI"


def build_cipher_string(config: TlsConfig) -> str:
    """Combine the baseline cipher list with any operator-supplied additions.

    Each addition must name exactly one suite, which is checked against
    OpenSSL rather than assumed. An OpenSSL cipher string is an expression
    language, so a single entry can rewrite the policy instead of adding to
    it: ``ALL`` widens it to everything, ``!aNULL`` removes what the baseline
    exists to require, and plain-looking aliases such as ``RSA`` or ``AES``
    quietly expand to dozens of suites. The result of this function goes
    straight to ``SSLContext.set_ciphers``, which accepts all of that
    silently, so nothing downstream would catch it.

    Raises:
        ValueError: If an addition does not name exactly one TLS 1.2 suite.
    """
    if not config.additional_ciphers:
        return config.ciphers

    for name in config.additional_ciphers:
        if name not in _known_tls12_suites():
            raise ValueError(
                f"additional_ciphers entries must each name exactly one TLS 1.2 "
                f"cipher suite; {name!r} is unknown to OpenSSL or selects a group, "
                f"which would widen the IEEE 2030.5 baseline rather than add to it"
            )

    extras = ":".join(config.additional_ciphers)
    return f"{config.ciphers}:{extras}"


def create_ssl_context(config: TlsConfig) -> ssl.SSLContext:
    """Create an SSL context from a TlsConfig."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = config.check_hostname
    if config.additional_ciphers:
        logger.warning(
            "TLS cipher policy relaxed beyond IEEE 2030.5 baseline; additional ciphers: %s",
            ", ".join(config.additional_ciphers),
        )
    ctx.set_ciphers(build_cipher_string(config))
    ctx.load_cert_chain(str(config.client_cert), str(config.client_key))
    ctx.load_verify_locations(str(config.ca_cert))
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    return ctx


class CertChainError(ssl.SSLCertVerificationError):
    """IEEE 2030.5 certificate chain validation failure.

    Raised when the peer's certificate chain violates IEEE 2030.5
    Section 6.11.3 requirements that OpenSSL does not enforce natively.
    """


def verify_ieee2030_5_chain(chain_der: list[bytes]) -> None:
    """Validate a verified cert chain against IEEE 2030.5 PKI requirements.

    Checks intermediate CA certificates (all except leaf and root) for
    extensions that IEEE 2030.5 and RFC 5280 prohibit or constrain:

    - Extended Key Usage MUST NOT be marked critical on CA certs
      (IEEE 2030.5 Section 6.11.3)
    - Name Constraints MUST be critical if present (RFC 5280 Section 4.2.1.10)
    - Policy Constraints MUST NOT be present (IEEE 2030.5 Section 6.11.3)

    Args:
        chain_der: List of DER-encoded certificates from get_verified_chain(),
            ordered leaf-first (index 0 = leaf, last = root).

    Raises:
        CertChainError: If any CA cert violates IEEE 2030.5 requirements.
    """
    if len(chain_der) < 2:
        return

    # Check CA certs (skip leaf at [0], skip root at [-1])
    ca_certs = chain_der[1:-1] if len(chain_der) > 2 else []

    for der_bytes in ca_certs:
        cert = x509.load_der_x509_certificate(der_bytes)
        cn = _cert_cn(cert)

        for ext in cert.extensions:
            # EKU on CA certs must not be critical
            if ext.oid == ExtensionOID.EXTENDED_KEY_USAGE and ext.critical:
                raise CertChainError(f"CA cert '{cn}' has critical ExtendedKeyUsage")

            # NameConstraints must be critical if present (RFC 5280)
            if ext.oid == ExtensionOID.NAME_CONSTRAINTS and not ext.critical:
                raise CertChainError(f"CA cert '{cn}' has non-critical NameConstraints")

            # PolicyConstraints forbidden in IEEE 2030.5 manufacturing PKI
            if ext.oid == ExtensionOID.POLICY_CONSTRAINTS:
                raise CertChainError(f"CA cert '{cn}' has PolicyConstraints (forbidden)")


def _cert_cn(cert: x509.Certificate) -> str:
    """Extract the Common Name from a certificate, or '(unknown)' if absent."""
    attrs = cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else "(unknown)"
