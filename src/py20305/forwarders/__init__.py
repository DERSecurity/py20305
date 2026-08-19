"""Message forwarding for IEEE 2030.5 protocol auditing.

This module provides infrastructure for capturing HTTP exchanges between
this client and IEEE 2030.5 servers, then forwarding them to
external systems (e.g., AWS IoT Core for security monitoring).

Key components:
- MessageFrame: Internal format for captured HTTP exchanges
- ProtocolMessage: the wire format described in `types.py` for external forwarding
- MQTTForwarder: Async MQTT publisher for AWS IoT Core
- MQTTForwarderAdapter: Converts MessageFrame to ProtocolMessage
- ForwarderManager: Routes messages to multiple forwarders
"""

from py20305.forwarders.base import (
    AbstractForwarder,
    BaseForwarder,
    EventFrame,
    MessageDirection,
    MessageFrame,
)
from py20305.forwarders.config import (
    DeviceTelemetryConfig,
    ForwarderConfig,
    MQTTForwarderConfig,
)
from py20305.forwarders.lfdi_extraction import extract_client_id, extract_lfdi
from py20305.forwarders.manager import ForwarderManager
from py20305.forwarders.mqtt_adapter import MQTTForwarderAdapter
from py20305.forwarders.mqtt_forwarder import (
    PROTOCOL_MESSAGE_TOPIC_SUFFIX,
    MQTTForwarder,
)
from py20305.forwarders.types import (
    NetworkEndpoint,
    PayloadEnvelope,
    Protocol,
    ProtocolMessage,
    ProtocolMetadata,
    WireDirection,
)

__all__ = [
    "PROTOCOL_MESSAGE_TOPIC_SUFFIX",
    "AbstractForwarder",
    "BaseForwarder",
    "DeviceTelemetryConfig",
    "EventFrame",
    "ForwarderConfig",
    "ForwarderManager",
    "MQTTForwarder",
    "MQTTForwarderAdapter",
    "MQTTForwarderConfig",
    "MessageDirection",
    "MessageFrame",
    "NetworkEndpoint",
    "PayloadEnvelope",
    "Protocol",
    "ProtocolMessage",
    "ProtocolMetadata",
    "WireDirection",
    "extract_client_id",
    "extract_lfdi",
]
