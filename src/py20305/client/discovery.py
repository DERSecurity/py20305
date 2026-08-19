"""Hierarchical IEEE 2030.5 resource discovery."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from py20305.client.errors import Sep2PayloadError, Sep2ProtocolError
from py20305.client.http import Sep2Client
from py20305.client.poll_rate import DEFAULT_POLL_RATE, normalize_poll_rate
from py20305.client.state import (
    DerProgramState,
    DeviceMapping,
    DiscoveredState,
    EndDeviceState,
    SelfDeviceState,
    TariffProfileState,
    TariffRateComponentState,
)
from py20305.client.timebase import observe_time_resource
from py20305.models.sep.sep import (
    DefaultDercontrol,
    Der1,
    DercontrolList,
    DercurveList,
    Derlist,
    DerprogramList,
    DeviceCapability,
    EndDeviceList,
    FunctionSetAssignmentsList,
    RateComponentList,
    Registration,
    SelfDevice,
    SubscriptionList,
    TariffProfileList,
    Time,
    TimeTariffIntervalList,
)

logger = logging.getLogger(__name__)


def _absent_reason(status_code: int) -> str:
    """Log phrase for a benign no-representation status on an optional GET:
    404 (resource absent) vs 204 (present but No Content)."""
    return "returned no content (HTTP 204)" if status_code == 204 else "not found (HTTP 404)"


def _extract_href(link: Any) -> str | None:
    """Null-safe href extraction from a Link object."""
    if link is None:
        return None
    href: str | None = getattr(link, "href", None)
    return href or None


@dataclass(frozen=True)
class _DerChildHrefs:
    """The DER child resource hrefs a DERList walk yields."""

    settings: str | None = None
    capability: str | None = None
    status: str | None = None
    availability: str | None = None
    current_controls: str | None = None


def _first_href(hrefs: Iterable[str | None]) -> str | None:
    """First non-empty href in *hrefs*, or None."""
    return next((href for href in hrefs if href), None)


def _der_child_hrefs(ders: list[Der1], list_href: str, *, legacy_multi_der: bool) -> _DerChildHrefs:
    """Child hrefs for the DER a DERList represents.

    IEEE 2030.5-2023 permits exactly one entry: "More than one DER object
    SHALL NOT be included ... This single DER object represents the entire
    DER for the EndDevice" (Annex B, DERList, p. 250), and Section
    10.10.4.4.1 (p. 134) gives that entry a one-to-one relationship with
    SelfDevice and EndDevice. Sub-components belong in DERComponentList, not
    in extra DERList entries. So by default every href comes from the first
    entry, because composing them across entries would describe a DER that
    does not exist -- one instance's operating limit against another's
    nameplate.

    The exception is ``legacy_multi_der`` (``server_2018_compat``): previous
    revisions of the standard did allow several DER objects, and a 2018
    server may legitimately spread child links across them. There, fall back
    to later entries for links the first one omits, so upgrading this client
    does not silently drop a DERSettings or DERAvailability href that such a
    server does publish.
    """
    if not ders:
        return _DerChildHrefs()
    if len(ders) > 1:
        logger.warning(
            "DERList at %s holds %d DER entries; IEEE 2030.5-2023 permits one "
            "(sub-components belong in DERComponentList). %s",
            list_href,
            len(ders),
            (
                "server_2018_compat is set, so filling gaps from later entries"
                if legacy_multi_der
                else f"Using the first ({ders[0].href or 'unnamed'}) only"
            ),
        )
    candidates = ders if legacy_multi_der else ders[:1]
    return _DerChildHrefs(
        settings=_first_href(_extract_href(d.dersettings_link) for d in candidates),
        capability=_first_href(_extract_href(d.dercapability_link) for d in candidates),
        status=_first_href(_extract_href(d.derstatus_link) for d in candidates),
        availability=_first_href(_extract_href(d.deravailability_link) for d in candidates),
        current_controls=_first_href(_extract_href(d.current_dercontrols_link) for d in candidates),
    )


async def _discover_self_device(
    client: Sep2Client, dcap: DeviceCapability
) -> SelfDeviceState | None:
    """Discover the server's own SelfDevice and its DER child resources.

    Returns None when the server advertises no SelfDeviceLink, or when the
    link is advertised but the resource cannot be read -- SelfDevice is
    optional in IEEE 2030.5, nothing downstream of discovery depends on it,
    and no failure to read it may cost the caller its control path. Absence
    logs at warning; a real server fault logs at error. Both continue.
    """
    sdev_href = _extract_href(dcap.self_device_link)
    if not sdev_href:
        logger.debug("DeviceCapability has no SelfDeviceLink")
        return None

    try:
        sdev = await client.get(sdev_href, SelfDevice)
    except Sep2ProtocolError as exc:
        if exc.status_code in (404, 204):
            logger.warning(
                "SelfDevice at %s %s, skipping", sdev_href, _absent_reason(exc.status_code)
            )
        else:
            logger.error("SelfDevice at %s failed to read, skipping: %s", sdev_href, exc)
        return None
    except Sep2PayloadError as exc:
        logger.error("SelfDevice at %s did not parse, skipping: %s", sdev_href, exc)
        return None

    sdev_state = SelfDeviceState(device=sdev, href=sdev_href, subscribable=bool(sdev.subscribable))

    der_href = _extract_href(sdev.derlist_link)
    if not der_href:
        logger.info("Discovered SelfDevice at %s (no DERListLink)", sdev_href)
        return sdev_state

    try:
        der_list = await client.get(der_href, Derlist)
    except Sep2ProtocolError as exc:
        if exc.status_code in (404, 204):
            logger.warning(
                "SelfDevice DER list at %s %s, skipping",
                der_href,
                _absent_reason(exc.status_code),
            )
        else:
            logger.error("SelfDevice DER list at %s failed to read, skipping: %s", der_href, exc)
        return sdev_state
    except Sep2PayloadError as exc:
        logger.error("SelfDevice DER list at %s did not parse, skipping: %s", der_href, exc)
        return sdev_state

    sdev_state.ders = list(der_list.der)
    if sdev_state.ders:
        sdev_state.der_subscribable = bool(sdev_state.ders[0].subscribable)
    hrefs = _der_child_hrefs(sdev_state.ders, der_href, legacy_multi_der=client.server_2018_compat)
    sdev_state.der_settings_href = hrefs.settings
    sdev_state.der_capability_href = hrefs.capability
    sdev_state.der_status_href = hrefs.status
    sdev_state.der_availability_href = hrefs.availability

    logger.info(
        "Discovered SelfDevice at %s with %d DER(s), settings=%s",
        sdev_href,
        len(sdev_state.ders),
        sdev_state.der_settings_href or "none",
    )
    return sdev_state


async def _verify_registration(
    client: Sep2Client,
    reg_href: str,
    lfdi_hex: str,
    expected_pins: dict[str, int],
) -> None:
    """Fetch Registration resource and verify PIN against expected value.

    Args:
        client: The Sep2Client for HTTP operations.
        reg_href: The href of the Registration resource.
        lfdi_hex: The hex LFDI string for this device.
        expected_pins: Mapping from LFDI hex -> expected PIN.
    """
    try:
        reg = await client.get(reg_href, Registration)
    except Sep2ProtocolError as exc:
        if exc.status_code in (404, 204):
            logger.debug("Registration at %s %s", reg_href, _absent_reason(exc.status_code))
            return
        raise

    server_pin = reg.p_in.value
    expected = expected_pins.get(lfdi_hex)
    if expected is None:
        logger.debug("No expected PIN configured for LFDI %s", lfdi_hex[:16])
        return
    if server_pin != expected:
        logger.warning(
            "Registration PIN mismatch for LFDI %s: server=%d expected=%d",
            lfdi_hex[:16],
            server_pin,
            expected,
        )
    else:
        logger.info("Registration PIN verified for LFDI %s", lfdi_hex[:16])


async def _discover_program(client: Sep2Client, derp: Any, derp_href: str) -> DerProgramState:
    """Discover a single DERProgram's sub-resources (DDERC, DERC list, curves)."""
    primacy = derp.primacy.value if derp.primacy is not None else 0
    derp_state = DerProgramState(program=derp, href=derp_href, primacy=primacy)

    # Default DER control
    dderc_href = _extract_href(derp.default_dercontrol_link)
    if dderc_href:
        try:
            dderc = await client.get(dderc_href, DefaultDercontrol)
            derp_state.default_dercontrol = dderc
            if hasattr(dderc, "subscribable") and dderc.subscribable:
                derp_state.dderc_subscribable = True
        except Sep2ProtocolError as exc:
            # 204 No Content (some servers return it for an optional resource that
            # has a link but no content -- e.g. a DERProgram with a
            # DefaultDERControlLink but no default control set) is treated like a
            # 404: the optional resource is simply absent, not a fatal error.
            # get() raises on any non-200, so an unforgiven 204 here would
            # propagate out of discovery and crash-loop the client.
            if exc.status_code in (404, 204):
                logger.debug(
                    "DefaultDERControl at %s %s", dderc_href, _absent_reason(exc.status_code)
                )
            else:
                raise
        except Sep2PayloadError as exc:
            logger.warning("Skipping DDERC for program %s: %s", derp_href, exc)

    # DER control list
    derc_list_href = _extract_href(derp.dercontrol_list_link)
    if derc_list_href:
        try:
            derc_pages = await client.get_list(derc_list_href, DercontrolList)
            for derc_page in derc_pages:
                derp_state.der_controls.extend(derc_page.dercontrol)
                if hasattr(derc_page, "subscribable") and derc_page.subscribable:
                    derp_state.derc_list_subscribable = True
        except Sep2PayloadError as exc:
            logger.warning("Skipping DERControl list for program %s: %s", derp_href, exc)
        except Exception:
            logger.warning(
                "Failed to parse DERControl list at %s, skipping controls for program %s",
                derc_list_href,
                derp_href,
                exc_info=True,
            )

    # DER curve list
    curve_list_href = _extract_href(derp.dercurve_list_link)
    if curve_list_href:
        try:
            curve_pages = await client.get_list(curve_list_href, DercurveList)
            for curve_page in curve_pages:
                derp_state.der_curves.extend(curve_page.dercurve)
        except Sep2PayloadError as exc:
            logger.warning("Skipping DERCurve list for program %s: %s", derp_href, exc)
        except Exception:
            logger.warning(
                "Failed to parse DERCurve list at %s, skipping curves for program %s",
                curve_list_href,
                derp_href,
                exc_info=True,
            )

    return derp_state


async def _discover_rate_component(client: Sep2Client, rc: Any) -> TariffRateComponentState:
    """Discover a RateComponent's TimeTariffInterval schedule.

    The caller guarantees ``rc.href`` is set.
    """
    rc_state = TariffRateComponentState(rate_component=rc, href=rc.href)
    tti_list_href = _extract_href(rc.time_tariff_interval_list_link)
    if tti_list_href:
        rc_state.tti_list_href = tti_list_href
        try:
            tti_pages = await client.get_list(tti_list_href, TimeTariffIntervalList)
            for tti_page in tti_pages:
                rc_state.time_tariff_intervals.extend(tti_page.time_tariff_interval)
                if getattr(tti_page, "subscribable", None):
                    rc_state.tti_list_subscribable = True
        except Sep2PayloadError as exc:
            logger.warning("Skipping TimeTariffInterval list at %s: %s", tti_list_href, exc)
        except Exception:
            logger.warning(
                "Failed to parse TimeTariffInterval list at %s", tti_list_href, exc_info=True
            )
    return rc_state


async def _discover_tariff_profile(
    client: Sep2Client, tp: Any, tp_href: str, fsa_href: str | None
) -> TariffProfileState:
    """Discover a single TariffProfile's RateComponents and interval schedule.

    Prices (ConsumptionTariffInterval) are not fetched here -- they are pulled
    on demand when an interval is processed, via each interval's
    ``consumption_tariff_interval_list_link``.
    """
    primacy = tp.primacy.value if tp.primacy is not None else 0
    tp_state = TariffProfileState(
        profile=tp, href=tp_href, primacy=primacy, discovered_from_fsa_href=fsa_href
    )
    rc_list_href = _extract_href(tp.rate_component_list_link)
    if rc_list_href:
        try:
            rc_pages = await client.get_list(rc_list_href, RateComponentList)
            for rc_page in rc_pages:
                for rc in rc_page.rate_component:
                    if not rc.href:
                        continue
                    rc_state = await _discover_rate_component(client, rc)
                    tp_state.rate_components.append(rc_state)
        except Sep2PayloadError as exc:
            logger.warning("Skipping RateComponent list for tariff %s: %s", tp_href, exc)
        except Exception:
            logger.warning("Failed to parse RateComponent list at %s", rc_list_href, exc_info=True)
    return tp_state


async def _discover_tariffs_for_fsa(client: Sep2Client, fsa: Any, state: DiscoveredState) -> None:
    """Follow an FSA's ``TariffProfileListLink`` and populate ``state.tariff_profiles``.

    Opt-in: the caller only invokes this when ``state.pricing_enabled`` is set.
    A missing link is a no-op (the FSA doesn't advertise pricing).
    """
    tp_list_href = _extract_href(fsa.tariff_profile_list_link)
    if not tp_list_href:
        return
    try:
        tp_pages = await client.get_list(tp_list_href, TariffProfileList)
    except Sep2ProtocolError as exc:
        if exc.status_code == 404:
            logger.warning("TariffProfileList at %s not found, skipping", tp_list_href)
            return
        raise
    except Sep2PayloadError as exc:
        logger.warning("Skipping TariffProfileList at %s: %s", tp_list_href, exc)
        return

    for tp_page in tp_pages:
        if "tariff" not in state.poll_rates and tp_page.poll_rate is not None:
            state.poll_rates["tariff"] = normalize_poll_rate(
                tp_page.poll_rate, resource_key="tariff"
            )
        for tp in tp_page.tariff_profile:
            # The tariff tree is global (every FSA's TariffProfileListLink points
            # at the same /tp), so skip profiles already discovered from an
            # earlier FSA: avoids re-fetching the RateComponent/interval subtree
            # and keeps discovered_from_fsa_href deterministic (first FSA wins).
            if not tp.href or tp.href in state.tariff_profiles:
                continue
            state.tariff_profiles[tp.href] = await _discover_tariff_profile(
                client, tp, tp.href, fsa.href
            )


async def _discover_programs_from_pages(
    client: Sep2Client,
    state: DiscoveredState,
    device_mapping: DeviceMapping,
    derp_pages: list[Any],
    edev_href: str,
    fsa_href: str | None,
) -> None:
    """Discover DER programs from paginated program list results."""
    for derp_page in derp_pages:
        # Capture poll rate from first page if not already set
        if "derp" not in state.poll_rates and derp_page.poll_rate is not None:
            state.poll_rates["derp"] = normalize_poll_rate(derp_page.poll_rate, resource_key="derp")

        for derp in derp_page.derprogram:
            derp_href = derp.href
            if not derp_href:
                continue

            device_mapping.add(derp_href, edev_href)

            if derp_href in state.der_programs:
                continue

            derp_state = await _discover_program(client, derp, derp_href)
            derp_state.discovered_from_fsa_href = fsa_href
            state.der_programs[derp_href] = derp_state
            logger.debug(
                "Discovered program %s (primacy=%d)",
                derp_href,
                derp_state.primacy,
            )


async def discover(
    client: Sep2Client,
    state: DiscoveredState,
    *,
    registration_pins: dict[str, int] | None = None,
    dcap_path: str = "/dcap",
) -> None:
    """Run full hierarchical discovery: dcap -> sdev -> edev -> der -> fsa -> derp.

    Populates ``state`` with all discovered resources and poll rates.
    Clears any existing state before starting.

    Args:
        client: The Sep2Client for HTTP operations.
        state: DiscoveredState to populate.
        registration_pins: Optional mapping from LFDI hex -> expected PIN
            for registration verification.
        dcap_path: Path to DeviceCapability resource (default: /dcap).
    """
    state.clear()

    # 1. Device Capability (with raw body for CSIP-AUS detection)
    dcap, raw_dcap = await client.get_with_body(dcap_path, DeviceCapability)
    state.dcap = dcap
    state.poll_rates["dcap"] = normalize_poll_rate(dcap.poll_rate, resource_key="dcap")

    # Auto-detect CSIP-AUS mode from namespace declaration in dcap response
    if b"csipaus" in raw_dcap:
        state.csip_aus_mode = True
        logger.info("CSIP-AUS mode detected from server dcap response")

    # 2. Time
    time_href = _extract_href(dcap.time_link)
    if time_href:
        state.time_href = time_href
        state.time = await client.get(time_href, Time)
        # Note: Time resource doesn't have its own pollRate, uses dcap rate
        observe_time_resource(client.timebase, state.time, href=time_href)

    # 3. SelfDevice (optional): the server's own device description. Used by
    #    clients that need the server's electrical context -- e.g. a site
    #    limit published on SelfDevice:DER:DERSettings that connected devices
    #    operate within.
    state.self_device = await _discover_self_device(client, dcap)
    if state.self_device is not None:
        state.poll_rates["sdev"] = normalize_poll_rate(
            state.self_device.device.poll_rate, resource_key="sdev"
        )

    # 4. MUP list href, for telemetry
    mup_href = _extract_href(dcap.mirror_usage_point_list_link)
    if mup_href:
        state.mup_list_href = mup_href
        logger.info("Discovered MirrorUsagePointListLink: %s", mup_href)
    else:
        logger.warning("DeviceCapability has no MirrorUsagePointListLink")

    # 5. EndDevice list
    edev_list_href = _extract_href(dcap.end_device_list_link)
    if not edev_list_href:
        logger.warning("No EndDeviceListLink in DeviceCapability")
        return

    edev_pages = await client.get_list(edev_list_href, EndDeviceList)
    device_mapping = DeviceMapping()

    for page in edev_pages:
        # Capture edev poll rate from first page
        if "edev" not in state.poll_rates and page.poll_rate is not None:
            state.poll_rates["edev"] = normalize_poll_rate(page.poll_rate, resource_key="edev")

        for edev in page.end_device:
            edev_href = edev.href
            if not edev_href:
                continue

            lfdi = edev.l_fdi or b""
            edev_state = EndDeviceState(device=edev, href=edev_href, lfdi=lfdi)

            # Log event list link (for alarm posting)
            log_event_href = _extract_href(edev.log_event_list_link)
            if log_event_href:
                edev_state.log_event_list_href = log_event_href

            # Subscription list link (for subscription management)
            sub_list_href = _extract_href(edev.subscription_list_link)
            if sub_list_href:
                edev_state.subscription_list_href = sub_list_href
                # Capture the SubscriptionList's server-advertised pollRate (once,
                # for the client's own SubscriptionList) so the reconcile
                # poll runs at the rate the server prefers. normalize_poll_rate
                # falls back to the IEEE 900s default when the server omits it.
                if "sub" not in state.poll_rates:
                    try:
                        sub_list = await client.get(sub_list_href, SubscriptionList)
                        state.poll_rates["sub"] = normalize_poll_rate(
                            sub_list.poll_rate, resource_key="sub"
                        )
                    except Exception as exc:
                        # Best-effort: the pollRate is an optimization. Any failure
                        # (protocol, malformed body, transport) just leaves
                        # poll_rates["sub"] unset so the reconcile poll falls back
                        # to the IEEE 2030.5 default cadence.
                        logger.debug(
                            "Could not read SubscriptionList pollRate, using default: %s", exc
                        )

            # Registration link (for PIN verification)
            reg_href = _extract_href(edev.registration_link)
            if reg_href:
                edev_state.registration_href = reg_href

            # 5a. DER list (optional; server may advertise link without resource).
            # Contained per device: one EndDevice's unreadable DER list must not
            # abort the walk for every other device, and at startup a raise here
            # never completes connect() at all (entry_point._connect_with_retry
            # retries every Sep2Error), which costs the control path as well.
            # The cost of continuing is that this device's DER resource PUTs stay
            # unscheduled (der_resource_manager skips None hrefs), so a real
            # fault logs at error rather than warning.
            der_href = _extract_href(edev.derlist_link)
            if der_href:
                try:
                    der_list = await client.get(der_href, Derlist)
                    edev_state.ders = list(der_list.der)
                    hrefs = _der_child_hrefs(
                        edev_state.ders, der_href, legacy_multi_der=client.server_2018_compat
                    )
                    edev_state.der_status_href = hrefs.status
                    edev_state.der_capability_href = hrefs.capability
                    edev_state.der_settings_href = hrefs.settings
                    edev_state.der_availability_href = hrefs.availability
                    edev_state.current_der_controls_href = hrefs.current_controls
                except Sep2ProtocolError as exc:
                    if exc.status_code in (404, 204):
                        logger.warning(
                            "DER list at %s %s, skipping", der_href, _absent_reason(exc.status_code)
                        )
                    else:
                        logger.error(
                            "DER list at %s failed to read, no DER resource PUTs for this "
                            "device: %s",
                            der_href,
                            exc,
                        )
                except Sep2PayloadError as exc:
                    logger.error(
                        "DER list at %s did not parse, no DER resource PUTs for this device: %s",
                        der_href,
                        exc,
                    )

            # 5b. FSA list (optional; server may advertise link without resource)
            fsa_href = _extract_href(edev.function_set_assignments_list_link)
            if fsa_href:
                try:
                    fsa_pages = await client.get_list(fsa_href, FunctionSetAssignmentsList)
                except Sep2ProtocolError as exc:
                    if exc.status_code == 404:
                        logger.warning("FSA list at %s not found, skipping", fsa_href)
                        fsa_pages = []
                    else:
                        raise
                for fsa_page in fsa_pages:
                    # Capture FSA poll rate from first page
                    if "fsa" not in state.poll_rates and fsa_page.poll_rate is not None:
                        state.poll_rates["fsa"] = normalize_poll_rate(
                            fsa_page.poll_rate, resource_key="fsa"
                        )
                    if hasattr(fsa_page, "subscribable") and fsa_page.subscribable:
                        edev_state.fsa_list_subscribable = True

                    for fsa in fsa_page.function_set_assignments:
                        edev_state.fsa_list.append(fsa)
                        logger.debug("Found FSA %s for device %s", fsa.href, edev_href)

                        # Pricing function set (opt-in): tariff tree per FSA. Done
                        # before the DERProgram branch so an FSA that advertises a
                        # tariff link but no DERProgram list is still discovered.
                        if state.pricing_enabled:
                            await _discover_tariffs_for_fsa(client, fsa, state)

                        # 6. DERProgram list per FSA
                        derp_list_href = _extract_href(fsa.derprogram_list_link)
                        if not derp_list_href:
                            continue

                        try:
                            derp_pages = await client.get_list(derp_list_href, DerprogramList)
                        except Sep2ProtocolError as exc:
                            if exc.status_code == 404:
                                logger.warning(
                                    "DERP list at %s not found, skipping", derp_list_href
                                )
                                continue
                            raise
                        for derp_page in derp_pages:
                            if hasattr(derp_page, "subscribable") and derp_page.subscribable:
                                edev_state.derp_list_subscribable = True
                        await _discover_programs_from_pages(
                            client, state, device_mapping, derp_pages, edev_href, fsa.href
                        )

                        # Gap 9: fetch per-FSA Time resource if available
                        fsa_time_href = _extract_href(fsa.time_link)
                        if fsa_time_href and fsa.href and fsa.href not in state.fsa_time:
                            try:
                                fsa_time = await client.get(fsa_time_href, Time)
                                state.fsa_time[fsa.href] = (fsa_time_href, fsa_time)
                                # IEEE 9.2.3: events from this FSA's programs
                                # follow this Time resource.
                                observe_time_resource(
                                    client.timebase,
                                    fsa_time,
                                    fsa_href=fsa.href,
                                    href=fsa_time_href,
                                )
                            except Sep2ProtocolError as exc:
                                if exc.status_code in (404, 204):
                                    logger.debug(
                                        "FSA Time at %s %s",
                                        fsa_time_href,
                                        _absent_reason(exc.status_code),
                                    )
                                else:
                                    raise

            # Gap 1: dcap fallback -- when no FSA or no programs from FSAs,
            # try discovering DERPrograms directly from DeviceCapability.
            if not edev_state.fsa_list:
                dcap_derp_href = _extract_href(dcap.derprogram_list_link)
                if dcap_derp_href:
                    logger.info("No FSA for %s, falling back to dcap DERProgramListLink", edev_href)
                    try:
                        dcap_derp_pages = await client.get_list(dcap_derp_href, DerprogramList)
                        await _discover_programs_from_pages(
                            client, state, device_mapping, dcap_derp_pages, edev_href, None
                        )
                    except Sep2ProtocolError as exc:
                        if exc.status_code == 404:
                            logger.debug("dcap DERProgramList at %s not found", dcap_derp_href)
                        else:
                            raise

            # Gap 3: snapshot current FSA hrefs for removal detection on next poll
            state.previous_fsa_hrefs[edev_href] = {
                fsa.href for fsa in edev_state.fsa_list if fsa.href
            }

            # Verify registration PIN if configured. Gate the fetch on map
            # membership so EndDevices without a configured PIN don't incur an
            # extra GET on every (re)discovery in mixed-config deployments.
            if registration_pins and edev_state.registration_href:
                lfdi_hex = edev_state.lfdi.hex() if edev_state.lfdi else ""
                if lfdi_hex in registration_pins:
                    await _verify_registration(
                        client, edev_state.registration_href, lfdi_hex, registration_pins
                    )

            state.end_devices[edev_href] = edev_state

    state.device_mapping = device_mapping

    # Set poll rate for DER program control refresh if not already set from the list.
    # Per IEEE 2030.5, the DeviceCapability pollRate is the default for the
    # entire hierarchy unless a more specific rate is provided on a sub-resource.
    if state.der_programs and "derp" not in state.poll_rates:
        dcap_rate = state.poll_rates.get("dcap")
        state.poll_rates["derp"] = normalize_poll_rate(
            None, resource_key="derp", default=dcap_rate or DEFAULT_POLL_RATE
        )

    # Set poll rate for Time resource (uses dcap rate as Time has no poll_rate attribute)
    if state.time_href and "time" not in state.poll_rates:
        dcap_rate = state.poll_rates.get("dcap")
        state.poll_rates["time"] = normalize_poll_rate(
            None, resource_key="time", default=dcap_rate or DEFAULT_POLL_RATE
        )

    logger.info(
        "Discovery complete: %d devices, %d programs",
        len(state.end_devices),
        len(state.der_programs),
    )


async def _refresh_curves_for_program(client: Sep2Client, derp_state: DerProgramState) -> None:
    """Re-fetch the DERCurveList for *derp_state* and replace the cached curves.

    Curves are referenced by the DERControl's curve-link fields
    (opModVoltVar.href, opModVoltWatt.href, etc.). When a server creates a
    new DERControl that references a freshly-created DERCurve, the
    the client must re-fetch curves alongside controls -- otherwise the
    cached curve list is stale and ``_find_curve`` in the translation layer
    returns ``None``, which falls back to ``{<mode>_mode_enable: 0}``
    (the operator's volt-var write silently disables the mode on the device).

    Failures are logged but not raised; the caller continues with whatever
    cached curves are available.
    """
    curve_list_href = _extract_href(derp_state.program.dercurve_list_link)
    if not curve_list_href:
        # Server removed the curve list link or never had one; clear cache.
        derp_state.der_curves.clear()
        return
    try:
        curve_pages = await client.get_list(curve_list_href, DercurveList)
    except Sep2ProtocolError as exc:
        if exc.status_code == 404:
            logger.debug("DERCurve list at %s not found during refresh", curve_list_href)
            derp_state.der_curves.clear()
            return
        raise
    except Sep2PayloadError as exc:
        logger.warning("Skipping DERCurve refresh: %s", exc)
        return
    except Exception:
        logger.warning(
            "Failed to parse DERCurve list at %s during refresh, skipping",
            curve_list_href,
            exc_info=True,
        )
        return

    derp_state.der_curves.clear()
    for curve_page in curve_pages:
        derp_state.der_curves.extend(curve_page.dercurve)


async def refresh_der_controls(client: Sep2Client, state: DiscoveredState) -> None:
    """Re-fetch DER control lists, DefaultDERControl, and DERCurves for all
    known programs.

    Unlike ``discover()``, this does not clear state or re-run the full
    hierarchy walk. It refreshes the DERControl list, DefaultDERControl, and
    DERCurve list for each already-discovered DERProgram, so new controls,
    DDERC changes, and freshly-created curves are picked up without a
    disruptive state clear.
    """
    for _, derp_state in list(state.der_programs.items()):
        # Refresh DefaultDERControl
        dderc_href = _extract_href(derp_state.program.default_dercontrol_link)
        if dderc_href:
            try:
                dderc = await client.get(dderc_href, DefaultDercontrol)
                derp_state.default_dercontrol = dderc
            except Sep2ProtocolError as exc:
                if exc.status_code in (404, 204):
                    logger.debug(
                        "DDERC at %s %s on refresh", dderc_href, _absent_reason(exc.status_code)
                    )
                    derp_state.default_dercontrol = None
                else:
                    raise
            except Sep2PayloadError as exc:
                logger.warning("Skipping DDERC refresh at %s: %s", dderc_href, exc)
            except Exception:
                logger.warning(
                    "Failed to parse DDERC at %s during refresh, skipping",
                    dderc_href,
                    exc_info=True,
                )
        else:
            # Server removed the DefaultDERControl link — clear cached value
            derp_state.default_dercontrol = None

        # Refresh DERCurve list before controls, so any new curves a fresh
        # control might reference are already cached when the translation
        # layer resolves the curve link.
        await _refresh_curves_for_program(client, derp_state)

        # Refresh DERControl list
        derc_list_href = _extract_href(derp_state.program.dercontrol_list_link)
        if not derc_list_href:
            continue

        try:
            derc_pages = await client.get_list(derc_list_href, DercontrolList)
        except Sep2ProtocolError as exc:
            if exc.status_code == 404:
                logger.warning("DERC list at %s not found during refresh", derc_list_href)
                continue
            raise
        except Sep2PayloadError as exc:
            logger.warning("Skipping DERControl refresh: %s", exc)
            continue
        except Exception:
            logger.warning(
                "Failed to parse DERControl list at %s during refresh, skipping",
                derc_list_href,
                exc_info=True,
            )
            continue

        derp_state.der_controls.clear()
        for derc_page in derc_pages:
            derp_state.der_controls.extend(derc_page.dercontrol)

    logger.debug("Refreshed DER controls for %d programs", len(state.der_programs))


async def refresh_der_controls_for_program(
    client: Sep2Client, state: DiscoveredState, program_href: str
) -> bool:
    """Re-fetch DERControl list and DefaultDERControl for a single program.

    Returns True if the program was found and refreshed, False if
    *program_href* is not in state (e.g. removed between notification
    and fetch).
    """
    derp_state = state.der_programs.get(program_href)
    if derp_state is None:
        logger.warning("Program %s not found in state during targeted refresh", program_href)
        return False

    # Refresh DefaultDERControl
    dderc_href = _extract_href(derp_state.program.default_dercontrol_link)
    if dderc_href:
        try:
            dderc = await client.get(dderc_href, DefaultDercontrol)
            derp_state.default_dercontrol = dderc
        except Sep2ProtocolError as exc:
            if exc.status_code in (404, 204):
                logger.debug(
                    "DDERC at %s %s on targeted refresh",
                    dderc_href,
                    _absent_reason(exc.status_code),
                )
                derp_state.default_dercontrol = None
            else:
                raise
        except Sep2PayloadError as exc:
            logger.warning("Skipping DDERC targeted refresh at %s: %s", dderc_href, exc)
        except Exception:
            logger.warning(
                "Failed to parse DDERC at %s during targeted refresh, skipping",
                dderc_href,
                exc_info=True,
            )
    else:
        derp_state.default_dercontrol = None

    # Refresh DERCurve list before controls so any newly-referenced curves
    # are cached when the translation layer resolves curve links.
    await _refresh_curves_for_program(client, derp_state)

    # Refresh DERControl list
    derc_list_href = _extract_href(derp_state.program.dercontrol_list_link)
    if derc_list_href:
        try:
            derc_pages = await client.get_list(derc_list_href, DercontrolList)
        except Sep2ProtocolError as exc:
            if exc.status_code == 404:
                logger.warning("DERC list at %s not found during targeted refresh", derc_list_href)
                return True
            raise
        except Sep2PayloadError as exc:
            logger.warning("Skipping DERControl targeted refresh: %s", exc)
            return True
        except Exception:
            logger.warning(
                "Failed to parse DERControl list at %s during targeted refresh, skipping",
                derc_list_href,
                exc_info=True,
            )
            return True

        derp_state.der_controls.clear()
        for derc_page in derc_pages:
            derp_state.der_controls.extend(derc_page.dercontrol)

    logger.debug("Targeted refresh complete for program %s", program_href)
    return True


async def refresh_end_device_lists(client: Sep2Client, state: DiscoveredState) -> None:
    """Re-fetch the EndDeviceList from the DeviceCapability link.

    This updates EndDevice data (like changedTime) but does NOT re-discover
    FSA or DERProgram hierarchies. Useful for catching device-level changes
    without full rediscovery.
    """
    # Get the EndDeviceList href from dcap
    if not state.dcap:
        logger.warning("No DeviceCapability in state, skipping EndDevice list refresh")
        return

    edev_list_href = _extract_href(state.dcap.end_device_list_link)
    if not edev_list_href:
        logger.warning("No EndDeviceListLink in DeviceCapability")
        return

    try:
        edev_pages = await client.get_list(edev_list_href, EndDeviceList)
    except Sep2ProtocolError as exc:
        logger.warning("Failed to refresh EndDeviceList at %s: %s", edev_list_href, exc)
        return

    # Update existing devices with refreshed data
    for page in edev_pages:
        for edev in page.end_device:
            edev_href = edev.href
            if not edev_href:
                continue

            if edev_href in state.end_devices:
                # Update the device object and LFDI
                state.end_devices[edev_href].device = edev
                state.end_devices[edev_href].lfdi = edev.l_fdi or b""

    logger.debug("Refreshed EndDevice list with %d devices", len(state.end_devices))


async def refresh_function_set_assignments(
    client: Sep2Client, state: DiscoveredState, *, skip_hrefs: set[str] | None = None
) -> list[str]:
    """Re-fetch FSA lists for all known end devices.

    Returns a list of program hrefs from removed FSAs (Gap 3: IEEE 8.8.3).
    Callers should cancel active events from these programs.

    ``skip_hrefs`` names FSA list hrefs that have an active subscription: the poll
    path passes these so it doesn't re-fetch a resource we're already subscribed to
    (IEEE 2030.5 §8.9.3.4 rule (r)). A single cancelled FSA can then be polled while
    its siblings stay subscription-only. Discovery/rediscovery pass nothing (a full
    re-sync refreshes every FSA).
    """
    removed_program_hrefs: list[str] = []

    if not state.end_devices:
        return removed_program_hrefs

    for edev_href, edev_state in list(state.end_devices.items()):
        # Refresh FSA list
        fsa_href = _extract_href(edev_state.device.function_set_assignments_list_link)
        if not fsa_href:
            continue
        if skip_hrefs and fsa_href in skip_hrefs:
            continue  # active subscription -> rule (r): don't poll it

        try:
            fsa_pages = await client.get_list(fsa_href, FunctionSetAssignmentsList)
        except Sep2ProtocolError as exc:
            if exc.status_code == 404:
                logger.warning("FSA list at %s not found during refresh", fsa_href)
                continue
            raise

        edev_state.fsa_list.clear()
        for fsa_page in fsa_pages:
            edev_state.fsa_list.extend(fsa_page.function_set_assignments)

        # Gap 3: detect removed FSAs by comparing to previous snapshot
        current_fsa_hrefs = {fsa.href for fsa in edev_state.fsa_list if fsa.href}
        previous = state.previous_fsa_hrefs.get(edev_href, set())
        removed_fsas = previous - current_fsa_hrefs

        if removed_fsas:
            logger.info(
                "Detected %d removed FSA(s) for %s: %s",
                len(removed_fsas),
                edev_href,
                removed_fsas,
            )
            for removed_fsa_href in removed_fsas:
                removed_program_hrefs.extend(state.programs_from_fsa(removed_fsa_href))

        # Update snapshot for next poll
        state.previous_fsa_hrefs[edev_href] = current_fsa_hrefs

    logger.debug("Refreshed FSA lists for %d end devices", len(state.end_devices))
    return removed_program_hrefs


async def refresh_tariffs(client: Sep2Client, state: DiscoveredState) -> None:
    """Re-walk the tariff tree from all known FSAs (Pricing poll refresh).

    Rebuilds ``state.tariff_profiles`` so newly-added TimeTariffIntervals are
    picked up and expired ones drop out. No-op unless pricing is enabled. The
    walk dedupes within this refresh (the global tariff tree is reachable from
    every FSA), so each profile is fetched once.
    """
    if not state.pricing_enabled:
        return
    state.tariff_profiles.clear()
    for edev_state in state.end_devices.values():
        for fsa in edev_state.fsa_list:
            await _discover_tariffs_for_fsa(client, fsa, state)


async def refresh_der_programs(client: Sep2Client, state: DiscoveredState) -> list[str]:
    """Re-fetch DER program lists from all known FSAs.

    Updates existing programs (primacy, program object) and discovers new
    programs that have appeared since the last full discovery. New programs
    are fully discovered (DDERC, DERC list, curves) and added to the device
    mapping.

    Prunes programs that are no longer present in any FSA's DER program
    list. Programs from FSAs whose DERP list returned 404 are preserved
    (we can't verify their status).

    Returns a list of removed program hrefs so callers can cancel events.
    """
    if not state.end_devices:
        return []

    # Track which programs are confirmed alive and which FSAs we couldn't verify.
    seen_programs: set[str] = set()
    unreachable_fsas: set[str | None] = set()

    for edev_href, edev_state in list(state.end_devices.items()):
        for fsa in edev_state.fsa_list:
            derp_list_href = _extract_href(fsa.derprogram_list_link)
            if not derp_list_href:
                continue

            try:
                derp_pages = await client.get_list(derp_list_href, DerprogramList)
            except Sep2ProtocolError as exc:
                if exc.status_code == 404:
                    logger.warning("DERP list at %s not found during refresh", derp_list_href)
                    unreachable_fsas.add(fsa.href)
                    continue
                raise

            for derp_page in derp_pages:
                for derp in derp_page.derprogram:
                    derp_href = derp.href
                    if not derp_href:
                        continue

                    seen_programs.add(derp_href)

                    if derp_href in state.der_programs:
                        # Update the program object and primacy
                        state.der_programs[derp_href].program = derp
                        state.der_programs[derp_href].primacy = (
                            derp.primacy.value if derp.primacy is not None else 0
                        )
                    else:
                        # New program discovered during refresh
                        derp_state = await _discover_program(client, derp, derp_href)
                        derp_state.discovered_from_fsa_href = fsa.href
                        state.der_programs[derp_href] = derp_state
                        state.device_mapping.add(derp_href, edev_href)
                        logger.info(
                            "Discovered new program %s (primacy=%d) during refresh",
                            derp_href,
                            derp_state.primacy,
                        )

    # Prune programs no longer in any FSA's DERP list. Preserve programs
    # from FSAs we couldn't reach (404) since we can't confirm removal.
    removed: list[str] = []
    for href, ps in list(state.der_programs.items()):
        if href in seen_programs:
            continue
        if ps.discovered_from_fsa_href in unreachable_fsas:
            continue
        logger.info("Removing stale program %s (no longer in any DERP list)", href)
        del state.der_programs[href]
        state.device_mapping.remove_program(href)
        removed.append(href)

    logger.debug(
        "Refreshed DER programs for %d end devices (%d pruned)",
        len(state.end_devices),
        len(removed),
    )
    return removed
