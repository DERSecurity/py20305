# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately rather than in a public issue.

Use GitHub's [private vulnerability
reporting](https://github.com/DERSecurity/py20305/security/advisories/new)
on this repository. It goes to the maintainers and stays private until an
advisory is published.

Please include what you have: affected version, what an attacker can do, and a
reproduction if you have one. You will get an acknowledgment within a few
working days.

## What is in scope

This is a protocol client, so the interesting surface is what it accepts from
the network and how it handles credentials:

- Parsing IEEE 2030.5 XML from a server — malformed input, entity expansion,
  schema-confusion.
- TLS handling: certificate verification, hostname checking, cipher selection.
- The notification listener, which accepts inbound HTTP from a server.
- Handling of private keys and certificate material.
- The management API, when enabled.

Two things are configurable weakenings rather than vulnerabilities, and are
documented as such: `check_hostname=False` and the notification listener's
`off` client-certificate mode. A report that one of them reduces security when
deliberately enabled is not a finding. A report that either is applied when
*not* configured very much is.

## Supported versions

| Version | Supported |
|---|---|
| latest release on [PyPI](https://pypi.org/project/py20305/) | yes |
| anything older | no -- upgrade first |

Until 1.0, fixes land on the latest release only; a report against an older
version is still welcome, but the fix ships as a new release rather than a
backport.
