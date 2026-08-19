#!/usr/bin/env python3
"""Write a throwaway configuration and certificate into a directory.

Used to exercise the container against a configuration it has to actually
read -- `--check` resolves the certificate and derives the LFDI, so a stub file
is not enough. The certificate is self-signed, valid for a year, and connects
to nothing; it exists so the client has real material to parse.

    python scripts/make_test_config.py <directory>
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

CONFIG = """server:
  url: https://server.example.com:8443
tls:
  client_cert: client.pem
  client_key: client.key
  ca_cert: ca.pem
devices:
  - type: print_demo
    lfdi: "abcdef0123456789abcdef0123456789abcdef01"
"""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "py20305-test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )

    pem = cert.public_bytes(serialization.Encoding.PEM)
    (out / "client.pem").write_bytes(pem)
    # The client verifies the server against this. Self-signed, so the
    # certificate is its own issuer and the same bytes serve as both.
    (out / "ca.pem").write_bytes(pem)
    (out / "client.key").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    (out / "client.yaml").write_text(CONFIG, encoding="utf-8")

    print(f"wrote a test configuration and certificate to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
