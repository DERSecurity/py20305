"""Tests for the configuration file a deployed client is started from."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from py20305.config import ClientConfig, ConfigError, load_config

MINIMAL = {
    "server": {"url": "https://server.example.com:8443"},
    "tls": {
        "client_cert": "certs/client.pem",
        "client_key": "certs/client.key",
        "ca_cert": "certs/ca.pem",
    },
}


def _write(tmp_path: Path, data: dict, name: str = "client.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestLoading:
    def test_loads_a_minimal_configuration(self, tmp_path: Path) -> None:
        config = load_config(_write(tmp_path, MINIMAL))
        assert config.server.url == "https://server.example.com:8443"
        assert config.devices == []

    def test_defaults_are_usable_without_being_written_out(self, tmp_path: Path) -> None:
        """A minimal file should produce a runnable client, not one needing more."""
        config = load_config(_write(tmp_path, MINIMAL))
        assert config.logging.level == "INFO"
        assert config.api.enabled is False
        assert config.connection.retry_forever is True
        assert config.register_on_start is True

    def test_yaml_is_accepted(self, tmp_path: Path) -> None:
        pytest.importorskip("yaml")
        path = tmp_path / "client.yaml"
        path.write_text(
            "server:\n"
            "  url: https://server.example.com:8443\n"
            "tls:\n"
            "  client_cert: certs/client.pem\n"
            "  client_key: certs/client.key\n"
            "  ca_cert: certs/ca.pem\n",
            encoding="utf-8",
        )
        assert load_config(path).server.url == "https://server.example.com:8443"

    def test_missing_file_names_the_path(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "absent.yaml")

    def test_unsupported_suffix_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "client.ini"
        path.write_text("[server]", encoding="utf-8")
        with pytest.raises(ConfigError, match="unsupported configuration format"):
            load_config(path)

    def test_malformed_json_is_reported_as_such(self, tmp_path: Path) -> None:
        path = tmp_path / "client.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError, match="not valid JSON"):
            load_config(path)

    def test_a_non_mapping_document_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "client.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ConfigError, match="mapping at the top level"):
            load_config(path)


class TestValidationMessages:
    """An operator reading a startup log should learn which field is wrong."""

    def test_a_missing_section_names_the_field(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="tls"):
            load_config(_write(tmp_path, {"server": {"url": "https://x:8443"}}))

    def test_a_plain_http_url_is_refused(self, tmp_path: Path) -> None:
        """IEEE 2030.5 is mutual TLS; http:// cannot work and should say so."""
        data = {**MINIMAL, "server": {"url": "http://server.example.com:8443"}}
        with pytest.raises(ConfigError, match="https"):
            load_config(_write(tmp_path, data))

    def test_a_url_with_no_host_is_rejected(self, tmp_path: Path) -> None:
        """A prefix check accepts this; it then fails deep inside the HTTP client."""
        data = {**MINIMAL, "server": {"url": "https://"}}
        with pytest.raises(ConfigError, match="host"):
            load_config(_write(tmp_path, data))

    def test_a_malformed_device_lfdi_names_the_device(self, tmp_path: Path) -> None:
        data = {**MINIMAL, "devices": [{"type": "print_demo", "lfdi": "too-short"}]}
        with pytest.raises(ConfigError, match="lfdi"):
            load_config(_write(tmp_path, data))

    def test_two_devices_with_one_lfdi_is_an_error(self, tmp_path: Path) -> None:
        """The registry keys on LFDI, so a duplicate silently drops a device."""
        lfdi = "a" * 40
        data = {
            **MINIMAL,
            "devices": [
                {"type": "print_demo", "lfdi": lfdi},
                {"type": "print_demo", "lfdi": lfdi.upper()},
            ],
        }
        with pytest.raises(ConfigError, match="duplicate device lfdi"):
            load_config(_write(tmp_path, data))

    @pytest.mark.parametrize(
        ("section", "bad"),
        [
            ("connection", {"retry_foreverr": False}),
            ("logging", {"levell": "DEBUG"}),
            ("api", {"prot": 9000}),
            ("server", {"urll": "https://x:8443"}),
        ],
    )
    def test_a_misspelled_setting_is_rejected_not_ignored(
        self, tmp_path: Path, section: str, bad: dict
    ) -> None:
        """Silently ignoring it is the mistake an operator cannot diagnose.

        `retry_foreverr: false` would validate and the client would retry
        forever anyway, contradicting the promise that a configuration mistake
        surfaces at startup.
        """
        data = {**MINIMAL}
        data[section] = {**data.get(section, {}), **bad}
        with pytest.raises(ConfigError):
            load_config(_write(tmp_path, data))

    def test_a_misspelled_device_setting_is_rejected(self, tmp_path: Path) -> None:
        data = {**MINIMAL, "devices": [{"type": "print_demo", "lfdi": "a" * 40, "hostt": "x"}]}
        with pytest.raises(ConfigError):
            load_config(_write(tmp_path, data))

    def test_an_unknown_device_type_is_rejected(self, tmp_path: Path) -> None:
        data = {**MINIMAL, "devices": [{"type": "nonesuch", "lfdi": "a" * 40}]}
        with pytest.raises(ConfigError):
            load_config(_write(tmp_path, data))


class TestPathResolution:
    def test_relative_paths_resolve_against_the_file_not_the_cwd(self, tmp_path: Path) -> None:
        """systemd does not start the process in the directory you wrote it in."""
        config = load_config(_write(tmp_path, MINIMAL))
        assert config.tls.client_cert == (tmp_path / "certs/client.pem").resolve()
        assert config.tls.ca_cert == (tmp_path / "certs/ca.pem").resolve()

    def test_absolute_paths_are_left_alone(self, tmp_path: Path) -> None:
        absolute = (tmp_path / "elsewhere" / "client.pem").resolve()
        data = {**MINIMAL, "tls": {**MINIMAL["tls"], "client_cert": str(absolute)}}
        assert load_config(_write(tmp_path, data)).tls.client_cert == absolute

    def test_a_devices_own_tls_paths_resolve_too(self, tmp_path: Path) -> None:
        """A SunSpec device over TLS carries certificate paths of its own.

        They go straight to an SSL context, so resolving only the server's
        would make the same relative path mean two different things depending
        on which section of the file it appeared in.
        """
        data = {
            **MINIMAL,
            "devices": [
                {
                    "type": "sunspec",
                    "lfdi": "a" * 40,
                    "transport": "tcp+tls",
                    "host": "10.0.0.5",
                    "ca_path": "device/ca.pem",
                    "cert_path": "device/client.pem",
                    "key_path": "device/client.key",
                }
            ],
        }
        device = load_config(_write(tmp_path, data)).devices[0]
        assert Path(device.ca_path) == (tmp_path / "device/ca.pem").resolve()
        assert Path(device.cert_path) == (tmp_path / "device/client.pem").resolve()
        assert Path(device.key_path) == (tmp_path / "device/client.key").resolve()

    def test_a_relative_log_file_resolves_too(self, tmp_path: Path) -> None:
        data = {**MINIMAL, "logging": {"level": "INFO", "file": "logs/client.log"}}
        config = load_config(_write(tmp_path, data))
        assert config.logging.file == (tmp_path / "logs/client.log").resolve()


class TestDefaultsThatAreSecurityDecisions:
    def test_hostname_verification_is_on_by_default(self) -> None:
        assert ClientConfig.model_validate(MINIMAL).tls.check_hostname is True

    def test_the_api_binds_to_loopback_by_default(self) -> None:
        """It is unauthenticated, so the default must not be routable."""
        assert ClientConfig.model_validate(MINIMAL).api.host == "127.0.0.1"

    def test_no_extra_ciphers_by_default(self) -> None:
        assert ClientConfig.model_validate(MINIMAL).tls.additional_ciphers == ()


class TestTelemetryDefaults:
    """What a configuration file that says nothing about telemetry gets."""

    def test_reporting_is_on(self) -> None:
        """A registered client that then says nothing looks failed to a server."""
        assert ClientConfig.model_validate(MINIMAL).telemetry.enabled is True

    def test_the_der_resource_rates(self) -> None:
        telemetry = ClientConfig.model_validate(MINIMAL).telemetry
        assert telemetry.post_rate_seconds == 300
        assert telemetry.der_capability_poll_rate_seconds == 86400
        assert telemetry.der_settings_poll_rate_seconds == 60

    @pytest.mark.parametrize(
        "field",
        ["post_rate_seconds", "der_capability_poll_rate_seconds", "der_settings_poll_rate_seconds"],
    )
    def test_a_rate_of_zero_is_rejected(self, field: str) -> None:
        """Zero would schedule a cycle that never waits."""
        with pytest.raises(ValidationError):
            ClientConfig.model_validate({**MINIMAL, "telemetry": {field: 0}})
