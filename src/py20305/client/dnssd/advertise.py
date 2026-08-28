"""Announcing this client on the local network over multicast DNS-SD.

What is advertised is *this client*, not an IEEE 2030.5 server. The standard
gives the advertising role to the server and the querying role to the client,
so the default service name here is ``_py20305._tcp`` rather than the
IANA-registered ``_smartenergy._tcp`` of §7.3. Claiming the registered name
would make every conformant client on the link believe it had found a server,
and then fail against a DeviceCapability resource this process does not serve.
The name is configurable for a test network where being taken for a server is
the point.

Being on the network under a well-known name is a disclosure, so the records
carry only what identifies the client to something already entitled to see it:
its LFDI and SFDI, both derived from a certificate the utility issued and both
already visible in the clear on every TLS handshake this client makes.
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import select
import socket
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from py20305.client.dnssd.wire import (
    DEFAULT_SERVICE,
    TYPE_A,
    TYPE_AAAA,
    TYPE_ANY,
    TYPE_PTR,
    TYPE_SRV,
    TYPE_TXT,
    DnsDecodeError,
    MulticastTransport,
    Question,
    Record,
    decode_questions,
    encode_address,
    encode_name,
    encode_response,
    encode_srv,
    encode_txt,
    fold,
    format_name,
    validate_service,
)

logger = logging.getLogger(__name__)

#: RFC 6763 §6.1 recommends 75 minutes for the records describing a service and
#: 120 seconds for address records, on the reasoning that the address is the
#: part most likely to change under a host.
_SERVICE_TTL = 4500
_ADDRESS_TTL = 120

#: RFC 6762 §8.3: announce at least twice, one second apart. Three, because a
#: single lost datagram on a busy link should not cost this client its
#: visibility for the 75 minutes the service TTL then claims.
_ANNOUNCE_COUNT = 3
_ANNOUNCE_INTERVAL = 1.0

#: Long enough for any query a responder will receive over UDP.
_MAX_DATAGRAM = 9000


def sfdi_label(sfdi: int) -> str:
    """Render an SFDI as §7.2 requires it inside a DNS-SD label.

    "When an SFDI is used as part of a DNS-SD label, it SHALL be represented as
    12 decimal digits including leading zeros (if any) as well as the checksum
    digit, and SHALL NOT include embedded hyphens." The zero padding is the
    part an implementation drops without noticing, because it only shows up on
    the minority of certificates whose SFDI is short.
    """
    text = str(sfdi)
    if len(text) > 12:
        raise ValueError(f"SFDI {sfdi} does not fit in 12 digits")
    return text.zfill(12)


@dataclass(frozen=True)
class ServiceAdvertisement:
    """The service this client publishes, independent of any transport.

    Held apart from the transport because the same service is announced under
    ``.local`` and ``.site`` with nothing but the domain differing, and because
    building the record set is the part worth testing without a socket.

    Attributes:
        instance: The ``<Instance>`` label of §7.2. Ends with the SFDI, which
            is what makes it collision-resistant without probing.
        service: The two service labels, e.g. ``(b"_py20305", b"_tcp")``.
        port: The TCP port being advertised.
        txt: TXT key/value pairs. A value of None writes the key bare.
        hostname: The single label the address records are published under.
    """

    instance: bytes
    service: tuple[bytes, bytes]
    port: int
    txt: Mapping[str, str | None]
    hostname: bytes

    def __post_init__(self) -> None:
        # §7.2: "A server SHALL assign a unique <Instance> label of up to 63
        # bytes in UTF-8 format". Checked here rather than at encode time so a
        # bad name fails at startup naming the setting, instead of making every
        # announcement raise from inside the responder thread.
        if not 0 < len(self.instance) <= 63:
            raise ValueError(f"instance label must be 1-63 bytes, got {len(self.instance)}")
        if not 0 < len(self.hostname) <= 63:
            raise ValueError(f"hostname label must be 1-63 bytes, got {len(self.hostname)}")
        if not 0 < self.port <= 65535:
            raise ValueError(f"port must be 1-65535, got {self.port}")

    def service_name(self, transport: MulticastTransport) -> tuple[bytes, ...]:
        """``_py20305._tcp.<domain>`` -- the name a browser sends a PTR query for."""
        return (*self.service, transport.domain)

    def instance_name(self, transport: MulticastTransport) -> tuple[bytes, ...]:
        """``<instance>._py20305._tcp.<domain>`` -- the Service Instance Name."""
        return (self.instance, *self.service, transport.domain)

    def host_name(self, transport: MulticastTransport) -> tuple[bytes, ...]:
        """``<hostname>.<domain>`` -- what the SRV record targets."""
        return (self.hostname, transport.domain)

    def records(self, transport: MulticastTransport, addresses: Sequence[str]) -> list[Record]:
        """The full record set to announce on one transport.

        PTR first, then the SRV/TXT pair it names, then the addresses the SRV
        targets -- the order a receiver can consume without a second lookup.
        """
        instance_name = self.instance_name(transport)
        host_name = self.host_name(transport)
        records = [
            Record(
                name=self.service_name(transport),
                rtype=TYPE_PTR,
                ttl=_SERVICE_TTL,
                rdata=encode_name(instance_name),
                # Shared: every host offering this service answers this name,
                # so flushing on it would erase the others' answers.
                unique=False,
            ),
            Record(
                name=instance_name,
                rtype=TYPE_SRV,
                ttl=_SERVICE_TTL,
                rdata=encode_srv(self.port, host_name),
                unique=True,
            ),
            Record(
                name=instance_name,
                rtype=TYPE_TXT,
                ttl=_SERVICE_TTL,
                rdata=encode_txt(self.txt),
                unique=True,
            ),
        ]
        records.extend(
            Record(
                name=host_name,
                rtype=TYPE_AAAA if ipaddress.ip_address(address).version == 6 else TYPE_A,
                ttl=_ADDRESS_TTL,
                rdata=encode_address(address),
                unique=True,
            )
            for address in addresses
        )
        return records


def build_advertisement(
    *,
    lfdi: str,
    sfdi: int,
    port: int,
    service: str = DEFAULT_SERVICE,
    instance: str | None = None,
    version: str | None = None,
    extra_txt: Mapping[str, str] | None = None,
) -> ServiceAdvertisement:
    """Build the advertisement for one running client.

    The instance name follows §7.2: a meaningful prefix, a hyphen, and the
    SFDI. That is what makes the name collision-resistant, and it is why this
    module does not implement the probing of RFC 6762 §8.1 -- two clients
    colliding would have to hold the same certificate, at which point they have
    a larger problem than a duplicate DNS-SD name.

    Args:
        lfdi: This client's LFDI, published so a tool that finds the client can
            tell which certificate identity it holds.
        sfdi: This client's SFDI, used for the instance label.
        port: The TCP port to advertise.
        service: The ``_name._proto`` service to advertise under.
        instance: Override for the instance label, for the case §7.2 leaves to
            the implementer: two clients sharing one certificate.
        version: Product version, published as the ``ver`` key.
        extra_txt: Additional TXT keys.
    """
    digits = sfdi_label(sfdi)
    labels = validate_service(service)
    txt: dict[str, str | None] = {
        # §7.4 puts txtvers first and fixes it at 1 in both editions. This is
        # not an IEEE 2030.5 service record, but a reader who knows the
        # standard's TXT conventions should not have to guess at ours.
        "txtvers": "1",
        "lfdi": lfdi,
        "sfdi": digits,
    }
    if version is not None:
        txt["ver"] = version
    if extra_txt:
        txt.update(extra_txt)

    return ServiceAdvertisement(
        instance=(instance or f"py20305-{digits}").encode("utf-8"),
        service=labels,
        port=port,
        txt=txt,
        hostname=f"py20305-{digits}".encode(),
    )


def local_address_for(group: str, interface: str | None = None) -> str | None:
    """The address this host would use to reach a multicast group.

    Asked of the routing table rather than guessed from the interface list: a
    gateway with a utility uplink and a device LAN has several addresses, and
    the one to publish is whichever one traffic to this group leaves from.
    ``connect`` on a UDP socket sends nothing; it only fixes the route.
    """
    version = ipaddress.ip_address(group).version
    family = socket.AF_INET6 if version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            if interface is not None and version == 6:
                sock.setsockopt(
                    socket.IPPROTO_IPV6,
                    socket.IPV6_MULTICAST_IF,
                    socket.if_nametoindex(interface),
                )
            elif interface is not None:
                sock.bind((interface, 0))
            sock.connect((group, 5353))
            address = sock.getsockname()[0]
    except (OSError, ValueError) as exc:
        logger.debug("no route to %s: %s", group, exc)
        return None

    # A scope suffix is meaningful only on this host, so it cannot go into a
    # record another host will read -- and stripped of it, a link-local address
    # is not connectable, which is the same reason the discovery side refuses
    # to use one. §7.1 settles it for xmDNS regardless: IEEE 2030.5 "SHALL use
    # global addresses or Unique Local Addresses (IETF RFC 4193) in the source
    # address of xmDNS requests and responses".
    address = address.split("%", 1)[0]
    parsed = ipaddress.ip_address(address)
    if parsed.is_unspecified or parsed.is_link_local:
        return None
    return address


class _GroupSocket:
    """One socket bound for a multicast group."""

    def __init__(self, sock: socket.socket, group: str, port: int) -> None:
        self.sock = sock
        self.group = group
        self.port = port

    def send(self, payload: bytes, to: tuple[Any, ...] | None = None) -> None:
        try:
            self.sock.sendto(payload, to or (self.group, self.port))
        except OSError as exc:
            logger.debug("could not send to %s: %s", to or self.group, exc)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


def _open_group_socket(
    group: str, transport: MulticastTransport, interface: str | None
) -> _GroupSocket | None:
    """Bind to the mDNS port and join a group. None if this group is unusable.

    The well-known port is shared. A host running its own responder already
    holds 5353, and the address-reuse options are what let both bind it -- that
    is how every mDNS implementation on a general-purpose host coexists with
    the others. Where the platform refuses anyway there is nothing usable left
    to fall back to: RFC 6762 §6 requires a response to be sent *from* 5353,
    so a socket on any other port announces into silence. :func:`_bind_and_join`
    reports the group unavailable instead, and the caller skips it.
    """
    version = ipaddress.ip_address(group).version
    family = socket.AF_INET6 if version == 6 else socket.AF_INET
    try:
        sock = socket.socket(family, socket.SOCK_DGRAM)
    except OSError as exc:
        logger.debug("no socket available for %s: %s", group, exc)
        return None

    try:
        _set_multicast_options(sock, group, transport, interface)
    except (OSError, ValueError) as exc:
        # A configured interface that does not exist is the operator's mistake
        # and is worth a warning; without one this is the host declining an
        # address family it does not have, which the other group may cover.
        logger.log(
            logging.WARNING if interface is not None else logging.DEBUG,
            "could not prepare a socket for %s: %s",
            group,
            exc,
        )
        sock.close()
        return None

    if not _bind_and_join(sock, group, transport, version, interface):
        sock.close()
        return None
    return _GroupSocket(sock, group, transport.port)


def _set_multicast_options(
    sock: socket.socket, group: str, transport: MulticastTransport, interface: str | None
) -> None:
    """Set the send-side multicast options for a group."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    reuse_port = getattr(socket, "SO_REUSEPORT", None)
    if reuse_port is not None:
        with contextlib.suppress(OSError):
            # Absent on Windows and rejected by some kernels even where the
            # constant exists; SO_REUSEADDR alone is enough on those.
            sock.setsockopt(socket.SOL_SOCKET, reuse_port, 1)

    if ipaddress.ip_address(group).version == 6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, transport.hop_limit)
        if interface is not None:
            sock.setsockopt(
                socket.IPPROTO_IPV6,
                socket.IPV6_MULTICAST_IF,
                socket.if_nametoindex(interface),
            )
    else:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, transport.hop_limit)
        if interface is not None:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))


def _bind_and_join(
    sock: socket.socket,
    group: str,
    transport: MulticastTransport,
    version: int,
    interface: str | None,
) -> bool:
    """Bind the well-known port and join the group. False if this group is unusable.

    The join takes ``interface`` as well, because a membership and a send are
    separate settings: joining on the default interface while announcing on the
    configured one gives a responder that publishes where it was told to and
    listens somewhere else.

    There is no ephemeral-port fallback, because there is no such thing as a
    half-working responder here. RFC 6762 §6 requires an mDNS response to be
    sent *from* UDP 5353 and has receivers ignore responses from any other
    source port, so announcing from an ephemeral port produces packets a
    conformant listener discards -- while the log claims the client is
    advertised. Reporting the group as unavailable is the honest outcome, and
    it points at the real cause: something else on this host already holds the
    port, and it is probably the right thing to be answering.
    """
    try:
        sock.bind(("" if version == 4 else "::", transport.port))
    except OSError as exc:
        logger.warning(
            "cannot announce on %s: UDP %d is already held on this host (%s). "
            "A responder already running here (avahi-daemon, mDNSResponder, "
            "Bonjour) owns the port; announce through it, or stop it first.",
            group,
            transport.port,
            exc,
        )
        return False

    # The membership takes the configured interface too, not just the send
    # side. On a multi-homed host the two are separate settings, and joining on
    # the default interface while sending on the chosen one produces a
    # responder that announces where it was told to and hears queries
    # somewhere else -- which looks like a responder that answers nothing.
    try:
        if version == 6:
            index = socket.if_nametoindex(interface) if interface is not None else 0
            # ipv6_mreq carries the interface index in host byte order. That
            # was invisible while the index was always zero, and is not once
            # a configured interface can make it something else.
            membership = ipaddress.ip_address(group).packed + index.to_bytes(4, sys.byteorder)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, membership)
        else:
            local = socket.inet_aton(interface if interface is not None else "0.0.0.0")
            membership = socket.inet_aton(group) + local
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    except (OSError, ValueError) as exc:
        logger.warning(
            "cannot announce on %s: bound UDP %d but could not join the group (%s)",
            group,
            transport.port,
            exc,
        )
        return False
    return True


#: RFC 6762 §6: a responder "MUST NOT ... multicast a record on a given
#: interface until at least one second has elapsed since the last time that
#: record was multicast on that interface".
_MULTICAST_MIN_INTERVAL = 1.0

#: Unicast answers are not covered by that rule, but they are the shape a
#: reflection attack takes: a spoofed source address turns this responder into
#: a small amplifier pointed at someone else. A modest ceiling costs a real
#: querier nothing, since one exchange is all discovery needs.
_UNICAST_BURST = 10
_UNICAST_WINDOW = 1.0


@dataclass
class _Binding:
    """One socket, and the records published through it."""

    socket: _GroupSocket
    transport: MulticastTransport
    addresses: tuple[str, ...]
    records: tuple[Record, ...]
    advertised_name: tuple[bytes, ...] = field(default_factory=tuple)
    _last_multicast: float = float("-inf")
    _unicast_window_start: float = float("-inf")
    _unicast_sent: int = 0

    def may_respond(self, unicast: bool, now: float) -> bool:
        """Whether a response may go out now, and record that it did.

        Two limits with different reasons. The multicast one is the standard's
        (§6) and protects the link from a responder answering every repeated
        query. The unicast one is ours and bounds how useful this process is to
        someone spoofing a source address.
        """
        if unicast:
            if now - self._unicast_window_start >= _UNICAST_WINDOW:
                self._unicast_window_start = now
                self._unicast_sent = 0
            if self._unicast_sent >= _UNICAST_BURST:
                logger.debug("dropping a unicast answer: over the per-second ceiling")
                return False
            self._unicast_sent += 1
            return True

        if now - self._last_multicast < _MULTICAST_MIN_INTERVAL:
            logger.debug("suppressing a multicast answer sent less than a second ago")
            return False
        self._last_multicast = now
        return True


class MulticastAdvertiser:
    """Announces one service on the configured transports, and answers for it.

    Runs a thread rather than an asyncio task. The work is a blocking
    ``select`` over a handful of sockets with a timer beside it, which is
    exactly what a thread expresses well, and it keeps multicast socket
    behavior -- the part that differs most between platforms -- out of the
    event loop the protocol client depends on.
    """

    def __init__(
        self,
        advertisement: ServiceAdvertisement,
        transports: Sequence[MulticastTransport],
        *,
        interface: str | None = None,
    ) -> None:
        self._advertisement = advertisement
        self._transports = tuple(transports)
        self._interface = interface
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._bindings: list[_Binding] = []
        self._started = threading.Event()

    @property
    def running(self) -> bool:
        """Whether the responder thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Open the sockets and begin announcing.

        Returns once the sockets are open, so a caller that stops the client
        immediately still sends the goodbye for records it announced.
        """
        if self.running or not self._transports:
            return
        self._stop.clear()
        self._started.clear()
        self._thread = threading.Thread(target=self._run, name="mdns-advertiser", daemon=True)
        self._thread.start()
        self._started.wait(timeout=5.0)

    def stop(self, timeout: float = 5.0) -> None:
        """Send the goodbye records and shut the responder down.

        The goodbye (RFC 6762 §10.1) is a re-announcement with TTL 0. Without
        it a receiver keeps this client in its cache for the 75 minutes the
        service TTL claims, so a restarting client is listed twice and a
        stopped one is listed at all.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning("the mDNS advertiser did not stop within %ss", timeout)
        self._thread = None

    def _run(self) -> None:
        try:
            self._bindings = self._open()
            if not self._bindings:
                logger.warning(
                    "could not announce on any configured transport (%s)",
                    ", ".join(t.name for t in self._transports),
                )
                return
            for binding in self._bindings:
                logger.info(
                    "announcing %s at %s:%d over %s",
                    format_name(binding.advertised_name),
                    ", ".join(binding.addresses),
                    self._advertisement.port,
                    binding.transport.name,
                )
        finally:
            # Set even on failure: start() waits on this, and a caller blocked
            # for five seconds because the sockets could not open learns
            # nothing it does not already have in the log.
            self._started.set()

        try:
            self._serve()
        finally:
            self._say_goodbye()
            for binding in self._bindings:
                binding.socket.close()
            self._bindings = []

    def _open(self) -> list[_Binding]:
        """Open a socket per group and work out what to publish on each."""
        bindings: list[_Binding] = []
        for transport in self._transports:
            for group in transport.groups:
                address = local_address_for(group, self._interface)
                if address is None:
                    continue
                sock = _open_group_socket(group, transport, self._interface)
                if sock is None:
                    continue
                bindings.append(
                    _Binding(
                        socket=sock,
                        transport=transport,
                        addresses=(address,),
                        records=tuple(self._advertisement.records(transport, [address])),
                        advertised_name=self._advertisement.instance_name(transport),
                    )
                )
        return bindings

    def _serve(self) -> None:
        """Announce, then answer queries until stopped."""
        announcements = 0
        next_announcement = time.monotonic()
        sockets = {b.socket.sock: b for b in self._bindings}

        while not self._stop.is_set():
            now = time.monotonic()
            if announcements < _ANNOUNCE_COUNT and now >= next_announcement:
                for binding in self._bindings:
                    # Marks the multicast budget, so an announcement and an
                    # answer to a query arriving right after it do not put the
                    # same records on the group twice inside a second.
                    binding.may_respond(unicast=False, now=now)
                    binding.socket.send(encode_response(binding.records))
                announcements += 1
                next_announcement = now + _ANNOUNCE_INTERVAL

            waiting = (
                max(0.0, next_announcement - time.monotonic())
                if announcements < _ANNOUNCE_COUNT
                else 1.0
            )
            try:
                ready, _, _ = select.select(list(sockets), [], [], waiting)
            except OSError as exc:
                logger.debug("select failed in the advertiser: %s", exc)
                continue
            for sock in ready:
                self._handle(sockets[sock])

    def _handle(self, binding: _Binding) -> None:
        """Read one datagram and answer it if it asks about our records."""
        try:
            payload, source = binding.socket.sock.recvfrom(_MAX_DATAGRAM)
        except OSError as exc:
            logger.debug("could not read a query: %s", exc)
            return

        try:
            questions = decode_questions(payload)
        except DnsDecodeError as exc:
            logger.debug("discarding a malformed query from %s: %s", source[0], exc)
            return

        answers = self._answers_for(binding, questions)
        if not answers:
            return

        # RFC 6762 §6.7: a query whose source port is not 5353 comes from an
        # ordinary DNS resolver doing a one-shot lookup. It matches the reply
        # to its request by transaction id and expects its question echoed, so
        # it needs the legacy encoding rather than the multicast one. §5.4's QU
        # bit is the other reason to answer one host directly.
        legacy = len(source) > 1 and source[1] != binding.socket.port
        unicast = legacy or any(question.unicast for question in questions)
        if not binding.may_respond(unicast, time.monotonic()):
            return

        if legacy:
            response = encode_response(
                answers, ident=int.from_bytes(payload[0:2], "big"), questions=questions
            )
        else:
            response = encode_response(answers)
        # The whole source tuple, not the first two elements. recvfrom on an
        # AF_INET6 socket returns (host, port, flowinfo, scopeid), and sending
        # to a link-local peer without the scope fails with EINVAL -- which
        # this would swallow, leaving the querier to time out instead.
        binding.socket.send(response, source if unicast else None)

    def _answers_for(self, binding: _Binding, questions: Sequence[Question]) -> list[Record]:
        """The records answering these questions, in announcement order.

        Only our own names are matched. A responder that answered for anything
        else would be poisoning its neighbors' lookups, which is what makes a
        badly written mDNS implementation a network problem rather than a
        local one.
        """
        wanted: list[Record] = []
        for question in questions:
            name = fold(question.name)
            for record in binding.records:
                if fold(record.name) != name:
                    continue
                if question.qtype not in (record.rtype, TYPE_ANY):
                    continue
                if record not in wanted:
                    wanted.append(record)
        if not wanted:
            return []

        # A PTR answer is useless on its own: the asker then needs the SRV, the
        # TXT and an address, and RFC 6763 §12.1 says to send them along rather
        # than make it ask three more times.
        if any(record.rtype == TYPE_PTR for record in wanted):
            for record in binding.records:
                if record.rtype != TYPE_PTR and record not in wanted:
                    wanted.append(record)
        return [record for record in binding.records if record in wanted]

    def _say_goodbye(self) -> None:
        """Re-announce every record with TTL 0 (RFC 6762 §10.1)."""
        for binding in self._bindings:
            binding.socket.send(
                encode_response([record.with_ttl(0) for record in binding.records])
            )
