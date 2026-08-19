"""Tests for TLS configuration."""

import ssl
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from py20305.client.connector import Ieee2030TCPConnector, _verified_chain_der
from py20305.client.tls import (
    CertChainError,
    TlsConfig,
    build_cipher_string,
    create_ssl_context,
    verify_ieee2030_5_chain,
)

# ---------------------------------------------------------------------------
# Helpers for generating DER certs for chain validation tests
# ---------------------------------------------------------------------------


def _generate_chain_der(
    *,
    eku_critical: bool = False,
    name_constraints_non_critical: bool = False,
    policy_constraints: bool = False,
) -> list[bytes]:
    """Build a 3-cert chain (leaf, MICA, root) with optional bad extensions.

    Returns DER-encoded certs in leaf-first order (matching get_verified_chain).
    """
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.UTC)
    validity = datetime.timedelta(days=365)

    # Root CA
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root")])
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + validity)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )

    # Intermediate CA (MICA)
    mica_key = ec.generate_private_key(ec.SECP256R1())
    mica_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test MICA")])
    mica_builder = (
        x509.CertificateBuilder()
        .subject_name(mica_name)
        .issuer_name(root_name)
        .public_key(mica_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + validity)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(mica_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),
            critical=False,
        )
    )
    if eku_critical:
        mica_builder = mica_builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]),
            critical=True,
        )
    if name_constraints_non_critical:
        mica_builder = mica_builder.add_extension(
            x509.NameConstraints(
                permitted_subtrees=[x509.DNSName(".example.com")],
                excluded_subtrees=None,
            ),
            critical=False,
        )
    if policy_constraints:
        mica_builder = mica_builder.add_extension(
            x509.PolicyConstraints(require_explicit_policy=0, inhibit_policy_mapping=None),
            critical=True,
        )
    mica_cert = mica_builder.sign(root_key, hashes.SHA256())

    # Leaf
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Device")]))
        .issuer_name(mica_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + validity)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(mica_key.public_key()),
            critical=False,
        )
        .sign(mica_key, hashes.SHA256())
    )

    return [
        leaf_cert.public_bytes(serialization.Encoding.DER),
        mica_cert.public_bytes(serialization.Encoding.DER),
        root_cert.public_bytes(serialization.Encoding.DER),
    ]


def test_tls_config_defaults():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
    )
    assert cfg.check_hostname is True
    assert "ECDHE" in cfg.ciphers


def test_tls_config_frozen():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
    )
    with pytest.raises(AttributeError):
        cfg.check_hostname = False  # type: ignore[misc]


def test_tls_config_default_additional_ciphers_empty():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
    )
    assert cfg.additional_ciphers == ()
    assert build_cipher_string(cfg) == cfg.ciphers


def test_tls_config_send_lfdi_header_defaults_false():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
    )
    assert cfg.send_lfdi_header is False


def test_tls_config_send_lfdi_header_explicit_true():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
        send_lfdi_header=True,
    )
    assert cfg.send_lfdi_header is True


def test_tls_config_send_lfdi_header_is_frozen():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
    )
    with pytest.raises(AttributeError):
        cfg.send_lfdi_header = True  # type: ignore[misc]


def test_tls_config_lfdi_header_name_defaults_to_lfdi():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
    )
    assert cfg.lfdi_header_name == "LFDI"


def test_tls_config_lfdi_header_name_explicit_override():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
        lfdi_header_name="X-LFDI",
    )
    assert cfg.lfdi_header_name == "X-LFDI"


def test_tls_config_lfdi_header_name_is_frozen():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
    )
    with pytest.raises(AttributeError):
        cfg.lfdi_header_name = "X-LFDI"  # type: ignore[misc]


def testbuild_cipher_string_appends_extras():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
        additional_ciphers=("ECDHE-RSA-AES256-GCM-SHA384",),
    )
    result = build_cipher_string(cfg)
    assert result.startswith(cfg.ciphers + ":")
    assert result.endswith("ECDHE-RSA-AES256-GCM-SHA384")


def testbuild_cipher_string_joins_multiple_extras():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
        additional_ciphers=("ECDHE-RSA-AES256-GCM-SHA384", "ECDHE-RSA-AES128-GCM-SHA256"),
    )
    result = build_cipher_string(cfg)
    assert "ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-GCM-SHA256" in result


def test_create_ssl_context_passes_combined_cipher_string():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
        additional_ciphers=("ECDHE-RSA-AES256-GCM-SHA384",),
    )
    with (
        patch.object(ssl.SSLContext, "load_cert_chain"),
        patch.object(ssl.SSLContext, "load_verify_locations"),
        patch.object(ssl.SSLContext, "set_ciphers") as mock_set,
    ):
        create_ssl_context(cfg)
        mock_set.assert_called_once()
        passed = mock_set.call_args.args[0]
        assert "ECDHE-ECDSA-AES256-GCM-SHA384" in passed
        assert "ECDHE-RSA-AES256-GCM-SHA384" in passed
        # Baseline must come before extras so OpenSSL prefers ECDSA when offered.
        assert passed.index("ECDHE-ECDSA-AES256-GCM-SHA384") < passed.index(
            "ECDHE-RSA-AES256-GCM-SHA384"
        )


def test_create_ssl_context_logs_warning_when_extras_used(caplog):
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
        additional_ciphers=("ECDHE-RSA-AES256-GCM-SHA384",),
    )
    with (
        patch.object(ssl.SSLContext, "load_cert_chain"),
        patch.object(ssl.SSLContext, "load_verify_locations"),
        patch.object(ssl.SSLContext, "set_ciphers"),
        caplog.at_level("WARNING", logger="py20305.client.tls"),
    ):
        create_ssl_context(cfg)
    assert any(
        "relaxed beyond IEEE 2030.5 baseline" in r.message and "ECDHE-RSA" in r.message
        for r in caplog.records
    )


def test_create_ssl_context_no_warning_for_baseline_only(caplog):
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
    )
    with (
        patch.object(ssl.SSLContext, "load_cert_chain"),
        patch.object(ssl.SSLContext, "load_verify_locations"),
        patch.object(ssl.SSLContext, "set_ciphers"),
        caplog.at_level("WARNING", logger="py20305.client.tls"),
    ):
        create_ssl_context(cfg)
    assert not any("relaxed" in r.message for r in caplog.records)


def test_create_ssl_context_configures_correctly():
    """Verify SSL context is configured with correct TLS version and verification."""
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
    )
    with (
        patch.object(ssl.SSLContext, "load_cert_chain"),
        patch.object(ssl.SSLContext, "load_verify_locations"),
        patch.object(ssl.SSLContext, "set_ciphers"),
    ):
        ctx = create_ssl_context(cfg)
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
        assert ctx.maximum_version == ssl.TLSVersion.TLSv1_2
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True


def test_create_ssl_context_sets_partial_chain_flag():
    """VERIFY_X509_PARTIAL_CHAIN must be set for COMM-004A/B short chains."""
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
    )
    with (
        patch.object(ssl.SSLContext, "load_cert_chain"),
        patch.object(ssl.SSLContext, "load_verify_locations"),
        patch.object(ssl.SSLContext, "set_ciphers"),
    ):
        ctx = create_ssl_context(cfg)
        assert ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN


def test_create_ssl_context_no_hostname_check():
    cfg = TlsConfig(
        client_cert=Path("/tmp/cert.pem"),
        client_key=Path("/tmp/key.pem"),
        ca_cert=Path("/tmp/ca.pem"),
        check_hostname=False,
    )
    with (
        patch.object(ssl.SSLContext, "load_cert_chain"),
        patch.object(ssl.SSLContext, "load_verify_locations"),
        patch.object(ssl.SSLContext, "set_ciphers"),
    ):
        ctx = create_ssl_context(cfg)
        assert ctx.check_hostname is False


# ---------------------------------------------------------------------------
# IEEE 2030.5 chain validation tests
# ---------------------------------------------------------------------------


class TestVerifyIeee20305Chain:
    """Tests for verify_ieee2030_5_chain."""

    def test_valid_chain_accepted(self) -> None:
        chain = _generate_chain_der()
        verify_ieee2030_5_chain(chain)  # should not raise

    def test_empty_chain_accepted(self) -> None:
        verify_ieee2030_5_chain([])

    def test_single_cert_accepted(self) -> None:
        chain = _generate_chain_der()
        verify_ieee2030_5_chain([chain[0]])  # leaf only

    def test_two_cert_chain_no_intermediates(self) -> None:
        """Chain with just leaf + root has no intermediates to check."""
        chain = _generate_chain_der()
        verify_ieee2030_5_chain([chain[0], chain[2]])  # leaf + root

    def test_critical_eku_rejected(self) -> None:
        chain = _generate_chain_der(eku_critical=True)
        with pytest.raises(CertChainError, match="critical ExtendedKeyUsage"):
            verify_ieee2030_5_chain(chain)

    def test_non_critical_name_constraints_rejected(self) -> None:
        chain = _generate_chain_der(name_constraints_non_critical=True)
        with pytest.raises(CertChainError, match="non-critical NameConstraints"):
            verify_ieee2030_5_chain(chain)

    def test_policy_constraints_rejected(self) -> None:
        chain = _generate_chain_der(policy_constraints=True)
        with pytest.raises(CertChainError, match="PolicyConstraints"):
            verify_ieee2030_5_chain(chain)

    def test_cert_chain_error_is_ssl_error(self) -> None:
        """CertChainError should be catchable as ssl.SSLCertVerificationError."""
        assert issubclass(CertChainError, ssl.SSLCertVerificationError)


class TestIeee2030TCPConnector:
    """The connector runs verify_ieee2030_5_chain at handshake time so the audit
    gates every request method, not just the first GET."""

    @staticmethod
    def _transport_with_chain(chain_der: list[bytes] | None, *, ssl_present: bool = True):
        ssl_obj = None
        if ssl_present:
            ssl_obj = MagicMock()
            if chain_der is None:
                # Simulate Python < 3.13: no get_verified_chain attribute.
                ssl_obj = MagicMock(spec=[])
            else:
                ssl_obj.get_verified_chain.return_value = chain_der
        transport = MagicMock()
        transport.get_extra_info.return_value = ssl_obj
        return transport

    def test_audit_accepts_valid_chain(self):
        transport = self._transport_with_chain(_generate_chain_der())
        Ieee2030TCPConnector._audit_peer_chain(transport)  # no raise

    def test_audit_rejects_critical_eku(self):
        transport = self._transport_with_chain(_generate_chain_der(eku_critical=True))
        with pytest.raises(CertChainError, match="critical ExtendedKeyUsage"):
            Ieee2030TCPConnector._audit_peer_chain(transport)

    def test_audit_rejects_non_critical_name_constraints(self):
        transport = self._transport_with_chain(
            _generate_chain_der(name_constraints_non_critical=True)
        )
        with pytest.raises(CertChainError, match="non-critical NameConstraints"):
            Ieee2030TCPConnector._audit_peer_chain(transport)

    def test_audit_skips_plaintext_transport(self):
        transport = self._transport_with_chain(None, ssl_present=False)
        Ieee2030TCPConnector._audit_peer_chain(transport)  # no ssl_object -> skip

    def test_audit_skips_when_no_verified_chain(self):
        # Python < 3.13: ssl_object without get_verified_chain -> basic TLS already
        # ran, so skip rather than reject.
        transport = self._transport_with_chain(None)
        Ieee2030TCPConnector._audit_peer_chain(transport)  # no raise
        assert _verified_chain_der(transport.get_extra_info.return_value) is None

    @pytest.mark.asyncio
    async def test_wrap_create_connection_rejects_bad_chain_and_closes(self):
        connector = Ieee2030TCPConnector()
        try:
            transport = self._transport_with_chain(_generate_chain_der(eku_critical=True))
            with (
                patch.object(
                    aiohttp.TCPConnector,
                    "_wrap_create_connection",
                    new=AsyncMock(return_value=(transport, MagicMock())),
                ),
                pytest.raises(CertChainError),
            ):
                await connector._wrap_create_connection()
            transport.close.assert_called_once()  # aborted, never returned to the pool
        finally:
            await connector.close()

    @pytest.mark.asyncio
    async def test_wrap_create_connection_accepts_good_chain(self):
        connector = Ieee2030TCPConnector()
        try:
            transport = self._transport_with_chain(_generate_chain_der())
            proto = MagicMock()
            with patch.object(
                aiohttp.TCPConnector,
                "_wrap_create_connection",
                new=AsyncMock(return_value=(transport, proto)),
            ):
                result = await connector._wrap_create_connection()
            assert result == (transport, proto)
            transport.close.assert_not_called()
        finally:
            await connector.close()
