"""Configuration for message forwarders.

Configuration is loaded from the config file's forwarders map.
Each forwarder type is an optional typed field on ForwarderConfig.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictForwarderModel(BaseModel):
    """Base for the forwarder models: an unknown key is an error.

    These are reachable from the runner's configuration file, where Pydantic's
    default of ignoring unknown fields would make a misspelling silently do
    nothing -- ``topic_bass:`` would validate and publish to the default topic.
    Every other configuration model in this package rejects unknown keys, and a
    section that quietly did not would be the one an operator gets wrong.
    """

    model_config = ConfigDict(extra="forbid")


class MQTTForwarderConfig(_StrictForwarderModel):
    """Configuration for MQTT message forwarding.

    Supports both mTLS connections (e.g., AWS IoT Core) and plain MQTT brokers.
    When cert_path/key_path/ca_path are all omitted, connects without TLS.
    When any cert path is provided, all three are required and validated.
    """

    endpoint: str = Field(description="MQTT broker hostname or IP")
    port: int = Field(default=8883, description="MQTT broker port (8883 for TLS, 1883 for plain)")
    cert_path: Path | None = Field(default=None, description="Path to client certificate (.pem)")
    key_path: Path | None = Field(default=None, description="Path to client private key (.key)")
    ca_path: Path | None = Field(default=None, description="Path to CA certificate (.pem)")
    topic_base: str = Field(default="csip_client", description="Base topic for publishing")
    client_id_prefix: str = Field(default="csip_client", description="Prefix for MQTT client ID")
    enabled: bool = Field(default=True, description="Whether forwarding is enabled")

    @property
    def tls_enabled(self) -> bool:
        """Return whether TLS is configured."""
        return self.cert_path is not None

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v

    @model_validator(mode="after")
    def validate_cert_paths(self) -> MQTTForwarderConfig:
        """Validate certificate file configuration.

        If all cert paths are None, TLS is disabled (plain MQTT).
        If any cert path is provided, all three must be present and point to
        existing files.
        """
        if not self.enabled:
            return self

        cert_fields = {
            "cert_path": self.cert_path,
            "key_path": self.key_path,
            "ca_path": self.ca_path,
        }
        provided = {k for k, v in cert_fields.items() if v is not None}
        missing = set(cert_fields) - provided

        # All None = plain MQTT, no validation needed
        if not provided:
            return self

        # Partial = error
        if missing:
            raise ValueError(
                f"Partial TLS configuration: {', '.join(sorted(provided))} provided "
                f"but {', '.join(sorted(missing))} missing. "
                f"Provide all three cert paths for TLS, or omit all for plain MQTT."
            )

        # All provided = validate files exist.
        #
        # Absolute paths only. A relative one is resolved against the directory
        # of the configuration file, which happens after validation and is not
        # visible here -- checking it now would test it against the working
        # directory instead, so a correct configuration would fail whenever the
        # client was started from anywhere but the config file's own directory.
        # The client's own TLS paths are treated the same way, and an
        # unreadable file surfaces when the context is built.
        for field_name, path in cert_fields.items():
            assert path is not None  # mypy narrowing
            if not path.is_absolute():
                continue
            if not path.exists():
                raise ValueError(f"{field_name} does not exist: {path}")
            if not path.is_file():
                raise ValueError(f"{field_name} is not a file: {path}")

        return self


class DeviceTelemetryConfig(_StrictForwarderModel):
    """Configuration for reporting southbound device reads and writes.

    Off by default: an operator who has not asked for southbound telemetry
    should not start publishing their device traffic on an upgrade.
    """

    enabled: bool = Field(
        default=False,
        description="Whether to report device reads and writes to the monitoring system",
    )
    topic_suffix: str = Field(
        default="",
        description=(
            "Topic to publish device telemetry on, relative to the forwarder topic base. "
            "Empty (the default) publishes alongside the existing protocol messages, which "
            "is where the rest of the captured traffic already goes."
        ),
    )

    @field_validator("topic_suffix")
    @classmethod
    def validate_topic_suffix(cls, v: str) -> str:
        """Normalize the suffix, allowing empty to mean "the default topic"."""
        return v.strip().strip("/")


class ForwarderConfig(_StrictForwarderModel):
    """Top-level forwarder configuration.

    Each forwarder type is an optional typed field. This replaces the
    previous list-of-ForwarderSpec approach with named fields.
    """

    schema_dir: Path | None = Field(
        default=None,
        description="Path to IEEE 2030.5 XSD schemas for message validation",
    )
    mqtt: MQTTForwarderConfig | None = Field(
        default=None,
        description="MQTT forwarder configuration",
    )
    retry_interval_seconds: int = Field(
        default=60,
        ge=0,
        description=(
            "How often to retry a forwarder whose broker was unreachable. "
            "Zero disables retrying, leaving a failed forwarder stopped for "
            "the lifetime of the process."
        ),
    )
    device_telemetry: DeviceTelemetryConfig = Field(
        default_factory=DeviceTelemetryConfig,
        description="Reporting of southbound device reads and writes",
    )

    def has_enabled_forwarders(self) -> bool:
        """Return True if any forwarder is configured and enabled."""
        return bool(self.mqtt is not None and self.mqtt.enabled)
