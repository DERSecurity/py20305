from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from xsdata_pydantic.fields import field

__NAMESPACE__ = "urn:ieee:std:2030.5:ns"


class AccumulationBehaviourType(BaseModel):
    """
    0 = Not Applicable (default, if not specified) 1 = BulkQuantity A value
    from a register which represents the bulk quantity of a commodity.

    This quantity is computed as the integral of the commodity usage rate.
    This value is typically used as the basis for the dial reading at the
    meter, and as a result, will roll over upon reaching a maximum dial
    value. Note: The roll-over behavior typically implies a roll-under
    behavior so that the value presented is always a positive value (e.g.,
    unsigned integer or positive decimal). Note: A BulkQuantity refers
    primarily to the dial reading and not the consumption over a specified
    period of time. 3 = Cumulative The sum of the previous billing period
    values. Note: “Cumulative” is commonly used in conjunction with
    “demand.” Each demand reset causes the maximum demand value for the
    present billing period (since the last demand reset) to accumulate as
    an accumulative total of all maximum demands. So instead of “zeroing”
    the demand register, a demand reset has the affect of adding the
    present maximum demand to this accumulating total. 4 = DeltaData The
    difference between the value at the end of the prescribed interval and
    the beginning of the interval. This is used for incremental interval
    data. Note: One common application would be for load profile data,
    another use might be to report the number of events within an interval
    (such as the number of equipment energizations within the specified
    period of time.) 6 = Indicating As if a needle is swung out on the
    meter face to a value to indicate the current value. (Note: An
    “indicating” value is typically measured over hundreds of milliseconds
    or greater, or may imply a “pusher” mechanism to capture a value.
    Compare this to “instantaneous” which is measured over a shorter period
    of time.) 9 = Summation A form of accumulation which is selective with
    respect to time. Note : “Summation” could be considered a
    specialization of “Bulk Quantity” according to the rules of inheritance
    where “Summation” selectively accumulates pulses over a timing pattern,
    and “BulkQuantity” accumulates pulses all of the time. 12 =
    Instantaneous Typically measured over the fastest period of time
    allowed by the definition of the metric (usually milliseconds or tens
    of milliseconds.) (Note: “Instantaneous” was moved to attribute #3 in
    61968-9Ed2 from attribute #1 in 61968-9Ed1.) All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class AggregationDistributionType(BaseModel):
    """
    Specifies how to distribute a control across the population of
    aggregated devices to achieve the specified total: 0 - Not applicable /
    Unspecified 1 - Uniform: use an equal value for each member of the
    aggregation 2 - Prorate: use an equal percentage of nameplate rating 3
    - Priority: prioritized based on the given AggregationPriority, with
    each member of the AggregationPriority completely utilized before
    proceeding to the next member of the AggregationPriority All other
    values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ApplianceLoadReductionType(BaseModel):
    """
    0 - Delay Appliance Load Parameter requesting the appliance to respond
    by providing a moderate load reduction for the duration of a delay
    period.

    Typically referring to a “non-emergency” event in which appliances can
    continue operating if already in a load consuming period. 1 - Temporary
    Appliance Load Reduction Parameter requesting the appliance to respond
    by providing an aggressive load reduction for a short time period.
    Typically referring to an “emergency/spinning reserve” event in which
    an appliance should start shedding load if currently in a load
    consuming period. * Full definition of how appliances react when
    receiving each parameter is document in the EPA document - ENERGY STAR®
    Program Requirements, Product Specification for Residential
    Refrigerators and Freezers, Eligibility Criteria 5, Draft 2 Version
    5.0. All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ChargeKind(BaseModel):
    """
    Kind of charge. 0 - Consumption Charge 1 - Rebate 2 - Auxiliary Charge
    3 - Demand Charge 4 - Tax Charge.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class CommodityType(BaseModel):
    """
    0 = Not Applicable (default, if not specified) 1 = Electricity
    secondary metered value (a premises meter is typically on the low
    voltage, or secondary, side of a service transformer) 2 = Electricity
    primary metered value (measured on the high voltage, or primary, side
    of the service transformer) 4 = Air 7 = NaturalGas 8 = Propane 9 =
    PotableWater 10 = Steam 11 = WasteWater 12 = HeatingFluid 13 =
    CoolingFluid All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ConsumptionBlockType(BaseModel):
    """
    0 = Not Applicable (default, if not specified) 1 = Block 1 2 = Block 2
    3 = Block 3 4 = Block 4 5 = Block 5 6 = Block 6 7 = Block 7 8 = Block 8
    9 = Block 9 10 = Block 10 11 = Block 11 12 = Block 12 13 = Block 13 14
    = Block 14 15 = Block 15 16 = Block 16 All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class CostKindType(BaseModel):
    """
    0 - Carbon Dioxide emissions, in grams per unit 1 - Sulfur Dioxide
    emissions, in grams per unit 2 - Nitrogen Oxides emissions, in grams
    per unit 3 - Renewable generation, as a percentage of overall
    generation All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class CountryType(BaseModel):
    """
    [ISO 3166-1] Alpha-2 code of a country.
    """

    model_config = ConfigDict(defer_build=True)
    value: str = field(
        default="",
        metadata={
            "required": True,
            "max_length": 2,
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class CreditStatusType(BaseModel):
    """
    0 - Credit Ok 1 - Credit Low 2 - Credit Exhausted 3 - Credit Negative
    All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class CreditTypeType(BaseModel):
    """
    0 - Regular 1 - Emergency 2 - Regular, then Emergency 3 - Emergency,
    then Regular All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class CurrencyCode(BaseModel):
    """
    Follows codes defined in [ISO 4217]. 0 - Not Applicable (default, if
    not specified) 36 - Australian Dollar 124 - Canadian Dollar 840 - US
    Dollar 978 - Euro This is not a complete list.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DercontrolType(BaseModel):
    """
    DERControl Modes for DER.

    Bit positions SHALL be defined as follows: 0 - Charge mode 1 -
    Discharge mode 2 - opModConnect 3 - opModEnergize 4 -
    opModFixedPFAbsorbW 5 - opModFixedPFInjectW 6 - opModFixedVar 7 -
    opModFixedW 8 - opModFreqDroop 9 - opModFreqWatt 10 - opModHFRTMayTrip
    11 - opModHFRTMustTrip 12 - opModHVRTMayTrip 13 -
    opModHVRTMomentaryCessation 14 - opModHVRTMustTrip 15 -
    opModLFRTMayTrip 16 - opModLFRTMustTrip 17 - opModLVRTMayTrip 18 -
    opModLVRTMomentaryCessation 19 - opModLVRTMustTrip 20 - opModMaxLimW 21
    - opModTargetVar 22 - opModTargetW 23 - opModVoltVar 24 - opModVoltWatt
    25 - opModWattPF 26 - opModWattVar Below values added in IEEE
    2030.5-2023 revision: 27 = opModDeltaVar 28 = opModDeltaW 29 =
    opModFixedV 30 = opModGridConnectPermit 31 = opModIslandPermit.
    """

    class Meta:
        name = "DERControlType"

    model_config = ConfigDict(defer_build=True)
    value: bytes = field(
        default=b"",
        metadata={
            "required": True,
            "max_length": 4,
            "format": "base16",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DercontrolType2(BaseModel):
    """
    Additional DERControl Modes for DER.

    Added in the IEEE 2030.5-2023 revision. Bit positions SHALL be defined
    as follows: 0 = opModMaxLimPctVAAbsorb 1 = opModMaxLimPctVAInject 2 =
    opModMaxLimPctVarAbsorb 3 = opModMaxLimPctVarInject 4 =
    opModMaxLimPctWAbsorb 5 = opModMaxLimVarAbsorb 6 = opModMaxLimVarInject
    7 = opModMaxLimWAbsorb 8 = opModMaxLimWInject 9 = opModTargetV All
    other values reserved.
    """

    class Meta:
        name = "DERControlType2"

    model_config = ConfigDict(defer_build=True)
    value: bytes = field(
        default=b"",
        metadata={
            "required": True,
            "max_length": 4,
            "format": "base16",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DercurveType(BaseModel):
    """
    0 - opModFreqWatt (Frequency-Watt Curve DERControl Mode) 1 -
    opModHFRTMayTrip (High Frequency Ride Through, May Trip DERControl
    Mode) 2 - opModHFRTMustTrip (High Frequency Ride Through, Must Trip
    DERControl Mode) 3 - opModHVRTMayTrip (High Voltage Ride Through, May
    Trip DERControl Mode) 4 - opModHVRTMomentaryCessation (High Voltage
    Ride Through, Momentary Cessation DERControl Mode) 5 -
    opModHVRTMustTrip (High Voltage Ride Through, Must Trip DERControl
    Mode) 6 - opModLFRTMayTrip (Low Frequency Ride Through, May Trip
    DERControl Mode) 7 - opModLFRTMustTrip (Low Frequency Ride Through,
    Must Trip DERControl Mode) 8 - opModLVRTMayTrip (Low Voltage Ride
    Through, May Trip DERControl Mode) 9 - opModLVRTMomentaryCessation (Low
    Voltage Ride Through, Momentary Cessation DERControl Mode) 10 -
    opModLVRTMustTrip (Low Voltage Ride Through, Must Trip DERControl Mode)
    11 - opModVoltVar (Volt-Var DERControl Mode) 12 - opModVoltWatt
    (Volt-Watt DERControl Mode) 13 - opModWattPF (Watt-PowerFactor
    DERControl Mode) 14 - opModWattVar (Watt-Var DERControl Mode) All other
    values reserved.
    """

    class Meta:
        name = "DERCurveType"

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Dertype(BaseModel):
    """
    0 - Not applicable / Unknown 1 - Virtual or mixed DER 2 - Reciprocating
    engine 3 - Fuel cell 4 - Photovoltaic system 5 - Combined heat and
    power 6 - Other generation system 80 - Other storage system 81 -
    Electric vehicle 82 - EVSE 83 - Combined PV and storage All other
    values reserved.
    """

    class Meta:
        name = "DERType"

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DerunitRefType(BaseModel):
    """
    Specifies context for interpreting percent values: 0 - N/A 1 - %setMaxW
    2 - %setMaxVar 3 - %statVarAvail 4 - %setEffectiveV 5 -
    %setMaxChargeRateW 6 - %setMaxDischargeRateW 7 - %statWAvail 8 -
    %setMaxVA All other values reserved.

    For %setMaxVar, if the device supports both setMaxVar and setMaxVarNeg,
    then %setMaxVar uses the percentage of setMaxVarNeg for negative
    values. If the device only supports setMaxVar, then %setMaxVar uses the
    percentage of (-1 * setMaxVar) for negative values. For %setMaxW, if
    the values are negative, %setMaxChargeRateW is used. For %setMaxW, if
    the values are positive, either %setMaxW or %setMaxDischargeRateW can
    be used.
    """

    class Meta:
        name = "DERUnitRefType"

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DataQualifierType(BaseModel):
    """
    0 = Not Applicable (default, if not specified) 2 = Average 8 = Maximum
    9 = Minimum 12 = Normal 29 = Standard Deviation of a Population
    (typically indicated by a lower case sigma) 30 = Standard Deviation of
    a Sample Drawn from a Population (typically indicated by a lower case
    's') All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DefaultDercontrolType(BaseModel):
    """
    DefaultDERControl elements.

    Bit positions SHALL be defined as follows: 0 - setESDelay 1 -
    setESHighFreq 2 - setESHighVolt 3 - setESLowFreq 4 - setESLowVolt 5 -
    setESRampTms 6 - setESRandomDelay 7 - setGradW 8 - setSoftGradW All
    other values reserved.
    """

    class Meta:
        name = "DefaultDERControlType"

    model_config = ConfigDict(defer_build=True)
    value: bytes = field(
        default=b"",
        metadata={
            "required": True,
            "max_length": 4,
            "format": "base16",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DeviceCategoryType(BaseModel):
    """
    The Device category types defined.

    Bit positions SHALL be defined as follows: 0 - Programmable
    Communicating Thermostat 1 - Strip Heaters 2 - Baseboard Heaters 3 -
    Water Heater 4 - Pool Pump 5 - Sauna 6 - Hot Tub 7 - Smart Appliance 8
    - Irrigation Pump 9 - Managed Commercial and Industrial (C&amp;amp;I)
    Loads 10 - Simple Misc. (Residential On/Off) Loads 11 - Exterior
    Lighting 12 - Interior Lighting 13 - Load Control Switch 14 - Energy
    Management System 15 - Smart Energy Module 16 - Electric Vehicle 17 -
    EVSE 18 - Virtual or Mixed DER 19 - Reciprocating Engine 20 - Fuel Cell
    21 - Photovoltaic System 22 - Combined Heat and Power 23 - Combined PV
    and Storage 24 - Other Generation System 25 - Other Storage System 26 -
    Microgrid Controller All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: bytes = field(
        default=b"",
        metadata={
            "required": True,
            "max_length": 4,
            "format": "base16",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DstRuleType(BaseModel):
    """
    Bit map encoded rule from which is calculated the start or end time,
    within the current year, to which daylight savings time offset must be
    applied.

    The rule encoding: Bits 0 - 11: seconds 0 - 3599 Bits 12 - 16: hours 0
    - 23 Bits 17 - 19: day of the week 0 = not applicable, 1 - 7 (Monday =
    1) Bits 20 - 24: day of the month 0 = not applicable, 1 - 31 Bits 25 -
    27: operator (detailed below) Bits 28 - 31: month 1 - 12 Rule value of
    0xFFFFFFFF means rule processing/DST correction is disabled. The
    operators: 0: DST starts/ends on the Day of the Month 1: DST
    starts/ends on the Day of the Week that is on or after the Day of the
    Month 2: DST starts/ends on the first occurrence of the Day of the Week
    in a month 3: DST starts/ends on the second occurrence of the Day of
    the Week in a month 4: DST starts/ends on the third occurrence of the
    Day of the Week in a month 5: DST starts/ends on the forth occurrence
    of the Day of the Week in a month 6: DST starts/ends on the fifth
    occurrence of the Day of the Week in a month 7: DST starts/ends on the
    last occurrence of the Day of the Week in a month An example: DST
    starts on third Friday in March at 1:45 AM. The rule... Seconds: 2700
    Hours: 1 Day of Week: 5 Day of Month: 0 Operator: 4 Month: 3.
    """

    model_config = ConfigDict(defer_build=True)
    value: bytes = field(
        default=b"",
        metadata={
            "required": True,
            "max_length": 4,
            "format": "base16",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class FlowDirectionType(BaseModel):
    """
    The following are recommended values sourced from the flow direction
    enumeration in IEC 61968-9 [61968].

    Note that IEEE 2030.5 uses the Generator/Producer frame of reference,
    where "Forward" is defined as flow from a generator to a load. Example
    generators include DER such as solar inverters as well as flow from a
    grid to a premises. 0 = Not Applicable (default, if not specified) 1 =
    Forward Also known as "delivered" or "injected." Values using the
    Forward flow direction SHALL be positive. 2 = Lagging Values using the
    Lagging flow direction SHALL be positive. 3 = Leading Values using the
    Leading flow direction SHALL be positive. 4 = Net Defined as the
    absolute value of the Forward flow direction - the absolute value of
    the Reverse flow direction. 19 = Reverse Also known as "received" or
    "absorbed." Values using the Reverse flow direction SHALL be positive.
    20 = Total Defined as the absolute value of the Forward flow direction
    + the absolute value of the Reverse flow direction. For polyphase
    measurement data, values using the Total flow direction are incremented
    when the absolute value of the sum of the phases is greater than zero.
    Values using the Total flow direction SHALL be positive. 21 =
    TotalByPhase Values using the TotalByPhase flow direction are
    incremented when the sum of the absolute values of the phases is
    greater than zero. The TotalByPhase flow direction SHOULD NOT be used
    for single phase measurement data. Values using the TotalByPhase flow
    direction SHALL be positive. Other values from the flow direction
    enumeration in Table C.4 of IEC 61968-9 [61968] Edition 1.0 (2009-09)
    MAY be used. All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class KindType(BaseModel):
    """
    0 = Not Applicable (default, if not specified) 3 = Currency 8 = Demand
    12 = Energy 37 = Power All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class LocaleType(BaseModel):
    """
    [RFC 5646] identifier of a language-region.
    """

    model_config = ConfigDict(defer_build=True)
    value: str = field(
        default="",
        metadata={
            "required": True,
            "max_length": 42,
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class OneHourRangeType(BaseModel):
    """
    A signed time offset, typically applied to a Time value, expressed in
    seconds, with range -3600 to 3600.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Pentype(BaseModel):
    """
    IANA Private Enterprise Number [PEN].
    """

    class Meta:
        name = "PENType"

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Pintype(BaseModel):
    """
    6 digit unsigned decimal integer (0 - 999999). (Note that this only
    requires 20 bits, if it can be allocated.).
    """

    class Meta:
        name = "PINType"

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PerCent(BaseModel):
    """
    Used for percentages, specified in hundredths of a percent, 0 - 10000.
    (10000 = 100%).
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PhaseCode(BaseModel):
    """
    0 = Not Applicable (default, if not specified) 32 = Phase C (and S2) 33
    = Phase CN (and S2N) 40 = Phase CA 64 = Phase B 65 = Phase BN 66 =
    Phase BC 128 = Phase A (and S1) 129 = Phase AN (and S1N) 132 = Phase AB
    224 = Phase ABC All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PowerOfTenMultiplierType(BaseModel):
    """
    -9 = nano=x10^-9 -6 = micro=x10^-6 -3 = milli=x10^-3 0 = none=x1
    (default, if not specified) 1 = deca=x10 2 = hecto=x100 3 = kilo=x1000
    6 = Mega=x10^6 9 = Giga=x10^9 This is not a complete list.

    Any integer between -9 and 9 SHALL be supported, indicating the power
    of ten multiplier for the units.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PowerSourceType(BaseModel):
    """
    0 - none 1 - mains 2 - battery 3 - local generation 4 - emergency 5 -
    unknown All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PrepayModeType(BaseModel):
    """
    0 - Central Wallet 1 - ESI 2 - Local 3 - Credit All other values
    reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PrimacyType(BaseModel):
    """
    Values possible for indication of "Primary" provider: 0: In home energy
    management system 1: Contracted premises service provider 2:
    Non-contractual service provider 3 - 64: Reserved 65 - 191:
    User-defined 192 - 255: Reserved Lower numbers indicate higher
    priority.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PriorityType(BaseModel):
    """
    Indicates the priority of a message: 0 - Low 1 - Normal 2 - High 3 -
    Critical All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Revision23Type(BaseModel):
    class Meta:
        name = "Revision2_3Type"

    model_config = ConfigDict(defer_build=True)
    target_namespace_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##targetNamespace",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class RoleFlagsType(BaseModel):
    """
    Specifies the roles that apply to a usage point.

    Bit 0 - isMirror - SHALL be set if the server is not the measurement
    device Bit 1 - isPremisesAggregationPoint - SHALL be set if the
    UsagePoint is the point of delivery for a premises Bit 2 - isPEV -
    SHALL be set if the usage applies to an electric vehicle Bit 3 - isDER
    - SHALL be set if the usage applies to a distributed energy resource,
    capable of delivering power to the grid. Bit 4 - isRevenueQuality -
    SHALL be set if usage was measured by a device certified as revenue
    quality Bit 5 - isDC - SHALL be set if the usage point measures direct
    current Bit 6 - isSubmeter - SHALL be set if the usage point is not a
    premises aggregation point Bit 7-15 - Reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: bytes = field(
        default=b"",
        metadata={
            "required": True,
            "max_length": 2,
            "format": "base16",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Sfditype(BaseModel):
    """
    Unsigned integer, max inclusive 687194767359, which is 2^36-1
    (68719476735), with added check digit.

    See Section 6.3.3 for check digit calculation.
    """

    class Meta:
        name = "SFDIType"

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
            "max_inclusive": 281474976710655,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ServiceKind(BaseModel):
    """
    Service kind 0 - electricity 1 - gas 2 - water 3 - time 4 - pressure 5
    - heat 6 - cooling All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ServiceStatusType(BaseModel):
    """
    0 - Connected 1 - Disconnected 2 - Armed for Connect 3 - Armed for
    Disconnect 4 - No Contactor 5 - Load Limited All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class SignedPerCent(BaseModel):
    """
    Used for signed percentages, specified in hundredths of a percent,
    -10000 - 10000. (10000 = 100%).
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class SubdivisionType(BaseModel):
    """
    [ISO 3166-2] subdivision code of a country.
    """

    model_config = ConfigDict(defer_build=True)
    value: str = field(
        default="",
        metadata={
            "required": True,
            "max_length": 3,
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Toutype(BaseModel):
    """
    0 = Not Applicable (default, if not specified) 1 = TOU A 2 = TOU B 3 =
    TOU C 4 = TOU D 5 = TOU E 6 = TOU F 7 = TOU G 8 = TOU H 9 = TOU I 10 =
    TOU J 11 = TOU K 12 = TOU L 13 = TOU M 14 = TOU N 15 = TOU O All other
    values reserved.
    """

    class Meta:
        name = "TOUType"

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class TimeOffsetType(BaseModel):
    """
    A signed time offset, typically applied to a Time value, expressed in
    seconds.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class TimeType(BaseModel):
    """
    Time is a signed 64 bit value representing the number of seconds since
    0 hours, 0 minutes, 0 seconds, on the 1st of January, 1970, in UTC, not
    counting leap seconds.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class UnitType(BaseModel):
    """
    The unit types defined for end device control target reductions. 0 -
    kWh 1 - kW 2 - Watts 3 - Cubic Meters 4 - Cubic Feet 5 - US Gallons 6 -
    Imperial Gallons 7 - BTUs 8 - Liters 9 - kPA (gauge) 10 - kPA
    (absolute) 11 - Mega Joule 12 - Unitless All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class UomType(BaseModel):
    """
    The following values are recommended values sourced from the unit of
    measure enumeration in IEC 61968-9 [61968].

    Other values from the unit of measure enumeration in IEC 61968-9
    [61968] MAY be used. 0 = Not Applicable (default, if not specified) 5 =
    A (Current in Amperes (RMS)) 6 = Kelvin (Temperature) 23 = Degrees
    Celsius (Relative temperature) 29 = Voltage 31 = J (Energy joule) 33 =
    Hz (Frequency) 38 =W (Real power in Watts) 42 = m3 (Cubic Meter) 61 =
    VA (Apparent power) 63 = var (Reactive power) 65 = CosTheta
    (Displacement Power Factor) 67 = V² (Volts squared) 69 = A² (Amp
    squared) 71 = VAh (Apparent energy) 72 = Wh (Real energy in Watt-hours)
    73 = varh (Reactive energy) 106 = Ah (Ampere-hours / Available Charge)
    119 = ft3 (Cubic Feet) 122 = ft3/h (Cubic Feet per Hour) 125 = m3/h
    (Cubic Meter per Hour) 128 = US gl (US Gallons) 129 = US gl/h (US
    Gallons per Hour) 130 = IMP gl (Imperial Gallons) 131 = IMP gl/h
    (Imperial Gallons per Hour) 132 = BTU 133 = BTU/h 134 = Liter 137 = L/h
    (Liters per Hour) 140 = PA(gauge) 155 = PA(absolute) 169 = Therm.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class VersionType(BaseModel):
    """
    Version SHALL indicate a distinct identifier for each revision of an
    IdentifiedObject.

    If not specified, a default version of "0" (initial version) SHALL be
    assumed. Upon modification of any IdentifiedObject, the mRID SHALL
    remain the same, but the version SHALL be incremented. Servers MAY NOT
    modify objects that they did not create, unless they were notified of
    the change from the entity controlling the object's PEN.
    """

    model_config = ConfigDict(defer_build=True)
    value: int = field(
        metadata={
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class MRidtype(BaseModel):
    """
    A master resource identifier.

    The IANA PEN [PEN] provider ID SHALL be specified in bits 0-31, the
    least-significant bits, and objects created by that provider SHALL be
    assigned unique IDs with the remaining 96 bits.
    0xFFFFFFFFFFFFFFFFFFFFFFFF[XXXXXXXX], where [XXXXXXXX] is the PEN, is
    reserved for a object that is being created (e.g., a ReadingSet for the
    current time that is still accumulating). Except for this special
    reserved identifier, each modification of an object (resource)
    representation SHALL have a different "version".
    """

    class Meta:
        name = "mRIDType"

    model_config = ConfigDict(defer_build=True)
    value: bytes = field(
        default=b"",
        metadata={
            "required": True,
            "max_length": 16,
            "format": "base16",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ActivePower(BaseModel):
    """
    The active (real) power P (in W) is the product of root-mean-square
    (RMS) voltage, RMS current, and cos(theta) where theta is the phase
    angle of current relative to voltage.

    It is the primary measure of the rate of flow of energy.

    :ivar multiplier: Specifies exponent for uom.
    :ivar value: Value in watts (uom 38)
    :ivar active_power_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    active_power_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActivePower_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class AmpereHour(BaseModel):
    """
    Available electric charge.

    :ivar multiplier: Specifies exponent of uom.
    :ivar value: Value in ampere-hours (uom 106)
    :ivar ampere_hour_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    ampere_hour_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AmpereHour_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ApparentPower(BaseModel):
    """
    The apparent power S (in VA) is the product of root mean square (RMS)
    voltage and RMS current.

    :ivar multiplier: Specifies exponent of uom.
    :ivar value: Value in volt-amperes (uom 61)
    :ivar apparent_power_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    apparent_power_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ApparentPower_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ApplianceLoadReduction(BaseModel):
    """
    The ApplianceLoadReduction object is used by a Demand Response service
    provider to provide signals for ENERGY STAR compliant appliances.

    See the definition of ApplianceLoadReductionType for more information.

    :ivar type_value: Indicates the type of appliance load reduction
        requested.
    :ivar appliance_load_reduction_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    type_value: ApplianceLoadReductionType = field(
        metadata={
            "name": "type",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    appliance_load_reduction_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ApplianceLoadReduction_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class AppliedTargetReduction(BaseModel):
    """
    Specifies the value of the TargetReduction applied by the device.

    :ivar type_value: Enumerated field representing the type of
        reduction requested.
    :ivar value: Indicates the requested amount of the relevant
        commodity to be reduced.
    :ivar applied_target_reduction_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    type_value: UnitType = field(
        metadata={
            "name": "type",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    applied_target_reduction_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AppliedTargetReduction_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Charge(BaseModel):
    """
    Charges contain charges on a customer bill.

    These could be items like taxes, levies, surcharges, rebates, or
    others. This is meant to allow the device to retrieve enough
    information to be able to reconstruct an estimate of what the total
    bill would look like. Providers can provide line item billing,
    including multiple charge kinds (e.g. taxes, surcharges) at whatever
    granularity desired, using as many Charges as desired during a billing
    period. There can also be any number of Charges associated with
    different ReadingTypes to distinguish between TOU tiers, consumption
    blocks, or demand charges.

    :ivar description: A description of the charge.
    :ivar kind: The type (kind) of charge.
    :ivar value: A monetary charge.
    :ivar charge_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    description: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 20,
        },
    )
    kind: None | ChargeKind = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    charge_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Charge_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Condition(BaseModel):
    """
    Indicates a condition that must be satisfied for the Notification to be
    triggered.

    :ivar attribute_identifier: 0 = Reading value 1-255 = Reserved
    :ivar lower_threshold: The value of the lower threshold
    :ivar upper_threshold: The value of the upper threshold
    :ivar condition_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    attribute_identifier: int = field(
        metadata={
            "name": "attributeIdentifier",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    lower_threshold: int = field(
        metadata={
            "name": "lowerThreshold",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "min_inclusive": -140737488355328,
            "max_inclusive": 140737488355328,
        }
    )
    upper_threshold: int = field(
        metadata={
            "name": "upperThreshold",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "min_inclusive": -140737488355328,
            "max_inclusive": 140737488355328,
        }
    )
    condition_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Condition_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ConnectStatusType(BaseModel):
    """
    DER ConnectStatus value (bitmap): 0 - Connected 1 - Available 2 -
    Operating 3 - Test 4 - Fault / Error All other values reserved.

    :ivar date_time: The date and time at which the state applied.
    :ivar value: The value indicating the state.
    :ivar connect_status_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    date_time: TimeType = field(
        metadata={
            "name": "dateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: bytes = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 1,
            "format": "base16",
        }
    )
    connect_status_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ConnectStatusType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ConnectStatusType2(BaseModel):
    """
    DER ConnectStatus value (bitmap): 0 - Connected DER is connected (1) or
    disconnected (0).

    Implies galvanic isolation. 1 - Energized DER is energized (1) or
    de-energized (0). All other values reserved.

    :ivar date_time: The date and time at which the state applied.
    :ivar value: The value indicating the state.
    :ivar connect_status_type2_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    date_time: TimeType = field(
        metadata={
            "name": "dateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: bytes = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 1,
            "format": "base16",
        }
    )
    connect_status_type2_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ConnectStatusType2_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class CreditTypeChange(BaseModel):
    """
    Specifies a change to the credit type.

    :ivar new_type: The new credit type, to take effect at the time
        specified by startTime
    :ivar start_time: The date/time when the change is to take effect.
    :ivar credit_type_change_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    new_type: CreditTypeType = field(
        metadata={
            "name": "newType",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    start_time: TimeType = field(
        metadata={
            "name": "startTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    credit_type_change_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CreditTypeChange_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class CurrentRms(BaseModel):
    """
    Average flow of charge through a conductor.

    :ivar multiplier: Specifies exponent of value.
    :ivar value: Value in amperes RMS (uom 5)
    :ivar current_rms_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    class Meta:
        name = "CurrentRMS"

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    current_rms_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CurrentRMS_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class CurveData(BaseModel):
    """
    Data point values for defining a curve or schedule.

    :ivar excitation: If yvalue is Power Factor, then this field SHALL
        be present. If yvalue is not Power Factor, then this field SHALL
        NOT be present. True when DER is absorbing reactive power
        (under-excited), false when DER is injecting reactive power
        (over-excited).
    :ivar xvalue: The data value of the X-axis (independent) variable,
        depending on the curve type. See definitions in DERControlBase
        for further information.
    :ivar yvalue: The data value of the Y-axis (dependent) variable,
        depending on the curve type. See definitions in DERControlBase
        for further information. If yvalue is Power Factor, the
        excitation field SHALL be present and yvalue SHALL be a positive
        value. If yvalue is not Power Factor, the excitation field SHALL
        NOT be present.
    :ivar curve_data_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    excitation: None | bool = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    xvalue: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    yvalue: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    curve_data_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CurveData_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DateTimeInterval(BaseModel):
    """
    Interval of date and time.

    :ivar duration: Duration of the interval, in seconds.
    :ivar start: Date and time of the start of the interval.
    :ivar date_time_interval_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    duration: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    start: TimeType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    date_time_interval_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DateTimeInterval_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DutyCycle(BaseModel):
    """
    Duty cycle control is a device specific issue and is managed by the
    device.

    The duty cycle of the device under control should span the shortest
    practical time period in accordance with the nature of the device under
    control and the intent of the request for demand reduction. The default
    factory setting SHOULD be three minutes for each 10% of duty cycle.
    This indicates that the default time period over which a duty cycle is
    applied is 30 minutes, meaning a 10% duty cycle would cause a device to
    be ON for 3 minutes. The “off state” SHALL precede the “on state”.

    :ivar normal_value: Contains the maximum On state duty cycle applied
        by the end device, as a percentage of time.  The field not
        present indicates that this field has not been used by the end
        device.
    :ivar duty_cycle_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    normal_value: int = field(
        metadata={
            "name": "normalValue",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    duty_cycle_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DutyCycle_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class EnvironmentalCost(BaseModel):
    """
    Provides alternative or secondary price information for the relevant
    RateComponent.

    Supports jurisdictions that seek to convey the environmental price per
    unit of the specified commodity not expressed in currency. Implementers
    and consumers can use this attribute to prioritize operations of their
    devices (e.g., PEV charging during times of high availability of
    renewable electricity resources).

    :ivar amount: The estimated or actual environmental or other cost,
        per commodity unit defined by the ReadingType, for this
        RateComponent (e.g., grams of carbon dioxide emissions each per
        kWh).
    :ivar cost_kind: The kind of cost referred to in the amount.
    :ivar cost_level: The relative level of the amount attribute.  In
        conjunction with numCostLevels, this attribute informs a device
        of the relative scarcity of the amount attribute (e.g., a high
        or low availability of renewable generation). numCostLevels and
        costLevel values SHALL ascend in order of scarcity, where "0"
        signals the lowest relative cost and higher values signal
        increasing cost.  For example, if numCostLevels is equal to “3,”
        then if the lowest relative costLevel were equal to “0,” devices
        would assume this is the lowest relative period to operate.
        Likewise, if the costLevel in the next TimeTariffInterval
        instance is equal to “1,” then the device would assume it is
        relatively more expensive, in environmental terms, to operate
        during this TimeTariffInterval instance than the previous one.
        There is no limit to the number of relative price levels other
        than that indicated in the attribute type, but for practicality,
        service providers should strive for simplicity and recognize the
        diminishing returns derived from increasing the numCostLevel
        value greater than four.
    :ivar num_cost_levels: The number of all relative cost levels. In
        conjunction with costLevel, numCostLevels signals the relative
        scarcity of the commodity for the duration of the
        TimeTariffInterval instance (e.g., a relative indication of
        cost). This is useful in providing context for nominal cost
        signals to consumers or devices that might see a range of amount
        values from different service providres or from the same service
        provider.
    :ivar environmental_cost_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    amount: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    cost_kind: CostKindType = field(
        metadata={
            "name": "costKind",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    cost_level: int = field(
        metadata={
            "name": "costLevel",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    num_cost_levels: int = field(
        metadata={
            "name": "numCostLevels",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    environmental_cost_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "EnvironmentalCost_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Error1(BaseModel):
    """
    Contains information about the nature of an error if a request could
    not be completed successfully.

    :ivar max_retry_duration: Contains the number of seconds the client
        SHOULD wait before retrying the request.
    :ivar reason_code: Code indicating the reason for failure. 0 -
        Invalid request format 1 - Invalid request values (e.g. invalid
        threshold values) 2 - Resource limit reached 3 - Conditional
        subscription field not supported 4 - Maximum request frequency
        exceeded All other values reserved
    :ivar error_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    class Meta:
        name = "Error"

    model_config = ConfigDict(defer_build=True)
    max_retry_duration: None | int = field(
        default=None,
        metadata={
            "name": "maxRetryDuration",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reason_code: int = field(
        metadata={
            "name": "reasonCode",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    error_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Error_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class EventStatus(BaseModel):
    """
    Current status information relevant to a specific object.

    The Status object is used to indicate the current status of an Event.
    Devices can read the containing resource (e.g. TextMessage) to get the
    most up to date status of the event. Devices can also subscribe to a
    specific resource instance to get updates when any of its attributes
    change, including the Status object.

    :ivar current_status: Field representing the current status type. 0
        = Scheduled This status indicates that the event has been
        scheduled and the event has not yet started.  The server SHALL
        set the event to this status when the event is first scheduled
        and persist until the event has become active or has been
        cancelled.  For events with a start time less than or equal to
        the current time, this status SHALL never be indicated, the
        event SHALL start with a status of “Active”. 1 = Active This
        status indicates that the event is currently active, even if the
        event is known to be overlapped. The server SHALL set the event
        to this status when the event reaches its earliest Effective
        Start Time. 2 = Cancelled When events are cancelled, the
        Status.dateTime attribute SHALL be set to the time the
        cancellation occurred, which cannot be in the future.  The
        server is responsible for maintaining the cancelled event in its
        collection for the duration of the original event, or until the
        server has run out of space and needs to store a new event.
        Client devices SHALL be aware of Cancelled events, determine if
        the Cancelled event applies to them, and cancel the event
        immediately if applicable. 3 = Cancelled with Randomization The
        server is responsible for maintaining the cancelled event in its
        collection for the duration of the Effective Scheduled Period.
        Client devices SHALL be aware of Cancelled with Randomization
        events, determine if the Cancelled event applies to them, and
        cancel the event immediately, using the larger of (absolute
        value of randomizeStart) and (absolute value of
        randomizeDuration) as the end randomization, in seconds. This
        Status.type SHALL NOT be used with "regular" Events, only with
        specializations of RandomizableEvent. 4 = Superseded
        (DEPRECATED) SHALL NOT be used by servers, but clients should
        note that it may be used by servers compliant with previous
        revisions of IEEE 2030.5. 5 = Completed This status indicates
        that the event has completed. The server SHALL set the event to
        this status after the event's maximum Effective Scheduled Period
        if the event has not been cancelled and is still present on the
        server. Note that this status value was not present in revisions
        prior to IEEE 2030.5-2023. All other values reserved.
    :ivar date_time: The dateTime attribute will provide a timestamp of
        when the current status was defined. dateTime SHALL be set to
        the time at which the status change occurred, not a time in the
        future or past.
    :ivar potentially_superseded: DEPRECATED SHALL be set to true.
    :ivar potentially_superseded_time: DEPRECATED SHALL NOT be included
        by servers, but clients should note that it may be included by
        servers compliant with previous revisions of IEEE 2030.5.
    :ivar reason: The Reason attribute allows a Service provider to
        provide a textual explanation of the status.
    :ivar event_status_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    current_status: int = field(
        metadata={
            "name": "currentStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    date_time: TimeType = field(
        metadata={
            "name": "dateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    potentially_superseded: bool = field(
        metadata={
            "name": "potentiallySuperseded",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    potentially_superseded_time: None | TimeType = field(
        default=None,
        metadata={
            "name": "potentiallySupersededTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reason: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 192,
        },
    )
    event_status_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "EventStatus_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class FixedPointType(BaseModel):
    """
    Abstract type for specifying a fixed-point value without a given unit
    of measure.

    :ivar multiplier: Specifies exponent of uom.
    :ivar value: Dimensionless value
    :ivar fixed_point_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    fixed_point_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FixedPointType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class FixedVar(BaseModel):
    """
    Specifies a signed setpoint for reactive power.

    :ivar ref_type: Indicates how to interpret 'value.'
    :ivar value: Specify a signed setpoint for reactive power in % (see
        'refType' for context).
    :ivar fixed_var_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    ref_type: DerunitRefType = field(
        metadata={
            "name": "refType",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: SignedPerCent = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    fixed_var_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FixedVar_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class GpslocationType(BaseModel):
    """
    Specifies a GPS location, expressed in WGS 84 coordinates.

    :ivar lat: Specifies the latitude from equator. -90 (south) to +90
        (north) in decimal degrees.
    :ivar lon: Specifies the longitude from Greenwich Meridian. -180
        (west) to +180 (east) in decimal degrees.
    :ivar gpslocation_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    class Meta:
        name = "GPSLocationType"

    model_config = ConfigDict(defer_build=True)
    lat: str = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 32,
        }
    )
    lon: str = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 32,
        }
    )
    gpslocation_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "GPSLocationType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class GeographicLocationType(BaseModel):
    """
    :ivar country: [ISO 3166-1] Alpha-2 code of a country
    :ivar subdivision: [ISO 3166-2] subdivision code of a country
    :ivar geographic_location_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    country: CountryType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    subdivision: None | SubdivisionType = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    geographic_location_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "GeographicLocationType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class InverterStatusType(BaseModel):
    """
    DER InverterStatus value: 0 - N/A 1 - off 2 - sleeping (auto-shutdown)
    or DER is at low output power/voltage 3 - starting up or ON but not
    producing power 4 - running 5 - forced power reduction/derating 6 -
    shutting down 7 - one or more faults exist 8 - standby (service on
    unit) - DER may be at high output voltage/power 9 - test mode 10 - as
    defined in manufacturer status All other values reserved.

    :ivar date_time: The date and time at which the state applied.
    :ivar value: The value indicating the state.
    :ivar inverter_status_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    date_time: TimeType = field(
        metadata={
            "name": "dateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    inverter_status_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "InverterStatusType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Link(BaseModel):
    """
    Links provide a reference, via URI, to another resource.

    :ivar link_r2_3:
    :ivar other_element:
    :ivar href: A URI reference.
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Link_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    href: str = field(
        metadata={
            "type": "Attribute",
            "required": True,
        }
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class LocalControlModeStatusType(BaseModel):
    """
    DER LocalControlModeStatus/value: 0 – local control 1 – remote control
    All other values reserved.

    :ivar date_time: The date and time at which the state applied.
    :ivar value: The value indicating the state.
    :ivar local_control_mode_status_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    date_time: TimeType = field(
        metadata={
            "name": "dateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    local_control_mode_status_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "LocalControlModeStatusType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ManufacturerStatusType(BaseModel):
    """
    DER ManufacturerStatus/value: String data type.

    :ivar date_time: The date and time at which the state applied.
    :ivar value: The value indicating the state.
    :ivar manufacturer_status_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    date_time: TimeType = field(
        metadata={
            "name": "dateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: str = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 6,
        }
    )
    manufacturer_status_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ManufacturerStatusType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Offset(BaseModel):
    """
    If a temperature offset is sent that causes the heating or cooling
    temperature set point to exceed the limit boundaries that are
    programmed into the device, the device SHALL respond by setting the
    temperature at the limit.

    If an EDC is being targeted at multiple devices or to a device that
    controls multiple devices (e.g., EMS), it can provide multiple Offset
    types within one EDC. For events with multiple Offset types, a client
    SHALL select the Offset that best fits their operating function.
    Alternatively, an event with a single Offset type can be targeted at an
    EMS in order to request a percentage load reduction on the average
    energy usage of the entire premise. An EMS SHOULD use the Metering
    function set to determine the initial load in the premise, reduce
    energy consumption by controlling devices at its disposal, and at the
    conclusion of the event, once again use the Metering function set to
    determine if the desired load reduction was achieved.

    :ivar cooling_offset: The value change requested for the cooling
        offset, in degree C / 10. The value should be added to the
        normal set point for cooling, or if loadShiftForward is true,
        then the value should be subtracted from the normal set point.
    :ivar heating_offset: The value change requested for the heating
        offset, in degree C / 10. The value should be subtracted for
        heating, or if loadShiftForward is true, then the value should
        be added to the normal set point.
    :ivar load_adjustment_percentage_offset: The value change requested
        for the load adjustment percentage. The value should be
        subtracted from the normal setting, or if loadShiftForward is
        true, then the value should be added to the normal setting.
    :ivar offset_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    cooling_offset: None | int = field(
        default=None,
        metadata={
            "name": "coolingOffset",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    heating_offset: None | int = field(
        default=None,
        metadata={
            "name": "heatingOffset",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    load_adjustment_percentage_offset: None | PerCent = field(
        default=None,
        metadata={
            "name": "loadAdjustmentPercentageOffset",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    offset_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Offset_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class OperationalModeStatusType(BaseModel):
    """
    DER OperationalModeStatus value: 0 - Not applicable / Unknown 1 - Off 2
    - Operational mode 3 - Test mode All other values reserved.

    :ivar date_time: The date and time at which the state applied.
    :ivar value: The value indicating the state.
    :ivar operational_mode_status_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    date_time: TimeType = field(
        metadata={
            "name": "dateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    operational_mode_status_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "OperationalModeStatusType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PerCentControlType(PerCent):
    """
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    model_config = ConfigDict(defer_build=True)
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class PowerConfiguration(BaseModel):
    """
    Contains configuration related to the device's power sources.

    :ivar battery_install_time: Time/Date at which battery was
        installed,
    :ivar low_charge_threshold: In context of the PowerStatus resource,
        this is the value of EstimatedTimeRemaining below which
        BatteryStatus "low" is indicated and the PS_LOW_BATTERY is
        raised.
    :ivar power_configuration_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    battery_install_time: None | TimeType = field(
        default=None,
        metadata={
            "name": "batteryInstallTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    low_charge_threshold: None | int = field(
        default=None,
        metadata={
            "name": "lowChargeThreshold",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    power_configuration_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PowerConfiguration_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PowerFactor(BaseModel):
    """
    Specifies a setpoint for Displacement Power Factor, the ratio between
    apparent and active powers at the fundamental frequency (e.g. 60 Hz).

    :ivar displacement: Significand of an unsigned value of cos(theta)
        between 0 and 1.0. E.g. a value of 0.95 may be specified as a
        displacement of 950 and a multiplier of -3.
    :ivar multiplier: Specifies exponent of 'displacement'.
    :ivar power_factor_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    displacement: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    power_factor_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PowerFactor_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PowerFactorWithExcitation(BaseModel):
    """
    Specifies a setpoint for Displacement Power Factor, the ratio between
    apparent and active powers at the fundamental frequency (e.g. 60 Hz)
    and includes an excitation flag.

    :ivar displacement: Significand of an unsigned value of cos(theta)
        between 0 and 1.0. E.g. a value of 0.95 may be specified as a
        displacement of 950 and a multiplier of -3.
    :ivar excitation: True when DER is absorbing reactive power (under-
        excited), false when DER is injecting reactive power (over-
        excited).
    :ivar multiplier: Specifies exponent of 'displacement'.
    :ivar power_factor_with_excitation_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    displacement: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    excitation: bool = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    power_factor_with_excitation_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PowerFactorWithExcitation_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PriorityData(BaseModel):
    """
    Contains an instance identifying data with which to prioritize an
    aggregation with a priority distribution.
    """

    model_config = ConfigDict(defer_build=True)
    l_fdi: bytes = field(
        metadata={
            "name": "lFDI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 20,
            "format": "base16",
        }
    )
    priority_data_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PriorityData_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ReactivePower(BaseModel):
    """
    The reactive power Q (in var) is the product of root mean square (RMS)
    voltage, RMS current, and sin(theta) where theta is the phase angle of
    current relative to voltage.

    :ivar multiplier: Specifies exponent of uom.
    :ivar value: Value in volt-amperes reactive (var) (uom 63)
    :ivar reactive_power_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    reactive_power_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReactivePower_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ReactiveSusceptance(BaseModel):
    """
    Reactive susceptance.

    :ivar multiplier: Specifies exponent of uom.
    :ivar value: Value in siemens (uom 53)
    :ivar reactive_susceptance_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    reactive_susceptance_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReactiveSusceptance_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class RealEnergy(BaseModel):
    """
    Real electrical energy.

    :ivar multiplier: Multiplier for 'unit'.
    :ivar value: Value of the energy in Watt-hours. (uom 72)
    :ivar real_energy_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_inclusive": 281474976710655,
        }
    )
    real_energy_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RealEnergy_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class RequestStatus(BaseModel):
    """
    The RequestStatus object is used to indicate the current status of a
    Flow Reservation Request.

    :ivar date_time: The dateTime attribute will provide a timestamp of
        when the request status was set. dateTime SHALL be set to the
        time at which the status change occurred, not a time in the
        future or past.
    :ivar request_status: Field representing the request status type. 0
        = Requested 1 = Cancelled All other values reserved.
    :ivar request_status_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    date_time: TimeType = field(
        metadata={
            "name": "dateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    request_status: int = field(
        metadata={
            "name": "requestStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    request_status_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RequestStatus_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Resource(BaseModel):
    """
    A resource is an addressable unit of information, either a collection
    (List) or instance of an object (identifiedObject, or simply,
    Resource).

    :ivar resource_r2_3:
    :ivar other_element:
    :ivar href: A reference to the resource address (URI). Required in a
        response to a GET, ignored otherwise.
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    resource_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Resource_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    href: None | str = field(
        default=None,
        metadata={
            "type": "Attribute",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ServiceChange(BaseModel):
    """
    Specifies a change to the service status.

    :ivar new_status: The new service status, to take effect at the time
        specified by startTime
    :ivar start_time: The date/time when the change is to take effect.
    :ivar service_change_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    new_status: ServiceStatusType = field(
        metadata={
            "name": "newStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    start_time: TimeType = field(
        metadata={
            "name": "startTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    service_change_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ServiceChange_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class SetPoint(BaseModel):
    """
    The SetPoint object is used to apply specific temperature set points to
    a temperature control device.

    The values of the heatingSetpoint and coolingSetpoint attributes SHALL
    be calculated as follows: Cooling/Heating Temperature Set Point / 100 =
    temperature in degrees Celsius where -273.15°C &amp;lt;= temperature
    &amp;lt;= 327.67°C, corresponding to a Cooling and/or Heating
    Temperature Set Point. The maximum resolution this format allows is
    0.01°C. The field not present in a Response indicates that this field
    has not been used by the end device. If a temperature is sent that
    exceeds the temperature limit boundaries that are programmed into the
    device, the device SHALL respond by setting the temperature at the
    limit.

    :ivar cooling_setpoint: This attribute represents the cooling
        temperature set point in degrees Celsius / 100. (Hundredths of a
        degree C)
    :ivar heating_setpoint: This attribute represents the heating
        temperature set point in degrees Celsius / 100. (Hundredths of a
        degree C)
    :ivar set_point_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    cooling_setpoint: None | int = field(
        default=None,
        metadata={
            "name": "coolingSetpoint",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    heating_setpoint: None | int = field(
        default=None,
        metadata={
            "name": "heatingSetpoint",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_point_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SetPoint_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class SignedPerCentControlType(SignedPerCent):
    """
    :ivar disabled: If set to true (disabled) this DERControl Mode is
        disabled and a value SHALL NOT be specified. A disabled
        DERControl Mode follows the rules and guidelines as if a value
        were present. If not specified, a default of false (enabled) is
        used.
    """

    model_config = ConfigDict(defer_build=True)
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class SignedRealEnergy(BaseModel):
    """
    Real electrical energy, signed.

    :ivar multiplier: Multiplier for 'unit'.
    :ivar value: Value of the energy in Watt-hours. (uom 72)
    :ivar signed_real_energy_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "min_inclusive": -140737488355328,
            "max_inclusive": 140737488355328,
        }
    )
    signed_real_energy_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SignedRealEnergy_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class StateOfChargeStatusType(BaseModel):
    """
    DER StateOfChargeStatus value: Percent data type.

    :ivar date_time: The date and time at which the state applied.
    :ivar value: The value indicating the state.
    :ivar state_of_charge_status_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    date_time: TimeType = field(
        metadata={
            "name": "dateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: PerCent = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    state_of_charge_status_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "StateOfChargeStatusType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class StorageModeStatusType(BaseModel):
    """
    DER StorageModeStatus value: 0 – storage charging 1 – storage
    discharging 2 – storage holding All other values reserved.

    :ivar date_time: The date and time at which the state applied.
    :ivar value: The value indicating the state.
    :ivar storage_mode_status_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    date_time: TimeType = field(
        metadata={
            "name": "dateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    storage_mode_status_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "StorageModeStatusType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class TargetReduction(BaseModel):
    """
    The TargetReduction object is used by a Demand Response service
    provider to provide a recommended threshold that a device/premises
    should maintain its consumption below.

    For example, a service provider can provide a recommended threshold of
    some kWh for a 3-hour event. This means that the device/premises SHOULD
    maintain its consumption below the specified limit for the specified
    period.

    :ivar type_value: Indicates the type of reduction requested.
    :ivar value: Indicates the requested amount of the relevant
        commodity to be reduced.
    :ivar target_reduction_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    type_value: UnitType = field(
        metadata={
            "name": "type",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    target_reduction_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TargetReduction_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Temperature(BaseModel):
    """
    Specification of a temperature.

    :ivar multiplier: Multiplier for 'unit'.
    :ivar subject: The subject of the temperature measurement 0 -
        Enclosure 1 - Transformer 2 - HeatSink
    :ivar value: Value in Degrees Celsius (uom 23).
    :ivar temperature_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    subject: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    temperature_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Temperature_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class TimeConfiguration(BaseModel):
    """
    Contains attributes related to the configuration of the time service.

    :ivar dst_end_rule: Rule to calculate end of daylight savings time
        in the current year.  Result of dstEndRule must be greater than
        result of dstStartRule.
    :ivar dst_offset: Daylight savings time offset from local standard
        time.
    :ivar dst_start_rule: Rule to calculate start of daylight savings
        time in the current year. Result of dstEndRule must be greater
        than result of dstStartRule.
    :ivar tz_offset: Local time zone offset from UTCTime. Does not
        include any daylight savings time offsets.
    :ivar time_configuration_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    dst_end_rule: DstRuleType = field(
        metadata={
            "name": "dstEndRule",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    dst_offset: TimeOffsetType = field(
        metadata={
            "name": "dstOffset",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    dst_start_rule: DstRuleType = field(
        metadata={
            "name": "dstStartRule",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    tz_offset: TimeOffsetType = field(
        metadata={
            "name": "tzOffset",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    time_configuration_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TimeConfiguration_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class UnitValueType(BaseModel):
    """
    Type for specification of a specific value, with units and power of ten
    multiplier.

    :ivar multiplier: Multiplier for 'unit'.
    :ivar unit: Unit in symbol
    :ivar value: Value in units specified
    :ivar unit_value_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    unit: UomType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    unit_value_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UnitValueType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class UnsignedActivePower(BaseModel):
    """
    The active (real) power P (in W) is the product of root-mean-square
    (RMS) voltage, RMS current, and cos(theta) where theta is the phase
    angle of current relative to voltage.

    It is the primary measure of the rate of flow of energy.

    :ivar multiplier: Specifies exponent for uom.
    :ivar value: Value in watts (uom 38)
    :ivar unsigned_active_power_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    unsigned_active_power_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UnsignedActivePower_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class UnsignedFixedPointType(BaseModel):
    """
    Abstract type for specifying an unsigned fixed-point value without a
    given unit of measure.

    :ivar multiplier: Specifies exponent of uom.
    :ivar value: Dimensionless value
    :ivar unsigned_fixed_point_type_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    unsigned_fixed_point_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UnsignedFixedPointType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class UnsignedFixedVar(BaseModel):
    """
    Specifies an unsigned setpoint for reactive power.

    :ivar ref_type: Indicates how to interpret 'value.'
    :ivar value: Specify an unsigned setpoint for reactive power in %
        (see 'refType' for context).
    :ivar unsigned_fixed_var_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    ref_type: DerunitRefType = field(
        metadata={
            "name": "refType",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: PerCent = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    unsigned_fixed_var_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UnsignedFixedVar_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class UnsignedReactivePower(BaseModel):
    """
    The reactive power Q (in var) is the product of root mean square (RMS)
    voltage, RMS current, and sin(theta) where theta is the phase angle of
    current relative to voltage.

    :ivar multiplier: Specifies exponent of uom.
    :ivar value: Value in volt-amperes reactive (var) (uom 63)
    :ivar unsigned_reactive_power_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    unsigned_reactive_power_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UnsignedReactivePower_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class VoltageRms(BaseModel):
    """
    Average electric potential difference between two points.

    :ivar multiplier: Specifies exponent of uom.
    :ivar value: Value in volts RMS (uom 29)
    :ivar voltage_rms_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    class Meta:
        name = "VoltageRMS"

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    voltage_rms_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "VoltageRMS_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class WattHour(BaseModel):
    """
    Active (real) energy.

    :ivar multiplier: Specifies exponent of uom.
    :ivar value: Value in watt-hours (uom 72)
    :ivar watt_hour_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    watt_hour_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "WattHour_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class LoWpan(BaseModel):
    """
    Contains information specific to 6LoWPAN.

    :ivar octets_rx: Number of Bytes received
    :ivar octets_tx: Number of Bytes transmitted
    :ivar packets_rx: Number of packets received
    :ivar packets_tx: Number of packets transmitted
    :ivar rx_frag_error: Number of errors receiving fragments
    :ivar lo_wpan_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    class Meta:
        name = "loWPAN"

    model_config = ConfigDict(defer_build=True)
    octets_rx: None | int = field(
        default=None,
        metadata={
            "name": "octetsRx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    octets_tx: None | int = field(
        default=None,
        metadata={
            "name": "octetsTx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    packets_rx: int = field(
        metadata={
            "name": "packetsRx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    packets_tx: int = field(
        metadata={
            "name": "packetsTx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    rx_frag_error: int = field(
        metadata={
            "name": "rxFragError",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    lo_wpan_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "loWPAN_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class AccountBalanceLink(Link):
    """
    SHALL contain a Link to an instance of AccountBalance.
    """

    model_config = ConfigDict(defer_build=True)
    account_balance_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AccountBalanceLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class AccountingUnit(BaseModel):
    """
    Unit for accounting; use either 'energyUnit' or 'currencyUnit' to
    specify the unit for 'value'.

    :ivar energy_unit: Unit of service.
    :ivar monetary_unit: Unit of currency.
    :ivar multiplier: Multiplier for the 'energyUnit' or 'monetaryUnit'.
    :ivar value: Value of the monetary aspect
    :ivar accounting_unit_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    energy_unit: None | RealEnergy = field(
        default=None,
        metadata={
            "name": "energyUnit",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    monetary_unit: CurrencyCode = field(
        metadata={
            "name": "monetaryUnit",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    value: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    accounting_unit_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AccountingUnit_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class ActivePowerControlType(ActivePower):
    """
    :ivar active_power_control_type_r2_3:
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    model_config = ConfigDict(defer_build=True)
    active_power_control_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActivePowerControlType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class ActivePowerDeltaControlType(ActivePower):
    """
    :ivar active_power_delta_control_type_r2_3:
    :ivar bidirectional: Specifies the behavior of a delta DERControl
        Mode regarding switching from absorbing/receiving to
        injecting/delivering or vice versa.
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    model_config = ConfigDict(defer_build=True)
    active_power_delta_control_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActivePowerDeltaControlType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    bidirectional: int = field(
        default=0,
        metadata={
            "type": "Attribute",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class AggregatedDevice1(Resource):
    """
    :ivar changed_time: The time at which this resource was last
        modified or created.
    :ivar device_category: This field is for use in devices that can
        adjust energy usage (e.g., demand response, distributed energy
        resources).  For devices that do not respond to
        EndDeviceControls or DERControls (for instance, an ESI), this
        field should not have any bits set.
    :ivar enabled: This attribute indicates whether or not a device is
        enabled, or registered, on the server. If a server sets this
        attribute to false, the device is no longer registered. It
        should be noted that servers can delete device instances, but
        using this attribute for some time is more convenient for
        clients.
    :ivar l_fdi: Long form of device identifier. See the Security
        section for additional details.
    :ivar s_fdi:
    :ivar aggregated_device_r2_3:
    """

    class Meta:
        name = "AggregatedDevice"

    model_config = ConfigDict(defer_build=True)
    changed_time: TimeType = field(
        metadata={
            "name": "changedTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    device_category: None | DeviceCategoryType = field(
        default=None,
        metadata={
            "name": "deviceCategory",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    enabled: None | bool = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    l_fdi: bytes = field(
        metadata={
            "name": "lFDI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 20,
            "format": "base16",
        }
    )
    s_fdi: Sfditype = field(
        metadata={
            "name": "sFDI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    aggregated_device_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AggregatedDevice_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class AggregationPriorityLink(Link):
    """
    SHALL contain a Link to an instance of AggregationPriority.

    If present, this resource contains the order in which an aggregation
    with a priority distribution is to be prioritized.
    """

    model_config = ConfigDict(defer_build=True)
    aggregation_priority_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AggregationPriorityLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class AssociatedUsagePointLink(Link):
    """
    SHALL contain a Link to an instance of UsagePoint.

    If present, this is the submeter that monitors the DER output. This is
    also the point of reference, or reference point of applicability, for
    voltage, limits, controls, etc.
    """

    model_config = ConfigDict(defer_build=True)
    associated_usage_point_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AssociatedUsagePointLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class BillingPeriod1(Resource):
    """
    A Billing Period relates to the period of time on which a customer is
    billed.

    As an example the billing period interval for a particular customer
    might be 31 days starting on July 1, 2011. The start date and interval
    can change on each billing period. There may also be multiple billing
    periods related to a customer agreement to support different tariff
    structures.

    :ivar bill_last_period: The amount of the bill for the previous
        billing period.
    :ivar bill_to_date: The bill amount related to the billing period as
        of the statusTimeStamp.
    :ivar interval: The time interval for this billing period.
    :ivar status_time_stamp: The date / time of the last update of this
        resource.
    :ivar billing_period_r2_3:
    """

    class Meta:
        name = "BillingPeriod"

    model_config = ConfigDict(defer_build=True)
    bill_last_period: None | int = field(
        default=None,
        metadata={
            "name": "billLastPeriod",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "min_inclusive": -140737488355328,
            "max_inclusive": 140737488355328,
        },
    )
    bill_to_date: None | int = field(
        default=None,
        metadata={
            "name": "billToDate",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "min_inclusive": -140737488355328,
            "max_inclusive": 140737488355328,
        },
    )
    interval: DateTimeInterval = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    status_time_stamp: None | TimeType = field(
        default=None,
        metadata={
            "name": "statusTimeStamp",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    billing_period_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "BillingPeriod_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ConfigurationLink(Link):
    """
    SHALL contain a Link to an instance of Configuration.
    """

    model_config = ConfigDict(defer_build=True)
    configuration_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ConfigurationLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ConsumptionTariffInterval1(Resource):
    """
    One of a sequence of thresholds defined in terms of consumption
    quantity of a service such as electricity, water, gas, etc.

    It defines the steps or blocks in a step tariff structure, where
    startValue simultaneously defines the entry value of this step and the
    closing value of the previous step. Where consumption is greater than
    startValue, it falls within this block and where consumption is less
    than or equal to startValue, it falls within one of the previous
    blocks.

    :ivar consumption_block: Indicates the consumption block of the
        ConsumptionTariffInterval.
    :ivar environmental_cost:
    :ivar price: The charge for this rate component, per unit of measure
        defined by the associated ReadingType, in currency specified in
        TariffProfile. The Pricing service provider determines the
        appropriate price attribute value based on its applicable
        regulatory rules. For example, price could be net or inclusive
        of applicable taxes, fees, or levies. The Billing function set
        provides the ability to represent billing information in a more
        detailed manner.
    :ivar start_value: The lowest level of consumption that defines the
        starting point of this consumption step or block. Thresholds
        start at zero for each billing period. If specified, the first
        ConsumptionTariffInterval.startValue for a TimeTariffInteral
        instance SHALL begin at "0." Subsequent
        ConsumptionTariffInterval.startValue elements SHALL be greater
        than the previous one.
    :ivar consumption_tariff_interval_r2_3:
    """

    class Meta:
        name = "ConsumptionTariffInterval"

    model_config = ConfigDict(defer_build=True)
    consumption_block: ConsumptionBlockType = field(
        metadata={
            "name": "consumptionBlock",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    environmental_cost: list[EnvironmentalCost] = field(
        default_factory=list,
        metadata={
            "name": "EnvironmentalCost",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    price: None | int = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    start_value: int = field(
        metadata={
            "name": "startValue",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_inclusive": 281474976710655,
        }
    )
    consumption_tariff_interval_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ConsumptionTariffInterval_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class CurrentDercontrolsLink(Link):
    """
    SHALL contain a Link to the CurrentDERControls for this DER.
    """

    class Meta:
        name = "CurrentDERControlsLink"

    model_config = ConfigDict(defer_build=True)
    current_dercontrols_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CurrentDERControlsLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class CurrentDerprogramLink(Link):
    """
    DEPRECATED SHALL NOT be included by servers, but clients should note
    that it may be included by servers compliant with previous revisions of
    IEEE 2030.5.
    """

    class Meta:
        name = "CurrentDERProgramLink"

    model_config = ConfigDict(defer_build=True)
    current_derprogram_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CurrentDERProgramLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class CustomerAccountLink(Link):
    """
    SHALL contain a Link to an instance of CustomerAccount.
    """

    model_config = ConfigDict(defer_build=True)
    customer_account_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CustomerAccountLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DeravailabilityLink(Link):
    """
    SHALL contain a Link to an instance of DERAvailability.
    """

    class Meta:
        name = "DERAvailabilityLink"

    model_config = ConfigDict(defer_build=True)
    deravailability_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERAvailabilityLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DercapabilityLink(Link):
    """
    SHALL contain a Link to an instance of DERCapability.
    """

    class Meta:
        name = "DERCapabilityLink"

    model_config = ConfigDict(defer_build=True)
    dercapability_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERCapabilityLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Dercapability1(Resource):
    """
    Distributed energy resource type and nameplate ratings.

    :ivar modes_supported: Bitmap indicating the DERControl Modes
        implemented by the device. See DERControlType for values.
    :ivar modes_supported2: Bitmap indicating the additional DERControl
        Modes implemented by the device. See DERControlType2 for values.
    :ivar rtg_abnormal_category: Abnormal operating performance category
        as defined by IEEE 1547-2018. One of: 0 - not specified 1 -
        Category I 2 - Category II 3 - Category III All other values
        reserved.
    :ivar rtg_max_a: Maximum continuous AC current capability of the
        DER, in Amperes (RMS).
    :ivar rtg_max_ah: Usable energy storage capacity of the DER, in
        AmpHours.
    :ivar rtg_max_charge_rate_va: Maximum apparent power charge rating
        in Volt-Amperes. May differ from the maximum apparent power
        rating.
    :ivar rtg_max_charge_rate_w: Maximum rate of energy transfer
        received by the storage DER, in Watts.
    :ivar rtg_max_discharge_rate_va: Maximum rate of apparent power
        discharge by the storage DER, in Volt-Amperes. May differ from
        the maximum apparent power rating (rtgMaxVA) as this is specific
        to storage.
    :ivar rtg_max_discharge_rate_w: Maximum rate of energy transfer
        delivered by the storage DER, in Watts. Required for combined
        generation/storage DERs (e.g. DERType == 83). May differ from
        the maximum active power rating (rtgMaxW) as this is specific to
        storage.
    :ivar rtg_max_v: AC voltage maximum rating.
    :ivar rtg_max_va: Maximum continuous apparent power output
        capability of the DER, in VA.
    :ivar rtg_max_var: Maximum continuous reactive power delivered by
        the DER, in var.
    :ivar rtg_max_var_neg: Maximum continuous reactive power received by
        the DER, in var.  If absent, defaults to negative rtgMaxVar.
    :ivar rtg_max_w: Maximum continuous active power output capability
        of the DER, in watts. Represents combined generation plus
        storage output if DERType == 83.
    :ivar rtg_max_wh: Maximum energy storage capacity of the DER, in
        WattHours.
    :ivar rtg_min_pfover_excited: Minimum Power Factor displacement
        capability of the DER when injecting reactive power (over-
        excited); SHALL be a positive value between 0.0 (typically
        &amp;gt; 0.7) and 1.0, inclusive. If absent, defaults to unity.
    :ivar rtg_min_pfunder_excited: Minimum Power Factor displacement
        capability of the DER when absorbing reactive power (under-
        excited); SHALL be a positive value between 0.0 (typically
        &amp;gt; 0.7) and 1.0, inclusive. If absent, defaults to
        rtgMinPFOverExcited.
    :ivar rtg_min_v: AC voltage minimum rating.
    :ivar rtg_normal_category: Normal operating performance category as
        defined by IEEE 1547-2018. One of: 0 - not specified 1 -
        Category A 2 - Category B All other values reserved.
    :ivar rtg_over_excited_pf: Specified over-excited power factor.
    :ivar rtg_over_excited_w: Active power rating in Watts at specified
        over-excited power factor (rtgOverExcitedPF). If present,
        rtgOverExcitedPF SHALL be present.
    :ivar rtg_reactive_susceptance: Reactive susceptance that remains
        connected to the Area EPS in the cease to energize and trip
        state.
    :ivar rtg_under_excited_pf: Specified under-excited power factor.
    :ivar rtg_under_excited_w: Active power rating in Watts at specified
        under-excited power factor (rtgUnderExcitedPF). If present,
        rtgUnderExcitedPF SHALL be present.
    :ivar rtg_vnom: AC voltage nominal rating.
    :ivar type_value: Type of DER; see DERType object
    :ivar dercapability_r2_3:
    """

    class Meta:
        name = "DERCapability"

    model_config = ConfigDict(defer_build=True)
    modes_supported: DercontrolType = field(
        metadata={
            "name": "modesSupported",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    modes_supported2: None | DercontrolType2 = field(
        default=None,
        metadata={
            "name": "modesSupported2",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_abnormal_category: None | int = field(
        default=None,
        metadata={
            "name": "rtgAbnormalCategory",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_max_a: None | CurrentRms = field(
        default=None,
        metadata={
            "name": "rtgMaxA",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_max_ah: None | AmpereHour = field(
        default=None,
        metadata={
            "name": "rtgMaxAh",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_max_charge_rate_va: None | ApparentPower = field(
        default=None,
        metadata={
            "name": "rtgMaxChargeRateVA",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_max_charge_rate_w: None | ActivePower = field(
        default=None,
        metadata={
            "name": "rtgMaxChargeRateW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_max_discharge_rate_va: None | ApparentPower = field(
        default=None,
        metadata={
            "name": "rtgMaxDischargeRateVA",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_max_discharge_rate_w: None | ActivePower = field(
        default=None,
        metadata={
            "name": "rtgMaxDischargeRateW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_max_v: None | VoltageRms = field(
        default=None,
        metadata={
            "name": "rtgMaxV",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_max_va: None | ApparentPower = field(
        default=None,
        metadata={
            "name": "rtgMaxVA",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_max_var: None | ReactivePower = field(
        default=None,
        metadata={
            "name": "rtgMaxVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_max_var_neg: None | ReactivePower = field(
        default=None,
        metadata={
            "name": "rtgMaxVarNeg",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_max_w: ActivePower = field(
        metadata={
            "name": "rtgMaxW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    rtg_max_wh: None | WattHour = field(
        default=None,
        metadata={
            "name": "rtgMaxWh",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_min_pfover_excited: None | PowerFactor = field(
        default=None,
        metadata={
            "name": "rtgMinPFOverExcited",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_min_pfunder_excited: None | PowerFactor = field(
        default=None,
        metadata={
            "name": "rtgMinPFUnderExcited",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_min_v: None | VoltageRms = field(
        default=None,
        metadata={
            "name": "rtgMinV",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_normal_category: None | int = field(
        default=None,
        metadata={
            "name": "rtgNormalCategory",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_over_excited_pf: None | PowerFactor = field(
        default=None,
        metadata={
            "name": "rtgOverExcitedPF",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_over_excited_w: None | ActivePower = field(
        default=None,
        metadata={
            "name": "rtgOverExcitedW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_reactive_susceptance: None | ReactiveSusceptance = field(
        default=None,
        metadata={
            "name": "rtgReactiveSusceptance",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_under_excited_pf: None | PowerFactor = field(
        default=None,
        metadata={
            "name": "rtgUnderExcitedPF",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_under_excited_w: None | ActivePower = field(
        default=None,
        metadata={
            "name": "rtgUnderExcitedW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rtg_vnom: None | VoltageRms = field(
        default=None,
        metadata={
            "name": "rtgVNom",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    type_value: Dertype = field(
        metadata={
            "name": "type",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    dercapability_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERCapability_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DercurveLink(Link):
    """
    SHALL contain a Link to an instance of DERCurve.

    :ivar dercurve_link_r2_3:
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    class Meta:
        name = "DERCurveLink"

    model_config = ConfigDict(defer_build=True)
    dercurve_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERCurveLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class Derlink(Link):
    """
    SHALL contain a Link to an instance of DER.
    """

    class Meta:
        name = "DERLink"

    model_config = ConfigDict(defer_build=True)
    derlink_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DerprogramLink(Link):
    """
    SHALL contain a Link to an instance of DERProgram.
    """

    class Meta:
        name = "DERProgramLink"

    model_config = ConfigDict(defer_build=True)
    derprogram_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERProgramLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DersettingsLink(Link):
    """
    SHALL contain a Link to an instance of DERSettings.
    """

    class Meta:
        name = "DERSettingsLink"

    model_config = ConfigDict(defer_build=True)
    dersettings_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERSettingsLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DerstatusLink(Link):
    """
    SHALL contain a Link to an instance of DERStatus.
    """

    class Meta:
        name = "DERStatusLink"

    model_config = ConfigDict(defer_build=True)
    derstatus_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERStatusLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Drlccapabilities(BaseModel):
    """
    Contains information about the static capabilities of the device, to
    allow service providers to know what types of functions are supported,
    what the normal operating ranges and limits are, and other similar
    information, in order to provide better suggestions of applicable
    programs to receive the maximum benefit.

    :ivar average_energy: The average hourly energy usage when in normal
        operating mode.
    :ivar max_demand: The maximum demand rating of this end device.
    :ivar options_implemented: Bitmap indicating the DRLC options
        implemented by the device. 0 - Target reduction (kWh) 1 - Target
        reduction (kW) 2 - Target reduction (Watts) 3 - Target reduction
        (Cubic Meters) 4 - Target reduction (Cubic Feet) 5 - Target
        reduction (US Gallons) 6 - Target reduction (Imperial Gallons) 7
        - Target reduction (BTUs) 8 - Target reduction (Liters) 9 -
        Target reduction (kPA (gauge)) 10 - Target reduction (kPA
        (absolute)) 11 - Target reduction (Mega Joule) 12 - Target
        reduction (Unitless) 13-15 - Reserved 16 - Temperature set point
        17 - Temperature offset 18 - Duty cycle 19 - Load adjustment
        percentage 20 - Appliance load reduction 21-31 - Reserved
    :ivar drlccapabilities_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    class Meta:
        name = "DRLCCapabilities"

    model_config = ConfigDict(defer_build=True)
    average_energy: RealEnergy = field(
        metadata={
            "name": "averageEnergy",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    max_demand: ActivePower = field(
        metadata={
            "name": "maxDemand",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    options_implemented: bytes = field(
        metadata={
            "name": "optionsImplemented",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 4,
            "format": "base16",
        }
    )
    drlccapabilities_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DRLCCapabilities_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DefaultDercontrolLink(Link):
    """
    SHALL contain a Link to an instance of DefaultDERControl containing the
    default DERControl Mode(s) of the DER which MAY be overridden by
    DERControl events.
    """

    class Meta:
        name = "DefaultDERControlLink"

    model_config = ConfigDict(defer_build=True)
    default_dercontrol_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DefaultDERControlLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DemandResponseProgramLink(Link):
    """
    SHALL contain a Link to an instance of DemandResponseProgram.
    """

    model_config = ConfigDict(defer_build=True)
    demand_response_program_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DemandResponseProgramLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DeviceCapabilityLink(Link):
    """
    SHALL contain a Link to an instance of DeviceCapability.
    """

    model_config = ConfigDict(defer_build=True)
    device_capability_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DeviceCapabilityLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DeviceInformationLink(Link):
    """
    SHALL contain a Link to an instance of DeviceInformation.
    """

    model_config = ConfigDict(defer_build=True)
    device_information_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DeviceInformationLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DeviceStatusLink(Link):
    """
    SHALL contain a Link to an instance of DeviceStatus.
    """

    model_config = ConfigDict(defer_build=True)
    device_status_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DeviceStatusLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class EndDeviceLink(Link):
    """
    SHALL contain a Link to an instance of EndDevice.
    """

    model_config = ConfigDict(defer_build=True)
    end_device_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "EndDeviceLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Error(Error1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class FileLink(Link):
    """
    This element SHALL be set to the URI of the most recent File being
    loaded/activated by the LD.

    In the case of file status 0, this element SHALL be omitted.
    """

    model_config = ConfigDict(defer_build=True)
    file_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FileLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class FileStatusLink(Link):
    """
    SHALL contain a Link to an instance of FileStatus.
    """

    model_config = ConfigDict(defer_build=True)
    file_status_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FileStatusLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class File1(Resource):
    """
    This resource contains various meta-data describing a file's
    characteristics.

    The meta-data provides general file information and also is used to
    support filtered queries of file lists.

    :ivar activate_time: This element SHALL be set to the date/time at
        which this file is activated. If the activation time is less
        than or equal to current time, the LD SHALL immediately place
        the file into the activated state (in the case of a firmware
        file, the file is now the running image).  If the activation
        time is greater than the current time, the LD SHALL wait until
        the specified activation time is reached, then SHALL place the
        file into the activated state. Omission of this element means
        that the LD SHALL NOT take any action to activate the file until
        a subsequent GET to this File resource provides an activateTime.
    :ivar file_uri: This element SHALL be set to the URI location of the
        file binary artifact.  This is the BLOB (binary large object)
        that is actually loaded by the LD
    :ivar l_fdi: This element SHALL be set to the LFDI of the device for
        which this file in targeted.
    :ivar mf_hw_ver: This element SHALL be set to the hardware version
        for which this file is targeted.
    :ivar mf_id: This element SHALL be set to the manufacturer's Private
        Enterprise Number (assigned by IANA).
    :ivar mf_model: This element SHALL be set to the manufacturer model
        number for which this file is targeted. The syntax and semantics
        are left to the manufacturer.
    :ivar mf_ser_num: This element SHALL be set to the manufacturer
        serial number for which this file is targeted. The syntax and
        semantics are left to the manufacturer.
    :ivar mf_ver: This element SHALL be set to the software version
        information for this file. The syntax and semantics are left to
        the manufacturer.
    :ivar size: This element SHALL be set to the total size (in bytes)
        of the file referenced by fileURI.
    :ivar type_value: A value indicating the type of the file.  SHALL be
        one of the following values: 00 = Software Image 01 = Security
        Credential 02 = Configuration 03 = Log 04–7FFF = reserved
        8000-FFFF = Manufacturer defined
    :ivar file_r2_3:
    """

    class Meta:
        name = "File"

    model_config = ConfigDict(defer_build=True)
    activate_time: None | TimeType = field(
        default=None,
        metadata={
            "name": "activateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    file_uri: str = field(
        metadata={
            "name": "fileURI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    l_fdi: None | bytes = field(
        default=None,
        metadata={
            "name": "lFDI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 20,
            "format": "base16",
        },
    )
    mf_hw_ver: None | str = field(
        default=None,
        metadata={
            "name": "mfHwVer",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    mf_id: Pentype = field(
        metadata={
            "name": "mfID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    mf_model: str = field(
        metadata={
            "name": "mfModel",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 32,
        }
    )
    mf_ser_num: None | str = field(
        default=None,
        metadata={
            "name": "mfSerNum",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    mf_ver: str = field(
        metadata={
            "name": "mfVer",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 16,
        }
    )
    size: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    type_value: bytes = field(
        metadata={
            "name": "type",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 2,
            "format": "base16",
        }
    )
    file_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "File_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class FixedVarControlType(FixedVar):
    """
    :ivar fixed_var_control_type_r2_3:
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    model_config = ConfigDict(defer_build=True)
    fixed_var_control_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FixedVarControlType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class FreqDroopType(BaseModel):
    """
    Type for Frequency-Droop (Frequency-Watt) operation.

    :ivar d_bof: Frequency droop dead band for over-frequency
        conditions. In thousandths of Hz.
    :ivar d_buf: Frequency droop dead band for under-frequency
        conditions. In thousandths of Hz.
    :ivar k_of: Frequency droop per-unit frequency change for over-
        frequency conditions corresponding to 1 per-unit power output
        change. In thousandths, unitless.
    :ivar k_uf: Frequency droop per-unit frequency change for under-
        frequency conditions corresponding to 1 per-unit power output
        change. In thousandths, unitless.
    :ivar open_loop_tms: Open loop response time, the duration from a
        step change in control signal input until the output changes by
        90% of its final change before any overshoot, in hundredths of a
        second. Resolution is 1/100 sec. A value of 0 is used to mean no
        limit.
    :ivar p_min: If present, specifies the minimum active power output.
        Used, for example, for testing purposes to direct a device to be
        able to absorb active power.
    :ivar freq_droop_type_r2_3:
    :ivar other_element:
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    :ivar any_attributes:
    """

    model_config = ConfigDict(defer_build=True)
    d_bof: int = field(
        metadata={
            "name": "dBOF",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    d_buf: int = field(
        metadata={
            "name": "dBUF",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    k_of: int = field(
        metadata={
            "name": "kOF",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    k_uf: int = field(
        metadata={
            "name": "kUF",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    open_loop_tms: int = field(
        metadata={
            "name": "openLoopTms",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    p_min: None | ActivePower = field(
        default=None,
        metadata={
            "name": "pMin",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    freq_droop_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FreqDroopType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class IdentifiedObject(Resource):
    """
    This is a root class to provide common naming attributes for all
    classes needing naming attributes.

    :ivar m_rid: The global identifier of the object.
    :ivar description: The description is a human readable text
        describing or naming the object.
    :ivar version: Contains the version number of the object. See the
        type definition for details.
    :ivar identified_object_r2_3:
    """

    model_config = ConfigDict(defer_build=True)
    m_rid: MRidtype = field(
        metadata={
            "name": "mRID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    description: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    version: None | VersionType = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    identified_object_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "IdentifiedObject_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class List(Resource):
    """
    Container to hold a collection of object instances or references.

    See Design Pattern section for additional details.

    :ivar list_r2_3:
    :ivar all: The number specifying "all" of the items in the list
        before any query string parameters are applied. Required on a
        response to a GET, ignored otherwise.
    :ivar results: Indicates the number of items in this page of
        results.
    """

    model_config = ConfigDict(defer_build=True)
    list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "List_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    all: int = field(
        metadata={
            "type": "Attribute",
            "required": True,
        }
    )
    results: int = field(
        metadata={
            "type": "Attribute",
            "required": True,
        }
    )


class ListLink(Link):
    """
    ListLinks provide a reference, via URI, to a List.

    :ivar list_link_r2_3:
    :ivar all: Indicates the total number of items in the referenced
        list before any query string parameters are applied. This
        attribute SHALL be present if the href is a local or relative
        URI. This attribute SHOULD NOT be present if the href is a
        remote or absolute URI, as the server may be unaware of changes
        to the value.
    """

    model_config = ConfigDict(defer_build=True)
    list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    all: None | int = field(
        default=None,
        metadata={
            "type": "Attribute",
        },
    )


class LogEvent1(Resource):
    """
    A time stamped instance of a significant event detected by the device.

    :ivar created_date_time: The date and time that the event occurred.
    :ivar details: Human readable text that MAY be used to transmit
        additional details about the event. A host MAY remove this field
        when received.
    :ivar extended_data: May be used to transmit additional details
        about the event.
    :ivar function_set: If the profileID indicates this is IEEE 2030.5,
        the functionSet is defined by IEEE 2030.5 and SHALL be one of
        the values from the table below (IEEE 2030.5 function set
        identifiers). If the profileID is anything else, the functionSet
        is defined by the identified profile. 0       General (not
        specific to a function set) 1       Subscription/Notification 2
        End Device 3       Function Set Assignments 4       Response 5
        Demand Response and Load Control 6       Metering 7
        Pricing 8       Messaging 9       Billing 10      Prepayment 11
        Distributed Energy Resources 12      Time 13      Software
        Download 14      Device Information 15      Power Status 16
        Network Status 17      Log Event 18      Configuration 19
        Security 20      Self Device 21      Flow Reservation 22
        Metering Mirror 23      Aggregation 24      Proxied Device All
        other values are reserved.
    :ivar log_event_code: An 8 bit unsigned integer. logEventCodes are
        scoped to a profile and a function set. If the profile is IEEE
        2030.5, the logEventCode is defined by IEEE 2030.5 within one of
        the function sets of IEEE 2030.5. If the profile is anything
        else, the logEventCode is defined by the specified profile.
    :ivar log_event_id: This 16-bit value, combined with
        createdDateTime, profileID, and logEventPEN, should provide a
        reasonable level of uniqueness.
    :ivar log_event_pen: The Private Enterprise Number(PEN) of the
        entity that defined the profileID, functionSet, and logEventCode
        of the logEvent. IEEE 2030.5-assigned logEventCodes SHALL use
        the IEEE 2030.5 PEN.  Combinations of profileID, functionSet,
        and logEventCode SHALL have unique meaning within a logEventPEN
        and are defined by the owner of the PEN.
    :ivar profile_id: The profileID identifies which profile (HA, BA,
        SE, etc) defines the following event information. 0       Not
        profile specific. 1       Vendor Defined 2       IEEE 2030.5 3
        Home Automation 4       Building Automation All other values are
        reserved.
    :ivar log_event_r2_3:
    """

    class Meta:
        name = "LogEvent"

    model_config = ConfigDict(defer_build=True)
    created_date_time: TimeType = field(
        metadata={
            "name": "createdDateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    details: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    extended_data: None | int = field(
        default=None,
        metadata={
            "name": "extendedData",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    function_set: int = field(
        metadata={
            "name": "functionSet",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    log_event_code: int = field(
        metadata={
            "name": "logEventCode",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    log_event_id: int = field(
        metadata={
            "name": "logEventID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    log_event_pen: Pentype = field(
        metadata={
            "name": "logEventPEN",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    profile_id: int = field(
        metadata={
            "name": "profileID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    log_event_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "LogEvent_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class MeterReadingLink(Link):
    """
    SHALL contain a Link to an instance of MeterReading.
    """

    model_config = ConfigDict(defer_build=True)
    meter_reading_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MeterReadingLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Neighbor1(Resource):
    """
    Contains 802.15.4 link layer specific attributes.

    :ivar is_child: True if the neighbor is a child.
    :ivar link_quality: The quality of the link, as defined by 802.15.4
    :ivar short_address: As defined by IEEE 802.15.4
    :ivar neighbor_r2_3:
    """

    class Meta:
        name = "Neighbor"

    model_config = ConfigDict(defer_build=True)
    is_child: bool = field(
        metadata={
            "name": "isChild",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    link_quality: int = field(
        metadata={
            "name": "linkQuality",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    short_address: int = field(
        metadata={
            "name": "shortAddress",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    neighbor_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Neighbor_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Pevinfo(BaseModel):
    """
    Contains attributes that can be exposed by PEVs and other devices that
    have charging requirements.

    :ivar charging_power_now: This is the actual power flow in or out of
        the charger or inverter. This is calculated by the vehicle based
        on actual measurements. This number is positive for charging.
    :ivar energy_request_now: This is the amount of energy that must be
        transferred from the grid to EVSE and PEV to achieve the target
        state of charge allowing for charger efficiency and any vehicle
        and EVSE parasitic loads. This is calculated by the vehicle and
        changes throughout the connection as forward or reverse power
        flow change the battery state of charge.  This number is
        positive for charging.
    :ivar max_forward_power: This is maximum power transfer capability
        that could be used for charging the PEV to perform the requested
        energy transfer.  It is the lower of the vehicle or EVSE
        physical power limitations. It is not based on economic
        considerations. The vehicle may draw less power than this value
        based on its charging cycle. The vehicle defines this parameter.
        This number is positive for charging power flow.
    :ivar minimum_charging_duration: This is computed by the PEV based
        on the charging profile to complete the energy transfer if the
        maximum power is authorized.  The value will never be smaller
        than the ratio of the energy request to the power request
        because the charging profile may not allow the maximum power to
        be used throughout the transfer.   This is a critical parameter
        for determining whether any slack time exists in the charging
        cycle between the current time and the TCIN.
    :ivar target_state_of_charge: This is the target state of charge
        that is to be achieved during charging before the time of
        departure (TCIN).  The default value is 100%. The value cannot
        be set to a value less than the actual state of charge.
    :ivar time_charge_is_needed: Time Charge is Needed (TCIN) is the
        time that the PEV is expected to depart. The value is manually
        entered using controls and displays in the vehicle or on the
        EVSE or using a mobile device.  It is authenticated and saved by
        the PEV.  This value may be updated during a charging session.
    :ivar time_charging_status_pev: This is the time that the parameters
        are updated, except for changes to TCIN.
    :ivar pevinfo_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    class Meta:
        name = "PEVInfo"

    model_config = ConfigDict(defer_build=True)
    charging_power_now: ActivePower = field(
        metadata={
            "name": "chargingPowerNow",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    energy_request_now: RealEnergy = field(
        metadata={
            "name": "energyRequestNow",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    max_forward_power: ActivePower = field(
        metadata={
            "name": "maxForwardPower",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    minimum_charging_duration: int = field(
        metadata={
            "name": "minimumChargingDuration",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    target_state_of_charge: PerCent = field(
        metadata={
            "name": "targetStateOfCharge",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    time_charge_is_needed: TimeType = field(
        metadata={
            "name": "timeChargeIsNeeded",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    time_charging_status_pev: TimeType = field(
        metadata={
            "name": "timeChargingStatusPEV",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    pevinfo_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PEVInfo_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class PowerFactorWithExcitationControlType(PowerFactorWithExcitation):
    """
    :ivar power_factor_with_excitation_control_type_r2_3:
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    model_config = ConfigDict(defer_build=True)
    power_factor_with_excitation_control_type_r2_3: None | Revision23Type = (
        field(
            default=None,
            metadata={
                "name": "PowerFactorWithExcitationControlType_r2_3",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class PowerStatusLink(Link):
    """
    SHALL contain a Link to an instance of PowerStatus.
    """

    model_config = ConfigDict(defer_build=True)
    power_status_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PowerStatusLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class PrepayOperationStatusLink(Link):
    """
    SHALL contain a Link to an instance of PrepayOperationStatus.
    """

    model_config = ConfigDict(defer_build=True)
    prepay_operation_status_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PrepayOperationStatusLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class PrepayOperationStatus1(Resource):
    """
    PrepayOperationStatus describes the status of the service or commodity
    being conditionally controlled by the Prepayment function set.

    :ivar credit_type_change: CreditTypeChange is used to define a
        pending change of creditTypeInUse, which will activate at a
        specified time.
    :ivar credit_type_in_use: CreditTypeInUse identifies whether the
        present mode of operation is consuming regular credit or
        emergency credit.
    :ivar service_change: ServiceChange is used to define a pending
        change of serviceStatus, which will activate at a specified
        time.
    :ivar service_status: ServiceStatus identifies whether the service
        is connected or disconnected, or armed for connection or
        disconnection.
    :ivar prepay_operation_status_r2_3:
    """

    class Meta:
        name = "PrepayOperationStatus"

    model_config = ConfigDict(defer_build=True)
    credit_type_change: None | CreditTypeChange = field(
        default=None,
        metadata={
            "name": "creditTypeChange",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    credit_type_in_use: None | CreditTypeType = field(
        default=None,
        metadata={
            "name": "creditTypeInUse",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    service_change: None | ServiceChange = field(
        default=None,
        metadata={
            "name": "serviceChange",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    service_status: ServiceStatusType = field(
        metadata={
            "name": "serviceStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    prepay_operation_status_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PrepayOperationStatus_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class PrepaymentLink(Link):
    """
    SHALL contain a Link to an instance of Prepayment.
    """

    model_config = ConfigDict(defer_build=True)
    prepayment_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PrepaymentLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RplsourceRoutes1(Resource):
    """
    A RPL source routes object.

    :ivar dest_address: See [RFC 6554].
    :ivar source_route: See [RFC 6554].
    :ivar rplsource_routes_r2_3:
    """

    class Meta:
        name = "RPLSourceRoutes"

    model_config = ConfigDict(defer_build=True)
    dest_address: bytes = field(
        metadata={
            "name": "DestAddress",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 16,
            "format": "base16",
        }
    )
    source_route: bytes = field(
        metadata={
            "name": "SourceRoute",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 16,
            "format": "base16",
        }
    )
    rplsource_routes_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RPLSourceRoutes_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RateComponentLink(Link):
    """
    SHALL contain a Link to an instance of RateComponent.
    """

    model_config = ConfigDict(defer_build=True)
    rate_component_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RateComponentLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ReactivePowerControlType(ReactivePower):
    """
    :ivar reactive_power_control_type_r2_3:
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    model_config = ConfigDict(defer_build=True)
    reactive_power_control_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReactivePowerControlType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class ReactivePowerDeltaControlType(ReactivePower):
    """
    :ivar reactive_power_delta_control_type_r2_3:
    :ivar bidirectional: Specifies the behavior of a delta DERControl
        Mode regarding switching from absorbing/receiving to
        injecting/delivering or vice versa.
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    model_config = ConfigDict(defer_build=True)
    reactive_power_delta_control_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReactivePowerDeltaControlType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    bidirectional: int = field(
        default=0,
        metadata={
            "type": "Attribute",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class ReadingBase(Resource):
    """
    Specific value measured by a meter or other asset.

    ReadingBase is abstract, used to define the elements common to Reading
    and IntervalReading.

    :ivar consumption_block: Indicates the consumption block related to
        the reading. REQUIRED if ReadingType numberOfConsumptionBlocks
        is non-zero. If not specified, is assumed to be "0 - N/A".
    :ivar quality_flags: List of codes indicating the quality of the
        reading, using specification: Bit 0 - valid: data that has gone
        through all required validation checks and either passed them
        all or has been verified Bit 1 - manually edited: Replaced or
        approved by a human Bit 2 - estimated using reference day: data
        value was replaced by a machine computed value based on analysis
        of historical data using the same type of measurement. Bit 3 -
        estimated using linear interpolation: data value was computed
        using linear interpolation based on the readings before and
        after it Bit 4 - questionable: data that has failed one or more
        checks Bit 5 - derived: data that has been calculated (using
        logic or mathematical operations), not necessarily measured
        directly Bit 6 - projected (forecast): data that has been
        calculated as a projection or forecast of future readings
    :ivar time_period: The time interval associated with the reading. If
        not specified, then defaults to the intervalLength specified in
        the associated ReadingType.
    :ivar tou_tier: Indicates the time of use tier related to the
        reading. REQUIRED if ReadingType numberOfTouTiers is non-zero.
        If not specified, is assumed to be "0 - N/A".
    :ivar value: Value in units specified by ReadingType
    :ivar reading_base_r2_3:
    """

    model_config = ConfigDict(defer_build=True)
    consumption_block: None | ConsumptionBlockType = field(
        default=None,
        metadata={
            "name": "consumptionBlock",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    quality_flags: None | bytes = field(
        default=None,
        metadata={
            "name": "qualityFlags",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 2,
            "format": "base16",
        },
    )
    time_period: None | DateTimeInterval = field(
        default=None,
        metadata={
            "name": "timePeriod",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    tou_tier: None | Toutype = field(
        default=None,
        metadata={
            "name": "touTier",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    value: None | int = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "min_inclusive": -140737488355328,
            "max_inclusive": 140737488355328,
        },
    )
    reading_base_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReadingBase_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ReadingLink(Link):
    """
    A Link to a Reading.
    """

    model_config = ConfigDict(defer_build=True)
    reading_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReadingLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ReadingTypeLink(Link):
    """
    SHALL contain a Link to an instance of ReadingType.
    """

    model_config = ConfigDict(defer_build=True)
    reading_type_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReadingTypeLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ReadingType1(Resource):
    """
    Type of data conveyed by a specific Reading.

    See IEC 61968 Part 9 Annex C for full definitions of these values.

    :ivar accumulation_behaviour: The “accumulation behaviour” indicates
        how the value is represented to accumulate over time.
    :ivar calorific_value: The amount of heat generated when a given
        mass of fuel is completely burned. The CalorificValue is used to
        convert the measured volume or mass of gas into kWh. The
        CalorificValue attribute represents the current active value.
    :ivar commodity: Indicates the commodity applicable to this
        ReadingType.
    :ivar conversion_factor: Accounts for changes in the volume of gas
        based on temperature and pressure. The ConversionFactor
        attribute represents the current active value. The
        ConversionFactor is dimensionless. If not present, no conversion
        is applied. A price server can advertise a new/different value
        at any time.
    :ivar data_qualifier: The data type can be used to describe a
        salient attribute of the data. Possible values are average,
        absolute, and etc.
    :ivar flow_direction: Anything involving current might have a flow
        direction.
    :ivar interval_length: Default interval length specified in seconds.
    :ivar kind: Compound class that contains kindCategory and kindIndex
    :ivar max_number_of_intervals: To be populated for mirrors of
        interval data to set the expected number of intervals per
        ReadingSet. Servers may discard intervals received that exceed
        this number.
    :ivar number_of_consumption_blocks: Number of consumption blocks. 0
        means not applicable, and is the default if not specified. The
        value needs to be at least 1 if any actual prices are provided.
    :ivar number_of_tou_tiers: The number of TOU tiers that can be used
        by any resource configured by this ReadingType. Servers SHALL
        populate this value with the largest touTier value that will
        &lt;i&gt;ever&lt;/i&gt; be used while this ReadingType is in
        effect. Servers SHALL set numberOfTouTiers equal to the number
        of standard TOU tiers plus the number of CPP tiers that may be
        used while this ReadingType is in effect. Servers SHALL specify
        a value between 0 and 255 (inclusive) for numberOfTouTiers
        (servers providing flat rate pricing SHOULD set numberOfTouTiers
        to 0, as in practice there is no difference between having no
        tiers and having one tier).
    :ivar phase: Contains phase information associated with the type.
    :ivar power_of_ten_multiplier: Indicates the power of ten multiplier
        applicable to the unit of measure of this ReadingType.
    :ivar sub_interval_length: Default sub-interval length specified in
        seconds for Readings of ReadingType. Some demand calculations
        are done over a number of smaller intervals. For example, in a
        rolling demand calculation, the demand value is defined as the
        rolling sum of smaller intervals over the intervalLength. The
        subintervalLength is the length of the smaller interval in this
        calculation. It SHALL be an integral division of the
        intervalLength. The number of sub-intervals can be calculated by
        dividing the intervalLength by the subintervalLength.
    :ivar supply_limit: Reflects the supply limit set in the meter. This
        value can be compared to the Reading value to understand if
        limits are being approached or exceeded. Units follow the same
        definition as in this ReadingType.
    :ivar tiered_consumption_blocks: Specifies whether or not the
        consumption blocks are differentiated by TOUTier or not. Default
        is false, if not specified. true = consumption accumulated over
        individual tiers false = consumption accumulated over all tiers
    :ivar uom: Indicates the measurement type for the units of measure
        for the readings of this type.
    :ivar reading_type_r2_3:
    """

    class Meta:
        name = "ReadingType"

    model_config = ConfigDict(defer_build=True)
    accumulation_behaviour: None | AccumulationBehaviourType = field(
        default=None,
        metadata={
            "name": "accumulationBehaviour",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    calorific_value: None | UnitValueType = field(
        default=None,
        metadata={
            "name": "calorificValue",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    commodity: None | CommodityType = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    conversion_factor: None | UnitValueType = field(
        default=None,
        metadata={
            "name": "conversionFactor",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    data_qualifier: None | DataQualifierType = field(
        default=None,
        metadata={
            "name": "dataQualifier",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    flow_direction: None | FlowDirectionType = field(
        default=None,
        metadata={
            "name": "flowDirection",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    interval_length: None | int = field(
        default=None,
        metadata={
            "name": "intervalLength",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    kind: None | KindType = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    max_number_of_intervals: None | int = field(
        default=None,
        metadata={
            "name": "maxNumberOfIntervals",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    number_of_consumption_blocks: None | int = field(
        default=None,
        metadata={
            "name": "numberOfConsumptionBlocks",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    number_of_tou_tiers: None | int = field(
        default=None,
        metadata={
            "name": "numberOfTouTiers",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    phase: None | PhaseCode = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    power_of_ten_multiplier: None | PowerOfTenMultiplierType = field(
        default=None,
        metadata={
            "name": "powerOfTenMultiplier",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    sub_interval_length: None | int = field(
        default=None,
        metadata={
            "name": "subIntervalLength",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    supply_limit: None | int = field(
        default=None,
        metadata={
            "name": "supplyLimit",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_inclusive": 281474976710655,
        },
    )
    tiered_consumption_blocks: None | bool = field(
        default=None,
        metadata={
            "name": "tieredConsumptionBlocks",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    uom: None | UomType = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReadingType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RegistrationLink(Link):
    """
    SHALL contain a Link to an instance of Registration.
    """

    model_config = ConfigDict(defer_build=True)
    registration_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RegistrationLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Registration1(Resource):
    """
    Registration represents an authorization to access the resources on a
    host.

    :ivar date_time_registered: Contains the time at which this
        registration was created, by which clients MAY prioritize
        information providers with the most recent registrations, when
        no additional direction from the consumer is available.
    :ivar p_in: Contains the registration PIN number associated with the
        device, including the checksum digit.
    :ivar registration_r2_3:
    :ivar poll_rate: DEPRECATED SHALL NOT be included by servers, but
        clients should note that it may be included by servers compliant
        with previous revisions of IEEE 2030.5.
    """

    class Meta:
        name = "Registration"

    model_config = ConfigDict(defer_build=True)
    date_time_registered: TimeType = field(
        metadata={
            "name": "dateTimeRegistered",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    p_in: Pintype = field(
        metadata={
            "name": "pIN",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    registration_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Registration_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class RespondableResource(Resource):
    """
    A Resource to which a Response can be requested.

    :ivar respondable_resource_r2_3:
    :ivar reply_to: A reference to the response resource address (URI).
        Required on a response to a GET if responseRequired is "true".
    :ivar response_required: Indicates whether or not a response is
        required upon receipt, creation or update of this resource.
        Responses shall be posted to the collection specified in
        "replyTo". If the resource has a deviceCategory field, devices
        that match one or more of the device types indicated in
        deviceCategory SHALL respond according to the rules listed
        below.  If the category does not match, the device SHALL NOT
        respond. If the resource does not have a deviceCategory field, a
        device receiving the resource SHALL respond according to the
        rules listed below. If a DERControl contains multiple DERControl
        Modes and if the Event responseRequired indicates, status
        changes for each DERControl Mode SHALL be provided. Clients
        SHOULD attempt to group all status changes for DERControl Modes
        with the same createdDateTime and the same status in a single
        Response. Value encoded as hex according to the following bit
        assignments, any combination is possible. See Table "Response
        types by function set" for the list of appropriate Response
        status codes to be sent for these purposes. 0 - End device shall
        indicate that message was received 1 - End device shall indicate
        specific response. 2 - End user / customer response is required.
        All other values reserved.
    """

    model_config = ConfigDict(defer_build=True)
    respondable_resource_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RespondableResource_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reply_to: None | str = field(
        default=None,
        metadata={
            "name": "replyTo",
            "type": "Attribute",
        },
    )
    response_required: bytes = field(
        default=b"\x00",
        metadata={
            "name": "responseRequired",
            "type": "Attribute",
            "max_length": 1,
            "format": "base16",
        },
    )


class Response1(Resource):
    """
    The Response object is the generic response data repository which is
    extended for specific function sets.

    :ivar created_date_time: The createdDateTime field contains the date
        and time when the acknowledgement/status occurred in the client.
        The client will provide the timestamp to ensure the proper time
        is captured in case the response is delayed in reaching the
        server (server receipt time would not be the same as the actual
        confirmation time). The time reported from the client should be
        relative to the time server indicated by the
        FunctionSetAssignment that also indicated the event resource; if
        no FunctionSetAssignment exists, the time of the server where
        the event resource was hosted.
    :ivar end_device_lfdi: Contains the LFDI of the device providing the
        response.
    :ivar status: The status field contains the acknowledgement or
        status. Each event type (DRLC, DER, Price, or Text) can return
        different status information (e.g. an Acknowledge will be
        returned for a Price event where a DRLC event can return Event
        Received, Event Started, and Event Completed). The Status field
        value definitions are defined in Table "Response types by
        function set."
    :ivar subject: The subject field provides a method to match the
        response with the originating event. It is populated with the
        mRID of the original object.
    :ivar response_r2_3:
    """

    class Meta:
        name = "Response"

    model_config = ConfigDict(defer_build=True)
    created_date_time: None | TimeType = field(
        default=None,
        metadata={
            "name": "createdDateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    end_device_lfdi: bytes = field(
        metadata={
            "name": "endDeviceLFDI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 20,
            "format": "base16",
        }
    )
    status: None | int = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    subject: MRidtype = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    response_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Response_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class SelfDeviceLink(Link):
    """
    SHALL contain a Link to an instance of SelfDevice.
    """

    model_config = ConfigDict(defer_build=True)
    self_device_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SelfDeviceLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ServiceSupplierLink(Link):
    """
    SHALL contain a Link to an instance of ServiceSupplier.
    """

    model_config = ConfigDict(defer_build=True)
    service_supplier_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ServiceSupplierLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class SubscribableResource(Resource):
    """
    A Resource to which a Subscription can be requested.

    :ivar subscribable_resource_r2_3:
    :ivar subscribable: Indicates whether or not subscriptions are
        supported for this resource, and whether or not conditional
        (thresholds) are supported. If not specified, is "not
        subscribable" (0).
    """

    model_config = ConfigDict(defer_build=True)
    subscribable_resource_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SubscribableResource_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    subscribable: int = field(
        default=0,
        metadata={
            "type": "Attribute",
        },
    )


class SubscriptionBase(Resource):
    """
    Holds the information related to a client subscription to receive
    updates to a resource automatically.

    The actual resources may be passed in the Notification by specifying a
    specific xsi:type for the Resource and passing the full representation.

    :ivar subscribed_resource: The resource for which the subscription
        applies. Query string parameters SHALL NOT be specified when
        subscribing to list resources.  Should a query string parameter
        be specified, servers SHALL ignore them.
    :ivar subscription_base_r2_3:
    """

    model_config = ConfigDict(defer_build=True)
    subscribed_resource: str = field(
        metadata={
            "name": "subscribedResource",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    subscription_base_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SubscriptionBase_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class SupplyInterruptionOverride1(Resource):
    """
    SupplyInterruptionOverride: There may be periods of time when social,
    regulatory or other concerns mean that service should not be
    interrupted, even when available credit has been exhausted.

    Each Prepayment instance links to a List of SupplyInterruptionOverride
    instances. Each SupplyInterruptionOverride defines a contiguous period
    of time during which supply SHALL NOT be interrupted.

    :ivar description: The description is a human readable text
        describing or naming the object.
    :ivar interval: Interval defines the period of time during which
        supply should not be interrupted.
    :ivar supply_interruption_override_r2_3:
    """

    class Meta:
        name = "SupplyInterruptionOverride"

    model_config = ConfigDict(defer_build=True)
    description: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    interval: DateTimeInterval = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    supply_interruption_override_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SupplyInterruptionOverride_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class SupportedLocale1(Resource):
    """
    Specifies a locale that is supported.

    :ivar locale: The code for a locale that is supported
    :ivar supported_locale_r2_3:
    """

    class Meta:
        name = "SupportedLocale"

    model_config = ConfigDict(defer_build=True)
    locale: LocaleType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    supported_locale_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SupportedLocale_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class TariffProfileLink(Link):
    """
    SHALL contain a Link to an instance of TariffProfile.
    """

    model_config = ConfigDict(defer_build=True)
    tariff_profile_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TariffProfileLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class TimeLink(Link):
    """
    SHALL contain a Link to an instance of Time.
    """

    model_config = ConfigDict(defer_build=True)
    time_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TimeLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Time1(Resource):
    """
    Contains the representation of time, constantly updated.

    :ivar current_time: The current time, in the format defined by
        TimeType.
    :ivar dst_end_time: Time at which daylight savings ends (dstOffset
        no longer applied).  Result of dstEndRule calculation.
    :ivar dst_offset: Daylight savings time offset from local standard
        time. A typical practice is advancing clocks one hour when
        daylight savings time is in effect, which would result in a
        positive dstOffset. If dstOffset is 0, dstStartTime and
        dstEndTime SHALL be ignored.
    :ivar dst_start_time: Time at which daylight savings begins (apply
        dstOffset).  Result of dstStartRule calculation.
    :ivar local_time: Local time: localTime = currentTime + tzOffset (+
        dstOffset when in effect).
    :ivar quality: Metric indicating the quality of the time source from
        which the service acquired time. Lower (smaller) quality
        enumeration values are assumed to be more accurate. 3 - time
        obtained from external authoritative source such as NTP 4 - time
        obtained from level 3 source 5 - time manually set or obtained
        from level 4 source 6 - time obtained from level 5 source 7 -
        time intentionally uncoordinated All other values are reserved
        for future use.
    :ivar tz_offset: Local time zone offset from currentTime. Does not
        include any daylight savings time offsets. For American time
        zones, a negative tzOffset SHALL be used (eg, EST = GMT-5 which
        is -18000).
    :ivar time_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "Time"

    model_config = ConfigDict(defer_build=True)
    current_time: TimeType = field(
        metadata={
            "name": "currentTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    dst_end_time: TimeType = field(
        metadata={
            "name": "dstEndTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    dst_offset: TimeOffsetType = field(
        metadata={
            "name": "dstOffset",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    dst_start_time: TimeType = field(
        metadata={
            "name": "dstStartTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    local_time: None | TimeType = field(
        default=None,
        metadata={
            "name": "localTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    quality: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    tz_offset: TimeOffsetType = field(
        metadata={
            "name": "tzOffset",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    time_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Time_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class UnsignedActivePowerControlType(UnsignedActivePower):
    """
    :ivar unsigned_active_power_control_type_r2_3:
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    model_config = ConfigDict(defer_build=True)
    unsigned_active_power_control_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UnsignedActivePowerControlType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class UnsignedFixedVarControlType(UnsignedFixedVar):
    """
    :ivar unsigned_fixed_var_control_type_r2_3:
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    model_config = ConfigDict(defer_build=True)
    unsigned_fixed_var_control_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UnsignedFixedVarControlType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class UnsignedReactivePowerControlType(UnsignedReactivePower):
    """
    :ivar unsigned_reactive_power_control_type_r2_3:
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    model_config = ConfigDict(defer_build=True)
    unsigned_reactive_power_control_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UnsignedReactivePowerControlType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class UsagePointLink(Link):
    """
    SHALL contain a Link to an instance of UsagePoint.
    """

    model_config = ConfigDict(defer_build=True)
    usage_point_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UsagePointLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class VoltageRmscontrolType(VoltageRms):
    """
    :ivar voltage_rmscontrol_type_r2_3:
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    class Meta:
        name = "VoltageRMSControlType"

    model_config = ConfigDict(defer_build=True)
    voltage_rmscontrol_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "VoltageRMSControlType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class AccountBalance1(Resource):
    """
    AccountBalance contains the regular credit and emergency credit balance
    for this given service or commodity prepay instance.

    It may also contain status information concerning the balance data.

    :ivar available_credit: AvailableCredit shows the balance of the sum
        of credits minus the sum of charges. In a Central Wallet mode
        this value may be passed down to the Prepayment server via an
        out-of-band mechanism. In Local or ESI modes, this value may be
        calculated based upon summation of CreditRegister transactions
        minus consumption charges calculated using Metering (and
        possibly Pricing) function set data. This value may be negative;
        for instance, if disconnection is prevented due to a Supply
        Interruption Override.
    :ivar credit_status: CreditStatus identifies whether the present
        value of availableCredit is considered OK, low, exhausted, or
        negative.
    :ivar emergency_credit: EmergencyCredit is the amount of credit
        still available for the given service or commodity prepayment
        instance. If both availableCredit and emergyCredit are
        exhausted, then service will typically be disconnected.
    :ivar emergency_credit_status: EmergencyCreditStatus identifies
        whether the present value of emergencyCredit is considered OK,
        low, exhausted, or negative.
    :ivar account_balance_r2_3:
    """

    class Meta:
        name = "AccountBalance"

    model_config = ConfigDict(defer_build=True)
    available_credit: AccountingUnit = field(
        metadata={
            "name": "availableCredit",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    credit_status: None | CreditStatusType = field(
        default=None,
        metadata={
            "name": "creditStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    emergency_credit: None | AccountingUnit = field(
        default=None,
        metadata={
            "name": "emergencyCredit",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    emergency_credit_status: None | CreditStatusType = field(
        default=None,
        metadata={
            "name": "emergencyCreditStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    account_balance_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AccountBalance_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ActiveBillingPeriodListLink(ListLink):
    """
    DEPRECATED SHALL NOT be included by servers, but clients should note
    that it may be included by servers compliant with previous revisions of
    IEEE 2030.5.
    """

    model_config = ConfigDict(defer_build=True)
    active_billing_period_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActiveBillingPeriodListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ActiveCreditRegisterListLink(ListLink):
    """
    DEPRECATED SHALL NOT be included by servers, but clients should note
    that it may be included by servers compliant with previous revisions of
    IEEE 2030.5.
    """

    model_config = ConfigDict(defer_build=True)
    active_credit_register_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActiveCreditRegisterListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ActiveDercontrolListLink(ListLink):
    """
    DEPRECATED SHALL NOT be included by servers, but clients should note
    that it may be included by servers compliant with previous revisions of
    IEEE 2030.5.
    """

    class Meta:
        name = "ActiveDERControlListLink"

    model_config = ConfigDict(defer_build=True)
    active_dercontrol_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActiveDERControlListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ActiveEndDeviceControlListLink(ListLink):
    """
    DEPRECATED SHALL NOT be included by servers, but clients should note
    that it may be included by servers compliant with previous revisions of
    IEEE 2030.5.
    """

    model_config = ConfigDict(defer_build=True)
    active_end_device_control_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActiveEndDeviceControlListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ActiveFlowReservationListLink(ListLink):
    """
    DEPRECATED SHALL NOT be included by servers, but clients should note
    that it may be included by servers compliant with previous revisions of
    IEEE 2030.5.
    """

    model_config = ConfigDict(defer_build=True)
    active_flow_reservation_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActiveFlowReservationListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ActiveProjectionReadingListLink(ListLink):
    """
    DEPRECATED SHALL NOT be included by servers, but clients should note
    that it may be included by servers compliant with previous revisions of
    IEEE 2030.5.
    """

    model_config = ConfigDict(defer_build=True)
    active_projection_reading_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActiveProjectionReadingListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ActiveSupplyInterruptionOverrideListLink(ListLink):
    """
    DEPRECATED SHALL NOT be included by servers, but clients should note
    that it may be included by servers compliant with previous revisions of
    IEEE 2030.5.
    """

    model_config = ConfigDict(defer_build=True)
    active_supply_interruption_override_list_link_r2_3: (
        None | Revision23Type
    ) = field(
        default=None,
        metadata={
            "name": "ActiveSupplyInterruptionOverrideListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ActiveTargetReadingListLink(ListLink):
    """
    DEPRECATED SHALL NOT be included by servers, but clients should note
    that it may be included by servers compliant with previous revisions of
    IEEE 2030.5.
    """

    model_config = ConfigDict(defer_build=True)
    active_target_reading_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActiveTargetReadingListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ActiveTextMessageListLink(ListLink):
    """
    DEPRECATED SHALL NOT be included by servers, but clients should note
    that it may be included by servers compliant with previous revisions of
    IEEE 2030.5.
    """

    model_config = ConfigDict(defer_build=True)
    active_text_message_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActiveTextMessageListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ActiveTimeTariffIntervalListLink(ListLink):
    """
    DEPRECATED SHALL NOT be included by servers, but clients should note
    that it may be included by servers compliant with previous revisions of
    IEEE 2030.5.
    """

    model_config = ConfigDict(defer_build=True)
    active_time_tariff_interval_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ActiveTimeTariffIntervalListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class AggregatedDevice(AggregatedDevice1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class AggregatedDeviceListLink(ListLink):
    """
    SHALL contain a Link to a List of AggregatedDevice instances.

    An AbstractDevice (and its derivatives) MAY be an aggregation of
    multiple assets. If so, it MAY contain an AggregatedDeviceList.
    """

    model_config = ConfigDict(defer_build=True)
    aggregated_device_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AggregatedDeviceListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class AggregationPriority1(IdentifiedObject):
    """
    Contains the order in which an aggregation with a priority distribution
    is to be prioritized.

    If an aggregation has a distribution of Priority, then this resource
    SHALL be present. If an aggregation does not have a distribution of
    Priority, then this resource SHALL NOT be present. PriorityData SHALL
    be listed in order of priority, with the highest priority listed first.
    Note that if there are a large number of PriorityData, then this
    resource could grow large. Devices SHOULD use Range / Content-Range for
    transferring large resources as well as HTTP HEAD or other HTTP
    mechanisms to determine the size of the resource.
    """

    class Meta:
        name = "AggregationPriority"

    model_config = ConfigDict(defer_build=True)
    priority_data: list[PriorityData] = field(
        default_factory=list,
        metadata={
            "name": "PriorityData",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "min_occurs": 1,
        },
    )
    aggregation_priority_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AggregationPriority_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class AssociatedDerprogramListLink(ListLink):
    """
    SHALL contain a Link to a List of DERPrograms having the DERControl(s)
    for this DER.
    """

    class Meta:
        name = "AssociatedDERProgramListLink"

    model_config = ConfigDict(defer_build=True)
    associated_derprogram_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AssociatedDERProgramListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class BillingPeriod(BillingPeriod1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class BillingPeriodListLink(ListLink):
    """
    SHALL contain a Link to a List of BillingPeriod instances.
    """

    model_config = ConfigDict(defer_build=True)
    billing_period_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "BillingPeriodListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class BillingReadingListLink(ListLink):
    """
    SHALL contain a Link to a List of BillingReading instances.
    """

    model_config = ConfigDict(defer_build=True)
    billing_reading_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "BillingReadingListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class BillingReadingSetListLink(ListLink):
    """
    SHALL contain a Link to a List of BillingReadingSet instances.
    """

    model_config = ConfigDict(defer_build=True)
    billing_reading_set_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "BillingReadingSetListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class BillingReading1(ReadingBase):
    """
    Data captured at regular intervals of time.

    Interval data could be captured as incremental data, absolute data, or
    relative data. The source for the data is usually a tariff quantity or
    an engineering quantity. Data is typically captured in time-tagged,
    uniform, fixed-length intervals of 5 min, 10 min, 15 min, 30 min, or 60
    min. However, consumption aggregations can also be represented with
    this class.
    """

    class Meta:
        name = "BillingReading"

    model_config = ConfigDict(defer_build=True)
    charge: list[Charge] = field(
        default_factory=list,
        metadata={
            "name": "Charge",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    billing_reading_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "BillingReading_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ConsumptionTariffInterval(ConsumptionTariffInterval1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ConsumptionTariffIntervalListLink(ListLink):
    """
    SHALL contain a Link to a List of ConsumptionTariffInterval instances.
    """

    model_config = ConfigDict(defer_build=True)
    consumption_tariff_interval_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ConsumptionTariffIntervalListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ConsumptionTariffIntervalList1(List):
    """
    A List element to hold ConsumptionTariffInterval objects.
    """

    class Meta:
        name = "ConsumptionTariffIntervalList"

    model_config = ConfigDict(defer_build=True)
    consumption_tariff_interval: list[ConsumptionTariffInterval1] = field(
        default_factory=list,
        metadata={
            "name": "ConsumptionTariffInterval",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    consumption_tariff_interval_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ConsumptionTariffIntervalList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class CreditRegisterListLink(ListLink):
    """
    SHALL contain a Link to a List of CreditRegister instances.
    """

    model_config = ConfigDict(defer_build=True)
    credit_register_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CreditRegisterListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class CreditRegister1(IdentifiedObject):
    """
    CreditRegister instances define a credit-modifying transaction.

    Typically this would be a credit-adding transaction, but may be a
    subtracting transaction (perhaps in response to an out-of-band debt
    signal).

    :ivar credit_amount: CreditAmount is the amount of credit being
        added by a particular CreditRegister transaction. Negative
        values indicate that credit is being subtracted.
    :ivar credit_type: CreditType indicates whether the credit
        transaction applies to regular or emergency credit.
    :ivar effective_time: EffectiveTime identifies the time at which the
        credit transaction goes into effect. For credit addition
        transactions, this is typically the moment at which the
        transaction takes place. For credit subtraction transactions,
        (e.g., non-fuel debt recovery transactions initiated from a
        back-haul or ESI) this may be a future time at which credit is
        deducted.
    :ivar token: Token is security data that authenticates the
        legitimacy of the transaction. The details of this token are not
        defined by IEEE 2030.5. How a Prepayment server handles this
        field is left as vendor specific implementation or will be
        defined by one or more other standards.
    :ivar credit_register_r2_3:
    """

    class Meta:
        name = "CreditRegister"

    model_config = ConfigDict(defer_build=True)
    credit_amount: AccountingUnit = field(
        metadata={
            "name": "creditAmount",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    credit_type: None | CreditTypeType = field(
        default=None,
        metadata={
            "name": "creditType",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    effective_time: TimeType = field(
        metadata={
            "name": "effectiveTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    token: str = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 32,
        }
    )
    credit_register_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CreditRegister_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class CustomerAccountListLink(ListLink):
    """
    SHALL contain a Link to a List of CustomerAccount instances.
    """

    model_config = ConfigDict(defer_build=True)
    customer_account_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CustomerAccountListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class CustomerAgreementListLink(ListLink):
    """
    SHALL contain a Link to a List of CustomerAgreement instances.
    """

    model_config = ConfigDict(defer_build=True)
    customer_agreement_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CustomerAgreementListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Deravailability1(SubscribableResource):
    """
    Indicates current reserve status.

    :ivar availability_duration: Indicates number of seconds the DER
        will be able to deliver active power at the reservePercent
        level.
    :ivar max_charge_duration: Indicates number of seconds the DER will
        be able to receive active power at the reserveChargePercent
        level.
    :ivar reading_time: The timestamp when the DER availability was last
        updated.
    :ivar reserve_charge_percent: Percent of continuous received active
        power (%setMaxChargeRateW) that is estimated to be available in
        reserve.
    :ivar reserve_percent: Percent of continuous delivered active power
        (%setMaxW) that is estimated to be available in reserve.
    :ivar stat_var_absorb_avail: Estimated reserve reactive power for
        absorption / reception, in var. This value is equal to
        (estimated maximum possible absorbed / received vars at
        readingTime) - (current vars at readingTime).
    :ivar stat_var_avail: Estimated reserve reactive power for injection
        / delivery, in var. This value is equal to (estimated maximum
        possible injected / delivered vars at readingTime) - (current
        vars at readingTime). Note that this value SHALL always be
        positive (defined as ReactivePower for legacy reasons).
    :ivar stat_wabsorb_avail: Estimated reserve active power for
        absorption / reception, in watts. This value is equal to
        (estimated maximum possible input at readingTime) - (current
        input at readingTime). Note that "current input" is defined to
        be greater than or equal to zero (not negative).
    :ivar stat_wavail: Estimated reserve active power for injection /
        delivery, in watts. This value is equal to (estimated maximum
        possible output at readingTime) - (current output at
        readingTime). Note that this value SHALL always be positive
        (defined as ActivePower for legacy reasons). Also note that
        "current output" is defined to be greater than or equal to zero
        (not negative).
    :ivar deravailability_r2_3:
    """

    class Meta:
        name = "DERAvailability"

    model_config = ConfigDict(defer_build=True)
    availability_duration: None | int = field(
        default=None,
        metadata={
            "name": "availabilityDuration",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    max_charge_duration: None | int = field(
        default=None,
        metadata={
            "name": "maxChargeDuration",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_time: TimeType = field(
        metadata={
            "name": "readingTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    reserve_charge_percent: None | PerCent = field(
        default=None,
        metadata={
            "name": "reserveChargePercent",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reserve_percent: None | PerCent = field(
        default=None,
        metadata={
            "name": "reservePercent",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    stat_var_absorb_avail: None | UnsignedReactivePower = field(
        default=None,
        metadata={
            "name": "statVarAbsorbAvail",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    stat_var_avail: None | ReactivePower = field(
        default=None,
        metadata={
            "name": "statVarAvail",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    stat_wabsorb_avail: None | UnsignedActivePower = field(
        default=None,
        metadata={
            "name": "statWAbsorbAvail",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    stat_wavail: None | ActivePower = field(
        default=None,
        metadata={
            "name": "statWAvail",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    deravailability_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERAvailability_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Dercapability(Dercapability1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERCapability"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DercomponentBase(SubscribableResource):
    """
    DER and DERComponent common base.
    """

    class Meta:
        name = "DERComponentBase"

    model_config = ConfigDict(defer_build=True)
    associated_usage_point_link: None | AssociatedUsagePointLink = field(
        default=None,
        metadata={
            "name": "AssociatedUsagePointLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    deravailability_link: None | DeravailabilityLink = field(
        default=None,
        metadata={
            "name": "DERAvailabilityLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercapability_link: None | DercapabilityLink = field(
        default=None,
        metadata={
            "name": "DERCapabilityLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dersettings_link: None | DersettingsLink = field(
        default=None,
        metadata={
            "name": "DERSettingsLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    derstatus_link: None | DerstatusLink = field(
        default=None,
        metadata={
            "name": "DERStatusLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercomponent_base_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERComponentBase_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DercomponentListLink(ListLink):
    """
    SHALL contain a Link to a List of DERComponent instances.
    """

    class Meta:
        name = "DERComponentListLink"

    model_config = ConfigDict(defer_build=True)
    dercomponent_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERComponentListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DercontrolBase(BaseModel):
    """
    Distributed Energy Resource (DER) Control Modes.

    :ivar op_mod_connect: Set DER as connected (true) or disconnected
        (false). Used in conjunction with ramp rate when re-connecting.
        Implies galvanic isolation. If galvanic isolation is not
        supported, a value of false implies de-energize. If both
        opModConnect and opModEnergize are present, the values are
        logically ANDed to determine the connection state.
    :ivar op_mod_delta_var: Change in reactive power, in var. This
        DERControl mode is relative to the current reactive power input
        or output at the time the DERControl begins.
    :ivar op_mod_delta_w: Change in active power, in Watts. This
        DERControl Mode is relative to the current active power input or
        output at the time the DERControl begins.
    :ivar op_mod_energize: Set DER as energized (true) or de-energized
        (false). Used in conjunction with ramp rate when re-energizing.
        If both opModConnect and opModEnergize are present, the values
        are logically ANDed to determine the connection state.
    :ivar op_mod_fixed_pfabsorb_w: The opModFixedPFAbsorbW function
        specifies a requested fixed Power Factor (PF) setting for when
        active power is being absorbed. The actual displacement SHALL be
        within the limits established by setMinPFOverExcited and
        setMinPFUnderExcited. If issued simultaneously with other
        reactive power DERControl Modes (e.g. opModFixedVar) the
        DERControl Mode resulting in least var magnitude SHOULD take
        precedence.
    :ivar op_mod_fixed_pfinject_w: The opModFixedPFInjectW function
        specifies a requested fixed Power Factor (PF) setting for when
        active power is being injected. The actual displacement SHALL be
        within the limits established by setMinPFOverExcited and
        setMinPFUnderExcited. If issued simultaneously with other
        reactive power DERControl Modes (e.g. opModFixedVar) the
        DERControl Mode resulting in least var magnitude SHOULD take
        precedence.
    :ivar op_mod_fixed_v: The opModFixedV function specifies a requested
        voltage setpoint, in %setVNom (in hundredths).
    :ivar op_mod_fixed_var: The opModFixedVar function specifies the
        delivered or received reactive power setpoint.  The context for
        the setpoint value is determined by refType and SHALL be one of
        %setMaxW, %setMaxVA, %setMaxVar, or %statVarAvail. If issued
        simultaneously with other reactive power DERControl Modes (e.g.
        opModFixedPFInjectW) the DERControl Mode resulting in least var
        magnitude SHOULD take precedence.
    :ivar op_mod_fixed_w: The opModFixedW function specifies a requested
        received (e.g., charge) or delivered (e.g., discharge) active
        power setpoint, in %setMaxChargeRateW if negative value or
        %setMaxW or %setMaxDischargeRateW if positive value (in
        hundredths).
    :ivar op_mod_freq_droop: Specifies a frequency-watt operation. This
        operation limits active power generation or consumption when the
        line frequency deviates from nominal by a specified amount.
    :ivar op_mod_freq_watt: Specify DERCurveLink for curveType == 0.
        The Frequency-Watt function limits active power generation or
        consumption when the line frequency deviates from nominal by a
        specified amount. The Frequency-Watt curve is specified as an
        array of Frequency-Watt pairs that are interpolated into a
        piecewise linear function with hysteresis.  The x value of each
        pair specifies a frequency in Hz. The y value specifies a
        corresponding active power output in %setMaxW.
    :ivar op_mod_grid_connect_permit: Permits (true) or disallows
        (false) a grid reconnection. This DERControl Mode is likely to
        be more useful for microgrid controllers.
    :ivar op_mod_hfrtmay_trip: Specify DERCurveLink for curveType == 1.
        The High Frequency Ride-Through (HFRT) function is specified by
        one or two duration-frequency curves that define the operating
        region under high frequency conditions. Each HFRT curve is
        specified by an array of duration-frequency pairs that will be
        interpolated into a piecewise linear function that defines an
        operating region. The x value of each pair specifies a duration
        (time at a given frequency in seconds). The y value of each pair
        specifies a frequency, in Hz. This DERControl Mode specifies the
        "may trip" region.
    :ivar op_mod_hfrtmust_trip: Specify DERCurveLink for curveType == 2.
        The High Frequency Ride-Through (HFRT) function is specified by
        a duration-frequency curve that defines the operating region
        under high frequency conditions.  Each HFRT curve is specified
        by an array of duration-frequency pairs that will be
        interpolated into a piecewise linear function that defines an
        operating region.  The x value of each pair specifies a duration
        (time at a given frequency in seconds). The y value of each pair
        specifies a frequency, in Hz. This DERControl Mode specifies the
        "must trip" region.
    :ivar op_mod_hvrtmay_trip: Specify DERCurveLink for curveType == 3.
        The High Voltage Ride-Through (HVRT) function is specified by
        one, two, or three duration-volt curves that define the
        operating region under high voltage conditions. Each HVRT curve
        is specified by an array of duration-volt pairs that will be
        interpolated into a piecewise linear function that defines an
        operating region. The x value of each pair specifies a duration
        (time at a given voltage in seconds). The y value of each pair
        specifies an effective percentage voltage, defined as ((locally
        measured voltage - setVRefOfs / setVRef). This DERControl Mode
        specifies the "may trip" region.
    :ivar op_mod_hvrtmomentary_cessation: Specify DERCurveLink for
        curveType == 4.  The High Voltage Ride-Through (HVRT) function
        is specified by duration-volt curves that define the operating
        region under high voltage conditions.  Each HVRT curve is
        specified by an array of duration-volt pairs that will be
        interpolated into a piecewise linear function that defines an
        operating region.  The x value of each pair specifies a duration
        (time at a given voltage in seconds). The y value of each pair
        specifies an effective percent voltage, defined as ((locally
        measured voltage - setVRefOfs) / setVRef). This DERControl Mode
        specifies the "momentary cessation" region.
    :ivar op_mod_hvrtmust_trip: Specify DERCurveLink for curveType == 5.
        The High Voltage Ride-Through (HVRT) function is specified by
        duration-volt curves that define the operating region under high
        voltage conditions.  Each HVRT curve is specified by an array of
        duration-volt pairs that will be interpolated into a piecewise
        linear function that defines an operating region.  The x value
        of each pair specifies a duration (time at a given voltage in
        seconds). The y value of each pair specifies an effective
        percent voltage, defined as ((locally measured voltage -
        setVRefOfs) / setVRef). This DERControl Mode specifies the "must
        trip" region.
    :ivar op_mod_island_permit: Permits (true) or disallows (false) grid
        islanding. This DERControl Mode is likely to be more useful for
        microgrid controllers.
    :ivar op_mod_lfrtmay_trip: Specify DERCurveLink for curveType == 6.
        The Low Frequency Ride-Through (LFRT) function is specified by
        one or two duration-frequency curves that define the operating
        region under low frequency conditions. Each LFRT curve is
        specified by an array of duration-frequency pairs that will be
        interpolated into a piecewise linear function that defines an
        operating region. The x value of each pair specifies a duration
        (time at a given frequency in seconds). The y value of each pair
        specifies a frequency, in Hz. This DERControl Mode specifies the
        "may trip" region.
    :ivar op_mod_lfrtmust_trip: Specify DERCurveLink for curveType == 7.
        The Low Frequency Ride-Through (LFRT) function is specified by a
        duration-frequency curve that defines the operating region under
        low frequency conditions.  Each LFRT curve is specified by an
        array of duration-frequency pairs that will be interpolated into
        a piecewise linear function that defines an operating region.
        The x value of each pair specifies a duration (time at a given
        frequency in seconds). The y value of each pair specifies a
        frequency, in Hz. This DERControl Mode specifies the "must trip"
        region.
    :ivar op_mod_lvrtmay_trip: Specify DERCurveLink for curveType == 8.
        The Low Voltage Ride-Through (LVRT) function is specified by
        one, two, or three duration-volt curves that define the
        operating region under low voltage conditions. Each LVRT curve
        is specified by an array of duration-volt pairs that will be
        interpolated into a piecewise linear function that defines an
        operating region. The x value of each pair specifies a duration
        (time at a given voltage in seconds). The y value of each pair
        specifies an effective percent voltage, defined as ((locally
        measured voltage - setVRefOfs) / setVRef). This DERControl Mode
        specifies the "may trip" region.
    :ivar op_mod_lvrtmomentary_cessation: Specify DERCurveLink for
        curveType == 9.  The Low Voltage Ride-Through (LVRT) function is
        specified by duration-volt curves that define the operating
        region under low voltage conditions.  Each LVRT curve is
        specified by an array of duration-volt pairs that will be
        interpolated into a piecewise linear function that defines an
        operating region.  The x value of each pair specifies a duration
        (time at a given voltage in seconds). The y value of each pair
        specifies an effective percent voltage, defined as ((locally
        measured voltage - setVRefOfs) / setVRef). This DERControl Mode
        specifies the "momentary cessation" region.
    :ivar op_mod_lvrtmust_trip: Specify DERCurveLink for curveType ==
        10.  The Low Voltage Ride-Through (LVRT) function is specified
        by duration-volt curves that define the operating region under
        low voltage conditions.  Each LVRT curve is specified by an
        array of duration-volt pairs that will be interpolated into a
        piecewise linear function that defines an operating region.  The
        x value of each pair specifies a duration (time at a given
        voltage in seconds). The y value of each pair specifies an
        effective percent voltage, defined as ((locally measured voltage
        - setVRefOfs) / setVRef). This DERControl Mode specifies the
        "must trip" region.
    :ivar op_mod_max_lim_pct_vaabsorb: The opModMaxLimPctVAAbsorb
        function sets the maximum apparent power absorption level at the
        electrical reference point as a percentage of set capacity
        (%setMaxChargeRateVA, in hundredths). If issued simultaneously
        with other active or reactive power modes/controls, this
        mode/control SHOULD take precedence.
    :ivar op_mod_max_lim_pct_vainject: The opModMaxLimPctVAInject
        function sets the maximum apparent power injection level at the
        electrical reference point as a percentage of set capacity
        (%setMaxVA, in hundredths). If issued simultaneously with other
        active or reactive power modes/controls, this mode/control
        SHOULD take precedence.
    :ivar op_mod_max_lim_pct_var_absorb: The opModMaxLimPctVarAbsorb
        function sets the maximum reactive power absorption level at the
        electrical reference point as a percentage of set capacity (in
        hundredths). The context for the setpoint value is determined by
        refType and SHALL be one of %setMaxW, %setMaxVA, %setMaxVar, or
        %statVarAvail.
    :ivar op_mod_max_lim_pct_var_inject: The opModMaxLimPctVarInject
        function sets the maximum reactive power injection level at the
        electrical reference point as a percentage of set capacity (in
        hundredths). The context for the setpoint value is determined by
        refType and SHALL be one of %setMaxW, %setMaxVA, %setMaxVar, or
        %statVarAvail.
    :ivar op_mod_max_lim_pct_wabsorb: The opModMaxLimPctWAbsorb function
        sets the maximum active power absorption level at the electrical
        reference point as a percentage of set capacity
        (%setMaxChargeRateW, in hundredths). This limitation may be met
        e.g. by increasing PV output or by decreasing active power used
        to charge associated storage or power other loads.
    :ivar op_mod_max_lim_var_absorb: The opModMaxLimVarAbsorb function
        sets the maximum reactive power absorption level at the
        electrical reference point.
    :ivar op_mod_max_lim_var_inject: The opModMaxLimVarInject function
        sets the maximum reactive power injection level at the
        electrical reference point.
    :ivar op_mod_max_lim_w: The opModMaxLimW function sets the maximum
        active power generation level at the electrical reference point
        as a percentage of set capacity (%setMaxW, in hundredths). This
        limitation may be met e.g. by reducing PV output or by using
        excess PV output to charge associated storage or power other
        loads. Note: opModMaxLimW is inconsistently named for historical
        reasons as its units are PerCent instead of ActivePower. Its
        preferred name would have been opModMaxLimPctWInject.
    :ivar op_mod_max_lim_wabsorb: The opModMaxLimWAbsorb function sets
        the maximum active power absorption level at the electrical
        reference point. This limitation may be met e.g. by increasing
        PV output or by decreasing active power used to charge
        associated storage or power other loads.
    :ivar op_mod_max_lim_winject: The opModMaxLimWInject function sets
        the maximum active power generation level at the electrical
        reference point. This limitation may be met e.g. by reducing PV
        output or by using excess PV output to charge associated storage
        or power other loads.
    :ivar op_mod_target_v: Target output power, in Volts.
    :ivar op_mod_target_var: Target reactive power, in var. This
        DERControl Mode is likely to be more useful for aggregators, as
        individual DERs may not be able to maintain a target setting.
    :ivar op_mod_target_w: Target output power, in Watts. This
        DERControl Mode is likely to be more useful for aggregators, as
        individual DERs may not be able to maintain a target setting.
    :ivar op_mod_volt_var: Specify DERCurveLink for curveType == 11.
        The static volt-var function provides over- or under-excited var
        compensation as a function of measured voltage. The volt-var
        curve is specified as an array of volt-var pairs that are
        interpolated into a piecewise linear function with hysteresis.
        The x value of each pair specifies an effective percent voltage,
        defined as ((locally measured voltage - setVRefOfs) / setVRef)
        and SHOULD support a domain of at least 0 - 135. If VRef is
        present in DERCurve, then the x value of each pair is
        additionally multiplied by (VRef / 10000). The y value specifies
        a target var output interpreted as a signed percentage (-100 to
        100). The meaning of the y value is determined by yRefType and
        must be one of %setMaxW, %setMaxVA, %setMaxVar, or
        %statVarAvail.
    :ivar op_mod_volt_watt: Specify DERCurveLink for curveType == 12.
        The Volt-Watt varies active power as a function of measured
        voltage. The Volt-Watt curve is specified as an array of Volt-
        Watt pairs that are interpolated into a piecewise linear
        function with hysteresis. The x value of each pair specifies an
        effective percent voltage, defined as ((locally measured voltage
        - setVRefOfs) / setVRef) and SHOULD support a domain of at least
        0 - 135. The y value specifies an active power setting
        interpreted as a signed percentage (-100 to 100). The meaning of
        the y value is determined by yRefType and must be one of
        %setMaxW or %statWAvail.
    :ivar op_mod_watt_pf: Specify DERCurveLink for curveType == 13.  The
        Watt-PF function varies Power Factor (PF) as a function of
        delivered or received active power. The Watt-PF curve is
        specified as an array of Watt-PF coordinates that are
        interpolated into a piecewise linear function with hysteresis.
        The x value of each pair specifies a watt setting in
        %setMaxChargeRateW if negative value or %setMaxW or
        %setMaxDischargeRateW if positive value, (-100 to 100). The PF
        output setting is an unsigned displacement in the y value with
        the excitation set according to the excitation boolean. These
        settings are not expected to be updated very often during the
        life of the installation, therefore only a single curve is
        required.  If issued simultaneously with other reactive power
        DERControl Modes (e.g. opModFixedPFInjectW) the DERControl Mode
        resulting in least var magnitude SHOULD take precedence.
    :ivar op_mod_watt_var: Specify DERCurveLink for curveType == 14. The
        Watt-Var function varies vars as a function of delivered or
        received active power. The Watt-Var curve is specified as an
        array of Watt-Var pairs that are interpolated into a piecewise
        linear function with hysteresis. The x value of each pair
        specifies a watt setting in %setMaxChargeRateW if negative value
        or %setMaxW or %setMaxDischargeRateW if positive value, (-100 to
        100). The y value specifies a target var output interpreted as a
        signed percentage (-100 to 100). The meaning of the y value is
        determined by yRefType and must be one of %setMaxW, %setMaxVA,
        %setMaxVar, or %statVarAvail.
    :ivar ramp_tms: Requested ramp time, in hundredths of a second, for
        the device to transition from the current DERControl Mode(s) to
        the new DERControl Mode(s). If absent, use default ramp rate
        (setGradW).  Resolution is 1/100 sec.
    :ivar dercontrol_base_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    class Meta:
        name = "DERControlBase"

    model_config = ConfigDict(defer_build=True)
    op_mod_connect: None | bool = field(
        default=None,
        metadata={
            "name": "opModConnect",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_delta_var: None | ReactivePowerDeltaControlType = field(
        default=None,
        metadata={
            "name": "opModDeltaVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_delta_w: None | ActivePowerDeltaControlType = field(
        default=None,
        metadata={
            "name": "opModDeltaW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_energize: None | bool = field(
        default=None,
        metadata={
            "name": "opModEnergize",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_fixed_pfabsorb_w: None | PowerFactorWithExcitationControlType = (
        field(
            default=None,
            metadata={
                "name": "opModFixedPFAbsorbW",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    op_mod_fixed_pfinject_w: None | PowerFactorWithExcitationControlType = (
        field(
            default=None,
            metadata={
                "name": "opModFixedPFInjectW",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    op_mod_fixed_v: None | SignedPerCentControlType = field(
        default=None,
        metadata={
            "name": "opModFixedV",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_fixed_var: None | FixedVarControlType = field(
        default=None,
        metadata={
            "name": "opModFixedVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_fixed_w: None | SignedPerCentControlType = field(
        default=None,
        metadata={
            "name": "opModFixedW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_freq_droop: None | FreqDroopType = field(
        default=None,
        metadata={
            "name": "opModFreqDroop",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_freq_watt: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModFreqWatt",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_grid_connect_permit: None | bool = field(
        default=None,
        metadata={
            "name": "opModGridConnectPermit",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_hfrtmay_trip: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModHFRTMayTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_hfrtmust_trip: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModHFRTMustTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_hvrtmay_trip: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModHVRTMayTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_hvrtmomentary_cessation: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModHVRTMomentaryCessation",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_hvrtmust_trip: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModHVRTMustTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_island_permit: None | bool = field(
        default=None,
        metadata={
            "name": "opModIslandPermit",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_lfrtmay_trip: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModLFRTMayTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_lfrtmust_trip: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModLFRTMustTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_lvrtmay_trip: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModLVRTMayTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_lvrtmomentary_cessation: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModLVRTMomentaryCessation",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_lvrtmust_trip: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModLVRTMustTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_pct_vaabsorb: None | PerCentControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimPctVAAbsorb",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_pct_vainject: None | PerCentControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimPctVAInject",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_pct_var_absorb: None | UnsignedFixedVarControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimPctVarAbsorb",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_pct_var_inject: None | UnsignedFixedVarControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimPctVarInject",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_pct_wabsorb: None | PerCentControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimPctWAbsorb",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_var_absorb: None | UnsignedReactivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimVarAbsorb",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_var_inject: None | UnsignedReactivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimVarInject",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_w: None | PerCentControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_wabsorb: None | UnsignedActivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimWAbsorb",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_winject: None | UnsignedActivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimWInject",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_target_v: None | VoltageRmscontrolType = field(
        default=None,
        metadata={
            "name": "opModTargetV",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_target_var: None | ReactivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModTargetVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_target_w: None | ActivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModTargetW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_volt_var: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModVoltVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_volt_watt: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModVoltWatt",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_watt_pf: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModWattPF",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_watt_var: None | DercurveLink = field(
        default=None,
        metadata={
            "name": "opModWattVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    ramp_tms: None | int = field(
        default=None,
        metadata={
            "name": "rampTms",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercontrol_base_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERControlBase_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class DercontrolListLink(ListLink):
    """
    SHALL contain a Link to a List of DERControl instances.
    """

    class Meta:
        name = "DERControlListLink"

    model_config = ConfigDict(defer_build=True)
    dercontrol_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERControlListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DercontrolResponse1(Response1):
    """
    A response to a DERControl.

    :ivar modes_responded: Indicates individual DERControl Modes for
        which the DERControlResponse applies. This field SHALL be
        present in DERControlResponse complying to this revision of IEEE
        2030.5. However, it should be noted that in previous revisions
        of IEEE 2030.5 this field was not defined. When the field is not
        present, the individual DERControl Modes for which the
        DERControlResponse applies is ambiguous.
    :ivar modes_responded2: Indicates additional individual DERControl
        Modes for which the DERControlResponse applies. It should be
        noted that in previous revisions of IEEE 2030.5 this field was
        not defined. When the field is not present, the additional
        individual DERControl Modes for which the DERControlResponse
        applies is none (as none of those DERControl Modes existed in
        previous revisions of IEEE 2030.5).
    :ivar dercontrol_response_r2_3:
    """

    class Meta:
        name = "DERControlResponse"

    model_config = ConfigDict(defer_build=True)
    modes_responded: None | DercontrolType = field(
        default=None,
        metadata={
            "name": "modesResponded",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    modes_responded2: None | DercontrolType2 = field(
        default=None,
        metadata={
            "name": "modesResponded2",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercontrol_response_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERControlResponse_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DercurveListLink(ListLink):
    """
    SHALL contain a Link to a List of DERCurve instances.
    """

    class Meta:
        name = "DERCurveListLink"

    model_config = ConfigDict(defer_build=True)
    dercurve_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERCurveListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Dercurve1(IdentifiedObject):
    """
    DER related curves such as Volt-Var DERControl Mode curves.

    Relationship between an independent variable (X-axis) and a dependent
    variable (Y-axis).

    :ivar autonomous_vref_enable: If the curveType is opModVoltVar, then
        this field MAY be present. If the curveType is not opModVoltVar,
        then this field SHALL NOT be present. Enable/disable autonomous
        vRef adjustment. When enabled, the Volt-Var curve characteristic
        SHALL be adjusted autonomously as vRef changes and
        autonomousVRefTimeConstant SHALL be present. If a DER is able to
        support the Volt-Var DERControl Mode but is unable to support
        autonomous vRef adjustment, then the DER SHALL execute the curve
        without autonomous vRef adjustment. If not specified, then the
        value is false.
    :ivar autonomous_vref_time_constant: If the curveType is
        opModVoltVar, then this field MAY be present. If the curveType
        is not opModVoltVar, then this field SHALL NOT be present.
        Adjustment range for vRef time constant, in hundredths of a
        second.
    :ivar creation_time: The time at which the object was created.
    :ivar curve_data:
    :ivar curve_type: Specifies the associated curve-based DERControl
        Mode.
    :ivar open_loop_tms: Open loop response time, the time to ramp up to
        90% of the new target in response to the change in voltage, in
        hundredths of a second. Resolution is 1/100 sec. A value of 0 is
        used to mean no limit. When not present, the device SHOULD
        follow its default behavior.
    :ivar ramp_dec_tms: Decreasing ramp rate, interpreted as a
        percentage change in output capability limit per second (e.g.
        %setMaxW / sec).  Resolution is in hundredths of a
        percent/second. A value of 0 means there is no limit. If absent,
        ramp rate defaults to setGradW.
    :ivar ramp_inc_tms: Increasing ramp rate, interpreted as a
        percentage change in output capability limit per second (e.g.
        %setMaxW / sec).  Resolution is in hundredths of a
        percent/second. A value of 0 means there is no limit. If absent,
        ramp rate defaults to rampDecTms.
    :ivar ramp_pt1_tms: The configuration parameter for a low-pass
        filter, PT1 is a time, in hundredths of a second, in which the
        filter will settle to 95% of a step change in the input value.
        Resolution is 1/100 sec.
    :ivar v_ref: If the curveType is opModVoltVar, then this field MAY
        be present. If the curveType is not opModVoltVar, then this
        field SHALL NOT be present. The nominal AC voltage (RMS)
        adjustment to the voltage curve points for Volt-Var curves.
    :ivar x_multiplier: Exponent for X-axis value.
    :ivar y_multiplier: Exponent for Y-axis value.
    :ivar y_ref_type: The Y-axis units context.
    :ivar dercurve_r2_3:
    """

    class Meta:
        name = "DERCurve"

    model_config = ConfigDict(defer_build=True)
    autonomous_vref_enable: None | bool = field(
        default=None,
        metadata={
            "name": "autonomousVRefEnable",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    autonomous_vref_time_constant: None | int = field(
        default=None,
        metadata={
            "name": "autonomousVRefTimeConstant",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    creation_time: TimeType = field(
        metadata={
            "name": "creationTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    curve_data: list[CurveData] = field(
        default_factory=list,
        metadata={
            "name": "CurveData",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "min_occurs": 1,
            "max_occurs": 10,
        },
    )
    curve_type: DercurveType = field(
        metadata={
            "name": "curveType",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    open_loop_tms: None | int = field(
        default=None,
        metadata={
            "name": "openLoopTms",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    ramp_dec_tms: None | int = field(
        default=None,
        metadata={
            "name": "rampDecTms",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    ramp_inc_tms: None | int = field(
        default=None,
        metadata={
            "name": "rampIncTms",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    ramp_pt1_tms: None | int = field(
        default=None,
        metadata={
            "name": "rampPT1Tms",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    v_ref: None | PerCent = field(
        default=None,
        metadata={
            "name": "vRef",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    x_multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "name": "xMultiplier",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    y_multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "name": "yMultiplier",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    y_ref_type: DerunitRefType = field(
        metadata={
            "name": "yRefType",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    dercurve_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERCurve_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DerlistLink(ListLink):
    """
    SHALL contain a Link to a List of DER instances.
    """

    class Meta:
        name = "DERListLink"

    model_config = ConfigDict(defer_build=True)
    derlist_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DerprogramListLink(ListLink):
    """
    SHALL contain a Link to a List of DERProgram instances.
    """

    class Meta:
        name = "DERProgramListLink"

    model_config = ConfigDict(defer_build=True)
    derprogram_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERProgramListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Dersettings1(SubscribableResource):
    """
    Distributed energy resource settings.

    :ivar modes_enabled: Bitmap indicating the DERControl Modes enabled
        on the device. See DERControlType for values. If a DERControl
        Mode is supported (see DERCapability::modesSupported), but not
        enabled, the DERControl Mode will not be executed if
        encountered.
    :ivar modes_enabled2: Bitmap indicating the additional DERControl
        Modes enabled on the device. See DERControlType2 for values. If
        a DERControl Mode is supported (see
        DERCapability::modesSupported2), but not enabled, the DERControl
        Mode will not be executed if encountered.
    :ivar set_esdelay: Enter service delay, in hundredths of a second.
    :ivar set_eshigh_freq: Enter service frequency high. Specified in
        hundredths of Hz.
    :ivar set_eshigh_volt: Enter service voltage high. Specified as an
        effective percent voltage, defined as (100% * (locally measured
        voltage - setVRefOfs) / setVRef), in hundredths of a percent.
    :ivar set_eslow_freq: Enter service frequency low. Specified in
        hundredths of Hz.
    :ivar set_eslow_volt: Enter service voltage low. Specified as an
        effective percent voltage, defined as (100% * (locally measured
        voltage - setVRefOfs) / setVRef), in hundredths of a percent.
    :ivar set_esramp_tms: Enter service ramp time, in hundredths of a
        second.
    :ivar set_esrandom_delay: Enter service randomized delay, in
        hundredths of a second.
    :ivar set_grad_w: Set default rate of change (ramp rate) of active
        power output due to command or internal action, defined in
        %setWMax / second.  Resolution is in hundredths of a
        percent/second. A value of 0 means there is no limit.
        Interpreted as a percentage change in output capability limit
        per second when used as a default ramp rate.
    :ivar set_max_a: AC current maximum. Maximum AC current in RMS
        Amperes.
    :ivar set_max_ah: Maximum usable energy storage capacity of the DER,
        in AmpHours. Note: this may be different from physical
        capability.
    :ivar set_max_charge_rate_va: Apparent power charge maximum. Maximum
        apparent power the DER can absorb from the grid in Volt-Amperes.
        May differ from the apparent power maximum (setMaxVA).
    :ivar set_max_charge_rate_w: Maximum rate of energy transfer
        received by the storage device, in Watts. Defaults to
        rtgMaxChargeRateW.
    :ivar set_max_discharge_rate_va: Apparent power discharge maximum.
        Maximum apparent power the storage DER can deliver to the grid
        in Volt-Amperes. May differ from the apparent power maximum
        (setMaxVA) as this is specific to storage.
    :ivar set_max_discharge_rate_w: Maximum rate of energy transfer
        delivered by the storage device, in Watts. Defaults to
        rtgMaxDischargeRateW. May differ from the active power maximum
        (setMaxW) as this is specific to storage.
    :ivar set_max_v: AC voltage maximum setting.
    :ivar set_max_va: Set limit for maximum apparent power capability of
        the DER (in VA). Defaults to rtgMaxVA.
    :ivar set_max_var: Set limit for maximum reactive power
        injected/delivered by the DER (in var). SHALL be a positive
        value &amp;lt;= rtgMaxVar (default).
    :ivar set_max_var_neg: Set limit for maximum reactive power
        absorbed/received by the DER (in var). If present, SHALL be a
        negative value &amp;gt;= rtgMaxVarNeg (default). If absent,
        defaults to negative setMaxVar.
    :ivar set_max_w: Set limit for maximum active power capability of
        the DER (in W). Defaults to rtgMaxW.
    :ivar set_max_wh: Maximum energy storage capacity of the DER, in
        WattHours. Note: this may be different from physical capability.
    :ivar set_min_pfover_excited: Set minimum Power Factor displacement
        limit of the DER when injecting reactive power (over-excited);
        SHALL be a positive value between 0.0 (typically &amp;gt; 0.7)
        and 1.0, inclusive. SHALL be &amp;gt;= rtgMinPFOverExcited
        (default).
    :ivar set_min_pfunder_excited: Set minimum Power Factor displacement
        limit of the DER when absorbing reactive power (under-excited);
        SHALL be a positive value between 0.0 (typically &amp;gt; 0.7)
        and 1.0, inclusive. If present, SHALL be &amp;gt;=
        rtgMinPFUnderExcited (default).  If absent, defaults to
        setMinPFOverExcited.
    :ivar set_min_v: AC voltage minimum setting.
    :ivar set_soft_grad_w: Set soft-start rate of change (soft-start
        ramp rate) of active power output due to command or internal
        action, defined in %setWMax / second.  Resolution is in
        hundredths of a percent/second. A value of 0 means there is no
        limit. Interpreted as a percentage change in output capability
        limit per second when used as a ramp rate.
    :ivar set_vnom: AC voltage nominal setting.
    :ivar set_vref: The nominal AC voltage (RMS) at the reference point.
    :ivar set_vref_ofs: The nominal AC voltage (RMS) offset between the
        DER's electrical connection point and the reference point.
    :ivar updated_time: Specifies the time at which the DER information
        was last updated.
    :ivar dersettings_r2_3:
    """

    class Meta:
        name = "DERSettings"

    model_config = ConfigDict(defer_build=True)
    modes_enabled: None | DercontrolType = field(
        default=None,
        metadata={
            "name": "modesEnabled",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    modes_enabled2: None | DercontrolType2 = field(
        default=None,
        metadata={
            "name": "modesEnabled2",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_esdelay: None | int = field(
        default=None,
        metadata={
            "name": "setESDelay",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_eshigh_freq: None | int = field(
        default=None,
        metadata={
            "name": "setESHighFreq",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_eshigh_volt: None | int = field(
        default=None,
        metadata={
            "name": "setESHighVolt",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_eslow_freq: None | int = field(
        default=None,
        metadata={
            "name": "setESLowFreq",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_eslow_volt: None | int = field(
        default=None,
        metadata={
            "name": "setESLowVolt",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_esramp_tms: None | int = field(
        default=None,
        metadata={
            "name": "setESRampTms",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_esrandom_delay: None | int = field(
        default=None,
        metadata={
            "name": "setESRandomDelay",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_grad_w: int = field(
        metadata={
            "name": "setGradW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    set_max_a: None | CurrentRms = field(
        default=None,
        metadata={
            "name": "setMaxA",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_max_ah: None | AmpereHour = field(
        default=None,
        metadata={
            "name": "setMaxAh",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_max_charge_rate_va: None | ApparentPower = field(
        default=None,
        metadata={
            "name": "setMaxChargeRateVA",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_max_charge_rate_w: None | ActivePower = field(
        default=None,
        metadata={
            "name": "setMaxChargeRateW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_max_discharge_rate_va: None | ApparentPower = field(
        default=None,
        metadata={
            "name": "setMaxDischargeRateVA",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_max_discharge_rate_w: None | ActivePower = field(
        default=None,
        metadata={
            "name": "setMaxDischargeRateW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_max_v: None | VoltageRms = field(
        default=None,
        metadata={
            "name": "setMaxV",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_max_va: None | ApparentPower = field(
        default=None,
        metadata={
            "name": "setMaxVA",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_max_var: None | ReactivePower = field(
        default=None,
        metadata={
            "name": "setMaxVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_max_var_neg: None | ReactivePower = field(
        default=None,
        metadata={
            "name": "setMaxVarNeg",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_max_w: ActivePower = field(
        metadata={
            "name": "setMaxW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    set_max_wh: None | WattHour = field(
        default=None,
        metadata={
            "name": "setMaxWh",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_min_pfover_excited: None | PowerFactor = field(
        default=None,
        metadata={
            "name": "setMinPFOverExcited",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_min_pfunder_excited: None | PowerFactor = field(
        default=None,
        metadata={
            "name": "setMinPFUnderExcited",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_min_v: None | VoltageRms = field(
        default=None,
        metadata={
            "name": "setMinV",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_soft_grad_w: None | int = field(
        default=None,
        metadata={
            "name": "setSoftGradW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_vnom: None | VoltageRms = field(
        default=None,
        metadata={
            "name": "setVNom",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_vref: None | VoltageRms = field(
        default=None,
        metadata={
            "name": "setVRef",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_vref_ofs: None | VoltageRms = field(
        default=None,
        metadata={
            "name": "setVRefOfs",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    updated_time: TimeType = field(
        metadata={
            "name": "updatedTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    dersettings_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERSettings_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Derstatus1(SubscribableResource):
    """
    DER status information.

    :ivar alarm_status: Bitmap indicating the status of DER alarms (see
        DER LogEvents for more details). 0 - DER_FAULT_OVER_CURRENT 1 -
        DER_FAULT_OVER_VOLTAGE 2 - DER_FAULT_UNDER_VOLTAGE 3 -
        DER_FAULT_OVER_FREQUENCY 4 - DER_FAULT_UNDER_FREQUENCY 5 -
        DER_FAULT_VOLTAGE_IMBALANCE 6 - DER_FAULT_CURRENT_IMBALANCE 7 -
        DER_FAULT_EMERGENCY_LOCAL 8 - DER_FAULT_EMERGENCY_REMOTE 9 -
        DER_FAULT_LOW_POWER_INPUT 10 - DER_FAULT_PHASE_ROTATION 11-31 -
        Reserved
    :ivar connect_status: Connection status for DER. See
        ConnectStatusType2 for values.
    :ivar gen_connect_status: DEPRECATED SHALL NOT be included, but note
        that it may be included by devices compliant with previous
        revisions of IEEE 2030.5.
    :ivar inverter_status: DER InverterStatus/value. See
        InverterStatusType for values.
    :ivar local_control_mode_status: The local control mode status. See
        LocalControlModeStatusType for values.
    :ivar manufacturer_status: Manufacturer status code.
    :ivar operational_mode_status: Operational mode currently in use.
        See OperationalModeStatusType for values.
    :ivar reading_time: The timestamp when the current status was last
        updated.
    :ivar state_of_charge_status: State of charge status. See
        StateOfChargeStatusType for values.
    :ivar storage_mode_status: Storage mode status. See
        StorageModeStatusType for values.
    :ivar stor_connect_status: DEPRECATED SHALL NOT be included, but
        note that it may be included by devices compliant with previous
        revisions of IEEE 2030.5.
    :ivar derstatus_r2_3:
    """

    class Meta:
        name = "DERStatus"

    model_config = ConfigDict(defer_build=True)
    alarm_status: None | bytes = field(
        default=None,
        metadata={
            "name": "alarmStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 4,
            "format": "base16",
        },
    )
    connect_status: None | ConnectStatusType2 = field(
        default=None,
        metadata={
            "name": "connectStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    gen_connect_status: None | ConnectStatusType = field(
        default=None,
        metadata={
            "name": "genConnectStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    inverter_status: None | InverterStatusType = field(
        default=None,
        metadata={
            "name": "inverterStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    local_control_mode_status: None | LocalControlModeStatusType = field(
        default=None,
        metadata={
            "name": "localControlModeStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    manufacturer_status: None | ManufacturerStatusType = field(
        default=None,
        metadata={
            "name": "manufacturerStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    operational_mode_status: None | OperationalModeStatusType = field(
        default=None,
        metadata={
            "name": "operationalModeStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_time: TimeType = field(
        metadata={
            "name": "readingTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    state_of_charge_status: None | StateOfChargeStatusType = field(
        default=None,
        metadata={
            "name": "stateOfChargeStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    storage_mode_status: None | StorageModeStatusType = field(
        default=None,
        metadata={
            "name": "storageModeStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    stor_connect_status: None | ConnectStatusType = field(
        default=None,
        metadata={
            "name": "storConnectStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    derstatus_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERStatus_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DefaultDercontrolResponse1(Response1):
    """
    A response to a DefaultDERControl.

    :ivar defaults_responded: Indicates individual default DERControl
        Modes for which the DefaultDERControlResponse applies.
    :ivar modes_responded: Indicates individual DERControl Modes for
        which the DefaultDERControlResponse applies.
    :ivar modes_responded2: Indicates additional individual DERControl
        Modes for which the DefaultDERControlResponse applies.
    :ivar default_dercontrol_response_r2_3:
    """

    class Meta:
        name = "DefaultDERControlResponse"

    model_config = ConfigDict(defer_build=True)
    defaults_responded: DefaultDercontrolType = field(
        metadata={
            "name": "defaultsResponded",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    modes_responded: DercontrolType = field(
        metadata={
            "name": "modesResponded",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    modes_responded2: DercontrolType2 = field(
        metadata={
            "name": "modesResponded2",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    default_dercontrol_response_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DefaultDERControlResponse_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DemandResponseProgramListLink(ListLink):
    """
    SHALL contain a Link to a List of DemandResponseProgram instances.
    """

    model_config = ConfigDict(defer_build=True)
    demand_response_program_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DemandResponseProgramListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DeviceStatus1(Resource):
    """
    Status of device.

    :ivar changed_time: The time at which the reported values were
        recorded.
    :ivar on_count: The number of times that the device has been turned
        on: Count of "device on" times, since the last time the counter
        was reset
    :ivar op_state: Device operational state: 0 - Not applicable /
        Unknown 1 - Not operating 2 - Operating 3 - Starting up 4 -
        Shutting down 5 - At disconnect level 6 - kW ramping 7 - kVar
        ramping
    :ivar op_time: Total time device has operated: re-settable:
        Accumulated time in seconds since the last time the counter was
        reset.
    :ivar temperature:
    :ivar time_link:
    :ivar device_status_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "DeviceStatus"

    model_config = ConfigDict(defer_build=True)
    changed_time: TimeType = field(
        metadata={
            "name": "changedTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    on_count: None | int = field(
        default=None,
        metadata={
            "name": "onCount",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_state: None | int = field(
        default=None,
        metadata={
            "name": "opState",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_time: None | int = field(
        default=None,
        metadata={
            "name": "opTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    temperature: list[Temperature] = field(
        default_factory=list,
        metadata={
            "name": "Temperature",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    time_link: None | TimeLink = field(
        default=None,
        metadata={
            "name": "TimeLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    device_status_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DeviceStatus_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class DrResponse1(Response1):
    """
    A response to a Demand Response Load Control (EndDeviceControl)
    message.

    :ivar appliance_load_reduction:
    :ivar applied_target_reduction:
    :ivar duty_cycle:
    :ivar offset:
    :ivar override_duration: Indicates the amount of time, in seconds,
        that the client partially opts-out during the demand response
        event. When overriding within the allowed override duration, the
        client SHALL send a partial opt-out (Response status code 8) for
        partial opt-out upon completion, with the total time the event
        was overridden (this attribute) populated. The client SHALL send
        a no participation status response (status type 10) if the user
        partially opts-out for longer than
        EndDeviceControl.overrideDuration.
    :ivar set_point:
    :ivar dr_response_r2_3:
    """

    class Meta:
        name = "DrResponse"

    model_config = ConfigDict(defer_build=True)
    appliance_load_reduction: None | ApplianceLoadReduction = field(
        default=None,
        metadata={
            "name": "ApplianceLoadReduction",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    applied_target_reduction: None | AppliedTargetReduction = field(
        default=None,
        metadata={
            "name": "AppliedTargetReduction",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    duty_cycle: None | DutyCycle = field(
        default=None,
        metadata={
            "name": "DutyCycle",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    offset: None | Offset = field(
        default=None,
        metadata={
            "name": "Offset",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    override_duration: None | int = field(
        default=None,
        metadata={
            "name": "overrideDuration",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_point: None | SetPoint = field(
        default=None,
        metadata={
            "name": "SetPoint",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dr_response_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DrResponse_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class EndDeviceControlListLink(ListLink):
    """
    SHALL contain a Link to a List of EndDeviceControl instances.
    """

    model_config = ConfigDict(defer_build=True)
    end_device_control_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "EndDeviceControlListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class EndDeviceListLink(ListLink):
    """
    SHALL contain a Link to a List of EndDevice instances.
    """

    model_config = ConfigDict(defer_build=True)
    end_device_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "EndDeviceListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class File(File1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class FileListLink(ListLink):
    """
    SHALL contain a Link to a List of File instances.
    """

    model_config = ConfigDict(defer_build=True)
    file_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FileListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class FileList1(List):
    """
    A List element to hold File objects.

    :ivar file:
    :ivar file_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "FileList"

    model_config = ConfigDict(defer_build=True)
    file: list[File1] = field(
        default_factory=list,
        metadata={
            "name": "File",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    file_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FileList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class FileStatus1(Resource):
    """
    This object provides status of device file load and activation
    operations.

    :ivar activate_time: Date/time at which this File, referred to by
        FileLink, will be activated. Omission of or presence and value
        of this element SHALL exactly match omission or presence and
        value of the activateTime element from the File resource.
    :ivar file_link:
    :ivar load_percent: This element SHALL be set to the percentage of
        the file, indicated by FileLink, that was loaded during the
        latest load attempt. This value SHALL be reset to 0 each time a
        load attempt is started for the File indicated by FileLink. This
        value SHALL be increased when an LD receives HTTP response
        containing file content. This value SHALL be set to 100 when the
        full content of the file has been received by the LD
    :ivar next_request_attempt: This element SHALL be set to the time at
        which the LD will issue its next GET request for file content
        from the File indicated by FileLink
    :ivar request503_count: This value SHALL be reset to 0 when FileLink
        is first pointed at a new File. This value SHALL be incremented
        each time an LD receives a 503 error from the FS.
    :ivar request_fail_count: This value SHALL be reset to 0 when
        FileLink is first pointed at a new File. This value SHALL be
        incremented each time a GET request for file content failed. 503
        errors SHALL be excluded from this counter.
    :ivar status: Current loading status of the file indicated by
        FileLink. This element SHALL be set to one of the following
        values: 0 - No load operation in progress 1 - File load in
        progress (first request for file content has been issued by LD)
        2 - File load failed 3 - File loaded successfully (full content
        of file has been received by the LD), signature verification in
        progress 4 - File signature verification failed 5 - File
        signature verified, waiting to activate file. 6 - File
        activation failed 7 - File activation in progress 8 - File
        activated successfully (this state may not be reached/persisted
        through an image activation) 9-255 - Reserved for future use.
    :ivar status_time: This element SHALL be set to the time at which
        file status transitioned to the value indicated in the status
        element.
    :ivar file_status_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "FileStatus"

    model_config = ConfigDict(defer_build=True)
    activate_time: None | TimeType = field(
        default=None,
        metadata={
            "name": "activateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    file_link: None | FileLink = field(
        default=None,
        metadata={
            "name": "FileLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    load_percent: int = field(
        metadata={
            "name": "loadPercent",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    next_request_attempt: TimeType = field(
        metadata={
            "name": "nextRequestAttempt",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    request503_count: int = field(
        metadata={
            "name": "request503Count",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    request_fail_count: int = field(
        metadata={
            "name": "requestFailCount",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    status: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    status_time: TimeType = field(
        metadata={
            "name": "statusTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    file_status_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FileStatus_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class FlowReservationRequestListLink(ListLink):
    """
    SHALL contain a Link to a List of FlowReservationRequest instances.
    """

    model_config = ConfigDict(defer_build=True)
    flow_reservation_request_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FlowReservationRequestListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class FlowReservationRequest1(IdentifiedObject):
    """
    Used to request flow transactions.

    Client EndDevices submit a request for charging or discharging from the
    server. The server creates an associated FlowReservationResponse
    containing the charging parameters and interval to provide a lower
    aggregated demand at the premises, or within a larger part of the
    distribution system.

    :ivar creation_time: The time at which the request was created.
    :ivar duration_requested: A value that is calculated by the storage
        device that defines the minimum duration, in seconds, that it
        will take to complete the actual flow transaction, including any
        ramp times and conditioning times, if applicable.
    :ivar energy_requested: Indicates the total amount of energy, in
        Watt-Hours, requested to be transferred between the storage
        device and the electric power system. Positive values indicate
        charging and negative values indicate discharging. This sign
        convention is different than for the DER function where
        discharging is positive.  Note that the energyRequestNow
        attribute in the PowerStatus Object must always represent a
        charging solution and it is not allowed to have a negative
        value.
    :ivar interval_requested: The time window during which the flow
        reservation is needed. For example, if an electric vehicle is
        set with a 7:00 AM time charge is needed, and price drops to the
        lowest tier at 11:00 PM, then this window would likely be from
        11:00 PM until 7:00 AM.
    :ivar power_requested: Indicates the sustained level of power, in
        Watts, that is requested. For charging this is calculated by the
        storage device and it represents the charging system capability
        (which for an electric vehicle must also account for any power
        limitations due to the EVSE control pilot). For discharging, a
        lower value than the inverter capability can be used as a
        target.
    :ivar request_status:
    :ivar flow_reservation_request_r2_3:
    """

    class Meta:
        name = "FlowReservationRequest"

    model_config = ConfigDict(defer_build=True)
    creation_time: TimeType = field(
        metadata={
            "name": "creationTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    duration_requested: None | int = field(
        default=None,
        metadata={
            "name": "durationRequested",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    energy_requested: SignedRealEnergy = field(
        metadata={
            "name": "energyRequested",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    interval_requested: DateTimeInterval = field(
        metadata={
            "name": "intervalRequested",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    power_requested: ActivePower = field(
        metadata={
            "name": "powerRequested",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    request_status: RequestStatus = field(
        metadata={
            "name": "RequestStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    flow_reservation_request_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FlowReservationRequest_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class FlowReservationResponseListLink(ListLink):
    """
    SHALL contain a Link to a List of FlowReservationResponse instances.
    """

    model_config = ConfigDict(defer_build=True)
    flow_reservation_response_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FlowReservationResponseListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class FlowReservationResponseResponse1(Response1):
    """
    A response to a FlowReservationResponse.
    """

    class Meta:
        name = "FlowReservationResponseResponse"

    model_config = ConfigDict(defer_build=True)
    flow_reservation_response_response_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FlowReservationResponseResponse_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class FunctionSetAssignmentsListLink(ListLink):
    """
    SHALL contain a Link to a List of FunctionSetAssignments instances.
    """

    model_config = ConfigDict(defer_build=True)
    function_set_assignments_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FunctionSetAssignmentsListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class HistoricalReadingListLink(ListLink):
    """
    SHALL contain a Link to a List of HistoricalReading instances.
    """

    model_config = ConfigDict(defer_build=True)
    historical_reading_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "HistoricalReadingListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class IpaddrListLink(ListLink):
    """
    SHALL contain a Link to a List of IPAddr instances.
    """

    class Meta:
        name = "IPAddrListLink"

    model_config = ConfigDict(defer_build=True)
    ipaddr_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "IPAddrListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class IpinterfaceListLink(ListLink):
    """
    SHALL contain a Link to a List of IPInterface instances.
    """

    class Meta:
        name = "IPInterfaceListLink"

    model_config = ConfigDict(defer_build=True)
    ipinterface_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "IPInterfaceListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class LlinterfaceListLink(ListLink):
    """
    SHALL contain a Link to a List of LLInterface instances.
    """

    class Meta:
        name = "LLInterfaceListLink"

    model_config = ConfigDict(defer_build=True)
    llinterface_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "LLInterfaceListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class LoadShedAvailabilityListLink(ListLink):
    """
    SHALL contain a Link to a List of LoadShedAvailability instances.
    """

    model_config = ConfigDict(defer_build=True)
    load_shed_availability_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "LoadShedAvailabilityListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class LoadShedAvailability1(Resource):
    """
    Indicates current consumption status and ability to shed load.

    :ivar availability_duration: Indicates for how many seconds the
        consuming device will be able to reduce consumption at the
        maximum response level.
    :ivar demand_response_program_link:
    :ivar sheddable_percent: Maximum percent of current operating load
        that is estimated to be sheddable.
    :ivar sheddable_power: Maximum amount of current operating load that
        is estimated to be sheddable, in Watts.
    :ivar load_shed_availability_r2_3:
    """

    class Meta:
        name = "LoadShedAvailability"

    model_config = ConfigDict(defer_build=True)
    availability_duration: None | int = field(
        default=None,
        metadata={
            "name": "availabilityDuration",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    demand_response_program_link: None | DemandResponseProgramLink = field(
        default=None,
        metadata={
            "name": "DemandResponseProgramLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    sheddable_percent: None | PerCent = field(
        default=None,
        metadata={
            "name": "sheddablePercent",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    sheddable_power: None | ActivePower = field(
        default=None,
        metadata={
            "name": "sheddablePower",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    load_shed_availability_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "LoadShedAvailability_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class LogEvent(LogEvent1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class LogEventListLink(ListLink):
    """
    SHALL contain a Link to a List of LogEvent instances.
    """

    model_config = ConfigDict(defer_build=True)
    log_event_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "LogEventListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class MessagingProgramListLink(ListLink):
    """
    SHALL contain a Link to a List of MessagingProgram instances.
    """

    model_config = ConfigDict(defer_build=True)
    messaging_program_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MessagingProgramListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class MeterReadingBase(IdentifiedObject):
    """
    A container for associating ReadingType, Readings and ReadingSets.
    """

    model_config = ConfigDict(defer_build=True)
    meter_reading_base_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MeterReadingBase_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class MeterReadingListLink(ListLink):
    """
    SHALL contain a Link to a List of MeterReading instances.
    """

    model_config = ConfigDict(defer_build=True)
    meter_reading_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MeterReadingListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class MirrorUsagePointListLink(ListLink):
    """
    SHALL contain a Link to a List of MirrorUsagePoint instances.
    """

    model_config = ConfigDict(defer_build=True)
    mirror_usage_point_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MirrorUsagePointListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Neighbor(Neighbor1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class NeighborListLink(ListLink):
    """
    SHALL contain a Link to a List of Neighbor instances.
    """

    model_config = ConfigDict(defer_build=True)
    neighbor_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "NeighborListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class NeighborList1(List):
    """
    List of 15.4 neighbors.
    """

    class Meta:
        name = "NeighborList"

    model_config = ConfigDict(defer_build=True)
    neighbor: list[Neighbor1] = field(
        default_factory=list,
        metadata={
            "name": "Neighbor",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    neighbor_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "NeighborList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class NotificationListLink(ListLink):
    """
    SHALL contain a Link to a List of Notification instances.
    """

    model_config = ConfigDict(defer_build=True)
    notification_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "NotificationListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Notification1(SubscriptionBase):
    """
    Holds the information related to a client subscription to receive
    updates to a resource automatically.

    The actual resources may be passed in the Notification by specifying a
    specific xsi:type for the Resource and passing the full representation.

    :ivar created_date_time: The date and time that the Notification was
        created.
    :ivar new_resource_uri: The new location of the resource, if moved.
        This attribute SHALL be a fully-qualified absolute URI, not a
        relative reference.
    :ivar resource:
    :ivar status: 0 = Default Status 1 = Subscription canceled, no
        additional information 2 = Subscription canceled, resource moved
        3 = Subscription canceled, resource definition changed (e.g., a
        new version of IEEE 2030.5) 4 = Subscription canceled, resource
        deleted All other values reserved.
    :ivar subscription_uri: The subscription from which this
        notification was triggered. This attribute SHALL be a fully-
        qualified absolute URI, not a relative reference.
    :ivar notification_r2_3:
    """

    class Meta:
        name = "Notification"

    model_config = ConfigDict(defer_build=True)
    created_date_time: None | TimeType = field(
        default=None,
        metadata={
            "name": "createdDateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    new_resource_uri: None | str = field(
        default=None,
        metadata={
            "name": "newResourceURI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    resource: None | Resource = field(
        default=None,
        metadata={
            "name": "Resource",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    status: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    subscription_uri: str = field(
        metadata={
            "name": "subscriptionURI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    notification_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Notification_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class PowerStatus1(Resource):
    """
    Contains the status of the device's power sources.

    :ivar battery_status: Battery system status 0 = unknown 1 = normal
        (more than LowChargeThreshold remaining) 2 = low (less than
        LowChargeThreshold remaining) 3 = depleted (0% charge remaining)
        4 = not applicable (mains powered only)
    :ivar changed_time: The time at which the reported values were
        recorded.
    :ivar current_power_source: This value will be fixed for devices
        powered by a single source.  This value may change for devices
        able to transition between multiple power sources (mains to
        battery backup, etc.).
    :ivar estimated_charge_remaining: Estimate of remaining battery
        charge as a percent of full charge.
    :ivar estimated_time_remaining: Estimated time (in seconds) to total
        battery charge depletion (under current load)
    :ivar pevinfo:
    :ivar session_time_on_battery: If the device has a battery, this is
        the time since the device last switched to battery power, or the
        time since the device was restarted, whichever is less, in
        seconds.
    :ivar total_time_on_battery: If the device has a battery, this is
        the total time the device has been on battery power, in seconds.
        It may be reset when the battery is replaced.
    :ivar power_status_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "PowerStatus"

    model_config = ConfigDict(defer_build=True)
    battery_status: int = field(
        metadata={
            "name": "batteryStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    changed_time: TimeType = field(
        metadata={
            "name": "changedTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    current_power_source: PowerSourceType = field(
        metadata={
            "name": "currentPowerSource",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    estimated_charge_remaining: None | PerCent = field(
        default=None,
        metadata={
            "name": "estimatedChargeRemaining",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    estimated_time_remaining: None | int = field(
        default=None,
        metadata={
            "name": "estimatedTimeRemaining",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    pevinfo: None | Pevinfo = field(
        default=None,
        metadata={
            "name": "PEVInfo",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    session_time_on_battery: None | int = field(
        default=None,
        metadata={
            "name": "sessionTimeOnBattery",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    total_time_on_battery: None | int = field(
        default=None,
        metadata={
            "name": "totalTimeOnBattery",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    power_status_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PowerStatus_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class PrepayOperationStatus(PrepayOperationStatus1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class PrepaymentListLink(ListLink):
    """
    SHALL contain a Link to a List of Prepayment instances.
    """

    model_config = ConfigDict(defer_build=True)
    prepayment_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PrepaymentListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class PriceResponseCfgListLink(ListLink):
    """
    SHALL contain a Link to a List of PriceResponseCfg instances.
    """

    model_config = ConfigDict(defer_build=True)
    price_response_cfg_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PriceResponseCfgListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class PriceResponseCfg1(Resource):
    """
    Configuration data that specifies how price responsive devices SHOULD
    respond to price changes while acting upon a given RateComponent.

    :ivar consume_threshold: Price responsive clients acting upon the
        associated RateComponent SHOULD consume the associated commodity
        while the price is less than this threshold.
    :ivar max_reduction_threshold: Price responsive clients acting upon
        the associated RateComponent SHOULD reduce consumption to the
        maximum extent possible while the price is greater than this
        threshold.
    :ivar rate_component_link:
    :ivar price_response_cfg_r2_3:
    """

    class Meta:
        name = "PriceResponseCfg"

    model_config = ConfigDict(defer_build=True)
    consume_threshold: int = field(
        metadata={
            "name": "consumeThreshold",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    max_reduction_threshold: int = field(
        metadata={
            "name": "maxReductionThreshold",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    rate_component_link: RateComponentLink = field(
        metadata={
            "name": "RateComponentLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    price_response_cfg_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PriceResponseCfg_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class PriceResponse1(Response1):
    """
    A response related to a price message.
    """

    class Meta:
        name = "PriceResponse"

    model_config = ConfigDict(defer_build=True)
    price_response_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PriceResponse_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ProjectionReadingListLink(ListLink):
    """
    SHALL contain a Link to a List of ProjectionReading instances.
    """

    model_config = ConfigDict(defer_build=True)
    projection_reading_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ProjectionReadingListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ProxiedDeviceListLink(ListLink):
    """
    SHALL contain a Link to a List of Proxied EndDevice instances.
    """

    model_config = ConfigDict(defer_build=True)
    proxied_device_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ProxiedDeviceListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RplinstanceListLink(ListLink):
    """
    SHALL contain a Link to a List of RPLInterface instances.
    """

    class Meta:
        name = "RPLInstanceListLink"

    model_config = ConfigDict(defer_build=True)
    rplinstance_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RPLInstanceListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RplsourceRoutes(RplsourceRoutes1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "RPLSourceRoutes"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class RplsourceRoutesListLink(ListLink):
    """
    SHALL contain a Link to a List of RPLSourceRoutes instances.
    """

    class Meta:
        name = "RPLSourceRoutesListLink"

    model_config = ConfigDict(defer_build=True)
    rplsource_routes_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RPLSourceRoutesListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RplsourceRoutesList1(List):
    """
    List or RPL source routes if the hosting device is the DODAGroot.
    """

    class Meta:
        name = "RPLSourceRoutesList"

    model_config = ConfigDict(defer_build=True)
    rplsource_routes: list[RplsourceRoutes1] = field(
        default_factory=list,
        metadata={
            "name": "RPLSourceRoutes",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rplsource_routes_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RPLSourceRoutesList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RateComponentListLink(ListLink):
    """
    SHALL contain a Link to a List of RateComponent instances.
    """

    model_config = ConfigDict(defer_build=True)
    rate_component_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RateComponentListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ReadingListLink(ListLink):
    """
    SHALL contain a Link to a List of Reading instances.
    """

    model_config = ConfigDict(defer_build=True)
    reading_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReadingListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ReadingSetBase(IdentifiedObject):
    """
    A set of Readings of the ReadingType indicated by the parent
    MeterReading.

    ReadingBase is abstract, used to define the elements common to
    ReadingSet and IntervalBlock.

    :ivar time_period: Specifies the time range during which the
        contained readings were taken.
    :ivar reading_set_base_r2_3:
    """

    model_config = ConfigDict(defer_build=True)
    time_period: DateTimeInterval = field(
        metadata={
            "name": "timePeriod",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    reading_set_base_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReadingSetBase_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ReadingSetListLink(ListLink):
    """
    SHALL contain a Link to a List of ReadingSet instances.
    """

    model_config = ConfigDict(defer_build=True)
    reading_set_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReadingSetListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ReadingType(ReadingType1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Reading1(ReadingBase):
    """
    Specific value measured by a meter or other asset.

    :ivar local_id: The local identifier for this reading within the
        reading set. localIDs are assigned in order of creation time.
        For interval data, this value SHALL increase with each interval
        time, and for block/tier readings, localID SHALL not be
        specified.
    :ivar reading_r2_3:
    :ivar subscribable: Indicates whether or not subscriptions are
        supported for this resource, and whether or not conditional
        (thresholds) are supported. If not specified, is "not
        subscribable" (0).
    """

    class Meta:
        name = "Reading"

    model_config = ConfigDict(defer_build=True)
    local_id: None | bytes = field(
        default=None,
        metadata={
            "name": "localID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 2,
            "format": "base16",
        },
    )
    reading_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Reading_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    subscribable: int = field(
        default=0,
        metadata={
            "type": "Attribute",
        },
    )


class Registration(Registration1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class RespondableIdentifiedObject(RespondableResource):
    """
    An IdentifiedObject to which a Response can be requested.

    :ivar m_rid: The global identifier of the object.
    :ivar description: The description is a human readable text
        describing or naming the object.
    :ivar version: Contains the version number of the object. See the
        type definition for details.
    :ivar respondable_identified_object_r2_3:
    """

    model_config = ConfigDict(defer_build=True)
    m_rid: MRidtype = field(
        metadata={
            "name": "mRID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    description: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    version: None | VersionType = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    respondable_identified_object_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RespondableIdentifiedObject_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RespondableSubscribableIdentifiedObject(RespondableResource):
    """
    An IdentifiedObject to which a Response can be requested.

    :ivar m_rid: The global identifier of the object.
    :ivar description: The description is a human readable text
        describing or naming the object.
    :ivar version: Contains the version number of the object. See the
        type definition for details.
    :ivar respondable_subscribable_identified_object_r2_3:
    :ivar subscribable: Indicates whether or not subscriptions are
        supported for this resource, and whether or not conditional
        (thresholds) are supported. If not specified, is "not
        subscribable" (0).
    """

    model_config = ConfigDict(defer_build=True)
    m_rid: MRidtype = field(
        metadata={
            "name": "mRID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    description: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    version: None | VersionType = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    respondable_subscribable_identified_object_r2_3: None | Revision23Type = (
        field(
            default=None,
            metadata={
                "name": "RespondableSubscribableIdentifiedObject_r2_3",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    subscribable: int = field(
        default=0,
        metadata={
            "type": "Attribute",
        },
    )


class Response(Response1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ResponseListLink(ListLink):
    """
    SHALL contain a Link to a List of Response instances.
    """

    model_config = ConfigDict(defer_build=True)
    response_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ResponseListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ResponseList1(List):
    """
    A List element to hold Response objects.
    """

    class Meta:
        name = "ResponseList"

    model_config = ConfigDict(defer_build=True)
    response: list[Response1] = field(
        default_factory=list,
        metadata={
            "name": "Response",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    response_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ResponseList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ResponseSetListLink(ListLink):
    """
    SHALL contain a Link to a List of ResponseSet instances.
    """

    model_config = ConfigDict(defer_build=True)
    response_set_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ResponseSetListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ServiceSupplier1(IdentifiedObject):
    """
    Organisation that provides services to Customers.

    :ivar email: E-mail address for this service supplier.
    :ivar phone: Human-readable phone number for this service supplier.
    :ivar provider_id: Contains the IANA PEN for the commodity provider.
    :ivar web: Website URI address for this service supplier.
    :ivar service_supplier_r2_3:
    """

    class Meta:
        name = "ServiceSupplier"

    model_config = ConfigDict(defer_build=True)
    email: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    phone: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 20,
        },
    )
    provider_id: None | int = field(
        default=None,
        metadata={
            "name": "providerID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    web: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 42,
        },
    )
    service_supplier_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ServiceSupplier_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class SubscribableIdentifiedObject(SubscribableResource):
    """
    An IdentifiedObject to which a Subscription can be requested.

    :ivar m_rid: The global identifier of the object.
    :ivar description: The description is a human readable text
        describing or naming the object.
    :ivar version: Contains the version number of the object. See the
        type definition for details.
    :ivar subscribable_identified_object_r2_3:
    """

    model_config = ConfigDict(defer_build=True)
    m_rid: MRidtype = field(
        metadata={
            "name": "mRID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    description: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    version: None | VersionType = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    subscribable_identified_object_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SubscribableIdentifiedObject_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class SubscribableList(SubscribableResource):
    """
    A List to which a Subscription can be requested.

    :ivar subscribable_list_r2_3:
    :ivar all: The number specifying "all" of the items in the list
        before any query string parameters are applied. Required on GET,
        ignored otherwise.
    :ivar results: Indicates the number of items in this page of
        results.
    """

    model_config = ConfigDict(defer_build=True)
    subscribable_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SubscribableList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    all: int = field(
        metadata={
            "type": "Attribute",
            "required": True,
        }
    )
    results: int = field(
        metadata={
            "type": "Attribute",
            "required": True,
        }
    )


class SubscriptionListLink(ListLink):
    """
    SHALL contain a Link to a List of Subscription instances.
    """

    model_config = ConfigDict(defer_build=True)
    subscription_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SubscriptionListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Subscription1(SubscriptionBase):
    """
    Holds the information related to a client subscription to receive
    updates to a resource automatically.

    :ivar condition:
    :ivar encoding: 0 - application/sep+xml 1 - application/sep-exi
        2-255 - reserved
    :ivar level: Contains the preferred schema and extensibility level
        indication such as "+S2"
    :ivar limit: This element is used to indicate the maximum number of
        list items that should be included in a notification when the
        subscribed resource changes. This limit is meant to be
        functionally equivalent to the ‘limit’ query string parameter,
        but applies to both list resources as well as other resources.
        For list resources, if a limit of ‘0’ is specified, then
        notifications SHALL contain a list resource with results=’0’
        (equivalent to a simple change notification).  For list
        resources, if a limit greater than ‘0’ is specified, then
        notifications SHALL contain a list resource with results equal
        to the limit specified (or less, should the list contain fewer
        items than the limit specified or should the server be unable to
        provide the requested number of items for any reason) and follow
        the same rules for list resources (e.g., ordering).  For non-
        list resources, if a limit of ‘0’ is specified, then
        notifications SHALL NOT contain a resource representation
        (equivalent to a simple change notification).  For non-list
        resources, if a limit greater than ‘0’ is specified, then
        notifications SHALL contain the representation of the changed
        resource.
    :ivar notification_uri: The resource to which to post the
        notifications about the requested subscribed resource. Because
        this URI will exist on a server other than the one being POSTed
        to, this attribute SHALL be a fully-qualified absolute URI, not
        a relative reference.
    :ivar subscription_r2_3:
    """

    class Meta:
        name = "Subscription"

    model_config = ConfigDict(defer_build=True)
    condition: None | Condition = field(
        default=None,
        metadata={
            "name": "Condition",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    encoding: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    level: str = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 16,
        }
    )
    limit: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    notification_uri: str = field(
        metadata={
            "name": "notificationURI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    subscription_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Subscription_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class SupplyInterruptionOverride(SupplyInterruptionOverride1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class SupplyInterruptionOverrideListLink(ListLink):
    """
    SHALL contain a Link to a List of SupplyInterruptionOverride instances.
    """

    model_config = ConfigDict(defer_build=True)
    supply_interruption_override_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SupplyInterruptionOverrideListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class SupplyInterruptionOverrideList1(List):
    """
    A List element to hold SupplyInterruptionOverride objects.
    """

    class Meta:
        name = "SupplyInterruptionOverrideList"

    model_config = ConfigDict(defer_build=True)
    supply_interruption_override: list[SupplyInterruptionOverride1] = field(
        default_factory=list,
        metadata={
            "name": "SupplyInterruptionOverride",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    supply_interruption_override_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SupplyInterruptionOverrideList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class SupportedLocale(SupportedLocale1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class SupportedLocaleListLink(ListLink):
    """
    SHALL contain a Link to a List of SupportedLocale instances.
    """

    model_config = ConfigDict(defer_build=True)
    supported_locale_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SupportedLocaleListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class SupportedLocaleList1(List):
    """
    A List element to hold SupportedLocale objects.
    """

    class Meta:
        name = "SupportedLocaleList"

    model_config = ConfigDict(defer_build=True)
    supported_locale: list[SupportedLocale1] = field(
        default_factory=list,
        metadata={
            "name": "SupportedLocale",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    supported_locale_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SupportedLocaleList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class TargetReadingListLink(ListLink):
    """
    SHALL contain a Link to a List of TargetReading instances.
    """

    model_config = ConfigDict(defer_build=True)
    target_reading_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TargetReadingListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class TariffProfileListLink(ListLink):
    """
    SHALL contain a Link to a List of TariffProfile instances.
    """

    model_config = ConfigDict(defer_build=True)
    tariff_profile_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TariffProfileListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class TextMessageListLink(ListLink):
    """
    SHALL contain a Link to a List of TextMessage instances.
    """

    model_config = ConfigDict(defer_build=True)
    text_message_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TextMessageListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class TextResponse1(Response1):
    """
    A response to a text message.
    """

    class Meta:
        name = "TextResponse"

    model_config = ConfigDict(defer_build=True)
    text_response_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TextResponse_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Time(Time1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TimeTariffIntervalListLink(ListLink):
    """
    SHALL contain a Link to a List of TimeTariffInterval instances.
    """

    model_config = ConfigDict(defer_build=True)
    time_tariff_interval_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TimeTariffIntervalListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class UsagePointBase(IdentifiedObject):
    """
    Logical point on a network at which consumption or production is either
    physically measured (e.g. metered) or estimated (e.g. unmetered street
    lights).

    A container for associating ReadingType, Readings and ReadingSets.

    :ivar role_flags: Specifies the roles that apply to the usage point.
    :ivar service_category_kind: The kind of service provided by this
        usage point.
    :ivar status: Specifies the current status of the service at this
        usage point. 0 = off 1 = on
    :ivar usage_point_base_r2_3:
    """

    model_config = ConfigDict(defer_build=True)
    role_flags: RoleFlagsType = field(
        metadata={
            "name": "roleFlags",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    service_category_kind: ServiceKind = field(
        metadata={
            "name": "serviceCategoryKind",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    status: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    usage_point_base_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UsagePointBase_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class UsagePointListLink(ListLink):
    """
    SHALL contain a Link to a List of UsagePoint instances.
    """

    model_config = ConfigDict(defer_build=True)
    usage_point_list_link_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UsagePointListLink_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class AbstractDevice(SubscribableResource):
    """
    Abstract asset container for devices.

    Contains information about a device/entity.

    :ivar aggregated_device_list_link:
    :ivar aggregation_priority_link:
    :ivar configuration_link:
    :ivar derlist_link:
    :ivar device_category: This field is for use in devices that can
        adjust energy usage (e.g., demand response, distributed energy
        resources).  For devices that do not respond to
        EndDeviceControls or DERControls (for instance, an ESI), this
        field should not have any bits set.
    :ivar device_information_link:
    :ivar device_status_link:
    :ivar distribution: When representing an aggregation of multiple
        devices, specifies how controls SHALL be distributed among
        members of the aggregation. If not specified, a default of 0
        (Not Applicable / Unspecified) is used.
    :ivar file_status_link:
    :ivar ipinterface_list_link:
    :ivar l_fdi: Long form of device identifier. See the Security
        section for additional details. Beginning with IEEE 2030.5-2023,
        this field SHALL be included. Note that this field is optional
        in revisions of IEEE 2030.5 prior to IEEE 2030.5-2023.
    :ivar load_shed_availability_list_link:
    :ivar log_event_list_link:
    :ivar phase: Indicates the electrical phase(s) on which this entity
        is connected.
    :ivar power_status_link:
    :ivar s_fdi: Short form of device identifier, WITH the checksum
        digit. See the Security section for additional details.
    :ivar abstract_device_r2_3:
    """

    model_config = ConfigDict(defer_build=True)
    aggregated_device_list_link: None | AggregatedDeviceListLink = field(
        default=None,
        metadata={
            "name": "AggregatedDeviceListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    aggregation_priority_link: None | AggregationPriorityLink = field(
        default=None,
        metadata={
            "name": "AggregationPriorityLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    configuration_link: None | ConfigurationLink = field(
        default=None,
        metadata={
            "name": "ConfigurationLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    derlist_link: None | DerlistLink = field(
        default=None,
        metadata={
            "name": "DERListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    device_category: None | DeviceCategoryType = field(
        default=None,
        metadata={
            "name": "deviceCategory",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    device_information_link: None | DeviceInformationLink = field(
        default=None,
        metadata={
            "name": "DeviceInformationLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    device_status_link: None | DeviceStatusLink = field(
        default=None,
        metadata={
            "name": "DeviceStatusLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    distribution: None | AggregationDistributionType = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    file_status_link: None | FileStatusLink = field(
        default=None,
        metadata={
            "name": "FileStatusLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    ipinterface_list_link: None | IpinterfaceListLink = field(
        default=None,
        metadata={
            "name": "IPInterfaceListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    l_fdi: None | bytes = field(
        default=None,
        metadata={
            "name": "lFDI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 20,
            "format": "base16",
        },
    )
    load_shed_availability_list_link: None | LoadShedAvailabilityListLink = (
        field(
            default=None,
            metadata={
                "name": "LoadShedAvailabilityListLink",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    log_event_list_link: None | LogEventListLink = field(
        default=None,
        metadata={
            "name": "LogEventListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    phase: None | PhaseCode = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    power_status_link: None | PowerStatusLink = field(
        default=None,
        metadata={
            "name": "PowerStatusLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    s_fdi: Sfditype = field(
        metadata={
            "name": "sFDI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    abstract_device_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AbstractDevice_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class AccountBalance(AccountBalance1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class AggregatedDeviceList1(SubscribableList):
    """
    A List element to hold AggregatedDevice objects.
    """

    class Meta:
        name = "AggregatedDeviceList"

    model_config = ConfigDict(defer_build=True)
    aggregated_device: list[AggregatedDevice1] = field(
        default_factory=list,
        metadata={
            "name": "AggregatedDevice",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    aggregated_device_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "AggregatedDeviceList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class AggregationPriority(AggregationPriority1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class BillingMeterReadingBase(MeterReadingBase):
    """
    Contains historical, target, and projection readings of various types,
    possibly associated with charges.
    """

    model_config = ConfigDict(defer_build=True)
    billing_reading_set_list_link: None | BillingReadingSetListLink = field(
        default=None,
        metadata={
            "name": "BillingReadingSetListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_type_link: None | ReadingTypeLink = field(
        default=None,
        metadata={
            "name": "ReadingTypeLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    billing_meter_reading_base_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "BillingMeterReadingBase_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class BillingPeriodList1(SubscribableList):
    """
    A List element to hold BillingPeriod objects.
    """

    class Meta:
        name = "BillingPeriodList"

    model_config = ConfigDict(defer_build=True)
    billing_period: list[BillingPeriod1] = field(
        default_factory=list,
        metadata={
            "name": "BillingPeriod",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    billing_period_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "BillingPeriodList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class BillingReading(BillingReading1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class BillingReadingList1(List):
    """
    A List element to hold BillingReading objects.
    """

    class Meta:
        name = "BillingReadingList"

    model_config = ConfigDict(defer_build=True)
    billing_reading: list[BillingReading1] = field(
        default_factory=list,
        metadata={
            "name": "BillingReading",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    billing_reading_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "BillingReadingList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class BillingReadingSet1(ReadingSetBase):
    """
    Time sequence of readings of the same reading type.
    """

    class Meta:
        name = "BillingReadingSet"

    model_config = ConfigDict(defer_build=True)
    billing_reading_list_link: None | BillingReadingListLink = field(
        default=None,
        metadata={
            "name": "BillingReadingListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    billing_reading_set_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "BillingReadingSet_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Configuration1(SubscribableResource):
    """
    This resource contains various settings to control the operation of the
    device.

    :ivar current_locale: [RFC 5646] identifier of the language-region
        currently in use.
    :ivar power_configuration:
    :ivar price_response_cfg_list_link:
    :ivar time_configuration:
    :ivar user_device_name: User assigned, convenience name used for
        network browsing displays, etc.  Example "My Thermostat"
    :ivar configuration_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "Configuration"

    model_config = ConfigDict(defer_build=True)
    current_locale: LocaleType = field(
        metadata={
            "name": "currentLocale",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    power_configuration: None | PowerConfiguration = field(
        default=None,
        metadata={
            "name": "PowerConfiguration",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    price_response_cfg_list_link: None | PriceResponseCfgListLink = field(
        default=None,
        metadata={
            "name": "PriceResponseCfgListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    time_configuration: None | TimeConfiguration = field(
        default=None,
        metadata={
            "name": "TimeConfiguration",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    user_device_name: str = field(
        metadata={
            "name": "userDeviceName",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 32,
        }
    )
    configuration_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Configuration_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class ConsumptionTariffIntervalList(ConsumptionTariffIntervalList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class CreditRegister(CreditRegister1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class CreditRegisterList1(List):
    """
    A List element to hold CreditRegister objects.
    """

    class Meta:
        name = "CreditRegisterList"

    model_config = ConfigDict(defer_build=True)
    credit_register: list[CreditRegister1] = field(
        default_factory=list,
        metadata={
            "name": "CreditRegister",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    credit_register_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CreditRegisterList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class CustomerAccount1(IdentifiedObject):
    """
    Assignment of a group of products and services purchased by the
    Customer through a CustomerAgreement, used as a mechanism for customer
    billing and payment.

    It contains common information from the various types of
    CustomerAgreements to create billings (invoices) for a Customer and
    receive payment.

    :ivar currency: The ISO 4217 code indicating the currency applicable
        to the bill amounts in the summary. See list at
        http://www.unece.org/cefact/recommendations/rec09/rec09_ecetrd203.pdf
    :ivar customer_account: The account number for the customer (if
        applicable).
    :ivar customer_agreement_list_link:
    :ivar customer_name: The name of the customer.
    :ivar price_power_of_ten_multiplier: Indicates the power of ten
        multiplier for the prices in this function set.
    :ivar service_supplier_link:
    :ivar customer_account_r2_3:
    """

    class Meta:
        name = "CustomerAccount"

    model_config = ConfigDict(defer_build=True)
    currency: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    customer_account: None | str = field(
        default=None,
        metadata={
            "name": "customerAccount",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 42,
        },
    )
    customer_agreement_list_link: None | CustomerAgreementListLink = field(
        default=None,
        metadata={
            "name": "CustomerAgreementListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    customer_name: None | str = field(
        default=None,
        metadata={
            "name": "customerName",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 42,
        },
    )
    price_power_of_ten_multiplier: PowerOfTenMultiplierType = field(
        metadata={
            "name": "pricePowerOfTenMultiplier",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    service_supplier_link: None | ServiceSupplierLink = field(
        default=None,
        metadata={
            "name": "ServiceSupplierLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    customer_account_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CustomerAccount_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class CustomerAgreement1(IdentifiedObject):
    """
    Agreement between the customer and the service supplier to pay for
    service at a specific service location.

    It records certain billing information about the type of service
    provided at the service location and is used during charge creation to
    determine the type of service.

    :ivar active_billing_period_list_link:
    :ivar active_projection_reading_list_link:
    :ivar active_target_reading_list_link:
    :ivar billing_period_list_link:
    :ivar historical_reading_list_link:
    :ivar prepayment_link:
    :ivar projection_reading_list_link:
    :ivar service_account: The account number of the service account (if
        applicable).
    :ivar service_location: The address or textual description of the
        service location.
    :ivar target_reading_list_link:
    :ivar tariff_profile_link:
    :ivar usage_point_link:
    :ivar customer_agreement_r2_3:
    """

    class Meta:
        name = "CustomerAgreement"

    model_config = ConfigDict(defer_build=True)
    active_billing_period_list_link: None | ActiveBillingPeriodListLink = (
        field(
            default=None,
            metadata={
                "name": "ActiveBillingPeriodListLink",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    active_projection_reading_list_link: (
        None | ActiveProjectionReadingListLink
    ) = field(
        default=None,
        metadata={
            "name": "ActiveProjectionReadingListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    active_target_reading_list_link: None | ActiveTargetReadingListLink = (
        field(
            default=None,
            metadata={
                "name": "ActiveTargetReadingListLink",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    billing_period_list_link: None | BillingPeriodListLink = field(
        default=None,
        metadata={
            "name": "BillingPeriodListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    historical_reading_list_link: None | HistoricalReadingListLink = field(
        default=None,
        metadata={
            "name": "HistoricalReadingListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    prepayment_link: None | PrepaymentLink = field(
        default=None,
        metadata={
            "name": "PrepaymentLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    projection_reading_list_link: None | ProjectionReadingListLink = field(
        default=None,
        metadata={
            "name": "ProjectionReadingListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    service_account: None | str = field(
        default=None,
        metadata={
            "name": "serviceAccount",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 42,
        },
    )
    service_location: None | str = field(
        default=None,
        metadata={
            "name": "serviceLocation",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 42,
        },
    )
    target_reading_list_link: None | TargetReadingListLink = field(
        default=None,
        metadata={
            "name": "TargetReadingListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    tariff_profile_link: None | TariffProfileLink = field(
        default=None,
        metadata={
            "name": "TariffProfileLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    usage_point_link: None | UsagePointLink = field(
        default=None,
        metadata={
            "name": "UsagePointLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    customer_agreement_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CustomerAgreement_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Deravailability(Deravailability1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERAvailability"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Dercomponent1(DercomponentBase):
    """
    Contains links to DER Component resources.

    Represents a component (e.g., storage in a solar+storage DER) of the
    parent DER.

    :ivar l_fdi: The LFDI of the DERComponent.
    :ivar dercomponent_r2_3:
    """

    class Meta:
        name = "DERComponent"

    model_config = ConfigDict(defer_build=True)
    l_fdi: bytes = field(
        metadata={
            "name": "lFDI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 20,
            "format": "base16",
        }
    )
    dercomponent_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERComponent_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DercontrolResponse(DercontrolResponse1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERControlResponse"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Dercurve(Dercurve1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERCurve"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DercurveControlType(Dercurve1):
    """
    :ivar dercurve_control_type_r2_3:
    :ivar disabled: If set to true (disabled), this DERControl Mode is
        disabled. A disabled DERControl Mode follows the rules and
        guidelines as if the DERControl Mode were not disabled. If not
        specified, a default of false (enabled) is used. For backward
        compatibility reasons a value SHALL be specified even when
        disabled is set to true. As this attribute was introduced in
        IEEE 2030.5-2023, devices that are compliant with previous
        revisions will ignore this attribute and use the specified
        value. Thus, the specified value can be thought of as a fallback
        for older devices.
    """

    class Meta:
        name = "DERCurveControlType"

    model_config = ConfigDict(defer_build=True)
    dercurve_control_type_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERCurveControlType_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    disabled: bool = field(
        default=False,
        metadata={
            "type": "Attribute",
        },
    )


class DercurveList1(List):
    """
    A List element to hold DERCurve objects.
    """

    class Meta:
        name = "DERCurveList"

    model_config = ConfigDict(defer_build=True)
    dercurve: list[Dercurve1] = field(
        default_factory=list,
        metadata={
            "name": "DERCurve",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercurve_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERCurveList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Derprogram1(SubscribableIdentifiedObject):
    """
    Distributed Energy Resource program.

    :ivar active_dercontrol_list_link:
    :ivar default_dercontrol_link:
    :ivar dercontrol_list_link:
    :ivar dercurve_list_link:
    :ivar primacy: Indicates the relative primacy of the provider of
        this Program.
    :ivar derprogram_r2_3:
    """

    class Meta:
        name = "DERProgram"

    model_config = ConfigDict(defer_build=True)
    active_dercontrol_list_link: None | ActiveDercontrolListLink = field(
        default=None,
        metadata={
            "name": "ActiveDERControlListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    default_dercontrol_link: None | DefaultDercontrolLink = field(
        default=None,
        metadata={
            "name": "DefaultDERControlLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercontrol_list_link: None | DercontrolListLink = field(
        default=None,
        metadata={
            "name": "DERControlListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercurve_list_link: None | DercurveListLink = field(
        default=None,
        metadata={
            "name": "DERCurveListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    primacy: PrimacyType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    derprogram_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERProgram_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Dersettings(Dersettings1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERSettings"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Derstatus(Derstatus1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERStatus"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Der1(SubscribableResource):
    """
    Contains links to DER resources.
    """

    class Meta:
        name = "DER"

    model_config = ConfigDict(defer_build=True)
    associated_derprogram_list_link: None | AssociatedDerprogramListLink = (
        field(
            default=None,
            metadata={
                "name": "AssociatedDERProgramListLink",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    associated_usage_point_link: None | AssociatedUsagePointLink = field(
        default=None,
        metadata={
            "name": "AssociatedUsagePointLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    current_dercontrols_link: None | CurrentDercontrolsLink = field(
        default=None,
        metadata={
            "name": "CurrentDERControlsLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    current_derprogram_link: None | CurrentDerprogramLink = field(
        default=None,
        metadata={
            "name": "CurrentDERProgramLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    deravailability_link: None | DeravailabilityLink = field(
        default=None,
        metadata={
            "name": "DERAvailabilityLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercapability_link: None | DercapabilityLink = field(
        default=None,
        metadata={
            "name": "DERCapabilityLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercomponent_list_link: None | DercomponentListLink = field(
        default=None,
        metadata={
            "name": "DERComponentListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dersettings_link: None | DersettingsLink = field(
        default=None,
        metadata={
            "name": "DERSettingsLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    derstatus_link: None | DerstatusLink = field(
        default=None,
        metadata={
            "name": "DERStatusLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    der_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DER_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DefaultDercontrolResponse(DefaultDercontrolResponse1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DefaultDERControlResponse"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DefaultDercontrol1(RespondableSubscribableIdentifiedObject):
    """
    Contains DERControl Mode information to be used if no active DERControl
    is found.

    :ivar dercontrol_base:
    :ivar set_esdelay: Enter service delay, in hundredths of a second.
        When present, this value SHALL update the value of the
        corresponding setting (DERSettings::setESDelay).
    :ivar set_eshigh_freq: Enter service frequency high. Specified in
        hundredths of Hz. When present, this value SHALL update the
        value of the corresponding setting (DERSettings::setESHighFreq).
    :ivar set_eshigh_volt: Enter service voltage high. Specified as an
        effective percent voltage, defined as (100% * (locally measured
        voltage - setVRefOfs) / setVRef), in hundredths of a percent.
        When present, this value SHALL update the value of the
        corresponding setting (DERSettings::setESHighVolt).
    :ivar set_eslow_freq: Enter service frequency low. Specified in
        hundredths of Hz. When present, this value SHALL update the
        value of the corresponding setting (DERSettings::setESLowFreq).
    :ivar set_eslow_volt: Enter service voltage low. Specified as an
        effective percent voltage, defined as (100% * (locally measured
        voltage - setVRefOfs) / setVRef), in hundredths of a percent.
        When present, this value SHALL update the value of the
        corresponding setting (DERSettings::setESLowVolt).
    :ivar set_esramp_tms: Enter service ramp time, in hundredths of a
        second. When present, this value SHALL update the value of the
        corresponding setting (DERSettings::setESRampTms).
    :ivar set_esrandom_delay: Enter service randomized delay, in
        hundredths of a second. When present, this value SHALL update
        the value of the corresponding setting
        (DERSettings::setESRandomDelay).
    :ivar set_grad_w: Set default rate of change (ramp rate) of active
        power output due to command or internal action, defined in
        %setWMax / second.  Resolution is in hundredths of a
        percent/second. A value of 0 means there is no limit.
        Interpreted as a percentage change in output capability limit
        per second when used as a default ramp rate. When present, this
        value SHALL update the value of the corresponding setting
        (DERSettings::setGradW).
    :ivar set_soft_grad_w: Set soft-start rate of change (soft-start
        ramp rate) of active power output due to command or internal
        action, defined in %setWMax / second.  Resolution is in
        hundredths of a percent/second. A value of 0 means there is no
        limit. Interpreted as a percentage change in output capability
        limit per second when used as a ramp rate. When present, this
        value SHALL update the value of the corresponding setting
        (DERSettings::setSoftGradW).
    :ivar updated_time: Specifies the time at which the
        DefaultDERControl was last updated. Provides an additional
        mechanism to mRID and version for clients to determine when a
        DefaultDERControl has been updated.
    :ivar default_dercontrol_r2_3:
    """

    class Meta:
        name = "DefaultDERControl"

    model_config = ConfigDict(defer_build=True)
    dercontrol_base: DercontrolBase = field(
        metadata={
            "name": "DERControlBase",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    set_esdelay: None | int = field(
        default=None,
        metadata={
            "name": "setESDelay",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_eshigh_freq: None | int = field(
        default=None,
        metadata={
            "name": "setESHighFreq",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_eshigh_volt: None | int = field(
        default=None,
        metadata={
            "name": "setESHighVolt",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_eslow_freq: None | int = field(
        default=None,
        metadata={
            "name": "setESLowFreq",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_eslow_volt: None | int = field(
        default=None,
        metadata={
            "name": "setESLowVolt",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_esramp_tms: None | int = field(
        default=None,
        metadata={
            "name": "setESRampTms",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_esrandom_delay: None | int = field(
        default=None,
        metadata={
            "name": "setESRandomDelay",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_grad_w: None | int = field(
        default=None,
        metadata={
            "name": "setGradW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_soft_grad_w: None | int = field(
        default=None,
        metadata={
            "name": "setSoftGradW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    updated_time: None | TimeType = field(
        default=None,
        metadata={
            "name": "updatedTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    default_dercontrol_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DefaultDERControl_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DemandResponseProgram1(IdentifiedObject):
    """
    Demand response program.

    :ivar active_end_device_control_list_link:
    :ivar availability_update_percent_change_threshold: This attribute
        allows program providers to specify the requested granularity of
        updates to LoadShedAvailability sheddablePercent. If not
        present, or set to 0, then updates to LoadShedAvailability SHALL
        NOT be provided. If present and greater than zero, then clients
        SHALL provide their LoadShedAvailability if it has not
        previously been provided, and thereafter if the difference
        between the previously provided value and the current value of
        LoadShedAvailability sheddablePercent is greater than
        availabilityUpdatePercentChangeThreshold.
    :ivar availability_update_power_change_threshold: This attribute
        allows program providers to specify the requested granularity of
        updates to LoadShedAvailability sheddablePower. If not present,
        then updates to LoadShedAvailability SHALL NOT be provided. If
        present and greater than zero, then clients SHALL provide their
        LoadShedAvailability if it has not previously been provided, and
        thereafter if the difference between the previously provided
        value and the current value of LoadShedAvailability
        sheddablePower is greater than
        availabilityUpdatePowerChangeThreshold.
    :ivar end_device_control_list_link:
    :ivar primacy: Indicates the relative primacy of the provider of
        this program.
    :ivar demand_response_program_r2_3:
    """

    class Meta:
        name = "DemandResponseProgram"

    model_config = ConfigDict(defer_build=True)
    active_end_device_control_list_link: (
        None | ActiveEndDeviceControlListLink
    ) = field(
        default=None,
        metadata={
            "name": "ActiveEndDeviceControlListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    availability_update_percent_change_threshold: None | PerCent = field(
        default=None,
        metadata={
            "name": "availabilityUpdatePercentChangeThreshold",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    availability_update_power_change_threshold: None | ActivePower = field(
        default=None,
        metadata={
            "name": "availabilityUpdatePowerChangeThreshold",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    end_device_control_list_link: None | EndDeviceControlListLink = field(
        default=None,
        metadata={
            "name": "EndDeviceControlListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    primacy: PrimacyType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    demand_response_program_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DemandResponseProgram_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DeviceInformation1(Resource):
    """
    Contains identification and other information about the device that
    changes very infrequently, typically only when updates are applied, if
    ever.

    :ivar connection_point_id: Identification of the device's service
        provider connection (e.g., Australian National Meter
        Identifier).
    :ivar drlccapabilities:
    :ivar functions_implemented: Bitmap indicating the function sets
        used by the device as a client. 0 - Device Capability 1 - Self
        Device Resource 2 - End Device Resource 3 - Function Set
        Assignments 4 - Subscription/Notification Mechanism 5 - Response
        6 - Time 7 - Device Information 8 - Power Status 9 - Network
        Status 10 - Log Event 11 - Configuration Resource 12 - Software
        Download 13 - DRLC 14 - Metering 15 - Pricing 16 - Messaging 17
        - Billing 18 - Prepayment 19 - Flow Reservation 20 - DER Control
        21 - DER Info 22 - Metering Mirror 23 - Aggregated Device 24 -
        Proxied Device 25-63 - Reserved
    :ivar gps_location: GPS location of this device.
    :ivar l_fdi: Long form device identifier. See the Security section
        for full details.
    :ivar mf_date: Date/time of manufacture
    :ivar mf_hw_ver: Manufacturer hardware version
    :ivar mf_id: The manufacturer's IANA Enterprise Number.
    :ivar mf_info: Manufacturer dependent information related to the
        manufacture of this device
    :ivar mf_model: Manufacturer's model number
    :ivar mf_ser_num: Manufacturer assigned serial number
    :ivar primary_power: Primary source of power.
    :ivar secondary_power: Secondary source of power
    :ivar supported_locale_list_link:
    :ivar sw_act_time: Activation date/time of currently running
        software
    :ivar sw_ver: Currently running software version
    :ivar device_information_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "DeviceInformation"

    model_config = ConfigDict(defer_build=True)
    connection_point_id: None | str = field(
        default=None,
        metadata={
            "name": "connectionPointID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    drlccapabilities: None | Drlccapabilities = field(
        default=None,
        metadata={
            "name": "DRLCCapabilities",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    functions_implemented: None | bytes = field(
        default=None,
        metadata={
            "name": "functionsImplemented",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 8,
            "format": "base16",
        },
    )
    gps_location: None | GpslocationType = field(
        default=None,
        metadata={
            "name": "gpsLocation",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    l_fdi: bytes = field(
        metadata={
            "name": "lFDI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 20,
            "format": "base16",
        }
    )
    mf_date: TimeType = field(
        metadata={
            "name": "mfDate",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    mf_hw_ver: str = field(
        metadata={
            "name": "mfHwVer",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 32,
        }
    )
    mf_id: Pentype = field(
        metadata={
            "name": "mfID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    mf_info: None | str = field(
        default=None,
        metadata={
            "name": "mfInfo",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    mf_model: str = field(
        metadata={
            "name": "mfModel",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 32,
        }
    )
    mf_ser_num: str = field(
        metadata={
            "name": "mfSerNum",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 32,
        }
    )
    primary_power: PowerSourceType = field(
        metadata={
            "name": "primaryPower",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    secondary_power: PowerSourceType = field(
        metadata={
            "name": "secondaryPower",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    supported_locale_list_link: None | SupportedLocaleListLink = field(
        default=None,
        metadata={
            "name": "SupportedLocaleListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    sw_act_time: TimeType = field(
        metadata={
            "name": "swActTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    sw_ver: str = field(
        metadata={
            "name": "swVer",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 32,
        }
    )
    device_information_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DeviceInformation_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class DeviceStatus(DeviceStatus1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DrResponse(DrResponse1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Event(RespondableSubscribableIdentifiedObject):
    """
    An Event indicates information that applies to a particular period of
    time.

    Events SHALL be executed relative to the time of the server, as
    described in the Time function set section 11.1.

    :ivar creation_time: The time at which the Event was created.
    :ivar event_status:
    :ivar interval: The period during which the Event applies.
    :ivar event_r2_3:
    """

    model_config = ConfigDict(defer_build=True)
    creation_time: TimeType = field(
        metadata={
            "name": "creationTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    event_status: EventStatus = field(
        metadata={
            "name": "EventStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    interval: DateTimeInterval = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    event_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Event_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class FileList(FileList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class FileStatus(FileStatus1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class FlowReservationRequest(FlowReservationRequest1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class FlowReservationRequestList1(List):
    """
    A List element to hold FlowReservationRequest objects.

    :ivar flow_reservation_request:
    :ivar flow_reservation_request_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "FlowReservationRequestList"

    model_config = ConfigDict(defer_build=True)
    flow_reservation_request: list[FlowReservationRequest1] = field(
        default_factory=list,
        metadata={
            "name": "FlowReservationRequest",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    flow_reservation_request_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FlowReservationRequestList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class FlowReservationResponseResponse(FlowReservationResponseResponse1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class FunctionSetAssignmentsBase(Resource):
    """
    Defines a collection of function set instances that are to be used by
    one or more devices as indicated by the EndDevice object(s) of the
    server.
    """

    model_config = ConfigDict(defer_build=True)
    customer_account_list_link: None | CustomerAccountListLink = field(
        default=None,
        metadata={
            "name": "CustomerAccountListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    demand_response_program_list_link: None | DemandResponseProgramListLink = (
        field(
            default=None,
            metadata={
                "name": "DemandResponseProgramListLink",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    derprogram_list_link: None | DerprogramListLink = field(
        default=None,
        metadata={
            "name": "DERProgramListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    file_list_link: None | FileListLink = field(
        default=None,
        metadata={
            "name": "FileListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    messaging_program_list_link: None | MessagingProgramListLink = field(
        default=None,
        metadata={
            "name": "MessagingProgramListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    prepayment_list_link: None | PrepaymentListLink = field(
        default=None,
        metadata={
            "name": "PrepaymentListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    response_set_list_link: None | ResponseSetListLink = field(
        default=None,
        metadata={
            "name": "ResponseSetListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    tariff_profile_list_link: None | TariffProfileListLink = field(
        default=None,
        metadata={
            "name": "TariffProfileListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    time_link: None | TimeLink = field(
        default=None,
        metadata={
            "name": "TimeLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    usage_point_list_link: None | UsagePointListLink = field(
        default=None,
        metadata={
            "name": "UsagePointListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    function_set_assignments_base_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FunctionSetAssignmentsBase_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Ieee802154(BaseModel):
    """
    Contains 802.15.4 link layer specific attributes.

    :ivar capability_info: As defined by IEEE 802.15.4
    :ivar neighbor_list_link:
    :ivar short_address: As defined by IEEE 802.15.4
    :ivar ieee_802_15_4_r2_3:
    :ivar other_element:
    :ivar any_attributes:
    """

    class Meta:
        name = "IEEE_802_15_4"

    model_config = ConfigDict(defer_build=True)
    capability_info: bytes = field(
        metadata={
            "name": "capabilityInfo",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 1,
            "format": "base16",
        }
    )
    neighbor_list_link: None | NeighborListLink = field(
        default=None,
        metadata={
            "name": "NeighborListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    short_address: int = field(
        metadata={
            "name": "shortAddress",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    ieee_802_15_4_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "IEEE_802_15_4_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    other_element: list[object] = field(
        default_factory=list,
        metadata={
            "type": "Wildcard",
            "namespace": "##other",
        },
    )
    any_attributes: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "type": "Attributes",
            "namespace": "##any",
        },
    )


class Ipaddr1(Resource):
    """
    An Internet Protocol address object.

    :ivar address: An IP address value.
    :ivar rplinstance_list_link:
    :ivar ipaddr_r2_3:
    """

    class Meta:
        name = "IPAddr"

    model_config = ConfigDict(defer_build=True)
    address: bytes = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 16,
            "format": "base16",
        }
    )
    rplinstance_list_link: None | RplinstanceListLink = field(
        default=None,
        metadata={
            "name": "RPLInstanceListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    ipaddr_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "IPAddr_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Ipinterface1(Resource):
    """
    Specific IPInterface resource.

    This resource may be thought of as network status information for a
    specific network (IP) layer interface.

    :ivar if_descr: Use rules from [RFC 2863].
    :ivar if_high_speed: Use rules from [RFC 2863].
    :ivar if_in_broadcast_pkts: Use rules from [RFC 2863].
    :ivar if_index: Use rules from [RFC 2863].
    :ivar if_in_discards: Use rules from [RFC 2863]. Can be thought of
        as Input Datagrams Discarded.
    :ivar if_in_errors: Use rules from [RFC 2863].
    :ivar if_in_multicast_pkts: Use rules from [RFC 2863]. Can be
        thought of as Multicast Datagrams Received.
    :ivar if_in_octets: Use rules from [RFC 2863]. Can be thought of as
        Bytes Received.
    :ivar if_in_ucast_pkts: Use rules from [RFC 2863]. Can be thought of
        as Datagrams Received.
    :ivar if_in_unknown_protos: Use rules from [RFC 2863]. Can be
        thought of as Datagrams with Unknown Protocol Received.
    :ivar if_mtu: Use rules from [RFC 2863].
    :ivar if_name: Use rules from [RFC 2863].
    :ivar if_oper_status: Use rules and assignments from [RFC 2863].
    :ivar if_out_broadcast_pkts: Use rules from [RFC 2863]. Can be
        thought of as Broadcast Datagrams Sent.
    :ivar if_out_discards: Use rules from [RFC 2863]. Can be thought of
        as Output Datagrams Discarded.
    :ivar if_out_errors: Use rules from [RFC 2863].
    :ivar if_out_multicast_pkts: Use rules from [RFC 2863]. Can be
        thought of as Multicast Datagrams Sent.
    :ivar if_out_octets: Use rules from [RFC 2863]. Can be thought of as
        Bytes Sent.
    :ivar if_out_ucast_pkts: Use rules from [RFC 2863]. Can be thought
        of as Datagrams Sent.
    :ivar if_promiscuous_mode: Use rules from [RFC 2863].
    :ivar if_speed: Use rules from [RFC 2863].
    :ivar if_type: Use rules and assignments from [RFC 2863].
    :ivar ipaddr_list_link:
    :ivar last_reset_time: Similar to ifLastChange in [RFC 2863].
    :ivar last_updated_time: The date/time of the reported status.
    :ivar llinterface_list_link:
    :ivar ipinterface_r2_3:
    """

    class Meta:
        name = "IPInterface"

    model_config = ConfigDict(defer_build=True)
    if_descr: None | str = field(
        default=None,
        metadata={
            "name": "ifDescr",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 192,
        },
    )
    if_high_speed: None | int = field(
        default=None,
        metadata={
            "name": "ifHighSpeed",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_in_broadcast_pkts: None | int = field(
        default=None,
        metadata={
            "name": "ifInBroadcastPkts",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_index: None | int = field(
        default=None,
        metadata={
            "name": "ifIndex",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_in_discards: None | int = field(
        default=None,
        metadata={
            "name": "ifInDiscards",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_in_errors: None | int = field(
        default=None,
        metadata={
            "name": "ifInErrors",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_in_multicast_pkts: None | int = field(
        default=None,
        metadata={
            "name": "ifInMulticastPkts",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_in_octets: None | int = field(
        default=None,
        metadata={
            "name": "ifInOctets",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_in_ucast_pkts: None | int = field(
        default=None,
        metadata={
            "name": "ifInUcastPkts",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_in_unknown_protos: None | int = field(
        default=None,
        metadata={
            "name": "ifInUnknownProtos",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_mtu: None | int = field(
        default=None,
        metadata={
            "name": "ifMtu",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_name: None | str = field(
        default=None,
        metadata={
            "name": "ifName",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 16,
        },
    )
    if_oper_status: None | int = field(
        default=None,
        metadata={
            "name": "ifOperStatus",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_out_broadcast_pkts: None | int = field(
        default=None,
        metadata={
            "name": "ifOutBroadcastPkts",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_out_discards: None | int = field(
        default=None,
        metadata={
            "name": "ifOutDiscards",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_out_errors: None | int = field(
        default=None,
        metadata={
            "name": "ifOutErrors",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_out_multicast_pkts: None | int = field(
        default=None,
        metadata={
            "name": "ifOutMulticastPkts",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_out_octets: None | int = field(
        default=None,
        metadata={
            "name": "ifOutOctets",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_out_ucast_pkts: None | int = field(
        default=None,
        metadata={
            "name": "ifOutUcastPkts",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_promiscuous_mode: None | bool = field(
        default=None,
        metadata={
            "name": "ifPromiscuousMode",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_speed: None | int = field(
        default=None,
        metadata={
            "name": "ifSpeed",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    if_type: None | int = field(
        default=None,
        metadata={
            "name": "ifType",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    ipaddr_list_link: None | IpaddrListLink = field(
        default=None,
        metadata={
            "name": "IPAddrListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    last_reset_time: None | int = field(
        default=None,
        metadata={
            "name": "lastResetTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    last_updated_time: None | int = field(
        default=None,
        metadata={
            "name": "lastUpdatedTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    llinterface_list_link: None | LlinterfaceListLink = field(
        default=None,
        metadata={
            "name": "LLInterfaceListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    ipinterface_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "IPInterface_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class LoadShedAvailability(LoadShedAvailability1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class LoadShedAvailabilityList1(List):
    """
    A List element to hold LoadShedAvailability objects.

    :ivar load_shed_availability:
    :ivar load_shed_availability_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "LoadShedAvailabilityList"

    model_config = ConfigDict(defer_build=True)
    load_shed_availability: list[LoadShedAvailability1] = field(
        default_factory=list,
        metadata={
            "name": "LoadShedAvailability",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    load_shed_availability_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "LoadShedAvailabilityList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class LogEventList1(SubscribableList):
    """
    A List element to hold LogEvent objects.

    :ivar log_event:
    :ivar log_event_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "LogEventList"

    model_config = ConfigDict(defer_build=True)
    log_event: list[LogEvent1] = field(
        default_factory=list,
        metadata={
            "name": "LogEvent",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    log_event_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "LogEventList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class MessagingProgram1(SubscribableIdentifiedObject):
    """
    Provides a container for collections of text messages.

    :ivar active_text_message_list_link:
    :ivar locale: Indicates the language and region of the messages in
        this collection.
    :ivar primacy: Indicates the relative primacy of the provider of
        this program.
    :ivar text_message_list_link:
    :ivar messaging_program_r2_3:
    """

    class Meta:
        name = "MessagingProgram"

    model_config = ConfigDict(defer_build=True)
    active_text_message_list_link: None | ActiveTextMessageListLink = field(
        default=None,
        metadata={
            "name": "ActiveTextMessageListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    locale: LocaleType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    primacy: PrimacyType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    text_message_list_link: None | TextMessageListLink = field(
        default=None,
        metadata={
            "name": "TextMessageListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    messaging_program_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MessagingProgram_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class MeterReading1(MeterReadingBase):
    """
    Set of values obtained from the meter.
    """

    class Meta:
        name = "MeterReading"

    model_config = ConfigDict(defer_build=True)
    rate_component_list_link: None | RateComponentListLink = field(
        default=None,
        metadata={
            "name": "RateComponentListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_link: None | ReadingLink = field(
        default=None,
        metadata={
            "name": "ReadingLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_set_list_link: None | ReadingSetListLink = field(
        default=None,
        metadata={
            "name": "ReadingSetListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_type_link: ReadingTypeLink = field(
        metadata={
            "name": "ReadingTypeLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    meter_reading_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MeterReading_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class MirrorReadingSet(ReadingSetBase):
    """
    A set of Readings of the ReadingType indicated by the parent
    MeterReading.
    """

    model_config = ConfigDict(defer_build=True)
    reading: list[Reading1] = field(
        default_factory=list,
        metadata={
            "name": "Reading",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    mirror_reading_set_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MirrorReadingSet_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class NeighborList(NeighborList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Notification(Notification1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class NotificationList1(List):
    """
    A List element to hold Notification objects.
    """

    class Meta:
        name = "NotificationList"

    model_config = ConfigDict(defer_build=True)
    notification: list[Notification1] = field(
        default_factory=list,
        metadata={
            "name": "Notification",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    notification_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "NotificationList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class PowerStatus(PowerStatus1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class PriceResponse(PriceResponse1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class PriceResponseCfg(PriceResponseCfg1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class PriceResponseCfgList1(List):
    """
    A List element to hold PriceResponseCfg objects.
    """

    class Meta:
        name = "PriceResponseCfgList"

    model_config = ConfigDict(defer_build=True)
    price_response_cfg: list[PriceResponseCfg1] = field(
        default_factory=list,
        metadata={
            "name": "PriceResponseCfg",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    price_response_cfg_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PriceResponseCfgList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Rplinstance1(Resource):
    """
    Specific RPLInstance resource.

    This resource may be thought of as network status information for a
    specific RPL instance associated with IPInterface.

    :ivar dodagid: See [RFC 6550].
    :ivar dodagroot: See [RFC 6550].
    :ivar flags: See [RFC 6550].
    :ivar grounded_flag: See [RFC 6550].
    :ivar mop: See [RFC 6550].
    :ivar prf: See [RFC 6550].
    :ivar rank: See [RFC 6550].
    :ivar rplinstance_id: See [RFC 6550].
    :ivar rplsource_routes_list_link:
    :ivar version_number: See [RFC 6550].
    :ivar rplinstance_r2_3:
    """

    class Meta:
        name = "RPLInstance"

    model_config = ConfigDict(defer_build=True)
    dodagid: int = field(
        metadata={
            "name": "DODAGid",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    dodagroot: bool = field(
        metadata={
            "name": "DODAGroot",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    flags: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    grounded_flag: bool = field(
        metadata={
            "name": "groundedFlag",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    mop: int = field(
        metadata={
            "name": "MOP",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    prf: int = field(
        metadata={
            "name": "PRF",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    rank: int = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    rplinstance_id: int = field(
        metadata={
            "name": "RPLInstanceID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    rplsource_routes_list_link: None | RplsourceRoutesListLink = field(
        default=None,
        metadata={
            "name": "RPLSourceRoutesListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    version_number: int = field(
        metadata={
            "name": "versionNumber",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    rplinstance_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RPLInstance_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RplsourceRoutesList(RplsourceRoutesList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "RPLSourceRoutesList"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class RateComponent1(IdentifiedObject):
    """
    Specifies the applicable charges for a single component of the rate,
    which could be generation price or consumption price, for example.

    :ivar active_time_tariff_interval_list_link:
    :ivar flow_rate_end_limit: Specifies the maximum flow rate (e.g. kW
        for electricity) for which this RateComponent applies, for the
        usage point and given rate / tariff. In combination with
        flowRateStartLimit, allows a service provider to define the
        demand or output characteristics for the particular tariff
        design.  If a server includes the flowRateEndLimit attribute,
        then it SHALL also include flowRateStartLimit attribute. For
        example, a service provider’s tariff limits customers to 20 kWs
        of demand for the given rate structure.  Above this threshold
        (from 20-50 kWs), there are different demand charges per unit of
        consumption.  The service provider can use flowRateStartLimit
        and flowRateEndLimit to describe the demand characteristics of
        the different rates.  Similarly, these attributes can be used to
        describe limits on premises DERs that might be producing a
        commodity and sending it back into the distribution network.
        Note: At the time of writing, service provider tariffs with
        demand-based components were not originally identified as being
        in scope, and service provider tariffs vary widely in their use
        of demand components and the method for computing charges.  It
        is expected that industry groups (e.g., OpenSG) will document
        requirements in the future that the IEEE 2030.5 community can
        then use as source material for the next version of IEEE 2030.5.
    :ivar flow_rate_start_limit: Specifies the minimum flow rate (e.g.,
        kW for electricity) for which this RateComponent applies, for
        the usage point and given rate / tariff. In combination with
        flowRateEndLimit, allows a service provider to define the demand
        or output characteristics for the particular tariff design.  If
        a server includes the flowRateStartLimit attribute, then it
        SHALL also include flowRateEndLimit attribute.
    :ivar reading_type_link: Provides indication of the ReadingType with
        which this price is associated.
    :ivar role_flags: Specifies the roles that this usage point has been
        assigned.
    :ivar time_tariff_interval_list_link:
    :ivar rate_component_r2_3:
    """

    class Meta:
        name = "RateComponent"

    model_config = ConfigDict(defer_build=True)
    active_time_tariff_interval_list_link: (
        None | ActiveTimeTariffIntervalListLink
    ) = field(
        default=None,
        metadata={
            "name": "ActiveTimeTariffIntervalListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    flow_rate_end_limit: None | UnitValueType = field(
        default=None,
        metadata={
            "name": "flowRateEndLimit",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    flow_rate_start_limit: None | UnitValueType = field(
        default=None,
        metadata={
            "name": "flowRateStartLimit",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_type_link: ReadingTypeLink = field(
        metadata={
            "name": "ReadingTypeLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    role_flags: RoleFlagsType = field(
        metadata={
            "name": "roleFlags",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    time_tariff_interval_list_link: TimeTariffIntervalListLink = field(
        metadata={
            "name": "TimeTariffIntervalListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    rate_component_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RateComponent_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Reading(Reading1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ReadingList1(SubscribableList):
    """
    A List element to hold Reading objects.
    """

    class Meta:
        name = "ReadingList"

    model_config = ConfigDict(defer_build=True)
    reading: list[Reading1] = field(
        default_factory=list,
        metadata={
            "name": "Reading",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReadingList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ReadingSet1(ReadingSetBase):
    """
    A set of Readings of the ReadingType indicated by the parent
    MeterReading.
    """

    class Meta:
        name = "ReadingSet"

    model_config = ConfigDict(defer_build=True)
    reading_list_link: None | ReadingListLink = field(
        default=None,
        metadata={
            "name": "ReadingListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_set_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReadingSet_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ResponseList(ResponseList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ResponseSet1(IdentifiedObject):
    """
    A container for a ResponseList.
    """

    class Meta:
        name = "ResponseSet"

    model_config = ConfigDict(defer_build=True)
    response_list_link: None | ResponseListLink = field(
        default=None,
        metadata={
            "name": "ResponseListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    response_set_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ResponseSet_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ServiceSupplier(ServiceSupplier1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Subscription(Subscription1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class SubscriptionList1(List):
    """
    A List element to hold Subscription objects.

    :ivar subscription:
    :ivar subscription_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "SubscriptionList"

    model_config = ConfigDict(defer_build=True)
    subscription: list[Subscription1] = field(
        default_factory=list,
        metadata={
            "name": "Subscription",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    subscription_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SubscriptionList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class SupplyInterruptionOverrideList(SupplyInterruptionOverrideList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class SupportedLocaleList(SupportedLocaleList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TariffProfile1(IdentifiedObject):
    """
    A schedule of charges; structure that allows the definition of tariff
    structures such as step (block) and time of use (tier) when used in
    conjunction with TimeTariffInterval and ConsumptionTariffInterval.

    :ivar binding_prices: Indicates whether future prices are
        guaranteed. Otherwise the prices are a non-binding forecast.
    :ivar currency: The currency code indicating the currency for this
        TariffProfile.
    :ivar date_announced: Date this tariff profile was announced or
        published.
    :ivar date_effective: Date this tariff profile is effective or
        available.
    :ivar local_price: Indicates whether the prices are other than the
        retail price at the point of measurement for purchasing the
        commodity.
    :ivar location: Geographic location of this tariff profile.
    :ivar price_power_of_ten_multiplier: Indicates the power of ten
        multiplier for the price attribute.
    :ivar primacy: Indicates the relative primacy of the provider of
        this program.
    :ivar rate_code: The rate code for this tariff profile. Provides a
        method to identify the specific rate code for the TariffProfile
        instance.
    :ivar rate_code_long: The long form, or full name, of the rate code
        for this tariff profile.
    :ivar rate_component_list_link:
    :ivar retailer: The retailer for this tariff profile.
    :ivar retailer_long: The long form, or full name, of the retailer
        for this tariff profile.
    :ivar service_category_kind: The kind of service provided by this
        usage point.
    :ivar tariff_description_external_uri: URI for information regarding
        the tariff. This may be a web page with a description of the
        tariff in machine or human readable form. This should describe
        the current tariff if there are multiple versions.
    :ivar tariff_profile_r2_3:
    """

    class Meta:
        name = "TariffProfile"

    model_config = ConfigDict(defer_build=True)
    binding_prices: None | bool = field(
        default=None,
        metadata={
            "name": "bindingPrices",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    currency: None | CurrencyCode = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    date_announced: None | TimeType = field(
        default=None,
        metadata={
            "name": "dateAnnounced",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    date_effective: None | TimeType = field(
        default=None,
        metadata={
            "name": "dateEffective",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    local_price: None | bool = field(
        default=None,
        metadata={
            "name": "localPrice",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    location: None | GeographicLocationType = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    price_power_of_ten_multiplier: None | PowerOfTenMultiplierType = field(
        default=None,
        metadata={
            "name": "pricePowerOfTenMultiplier",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    primacy: PrimacyType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    rate_code: None | str = field(
        default=None,
        metadata={
            "name": "rateCode",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 20,
        },
    )
    rate_code_long: None | str = field(
        default=None,
        metadata={
            "name": "rateCodeLong",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 42,
        },
    )
    rate_component_list_link: None | RateComponentListLink = field(
        default=None,
        metadata={
            "name": "RateComponentListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    retailer: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 20,
        },
    )
    retailer_long: None | str = field(
        default=None,
        metadata={
            "name": "retailerLong",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 42,
        },
    )
    service_category_kind: ServiceKind = field(
        metadata={
            "name": "serviceCategoryKind",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    tariff_description_external_uri: None | str = field(
        default=None,
        metadata={
            "name": "tariffDescriptionExternalURI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    tariff_profile_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TariffProfile_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class TextResponse(TextResponse1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class UsagePoint1(UsagePointBase):
    """
    Logical point on a network at which consumption or production is either
    physically measured (e.g. metered) or estimated (e.g. unmetered street
    lights).

    :ivar device_lfdi: The LFDI of the source device. This attribute
        SHALL be present when mirroring.
    :ivar meter_reading_list_link:
    :ivar usage_point_r2_3:
    """

    class Meta:
        name = "UsagePoint"

    model_config = ConfigDict(defer_build=True)
    device_lfdi: None | bytes = field(
        default=None,
        metadata={
            "name": "deviceLFDI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 20,
            "format": "base16",
        },
    )
    meter_reading_list_link: None | MeterReadingListLink = field(
        default=None,
        metadata={
            "name": "MeterReadingListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    usage_point_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UsagePoint_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class AggregatedDeviceList(AggregatedDeviceList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class BillingPeriodList(BillingPeriodList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class BillingReadingList(BillingReadingList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class BillingReadingSet(BillingReadingSet1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class BillingReadingSetList1(SubscribableList):
    """
    A List element to hold BillingReadingSet objects.
    """

    class Meta:
        name = "BillingReadingSetList"

    model_config = ConfigDict(defer_build=True)
    billing_reading_set: list[BillingReadingSet1] = field(
        default_factory=list,
        metadata={
            "name": "BillingReadingSet",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    billing_reading_set_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "BillingReadingSetList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Configuration(Configuration1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class CreditRegisterList(CreditRegisterList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class CurrentDercontrols1(SubscribableResource):
    """
    This resource allows reporting the currently active DERControl modes
    and is not a mechanism for modifying the currently active DERControl
    modes.

    :ivar op_mod_connect: If present, SHALL contain the value of the
        currently executing opModConnect, regardless of source.
    :ivar op_mod_delta_var: If present, SHALL contain the value of the
        currently executing opModDeltaVar, regardless of source.
    :ivar op_mod_delta_w: If present, SHALL contain the value of the
        currently executing opModDeltaW, regardless of source.
    :ivar op_mod_energize: If present, SHALL contain the value of the
        currently executing opModEnergize, regardless of source.
    :ivar op_mod_fixed_pfabsorb_w: If present, SHALL contain the value
        of the currently executing opModFixedPFAbsorbW, regardless of
        source.
    :ivar op_mod_fixed_pfinject_w: If present, SHALL contain the value
        of the currently executing opModFixedPFInjectW, regardless of
        source.
    :ivar op_mod_fixed_v: If present, SHALL contain the value of the
        currently executing opModFixedV, regardless of source.
    :ivar op_mod_fixed_var: If present, SHALL contain the value of the
        currently executing opModFixedVar, regardless of source.
    :ivar op_mod_fixed_w: If present, SHALL contain the value of the
        currently executing opModFixedW, regardless of source.
    :ivar op_mod_freq_droop: If present, SHALL contain the value of the
        currently executing opModFreqDroop, regardless of source.
    :ivar op_mod_freq_watt: If present, SHALL contain the value of the
        currently executing opModFreqWatt, regardless of source.
    :ivar op_mod_grid_connect_permit: If present, SHALL contain the
        value of the currently executing opModGridConnectPermit,
        regardless of source.
    :ivar op_mod_hfrtmay_trip: If present, SHALL contain the value of
        the currently executing opModHFRTMayTrip, regardless of source.
    :ivar op_mod_hfrtmust_trip: If present, SHALL contain the value of
        the currently executing opModHFRTMustTrip, regardless of source.
    :ivar op_mod_hvrtmay_trip: If present, SHALL contain the value of
        the currently executing opModHVRTMayTrip, regardless of source.
    :ivar op_mod_hvrtmomentary_cessation: If present, SHALL contain the
        value of the currently executing opModHVRTMomentaryCessation,
        regardless of source.
    :ivar op_mod_hvrtmust_trip: If present, SHALL contain the value of
        the currently executing opModHVRTMustTrip, regardless of source.
    :ivar op_mod_island_permit: If present, SHALL contain the value of
        the currently executing opModIslandPermit, regardless of source.
    :ivar op_mod_lfrtmay_trip: If present, SHALL contain the value of
        the currently executing opModLFRTMayTrip, regardless of source.
    :ivar op_mod_lfrtmust_trip: If present, SHALL contain the value of
        the currently executing opModLFRTMustTrip, regardless of source.
    :ivar op_mod_lvrtmay_trip: If present, SHALL contain the value of
        the currently executing opModLVRTMayTrip, regardless of source.
    :ivar op_mod_lvrtmomentary_cessation: If present, SHALL contain the
        value of the currently executing opModLVRTMomentaryCessation,
        regardless of source.
    :ivar op_mod_lvrtmust_trip: If present, SHALL contain the value of
        the currently executing opModLVRTMustTrip, regardless of source.
    :ivar op_mod_max_lim_pct_vaabsorb: If present, SHALL contain the
        value of the currently executing opModMaxLimPctVAAbsorb,
        regardless of source.
    :ivar op_mod_max_lim_pct_vainject: If present, SHALL contain the
        value of the currently executing opModMaxLimPctVAInject,
        regardless of source.
    :ivar op_mod_max_lim_pct_var_absorb: If present, SHALL contain the
        value of the currently executing opModMaxLimPctVarAbsorb,
        regardless of source.
    :ivar op_mod_max_lim_pct_var_inject: If present, SHALL contain the
        value of the currently executing opModMaxLimPctVarInject,
        regardless of source.
    :ivar op_mod_max_lim_pct_wabsorb: If present, SHALL contain the
        value of the currently executing opModMaxLimPctWAbsorb,
        regardless of source.
    :ivar op_mod_max_lim_var_absorb: If present, SHALL contain the value
        of the currently executing opModMaxLimVarAbsorb, regardless of
        source.
    :ivar op_mod_max_lim_var_inject: If present, SHALL contain the value
        of the currently executing opModMaxLimVarInject, regardless of
        source.
    :ivar op_mod_max_lim_w: If present, SHALL contain the value of the
        currently executing opModMaxLimW, regardless of source.
    :ivar op_mod_max_lim_wabsorb: If present, SHALL contain the value of
        the currently executing opModMaxLimWAbsorb, regardless of
        source.
    :ivar op_mod_max_lim_winject: If present, SHALL contain the value of
        the currently executing opModMaxLimWInject, regardless of
        source.
    :ivar op_mod_target_v: If present, SHALL contain the value of the
        currently executing opModTargetV, regardless of source.
    :ivar op_mod_target_var: If present, SHALL contain the value of the
        currently executing opModTargetVar, regardless of source.
    :ivar op_mod_target_w: If present, SHALL contain the value of the
        currently executing opModTargetW, regardless of source.
    :ivar op_mod_volt_var: If present, SHALL contain the value of the
        currently executing opModVoltVar, regardless of source.
    :ivar op_mod_volt_watt: If present, SHALL contain the value of the
        currently executing opModVoltWatt, regardless of source.
    :ivar op_mod_watt_pf: If present, SHALL contain the value of the
        currently executing opModWattPF, regardless of source.
    :ivar op_mod_watt_var: If present, SHALL contain the value of the
        currently executing opModWattVar, regardless of source.
    :ivar updated_time: Specifies the time at which the
        CurrentDERControls information was last updated.
    :ivar current_dercontrols_r2_3:
    """

    class Meta:
        name = "CurrentDERControls"

    model_config = ConfigDict(defer_build=True)
    op_mod_connect: None | bool = field(
        default=None,
        metadata={
            "name": "opModConnect",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_delta_var: None | ReactivePowerDeltaControlType = field(
        default=None,
        metadata={
            "name": "opModDeltaVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_delta_w: None | ActivePowerDeltaControlType = field(
        default=None,
        metadata={
            "name": "opModDeltaW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_energize: None | bool = field(
        default=None,
        metadata={
            "name": "opModEnergize",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_fixed_pfabsorb_w: None | PowerFactorWithExcitationControlType = (
        field(
            default=None,
            metadata={
                "name": "opModFixedPFAbsorbW",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    op_mod_fixed_pfinject_w: None | PowerFactorWithExcitationControlType = (
        field(
            default=None,
            metadata={
                "name": "opModFixedPFInjectW",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    op_mod_fixed_v: None | SignedPerCentControlType = field(
        default=None,
        metadata={
            "name": "opModFixedV",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_fixed_var: None | FixedVarControlType = field(
        default=None,
        metadata={
            "name": "opModFixedVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_fixed_w: None | SignedPerCentControlType = field(
        default=None,
        metadata={
            "name": "opModFixedW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_freq_droop: None | FreqDroopType = field(
        default=None,
        metadata={
            "name": "opModFreqDroop",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_freq_watt: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModFreqWatt",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_grid_connect_permit: None | bool = field(
        default=None,
        metadata={
            "name": "opModGridConnectPermit",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_hfrtmay_trip: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModHFRTMayTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_hfrtmust_trip: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModHFRTMustTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_hvrtmay_trip: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModHVRTMayTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_hvrtmomentary_cessation: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModHVRTMomentaryCessation",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_hvrtmust_trip: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModHVRTMustTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_island_permit: None | bool = field(
        default=None,
        metadata={
            "name": "opModIslandPermit",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_lfrtmay_trip: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModLFRTMayTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_lfrtmust_trip: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModLFRTMustTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_lvrtmay_trip: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModLVRTMayTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_lvrtmomentary_cessation: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModLVRTMomentaryCessation",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_lvrtmust_trip: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModLVRTMustTrip",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_pct_vaabsorb: None | PerCentControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimPctVAAbsorb",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_pct_vainject: None | PerCentControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimPctVAInject",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_pct_var_absorb: None | UnsignedFixedVarControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimPctVarAbsorb",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_pct_var_inject: None | UnsignedFixedVarControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimPctVarInject",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_pct_wabsorb: None | PerCentControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimPctWAbsorb",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_var_absorb: None | UnsignedReactivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimVarAbsorb",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_var_inject: None | UnsignedReactivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimVarInject",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_w: None | PerCentControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_wabsorb: None | UnsignedActivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimWAbsorb",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_max_lim_winject: None | UnsignedActivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModMaxLimWInject",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_target_v: None | VoltageRmscontrolType = field(
        default=None,
        metadata={
            "name": "opModTargetV",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_target_var: None | ReactivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModTargetVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_target_w: None | ActivePowerControlType = field(
        default=None,
        metadata={
            "name": "opModTargetW",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_volt_var: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModVoltVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_volt_watt: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModVoltWatt",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_watt_pf: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModWattPF",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    op_mod_watt_var: None | DercurveControlType = field(
        default=None,
        metadata={
            "name": "opModWattVar",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    updated_time: TimeType = field(
        metadata={
            "name": "updatedTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    current_dercontrols_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CurrentDERControls_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class CustomerAccount(CustomerAccount1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class CustomerAccountList1(SubscribableList):
    """
    A List element to hold CustomerAccount objects.

    :ivar customer_account:
    :ivar customer_account_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "CustomerAccountList"

    model_config = ConfigDict(defer_build=True)
    customer_account: list[CustomerAccount1] = field(
        default_factory=list,
        metadata={
            "name": "CustomerAccount",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    customer_account_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CustomerAccountList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class CustomerAgreement(CustomerAgreement1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class CustomerAgreementList1(SubscribableList):
    """
    A List element to hold CustomerAgreement objects.
    """

    class Meta:
        name = "CustomerAgreementList"

    model_config = ConfigDict(defer_build=True)
    customer_agreement: list[CustomerAgreement1] = field(
        default_factory=list,
        metadata={
            "name": "CustomerAgreement",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    customer_agreement_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "CustomerAgreementList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Der(Der1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DER"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Dercomponent(Dercomponent1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERComponent"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DercomponentList1(List):
    """
    A List element to hold DERComponent resources.

    These DERComponents are components of their parent DER.
    """

    class Meta:
        name = "DERComponentList"

    model_config = ConfigDict(defer_build=True)
    dercomponent: list[Dercomponent1] = field(
        default_factory=list,
        metadata={
            "name": "DERComponent",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercomponent_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERComponentList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DercurveList(DercurveList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERCurveList"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Derlist1(List):
    """
    A List element to hold a DER object.

    More than one DER object SHALL NOT be included, but it should be noted
    that previous revisions of IEEE 2030.5 allowed more than one DER
    object. This single DER object represents the entire DER for the
    EndDevice and is the DER that acts upon DERControls. Components of this
    DER MAY be represented in the DERComponentList.

    :ivar der:
    :ivar derlist_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "DERList"

    model_config = ConfigDict(defer_build=True)
    der: list[Der1] = field(
        default_factory=list,
        metadata={
            "name": "DER",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    derlist_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class Derprogram(Derprogram1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERProgram"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DerprogramList1(SubscribableList):
    """
    A List element to hold DERProgram objects.

    :ivar derprogram:
    :ivar derprogram_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "DERProgramList"

    model_config = ConfigDict(defer_build=True)
    derprogram: list[Derprogram1] = field(
        default_factory=list,
        metadata={
            "name": "DERProgram",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    derprogram_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERProgramList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class DefaultDercontrol(DefaultDercontrol1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DefaultDERControl"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DemandResponseProgram(DemandResponseProgram1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DemandResponseProgramList1(SubscribableList):
    """
    A List element to hold DemandResponseProgram objects.

    :ivar demand_response_program:
    :ivar demand_response_program_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "DemandResponseProgramList"

    model_config = ConfigDict(defer_build=True)
    demand_response_program: list[DemandResponseProgram1] = field(
        default_factory=list,
        metadata={
            "name": "DemandResponseProgram",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    demand_response_program_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DemandResponseProgramList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class DeviceCapability1(FunctionSetAssignmentsBase):
    """
    Returned by the URI provided by DNS-SD, to allow clients to find the
    URIs to the resources in which they are interested.

    :ivar end_device_list_link:
    :ivar mirror_usage_point_list_link:
    :ivar self_device_link:
    :ivar device_capability_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "DeviceCapability"

    model_config = ConfigDict(defer_build=True)
    end_device_list_link: None | EndDeviceListLink = field(
        default=None,
        metadata={
            "name": "EndDeviceListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    mirror_usage_point_list_link: None | MirrorUsagePointListLink = field(
        default=None,
        metadata={
            "name": "MirrorUsagePointListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    self_device_link: None | SelfDeviceLink = field(
        default=None,
        metadata={
            "name": "SelfDeviceLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    device_capability_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DeviceCapability_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class DeviceInformation(DeviceInformation1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ExternalDevice(AbstractDevice):
    """
    Asset container that performs one or more end device functions.

    Contains information about external devices/entities.

    :ivar changed_time: The time at which this resource was last
        modified or created.
    :ivar enabled: This attribute indicates whether or not a device is
        enabled, or registered, on the server. If a server sets this
        attribute to false, the device is no longer registered. It
        should be noted that servers can delete device instances, but
        using this attribute for some time is more convenient for
        clients.
    :ivar flow_reservation_request_list_link:
    :ivar flow_reservation_response_list_link:
    :ivar function_set_assignments_list_link:
    :ivar post_rate: POST rate, or how often EndDevice and subordinate
        resources should be POSTed, in seconds. A client MAY indicate a
        preferred postRate when POSTing EndDevice. A server MAY add or
        modify postRate to indicate its preferred posting rate. If not
        specified, a default of 900 seconds (15 minutes) is used.
    :ivar registration_link:
    :ivar external_device_r2_3:
    """

    model_config = ConfigDict(defer_build=True)
    changed_time: TimeType = field(
        metadata={
            "name": "changedTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    enabled: None | bool = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    flow_reservation_request_list_link: (
        None | FlowReservationRequestListLink
    ) = field(
        default=None,
        metadata={
            "name": "FlowReservationRequestListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    flow_reservation_response_list_link: (
        None | FlowReservationResponseListLink
    ) = field(
        default=None,
        metadata={
            "name": "FlowReservationResponseListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    function_set_assignments_list_link: (
        None | FunctionSetAssignmentsListLink
    ) = field(
        default=None,
        metadata={
            "name": "FunctionSetAssignmentsListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    post_rate: None | int = field(
        default=None,
        metadata={
            "name": "postRate",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    registration_link: None | RegistrationLink = field(
        default=None,
        metadata={
            "name": "RegistrationLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    external_device_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ExternalDevice_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class FlowReservationRequestList(FlowReservationRequestList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class FlowReservationResponse1(Event):
    """
    The server may modify the charging or discharging parameters and
    interval to provide a lower aggregated demand at the premises, or
    within a larger part of the distribution system.

    :ivar energy_available: Indicates the amount of energy available.
    :ivar power_available: Indicates the amount of power available.
    :ivar subject: The subject field provides a method to match the
        response with the originating event. It is populated with the
        mRID of the corresponding FlowReservationRequest object.
    :ivar flow_reservation_response_r2_3:
    """

    class Meta:
        name = "FlowReservationResponse"

    model_config = ConfigDict(defer_build=True)
    energy_available: SignedRealEnergy = field(
        metadata={
            "name": "energyAvailable",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    power_available: ActivePower = field(
        metadata={
            "name": "powerAvailable",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    subject: MRidtype = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    flow_reservation_response_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FlowReservationResponse_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class FunctionSetAssignments1(FunctionSetAssignmentsBase):
    """
    Provides an identifiable, subscribable collection of resources for a
    particular device to consume.

    :ivar m_rid: The global identifier of the object.
    :ivar description: The description is a human readable text
        describing or naming the object.
    :ivar version: Contains the version number of the object. See the
        type definition for details.
    :ivar function_set_assignments_r2_3:
    :ivar subscribable: Indicates whether or not subscriptions are
        supported for this resource, and whether or not conditional
        (thresholds) are supported. If not specified, is "not
        subscribable" (0).
    """

    class Meta:
        name = "FunctionSetAssignments"

    model_config = ConfigDict(defer_build=True)
    m_rid: MRidtype = field(
        metadata={
            "name": "mRID",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    description: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 32,
        },
    )
    version: None | VersionType = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    function_set_assignments_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FunctionSetAssignments_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    subscribable: int = field(
        default=0,
        metadata={
            "type": "Attribute",
        },
    )


class HistoricalReading1(BillingMeterReadingBase):
    """
    To be used to present readings that have been processed and possibly
    corrected (as allowed, due to missing or incorrect data) by backend
    systems.

    This includes quality codes valid, verified, estimated, and derived /
    corrected.
    """

    class Meta:
        name = "HistoricalReading"

    model_config = ConfigDict(defer_build=True)
    historical_reading_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "HistoricalReading_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Ipaddr(Ipaddr1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "IPAddr"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class IpaddrList1(List):
    """
    List of IPAddr instances.
    """

    class Meta:
        name = "IPAddrList"

    model_config = ConfigDict(defer_build=True)
    ipaddr: list[Ipaddr1] = field(
        default_factory=list,
        metadata={
            "name": "IPAddr",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    ipaddr_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "IPAddrList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Ipinterface(Ipinterface1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "IPInterface"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class IpinterfaceList1(List):
    """
    List of IPInterface instances.

    :ivar ipinterface:
    :ivar ipinterface_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "IPInterfaceList"

    model_config = ConfigDict(defer_build=True)
    ipinterface: list[Ipinterface1] = field(
        default_factory=list,
        metadata={
            "name": "IPInterface",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    ipinterface_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "IPInterfaceList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class Llinterface1(Resource):
    """
    A link-layer interface object.

    :ivar crcerrors: Contains the number of CRC errors since reset.
    :ivar eui64: Contains the EUI-64 of the link layer interface. 48 bit
        MAC addresses SHALL be changed into an EUI-64 using the method
        defined in [RFC 4291], Appendix A. (The method is to insert
        "0xFFFE" as described in the reference.)
    :ivar ieee_802_15_4:
    :ivar link_layer_type: Specifies the type of link layer interface
        associated with the IPInterface. Values are below. 0 =
        Unspecified 1 = IEEE 802.3 (Ethernet) 2 = IEEE 802.11 (WLAN) 3 =
        IEEE 802.15 (PAN) 4 = IEEE 1901 (PLC) All other values reserved.
    :ivar llack_not_rx: Number of times an ACK was not received for a
        frame transmitted (when ACK was requested).
    :ivar llcsmafail: Number of times CSMA failed.
    :ivar llframes_drop_rx: Number of dropped receive frames.
    :ivar llframes_drop_tx: Number of dropped transmit frames.
    :ivar llframes_rx: Number of link layer frames received.
    :ivar llframes_tx: Number of link layer frames transmitted.
    :ivar llmedia_access_fail: Number of times access to media failed.
    :ivar lloctets_rx: Number of Bytes received.
    :ivar lloctets_tx: Number of Bytes transmitted.
    :ivar llretry_count: Number of MAC transmit retries.
    :ivar llsecurity_error_rx: Number of receive security errors.
    :ivar lo_wpan:
    :ivar llinterface_r2_3:
    """

    class Meta:
        name = "LLInterface"

    model_config = ConfigDict(defer_build=True)
    crcerrors: int = field(
        metadata={
            "name": "CRCerrors",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    eui64: bytes = field(
        metadata={
            "name": "EUI64",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 8,
            "format": "base16",
        }
    )
    ieee_802_15_4: None | Ieee802154 = field(
        default=None,
        metadata={
            "name": "IEEE_802_15_4",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    link_layer_type: int = field(
        metadata={
            "name": "linkLayerType",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    llack_not_rx: None | int = field(
        default=None,
        metadata={
            "name": "LLAckNotRx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    llcsmafail: None | int = field(
        default=None,
        metadata={
            "name": "LLCSMAFail",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    llframes_drop_rx: None | int = field(
        default=None,
        metadata={
            "name": "LLFramesDropRx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    llframes_drop_tx: None | int = field(
        default=None,
        metadata={
            "name": "LLFramesDropTx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    llframes_rx: None | int = field(
        default=None,
        metadata={
            "name": "LLFramesRx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    llframes_tx: None | int = field(
        default=None,
        metadata={
            "name": "LLFramesTx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    llmedia_access_fail: None | int = field(
        default=None,
        metadata={
            "name": "LLMediaAccessFail",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    lloctets_rx: None | int = field(
        default=None,
        metadata={
            "name": "LLOctetsRx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    lloctets_tx: None | int = field(
        default=None,
        metadata={
            "name": "LLOctetsTx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    llretry_count: None | int = field(
        default=None,
        metadata={
            "name": "LLRetryCount",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    llsecurity_error_rx: None | int = field(
        default=None,
        metadata={
            "name": "LLSecurityErrorRx",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    lo_wpan: None | LoWpan = field(
        default=None,
        metadata={
            "name": "loWPAN",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    llinterface_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "LLInterface_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class LoadShedAvailabilityList(LoadShedAvailabilityList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class LogEventList(LogEventList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class MessagingProgram(MessagingProgram1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class MessagingProgramList1(SubscribableList):
    """
    A List element to hold MessagingProgram objects.

    :ivar messaging_program:
    :ivar messaging_program_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "MessagingProgramList"

    model_config = ConfigDict(defer_build=True)
    messaging_program: list[MessagingProgram1] = field(
        default_factory=list,
        metadata={
            "name": "MessagingProgram",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    messaging_program_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MessagingProgramList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class MeterReading(MeterReading1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class MeterReadingList1(SubscribableList):
    """
    A List element to hold MeterReading objects.
    """

    class Meta:
        name = "MeterReadingList"

    model_config = ConfigDict(defer_build=True)
    meter_reading: list[MeterReading1] = field(
        default_factory=list,
        metadata={
            "name": "MeterReading",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    meter_reading_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MeterReadingList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class MirrorMeterReading1(MeterReadingBase):
    """
    Mimic of MeterReading used for managing mirrors.

    :ivar last_update_time: The date and time of the last update.
    :ivar mirror_reading_set:
    :ivar next_update_time: The date and time of the next planned
        update.
    :ivar reading:
    :ivar reading_type:
    :ivar mirror_meter_reading_r2_3:
    """

    class Meta:
        name = "MirrorMeterReading"

    model_config = ConfigDict(defer_build=True)
    last_update_time: None | TimeType = field(
        default=None,
        metadata={
            "name": "lastUpdateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    mirror_reading_set: list[MirrorReadingSet] = field(
        default_factory=list,
        metadata={
            "name": "MirrorReadingSet",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    next_update_time: None | TimeType = field(
        default=None,
        metadata={
            "name": "nextUpdateTime",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading: None | Reading1 = field(
        default=None,
        metadata={
            "name": "Reading",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_type: None | ReadingType1 = field(
        default=None,
        metadata={
            "name": "ReadingType",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    mirror_meter_reading_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MirrorMeterReading_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class NotificationList(NotificationList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Prepayment1(IdentifiedObject):
    """
    Prepayment (inherited from CIM SDPAccountingFunction).

    :ivar account_balance_link:
    :ivar active_credit_register_list_link:
    :ivar active_supply_interruption_override_list_link:
    :ivar credit_expiry_level: CreditExpiryLevel is the set point for
        availableCredit at which the service level may be changed. The
        typical value for this attribute is 0, regardless of whether the
        account balance is measured in a monetary or commodity basis.
        The units for this attribute SHALL match the units used for
        availableCredit.
    :ivar credit_register_list_link:
    :ivar low_credit_warning_level: LowCreditWarningLevel is the set
        point for availableCredit at which the creditStatus attribute in
        the AccountBalance resource SHALL indicate that available credit
        is low. The units for this attribute SHALL match the units used
        for availableCredit. Typically, this value is set by the service
        provider.
    :ivar low_emergency_credit_warning_level:
        LowEmergencyCreditWarningLevel is the set point for
        emergencyCredit at which the creditStatus attribute in the
        AccountBalance resource SHALL indicate that emergencycredit is
        low. The units for this attribute SHALL match the units used for
        availableCredit. Typically, this value is set by the service
        provider.
    :ivar prepay_mode: PrepayMode specifies whether the given Prepayment
        instance is operating in Credit, Central Wallet, ESI, or Local
        prepayment mode. The Credit mode indicates that prepayment is
        not presently in effect. The other modes are described in the
        Overview Section above.
    :ivar prepay_operation_status_link:
    :ivar supply_interruption_override_list_link:
    :ivar usage_point:
    :ivar usage_point_link:
    :ivar prepayment_r2_3:
    """

    class Meta:
        name = "Prepayment"

    model_config = ConfigDict(defer_build=True)
    account_balance_link: AccountBalanceLink = field(
        metadata={
            "name": "AccountBalanceLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    active_credit_register_list_link: None | ActiveCreditRegisterListLink = (
        field(
            default=None,
            metadata={
                "name": "ActiveCreditRegisterListLink",
                "type": "Element",
                "namespace": "urn:ieee:std:2030.5:ns",
            },
        )
    )
    active_supply_interruption_override_list_link: (
        None | ActiveSupplyInterruptionOverrideListLink
    ) = field(
        default=None,
        metadata={
            "name": "ActiveSupplyInterruptionOverrideListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    credit_expiry_level: None | AccountingUnit = field(
        default=None,
        metadata={
            "name": "creditExpiryLevel",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    credit_register_list_link: CreditRegisterListLink = field(
        metadata={
            "name": "CreditRegisterListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    low_credit_warning_level: None | AccountingUnit = field(
        default=None,
        metadata={
            "name": "lowCreditWarningLevel",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    low_emergency_credit_warning_level: None | AccountingUnit = field(
        default=None,
        metadata={
            "name": "lowEmergencyCreditWarningLevel",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    prepay_mode: PrepayModeType = field(
        metadata={
            "name": "prepayMode",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    prepay_operation_status_link: PrepayOperationStatusLink = field(
        metadata={
            "name": "PrepayOperationStatusLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    supply_interruption_override_list_link: SupplyInterruptionOverrideListLink = field(
        metadata={
            "name": "SupplyInterruptionOverrideListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    usage_point: list[UsagePoint1] = field(
        default_factory=list,
        metadata={
            "name": "UsagePoint",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    usage_point_link: None | UsagePointLink = field(
        default=None,
        metadata={
            "name": "UsagePointLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    prepayment_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "Prepayment_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class PriceResponseCfgList(PriceResponseCfgList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ProjectionReading1(BillingMeterReadingBase):
    """
    Contains values that forecast a future reading for the time or interval
    specified.
    """

    class Meta:
        name = "ProjectionReading"

    model_config = ConfigDict(defer_build=True)
    projection_reading_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ProjectionReading_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Rplinstance(Rplinstance1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "RPLInstance"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class RplinstanceList1(List):
    """
    List of RPLInstances associated with the IPinterface.
    """

    class Meta:
        name = "RPLInstanceList"

    model_config = ConfigDict(defer_build=True)
    rplinstance: list[Rplinstance1] = field(
        default_factory=list,
        metadata={
            "name": "RPLInstance",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rplinstance_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RPLInstanceList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RandomizableEvent(Event):
    """
    An Event that can indicate time ranges over which the start time and
    duration SHALL be randomized.

    :ivar randomize_duration: Number of seconds boundary inside which a
        random value must be selected to be applied to the associated
        interval duration, to avoid sudden synchronized demand changes.
        If related to price level changes, sign may be ignored. Valid
        range is -3600 to 3600. If not specified, 0 is the default.
    :ivar randomize_start: Number of seconds boundary inside which a
        random value must be selected to be applied to the associated
        interval start time, to avoid sudden synchronized demand
        changes. If related to price level changes, sign may be ignored.
        Valid range is -3600 to 3600. If not specified, 0 is the
        default.
    :ivar randomizable_event_r2_3:
    """

    model_config = ConfigDict(defer_build=True)
    randomize_duration: None | OneHourRangeType = field(
        default=None,
        metadata={
            "name": "randomizeDuration",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    randomize_start: None | OneHourRangeType = field(
        default=None,
        metadata={
            "name": "randomizeStart",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    randomizable_event_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RandomizableEvent_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RateComponent(RateComponent1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class RateComponentList1(List):
    """
    A List element to hold RateComponent objects.
    """

    class Meta:
        name = "RateComponentList"

    model_config = ConfigDict(defer_build=True)
    rate_component: list[RateComponent1] = field(
        default_factory=list,
        metadata={
            "name": "RateComponent",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    rate_component_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "RateComponentList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ReadingList(ReadingList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ReadingSet(ReadingSet1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ReadingSetList1(SubscribableList):
    """
    A List element to hold ReadingSet objects.
    """

    class Meta:
        name = "ReadingSetList"

    model_config = ConfigDict(defer_build=True)
    reading_set: list[ReadingSet1] = field(
        default_factory=list,
        metadata={
            "name": "ReadingSet",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    reading_set_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ReadingSetList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ResponseSet(ResponseSet1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ResponseSetList1(List):
    """
    A List element to hold ResponseSet objects.

    :ivar response_set:
    :ivar response_set_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "ResponseSetList"

    model_config = ConfigDict(defer_build=True)
    response_set: list[ResponseSet1] = field(
        default_factory=list,
        metadata={
            "name": "ResponseSet",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    response_set_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ResponseSetList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class SelfDevice1(AbstractDevice):
    """
    Asset container for the host serving the resources available within
    DeviceCapability.

    Contains information about the given host device/entity.

    :ivar proxied_device_list_link:
    :ivar self_device_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "SelfDevice"

    model_config = ConfigDict(defer_build=True)
    proxied_device_list_link: None | ProxiedDeviceListLink = field(
        default=None,
        metadata={
            "name": "ProxiedDeviceListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    self_device_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "SelfDevice_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class SubscriptionList(SubscriptionList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TargetReading1(BillingMeterReadingBase):
    """
    Contains readings that specify a target or goal, such as a consumption
    target, to which billing incentives or other contractual ramifications
    may be associated.
    """

    class Meta:
        name = "TargetReading"

    model_config = ConfigDict(defer_build=True)
    target_reading_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TargetReading_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class TariffProfile(TariffProfile1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TariffProfileList1(SubscribableList):
    """
    A List element to hold TariffProfile objects.

    :ivar tariff_profile:
    :ivar tariff_profile_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "TariffProfileList"

    model_config = ConfigDict(defer_build=True)
    tariff_profile: list[TariffProfile1] = field(
        default_factory=list,
        metadata={
            "name": "TariffProfile",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    tariff_profile_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TariffProfileList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class TextMessage1(Event):
    """
    Text message such as a notification.

    :ivar originator: Indicates the human-readable name of the publisher
        of the message
    :ivar priority: The priority is used to inform the client of the
        priority of the particular message.  Devices with constrained or
        limited resources for displaying Messages should use this
        attribute to determine how to handle displaying currently active
        Messages (e.g. if a device uses a scrolling method with a single
        Message viewable at a time it MAY want to push a low priority
        Message to the background and bring a newly received higher
        priority Message to the foreground).
    :ivar text_message: The textMessage attribute contains the actual
        UTF-8 encoded text to be displayed in conjunction with the
        messageLength attribute which contains the overall length of the
        textMessage attribute.  Clients and servers SHALL support a
        reception of a Message of 100 bytes in length.  Messages that
        exceed the clients display size will be left to the client to
        choose what method to handle the message (truncation, scrolling,
        etc.).
    :ivar text_message_r2_3:
    """

    class Meta:
        name = "TextMessage"

    model_config = ConfigDict(defer_build=True)
    originator: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "max_length": 20,
        },
    )
    priority: PriorityType = field(
        metadata={
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    text_message: str = field(
        metadata={
            "name": "textMessage",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    text_message_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TextMessage_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class UsagePoint(UsagePoint1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class UsagePointList1(SubscribableList):
    """
    A List element to hold UsagePoint objects.

    :ivar usage_point:
    :ivar usage_point_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "UsagePointList"

    model_config = ConfigDict(defer_build=True)
    usage_point: list[UsagePoint1] = field(
        default_factory=list,
        metadata={
            "name": "UsagePoint",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    usage_point_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "UsagePointList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class BillingReadingSetList(BillingReadingSetList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class CurrentDercontrols(CurrentDercontrols1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "CurrentDERControls"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class CustomerAccountList(CustomerAccountList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class CustomerAgreementList(CustomerAgreementList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DercomponentList(DercomponentList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERComponentList"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Dercontrol1(RandomizableEvent):
    """
    Distributed Energy Resource (DER) time/event-based control.

    :ivar dercontrol_base:
    :ivar device_category: Specifies the bitmap indicating  the
        categories of devices that SHOULD respond. Devices SHOULD ignore
        events that do not indicate their device category. If not
        present, all devices SHOULD respond.
    :ivar dercontrol_r2_3:
    """

    class Meta:
        name = "DERControl"

    model_config = ConfigDict(defer_build=True)
    dercontrol_base: DercontrolBase = field(
        metadata={
            "name": "DERControlBase",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    device_category: None | DeviceCategoryType = field(
        default=None,
        metadata={
            "name": "deviceCategory",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercontrol_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERControl_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class Derlist(Derlist1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERList"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DerprogramList(DerprogramList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERProgramList"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DemandResponseProgramList(DemandResponseProgramList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DeviceCapability(DeviceCapability1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class EndDeviceControl1(RandomizableEvent):
    """
    Instructs an EndDevice to perform a specified action.

    :ivar appliance_load_reduction:
    :ivar device_category: Specifies the bitmap indicating  the
        categories of devices that SHOULD respond. Devices SHOULD ignore
        events that do not indicate their device category.
    :ivar dr_program_mandatory: A flag to indicate if the
        EndDeviceControl is considered a mandatory event as defined by
        the service provider issuing the EndDeviceControl. The
        drProgramMandatory flag alerts the client/user that they will be
        subject to penalty or ineligibility based on the service
        provider’s program rules for that deviceCategory.
    :ivar duty_cycle:
    :ivar load_shift_forward: Indicates that the event intends to
        increase consumption. A value of true indicates the intention to
        increase usage value, and a value of false indicates the
        intention to decrease usage.
    :ivar offset:
    :ivar override_duration: The overrideDuration attribute provides a
        duration, in seconds, for which a client device is allowed to
        override this EndDeviceControl and still meet the contractual
        agreement with a service provider without opting out. If
        overrideDuration is not specified, then it SHALL default to 0.
    :ivar set_point:
    :ivar target_reduction:
    :ivar end_device_control_r2_3:
    """

    class Meta:
        name = "EndDeviceControl"

    model_config = ConfigDict(defer_build=True)
    appliance_load_reduction: None | ApplianceLoadReduction = field(
        default=None,
        metadata={
            "name": "ApplianceLoadReduction",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    device_category: DeviceCategoryType = field(
        metadata={
            "name": "deviceCategory",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    dr_program_mandatory: bool = field(
        metadata={
            "name": "drProgramMandatory",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    duty_cycle: None | DutyCycle = field(
        default=None,
        metadata={
            "name": "DutyCycle",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    load_shift_forward: bool = field(
        metadata={
            "name": "loadShiftForward",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    offset: None | Offset = field(
        default=None,
        metadata={
            "name": "Offset",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    override_duration: None | int = field(
        default=None,
        metadata={
            "name": "overrideDuration",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    set_point: None | SetPoint = field(
        default=None,
        metadata={
            "name": "SetPoint",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    target_reduction: None | TargetReduction = field(
        default=None,
        metadata={
            "name": "TargetReduction",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    end_device_control_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "EndDeviceControl_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class EndDevice1(ExternalDevice):
    """
    Asset container that performs one or more end device functions.

    Contains information about individual devices in the network.
    """

    class Meta:
        name = "EndDevice"

    model_config = ConfigDict(defer_build=True)
    proxied_device_list_link: None | ProxiedDeviceListLink = field(
        default=None,
        metadata={
            "name": "ProxiedDeviceListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    subscription_list_link: None | SubscriptionListLink = field(
        default=None,
        metadata={
            "name": "SubscriptionListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    end_device_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "EndDevice_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class FlowReservationResponse(FlowReservationResponse1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class FlowReservationResponseList1(SubscribableList):
    """
    A List element to hold FlowReservationResponse objects.

    :ivar flow_reservation_response:
    :ivar flow_reservation_response_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "FlowReservationResponseList"

    model_config = ConfigDict(defer_build=True)
    flow_reservation_response: list[FlowReservationResponse1] = field(
        default_factory=list,
        metadata={
            "name": "FlowReservationResponse",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    flow_reservation_response_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FlowReservationResponseList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class FunctionSetAssignments(FunctionSetAssignments1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class FunctionSetAssignmentsList1(SubscribableList):
    """
    A List element to hold FunctionSetAssignments objects.

    :ivar function_set_assignments:
    :ivar function_set_assignments_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "FunctionSetAssignmentsList"

    model_config = ConfigDict(defer_build=True)
    function_set_assignments: list[FunctionSetAssignments1] = field(
        default_factory=list,
        metadata={
            "name": "FunctionSetAssignments",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    function_set_assignments_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "FunctionSetAssignmentsList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class HistoricalReading(HistoricalReading1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class HistoricalReadingList1(List):
    """
    A List element to hold HistoricalReading objects.
    """

    class Meta:
        name = "HistoricalReadingList"

    model_config = ConfigDict(defer_build=True)
    historical_reading: list[HistoricalReading1] = field(
        default_factory=list,
        metadata={
            "name": "HistoricalReading",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    historical_reading_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "HistoricalReadingList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class IpaddrList(IpaddrList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "IPAddrList"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class IpinterfaceList(IpinterfaceList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "IPInterfaceList"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Llinterface(Llinterface1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "LLInterface"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class LlinterfaceList1(List):
    """
    List of LLInterface instances.
    """

    class Meta:
        name = "LLInterfaceList"

    model_config = ConfigDict(defer_build=True)
    llinterface: list[Llinterface1] = field(
        default_factory=list,
        metadata={
            "name": "LLInterface",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    llinterface_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "LLInterfaceList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class MessagingProgramList(MessagingProgramList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class MeterReadingList(MeterReadingList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class MirrorMeterReading(MirrorMeterReading1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class MirrorMeterReadingList1(List):
    """
    A List of MirrorMeterReading instances.
    """

    class Meta:
        name = "MirrorMeterReadingList"

    model_config = ConfigDict(defer_build=True)
    mirror_meter_reading: list[MirrorMeterReading1] = field(
        default_factory=list,
        metadata={
            "name": "MirrorMeterReading",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    mirror_meter_reading_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MirrorMeterReadingList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class MirrorUsagePoint1(UsagePointBase):
    """
    A parallel to UsagePoint to support mirroring.

    :ivar device_lfdi: The LFDI of the device being mirrored.
    :ivar mirror_meter_reading:
    :ivar post_rate: POST rate, or how often mirrored data should be
        POSTed, in seconds. A client MAY indicate a preferred postRate
        when POSTing MirrorUsagePoint. A server MAY add or modify
        postRate to indicate its preferred posting rate. If not
        specified, a default of 900 seconds (15 minutes) is used.
    :ivar usage_point_link:
    :ivar mirror_usage_point_r2_3:
    :ivar subscribable:
    """

    class Meta:
        name = "MirrorUsagePoint"

    model_config = ConfigDict(defer_build=True)
    device_lfdi: bytes = field(
        metadata={
            "name": "deviceLFDI",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
            "max_length": 20,
            "format": "base16",
        }
    )
    mirror_meter_reading: list[MirrorMeterReading1] = field(
        default_factory=list,
        metadata={
            "name": "MirrorMeterReading",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    post_rate: None | int = field(
        default=None,
        metadata={
            "name": "postRate",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    usage_point_link: None | UsagePointLink = field(
        default=None,
        metadata={
            "name": "UsagePointLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    mirror_usage_point_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MirrorUsagePoint_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    subscribable: int = field(
        default=0,
        metadata={
            "type": "Attribute",
        },
    )


class Prepayment(Prepayment1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class PrepaymentList1(SubscribableList):
    """
    A List element to hold Prepayment objects.

    :ivar prepayment:
    :ivar prepayment_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "PrepaymentList"

    model_config = ConfigDict(defer_build=True)
    prepayment: list[Prepayment1] = field(
        default_factory=list,
        metadata={
            "name": "Prepayment",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    prepayment_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "PrepaymentList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class ProjectionReading(ProjectionReading1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ProjectionReadingList1(List):
    """
    A List element to hold ProjectionReading objects.
    """

    class Meta:
        name = "ProjectionReadingList"

    model_config = ConfigDict(defer_build=True)
    projection_reading: list[ProjectionReading1] = field(
        default_factory=list,
        metadata={
            "name": "ProjectionReading",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    projection_reading_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ProjectionReadingList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class ProxiedDevice1(ExternalDevice):
    """
    Asset container that performs one or more end device functions.

    Contains information about individual devices that are proxied by
    another device.
    """

    class Meta:
        name = "ProxiedDevice"

    model_config = ConfigDict(defer_build=True)
    proxied_device_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ProxiedDevice_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class RplinstanceList(RplinstanceList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "RPLInstanceList"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class RateComponentList(RateComponentList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ReadingSetList(ReadingSetList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ResponseSetList(ResponseSetList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class SelfDevice(SelfDevice1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TargetReading(TargetReading1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TargetReadingList1(List):
    """
    A List element to hold TargetReading objects.
    """

    class Meta:
        name = "TargetReadingList"

    model_config = ConfigDict(defer_build=True)
    target_reading: list[TargetReading1] = field(
        default_factory=list,
        metadata={
            "name": "TargetReading",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    target_reading_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TargetReadingList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class TariffProfileList(TariffProfileList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TextMessage(TextMessage1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TextMessageList1(SubscribableList):
    """
    A List element to hold TextMessage objects.
    """

    class Meta:
        name = "TextMessageList"

    model_config = ConfigDict(defer_build=True)
    text_message: list[TextMessage1] = field(
        default_factory=list,
        metadata={
            "name": "TextMessage",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    text_message_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TextMessageList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class TimeTariffInterval1(RandomizableEvent):
    """
    Describes the time-differentiated portion of the RateComponent, if
    applicable, and provides the ability to specify multiple time
    intervals, each with its own consumption-based components and other
    attributes.

    :ivar consumption_tariff_interval_list_link:
    :ivar tou_tier: Indicates the time of use tier related to the
        reading. If not specified, is assumed to be "0 - N/A".
    :ivar time_tariff_interval_r2_3:
    """

    class Meta:
        name = "TimeTariffInterval"

    model_config = ConfigDict(defer_build=True)
    consumption_tariff_interval_list_link: (
        None | ConsumptionTariffIntervalListLink
    ) = field(
        default=None,
        metadata={
            "name": "ConsumptionTariffIntervalListLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    tou_tier: Toutype = field(
        metadata={
            "name": "touTier",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
            "required": True,
        }
    )
    time_tariff_interval_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TimeTariffInterval_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class UsagePointList(UsagePointList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class Dercontrol(Dercontrol1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERControl"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class DercontrolList1(SubscribableList):
    """
    A List element to hold DERControl objects.
    """

    class Meta:
        name = "DERControlList"

    model_config = ConfigDict(defer_build=True)
    dercontrol: list[Dercontrol1] = field(
        default_factory=list,
        metadata={
            "name": "DERControl",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    dercontrol_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "DERControlList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class EndDevice(EndDevice1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class EndDeviceControl(EndDeviceControl1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class EndDeviceControlList1(SubscribableList):
    """
    A List element to hold EndDeviceControl objects.
    """

    class Meta:
        name = "EndDeviceControlList"

    model_config = ConfigDict(defer_build=True)
    end_device_control: list[EndDeviceControl1] = field(
        default_factory=list,
        metadata={
            "name": "EndDeviceControl",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    end_device_control_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "EndDeviceControlList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class EndDeviceList1(SubscribableList):
    """
    A List element to hold EndDevice objects.

    :ivar end_device:
    :ivar end_device_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "EndDeviceList"

    model_config = ConfigDict(defer_build=True)
    end_device: list[EndDevice1] = field(
        default_factory=list,
        metadata={
            "name": "EndDevice",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    end_device_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "EndDeviceList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class FlowReservationResponseList(FlowReservationResponseList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class FunctionSetAssignmentsList(FunctionSetAssignmentsList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class HistoricalReadingList(HistoricalReadingList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class LlinterfaceList(LlinterfaceList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "LLInterfaceList"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class MirrorMeterReadingList(MirrorMeterReadingList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class MirrorUsagePoint(MirrorUsagePoint1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class MirrorUsagePointList1(SubscribableList):
    """
    A List of MirrorUsagePoint instances.

    :ivar mirror_usage_point:
    :ivar mirror_usage_point_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "MirrorUsagePointList"

    model_config = ConfigDict(defer_build=True)
    mirror_usage_point: list[MirrorUsagePoint1] = field(
        default_factory=list,
        metadata={
            "name": "MirrorUsagePoint",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    mirror_usage_point_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "MirrorUsagePointList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class PrepaymentList(PrepaymentList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ProjectionReadingList(ProjectionReadingList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ProxiedDevice(ProxiedDevice1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ProxiedDeviceList1(SubscribableList):
    """
    A List element to hold ProxiedDevice objects.

    :ivar proxied_device:
    :ivar proxied_device_list_r2_3:
    :ivar poll_rate: The default polling rate for this function set
        (this resource and all resources below), in seconds. If not
        specified, a default of 900 seconds (15 minutes) is used.
        Clients SHOULD poll the resources of this function set every
        pollRate seconds.
    """

    class Meta:
        name = "ProxiedDeviceList"

    model_config = ConfigDict(defer_build=True)
    proxied_device: list[ProxiedDevice1] = field(
        default_factory=list,
        metadata={
            "name": "ProxiedDevice",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    proxied_device_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "ProxiedDeviceList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    poll_rate: int = field(
        default=900,
        metadata={
            "name": "pollRate",
            "type": "Attribute",
        },
    )


class TargetReadingList(TargetReadingList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TextMessageList(TextMessageList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TimeTariffInterval(TimeTariffInterval1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TimeTariffIntervalList1(SubscribableList):
    """
    A List element to hold TimeTariffInterval objects.
    """

    class Meta:
        name = "TimeTariffIntervalList"

    model_config = ConfigDict(defer_build=True)
    time_tariff_interval: list[TimeTariffInterval1] = field(
        default_factory=list,
        metadata={
            "name": "TimeTariffInterval",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
    time_tariff_interval_list_r2_3: None | Revision23Type = field(
        default=None,
        metadata={
            "name": "TimeTariffIntervalList_r2_3",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class DercontrolList(DercontrolList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        name = "DERControlList"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class EndDeviceControlList(EndDeviceControlList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class EndDeviceList(EndDeviceList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class MirrorUsagePointList(MirrorUsagePointList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class ProxiedDeviceList(ProxiedDeviceList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )


class TimeTariffIntervalList(TimeTariffIntervalList1):
    """
    :ivar schema_ver: The schema version used. All XML payloads SHALL
        include a schemaVer attribute in the top-level XML element equal
        to the version of the IEEE 2030.5 schema (IEEE Std 2030.5
        supplemental material) used (e.g., schemaVer="2.2"). It should
        be noted that previous revisions of IEEE 2030.5 did not require
        this schemaVer attribute.
    """

    class Meta:
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    schema_ver: str = field(
        default="2.2",
        metadata={
            "name": "schemaVer",
            "type": "Attribute",
            "pattern": r"[1-9][0-9]{0,2}[.]([0]|[1-9][0-9]{0,2})",
        },
    )
