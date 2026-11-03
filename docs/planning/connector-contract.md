# An IEEE 2030.5 connector contract

Status: proposal, not implemented. Ships, once approved, in the same breaking
release as the removal of the SunSpec connector.

## Problem

The connector contract, what `BaseConnector.fetch_*` returns and what each
`update_*` receives, still uses the SunSpec model 701 and 702 point names
(`W`, `ACType`, `WL1`, `VL1L2`, `WMaxRtg`, `WMax`) and states its sign
convention by reference to SunSpec. The library maps those names to IEEE 2030.5
in `telemetry/`, so every connector speaks one vendor vocabulary and the
library carries a second standard it does not otherwise implement. Readings
also cannot say what they measure: phase, kind and data qualifier come from a
fixed table keyed by the SunSpec name, and a connector can only patch them with
`ReadingOverride`, which changes the ReadingType of an mRID a server may
already hold (IEEE 2030.5-2023 §10.11.3 Rule n).

## Proposal

Every key and value a connector exchanges with the library is an IEEE 2030.5
element name and value.

### Readings (`fetch_monitoring`)

`fetch_monitoring` returns a list of readings instead of a flat dict. Each
reading states its own IEEE 2030.5 ReadingType:

```python
@dataclass(frozen=True)
class Reading:
    uom: int                     # UomType: 38 W, 63 var, 61 VA, 29 V, 5 A, 33 Hz, 65 cos(theta), 72 Wh
    value: float                 # engineering units; signed, see "Signs"
    phase: int = 0               # PhaseCode: 0 n/a, 128 A, 129 AN, 132 AB, 224 ABC, ...
    kind: int = 37               # KindType: 37 power, 12 energy, 0 not applicable
    data_qualifier: int = 12     # DataQualifierType: 12 normal, 2 average, 8 max, 9 min
    accumulation_behaviour: int = 12   # 12 instantaneous, 9 summation
    flow_direction: int | None = None  # set only for a total that is direction-specific (Wh forward 1, reverse 19)
    multiplier: int | None = None      # powerOfTenMultiplier to post with; library default per uom
    quality_flags: int | None = None   # this sample's qualityFlags; default valid
```

| Old key | New reading |
|---|---|
| `W`, `Var`, `VA`, `A` | `uom` 38, 63, 61, 5; `phase` 0 (not applicable) or the phases the total covers (132 AB, 224 ABC) |
| `V` (line-to-line average) | `uom` 29, `data_qualifier` 2 (average), `phase` 132 or 224 |
| A line-to-neutral average | `uom` 29, `data_qualifier` 2, `phase` 129, 65 or 33 for one line; 0 if it spans lines (IEEE 2030.5 has no code for that) |
| `Hz` | `uom` 33, `phase` 0 |
| `PF` | `uom` 65, `kind` 0 |
| `WL1`..`WL3`, `VarL1`.., `VAL1`.., `PFL1`.., `AL1`.. | the same `uom` with `phase` 128, 64, 32 |
| `VL1`..`VL3` | `uom` 29, `phase` 129, 65, 33 |
| `VL1L2`, `VL2L3`, `VL3L1` | `uom` 29, `phase` 132, 66, 40 |
| `WHAvail` (state of energy) | `uom` 72, `kind` 0 (the signed-load profile's reading) |
| `WhFwd`, `WhRev` | `uom` 72, `kind` 12, `accumulation_behaviour` 9, `flow_direction` 1 or 19 |
| `ACType` | removed: each reading carries its own phase, so the wiring type is no longer needed to choose per-line readings or the totals' phase |
| `"<key>__quality"` | `Reading.quality_flags` |
| `ReadingOverride` (`reading_overrides()`) | removed: every field it set is a field of the reading |

**mRIDs.** A reading's mRID is derived from its full ReadingType identity
(`uom`, `phase`, `kind`, `data_qualifier`, `accumulation_behaviour`,
`flow_direction`), so a reading whose ReadingType differs gets a different mRID
by construction and Rule n holds without special cases. Readings that 0.16.x
could register keep the mRID it gave them: a fixed table maps each of those
identities to its 0.16.x slot (system 1 to 7, signed-load state of energy 8,
per-line 10 to 27, line-to-line 28 to 30, the net and split power slots 31 to
58, the energy totals 61 and 62), so a server that holds a 0.16.x registration
keeps receiving readings under the mRIDs it knows. Any other identity, a phased
system voltage among them, gets an mRID hashed from the identity in a layout
that cannot collide with the slot layout (SFDI, a marker, the identity hash,
the PEN). The slot 63 and 64 rule of the earlier draft is not needed: it was
never released, and identity-derived mRIDs cover the case.

The reading profile (`default`, `signed_load_convention`) and `power_direction`
keep their meaning and apply to the readings with `uom` 38 and 63.

### Signs

Real and reactive power are signed in the frame of the device's role:

- **DER and DER meter.** Positive real power is power the DER delivers (its
  output); negative is power it absorbs. Positive reactive power is reactive
  power the DER injects (over-excited); negative is reactive power it absorbs
  (under-excited).
- **Site meter.** Positive real and reactive power are import, from the area
  EPS to the premises, which IEEE 2030.5 FlowDirectionType calls Forward.

This is the orientation the contract has today, stated without reference to
another standard; no connector's numbers change. The default profile posts the
sign as flowDirection, the signed-load profile negates a DER's output, as now.

### Ratings (`fetch_nameplate`) and settings (`fetch_configuration`)

Keys become the DERCapability and DERSettings element names. Values keep their
shape: a number, or `{value, multiplier}`, or `{displacement, multiplier}` for
a power factor.

| Old nameplate key | New key |
|---|---|
| `WMaxRtg` | `rtgMaxW` (required) |
| `VAMaxRtg` | `rtgMaxVA` |
| `VarMaxInjRtg`, `VarMaxAbsRtg` | `rtgMaxVar`, `rtgMaxVarNeg` |
| `WOvrExtRtg`, `WOvrExtRtgPF` | `rtgOverExcitedW`, `rtgOverExcitedPF` |
| `WUndExtRtg`, `WUndExtRtgPF` | `rtgUnderExcitedW`, `rtgUnderExcitedPF` |
| `WChaRteMaxRtg`, `VAChaRteMaxRtg` | `rtgMaxChargeRateW`, `rtgMaxChargeRateVA` |
| `WDisChaRteMaxRtg`, `VADisChaRteMaxRtg` | `rtgMaxDischargeRateW`, `rtgMaxDischargeRateVA` |
| `WhMaxRtg`, `AhMaxRtg` | `rtgMaxWh`, `rtgMaxAh` |
| `rtgMaxA` | unchanged |
| `VNomRtg`, `VMaxRtg`, `VMinRtg` | `rtgVNom`, `rtgMaxV`, `rtgMinV` |
| `ReactSusceptRtg` | `rtgReactiveSusceptance` |
| `NorOpCatRtg`, `AbnOpCatRtg` | `rtgNormalCategory`, `rtgAbnormalCategory` |
| `CtrlModes` | `modesSupported` (DERControlType bitmap) |
| `DoeModesSupported` | `doeModesSupported` (CSIP-AUS extension) |

| Old configuration key | New key |
|---|---|
| `WMax` | `setMaxW` (required) |
| `VAMax` | `setMaxVA` |
| `VarMaxInj`, `VarMaxAbs` | `setMaxVar`, `setMaxVarNeg` |
| `WOvrExtPF`, `WUndExtPF` | `setMinPFOverExcited`, `setMinPFUnderExcited` |
| `WChaRteMax`, `VAChaRteMax` | `setMaxChargeRateW`, `setMaxChargeRateVA` |
| `WDisChaRteMax`, `VADisChaRteMax` | `setMaxDischargeRateW`, `setMaxDischargeRateVA` |
| `WhMax`, `AhMax` | `setMaxWh`, `setMaxAh` |
| `VNom`, `VMax`, `VMin` | `setVNom`, `setMaxV`, `setMinV` |
| `GradW` | `setGradW` |
| `CtrlModes` | `modesEnabled` |
| `DoeModesEnabled` | `doeModesEnabled` (CSIP-AUS extension) |
| `WMaxOvrExt`, `WMaxUndExt` | removed: no DERSettings element carries them, and nothing reads them |

### Status (`fetch_status`) and availability (`fetch_availability`)

Already IEEE 2030.5 element names and values, checked against their types
since the change that added DERStatus validation. Unchanged.

### Controls (`update_*`)

Two options; the second is recommended.

1. **Rename only what is SunSpec-named.** Keep the `update_*` methods and their
   parameter dicts, and rename the parameters that use SunSpec point names:
   `WSet`, `WSetEna`, `WSetMod` (`update_fixed_w`), `*_deptref` and `*_olrt`
   (curve modes), `pf_kof`, `pf_kuf`, `pf_dbof`, `pf_dbuf` (frequency droop).
   Smallest change; the method names stay the library's own.
2. **One method per DERControlBase element.** `update_<element>` named after
   the IEEE 2030.5 element in snake case (`update_op_mod_volt_var`,
   `update_op_mod_max_lim_w`, `update_op_mod_fixed_pf_inject_w`, ...), receiving
   that element's value as IEEE 2030.5 defines it (a curve with its
   `xMultiplier`, `yMultiplier`, `yRefType` and points; an `ActivePower`; a
   `PowerFactorWithExcitation`) plus `enabled`. A connector then reads the
   control in the standard's own terms, and the translation table in `modes.py`
   becomes a dispatch table.

## What it breaks

Every connector outside the package: each `fetch_monitoring`,
`fetch_nameplate` and `fetch_configuration` implementation, every
`reading_overrides()`, and every `update_*` that reads a renamed parameter.
Embedding applications that read the monitoring dict (for example to display
or forward it) break too. The print connector in the package is updated with
the rename.

## Rejected

- **Keeping the SunSpec names as aliases.** Two vocabularies for one value is
  what the change removes, and an alias period would keep the library mapping
  SunSpec names indefinitely.
- **Keeping `ReadingOverride` beside `Reading`.** Every field it sets belongs to
  the reading; two ways to set a phase invite the ReadingType changes Rule n
  forbids.
