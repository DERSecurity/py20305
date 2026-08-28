"""Locating IEEE 2030.5 servers by DNS-SD query, per Clause 7.

§6.9.2 puts this on the client: "Clients SHALL locate local services by
performing DNS service discovery (DNS-SD) queries to the local network", and
§8.3.3 says which transports carry it: "IEEE 2030.5 clients locate local
services by performing DNS service discovery (DNS-SD) queries using mDNS
(mDNS) and xmDNS (xmDNS)."

The walk is four records deep. A PTR query on ``_smartenergy._tcp.<domain>``
returns Service Instance Names; the SRV and TXT records under an instance give
the host, the port and the path; an A or AAAA record under the SRV target gives
the address. The URL is then assembled from three of those, and which piece
comes from where is the part §7.4 and §7.5 make easy to get wrong:

* The scheme and port come from the TXT ``https`` key, **not** from the SRV
  record. §7.5 fixes the SRV port as the one "specified for the default (http)
  scheme" and requires every SRV record on a device to be identical, so an
  implementation that connects to ``srv.port`` over TLS is using a number the
  standard guarantees is the wrong one.
* The ``https`` key has three states (§7.4): absent means plain HTTP, present
  with no value means HTTPS on 443, present with a value means that port.
* The path comes from ``dcap``, or from ``path`` when a subtype query already
  narrowed the answer to one function set (§7.6 b) 5).

Queries are one-shot and sent from an ephemeral port. RFC 6762 §6.7 has a
responder treat a query whose source port is not 5353 as a legacy query and
answer it by unicast, which is what lets this run without binding the
well-known port and joining the group. A client library that did bind 5353
would fight the host's own responder for it, and lose on most systems. The QU
bit is set on every question, both because that is what a legacy querier does
and because §7.1 requires unicast responses for xmDNS requests whose answer is
specific to the requesting device.
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
import select
import socket
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from py20305.client.dnssd.wire import (
    TYPE_A,
    TYPE_AAAA,
    TYPE_ANY,
    TYPE_PTR,
    TYPE_SRV,
    TYPE_TXT,
    DnsDecodeError,
    DnsMessage,
    MulticastTransport,
    ResourceRecord,
    decode_address,
    decode_message,
    decode_ptr,
    decode_srv,
    decode_txt,
    encode_query,
    fold,
    format_name,
    record_rdata,
    validate_subtype,
)

logger = logging.getLogger(__name__)

#: The IANA-registered service name for IEEE 2030.5 (§7.3), with the transport
#: label the standard fixes alongside it.
SERVICE_LABELS: tuple[bytes, ...] = (b"_smartenergy", b"_tcp")

#: The subtype strings of §7.5 Table 17, identical in the 2018 and 2023
#: editions. Used only to tell an operator their subtype is not one the
#: standard defines; an unknown subtype is still queried for, because a server
#: may advertise one the table does not carry and because the ``edev-<SFDI>``
#: form §7.5 defines is not in it either.
KNOWN_SUBTYPES: frozenset[str] = frozenset(
    {
        "bill",
        "derp",
        "dr",
        "edev",
        "file",
        "msg",
        "mup",
        "ppy",
        "rsps",
        "sdev",
        "tm",
        "tp",
        "upt",
    }
)

#: Long enough for any response a responder will send over UDP.
_MAX_DATAGRAM = 9000

#: Distinguishes "the https key is present but unusable" from "it is absent",
#: which §7.4 gives different meanings and which deserve different messages.
_INVALID_PORT = -1


@dataclass(frozen=True)
class DiscoveredServer:
    """One IEEE 2030.5 server found by query.

    Attributes:
        instance: The Service Instance Name, for logging and for telling two
            answers apart.
        host: Where to connect. A literal address wherever the responder gave
            one, because a ``.local`` name only resolves on a host running its
            own mDNS resolver, which a container generally is not.
        port: The TLS port, from the TXT ``https`` key.
        txt: The decoded TXT record. Keys are lowercased; a key present with no
            value maps to None, which §7.4 distinguishes from absent.
        transport: Which transport found it.
    """

    instance: str
    host: str
    port: int
    txt: Mapping[str, str | None]
    transport: str

    @property
    def dcap_path(self) -> str:
        """Path of the DeviceCapability resource, from the TXT ``dcap`` key."""
        # Guaranteed present and non-empty by server_from_records, which
        # discards the record otherwise; the fallback keeps this total.
        return self.txt.get("dcap") or "/dcap"

    @property
    def function_set_path(self) -> str | None:
        """Base path of the function set a subtype query asked about (§7.4).

        Present only in a subtype query response, where §7.6 b) 5) lets a
        client use it directly instead of reading DeviceCapability first.
        """
        return self.txt.get("path") or None

    @property
    def level(self) -> str | None:
        """The schema extensibility level the server advertises, e.g. ``-S1``.

        §5.7 ties the letter to an edition: ``S1`` is IEEE 2030.5-2018 and
        ``S2`` is IEEE 2030.5-2023. That makes this the server's own statement
        about which edition it implements, which is a better answer than
        asking the operator to know.
        """
        return self.txt.get("level")

    @property
    def is_2018(self) -> bool:
        """Whether the advertised level names the 2018 schema (§5.7)."""
        level = self.level
        if level is None:
            return False
        return level.lstrip("+-").upper().startswith("S1")

    @property
    def base_url(self) -> str:
        """The server's base URL, with an IPv6 literal bracketed."""
        host = self.host
        try:
            if isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address):
                host = f"[{host}]"
        except ValueError:
            pass  # A name, not a literal; nothing to bracket.
        return f"https://{host}:{self.port}"

    @property
    def dcap_url(self) -> str:
        """The full DeviceCapability URL this client would connect to."""
        return f"{self.base_url}{self.dcap_path}"


def service_labels(transport: MulticastTransport, subtype: str | None = None) -> tuple[bytes, ...]:
    """Build the name to send a PTR query for.

    Plain: ``_smartenergy._tcp.<domain>``. With a subtype (§7.5):
    ``<subtype>._sub._smartenergy._tcp.<domain>`` -- which is how the standard
    says to ask for one function set rather than enumerating every server,
    something §7.5 calls "STRONGLY DISCOURAGED except for diagnostic purposes".
    """
    if subtype is None:
        return (*SERVICE_LABELS, transport.domain)
    return (subtype.encode("utf-8"), b"_sub", *SERVICE_LABELS, transport.domain)


def edev_subtype(sfdi: int) -> str:
    """The §7.5 subtype that finds the server holding this device's EndDevice.

    "the subtype name SHALL consist of the string edev, concatenated with a
    hyphen and the remote device's SFDI". The SFDI here is the *client's* own,
    which is the query Annex C Table C.1 step 3 has a device make first, before
    falling back to asking for any server at all.
    """
    digits = str(sfdi)
    if len(digits) > 12:
        raise ValueError(f"SFDI {sfdi} does not fit in 12 digits")
    return f"edev-{digits.zfill(12)}"


def server_from_records(
    message: DnsMessage,
    instance: tuple[bytes, ...],
    transport: MulticastTransport,
    *,
    fallback_host: str | None = None,
) -> DiscoveredServer | None:
    """Assemble one server from the SRV/TXT pair naming ``instance``.

    Returns None, having logged why, wherever §7.4 tells a client to discard
    the record or wherever the advertisement describes something this client
    cannot use. Every rule applied here is from §7.4 and holds in both the
    2018 and 2023 editions, whose TXT sections are identical down to
    ``txtvers=1``.
    """
    srv = _first(message, instance, TYPE_SRV)
    txt_record = _first(message, instance, TYPE_TXT)
    name = format_name(instance)

    if srv is None or txt_record is None:
        logger.debug("%s: no SRV/TXT pair in the response", name)
        return None

    try:
        _srv_port, target = decode_srv(message, srv)
        txt = decode_txt(record_rdata(message, txt_record))
    except DnsDecodeError as exc:
        logger.warning("%s: discarding a malformed record (%s)", name, exc)
        return None

    # §7.4: "txtvers SHALL be the first key ... If it is found in a response to
    # be other than 1, the TXT record SHALL be ignored." Both editions say 1.
    if txt.get("txtvers") != "1":
        logger.debug("%s: ignoring, txtvers=%r is not 1", name, txt.get("txtvers"))
        return None

    # §7.4: dcap and level SHALL each be present with a non-empty value, and a
    # client SHALL silently discard a record where either is not.
    for key in ("dcap", "level"):
        if not txt.get(key):
            logger.debug("%s: ignoring, TXT key %r is absent or empty", name, key)
            return None

    port = _https_port(txt)
    if port is None:
        # §7.4 has a client use plain HTTP when the https key is absent. This
        # client cannot: IEEE 2030.5 is mutual TLS throughout and the
        # configuration model rejects an http:// URL, so an advertisement
        # without the key describes a server it has no way to talk to.
        logger.warning(
            "%s: advertises no https TXT key, so it offers only plain HTTP; skipping it, "
            "because IEEE 2030.5 requires TLS and this client cannot use a plaintext server",
            name,
        )
        return None
    if port == _INVALID_PORT:
        logger.warning("%s: ignoring, the https TXT key is not a port number", name)
        return None

    host = _address_for(message, target) or _usable_host(fallback_host)
    if host is None:
        logger.debug(
            "%s: no usable address for %s and no usable source address",
            name,
            format_name(target),
        )
        return None

    return DiscoveredServer(
        instance=name, host=host, port=port, txt=txt, transport=transport.name
    )


def _https_port(txt: Mapping[str, str | None]) -> int | None:
    """Resolve the TLS port from the TXT record (§7.4).

    Three states, and the SRV port is not one of them. Returns None when the
    key is absent (the server offers plain HTTP) and :data:`_INVALID_PORT`
    when it is present but is not a port.
    """
    if "https" not in txt:
        return None
    value = txt["https"]
    if not value:
        # Present with no value, or present and empty: HTTPS on the default port.
        return 443
    try:
        port = int(value)
    except ValueError:
        return _INVALID_PORT
    return port if 0 < port <= 65535 else _INVALID_PORT


def _first(message: DnsMessage, name: tuple[bytes, ...], rtype: int) -> ResourceRecord | None:
    """First record of a type with this name, comparing labels case-insensitively."""
    folded = fold(name)
    for record in message.records:
        if record.rtype == rtype and fold(record.name) == folded:
            return record
    return None


def _address_for(message: DnsMessage, target: tuple[bytes, ...]) -> str | None:
    """The first usable address for an SRV target, IPv6 preferred over IPv4.

    Link-local IPv6 is skipped: without the scope identifier -- which the
    record does not carry -- it cannot be connected to, and preferring it
    would turn a successful discovery into an unreachable address.
    """
    folded = fold(target)
    found: list[str] = []
    for record in message.records:
        if record.rtype not in (TYPE_A, TYPE_AAAA) or fold(record.name) != folded:
            continue
        try:
            address = decode_address(message, record)
        except DnsDecodeError as exc:
            logger.debug("skipping a malformed address record: %s", exc)
            continue
        if ipaddress.ip_address(address).is_link_local:
            continue
        found.append(address)

    found.sort(key=lambda address: 0 if ipaddress.ip_address(address).version == 6 else 1)
    return found[0] if found else None


def _usable_host(address: str | None) -> str | None:
    """The responder's own address, unless it is one we could not connect to.

    A responder on IPv6 commonly answers from a link-local address, and the
    scope identifier that would make it dialable is stripped from the source
    tuple by the time it reaches here. Accepting it would build a URL like
    ``https://[fe80::1]:8443`` that fails at connect -- which is the same
    reason :func:`_address_for` rejects a link-local AAAA record, so applying
    it to the fallback keeps the two consistent.
    """
    if address is None:
        return None
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return address  # A name rather than a literal; not ours to judge.
    return None if parsed.is_link_local or parsed.is_unspecified else address


class PacketSource(Protocol):
    """Sends queries and collects the replies.

    A seam, so the logic above can be tested against captured packets. A test
    that had to bring multicast up would be testing the host's network stack,
    and would be skipped on exactly the CI runners meant to run it.
    """

    def exchange(
        self,
        transport: MulticastTransport,
        queries: Sequence[bytes],
        timeout: float,
        interface: str | None,
    ) -> list[tuple[bytes, str]]:
        """Send each query to the transport's groups; return (payload, source address)."""


class SocketPacketSource:
    """The real network: one UDP socket per address family, no group joined."""

    def exchange(
        self,
        transport: MulticastTransport,
        queries: Sequence[bytes],
        timeout: float,
        interface: str | None,
    ) -> list[tuple[bytes, str]]:
        sockets: dict[socket.socket, str] = {}
        for group in transport.groups:
            sock = self._open(group, transport, interface)
            if sock is not None:
                sockets[sock] = group

        if not sockets:
            logger.warning(
                "%s discovery could not open a socket for any of its groups (%s)",
                transport.name,
                ", ".join(transport.groups),
            )
            return []

        try:
            sent = 0
            for sock, group in sockets.items():
                for query in queries:
                    try:
                        sock.sendto(query, (group, transport.port))
                        sent += 1
                    except OSError as exc:
                        # One group being unroutable is ordinary: a host with no
                        # IPv6 route cannot reach ff02::fb, and mDNS still works
                        # over IPv4. That is not the query failing.
                        logger.debug("could not send to %s: %s", group, exc)
            if not sent:
                logger.warning(
                    "%s discovery could not reach any of its groups (%s); "
                    "the host may have no route for them",
                    transport.name,
                    ", ".join(transport.groups),
                )
                return []
            return self._collect(list(sockets), timeout)
        finally:
            for sock in sockets:
                sock.close()

    @staticmethod
    def _open(
        group: str, transport: MulticastTransport, interface: str | None
    ) -> socket.socket | None:
        """Open a socket able to send to one group, or None if the host cannot."""
        version = ipaddress.ip_address(group).version
        family = socket.AF_INET6 if version == 6 else socket.AF_INET
        try:
            sock = socket.socket(family, socket.SOCK_DGRAM)
        except OSError as exc:
            logger.debug("no socket available for %s: %s", group, exc)
            return None

        try:
            # An ephemeral port on purpose, not 5353. See the module docstring:
            # this makes the query a legacy one-shot query that responders
            # answer by unicast, and leaves the host's own mDNS responder in
            # possession of the well-known port.
            sock.bind(("::" if version == 6 else "0.0.0.0", 0))
            if version == 6:
                sock.setsockopt(
                    socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, transport.hop_limit
                )
                if interface is not None:
                    sock.setsockopt(
                        socket.IPPROTO_IPV6,
                        socket.IPV6_MULTICAST_IF,
                        socket.if_nametoindex(interface),
                    )
            else:
                # RFC 6762 §11 fixes the TTL at 255 for mDNS, and a responder
                # may discard anything else. The IPv6 branch above sets the
                # equivalent; leaving IPv4 on the OS default (usually 1) would
                # make queries from this half look non-conformant while the
                # announcements from the other half did not.
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, transport.hop_limit)
                if interface is not None:
                    sock.setsockopt(
                        socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface)
                    )
        except (OSError, ValueError) as exc:
            # A configured interface that does not exist is the operator's
            # mistake and is worth a warning; without one this is the host
            # declining an address family it does not have, which the
            # transport's other group may cover.
            logger.log(
                logging.WARNING if interface is not None else logging.DEBUG,
                "could not prepare a socket for %s: %s",
                group,
                exc,
            )
            sock.close()
            return None
        return sock

    @staticmethod
    def _collect(sockets: list[socket.socket], timeout: float) -> list[tuple[bytes, str]]:
        """Read replies until the deadline passes.

        To the deadline rather than to the first reply: several servers may
        answer one PTR query, and stopping at the first would make discovery a
        race between them.
        """
        deadline = time.monotonic() + timeout
        replies: list[tuple[bytes, str]] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return replies
            try:
                ready, _, _ = select.select(sockets, [], [], remaining)
            except OSError as exc:
                logger.debug("select failed while waiting for replies: %s", exc)
                return replies
            for sock in ready:
                try:
                    payload, address = sock.recvfrom(_MAX_DATAGRAM)
                except OSError as exc:
                    logger.debug("could not read a reply: %s", exc)
                    continue
                replies.append((payload, address[0]))


def discover(
    transport: MulticastTransport,
    *,
    timeout: float = 3.0,
    subtype: str | None = None,
    interface: str | None = None,
    source: PacketSource | None = None,
) -> list[DiscoveredServer]:
    """Find IEEE 2030.5 servers on the local network over one transport.

    Two rounds. The first asks for the PTR records naming the service
    instances; the second fills in any instance whose SRV/TXT pair the
    responder did not volunteer. Most responders answer the first round
    completely, in which case the second never runs and discovery costs one
    round trip -- but a responder is not obliged to, and an implementation
    assuming otherwise finds nothing against the ones that do not.

    Args:
        transport: Which multicast transport to query over.
        timeout: Total seconds to spend, across both rounds.
        subtype: A §7.5 subtype narrowing the query to one function set.
        interface: Interface to send from -- an address for IPv4, a name for
            IPv6. Unset sends over the default route, which on a gateway with
            both a utility uplink and a device LAN is usually the wrong one.
        source: Injectable network seam; the real sockets by default.

    Returns:
        The servers found, in the order their instances were named. Empty if
        nothing answered, which is not an error: nothing answering is the
        expected result on a network with no IEEE 2030.5 server on it.
    """
    if subtype is not None:
        subtype = validate_subtype(subtype)
        if subtype.split("-", 1)[0] not in KNOWN_SUBTYPES:
            logger.info(
                "subtype %r is not one of the strings IEEE 2030.5 §7.5 Table 17 defines; "
                "querying for it anyway",
                subtype,
            )

    source = source or SocketPacketSource()
    service = service_labels(transport, subtype)
    logger.debug("querying %s over %s", format_name(service), transport.name)

    # Two thirds of the budget on the round that usually answers everything.
    ptr_timeout = timeout * 2 / 3
    ident = _new_ident()
    replies = source.exchange(
        transport,
        [encode_query(service, TYPE_PTR, ident, unicast=True)],
        ptr_timeout,
        interface,
    )
    instances = _instances(_decode_all(replies, {ident}), service)
    if not instances:
        return []

    servers: dict[tuple[bytes, ...], DiscoveredServer] = {}
    unresolved: list[tuple[bytes, ...]] = []
    for instance, message, host in instances:
        server = server_from_records(message, instance, transport, fallback_host=host)
        if server is not None:
            servers.setdefault(instance, server)
        elif (
            _first(message, instance, TYPE_SRV) is None
            or _first(message, instance, TYPE_TXT) is None
        ):
            # Incomplete rather than rejected: the responder named an instance
            # without fully describing it, so ask it directly. Either half
            # missing is a reason to ask -- a PTR with an SRV and no TXT is
            # just as unusable as one with neither, and treating it as a
            # rejection would silently lose the server. A record that *was*
            # complete and failed §7.4 is not retried, because asking again
            # returns the same thing.
            unresolved.append(instance)

    if unresolved:
        servers.update(
            _resolve_instances(source, transport, unresolved, timeout - ptr_timeout, interface)
        )

    ordered = [servers[instance] for instance, _, _ in instances if instance in servers]
    logger.debug("%s found %d server(s)", transport.name, len(ordered))
    return ordered


def discover_all(
    transports: Iterable[MulticastTransport],
    *,
    timeout: float = 3.0,
    subtype: str | None = None,
    interface: str | None = None,
    source: PacketSource | None = None,
) -> list[DiscoveredServer]:
    """Discover over several transports, keeping the first answer for a server.

    A server reachable over both mDNS and xmDNS answers twice, with the same
    host and port under two instance names. Deduplicating on the endpoint
    rather than on the name is what stops ``both`` reporting one server as two,
    and the earlier transport wins, which is why ``transports_for`` puts mDNS
    first.

    ``timeout`` is per transport, not shared between them: halving each
    transport's listening window because the other is also being queried would
    make ``both`` worse at finding a server than either alone.
    """
    found: dict[tuple[str, int], DiscoveredServer] = {}
    for transport in transports:
        for server in discover(
            transport, timeout=timeout, subtype=subtype, interface=interface, source=source
        ):
            found.setdefault((server.host, server.port), server)
    return list(found.values())


def discover_for_client(
    transports: Sequence[MulticastTransport],
    *,
    sfdi: int | None = None,
    subtype: str | None = None,
    timeout: float = 3.0,
    interface: str | None = None,
    source: PacketSource | None = None,
) -> list[DiscoveredServer]:
    """Locate servers the way Annex C Table C.1 has a client do it.

    Ask first for the server that already holds this client's own EndDevice,
    keyed by its SFDI (steps 3 to 4). Only if nothing answers, ask for any
    "smartenergy" server at all (steps 4 to 5 of the second sequence). The
    order matters on a network with several servers: the one already holding
    this device's registration is the one it should be talking to, and a
    generic query would return it alongside servers that know nothing about
    this device.

    ``subtype`` replaces that sequence with a single §7.5 subtype query, for a
    caller that wants one function set rather than a server to register with.
    It is not a filter applied afterwards: the subtype goes into the PTR name,
    so passing it and then falling back to a generic query would answer a
    different question from the one that was asked.
    """
    if subtype is not None:
        logger.info("looking for a server advertising the %r function set", subtype)
        return discover_all(
            transports, timeout=timeout, subtype=subtype, interface=interface, source=source
        )

    if sfdi is not None:
        own = edev_subtype(sfdi)
        logger.info("looking for a server holding this client's EndDevice (%s)", own)
        targeted = discover_all(
            transports, timeout=timeout, subtype=own, interface=interface, source=source
        )
        if targeted:
            return targeted
        logger.info("no server advertises this client's EndDevice; asking for any server")

    return discover_all(transports, timeout=timeout, interface=interface, source=source)


def _resolve_instances(
    source: PacketSource,
    transport: MulticastTransport,
    instances: Sequence[tuple[bytes, ...]],
    timeout: float,
    interface: str | None,
) -> dict[tuple[bytes, ...], DiscoveredServer]:
    """Ask directly about instances the first round named but did not describe.

    One QTYPE ANY question per instance rather than a separate SRV and TXT
    question, so a responder returns both records in the same message. That
    matters for more than round trips: a name inside rdata may be a
    compression pointer, and a pointer is an offset from the start of the
    message that wrote it. Records collected from two different datagrams
    cannot be read as though they shared one buffer, so asking one question
    that yields both records is what keeps every name resolvable against the
    bytes it was written against.
    """
    if timeout <= 0:
        return {}

    idents: set[int] = set()
    queries: list[bytes] = []
    for instance in instances:
        ident = _new_ident()
        idents.add(ident)
        queries.append(encode_query(instance, TYPE_ANY, ident, unicast=True))

    messages = _decode_all(source.exchange(transport, queries, timeout, interface), idents)

    resolved: dict[tuple[bytes, ...], DiscoveredServer] = {}
    for instance in instances:
        server = next(
            (
                found
                for message, host in messages
                if (found := server_from_records(message, instance, transport, fallback_host=host))
            ),
            None,
        )
        if server is not None:
            resolved[instance] = server
    return resolved


def _instances(
    messages: Sequence[tuple[DnsMessage, str]], service: tuple[bytes, ...]
) -> list[tuple[tuple[bytes, ...], DnsMessage, str]]:
    """Every distinct instance named by a PTR record for the service."""
    seen: set[tuple[bytes, ...]] = set()
    out: list[tuple[tuple[bytes, ...], DnsMessage, str]] = []
    folded = fold(service)

    for message, host in messages:
        for record in message.records:
            if record.rtype != TYPE_PTR or fold(record.name) != folded:
                continue
            try:
                instance = decode_ptr(message, record)
            except DnsDecodeError as exc:
                logger.warning("discarding a malformed PTR record: %s", exc)
                continue
            key = fold(instance)
            if key in seen:
                continue
            seen.add(key)
            out.append((instance, message, host))
    return out


def _decode_all(
    replies: Sequence[tuple[bytes, str]], idents: set[int]
) -> list[tuple[DnsMessage, str]]:
    """Decode replies, dropping any that is malformed or unsolicited.

    Matching the transaction identifier is what a legacy resolver does and is
    worth doing here: the socket is an ephemeral UDP port, so anything on the
    host can send to it, and a reply nobody asked for should not become a
    server this client then connects to.
    """
    out: list[tuple[DnsMessage, str]] = []
    for payload, host in replies:
        try:
            message = decode_message(payload)
        except DnsDecodeError as exc:
            logger.warning("discarding a malformed reply from %s: %s", host, exc)
            continue
        if message.ident not in idents:
            logger.debug("discarding a reply from %s carrying an unexpected id", host)
            continue
        out.append((message, host))
    return out


def _new_ident() -> int:
    """A transaction identifier for one query.

    Random rather than zero. RFC 6762 §18.1 has a multicast query use zero,
    but this is a one-shot query answered by unicast to an ephemeral port
    (§6.7), where the identifier is the only thing tying a reply to a request.
    """
    return secrets.randbelow(65536)
