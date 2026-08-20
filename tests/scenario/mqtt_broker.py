"""A lightweight MQTT 3.1.1 broker, for wire tests.

Speaks exactly the subset a publishing client needs -- CONNECT/CONNACK,
PUBLISH at QoS 0 and 1 (with PUBACK), PINGREQ/PINGRESP and DISCONNECT -- and
records every PUBLISH it receives, so a test can assert on what actually
crossed the socket rather than on what a forwarder believed it sent.

A test double, not a broker: it has no subscriptions, no retained messages,
no sessions. What it offers over a mock is the real protocol boundary -- a
client that framed its packet wrong, or a forwarder that never connected,
fails here the same way it would against production infrastructure.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PublishedMessage:
    """One PUBLISH as it arrived at the broker."""

    topic: str
    payload: bytes
    qos: int


@dataclass
class _Session:
    client_id: str = ""
    writer: asyncio.StreamWriter | None = None
    packets: list[int] = field(default_factory=list)


def _decode_remaining_length(data: bytes, offset: int) -> tuple[int, int]:
    """Decode MQTT's variable-length int, returning (value, bytes consumed)."""
    multiplier = 1
    value = 0
    consumed = 0
    while True:
        byte = data[offset + consumed]
        consumed += 1
        value += (byte & 0x7F) * multiplier
        if not byte & 0x80:
            return value, consumed
        multiplier *= 128
        if multiplier > 128**3:
            raise ValueError("malformed remaining length")


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read one MQTT control packet, returning (first byte, variable payload)."""
    first = await reader.readexactly(1)
    # Remaining Length: up to four continuation bytes.
    length_bytes = b""
    while True:
        b = await reader.readexactly(1)
        length_bytes += b
        if not b[0] & 0x80:
            break
        if len(length_bytes) > 4:
            raise ValueError("malformed remaining length")
    remaining, _ = _decode_remaining_length(length_bytes, 0)
    body = await reader.readexactly(remaining) if remaining else b""
    return first[0], body


class ScenarioMqttBroker:
    """CONNECT, PUBLISH (QoS 0/1), PING, DISCONNECT -- and a message log."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.published: list[PublishedMessage] = []
        self.connects: list[str] = []  # client ids, in connection order
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._refuse = False

    def messages_on(self, topic: str) -> list[PublishedMessage]:
        """Every message published to exactly ``topic``."""
        return [m for m in self.published if m.topic == topic]

    def refuse_connections(self, refuse: bool = True) -> None:
        """Make the broker close each new connection before CONNACK."""
        self._refuse = refuse

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._session, "127.0.0.1", self.port)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            # wait_closed waits for live sessions too, and an MQTT client
            # holds its socket open between packets -- close them, or the
            # teardown waits on a connection nobody will ever finish.
            for writer in list(self._writers):
                writer.close()
            await self._server.wait_closed()

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            if self._refuse:
                return
            while True:
                first, body = await _read_packet(reader)
                packet_type = first >> 4
                if packet_type == 1:  # CONNECT
                    self._handle_connect(body)
                    writer.write(bytes([0x20, 0x02, 0x00, 0x00]))  # CONNACK, accepted
                elif packet_type == 3:  # PUBLISH
                    self._handle_publish(first, body, writer)
                elif packet_type == 12:  # PINGREQ
                    writer.write(bytes([0xD0, 0x00]))  # PINGRESP
                elif packet_type == 14:  # DISCONNECT
                    return
                else:
                    # SUBSCRIBE and friends are outside this double's remit; a
                    # client sending one is a test asking for a real broker.
                    raise AssertionError(f"unsupported MQTT packet type {packet_type}")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def _handle_connect(self, body: bytes) -> None:
        # Variable header: protocol name, level, flags, keepalive -- then the
        # client id is the first field of the payload.
        name_len = struct.unpack(">H", body[0:2])[0]
        offset = 2 + name_len + 1 + 1 + 2  # name, level, connect flags, keepalive
        cid_len = struct.unpack(">H", body[offset : offset + 2])[0]
        client_id = body[offset + 2 : offset + 2 + cid_len].decode()
        self.connects.append(client_id)

    def _handle_publish(self, first: int, body: bytes, writer: asyncio.StreamWriter) -> None:
        qos = (first >> 1) & 0x03
        topic_len = struct.unpack(">H", body[0:2])[0]
        topic = body[2 : 2 + topic_len].decode()
        offset = 2 + topic_len
        if qos > 0:
            packet_id = struct.unpack(">H", body[offset : offset + 2])[0]
            offset += 2
            writer.write(struct.pack(">BBH", 0x40, 0x02, packet_id))  # PUBACK
        self.published.append(PublishedMessage(topic=topic, payload=body[offset:], qos=qos))
