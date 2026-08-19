"""Tests for proxied LFDI generation."""

from __future__ import annotations

from unittest.mock import patch

from py20305.security.identity import compute_sfdi, generate_proxied_lfdi


class TestGenerateProxiedLfdi:
    def test_returns_40_hex_chars(self):
        lfdi = generate_proxied_lfdi()
        assert len(lfdi) == 40
        int(lfdi, 16)  # should not raise

    def test_different_each_call(self):
        a = generate_proxied_lfdi()
        b = generate_proxied_lfdi()
        assert a != b

    def test_sfdi_computable(self):
        lfdi = generate_proxied_lfdi()
        sfdi = compute_sfdi(lfdi)
        assert isinstance(sfdi, int)
        assert sfdi > 0

    def test_custom_pen(self):
        lfdi = generate_proxied_lfdi(pen=12345)
        assert len(lfdi) == 40

    def test_deterministic_with_mock(self):
        with patch("py20305.security.identity.secrets.token_bytes") as mock_token:
            mock_token.return_value = b"\x00" * 16
            lfdi1 = generate_proxied_lfdi()
            mock_token.return_value = b"\x00" * 16
            lfdi2 = generate_proxied_lfdi()
            assert lfdi1 == lfdi2
