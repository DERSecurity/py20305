"""The configuration file a deployed client is started from.

One document describes a running client: which server to talk to, which
certificate to present, which devices to drive, and what to expose locally.
It is validated on load, so a mistake surfaces at startup with a message
naming the field rather than hours later as a failed poll.

Loading is separate from the models on purpose. Anything embedding this client
in its own application already has configuration of its own and should build
:class:`ClientConfig` directly, or skip it and construct the client itself --
:func:`load_config` exists for the command-line runner, not as the only way in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from py20305.connectors.config import DeviceConfig
from py20305.forwarders.config import ForwarderConfig

#: Suffixes understood by :func:`load_config`.
_YAML_SUFFIXES = {".yaml", ".yml"}
_JSON_SUFFIXES = {".json"}


class ConfigError(Exception):
    """A configuration file could not be read, parsed, or validated."""


class _Strict(BaseModel):
    """Base for every configuration model: unknown keys are an error.

    Pydantic ignores unknown fields by default, which would make a misspelling
    silently do nothing -- ``retry_foreverr: false`` would validate, and the
    client would retry forever anyway. That directly contradicts the promise
    that a configuration mistake surfaces at startup, and it is the failure
    mode an operator is least able to diagnose, because everything appears to
    have been accepted.
    """

    model_config = ConfigDict(extra="forbid")


class ServerConfig(_Strict):
    """The IEEE 2030.5 server this client talks to."""

    url: str = Field(description="Base URL of the server, e.g. https://server.example.com:8443")

    dcap_path: str = Field(
        default="/dcap",
        description="Path of the DeviceCapability resource. Standard servers use /dcap.",
    )
    poll_now_on_start: bool = Field(
        default=True,
        description="Poll once immediately after connecting rather than waiting for the schedule.",
    )
    server_2018_compat: bool = Field(
        default=False,
        description="Adjust behavior for servers implementing IEEE 2030.5-2018 rather than 2023.",
    )
    use_server_time: bool = Field(
        default=True,
        description=(
            "Follow the server's Time resource for scheduling rather than the local clock. "
            "The local clock is never modified either way."
        ),
    )

    @field_validator("url")
    @classmethod
    def _must_be_a_usable_https_url(cls, v: str) -> str:
        """Reject an unusable URL at load rather than at handshake.

        IEEE 2030.5 is mutual TLS throughout, so a plain http:// URL cannot
        work. The URL is parsed rather than prefix-matched because
        ``https://`` alone passes a prefix check and then fails much later
        inside the HTTP client, outside this module's error handling and
        looking like the server is down.
        """
        parsed = urlparse(v)
        if parsed.scheme != "https":
            raise ValueError(f"must be an https:// URL (IEEE 2030.5 requires TLS), got {v!r}")
        if not parsed.hostname:
            raise ValueError(f"must include a host, got {v!r}")
        return v.rstrip("/")


class TlsFileConfig(_Strict):
    """The certificate material this client presents and trusts.

    Paths are resolved relative to the configuration file, so a deployment can
    be moved without rewriting them.
    """

    client_cert: Path = Field(description="Client certificate (PEM). Its LFDI identifies you.")
    client_key: Path = Field(description="Private key for the client certificate (PEM).")
    ca_cert: Path = Field(description="CA bundle that signed the server's certificate (PEM).")

    check_hostname: bool = Field(
        default=True,
        description=(
            "Verify the server's certificate names the host being connected to. "
            "Turn off only for a server reached by an address its certificate does not carry."
        ),
    )
    additional_ciphers: tuple[str, ...] = Field(
        default=(),
        description=(
            "Extra TLS 1.2 cipher suites, each naming exactly one suite. For peers presenting "
            "RSA certificates, which the IEEE 2030.5 ECDSA-only baseline cannot handshake with."
        ),
    )


class SubscriptionConfig(_Strict):
    """Subscribe/notify instead of relying on polling alone.

    Off by default: enabling it opens a listening socket for the server's
    notifications, which a deployment should choose rather than inherit.
    """

    enabled: bool = Field(
        default=False,
        description="Subscribe to server resources and receive notifications",
    )
    notification_host: str = Field(
        default="0.0.0.0",
        description="Address the notification listener binds",
    )
    notification_port: int = Field(
        default=10443,
        ge=1,
        le=65535,
        description="Port the notification listener binds",
    )
    notification_external_host: str | None = Field(
        default=None,
        description=(
            "Hostname or IP the server should deliver notifications to -- what goes "
            "into each subscription's notificationURI. Required when enabled: the "
            "bind address is not it, and advertising 0.0.0.0 would subscribe with a "
            "callback no server can reach."
        ),
    )
    notification_client_cert_mode: Literal["off", "warn", "enforce"] = Field(
        default="warn",
        description="How strictly the listener checks the notifying server's client certificate",
    )

    @model_validator(mode="after")
    def _external_host_when_enabled(self) -> SubscriptionConfig:
        """The advertised callback host cannot be guessed, so it must be stated."""
        if self.enabled and not self.notification_external_host:
            raise ValueError(
                "subscription.enabled requires subscription.notification_external_host -- "
                "it becomes the notificationURI the server delivers to"
            )
        return self


class TelemetryConfig(_Strict):
    """Posting measurements back to the server.

    Off by default. Turning it on makes the client read its devices on a
    schedule and mirror the readings upstream, which is a conversation with
    the utility that an operator should choose rather than inherit.
    """

    enabled: bool = Field(
        default=False,
        description="Read each configured device on a schedule and post the readings",
    )
    post_rate_seconds: int = Field(
        default=300,
        gt=0,
        description="How often each device is read and its readings posted",
    )


class LoggingConfig(_Strict):
    """How much the runner says while it works."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    file: Path | None = Field(
        default=None,
        description="Also write to this file. Logs go to stderr regardless.",
    )


class ApiConfig(_Strict):
    """The optional local management API."""

    enabled: bool = False
    host: str = Field(
        default="127.0.0.1",
        description=(
            "Bind address. Loopback by default: the API is unauthenticated, so exposing it "
            "on a routable address makes the client's controls available to that network."
        ),
    )
    port: int = Field(default=8080, ge=1, le=65535)


class ConnectionConfig(_Strict):
    """What to do when the server cannot be reached.

    A client on a gateway is expected to outlive the server's outages, so it
    retries indefinitely by default rather than exiting and relying on
    something else to restart it.
    """

    retry_forever: bool = Field(default=True, description="Keep retrying the initial connection.")
    max_attempts: int = Field(
        default=0,
        ge=0,
        description="Give up after this many attempts. 0 means never, and requires retry_forever.",
    )
    initial_delay_seconds: float = Field(default=5.0, gt=0)
    max_delay_seconds: float = Field(default=300.0, gt=0)
    backoff_factor: float = Field(default=2.0, ge=1.0)


class ClientConfig(_Strict):
    """A complete description of one running client."""

    server: ServerConfig
    tls: TlsFileConfig
    devices: list[Annotated[DeviceConfig, Field(discriminator="type")]] = Field(
        default_factory=list,
        description="The devices this client drives. An empty list monitors without dispatching.",
    )

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    connection: ConnectionConfig = Field(default_factory=ConnectionConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    subscription: SubscriptionConfig = Field(default_factory=SubscriptionConfig)
    forwarders: ForwarderConfig | None = Field(
        default=None,
        description=(
            "Publish captured traffic to a monitoring system. Omitted, the client "
            "forwards nothing and never loads the transport."
        ),
    )

    register_on_start: bool = Field(
        default=True,
        description=(
            "Register an EndDevice for this client's own certificate identity if the server "
            "does not already have one. Registering when it does would create a duplicate."
        ),
    )

    @field_validator("devices")
    @classmethod
    def _reject_duplicate_lfdis(cls, devices: list[Any]) -> list[Any]:
        """Two entries for one device is a configuration error, not a merge.

        The registry keys on LFDI, so a duplicate silently wins over the other
        and the operator sees one of their two devices never being polled.
        """
        seen: set[str] = set()
        for device in devices:
            lfdi = device.lfdi.lower()
            if lfdi in seen:
                raise ValueError(f"duplicate device lfdi {device.lfdi!r}")
            seen.add(lfdi)
        return devices

    def resolve_paths(self, base: Path) -> ClientConfig:
        """Return a copy with relative certificate paths resolved against ``base``.

        Relative to the configuration file rather than the working directory,
        so the same deployment works whichever directory it is started from --
        which is not the same directory under systemd as it is by hand.
        """
        tls = self.tls.model_copy(
            update={
                field: (base / value).resolve()
                for field in ("client_cert", "client_key", "ca_cert")
                if not (value := getattr(self.tls, field)).is_absolute()
            }
        )
        logging_config = self.logging
        if logging_config.file is not None and not logging_config.file.is_absolute():
            logging_config = logging_config.model_copy(
                update={"file": (base / logging_config.file).resolve()}
            )

        # A device speaking Modbus over TLS carries its own certificate paths,
        # and they go straight to an SSL context. Resolving only the server's
        # would make the same relative path mean different things depending on
        # which section it appeared in.
        devices = [self._resolve_device_paths(device, base) for device in self.devices]

        # The forwarder's paths are paths in the same file, read the same way,
        # so they resolve the same way: its schema directory, and the broker
        # credentials, which go straight to an SSL context exactly as a
        # device's do.
        forwarders = self._resolve_forwarder_paths(self.forwarders, base)

        return self.model_copy(
            update={
                "tls": tls,
                "logging": logging_config,
                "devices": devices,
                "forwarders": forwarders,
            }
        )

    @staticmethod
    def _resolve_forwarder_paths(forwarders: Any, base: Path) -> Any:
        """Resolve the forwarder's schema directory and broker credentials."""
        if forwarders is None:
            return None

        updates: dict[str, Any] = {}
        if forwarders.schema_dir is not None and not forwarders.schema_dir.is_absolute():
            updates["schema_dir"] = (base / forwarders.schema_dir).resolve()

        mqtt = forwarders.mqtt
        if mqtt is not None:
            mqtt_updates = {
                field: (base / value).resolve()
                for field in ("cert_path", "key_path", "ca_path")
                if (value := getattr(mqtt, field, None)) is not None and not value.is_absolute()
            }
            if mqtt_updates:
                updates["mqtt"] = mqtt.model_copy(update=mqtt_updates)

        return forwarders.model_copy(update=updates) if updates else forwarders

    @staticmethod
    def _resolve_device_paths(device: Any, base: Path) -> Any:
        """Resolve a device's own certificate paths, if it has any."""
        updates = {
            field: str((base / value).resolve())
            for field in ("ca_path", "cert_path", "key_path")
            if (value := getattr(device, field, None)) and not Path(value).is_absolute()
        }
        return device.model_copy(update=updates) if updates else device


def load_config(path: str | Path) -> ClientConfig:
    """Load, parse and validate a configuration file.

    Accepts YAML (``.yaml``/``.yml``) or JSON (``.json``). Certificate paths
    are resolved relative to the file.

    Raises:
        ConfigError: If the file is missing, unparseable, or invalid. The
            message names the file and, for a validation failure, the field --
            an operator reading a startup log should not have to map a
            pydantic traceback back onto their document.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path}")

    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc

    if suffix in _YAML_SUFFIXES:
        raw = _parse_yaml(text, path)
    elif suffix in _JSON_SUFFIXES:
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    else:
        known = ", ".join(sorted(_YAML_SUFFIXES | _JSON_SUFFIXES))
        raise ConfigError(
            f"unsupported configuration format {suffix!r} for {path} (expected {known})"
        )

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")

    try:
        config = ClientConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {path}:\n{_describe(exc)}") from exc

    return config.resolve_paths(path.parent.resolve())


def _parse_yaml(text: str, path: Path) -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ConfigError(
            f"{path} is YAML, which needs PyYAML. Install it with "
            f"`pip install py20305[cli]`, or use a .json file instead."
        ) from exc

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc


def _describe(exc: ValidationError) -> str:
    """Render a validation failure as one readable line per bad field."""
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(top level)"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)
