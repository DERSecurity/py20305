"""Guards for defects the extraction introduced.

Each of these was found reviewing the extraction commit, and each passed the
existing suite while broken -- because the existing suite came from the
repository this code was extracted from, and tested it in the shape it had
there. These pin the shape it has here.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest import mock

import pytest

from py20305.client.tls import TlsConfig, build_cipher_string
from py20305.connectors.config import PrintDemoDeviceConfig
from py20305.connectors.registry import ConnectorConfigRegistry


class TestRegistryCaching:
    def test_lookup_is_cached_whatever_the_case(self) -> None:
        """A differently-cased lookup must not rebuild the connector.

        The registry documents case-insensitive lookup. Caching under the
        caller's spelling rather than the configured one means an uppercase
        caller misses the cache every time -- rebuilding the proxy and, for a
        Modbus device, reconnecting and rescanning on every dispatch, while
        defeating the proxy's failure cache and single-flight lock.
        """
        registry = ConnectorConfigRegistry([PrintDemoDeviceConfig(lfdi="a" * 40)])

        upper_first = registry.get_connector("A" * 40)
        upper_again = registry.get_connector("A" * 40)
        lower = registry.get_connector("a" * 40)

        assert upper_first is upper_again
        assert upper_first is lower
        assert upper_first is not None
        assert upper_first.resolve() is lower.resolve()  # type: ignore[union-attr]

    def test_unknown_lfdi_is_not_cached(self) -> None:
        registry = ConnectorConfigRegistry([PrintDemoDeviceConfig(lfdi="a" * 40)])
        assert registry.get_connector("f" * 40) is None
        assert list(registry._device_proxies) == []


class TestCipherPolicy:
    """``additional_ciphers`` goes straight to ``set_ciphers``, which accepts a
    whole expression language. An addition must add, not rewrite."""

    BASE = {"client_cert": Path("c.pem"), "client_key": Path("c.key"), "ca_cert": Path("ca.pem")}

    @pytest.mark.parametrize(
        "name", ["ECDHE-RSA-AES256-GCM-SHA384", "ECDHE-RSA-AES128-GCM-SHA256"]
    )
    def test_accepts_a_plain_suite_name(self, name: str) -> None:
        assert name in build_cipher_string(TlsConfig(**self.BASE, additional_ciphers=(name,)))

    @pytest.mark.parametrize(
        "name",
        [
            "ALL",            # widens to everything OpenSSL will do
            "COMPLEMENTOFALL",
            "HIGH",
            "DEFAULT",
            "MEDIUM",
            # Aliases spelled exactly like suite names, which is why a denylist
            # of "group keywords" cannot work: each expands to many suites.
            "RSA",
            "AES",
            "AESGCM",
            "ECDHE",
            "PSK",
            "DH",
            "SHA256",
            "kRSA",
            "eNULL",          # resolves to exactly one suite -- that encrypts nothing
            "aNULL",          # no authentication
            "!aNULL",         # removal operator
            "-ECDHE-ECDSA-AES256-GCM-SHA384",
            "A:B",            # list separator
            "@SECLEVEL=0",    # directive
            "TLSv1",
            "nonsense",       # not a cipher at all
        ],
    )
    def test_rejects_anything_that_rewrites_the_baseline(self, name: str) -> None:
        with pytest.raises(ValueError):
            build_cipher_string(TlsConfig(**self.BASE, additional_ciphers=(name,)))

    def test_baseline_is_unchanged_without_additions(self) -> None:
        config = TlsConfig(**self.BASE)
        assert build_cipher_string(config) == config.ciphers


class TestLazyClientPackage:
    def test_submodule_access_does_not_depend_on_import_order(self) -> None:
        """``client.tls`` must not exist only after unrelated code ran.

        Making the package lazy to break an import cycle left submodules
        unbound until some export happened to import one as a side effect, so
        the same attribute raised or resolved depending on what came first.
        """
        import subprocess
        import sys

        probe = (
            "import py20305.client as c; "
            "c.tls; c.errors; c.http; c.discovery; print('ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_unknown_attribute_still_raises(self) -> None:
        import py20305.client as client_pkg

        with pytest.raises(AttributeError):
            client_pkg.definitely_not_a_module  # noqa: B018 - the access is the assertion

    def test_a_broken_submodule_reports_its_own_missing_dependency(self) -> None:
        """An import failure inside a submodule must not become AttributeError.

        Catching every ModuleNotFoundError would swallow a submodule that
        exists but cannot import one of its own dependencies, reporting the
        attribute as absent and hiding which package is actually missing.
        """
        import py20305.client as client_pkg

        with mock.patch.dict(sys.modules):
            sys.modules.pop("py20305.client.tls", None)
            client_pkg.__dict__.pop("tls", None)
            missing = ModuleNotFoundError(
                "No module named 'cryptography'", name="cryptography"
            )
            with (
                mock.patch.object(importlib, "import_module", side_effect=missing),
                pytest.raises(ModuleNotFoundError, match="cryptography"),
            ):
                client_pkg.tls  # noqa: B018 - the access is the assertion
