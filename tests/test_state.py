"""Tests for discovered state containers."""

from py20305.client.state import (
    DerProgramState,
    DeviceMapping,
    DiscoveredState,
    EndDeviceState,
)
from py20305.models.sep.sep import (
    DefaultDercontrolLink,
    DercontrolListLink,
    Derprogram1,
    DeviceCapability,
    EndDevice1,
    MRidtype,
    PrimacyType,
    Sfditype,
    Time,
    TimeOffsetType,
    TimeType,
)


def _make_end_device() -> EndDevice1:
    return EndDevice1(s_fdi=Sfditype(value=0), changed_time=TimeType(value=0))


def test_discovered_state_defaults_empty():
    state = DiscoveredState()
    assert state.dcap is None
    assert state.time is None
    assert state.end_devices == {}
    assert state.der_programs == {}
    assert state.poll_rates == {}


def test_discovered_state_clear():
    state = DiscoveredState()
    state.dcap = DeviceCapability()
    state.end_devices["x"] = EndDeviceState(device=_make_end_device(), href="x", lfdi=b"\x00")
    state.poll_rates["dcap"] = 300
    state.device_mapping.add("/derp/1", "/edev/1")

    state.clear()

    assert state.dcap is None
    assert state.end_devices == {}
    assert state.poll_rates == {}
    assert state.device_mapping.program_to_devices == {}


def test_device_mapping_bidirectional():
    mapping = DeviceMapping()
    mapping.add("/derp/1", "/edev/1")
    mapping.add("/derp/1", "/edev/2")
    mapping.add("/derp/2", "/edev/1")

    assert mapping.program_to_devices["/derp/1"] == ["/edev/1", "/edev/2"]
    assert mapping.device_to_programs["/edev/1"] == ["/derp/1", "/derp/2"]


def _make_derp_state(href: str, fsa_href: str | None = None) -> DerProgramState:
    derp = Derprogram1(m_rid=MRidtype(value=b"\x00" * 16), primacy=PrimacyType(value=0))
    derp.href = href
    return DerProgramState(program=derp, href=href, primacy=0, discovered_from_fsa_href=fsa_href)


def _make_time() -> Time:
    tt = TimeType(value=1000)
    zero = TimeOffsetType(value=0)
    return Time(
        current_time=tt,
        dst_end_time=tt,
        dst_offset=zero,
        dst_start_time=tt,
        quality=3,
        tz_offset=zero,
    )


def test_programs_from_fsa():
    """Gap 3: programs_from_fsa returns hrefs of programs from a given FSA."""
    state = DiscoveredState()
    state.der_programs["/derp/1"] = _make_derp_state("/derp/1", "/fsa/1")
    state.der_programs["/derp/2"] = _make_derp_state("/derp/2", "/fsa/1")
    state.der_programs["/derp/3"] = _make_derp_state("/derp/3", "/fsa/2")

    from_fsa1 = state.programs_from_fsa("/fsa/1")
    assert set(from_fsa1) == {"/derp/1", "/derp/2"}
    assert state.programs_from_fsa("/fsa/2") == ["/derp/3"]
    assert state.programs_from_fsa("/fsa/unknown") == []


def test_get_fsa_time_for_program():
    """Gap 9: get_fsa_time_for_program returns the FSA-specific Time."""
    state = DiscoveredState()
    state.der_programs["/derp/1"] = _make_derp_state("/derp/1", "/fsa/1")
    state.der_programs["/derp/2"] = _make_derp_state("/derp/2", "/fsa/2")
    state.der_programs["/derp/3"] = _make_derp_state("/derp/3", None)

    t1 = _make_time()
    state.fsa_time["/fsa/1"] = ("/fsa/1/tm", t1)

    assert state.get_fsa_time_for_program("/derp/1") is t1
    assert state.get_fsa_time_for_program("/derp/2") is None  # No time for fsa/2
    assert state.get_fsa_time_for_program("/derp/3") is None  # No FSA
    assert state.get_fsa_time_for_program("/derp/unknown") is None


def test_find_program_for_derc_href():
    """DERControlList href resolves to the owning program."""
    state = DiscoveredState()
    derp = _make_derp_state("/derp/1")
    derp.program.dercontrol_list_link = DercontrolListLink(href="/derp/1/derc")
    state.der_programs["/derp/1"] = derp

    assert state.find_program_for_resource("/derp/1/derc") == "/derp/1"


def test_find_program_for_dderc_href():
    """DefaultDERControl href resolves to the owning program."""
    state = DiscoveredState()
    derp = _make_derp_state("/derp/2")
    derp.program.default_dercontrol_link = DefaultDercontrolLink(href="/derp/2/dderc")
    state.der_programs["/derp/2"] = derp

    assert state.find_program_for_resource("/derp/2/dderc") == "/derp/2"


def test_find_program_for_unknown_href():
    """Unknown href returns None."""
    state = DiscoveredState()
    state.der_programs["/derp/1"] = _make_derp_state("/derp/1")
    assert state.find_program_for_resource("/unknown/derc") is None


def test_find_program_for_empty_state():
    """Empty der_programs returns None."""
    state = DiscoveredState()
    assert state.find_program_for_resource("/derp/1/derc") is None


def test_clear_resets_fsa_time_and_previous():
    """clear() resets Gap 3 and Gap 9 state."""
    state = DiscoveredState()
    state.fsa_time["/fsa/1"] = ("/fsa/1/tm", _make_time())
    state.previous_fsa_hrefs["/edev/1"] = {"/fsa/1"}

    state.clear()

    assert state.fsa_time == {}
    assert state.previous_fsa_hrefs == {}


class TestDeviceMappingIsASet:
    """A device listed twice is dispatched to twice, and answered for twice."""

    def test_the_same_pair_added_twice_appears_once(self):
        mapping = DeviceMapping()
        mapping.add("/derp/1", "/edev/1")
        mapping.add("/derp/1", "/edev/1")

        assert mapping.program_to_devices["/derp/1"] == ["/edev/1"]
        assert mapping.device_to_programs["/edev/1"] == ["/derp/1"]

    def test_distinct_pairs_still_accumulate(self):
        mapping = DeviceMapping()
        mapping.add("/derp/1", "/edev/1")
        mapping.add("/derp/1", "/edev/2")
        mapping.add("/derp/2", "/edev/1")

        assert mapping.program_to_devices["/derp/1"] == ["/edev/1", "/edev/2"]
        assert mapping.device_to_programs["/edev/1"] == ["/derp/1", "/derp/2"]

    def test_removing_a_program_after_a_repeated_add_leaves_nothing_behind(self):
        mapping = DeviceMapping()
        mapping.add("/derp/1", "/edev/1")
        mapping.add("/derp/1", "/edev/1")

        mapping.remove_program("/derp/1")

        assert "/derp/1" not in mapping.program_to_devices
        assert mapping.device_to_programs["/edev/1"] == []
