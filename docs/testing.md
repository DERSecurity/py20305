# Testing

Two suites, answering different questions.

## Unit tests

```bash
pytest tests -q
```

The unit suite runs with no network, no Docker, in about a minute. It asserts
this client behaves the way we believe IEEE 2030.5 requires — against
transports written here.

That last clause is the limit of what they can prove. A misreading of the
standard that is consistent between the client and its own test doubles passes
every one of them.

## End-to-end tests

```bash
python scripts/e2e_server.py up      # start a real server (needs Docker)
eval "$(python scripts/e2e_server.py env)"
pytest tests/e2e -v
python scripts/e2e_server.py down
```

These run against [envoy](https://github.com/bsgip/envoy), the open-source
CSIP-AUS utility server from the Australian National University's Battery
Storage and Grid Integration Program — the implementation ANU's CSIP-AUS
certification program runs on. Its XML, headers, status codes and resource
layout were decided by someone reading the same specification independently,
which is what makes disagreement visible.

They are excluded from the default `pytest` run and skip if no server is
configured, so neither the absence of Docker nor the absence of a server
affects the unit suite.

`scripts/e2e_server.py` clones envoy at a pinned tag, builds its image and
brings up its demo stack: nginx terminating TLS on port 8443 and validating
client certificates, PostgreSQL, the server, and its notification worker. The
test certificates it generates are what the tests present.

### Pointing them somewhere else

Nothing in the tests is specific to that server. They read two variables:

| Variable | Meaning |
|---|---|
| `PY20305_E2E_SERVER_URL` | Base URL of the server |
| `PY20305_E2E_CERT_DIR` | Directory holding the CA and a client certificate and key |
| `PY20305_E2E_CLIENT_CERT` | Certificate stem, default `testdevice1` |
| `PY20305_E2E_CA` | CA filename, default `testca.crt` |
| `PY20305_E2E_CHECK_HOSTNAME` | Hostname verification, on by default and left on locally. Set `0` only for a server reached by an address its certificate does not name |

So the same suite can be pointed at a utility's own server during an interop
exercise, which is the situation it is most valuable in and the one no fake can
reproduce.

### What they cover

- **Transport** — the mutual-TLS handshake completes and the server serves a
  DeviceCapability. Anything wrong with the cipher list or certificate chain
  fails here rather than somewhere less obvious.
- **Schema** — XML from a foreign implementation validates against the XSDs
  this package ships. A schema that only accepts XML we generate is worth
  little; this is the assertion that says otherwise.
- **Discovery** — the client walks the server's links rather than assuming a
  resource layout.
- **Registration** — in-band registration produces an EndDevice the server
  serves back; registering twice is refused, so a restart cannot create a
  second EndDevice for one physical device; and registering *another*
  identity is refused by the server, because IEEE 2030.5 ties an EndDevice to
  the certificate presenting it.
- **Control path** — a registered device is given a DERProgram, and a full
  poll cycle completes.

### A note on Windows

Git for Windows checks shell scripts out with CRLF by default, and envoy's
containers run several. The container's `sh` fails on the trailing carriage
return with a message that says nothing about line endings. The script clones
with `core.autocrlf=false` and normalizes anything that slips through, so this
should not surface — but that is what it is guarding against.
