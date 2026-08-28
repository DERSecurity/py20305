"""DNS-SD: the record format, the query side, and the announcement side.

The two halves are tested against each other wherever possible -- the
advertiser encodes a record set and the discovery side decodes it -- because
that is the coupling most likely to drift, and a test that hand-writes the
bytes it expects proves only that the test agrees with itself.

The rules from IEEE 2030.5 §7.4 get individual tests because each one is a
silent failure: a record the client is told to discard and does not becomes a
server it connects to, and a scheme rule read wrongly becomes a plaintext
connection to a server that offered TLS.
"""

from __future__ import annotations

import struct

import pytest

from py20305.client.dnssd.advertise import (
    ServiceAdvertisement,
    build_advertisement,
    sfdi_label,
    validate_service,
)
from py20305.client.dnssd.discover import (
    DiscoveredServer,
    discover,
    discover_all,
    discover_for_client,
    edev_subtype,
    service_labels,
    validate_subtype,
)
from py20305.client.dnssd.wire import (
    MDNS,
    TYPE_A,
    TYPE_AAAA,
    TYPE_PTR,
    TYPE_SRV,
    TYPE_TXT,
    XMDNS,
    DnsDecodeError,
    Question,
    Record,
    decode_message,
    decode_questions,
    decode_srv,
    decode_txt,
    encode_address,
    encode_name,
    encode_query,
    encode_response,
    encode_srv,
    encode_txt,
    read_name,
    record_rdata,
    transports_for,
)

# --------------------------------------------------------------------------
# Building a server's answer, so the client side has something real to read
# --------------------------------------------------------------------------

SERVICE = (b"_smartenergy", b"_tcp")


def server_records(
    *,
    domain: bytes = b"local",
    instance: bytes = b"127-edev-000001111114",
    txt: dict[str, str | None] | None = None,
    srv_port: int = 80,
    addresses: tuple[str, ...] = ("192.168.1.40",),
    host: bytes = b"server-a",
    subtype: str | None = None,
) -> list[Record]:
    """The record set a conformant IEEE 2030.5 server would publish.

    ``subtype`` publishes the PTR under the §7.5 subtype name instead of the
    plain service name, which is what a server registering a function set
    does and what a subtype query actually matches against.
    """
    if txt is None:
        txt = {"txtvers": "1", "dcap": "/dcap", "https": "8443", "level": "-S2"}
    service_name = (
        (*SERVICE, domain)
        if subtype is None
        else (subtype.encode(), b"_sub", *SERVICE, domain)
    )
    instance_name = (instance, *SERVICE, domain)
    host_name = (host, domain)
    records = [
        Record(service_name, TYPE_PTR, 4500, encode_name(instance_name), unique=False),
        Record(instance_name, TYPE_SRV, 4500, encode_srv(srv_port, host_name), unique=True),
        Record(instance_name, TYPE_TXT, 4500, encode_txt(txt), unique=True),
    ]
    records += [
        Record(
            host_name,
            TYPE_AAAA if ":" in address else TYPE_A,
            120,
            encode_address(address),
            unique=True,
        )
        for address in addresses
    ]
    return records


def response_with_ident(records: list[Record], ident: int) -> bytes:
    """An encoded response stamped with a transaction id, as a reply would be."""
    payload = bytearray(encode_response(records))
    payload[0:2] = ident.to_bytes(2, "big")
    return bytes(payload)


class FakeSource:
    """A PacketSource that answers with canned records.

    Echoes each query's transaction id, which is what a real responder does
    for a legacy unicast query and what the discovery side checks before
    trusting a reply.
    """

    def __init__(self, records: list[Record] | None, source_ip: str = "192.168.1.40") -> None:
        self.records = records
        self.source_ip = source_ip
        self.queries: list[tuple[str, bytes]] = []

    def exchange(self, transport, queries, timeout, interface):  # noqa: ANN001, ANN201
        replies = []
        for query in queries:
            name, _ = read_name(query, 12)
            self.queries.append((".".join(x.decode() for x in name), query))
            if self.records is None:
                continue
            ident = int.from_bytes(query[0:2], "big")
            replies.append((response_with_ident(self.records, ident), self.source_ip))
        return replies


def discovered(records: list[Record], transport=MDNS, **kwargs) -> list[DiscoveredServer]:  # noqa: ANN001
    return discover(transport, timeout=0.01, source=FakeSource(records), **kwargs)


# --------------------------------------------------------------------------
# Wire format
# --------------------------------------------------------------------------


class TestNameDecoding:
    def test_backward_compression_pointer_is_followed(self) -> None:
        """A legal pointer resolves to the name it points at."""
        target = encode_name((b"server-a", b"local"))
        data = b"\x00" * 12 + target + b"\x04test" + struct.pack("!H", 0xC000 | 12)
        labels, _ = read_name(data, 12 + len(target))
        assert labels == (b"test", b"server-a", b"local")

    def test_self_referential_pointer_is_rejected(self) -> None:
        """The shape that hangs a naive parser."""
        data = b"\x00" * 12 + struct.pack("!H", 0xC000 | 12)
        with pytest.raises(DnsDecodeError, match="backwards"):
            read_name(data, 12)

    def test_forward_pointer_is_rejected(self) -> None:
        """Forward pointers are how a loop is built out of two legal-looking jumps."""
        data = b"\x00" * 12 + struct.pack("!H", 0xC000 | 20) + b"\x00" * 8
        with pytest.raises(DnsDecodeError, match="backwards"):
            read_name(data, 12)

    def test_label_running_past_the_end_is_rejected(self) -> None:
        with pytest.raises(DnsDecodeError):
            read_name(b"\x20abc", 0)


class TestMessageDecoding:
    def test_message_shorter_than_a_header_is_rejected(self) -> None:
        with pytest.raises(DnsDecodeError, match="shorter than a DNS header"):
            decode_message(b"\x00" * 11)

    def test_rdata_past_the_end_is_rejected(self) -> None:
        """A record claiming more data than the datagram holds."""
        message = bytearray(encode_response(server_records()))
        # Inflate the first record's rdlength far past the end of the buffer.
        offset = message.index(encode_name((*SERVICE, b"local"))) + len(
            encode_name((*SERVICE, b"local"))
        )
        message[offset + 8 : offset + 10] = (9000).to_bytes(2, "big")
        with pytest.raises(DnsDecodeError, match="past the end"):
            decode_message(bytes(message))

    def test_cache_flush_bit_does_not_hide_a_record(self) -> None:
        """A unique record carries the flush bit; it must still read as class IN."""
        message = decode_message(encode_response(server_records()))
        srv = next(r for r in message.records if r.rtype == TYPE_SRV)
        assert srv.rclass == 1


class TestTxtEncoding:
    """§7.4 leans on three distinct states of a key, so the codec must carry them."""

    def test_the_three_states_survive_a_round_trip(self) -> None:
        encoded = encode_txt({"bare": None, "empty": "", "valued": "8443"})
        decoded = decode_txt(encoded)
        assert decoded == {"bare": None, "empty": "", "valued": "8443"}

    def test_a_bare_key_is_not_an_empty_one(self) -> None:
        """The distinction the https rule depends on."""
        assert decode_txt(encode_txt({"https": None})) != decode_txt(encode_txt({"https": ""}))

    def test_keys_are_lowercased(self) -> None:
        assert decode_txt(encode_txt({"TxtVers": "1"})) == {"txtvers": "1"}

    def test_first_duplicate_key_wins(self) -> None:
        """RFC 6763 §6.4."""
        raw = b"\x07dcap=/a" + b"\x07dcap=/b"
        assert decode_txt(raw) == {"dcap": "/a"}

    def test_a_service_with_no_attributes_encodes_one_empty_string(self) -> None:
        """RFC 6763 §6.1: empty rdata is invalid, a single empty string is not."""
        assert encode_txt({}) == b"\x00"

    def test_txt_string_past_the_record_end_is_rejected(self) -> None:
        with pytest.raises(DnsDecodeError):
            decode_txt(b"\x40short")


class TestSrvAndAddresses:
    def test_srv_round_trip(self) -> None:
        message = decode_message(encode_response(server_records(srv_port=8080)))
        srv = next(r for r in message.records if r.rtype == TYPE_SRV)
        port, target = decode_srv(message, srv)
        assert port == 8080
        assert target == (b"server-a", b"local")

    def test_short_srv_is_rejected(self) -> None:
        message = decode_message(
            encode_response([Record((b"x", b"local"), TYPE_SRV, 60, b"\x00\x00", unique=True)])
        )
        with pytest.raises(DnsDecodeError, match="too short"):
            decode_srv(message, message.records[0])


class TestQueryEncoding:
    def test_qu_bit_is_set_only_when_asked(self) -> None:
        with_qu = encode_query((b"_smartenergy", b"_tcp", b"local"), TYPE_PTR, 1, unicast=True)
        without = encode_query((b"_smartenergy", b"_tcp", b"local"), TYPE_PTR, 1, unicast=False)
        assert int.from_bytes(with_qu[-2:], "big") & 0x8000
        assert not int.from_bytes(without[-2:], "big") & 0x8000

    def test_a_query_is_not_mistaken_for_a_response(self) -> None:
        """decode_questions must return the questions of a query."""
        query = encode_query((b"_smartenergy", b"_tcp", b"local"), TYPE_PTR, 7, unicast=True)
        questions = decode_questions(query)
        assert questions == [
            Question(name=(b"_smartenergy", b"_tcp", b"local"), qtype=TYPE_PTR, unicast=True)
        ]

    def test_a_response_yields_no_questions(self) -> None:
        """So a responder does not try to answer its own announcements."""
        assert decode_questions(encode_response(server_records())) == []


# --------------------------------------------------------------------------
# §7.4 rules on the client side
# --------------------------------------------------------------------------


class TestSection74DiscardRules:
    """Each rule here is one the standard states as a client SHALL."""

    def test_a_conformant_advertisement_is_accepted(self) -> None:
        servers = discovered(server_records())
        assert len(servers) == 1
        assert servers[0].dcap_url == "https://192.168.1.40:8443/dcap"

    @pytest.mark.parametrize("txtvers", ["2", "0", "", "one"])
    def test_txtvers_other_than_1_is_ignored(self, txtvers: str) -> None:
        records = server_records(
            txt={"txtvers": txtvers, "dcap": "/dcap", "https": "8443", "level": "-S2"}
        )
        assert discovered(records) == []

    def test_a_record_without_txtvers_is_discarded(self) -> None:
        records = server_records(txt={"dcap": "/dcap", "https": "8443", "level": "-S2"})
        assert discovered(records) == []

    @pytest.mark.parametrize("key", ["dcap", "level"])
    def test_a_missing_mandatory_key_is_discarded(self, key: str) -> None:
        txt = {"txtvers": "1", "dcap": "/dcap", "https": "8443", "level": "-S2"}
        del txt[key]
        assert discovered(server_records(txt=txt)) == []

    @pytest.mark.parametrize("key", ["dcap", "level"])
    def test_an_empty_mandatory_key_is_discarded(self, key: str) -> None:
        """§7.4 requires present *and* non-empty."""
        txt = {"txtvers": "1", "dcap": "/dcap", "https": "8443", "level": "-S2"}
        txt[key] = ""
        assert discovered(server_records(txt=txt)) == []

    def test_unknown_keys_are_ignored_rather_than_fatal(self) -> None:
        txt = {
            "txtvers": "1",
            "dcap": "/dcap",
            "https": "8443",
            "level": "-S2",
            "somethingnew": "x",
        }
        assert len(discovered(server_records(txt=txt))) == 1


class TestSection74SchemeRules:
    """Absent, present-empty and present-with-a-value are three different things."""

    def test_https_with_a_port_is_used(self) -> None:
        assert discovered(server_records())[0].port == 8443

    def test_https_present_but_valueless_means_443(self) -> None:
        txt = {"txtvers": "1", "dcap": "/dcap", "https": None, "level": "-S2"}
        assert discovered(server_records(txt=txt))[0].port == 443

    def test_https_present_but_empty_means_443(self) -> None:
        """The state a truthiness check silently collapses into "absent"."""
        txt = {"txtvers": "1", "dcap": "/dcap", "https": "", "level": "-S2"}
        assert discovered(server_records(txt=txt))[0].port == 443

    def test_a_server_offering_only_http_is_skipped(self) -> None:
        """This client is mutual-TLS only, so a plaintext server is unusable."""
        txt = {"txtvers": "1", "dcap": "/dcap", "level": "-S2"}
        assert discovered(server_records(txt=txt)) == []

    @pytest.mark.parametrize("value", ["https", "-1", "70000", "0"])
    def test_an_https_value_that_is_not_a_port_is_discarded(self, value: str) -> None:
        txt = {"txtvers": "1", "dcap": "/dcap", "https": value, "level": "-S2"}
        assert discovered(server_records(txt=txt)) == []

    def test_the_srv_port_is_never_used_for_the_tls_connection(self) -> None:
        """§7.5 fixes the SRV port as the http one, so reaching for it is a bug.

        The record set here advertises SRV port 80 and https=8443, which is
        exactly what a conformant server publishes. A client that used the SRV
        port would produce https://...:80 and never connect.
        """
        servers = discovered(server_records(srv_port=80))
        assert servers[0].port == 8443
        assert ":80" not in servers[0].base_url


class TestAddressSelection:
    def test_an_ipv6_literal_is_bracketed_in_the_url(self) -> None:
        servers = discovered(server_records(addresses=("2001:db8::1",)))
        assert servers[0].base_url == "https://[2001:db8::1]:8443"

    def test_link_local_ipv6_is_not_preferred_over_a_usable_v4(self) -> None:
        """A link-local address has no scope id in the record, so it is unusable."""
        servers = discovered(server_records(addresses=("fe80::1", "192.168.1.40")))
        assert servers[0].host == "192.168.1.40"

    def test_the_source_address_is_used_when_no_address_record_is_sent(self) -> None:
        servers = discovered(server_records(addresses=()))
        assert servers[0].host == "192.168.1.40"


class TestSchemaLevel:
    def test_s1_names_the_2018_schema(self) -> None:
        txt = {"txtvers": "1", "dcap": "/dcap", "https": "443", "level": "-S1"}
        assert discovered(server_records(txt=txt))[0].is_2018

    def test_s2_does_not(self) -> None:
        assert not discovered(server_records())[0].is_2018

    def test_the_plus_form_is_read_the_same_way(self) -> None:
        txt = {"txtvers": "1", "dcap": "/dcap", "https": "443", "level": "+S1"}
        assert discovered(server_records(txt=txt))[0].is_2018


# --------------------------------------------------------------------------
# Query construction and the discovery flow
# --------------------------------------------------------------------------


class TestQueryNames:
    def test_the_plain_service_name(self) -> None:
        assert service_labels(MDNS) == (b"_smartenergy", b"_tcp", b"local")

    def test_xmdns_uses_the_site_domain(self) -> None:
        assert service_labels(XMDNS) == (b"_smartenergy", b"_tcp", b"site")

    def test_a_subtype_takes_the_sub_form(self) -> None:
        """§7.5: `<subtype>._sub._smartenergy._tcp.<domain>`."""
        assert service_labels(MDNS, "upt") == (
            b"upt",
            b"_sub",
            b"_smartenergy",
            b"_tcp",
            b"local",
        )

    def test_an_sfdi_subtype_is_padded_to_twelve_digits(self) -> None:
        """§7.2: 12 decimal digits including leading zeros, no embedded hyphens."""
        assert edev_subtype(11111) == "edev-000000011111"
        assert edev_subtype(222222222228) == "edev-222222222228"

    def test_a_subtype_may_not_begin_with_an_underscore(self) -> None:
        with pytest.raises(ValueError, match="underscore"):
            validate_subtype("_upt")

    def test_a_subtype_is_one_label(self) -> None:
        with pytest.raises(ValueError, match="single DNS label"):
            validate_subtype("upt.local")


class TestDiscoveryFlow:
    def test_nothing_answering_is_not_an_error(self) -> None:
        assert discover(MDNS, timeout=0.01, source=FakeSource(None)) == []

    def test_a_reply_with_an_unexpected_id_is_dropped(self) -> None:
        """An ephemeral socket can receive anything the host cares to send."""

        class WrongIdent(FakeSource):
            def exchange(self, transport, queries, timeout, interface):  # noqa: ANN001, ANN201
                ident = int.from_bytes(queries[0][0:2], "big")
                return [(response_with_ident(self.records, ident ^ 0xFFFF), self.source_ip)]

        assert discover(MDNS, timeout=0.01, source=WrongIdent(server_records())) == []

    def test_a_malformed_reply_does_not_stop_discovery(self) -> None:
        class Garbage(FakeSource):
            def exchange(self, transport, queries, timeout, interface):  # noqa: ANN001, ANN201
                ident = int.from_bytes(queries[0][0:2], "big")
                return [
                    (b"\x00\x01\x02", "10.0.0.9"),
                    (response_with_ident(self.records, ident), self.source_ip),
                ]

        assert len(discover(MDNS, timeout=0.01, source=Garbage(server_records()))) == 1

    def test_the_subtype_query_asks_for_the_sub_name(self) -> None:
        source = FakeSource(server_records())
        discover(MDNS, timeout=0.01, subtype="derp", source=source)
        assert source.queries[0][0] == "derp._sub._smartenergy._tcp.local"

    def test_the_function_set_path_is_exposed_for_a_subtype_hit(self) -> None:
        """§7.6 b) 5): use the path from the subtype response directly."""
        txt = {"txtvers": "1", "dcap": "/dcap", "path": "/derp", "https": "443", "level": "-S2"}
        servers = discovered(server_records(txt=txt, subtype="derp"), subtype="derp")
        assert servers[0].function_set_path == "/derp"

    def test_two_transports_do_not_report_one_server_twice(self) -> None:
        """The same endpoint answering on mDNS and xmDNS is one server."""
        source = FakeSource(server_records())
        servers = discover_all(transports_for("both"), timeout=0.01, source=source)
        assert len(servers) == 1

    def test_both_queries_mdns_first(self) -> None:
        assert [t.name for t in transports_for("both")] == ["mdns", "xmdns"]

    def test_off_selects_no_transport(self) -> None:
        assert transports_for("off") == ()


class TestClientSequence:
    """Annex C Table C.1: ask for your own EndDevice, then for any server."""

    def test_the_sfdi_query_is_tried_first(self) -> None:
        source = FakeSource(server_records())
        discover_for_client([MDNS], sfdi=222222222228, timeout=0.01, source=source)
        assert source.queries[0][0].startswith("edev-222222222228._sub.")

    def test_a_generic_query_follows_when_no_edev_answers(self) -> None:
        class OnlyGeneric(FakeSource):
            def exchange(self, transport, queries, timeout, interface):  # noqa: ANN001, ANN201
                name, _ = read_name(queries[0], 12)
                self.queries.append((".".join(x.decode() for x in name), queries[0]))
                if b"_sub" in queries[0]:
                    return []
                ident = int.from_bytes(queries[0][0:2], "big")
                return [(response_with_ident(self.records, ident), self.source_ip)]

        source = OnlyGeneric(server_records())
        servers = discover_for_client([MDNS], sfdi=222222222228, timeout=0.01, source=source)
        assert len(servers) == 1
        assert [q[0] for q in source.queries] == [
            "edev-222222222228._sub._smartenergy._tcp.local",
            "_smartenergy._tcp.local",
        ]

    def test_no_generic_query_when_the_edev_query_answered(self) -> None:
        """The server holding this device's registration is the right one."""
        source = FakeSource(server_records(subtype="edev-222222222228"))
        discover_for_client([MDNS], sfdi=222222222228, timeout=0.01, source=source)
        assert all("_sub" in name for name, _ in source.queries)


# --------------------------------------------------------------------------
# The announcement side
# --------------------------------------------------------------------------


class TestAdvertisement:
    def build(self, **kwargs) -> ServiceAdvertisement:  # noqa: ANN003
        defaults = {"lfdi": "a" * 40, "sfdi": 11111, "port": 8443, "version": "0.5.0"}
        return build_advertisement(**{**defaults, **kwargs})

    def test_the_instance_name_ends_with_a_padded_sfdi(self) -> None:
        """§7.2 makes the SFDI what keeps the name unique."""
        assert self.build().instance == b"py20305-000000011111"

    def test_sfdi_labels_are_twelve_digits(self) -> None:
        assert sfdi_label(11111) == "000000011111"
        with pytest.raises(ValueError, match="12 digits"):
            sfdi_label(1234567890123)

    def test_the_default_service_is_not_the_registered_one(self) -> None:
        """Claiming _smartenergy._tcp would make clients treat us as a server."""
        assert self.build().service == (b"_py20305", b"_tcp")

    def test_identity_is_published_in_the_txt_record(self) -> None:
        txt = self.build().txt
        assert txt["txtvers"] == "1"
        assert txt["lfdi"] == "a" * 40
        assert txt["sfdi"] == "000000011111"

    def test_extra_txt_keys_are_carried(self) -> None:
        assert self.build(extra_txt={"site": "lab"}).txt["site"] == "lab"

    def test_an_oversized_instance_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="1-63 bytes"):
            self.build(instance="x" * 64)

    @pytest.mark.parametrize("service", ["_py20305", "py20305._tcp", "_py20305._sctp", "a.b.c"])
    def test_a_malformed_service_name_is_rejected(self, service: str) -> None:
        with pytest.raises(ValueError):
            validate_service(service)

    def test_the_record_set_is_complete_and_ordered(self) -> None:
        records = self.build().records(MDNS, ["192.168.1.5"])
        assert [r.rtype for r in records] == [TYPE_PTR, TYPE_SRV, TYPE_TXT, TYPE_A]
        assert records[0].name == (b"_py20305", b"_tcp", b"local")
        assert records[1].name == (b"py20305-000000011111", b"_py20305", b"_tcp", b"local")

    def test_xmdns_publishes_the_same_records_under_site(self) -> None:
        """The edition difference is a domain, not a record format."""
        local = self.build().records(MDNS, ["192.168.1.5"])
        site = self.build().records(XMDNS, ["192.168.1.5"])
        assert [r.rtype for r in local] == [r.rtype for r in site]
        assert local[0].name[-1] == b"local"
        assert site[0].name[-1] == b"site"

    def test_the_shared_ptr_does_not_carry_the_cache_flush_bit(self) -> None:
        """Flushing a shared name would erase other hosts' answers (§10.2)."""
        records = self.build().records(MDNS, ["192.168.1.5"])
        assert records[0].unique is False
        assert all(r.unique for r in records[1:])

    def test_the_flush_bit_reaches_the_wire_only_for_unique_records(self) -> None:
        message = decode_message(encode_response(self.build().records(MDNS, ["192.168.1.5"])))
        by_type = {r.rtype: r for r in message.records}
        raw = message.raw
        # Re-read the class field including the top bit, which decode masks off.
        def raw_class(record):  # noqa: ANN001, ANN202
            return int.from_bytes(raw[record.rdata_offset - 8 : record.rdata_offset - 6], "big")

        assert not raw_class(by_type[TYPE_PTR]) & 0x8000
        assert raw_class(by_type[TYPE_SRV]) & 0x8000

    def test_an_ipv6_address_publishes_a_quad_a_record(self) -> None:
        records = self.build().records(MDNS, ["2001:db8::5"])
        assert records[-1].rtype == TYPE_AAAA

    def test_a_goodbye_is_the_same_records_at_ttl_zero(self) -> None:
        """RFC 6762 §10.1: without it a receiver caches a stopped client."""
        records = self.build().records(MDNS, ["192.168.1.5"])
        goodbye = [r.with_ttl(0) for r in records]
        assert all(r.ttl == 0 for r in goodbye)
        assert [r.rdata for r in goodbye] == [r.rdata for r in records]

    def test_what_it_publishes_is_readable_as_dns_sd(self) -> None:
        """Round trip: encode the announcement, decode it as a client would."""
        message = decode_message(encode_response(self.build().records(MDNS, ["192.168.1.5"])))
        txt = next(r for r in message.records if r.rtype == TYPE_TXT)
        assert decode_txt(record_rdata(message, txt))["sfdi"] == "000000011111"
        srv = next(r for r in message.records if r.rtype == TYPE_SRV)
        assert decode_srv(message, srv)[0] == 8443
