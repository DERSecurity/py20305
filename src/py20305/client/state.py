"""Discovered resource state containers for CSIP client."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from py20305.models.sep.sep import (
    DefaultDercontrol,
    Der1,
    Dercontrol1,
    Dercurve1,
    Derprogram1,
    DeviceCapability,
    EndDevice1,
    FunctionSetAssignments1,
    RateComponent1,
    SelfDevice,
    TariffProfile1,
    Time,
    TimeTariffInterval1,
)


@dataclass
class SelfDeviceState:
    """State for the server's own SelfDevice resource and its DER children.

    SelfDevice describes the *server* as a device, as distinct from the
    EndDevices it hosts on a client's behalf. Its DER child resources
    describe the server's own electrical context rather than any client's
    DER -- for example a site-level active-power or voltage limit that
    connected devices are expected to operate within.

    Discovery records structure only (the resource and its child hrefs);
    callers fetch DERSettings / DERCapability content themselves, matching
    how :class:`EndDeviceState` is populated.

    ``ders`` holds whatever the server served, but the child hrefs come from
    the single DER that IEEE 2030.5-2023 permits a DERList to hold (Annex B,
    DERList, p. 250), so they always describe one DER rather than a
    composite. The ``subscribable`` flags record whether a refresh can ride
    the notification path instead of a poll.
    """

    device: SelfDevice
    href: str
    ders: list[Der1] = field(default_factory=list)
    der_settings_href: str | None = None
    der_capability_href: str | None = None
    der_status_href: str | None = None
    der_availability_href: str | None = None
    subscribable: bool = False
    der_subscribable: bool = False


@dataclass
class EndDeviceState:
    """State for a discovered EndDevice and its child resources."""

    device: EndDevice1
    href: str
    lfdi: bytes
    ders: list[Der1] = field(default_factory=list)
    fsa_list: list[FunctionSetAssignments1] = field(default_factory=list)
    log_event_list_href: str | None = None
    der_status_href: str | None = None
    der_capability_href: str | None = None
    der_settings_href: str | None = None
    der_availability_href: str | None = None
    current_der_controls_href: str | None = None
    registration_href: str | None = None
    subscription_list_href: str | None = None
    fsa_list_subscribable: bool = False
    derp_list_subscribable: bool = False


@dataclass
class DerProgramState:
    """State for a discovered DERProgram and its child resources."""

    program: Derprogram1
    href: str
    primacy: int
    default_dercontrol: DefaultDercontrol | None = None
    der_controls: list[Dercontrol1] = field(default_factory=list)
    der_curves: list[Dercurve1] = field(default_factory=list)
    discovered_from_fsa_href: str | None = None
    derc_list_subscribable: bool = False
    dderc_subscribable: bool = False


@dataclass
class TariffRateComponentState:
    """State for a discovered RateComponent and its TimeTariffIntervals.

    The interval schedule is discovered here; each interval's price
    (ConsumptionTariffInterval) is fetched on demand when the interval is
    processed, via ``interval.consumption_tariff_interval_list_link``.
    """

    rate_component: RateComponent1
    href: str
    time_tariff_intervals: list[TimeTariffInterval1] = field(default_factory=list)
    tti_list_subscribable: bool = False
    #: TimeTariffIntervalList href -- the subscribe target when the server marks
    #: the list subscribable.
    tti_list_href: str | None = None


@dataclass
class TariffProfileState:
    """State for a discovered TariffProfile and its child resources (Pricing)."""

    profile: TariffProfile1
    href: str
    primacy: int
    rate_components: list[TariffRateComponentState] = field(default_factory=list)
    discovered_from_fsa_href: str | None = None


@dataclass
class DeviceMapping:
    """Bidirectional mapping between DERPrograms and EndDevices."""

    program_to_devices: dict[str, list[str]] = field(default_factory=dict)
    device_to_programs: dict[str, list[str]] = field(default_factory=dict)

    def add(self, program_href: str, device_href: str) -> None:
        """Add a program <-> device relationship."""
        self.program_to_devices.setdefault(program_href, []).append(device_href)
        self.device_to_programs.setdefault(device_href, []).append(program_href)

    def remove_program(self, program_href: str) -> None:
        """Remove a program and its device associations."""
        devices = self.program_to_devices.pop(program_href, [])
        for dev_href in devices:
            progs = self.device_to_programs.get(dev_href)
            if progs is not None:
                with contextlib.suppress(ValueError):
                    progs.remove(program_href)


@dataclass
class ResourceVersionCache:
    """Cache of (mRID, version) tuples for resource change detection (Gap 10)."""

    versions: dict[str, tuple[bytes, int]] = field(default_factory=dict)

    def is_unchanged(self, path: str, m_rid: bytes, version: int) -> bool:
        """Return True if the resource at path has not changed."""
        cached = self.versions.get(path)
        if cached is not None and cached == (m_rid, version):
            return True
        self.versions[path] = (m_rid, version)
        return False


@dataclass
class DiscoveredState:
    """All state discovered from the IEEE 2030.5 server."""

    dcap: DeviceCapability | None = None
    time: Time | None = None
    time_href: str | None = None
    mup_list_href: str | None = None
    #: The server's own SelfDevice, when it advertises a SelfDeviceLink.
    #: Optional in IEEE 2030.5, so this stays None against servers that
    #: don't publish one.
    self_device: SelfDeviceState | None = None
    end_devices: dict[str, EndDeviceState] = field(default_factory=dict)
    der_programs: dict[str, DerProgramState] = field(default_factory=dict)
    device_mapping: DeviceMapping = field(default_factory=DeviceMapping)
    poll_rates: dict[str, int | None] = field(default_factory=dict)
    csip_aus_mode: bool = False

    #: Pricing function set (opt-in). When False, the tariff tree is not
    #: discovered or polled. Populated from the client configuration at init.
    pricing_enabled: bool = False

    #: Discovered TariffProfiles keyed by href (Pricing function set).
    tariff_profiles: dict[str, TariffProfileState] = field(default_factory=dict)

    #: Per-FSA Time resource (Gap 9): maps FSA href to (time_href, Time)
    fsa_time: dict[str, tuple[str, Time]] = field(default_factory=dict)

    #: Previous FSA hrefs per edev (Gap 3): tracks removals between polls
    previous_fsa_hrefs: dict[str, set[str]] = field(default_factory=dict)

    def clear(self) -> None:
        """Reset all discovered state for rediscovery."""
        self.dcap = None
        self.time = None
        self.time_href = None
        self.mup_list_href = None
        self.self_device = None
        self.end_devices.clear()
        self.der_programs.clear()
        self.tariff_profiles.clear()
        self.device_mapping = DeviceMapping()
        self.poll_rates.clear()
        self.csip_aus_mode = False
        # pricing_enabled is a config flag (set at client init), not discovered
        # state -- preserve it across rediscovery.
        self.fsa_time.clear()
        self.previous_fsa_hrefs.clear()

    def programs_from_fsa(self, fsa_href: str) -> list[str]:
        """Return program hrefs discovered from a given FSA."""
        return [
            href
            for href, ps in self.der_programs.items()
            if ps.discovered_from_fsa_href == fsa_href
        ]

    def get_fsa_time_for_program(self, program_href: str) -> Time | None:
        """Get the FSA-specific Time resource for a given program (Gap 9)."""
        ps = self.der_programs.get(program_href)
        if ps is None or ps.discovered_from_fsa_href is None:
            return None
        entry = self.fsa_time.get(ps.discovered_from_fsa_href)
        return entry[1] if entry is not None else None

    def find_program_for_resource(self, resource_href: str) -> str | None:
        """Find the program href that owns a DERControlList or DefaultDERControl.

        Returns None if *resource_href* does not match any known program's
        ``dercontrol_list_link`` or ``default_dercontrol_link``.
        """
        for program_href, derp_state in self.der_programs.items():
            prog = derp_state.program
            derc_link = getattr(prog.dercontrol_list_link, "href", None)
            if derc_link and derc_link == resource_href:
                return program_href
            dderc_link = getattr(prog.default_dercontrol_link, "href", None)
            if dderc_link and dderc_link == resource_href:
                return program_href
        return None
