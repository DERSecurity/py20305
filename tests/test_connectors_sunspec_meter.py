"""Tests for the SunSpec connector's meter support: configuration, reading and the power sign.

A SunSpec meter is read through models 201 to 204, 211 to 214 or 220, in the
frame of its configured role. The meter models do not say which sign of real
power is import, so the connector learns it from the energy totals unless the
configuration sets it.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import TypeAdapter, ValidationError

from py20305.connectors.config import DeviceConfig
from py20305.connectors.errors import ConnectorConnectionError
from py20305.connectors.sunspec import _meter as meter_module
from py20305.connectors.sunspec import _modbus
from py20305.connectors.sunspec._meter import PowerSignCalibrator, read_meter

METER_LFDI = "e0" * 20

_DEVICE = TypeAdapter(DeviceConfig)


def _device(**fields: Any) -> Any:
    return _DEVICE.validate_python({"type": "sunspec", "lfdi": METER_LFDI, **fields})


class TestPowerSignConfiguration:
    @pytest.mark.parametrize("role", ["site_meter", "der_meter"])
    def test_power_sign_is_accepted_on_a_meter(self, role):
        assert _device(role=role, power_sign="export_positive").power_sign == "export_positive"

    def test_power_sign_on_a_der_is_rejected(self):
        with pytest.raises(ValidationError, match="power_sign applies to role"):
            _device(power_sign="import_positive")

    def test_an_unknown_power_sign_is_rejected(self):
        with pytest.raises(ValidationError, match="power_sign"):
            _device(role="site_meter", power_sign="positive")

    def test_the_sunspec_connector_is_given_the_role_and_power_sign_only(self):
        kwargs = _device(
            role="site_meter", revenue_quality=True, mirror_under="own", power_sign="auto"
        ).connector_kwargs()

        assert kwargs["role"] == "site_meter"
        assert kwargs["power_sign"] == "auto"
        assert not {"lfdi", "type", "revenue_quality", "mirror_under"} & set(kwargs)


# -- Power sign -----------------------------------------------------------------------


class _Point:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.cvalue = value


class _Meter:
    """A read SunSpec meter model: every point the test sets, nothing else."""

    def __init__(self, **points: Any) -> None:
        for name, value in points.items():
            setattr(self, name, _Point(value))
        self.reads = 0

    def read(self) -> None:
        self.reads += 1

    def set(self, **points: Any) -> None:
        for name, value in points.items():
            getattr(self, name).value = value
            getattr(self, name).cvalue = value


def _readings(
    meter: _Meter, role: str, calibrator: PowerSignCalibrator, steps: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    for step in steps:
        meter.set(**step)
        out.append(read_meter(meter, 213, role, calibrator))
    return out


#: A meter whose positive W is import: its TotWhImp rises while W is positive.
IMPORT_POSITIVE_STEPS = [
    {"W": 2000.0, "VAR": 100.0, "TotWhImp": 1000.0, "TotWhExp": 50.0},
    {"W": 2000.0, "VAR": 100.0, "TotWhImp": 1010.0, "TotWhExp": 50.0},
    {"W": 2000.0, "VAR": 100.0, "TotWhImp": 1020.0, "TotWhExp": 50.0},
]
#: A meter whose positive W is export: its TotWhExp rises while W is positive.
EXPORT_POSITIVE_STEPS = [
    {"W": 2000.0, "VAR": 100.0, "TotWhImp": 1000.0, "TotWhExp": 50.0},
    {"W": 2000.0, "VAR": 100.0, "TotWhImp": 1000.0, "TotWhExp": 60.0},
    {"W": 2000.0, "VAR": 100.0, "TotWhImp": 1000.0, "TotWhExp": 70.0},
]


def _fresh_meter() -> _Meter:
    return _Meter(
        W=0.0, VAR=0.0, Hz=50.0, PhV=230.0, PF=-0.95, VA=2100.0, A=9.0, TotWhImp=0.0, TotWhExp=0.0
    )


class TestPowerSignCalibration:
    def test_power_is_not_reported_before_the_sign_is_known(self):
        first = _readings(
            _fresh_meter(), "site_meter", PowerSignCalibrator(), IMPORT_POSITIVE_STEPS[:1]
        )

        assert first[0]["W"] is None
        assert first[0]["Var"] is None
        assert first[0]["Hz"] == 50.0
        assert first[0]["WhFwd"] == 1000.0

    def test_an_import_positive_meter_is_reported_import_positive_at_the_site(self):
        out = _readings(_fresh_meter(), "site_meter", PowerSignCalibrator(), IMPORT_POSITIVE_STEPS)

        assert out[-1]["W"] == 2000.0
        assert out[-1]["Var"] == 100.0

    def test_an_export_positive_meter_is_reported_import_positive_at_the_site(self):
        out = _readings(_fresh_meter(), "site_meter", PowerSignCalibrator(), EXPORT_POSITIVE_STEPS)

        assert out[-1]["W"] == -2000.0
        assert out[-1]["Var"] == -100.0

    def test_a_der_meter_reports_the_ders_output_as_positive(self):
        """The meter's export is the DER's output."""
        out = _readings(_fresh_meter(), "der_meter", PowerSignCalibrator(), EXPORT_POSITIVE_STEPS)

        assert out[-1]["W"] == 2000.0
        assert out[-1]["Var"] == 100.0

    def test_a_der_meter_that_counts_output_as_import_is_still_reported_output_positive(self):
        out = _readings(_fresh_meter(), "der_meter", PowerSignCalibrator(), IMPORT_POSITIVE_STEPS)

        assert out[-1]["W"] == -2000.0

    def test_one_observation_does_not_calibrate(self):
        """A single read can straddle a change of direction."""
        out = _readings(
            _fresh_meter(), "site_meter", PowerSignCalibrator(), IMPORT_POSITIVE_STEPS[:2]
        )

        assert out[-1]["W"] is None

    def test_a_reading_with_no_power_shows_no_sign(self):
        steps = [
            {"W": 0.0, "TotWhImp": 1000.0, "TotWhExp": 50.0},
            {"W": 0.0, "TotWhImp": 1010.0, "TotWhExp": 50.0},
            {"W": 0.0, "TotWhImp": 1020.0, "TotWhExp": 50.0},
        ]
        calibrator = PowerSignCalibrator()

        _readings(_fresh_meter(), "site_meter", calibrator, steps)

        assert calibrator.import_sign is None

    def test_a_total_that_falls_shows_no_sign(self):
        steps = [
            {"W": 2000.0, "TotWhImp": 1000.0, "TotWhExp": 50.0},
            {"W": 2000.0, "TotWhImp": 10.0, "TotWhExp": 50.0},
            {"W": 2000.0, "TotWhImp": 20.0, "TotWhExp": 50.0},
        ]
        calibrator = PowerSignCalibrator()

        _readings(_fresh_meter(), "site_meter", calibrator, steps)

        assert calibrator.import_sign is None

    def test_a_set_power_sign_reports_power_from_the_first_reading(self):
        out = _readings(
            _fresh_meter(),
            "site_meter",
            PowerSignCalibrator("export_positive"),
            IMPORT_POSITIVE_STEPS[:1],
        )

        assert out[0]["W"] == -2000.0

    def test_a_set_power_sign_is_not_replaced_by_what_the_totals_show(self):
        out = _readings(
            _fresh_meter(),
            "site_meter",
            PowerSignCalibrator("export_positive"),
            IMPORT_POSITIVE_STEPS,
        )

        assert out[-1]["W"] == -2000.0

    def test_calibration_is_logged_once_at_info(self, caplog):
        steps = IMPORT_POSITIVE_STEPS + [
            {"W": 2000.0, "TotWhImp": 1030.0, "TotWhExp": 50.0},
        ]
        with caplog.at_level(logging.INFO, logger=_meter_logger()):
            _readings(_fresh_meter(), "site_meter", PowerSignCalibrator(label="m1"), steps)

        calibrated = [r for r in caplog.records if "calibrated" in r.getMessage()]
        assert len(calibrated) == 1
        assert calibrated[0].levelno == logging.INFO
        assert "positive W is import" in calibrated[0].getMessage()

    def test_totals_that_keep_contradicting_a_set_sign_are_warned_once(self, caplog):
        steps = [{"W": 2000.0, "TotWhImp": 1000.0 + 10 * n, "TotWhExp": 50.0} for n in range(8)]
        with caplog.at_level(logging.WARNING, logger=_meter_logger()):
            _readings(_fresh_meter(), "site_meter", PowerSignCalibrator("export_positive"), steps)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "power_sign" in warnings[0].getMessage()

    def test_totals_that_contradict_twice_are_not_warned(self, caplog):
        steps = [{"W": 2000.0, "TotWhImp": 1000.0 + 10 * n, "TotWhExp": 50.0} for n in range(3)]
        with caplog.at_level(logging.WARNING, logger=_meter_logger()):
            _readings(_fresh_meter(), "site_meter", PowerSignCalibrator("export_positive"), steps)

        assert not [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_an_unknown_power_sign_is_refused(self):
        with pytest.raises(ValueError, match="power_sign"):
            PowerSignCalibrator("import")


def _meter_logger() -> str:
    return meter_module.__name__


class TestMeterEnergyAndPhases:
    def test_a_site_meter_reports_import_as_forward_energy(self):
        out = read_meter(
            _Meter(TotWhImp=500.0, TotWhExp=20.0), 213, "site_meter", PowerSignCalibrator()
        )

        assert (out["WhFwd"], out["WhRev"]) == (500.0, 20.0)

    def test_a_der_meter_reports_export_as_forward_energy(self):
        out = read_meter(
            _Meter(TotWhImp=500.0, TotWhExp=20.0), 213, "der_meter", PowerSignCalibrator()
        )

        assert (out["WhFwd"], out["WhRev"]) == (20.0, 500.0)

    def test_power_factor_is_reported_as_a_magnitude(self):
        out = read_meter(_fresh_meter(), 213, "site_meter", PowerSignCalibrator())

        assert out["PF"] == 0.95

    def test_a_three_phase_meter_reports_per_line_values(self):
        meter = _Meter(
            W=3000.0,
            PhVphA=231.0,
            PhVphB=232.0,
            PhVphC=233.0,
            AphA=4.0,
            AphB=5.0,
            AphC=6.0,
            WphA=1000.0,
            WphB=1000.0,
            WphC=1000.0,
            TotWhImp=0.0,
            TotWhExp=0.0,
        )

        out = read_meter(meter, 203, "site_meter", PowerSignCalibrator("import_positive"))

        assert out["ACType"] == 2
        assert (out["VL1"], out["VL2"], out["VL3"]) == (231.0, 232.0, 233.0)
        assert (out["AL1"], out["AL2"], out["AL3"]) == (4.0, 5.0, 6.0)
        assert out["WL2"] == 1000.0

    def test_per_line_power_takes_the_orientation_of_the_total(self):
        meter = _Meter(W=3000.0, WphA=1000.0, VARphA=50.0, TotWhImp=0.0, TotWhExp=0.0)

        out = read_meter(meter, 213, "site_meter", PowerSignCalibrator("export_positive"))

        assert (out["WL1"], out["VarL1"]) == (-1000.0, -50.0)

    def test_a_delta_meter_without_phase_voltage_reports_line_to_line_voltage(self):
        meter = _Meter(PPV=400.0, PPVphAB=401.0, TotWhImp=0.0, TotWhExp=0.0)

        out = read_meter(meter, 204, "site_meter", PowerSignCalibrator())

        assert out["V"] == 400.0
        assert out["VL1L2"] == 401.0

    def test_the_secure_meter_model_reports_no_ac_type(self):
        """Model 220 carries totals only, so no per-line readings are registered."""
        out = read_meter(_fresh_meter(), 220, "site_meter", PowerSignCalibrator())

        assert "ACType" not in out




# -- The SunSpec connector ------------------------------------------------------------


def _target(models: dict[int, Any]) -> MagicMock:
    target = MagicMock()
    # pysunspec2 keys its model table by group name as well as by ID.
    target.models = {**{mid: [model] for mid, model in models.items()}, "common": [object()]}
    return target


def _connector(target: MagicMock, **kwargs: Any) -> Any:
    with patch.dict(
        "sys.modules",
        {
            "sunspec2": MagicMock(),
            "sunspec2.modbus": MagicMock(),
            "sunspec2.modbus.client": MagicMock(),
        },
    ):
        import sunspec2.modbus.client as mock_client

        mock_client.SunSpecModbusClientDeviceTCP = MagicMock(return_value=target)
        from py20305.connectors.sunspec._modbus import SunSpecModbusConnector as ConnectorSunSpec

        return ConnectorSunSpec(
            host="127.0.0.1", port=8502, scan_retries=1, scan_retry_delay=0, **kwargs
        )


def _integer_meter(**points: Any) -> _Meter:
    """Model 203: integer points, which pysunspec2 scales into ``cvalue``."""
    defaults: dict[str, Any] = {
        "W": -1200.0,
        "VAR": 40.0,
        "Hz": 60.0,
        "PhV": 120.0,
        "PPV": 208.0,
        "PF": 0.9,
        "VA": 1300.0,
        "A": 10.0,
        "PhVphA": 120.0,
        "PhVphB": 121.0,
        "PhVphC": 122.0,
        "TotWhImp": 10000.0,
        "TotWhExp": 2000.0,
    }
    return _Meter(**{**defaults, **points})


class TestSunSpecMeterConnector:
    async def test_a_site_meter_reads_its_meter_model(self):
        meter = _integer_meter()
        connector = _connector(
            _target({1: object(), 203: meter}), role="site_meter", power_sign="export_positive"
        )

        out = await connector.fetch_monitoring()

        assert out["W"] == 1200.0  # the meter exports 1200 W, so the site imports it
        assert out["Var"] == -40.0
        assert out["V"] == 120.0
        assert (out["VL1"], out["VL2"], out["VL3"]) == (120.0, 121.0, 122.0)
        assert out["ACType"] == 2
        assert (out["WhFwd"], out["WhRev"]) == (10000.0, 2000.0)

    async def test_a_float_meter_model_is_read_the_same_way(self):
        meter = _integer_meter()
        connector = _connector(
            _target({1: object(), 213: meter}), role="der_meter", power_sign="import_positive"
        )

        out = await connector.fetch_monitoring()

        assert out["W"] == 1200.0
        assert out["ACType"] == 2
        assert (out["WhFwd"], out["WhRev"]) == (2000.0, 10000.0)

    async def test_the_connector_calibrates_from_successive_reads(self):
        meter = _integer_meter(W=500.0)
        connector = _connector(_target({1: object(), 211: meter}), role="site_meter")

        first = await connector.fetch_monitoring()
        meter.set(TotWhExp=2010.0)
        await connector.fetch_monitoring()
        meter.set(TotWhExp=2020.0)
        third = await connector.fetch_monitoring()

        assert first["W"] is None
        assert third["W"] == -500.0
        assert third["ACType"] == 0

    def test_a_meter_without_a_meter_model_fails_naming_the_models_found(self):
        target = _target({1: object(), 701: object(), 702: object()})

        with pytest.raises(ConnectorConnectionError, match=r"models found: 1, 701, 702"):
            _connector(target, role="site_meter")

    def test_an_unknown_role_is_refused(self):
        with pytest.raises(ValueError, match="role"):
            _connector(_target({1: object(), 203: _integer_meter()}), role="meter")

    def test_a_der_that_exposes_only_a_meter_model_is_warned_about_once(self, caplog, monkeypatch):
        monkeypatch.setattr(_modbus, "_ROLE_HINTS_GIVEN", set())
        target = _target({1: object(), 203: _integer_meter()})

        with caplog.at_level(logging.WARNING, logger=_modbus.__name__):
            for _ in range(2):
                with pytest.raises(ConnectorConnectionError):
                    _connector(target)

        hints = [
            r.getMessage() for r in caplog.records if "site_meter or der_meter" in r.getMessage()
        ]
        assert len(hints) == 1
        assert "203" in hints[0]

    def test_a_der_with_der_models_is_not_warned_about(self, caplog, monkeypatch):
        monkeypatch.setattr(_modbus, "_ROLE_HINTS_GIVEN", set())
        target = _target({1: object(), 701: object(), 203: _integer_meter()})

        with (
            caplog.at_level(logging.WARNING, logger=_modbus.__name__),
            pytest.raises(ConnectorConnectionError),
        ):
            _connector(target)

        assert not [r for r in caplog.records if "site_meter or der_meter" in r.getMessage()]
