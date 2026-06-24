import json
from datetime import date
from pathlib import Path

import pytest

from smoke_sense import data
from smoke_sense.data import Pollutant
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
    assert (df["pollutant"] == Pollutant.PM2_5.value).all()
    assert df["station_id"].iloc[0] == "060371103"
    # constant 9.0 µg/m³ -> NowCast 9.0 -> PM2.5 AQI 50
    assert df["aqi"].iloc[-1] == 50


def test_parse_drops_unknown_parameter_codes():
    # AQS can return codes we don't map (e.g. non-FRM PM2.5 88502). These must
    # be dropped, not crash the parse.
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
    assert len(df) == 1
    assert (df["pollutant"] == Pollutant.PM2_5.value).all()


def test_supported_pollutants():
    assert EPAAQSProvider.supported == {Pollutant.PM2_5, Pollutant.PM10, Pollutant.O3}
