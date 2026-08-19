"""Tests for modesResponded bitmask builder."""

from __future__ import annotations

import pytest

from py20305.events.modes_bitmask import (
    _FIELD_TO_BIT,
    build_modes_responded,
    get_active_mode_names,
)
from py20305.models.sep.sep import (
    DercontrolBase,
    DerunitRefType,
    FixedVarControlType,
    SignedPerCent,
    SignedPerCentControlType,
)


class TestBuildModesResponded:
    def test_empty_base_returns_zero(self):
        result = build_modes_responded(DercontrolBase())
        assert int.from_bytes(result.value, "big") == 0

    def test_connect_sets_bit_2(self):
        base = DercontrolBase(op_mod_connect=True)
        result = build_modes_responded(base)
        bitmask = int.from_bytes(result.value, "big")
        assert bitmask & (1 << 2) != 0
        assert bitmask == (1 << 2)

    def test_fixed_var_sets_bit_6(self):
        base = DercontrolBase(
            op_mod_fixed_var=FixedVarControlType(
                ref_type=DerunitRefType(value=0),
                value=SignedPerCent(value=50),
            )
        )
        result = build_modes_responded(base)
        bitmask = int.from_bytes(result.value, "big")
        assert bitmask & (1 << 6) != 0

    def test_fixed_w_sets_bit_7(self):
        base = DercontrolBase(op_mod_fixed_w=SignedPerCentControlType(value=50))
        result = build_modes_responded(base)
        bitmask = int.from_bytes(result.value, "big")
        assert bitmask & (1 << 7) != 0

    def test_multiple_modes(self):
        base = DercontrolBase(
            op_mod_connect=True,
            op_mod_energize=False,
        )
        result = build_modes_responded(base)
        bitmask = int.from_bytes(result.value, "big")
        assert bitmask & (1 << 2) != 0  # connect
        assert bitmask & (1 << 3) != 0  # energize

    @pytest.mark.parametrize(
        "field_name,bit_pos",
        list(_FIELD_TO_BIT.items())[:5],
        ids=lambda x: str(x),
    )
    def test_field_bit_mapping(self, field_name: str, bit_pos: int):
        """Verify a sample of field->bit mappings are defined."""
        assert field_name in _FIELD_TO_BIT
        assert _FIELD_TO_BIT[field_name] == bit_pos

    def test_result_is_4_bytes(self):
        base = DercontrolBase(op_mod_connect=True)
        result = build_modes_responded(base)
        assert len(result.value) == 4


class TestGetActiveModeNames:
    def test_empty_base(self):
        assert get_active_mode_names(DercontrolBase()) == frozenset()

    def test_connect_and_energize(self):
        base = DercontrolBase(op_mod_connect=True, op_mod_energize=False)
        names = get_active_mode_names(base)
        assert "op_mod_connect" in names
        assert "op_mod_energize" in names
        assert len(names) == 2
