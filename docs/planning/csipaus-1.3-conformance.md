# CSIP-AUS 1.3: the extension namespace and the tariff RateComponentLink

## Problem

Two gaps keep py20305 from working with a CSIP-AUS 1.3 server. Both were found by
third-party interoperability tests against Envoy 2.5 and reproduce on v0.16.1.

1. **Extensions are written in the pre-1.3 namespace.** py20305 writes
   `doeModesSupported`, `doeModesEnabled` and `ConnectionPoint` in
   `https://csipaus.org/ns` (`models/csipaus/csipaus_ext.py`,
   `xml/serialization.py:NS_CSIPAUS`). CSIP-AUS 1.3 (`csipaus-ext-v1.3.xsd`)
   moved them to `https://csipaus.org/ns/v1.3`. A 1.3 server refuses the
   DERCapability PUT with 400, or drops the extension silently.
2. **A CSIP-AUS 1.3 TimeTariffIntervalList does not parse.**
   `csipaus-core-v1.3.xsd` extends TimeTariffInterval with a mandatory
   `RateComponentLink` in the IEEE 2030.5 namespace. The generated model has no
   such field, and the `xs:any` wildcard takes only other namespaces, so parsing
   fails with `Unknown property ... TimeTariffInterval:RateComponentLink` and the
   client finds no tariff intervals.

Reading the DOE limits is unaffected: `models/csipaus/elements.py` reads them by
local name, so limits in either namespace already apply.

## Decisions

- **D1. Write in the namespace the server declares.** Discovery records the
  CSIP-AUS namespace URI that the DeviceCapability declares, and serialization
  writes the extensions in it. A server on 1.3 gets `ns/v1.3`, and a server on an
  earlier version keeps getting `https://csipaus.org/ns`. Rejected: switching the
  constant to 1.3 (breaks pre-1.3 servers) and a configuration key (the operator
  would have to know the server's version, which the server already states).
- **D2. Rewrite the declaration, do not retype the models.** The typed extension
  models keep their pre-1.3 `Meta.namespace`. `to_xml` renders as today, then
  replaces the URI on the `csipaus` prefix declaration. The models stay
  registered for parsing, and no second set of classes is needed.
- **D3. RateComponentLink is an optional field on hand-written subclasses.**
  `models/csipaus/tariff.py` subclasses the generated TimeTariffInterval and
  TimeTariffIntervalList to add `rate_component_link: RateComponentLink | None`,
  citing `csipaus-core-v1.3.xsd`, and discovery parses tariff lists with them.
  The generated models stay as the schemas define them. The link is optional
  because plain IEEE 2030.5 servers do not send it. Rejected: adding the field
  to the generated `sep.py` (the models are generated, and a regeneration would
  drop it), regenerating from the official 1.3 schema (it makes the link and
  `ConsumptionTariffIntervalListSummary` mandatory, so lists from other servers
  would fail to parse; schema files are also ask-first), and lenient parsing
  (hides real schema errors from other servers).
- **D4. Out of scope: copying the detected mode to the HTTP client.**
  `CsipClient.connect()` leaves `Sep2Client.csip_aus_mode` False after discovery
  detects CSIP-AUS, so a standalone client sends no extensions at all. Callers
  (applications that embed the client, such as an interop adapter) set it
  themselves today. It is tracked in #79, because the fix interacts
  with callers' on/off overrides and with rediscovery.

## Changes

- `client/discovery.py`: read the CSIP-AUS namespace declarations of the raw
  DeviceCapability body with the XML parser (entities and network access off),
  prefer the `csipaus` prefix, then another prefix, then a default namespace,
  accept only `https://csipaus.org/ns` and paths under it, and set
  `client.csipaus_namespace`. Keep setting `state.csip_aus_mode` as today.
  Parse TimeTariffIntervalLists with the subclass from D3.
- `client/http.py`: a `csipaus_namespace` property (default
  `https://csipaus.org/ns`), passed to `to_xml` on POST and PUT.
- `telemetry/der_resource_manager.py`: pass the namespace on the DERCapability
  and DERSettings PUTs.
- `xml/serialization.py`: `to_xml(..., csipaus_namespace=NS_CSIPAUS)`.
- `models/csipaus/tariff.py`: the subclasses with the optional
  `rate_component_link`.
- Documentation and a changelog fragment.

## Acceptance criteria

- A DERCapability PUT to a server whose DeviceCapability declares
  `https://csipaus.org/ns/v1.3` carries `doeModesSupported` in that namespace,
  and the old URI does not appear in the body.
- Against a server declaring `https://csipaus.org/ns`, the body is unchanged.
- A CSIP-AUS 1.3 TimeTariffIntervalList parses, and each interval exposes its
  RateComponentLink href. A list without the link still parses.
- Each test fails on `origin/main` before the change.
- `pytest tests -q`, `ruff check`, `mypy src/py20305` and
  `python scripts/build_changelog.py --check` pass.
