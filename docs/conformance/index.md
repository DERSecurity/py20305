# CSIP test results

A recorded run of the CSIP client test suite against py20305, kept here as
interop evidence: what the client was asked to do, and how it answered.

[`2026-08-26_17-25-36-220_client_test.xlsx`](2026-08-26_17-25-36-220_client_test.xlsx)
— 45 tests, all passing.

- **Summary** — one row per test with its result.
- **CORE Tests** — polling, time, end device, function set assignments, DER
  program and control, settings, randomized events, responses.
- **BASIC Tests** — DER identification, group management, ride-through, the
  autonomous control functions, and the DERControl overlap and precedence
  cases.
- **COMM Tests** — discovery, and the certificate chain and validation cases.

The detail sheets carry the request and response bodies each test exchanged,
so a disagreement about what the client sent can be settled from the file
rather than reconstructed.

## This is not a certification

A passing run here says the client behaved correctly against this suite on
this date. It is SunSpec CSIP conformant, but it does not make a
product built on py20305 certified. Certification is a property of the
assembled product, tested as a whole. See
[Certification](../index.md#certification) for the process.

## Reading the certificate columns

The COMM-004 rows name the certificate each subtest used by filename only.
The paths those files came from are deliberately not recorded here.
