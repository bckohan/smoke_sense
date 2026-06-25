import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from smoke_sense import data
from smoke_sense.data import Metric
from smoke_sense.providers.aqs import EPAAQSProvider

FIXTURE = Path(__file__).parent / "fixtures" / "aqs_sample.json"


def test_year_ranges_splits_by_calendar_year():
    ranges = EPAAQSProvider._year_ranges(date(2022, 6, 1), date(2024, 2, 15))
    assert ranges == [
        (date(2022, 6, 1), date(2022, 12, 31)),
        (date(2023, 1, 1), date(2023, 12, 31)),
        (date(2024, 1, 1), date(2024, 2, 15)),
    ]


def test_year_ranges_single_year():
    ranges = EPAAQSProvider._year_ranges(date(2023, 3, 1), date(2023, 9, 1))
    assert ranges == [(date(2023, 3, 1), date(2023, 9, 1))]


def test_parse_produces_valid_schema_with_aqi():
    payload = json.loads(FIXTURE.read_text())
    provider = EPAAQSProvider(email="a@b.com", api_key="key")
    df = provider._parse(payload, county_fips="06037")
    df = data.validate(df)
    assert (df["source"] == "aqs").all()
    assert (df["metric"] == Metric.PM2_5.value).all()
    assert df["station_id"].iloc[0] == "060371103"
    # constant 9.0 µg/m³ -> NowCast 9.0 -> PM2.5 AQI 50
    assert df["aqi"].iloc[-1] == 50


def test_parse_drops_unknown_parameter_codes():
    # AQS can return codes we don't map. These must be dropped, not crash the
    # parse. (Note: 88502 IS mapped now, collapsing into canonical PM2.5.)
    payload = {
        "Data": [
            {
                "state_code": "06", "county_code": "037", "site_number": "1103",
                "parameter_code": "88101", "latitude": 34.0, "longitude": -118.2,
                "date_gmt": "2023-07-01", "time_gmt": "00:00",
                "sample_measurement": 9.0, "units_of_measure": "Micrograms/cubic meter (LC)",
            },
            {
                "state_code": "06", "county_code": "037", "site_number": "1103",
                "parameter_code": "99999", "latitude": 34.0, "longitude": -118.2,
                "date_gmt": "2023-07-01", "time_gmt": "01:00",
                "sample_measurement": 12.0, "units_of_measure": "unknown",
            },
        ]
    }
    provider = EPAAQSProvider(email="a@b.com", api_key="key")
    df = data.validate(provider._parse(payload, county_fips="06037"))
    assert len(df) == 1
    assert (df["metric"] == Metric.PM2_5.value).all()


def test_parse_collapses_frm_and_nonfrm_pm25():
    # Both 88101 (FRM) and 88502 (non-FRM) map to canonical PM2.5.
    payload = {
        "Data": [
            {
                "state_code": "06", "county_code": "037", "site_number": "1103",
                "parameter_code": "88101", "latitude": 34.0, "longitude": -118.2,
                "date_gmt": "2023-07-01", "time_gmt": "00:00",
                "sample_measurement": 9.0, "units_of_measure": "Micrograms/cubic meter (LC)",
            },
            {
                "state_code": "06", "county_code": "037", "site_number": "1103",
                "parameter_code": "88502", "latitude": 34.0, "longitude": -118.2,
                "date_gmt": "2023-07-01", "time_gmt": "01:00",
                "sample_measurement": 12.0, "units_of_measure": "Micrograms/cubic meter (LC)",
            },
        ]
    }
    provider = EPAAQSProvider(email="a@b.com", api_key="key")
    df = data.validate(provider._parse(payload, county_fips="06037"))
    assert len(df) == 2
    assert (df["metric"] == Metric.PM2_5.value).all()


def test_supported_metrics():
    assert EPAAQSProvider.supported_metrics == {
        Metric.PM2_5, Metric.PM10, Metric.O3, Metric.CO, Metric.SO2,
        Metric.NO2, Metric.PB, Metric.TEMP, Metric.RH, Metric.PRESSURE,
        Metric.WIND_SPEED, Metric.WIND_DIR,
    }


def test_fetch_batches_params_max_5():
    calls = []

    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"Data": []}

    class S:
        def get(self, url, params=None, timeout=None):
            calls.append(params["param"])
            return _R()

    provider = EPAAQSProvider(email="a@b.com", api_key="k", session=S())
    list(provider.fetch("06037", date(2026, 1, 1), date(2026, 1, 2),
                        list(provider.supported_metrics), cadence=60))
    assert calls
    assert all(len(p.split(",")) <= 5 for p in calls)


def test_parse_converts_temperature_f_to_c():
    payload = {"Data": [{
        "state_code": "06", "county_code": "037", "site_number": "0001",
        "parameter_code": "62101", "latitude": 34.0, "longitude": -118.2,
        "date_gmt": "2026-01-01", "time_gmt": "00:00", "sample_measurement": 32.0,
    }]}
    provider = EPAAQSProvider(email="a@b.com", api_key="k")
    df = provider._parse(payload, "06037")
    assert df["metric"].iloc[0] == Metric.TEMP.value
    assert df["value"].iloc[0] == pytest.approx(0.0, abs=1e-9)
    assert pd.isna(df["aqi"].iloc[0])


def test_resolve_cadence_always_hourly():
    provider = EPAAQSProvider(email="a@b.com", api_key="key")
    assert provider.resolve_cadence(10) == 60
    assert provider.resolve_cadence(1440) == 60


class _LogResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"Data": []}


class _LogSession:
    def get(self, url, params=None, timeout=None):
        return _LogResp()


def test_request_logs_redact_credentials(caplog):
    provider = EPAAQSProvider(email="me@example.com", api_key="SECRETKEY",
                              session=_LogSession())
    with caplog.at_level(logging.INFO, logger="smoke_sense"):
        provider._request({"email": "me@example.com", "key": "SECRETKEY", "state": "06"})
    assert "GET" in caplog.text
    assert "SECRETKEY" not in caplog.text
    assert "me@example.com" not in caplog.text
