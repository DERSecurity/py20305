"""Guards for defects the extraction introduced.

Each of these was found reviewing the extraction commit, and each passed the
existing suite while broken -- because the existing suite came from the
repository this code was extracted from, and tested it in the shape it had
there. These pin the shape it has here.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

from py20305.client.tls import TlsConfig, build_cipher_string
from py20305.connectors.config import PrintDemoDeviceConfig
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.forwarders import types as types_module
from py20305.forwarders.types import (
    NetworkEndpoint,
    PayloadEnvelope,
    Protocol,
    ProtocolMessage,
    ProtocolMetadata,
    WireDirection,
)


def _message(**overrides: object) -> ProtocolMessage:
    kwargs: dict = {
        "protocol": Protocol.IEEE_2030_5,
        "direction": WireDirection.UPSTREAM,
        "client_id": "c" * 40,
        "payload": PayloadEnvelope.from_xml("<DERControl/>"),
        "source": NetworkEndpoint(ip="10.0.0.5", port=443),
        "timestamp": "2026-08-14T12:00:00+00:00",
    }
    kwargs.update(overrides)
    return ProtocolMessage(**kwargs)  # type: ignore[arg-type]


class TestWireFormatVersion:
    #: What a consumer of this format requires of the ``version`` field: a bare
    #: semantic version, or the literal "2.0" from the original wire format.
    #: Reproduced here because the consumer's schema lives in a package this
    #: one deliberately does not depend on -- so this is the only place the
    #: constraint is written down on this side of the boundary.
    PATTERN = re.compile(r"^(2\.0|\d+\.\d+\.\d+(?:[-+][\w.-]+)?)$")

    def test_version_satisfies_the_consumer_pattern(self) -> None:
        """A descriptive producer string here makes every message invalid.

        ``version`` is a required, pattern-constrained field. Emitting
        something like ``py20305/0.1.0`` passes every test that
        only round-trips our own output, and is rejected by the consumer.
        """
        version = _message().to_dict()["version"]
        assert self.PATTERN.match(version), (
            f"version {version!r} does not match the consumer's required pattern"
        )

    def test_provenance_is_carried_by_forwarder_id(self) -> None:
        """Identifying the producer is what ``forwarder_id`` is for."""
        assert _message(forwarder_id="site-1").to_dict()["forwarder_id"] == "site-1"

    @pytest.mark.parametrize(
        "installed",
        [
            "unknown",     # what get_package_version() returns from a source tree
            "0.1.0.dev1",  # a PEP 440 development build
            "0.1.0rc1",
            "",
        ],
    )
    def test_an_unusable_installed_version_still_yields_a_valid_field(
        self, installed: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The field is required, so an invalid value invalidates the message.

        Passing the installed version straight through is not enough: running
        from a source tree reports ``unknown``, and a development build carries
        a PEP 440 suffix. Neither satisfies the consumer.
        """
        monkeypatch.setattr(types_module, "get_package_version", lambda: installed)
        value = types_module._wire_version()
        assert self.PATTERN.match(value), (
            f"installed version {installed!r} produced {value!r}, which the consumer rejects"
        )

    def test_a_normal_release_is_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(types_module, "get_package_version", lambda: "1.4.2")
        assert types_module._wire_version() == "1.4.2"


class TestWireFormatFidelity:
    def test_mac_survives_a_round_trip(self) -> None:
        """The format defines ``mac``; dropping it loses a producer's data."""
        wire = {"ip": "10.0.0.5", "port": 443, "mac": "aa:bb:cc:dd:ee:ff"}
        assert NetworkEndpoint.from_dict(wire).to_dict() == wire

    def test_mac_is_normalized_to_one_spelling(self) -> None:
        """Either separator is accepted; one form is emitted."""
        endpoint = NetworkEndpoint(ip="10.0.0.5", port=443, mac="AA-BB-CC-DD-EE-FF")
        assert endpoint.to_dict()["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_malformed_mac_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="six hex pairs"):
            NetworkEndpoint(ip="10.0.0.5", port=443, mac="not-a-mac")

    def test_unmodelled_metadata_keys_keep_their_position(self) -> None:
        """A relayed message must not be quietly reshaped.

        This client models only the fields IEEE 2030.5 populates. Folding the
        rest into ``extra`` would move another producer's keys a level deeper,
        so they stay where they were found.
        """
        wire = {
            "lfdi": "c" * 40,
            "uri": "/derp/1/derc",
            "ocpp_version": "2.0.1",
            "function_code": 3,
            "extra": {"site": "north"},
        }
        assert ProtocolMetadata.from_dict(wire).to_dict() == wire

    def test_non_mapping_extra_is_rejected_not_discarded(self) -> None:
        with pytest.raises(TypeError, match="must be a mapping"):
            ProtocolMetadata.from_dict({"extra": "not-a-mapping"})

    def test_message_round_trips_unchanged(self) -> None:
        original = _message(
            destination=NetworkEndpoint(ip="1.2.3.4", port=443),
            protocol_data=ProtocolMetadata(lfdi="c" * 40, uri="/derc"),
        ).to_dict()
        assert ProtocolMessage.from_dict(original).to_dict() == original


class TestWireDirectionIsDistinct:
    def test_does_not_shadow_the_capture_side_enum(self) -> None:
        """Two same-named enums compare unequal with nothing to say so.

        ``forwarders.base`` defines a capture-side ``MessageDirection`` with
        identical members. Naming the wire enum the same makes a cross-module
        comparison silently False, which is why it has its own name.
        """
        from py20305.forwarders import types

        assert not hasattr(types, "MessageDirection"), (
            "forwarders.types must not export a second MessageDirection"
        )


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
