"""DNS-SD for this client: the records, and the multicast transport carrying them.

DNS-SD (IETF RFC 6763) and mDNS (IETF RFC 6762) are separate layers and this
package keeps them separate. DNS-SD is the naming convention -- a service
instance is a PTR record naming an SRV/TXT pair, with attributes in the TXT
record. mDNS is one way to put those records on a network that has no DNS
server, by multicasting them.

IEEE 2030.5 Clause 7 uses both, and §7.1 differs between editions over which
multicast transport is normative: the 2018 edition on xmDNS (site-local
``FF05::FB``, the ``.site`` domain) and the 2023 edition on plain mDNS
(link-local, ``.local``), which deprecates xmDNS while keeping it normative for
backward compatibility. The records are identical in both, so the choice is a
transport rather than a record format -- which is why it is one setting.

:mod:`~py20305.client.dnssd.wire` holds the record format and the transport
table. :mod:`~py20305.client.dnssd.discover` is the client half the standard
requires, querying for IEEE 2030.5 servers (§6.9.2, §8.3.3).
:mod:`~py20305.client.dnssd.advertise` publishes this client's own service,
which the standard does not describe -- it gives the advertising role to
servers -- and which exists so an operator can find their own clients on a
network they run.
"""

from py20305.client.dnssd.advertise import (
    DEFAULT_SERVICE,
    MulticastAdvertiser,
    ServiceAdvertisement,
    build_advertisement,
    local_address_for,
    sfdi_label,
    validate_service,
)
from py20305.client.dnssd.discover import (
    KNOWN_SUBTYPES,
    SERVICE_LABELS,
    DiscoveredServer,
    SocketPacketSource,
    discover,
    discover_all,
    discover_for_client,
    edev_subtype,
    service_labels,
    validate_subtype,
)
from py20305.client.dnssd.wire import (
    MDNS,
    TRANSPORTS,
    XMDNS,
    DnsDecodeError,
    MulticastTransport,
    Question,
    Record,
    transports_for,
)

__all__ = [
    "DEFAULT_SERVICE",
    "KNOWN_SUBTYPES",
    "MDNS",
    "SERVICE_LABELS",
    "TRANSPORTS",
    "XMDNS",
    "DiscoveredServer",
    "DnsDecodeError",
    "MulticastAdvertiser",
    "MulticastTransport",
    "Question",
    "Record",
    "ServiceAdvertisement",
    "SocketPacketSource",
    "build_advertisement",
    "discover",
    "discover_all",
    "discover_for_client",
    "edev_subtype",
    "local_address_for",
    "service_labels",
    "sfdi_label",
    "transports_for",
    "validate_service",
    "validate_subtype",
]
