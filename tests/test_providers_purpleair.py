from datetime import date

import pandas as pd
import pytest

from smoke_sense import data
from smoke_sense.data import Pollutant
from smoke_sense.providers.purpleair import PurpleAirProvider, epa_correct_pm25


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """Records GET calls and returns canned sensor-list / history payloads."""

    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        if url.endswith("/v1/sensors"):
            return _FakeResp(
                {"fields": ["sensor_index", "latitude", "longitude"],
                 "data": [[262253, 33.75, -118.33]]}
            )
        # history endpoint — PurpleAir always returns time_stamp as the first column
        return _FakeResp(
            {"fields": ["time_stamp", "humidity", "pm2.5_cf_1", "pm10.0_cf_1"],
             "data": [[1781996400, 44, 1.8, 3.2]]}
        )


def test_epa_correction_hand_computed():
    # 0.524*100 - 0.0862*50 + 5.75 = 52.4 - 4.31 + 5.75 = 53.84
    assert epa_correct_pm25(100.0, 50.0) == pytest.approx(53.84, abs=1e-6)


def test_supported_pollutants():
    assert PurpleAirProvider.supported == {Pollutant.PM2_5, Pollutant.PM10}


def test_ignores_foreign_credentials_and_fails_fast():
    # The CLI passes a shared creds dict to every provider; PurpleAir must not
    # adopt another provider's api_key. With only an AQS key present, PurpleAir
    # has no key and must fail fast rather than authenticate with the wrong one.
    provider = PurpleAirProvider(email="a@b.com", api_key="AQSKEY")
    assert provider.api_key is None
    with pytest.raises(ValueError, match="PurpleAir requires credentials"):
        provider._headers()


def test_parse_history_corrects_and_validates():
    # PurpleAir history rows: [time_stamp, pm2.5_cf_1, pm10.0_cf_1, humidity]
    payload = {
        "fields": ["time_stamp", "pm2.5_cf_1", "pm10.0_cf_1", "humidity"],
        "data": [
            [1688169600, 100.0, 30.0, 50.0],
            [1688173200, 100.0, 30.0, 50.0],
        ],
    }
    provider = PurpleAirProvider(purpleair_key="key")
    df = provider._parse_history(
        payload, sensor_id="123", lat=34.0, lon=-118.2, county_fips="06037",
        pollutants=[Pollutant.PM2_5, Pollutant.PM10],
    )
    df = data.validate(df)
    pm25 = df[df["pollutant"] == Pollutant.PM2_5.value]
    assert pm25["value"].iloc[0] == pytest.approx(53.84, abs=1e-6)
    assert (df["source"] == "purpleair").all()
    assert df["station_id"].iloc[0] == "123"


def test_history_request_does_not_request_time_stamp_field():
    # PurpleAir returns time_stamp automatically and rejects it as a requested
    # history field with HTTP 400, so fetch must not include it in `fields`.
    session = _FakeSession()
    provider = PurpleAirProvider(purpleair_key="key", session=session)
    df = provider.fetch(
        "06037", date(2026, 6, 16), date(2026, 6, 24),
        [Pollutant.PM2_5, Pollutant.PM10],
    )
    history_calls = [c for c in session.calls if "/history" in c["url"]]
    assert history_calls, "expected at least one history call"
    for call in history_calls:
        requested = call["params"]["fields"].split(",")
        assert "time_stamp" not in requested
    # response still carries time_stamp, so parsing produced rows
    assert not df.empty
