# Finding servers, and being found

This client does two things with multicast DNS-SD, and they are separate
features with separate settings:

- **Discovery** locates an IEEE 2030.5 server on the local network, so you do
  not have to configure its URL. This is what the standard asks a client to do.
- **Announcement** publishes this client on the local network, so you can find
  *it*. The standard does not describe this; it is here because operators need
  to know what is running on a network they own.

Both are off the beaten path for a utility deployment. If your server is
reached over the internet, neither one applies and you should configure
`server.url` directly, which is what CSIP expects. Multicast does not cross a
router, so a server that is not on this network segment will never answer.

## Discovery

IEEE 2030.5 §6.9.2 puts this on the client: *"Clients SHALL locate local
services by performing DNS service discovery (DNS-SD) queries to the local
network."* §7.6 then gives three ways to find a server, and a configured URL is
one of them, so discovery only runs when you have not named a server.

```yaml
server:
  # No url: the client finds a server at startup.

discovery:
  enabled: true          # default
  transport: mdns        # mdns | xmdns | both
  timeout_seconds: 3.0
```

Setting `server.url` turns discovery off for that deployment without changing
any of these settings, because a URL is an answer to the same question.

### See what is out there

```console
$ py20305 --config client.yaml --discover
querying _smartenergy._tcp.local over mdns (224.0.0.251, ff02::fb), 3s
asking first for this client's own EndDevice, SFDI 000001111114

1 server(s):

  127-edev-000001111114._smartenergy._tcp.local
    DeviceCapability : https://192.168.1.40:8443/dcap
    transport        : mdns
    schema level     : -S2 (IEEE 2030.5-2023)
```

This queries and exits. It never connects, so it is safe to run against a
production configuration.

### What it asks for

Annex C describes the order, and the client follows it. First a subtype query
for the server that already holds *this client's* EndDevice, keyed by its SFDI:

```
edev-000001111114._sub._smartenergy._tcp.local    PTR
```

If nothing answers, a second query for any server at all:

```
_smartenergy._tcp.local    PTR
```

Asking the narrow question first matters on a network with more than one
server: the one already holding your registration is the one to talk to, and a
generic query returns it alongside servers that have never heard of this
device. Set `discovery.subtype` to ask about a function set instead, using one
of the §7.5 Table 17 strings such as `derp` or `mup`.

### Which edition the server speaks

The TXT record carries a `level` key, and §5.7 ties its value to an edition:
`S1` is IEEE 2030.5-2018 and `S2` is IEEE 2030.5-2023. A discovered server
therefore tells you which schema it implements, and `--discover` prints it.
Set `server.server_2018_compat` to match.

### mDNS or xmDNS

The two editions of the standard disagree about which multicast transport
carries the exchange, so `transport` selects one:

| Setting | Transport | Domain | Group | Status |
|---|---|---|---|---|
| `mdns` | Multicast DNS (RFC 6762) | `.local` | `224.0.0.251`, `ff02::fb` | Normative in IEEE 2030.5-2023 |
| `xmdns` | Extended Multicast DNS | `.site` | `ff05::fb` | Normative in 2018, deprecated in 2023 |
| `both` | Each in turn, mDNS first | | | |

The records are identical either way. Only the multicast group and the domain
differ, which is why this is one setting and not a schema version.

`xmdns` is worth trying only against equipment built to the 2018 edition. It
rests on an IETF draft that expired without being adopted, it is IPv6-only, and
2023 marks it *"DEPRECATED but still normative to maintain backward
compatibility with previous revisions."*

## Announcement

The client can publish itself as a DNS-SD service so that an inventory tool, a
commissioning laptop, or a passive network monitor on the same segment can see
it without probing.

```yaml
advertise:
  transport: mdns        # mdns | xmdns | both | off
```

It publishes a PTR, an SRV/TXT pair and address records under
`py20305-<SFDI>._py20305._tcp.local`, announces three times at startup per
RFC 6762 §8.3, answers later queries for those names, and sends a goodbye
record on shutdown so a listener does not keep a stopped client in its cache.

Responses are rate limited. Multicast answers are capped at one per second per
interface, which RFC 6762 §6 states as a MUST NOT, and unicast answers are
capped at ten per second. The second limit is not in the standard: a UDP source
address is trivially spoofed, and without a ceiling a responder is a small
amplifier pointed at whoever the attacker names. One exchange is all a genuine
querier needs.

The TXT record carries `txtvers=1`, the client's `lfdi` and `sfdi`, and the
package version.

### Two things to know before turning it on

**This is not part of IEEE 2030.5.** The standard gives the advertising role to
servers and the querying role to clients; a client publishing a record about
itself is outside its scope. That is why the default service name is
`_py20305._tcp` rather than the registered `_smartenergy._tcp`. Announcing
under the registered name would make every conformant client on the link
believe it had found a server and then fail against a DeviceCapability resource
this process does not serve. Set `advertise.service` if a test network wants
exactly that behavior.

**The announcement discloses identity.** The `lfdi` and `sfdi` keys are derived
from the certificate the utility issued, so anything on the segment can build
an inventory of clients keyed by their utility identity. Those values already
cross the wire in the clear on every TLS handshake this client makes, so this
is not a new secret, but it does make collecting them considerably easier.
Announcement is off unless you enable it.

### What gets advertised

The service record needs a port, so announcement names one of these, in order:

1. `advertise.port`, if set.
2. The management API port, if the API is enabled.
3. The notification server port, if subscriptions are enabled.

With none of them, the client logs why and stays quiet. A service record
pointing at a port nothing listens on is worse than no record at all.

A port is inferred only when its listener is bound to an address something
else can reach. `api.host` defaults to `127.0.0.1`, so an API left on its
default is not advertised: the SRV record carries the LAN address, and pairing
it with a loopback-only port publishes an endpoint that refuses every
connection. Bind the API to `0.0.0.0`, or set `advertise.port` explicitly --
which is taken on trust, for the case where a proxy fronts a loopback
listener.

Announcement needs UDP port 5353. If something else on the host already holds
it, which on a desktop usually means a system mDNS responder, the client
reports the transport unavailable rather than announcing from another port:
RFC 6762 has receivers ignore responses that do not come from 5353, so the
packets would be discarded while the log claimed success.

## The command-line switch

`--multicast-transport` overrides both halves at once, which is useful for a
one-off run without editing the configuration:

```console
$ py20305 --config client.yaml --multicast-transport off
```

`off` silences announcement and disables discovery, so a configuration with no
`server.url` is rejected rather than starting a client that cannot reach
anything.

## Multi-homed hosts

A gateway with a utility uplink and a device LAN sends multicast over whichever
interface the default route names, which is often the wrong one. Both sections
take an `interface` setting: an address for IPv4, an interface name for IPv6.

```yaml
discovery:
  interface: 192.168.1.10
```

Announcement publishes only an address a receiver can actually connect to, so a
host with no routable IPv6 address announces over IPv4 alone rather than
publishing a link-local `fe80::` address nothing off-host can dial. §7.1
requires the same of xmDNS, which "SHALL use global addresses or Unique Local
Addresses (IETF RFC 4193)".
