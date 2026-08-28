"""DNS record encoding and the multicast transports IEEE 2030.5 defines.

Shared by the two halves of this package: the advertiser, which encodes
records and answers questions about them, and the discovery side, which reads
the same records back. Keeping the wire format in one module is what stops the
two from disagreeing about it.

The transports here are the multicast ones, and IEEE 2030.5 §7.1 differs
between editions over which is normative: the 2018 edition on xmDNS
(site-local ``FF05::FB``, the ``.site`` domain) and the 2023 edition on plain
mDNS (link-local, ``.local``), where xmDNS is retained but deprecated. The
records are identical either way, so the difference is a group and a domain
rather than a record format.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_AAAA = 28
TYPE_SRV = 33
#: RFC 1035 §3.2.3. Sent by a querier that wants a name's whole record set.
TYPE_ANY = 255

CLASS_IN = 0x0001
#: Top bit of a question's class: "answer me by unicast" (RFC 6762 §5.4).
QU_BIT = 0x8000
#: Top bit of a record's class: "this is the authoritative set, flush what you
#: had" (RFC 6762 §10.2). Set on records unique to one host, never on a shared
#: PTR record several hosts may each contribute an answer to.
CACHE_FLUSH = 0x8000

#: Guards a malformed or hostile name; see :func:`read_name`.
MAX_LABELS = 128

#: Default service name for announcing this client. Deliberately not the
#: IANA-registered ``smartenergy`` of §7.3 -- this client is not an IEEE 2030.5
#: server, and claiming that name would make conformant clients try to read a
#: DeviceCapability resource it does not serve.
DEFAULT_SERVICE = "_py20305._tcp"


class DnsDecodeError(ValueError):
    """A DNS message could not be decoded.

    Raised rather than returning a partial parse: a multicast group is the one
    place on this path where bytes arrive from an unauthenticated source, and
    a half-decoded record is how a parser starts trusting them.
    """


@dataclass(frozen=True)
class MulticastTransport:
    """One way of carrying the DNS-SD exchange over multicast (§7.1).

    Attributes:
        name: The name an operator configures (``mdns`` or ``xmdns``).
        domain: The DNS domain services are named under -- ``local`` for mDNS,
            ``site`` for xmDNS.
        groups: Multicast groups to use. mDNS has an IPv4 and an IPv6 group and
            either may be the one that works on a given host; xmDNS is defined
            only over IPv6.
        hop_limit: IP TTL / IPv6 hop limit for outgoing packets. RFC 6762
            §11 requires 255 for mDNS, and not as a routing decision: the
            scope is already fixed by the group address, and the fixed value
            exists so a receiver can tell a packet that really came from the
            local link from one that did not. A responder is permitted to
            discard traffic carrying anything else.
        port: UDP port. The Extended Multicast DNS draft reuses the mDNS port
            rather than registering one of its own.
    """

    name: str
    domain: bytes
    groups: tuple[str, ...]
    hop_limit: int
    port: int = 5353


#: RFC 6762: link-local groups, ``.local``. Normative from IEEE 2030.5-2023.
MDNS = MulticastTransport(
    name="mdns",
    domain=b"local",
    groups=("224.0.0.251", "ff02::fb"),
    hop_limit=255,
)

#: The Extended Multicast DNS draft: site-local ``FF05::FB``, ``.site``.
#: Normative from IEEE 2030.5-2018, deprecated but retained in 2023. There is
#: no IPv4 form -- the draft and §7.1 both define it only over IPv6.
XMDNS = MulticastTransport(
    name="xmdns",
    domain=b"site",
    groups=("ff05::fb",),
    hop_limit=255,
)

TRANSPORTS: Mapping[str, MulticastTransport] = {"mdns": MDNS, "xmdns": XMDNS}


def transports_for(selection: str) -> tuple[MulticastTransport, ...]:
    """Map a configured transport selection onto the transports to use.

    ``both`` lists mDNS first, which is the transport the current edition of
    the standard makes normative.
    """
    if selection == "off":
        return ()
    if selection == "both":
        return (MDNS, XMDNS)
    return (TRANSPORTS[selection],)


@dataclass(frozen=True)
class Record:
    """One record to publish.

    Attributes:
        name: The owner name, as labels.
        rtype: Record type.
        ttl: Seconds a receiver may cache it. Zero is a goodbye (§10.1).
        rdata: The already-encoded record data.
        unique: Whether this host is the only source of the name, which sets
            the cache-flush bit (§10.2). True for SRV, TXT and addresses;
            false for the shared service PTR, where several hosts each
            contribute one answer and flushing would erase the others.
    """

    name: tuple[bytes, ...]
    rtype: int
    ttl: int
    rdata: bytes
    unique: bool

    def with_ttl(self, ttl: int) -> Record:
        """A copy with a different TTL, for building the goodbye set."""
        return Record(
            name=self.name, rtype=self.rtype, ttl=ttl, rdata=self.rdata, unique=self.unique
        )


@dataclass(frozen=True)
class Question:
    """One question from a received query."""

    name: tuple[bytes, ...]
    qtype: int
    unicast: bool


def encode_name(labels: Sequence[bytes]) -> bytes:
    """Encode labels as an uncompressed DNS name.

    Uncompressed on purpose. Compression would save a few dozen bytes in a
    datagram with thousands to spare, and every pointer is an offset that has
    to stay correct as records are added or reordered.
    """
    out = bytearray()
    for label in labels:
        if not 0 < len(label) <= 63:
            raise ValueError(f"a DNS label must be 1-63 bytes, got {len(label)}")
        out.append(len(label))
        out += label
    out.append(0)
    if len(out) > 255:
        raise ValueError(f"a DNS name must be at most 255 bytes, got {len(out)}")
    return bytes(out)


def encode_txt(values: Mapping[str, str | None]) -> bytes:
    """Encode TXT key/value pairs (RFC 6763 §6).

    A key mapped to None is written bare, with no ``=``. That distinction is
    not decorative: IEEE 2030.5 §7.4 gives "absent", "present with no value"
    and "present with an empty value" three different meanings for its
    ``https`` key, and a writer unable to express the middle one could not
    produce a conformant record.
    """
    out = bytearray()
    for key, value in values.items():
        item = key.encode("utf-8") if value is None else f"{key}={value}".encode()
        if len(item) > 255:
            raise ValueError(f"TXT entry for {key!r} is longer than 255 bytes")
        out.append(len(item))
        out += item
    if not out:
        # RFC 6763 §6.1: a service with no attributes still carries a TXT
        # record, holding a single empty string. Empty rdata is invalid.
        return b"\x00"
    return bytes(out)


def encode_srv(port: int, target: Sequence[bytes]) -> bytes:
    """Encode SRV rdata with the priority and weight DNS-SD uses for one host."""
    return (
        (0).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
        + port.to_bytes(2, "big")
        + encode_name(target)
    )


def encode_address(address: str) -> bytes:
    """Encode an address for an A or AAAA record."""
    return ipaddress.ip_address(address).packed


#: RFC 6762 §6.7 caps the TTL of a legacy unicast response at ten seconds. That
#: querier will never receive the goodbye that would otherwise correct it, so
#: its copy is deliberately short-lived.
LEGACY_MAX_TTL = 10


def encode_response(
    records: Sequence[Record],
    *,
    ident: int = 0,
    questions: Sequence[Question] = (),
) -> bytes:
    """Encode an authoritative response carrying these records.

    With no ``ident`` and no ``questions`` this is the multicast form: RFC 6762
    §6 has a multicast response carry the answers alone under transaction id
    zero (§18.1), which is what an announcement (§8.3) and a goodbye (§10.1)
    are.

    Passing both produces the legacy unicast form of §6.7, for a querier whose
    source port was not 5353. That querier is an ordinary DNS resolver as far as
    it knows: it matches the reply to its request by transaction id and expects
    its question echoed. Answering it with id zero and no question leaves the
    reply unmatchable, including by this package's own discovery side, which
    drops replies carrying an id it did not send.
    """
    legacy = bool(questions)
    header = b"".join(
        (
            ident.to_bytes(2, "big"),
            (0x8400).to_bytes(2, "big"),  # QR=1, AA=1
            len(questions).to_bytes(2, "big"),
            len(records).to_bytes(2, "big"),
            (0).to_bytes(2, "big") * 2,  # no authority or additional
        )
    )
    body = bytearray()
    for question in questions:
        body += encode_name(question.name)
        body += question.qtype.to_bytes(2, "big")
        # Echoed without the QU bit, which is a property of the request.
        body += CLASS_IN.to_bytes(2, "big")
    for record in records:
        # The cache-flush bit means nothing to a legacy querier, which is not
        # running an mDNS cache to flush.
        rclass = CLASS_IN if legacy else CLASS_IN | (CACHE_FLUSH if record.unique else 0)
        body += encode_name(record.name)
        body += record.rtype.to_bytes(2, "big")
        body += rclass.to_bytes(2, "big")
        body += (min(record.ttl, LEGACY_MAX_TTL) if legacy else record.ttl).to_bytes(4, "big")
        body += len(record.rdata).to_bytes(2, "big")
        body += record.rdata
    return header + bytes(body)


def read_name(data: bytes, offset: int) -> tuple[tuple[bytes, ...], int]:
    """Read a (possibly compressed) name, returning its labels and the offset past it.

    Compression pointers may only point strictly backwards. That single rule is
    what makes a pointer loop impossible rather than merely unlikely: a forward
    or self-referential pointer is the shape every "malformed DNS packet hangs
    the parser" bug takes, and bounding the iteration count instead would still
    let a crafted datagram cost far more work than it took to send.
    """
    labels: list[bytes] = []
    end_offset = offset
    jumped = False

    while True:
        if offset >= len(data):
            raise DnsDecodeError("name runs past the end of the message")
        length = data[offset]

        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise DnsDecodeError("truncated compression pointer")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                end_offset = offset + 2
                jumped = True
            if pointer >= offset:
                raise DnsDecodeError("compression pointer does not point backwards")
            offset = pointer
            continue

        if length & 0xC0:
            raise DnsDecodeError(f"reserved label type {length:#04x}")

        offset += 1
        if length == 0:
            if not jumped:
                end_offset = offset
            return tuple(labels), end_offset

        label = data[offset : offset + length]
        if len(label) != length:
            raise DnsDecodeError("label runs past the end of the message")
        labels.append(label)
        offset += length
        if len(labels) > MAX_LABELS:
            raise DnsDecodeError("name has too many labels")


def decode_questions(data: bytes) -> list[Question]:
    """Read the questions from an incoming message.

    Only the questions. A responder has no use for the answers a querier
    includes for known-answer suppression beyond skipping them, and parsing
    records this process will not act on is parsing done on an attacker's
    behalf.

    Returns an empty list for a message that is a response rather than a
    query, so a responder does not answer its own announcements.
    """
    if len(data) < 12:
        raise DnsDecodeError(f"message shorter than a DNS header ({len(data)} bytes)")

    flags = int.from_bytes(data[2:4], "big")
    if flags & 0x8000:  # QR bit: this is a response
        return []

    count = int.from_bytes(data[4:6], "big")
    questions: list[Question] = []
    offset = 12
    for _ in range(count):
        name, offset = read_name(data, offset)
        if offset + 4 > len(data):
            raise DnsDecodeError("question runs past the end of the message")
        qtype = int.from_bytes(data[offset : offset + 2], "big")
        qclass = int.from_bytes(data[offset + 2 : offset + 4], "big")
        offset += 4
        questions.append(Question(name=name, qtype=qtype, unicast=bool(qclass & QU_BIT)))
    return questions


def decode_txt(rdata: bytes) -> dict[str, str | None]:
    """Decode TXT rdata into its key/value pairs.

    Three states of a key matter here. RFC 6763 §6.4 distinguishes a key that
    is absent, one present with no value, and one present with an empty value,
    and IEEE 2030.5 §7.4 leans on exactly that distinction for the ``https``
    key: absent means plain HTTP, present-with-no-value means HTTPS on 443. A
    decoder collapsing the two would silently downgrade a TLS-only server, so a
    valueless key maps to None and ``key=`` maps to "".

    Duplicate keys keep the first occurrence, per RFC 6763 §6.4.
    """
    out: dict[str, str | None] = {}
    offset = 0
    while offset < len(rdata):
        length = rdata[offset]
        offset += 1
        if offset + length > len(rdata):
            raise DnsDecodeError("TXT string runs past the end of the record")
        item = rdata[offset : offset + length]
        offset += length
        if not item:
            continue  # An empty string is the "no attributes" encoding.

        key, separator, value = item.partition(b"=")
        try:
            name = key.decode("utf-8").lower()
        except UnicodeDecodeError:
            continue
        if name in out:
            continue
        out[name] = value.decode("utf-8", "replace") if separator else None
    return out


def format_name(labels: Sequence[bytes]) -> str:
    """Render a name for a human, escaping the dots a label may contain."""
    return ".".join(
        label.decode("utf-8", "replace").replace("\\", "\\\\").replace(".", "\\.")
        for label in labels
    )


def validate_service(service: str) -> tuple[bytes, bytes]:
    """Split a ``_name._proto`` service into its two labels.

    Rejecting the malformed forms here means a failure names the setting the
    operator got wrong, rather than surfacing later as a name nothing answers.
    """
    parts = service.split(".")
    if len(parts) != 2:
        raise ValueError(f"service {service!r} must have the form _name._tcp (two labels)")
    name, proto = parts
    if proto not in ("_tcp", "_udp"):
        raise ValueError(f"service {service!r} must end in _tcp or _udp, got {proto!r}")
    for label in (name, proto):
        if not label.startswith("_") or len(label) < 2:
            raise ValueError(f"service label {label!r} must begin with an underscore")
        if len(label.encode("utf-8")) > 63:
            raise ValueError(f"service label {label!r} is longer than one DNS label allows")
    return name.encode("utf-8"), proto.encode("utf-8")


def validate_subtype(subtype: str) -> str:
    """Check a subtype string against §7.5, returning it unchanged.

    §7.5 says subtype strings "SHALL NOT begin with an underscore", and one has
    to survive as a single DNS label. Membership of Table 17 is deliberately
    not enforced: a server may advertise a subtype the table does not list, and
    ``edev-<SFDI>`` -- the per-device form §7.5 defines for finding the server
    that holds your own EndDevice -- is not in the table either.
    """
    if not subtype:
        raise ValueError("a discovery subtype must not be empty")
    if subtype.startswith("_"):
        raise ValueError(
            f"discovery subtype {subtype!r} must not begin with an underscore (IEEE 2030.5 §7.5)"
        )
    if "." in subtype or "\x00" in subtype:
        raise ValueError(f"discovery subtype {subtype!r} must be a single DNS label")
    if len(subtype.encode("utf-8")) > 63:
        raise ValueError(f"discovery subtype {subtype!r} is longer than one DNS label allows")
    return subtype


def fold(name: Sequence[bytes]) -> tuple[bytes, ...]:
    """Case-fold a name for comparison. DNS labels are case-insensitive."""
    return tuple(label.lower() for label in name)


# --------------------------------------------------------------------------
# Reading a response
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceRecord:
    """One record from a response, with its rdata still packed.

    rdata is decoded on demand rather than at parse time because decoding a
    PTR or SRV target needs the whole message for compression pointers, and
    because a record of a type the caller does not use should cost nothing.
    """

    name: tuple[bytes, ...]
    rtype: int
    rclass: int
    ttl: int
    rdata_offset: int
    rdata_length: int


@dataclass(frozen=True)
class DnsMessage:
    """A decoded response, keeping the bytes its records point into."""

    ident: int
    records: tuple[ResourceRecord, ...]
    raw: bytes


def encode_query(labels: Sequence[bytes], qtype: int, ident: int, *, unicast: bool) -> bytes:
    """Encode a query message carrying one question."""
    qclass = CLASS_IN | (QU_BIT if unicast else 0)
    header = b"".join(
        (
            ident.to_bytes(2, "big"),
            (0).to_bytes(2, "big"),  # standard query, recursion not desired
            (1).to_bytes(2, "big"),  # one question
            (0).to_bytes(2, "big") * 3,  # no answer, authority or additional
        )
    )
    return header + encode_name(labels) + qtype.to_bytes(2, "big") + qclass.to_bytes(2, "big")


def decode_message(data: bytes) -> DnsMessage:
    """Decode a response into its records.

    Questions are skipped: a legacy unicast response repeats the question
    (RFC 6762 §6.7) and there is nothing in it the caller does not already
    know. Answer, authority and additional sections are read into one list,
    because mDNS routinely puts the SRV, TXT and address records completing a
    PTR answer in the additional section, and treating the sections
    differently would mean ignoring most of what a responder actually sent.
    """
    if len(data) < 12:
        raise DnsDecodeError(f"message shorter than a DNS header ({len(data)} bytes)")

    ident = int.from_bytes(data[0:2], "big")
    counts = [int.from_bytes(data[offset : offset + 2], "big") for offset in (4, 6, 8, 10)]
    question_count, record_count = counts[0], sum(counts[1:])

    offset = 12
    for _ in range(question_count):
        _, offset = read_name(data, offset)
        offset += 4  # QTYPE + QCLASS
        if offset > len(data):
            raise DnsDecodeError("question section runs past the end of the message")

    records: list[ResourceRecord] = []
    for _ in range(record_count):
        name, offset = read_name(data, offset)
        if offset + 10 > len(data):
            raise DnsDecodeError("record header runs past the end of the message")
        rtype = int.from_bytes(data[offset : offset + 2], "big")
        rclass = int.from_bytes(data[offset + 2 : offset + 4], "big")
        ttl = int.from_bytes(data[offset + 4 : offset + 8], "big")
        rdlength = int.from_bytes(data[offset + 8 : offset + 10], "big")
        offset += 10
        if offset + rdlength > len(data):
            raise DnsDecodeError("record data runs past the end of the message")
        records.append(
            ResourceRecord(
                name=name,
                rtype=rtype,
                # The cache-flush bit (RFC 6762 §10.2) rides the top of the
                # class field. Masked off so a record carrying it still reads
                # as class IN rather than being silently discarded.
                rclass=rclass & 0x7FFF,
                ttl=ttl,
                rdata_offset=offset,
                rdata_length=rdlength,
            )
        )
        offset += rdlength

    return DnsMessage(ident=ident, records=tuple(records), raw=data)


def record_rdata(message: DnsMessage, record: ResourceRecord) -> bytes:
    """The raw rdata of a record."""
    return message.raw[record.rdata_offset : record.rdata_offset + record.rdata_length]


def _read_rdata_name(
    message: DnsMessage, record: ResourceRecord, offset: int
) -> tuple[bytes, ...]:
    """Read a name from inside a record, refusing to read past its end.

    ``read_name`` works against the whole message, because a compression
    pointer legitimately reaches backwards outside the record. What must not
    happen is the uncompressed part of a name running past this record's
    rdata: a short RDLENGTH would let the name consume the record that follows
    it, and the result would be accepted as a valid target. Checking the end
    offset ``read_name`` already computes is what bounds it.
    """
    name, end = read_name(message.raw, offset)
    if end > record.rdata_offset + record.rdata_length:
        raise DnsDecodeError("a name runs past the end of the record that holds it")
    return name


def decode_ptr(message: DnsMessage, record: ResourceRecord) -> tuple[bytes, ...]:
    """Decode a PTR record into the name it points at."""
    return _read_rdata_name(message, record, record.rdata_offset)


def decode_srv(message: DnsMessage, record: ResourceRecord) -> tuple[int, tuple[bytes, ...]]:
    """Decode an SRV record into its port and target name."""
    if record.rdata_length < 7:
        raise DnsDecodeError("SRV record is too short")
    port = int.from_bytes(
        message.raw[record.rdata_offset + 4 : record.rdata_offset + 6], "big"
    )
    return port, _read_rdata_name(message, record, record.rdata_offset + 6)


def decode_address(message: DnsMessage, record: ResourceRecord) -> str:
    """Decode an A or AAAA record into a printable address."""
    raw = record_rdata(message, record)
    expected = 4 if record.rtype == TYPE_A else 16
    if len(raw) != expected:
        raise DnsDecodeError(f"address record has {len(raw)} bytes, expected {expected}")
    return str(ipaddress.ip_address(raw))
