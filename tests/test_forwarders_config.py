"""Tests for forwarder configuration models."""

from __future__ import annotations

from pathlib import Path

import pytest

from py20305.forwarders.config import (
    ForwarderConfig,
    MQTTForwarderConfig,
)


class TestForwarderConfig:
    def test_empty_config(self) -> None:
        config = ForwarderConfig()
        assert config.mqtt is None
        assert config.schema_dir is None
        assert config.has_enabled_forwarders() is False

    def test_mqtt_configured(self) -> None:
        config = ForwarderConfig(
            mqtt=MQTTForwarderConfig(endpoint="broker.example.com"),
        )
        assert config.mqtt is not None
        assert config.mqtt.endpoint == "broker.example.com"
        assert config.has_enabled_forwarders() is True

    def test_mqtt_disabled(self) -> None:
        config = ForwarderConfig(
            mqtt=MQTTForwarderConfig(endpoint="broker.example.com", enabled=False),
        )
        assert config.has_enabled_forwarders() is False

    def test_schema_dir(self) -> None:
        config = ForwarderConfig(schema_dir=Path("/schemas"))
        assert config.schema_dir == Path("/schemas")

    def test_model_validate_from_dict(self) -> None:
        data = {
            "schema_dir": "/schemas",
            "mqtt": {
                "endpoint": "xxx.iot.region.amazonaws.com",
            },
        }
        config = ForwarderConfig.model_validate(data)
        assert config.mqtt is not None
        assert config.mqtt.endpoint == "xxx.iot.region.amazonaws.com"
        assert config.schema_dir == Path("/schemas")


class TestMQTTForwarderConfigPlainMQTT:
    """Tests for TLS-optional MQTT forwarder configuration."""

    def test_plain_mqtt_no_certs(self) -> None:
        """Config with no cert fields is valid (plain MQTT)."""
        config = MQTTForwarderConfig(
            endpoint="rabbitmq",
            port=1883,
        )
        assert config.endpoint == "rabbitmq"
        assert config.port == 1883
        assert config.cert_path is None
        assert config.key_path is None
        assert config.ca_path is None
        assert config.tls_enabled is False

    def test_tls_enabled_property(self, tmp_path: Path) -> None:
        """Config with cert fields has tls_enabled=True."""
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        ca = tmp_path / "ca.pem"
        cert.touch()
        key.touch()
        ca.touch()

        config = MQTTForwarderConfig(
            endpoint="iot.example.com",
            cert_path=cert,
            key_path=key,
            ca_path=ca,
        )
        assert config.tls_enabled is True

    def test_partial_certs_raises(self, tmp_path: Path) -> None:
        """Config with only some cert fields raises ValueError."""
        cert = tmp_path / "cert.pem"
        cert.touch()

        with pytest.raises(ValueError, match="Partial TLS configuration"):
            MQTTForwarderConfig(
                endpoint="iot.example.com",
                cert_path=cert,
            )

    def test_partial_certs_two_of_three_raises(self, tmp_path: Path) -> None:
        """Config with two of three cert fields raises ValueError."""
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        cert.touch()
        key.touch()

        with pytest.raises(ValueError, match="Partial TLS configuration"):
            MQTTForwarderConfig(
                endpoint="iot.example.com",
                cert_path=cert,
                key_path=key,
            )
