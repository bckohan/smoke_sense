from datetime import date

import pandas as pd
import pytest
import requests

from smoke_sense import data
from smoke_sense.data import Metric
from smoke_sense.providers import all_providers, get_provider
from smoke_sense.providers.clarity import ClarityProvider


class _FakeResp:
    def __init__(self, *, json_data=None, text="", status_code=200, headers=None):
        self._json = json_data
        self.text = text
        self.status_code = status_code
        # Default to JSON so json() paths work unless a test overrides it.
        self.headers = headers or {"Content-Type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self._json


def test_registered():
    assert "clarity" in all_providers()
    assert isinstance(get_provider("clarity"), ClarityProvider)


def test_supported_metrics_and_cadence():
    assert ClarityProvider.supported_metrics == {
        Metric.PM2_5, Metric.PM10, Metric.NO2,
        Metric.TEMP, Metric.RH, Metric.WIND_SPEED, Metric.WIND_DIR,
    }
    assert ClarityProvider.supported_cadences == [60]


def test_constructs_with_foreign_credentials():
    # The CLI passes a shared creds dict to every provider; clarity needs none
    # and must not choke on another provider's keys.
    provider = ClarityProvider(email="a@b.com", api_key="AQSKEY", purpleair_key="PK")
    assert isinstance(provider, ClarityProvider)


def test_get_sends_browser_user_agent():
    seen = {}

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            seen["headers"] = headers
            return _FakeResp(json_data={"ok": True})

    provider = ClarityProvider(session=S())
    assert provider._get("https://x", {}) == {"ok": True}
    assert "Mozilla" in seen["headers"]["User-Agent"]


def test_get_retries_on_429_with_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr("smoke_sense.providers.clarity.time.sleep", slept.append)
    responses = [
        _FakeResp(status_code=429, headers={"Retry-After": "7"}),
        _FakeResp(json_data={"ok": True}),
    ]

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return responses.pop(0)

    provider = ClarityProvider(session=S())
    assert provider._get("https://x", {}) == {"ok": True}
    assert slept == [7.0]


def test_get_backoff_without_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr("smoke_sense.providers.clarity.time.sleep", slept.append)
    responses = [
        _FakeResp(status_code=503),
        _FakeResp(status_code=429),
        _FakeResp(json_data={"ok": True}),
    ]

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return responses.pop(0)

    provider = ClarityProvider(session=S())
    assert provider._get("https://x", {}) == {"ok": True}
    assert slept == [2.0, 4.0]


def test_get_raises_after_max_retries(monkeypatch):
    monkeypatch.setattr("smoke_sense.providers.clarity.time.sleep", lambda *_: None)

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return _FakeResp(status_code=429)

    provider = ClarityProvider(session=S())
    with pytest.raises(requests.HTTPError):
        provider._get("https://x", {})


def test_get_rejects_html_spa_fallback():
    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return _FakeResp(text="<!DOCTYPE html>",
                             headers={"Content-Type": "text/html; charset=utf-8"})

    provider = ClarityProvider(session=S())
    with pytest.raises(ValueError, match="User-Agent"):
        provider._get("https://x", {}, as_text=True)


_MARKERS_PAYLOAD = {
    "data": {
        "markers": [
            {"datasourceId": "DAABL1560", "sourceType": "CLARITY_NODE",
             "datasourceName": "Gates ES",
             "location": {"type": "Point", "coordinates": [-118.33, 33.75]}},
            {"datasourceId": "NOCOORDS", "sourceType": "CLARITY_NODE",
             "datasourceName": "Broken", "location": None},
        ]
    }
}


def test_list_stations_parses_and_skips_missing_coords():
    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return _FakeResp(json_data=_MARKERS_PAYLOAD)

    provider = ClarityProvider(session=S())
    stations = provider._list_stations()
    assert stations == [
        {"datasourceId": "DAABL1560", "name": "Gates ES",
         "lon": -118.33, "lat": 33.75}
    ]


def test_filter_stations_keeps_only_in_polygon():
    stations = [
        {"datasourceId": "IN", "name": "in", "lon": -118.0, "lat": 34.0},
        {"datasourceId": "OUT", "name": "out", "lon": 0.0, "lat": 0.0},
    ]
    geom = {"type": "Polygon",
            "coordinates": [[[-119, 33], [-117, 33], [-117, 35], [-119, 35], [-119, 33]]]}
    kept = ClarityProvider._filter_stations(stations, geom)
    assert [s["datasourceId"] for s in kept] == ["IN"]


# Row 1: every sensor populated. Row 2: only PM2.5 (a typical school node).
_CSV = (
    '"time (UTC)","time (America/Los_Angeles)",'
    '"no2Conc1HourMean","no2Conc1HourMeanUsEpaAqi",'
    '"pm10ConcMassNowcast","pm10ConcMassNowcastUsEpaAqi",'
    '"pm2_5ConcMassNowcast","pm2_5ConcMassNowcastUsEpaAqi",'
    '"relHumidAmbient1HourMean","temperatureAmbient1HourMean",'
    '"windDirection1HourMean","windSpeed1HourMean"\n'
    "2026-06-24T07:00:00.000,2026-06-24T00:00:00.000,"
    "18.8,11,30.0,28,12.0,50,44.0,20.0,180.0,2.5\n"
    "2026-06-25T07:00:00.000,2026-06-25T00:00:00.000,"
    ",,,,4.5,24,,,,\n"
)

_STATION = {"datasourceId": "DAABL1560", "name": "Gates ES",
            "lon": -118.33, "lat": 33.75}

_ALL_METRICS = [Metric.PM2_5, Metric.PM10, Metric.NO2,
                Metric.TEMP, Metric.RH, Metric.WIND_SPEED, Metric.WIND_DIR]


def test_parse_csv_maps_metrics_aqi_and_units():
    provider = ClarityProvider()
    df = provider._parse_csv(_CSV, _STATION, "06037", _ALL_METRICS)
    df = data.validate(df)

    first = df[df["timestamp"] == pd.Timestamp("2026-06-24T07:00:00Z")]
    by_value = {Metric(m): first[first["metric"] == m]["value"].iloc[0]
                for m in first["metric"].unique()}
    assert by_value[Metric.PM2_5] == pytest.approx(12.0)
    assert by_value[Metric.PM10] == pytest.approx(30.0)
    assert by_value[Metric.NO2] == pytest.approx(18.8 / 1.88)  # µg/m³ -> ppb
    assert by_value[Metric.TEMP] == pytest.approx(20.0)
    assert by_value[Metric.RH] == pytest.approx(44.0)
    assert by_value[Metric.WIND_SPEED] == pytest.approx(2.5)
    assert by_value[Metric.WIND_DIR] == pytest.approx(180.0)

    # AQI taken from Clarity's columns for AQI metrics; NA otherwise.
    pm25 = first[first["metric"] == Metric.PM2_5.value]
    assert pm25["aqi"].iloc[0] == 50
    no2 = first[first["metric"] == Metric.NO2.value]
    assert no2["aqi"].iloc[0] == 11
    temp = first[first["metric"] == Metric.TEMP.value]
    assert pd.isna(temp["aqi"].iloc[0])

    assert (df["source"] == "clarity").all()
    assert (df["agg_window"] == 60).all()
    assert df["station_id"].iloc[0] == "DAABL1560"


def test_parse_csv_drops_empty_cells():
    provider = ClarityProvider()
    df = provider._parse_csv(_CSV, _STATION, "06037", _ALL_METRICS)
    second = df[df["timestamp"] == pd.Timestamp("2026-06-25T07:00:00Z")]
    # Only PM2.5 is populated in row 2.
    assert set(second["metric"]) == {Metric.PM2_5.value}
    assert second["value"].iloc[0] == pytest.approx(4.5)


def test_parse_csv_only_wanted_metrics():
    provider = ClarityProvider()
    df = provider._parse_csv(_CSV, _STATION, "06037", [Metric.PM2_5])
    assert set(df["metric"]) == {Metric.PM2_5.value}


def test_parse_csv_empty_when_no_time_column():
    provider = ClarityProvider()
    df = provider._parse_csv("garbage,header\n1,2\n", _STATION, "06037", _ALL_METRICS)
    assert df.empty


class _FullSession:
    """Routes markers vs CSV by URL and records calls."""

    def __init__(self, csv_text):
        self.calls = []
        self.csv_text = csv_text

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        if url.endswith("/air-quality-markers"):
            return _FakeResp(json_data=_MARKERS_PAYLOAD)
        if url.endswith("/measurements.csv"):
            return _FakeResp(text=self.csv_text,
                             headers={"Content-Type": "text/csv; charset=utf-8"})
        return _FakeResp(text="<!DOCTYPE html>",
                         headers={"Content-Type": "text/html"})


_WORLD = {"type": "Polygon",
          "coordinates": [[[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]]}


@pytest.fixture
def _stub_world(monkeypatch):
    monkeypatch.setattr(
        "smoke_sense.providers.clarity.county_polygon", lambda fips: _WORLD)


def test_fetch_no_wanted_metrics_makes_no_requests():
    session = _FullSession(_CSV)
    provider = ClarityProvider(session=session)
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 24), date(2026, 6, 25), [Metric.O3]))
    assert chunks == []
    assert session.calls == []


def test_fetch_downloads_and_filters_window(_stub_world):
    session = _FullSession(_CSV)
    provider = ClarityProvider(session=session)
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 24), date(2026, 6, 24),  # only the first row's date
        [Metric.PM2_5]))
    df = pd.concat(chunks, ignore_index=True) if chunks else data.empty_frame()
    df = data.validate(df)
    csv_calls = [c for c in session.calls if c["url"].endswith("/measurements.csv")]
    assert len(csv_calls) == 1
    assert csv_calls[0]["params"] == {"networkId": "lausd"}
    # 2026-06-25 row filtered out by the window.
    assert df["timestamp"].dt.date.unique().tolist() == [date(2026, 6, 24)]
    assert (df["metric"] == Metric.PM2_5.value).all()


def test_fetch_excludes_out_of_polygon_station(monkeypatch):
    # Polygon that does NOT contain the marker at (lon -118.33, lat 33.75);
    # guards against a lon/lat swap a world polygon would hide.
    tiny = {"type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    monkeypatch.setattr(
        "smoke_sense.providers.clarity.county_polygon", lambda fips: tiny)
    session = _FullSession(_CSV)
    provider = ClarityProvider(session=session)
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 24), date(2026, 6, 25), [Metric.PM2_5]))
    csv_calls = [c for c in session.calls if c["url"].endswith("/measurements.csv")]
    assert csv_calls == []
    assert chunks == []
