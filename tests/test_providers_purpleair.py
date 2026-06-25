from datetime import date

import pandas as pd
import pytest
import requests

from smoke_sense import data
from smoke_sense.data import Metric
from smoke_sense.providers.purpleair import PurpleAirProvider, epa_correct_pm25


class _FakeResp:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

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
                {"fields": ["sensor_index", "latitude", "longitude",
                            "last_seen", "date_created"],
                 "data": [[262253, 33.75, -118.33, 1782000000, 1600000000]]}
            )
        # A single inclusive day spans 86400s; this simulated server accepts one
        # day and rejects larger ranges, forcing the chunker down to day chunks.
        span = params["end_timestamp"] - params["start_timestamp"]
        if span > 86400 + 3600:
            raise requests.HTTPError(response=_FakeResp({}, status_code=400))
        return _FakeResp(
            {"fields": ["time_stamp", "humidity", "pm2.5_cf_1", "pm10.0_cf_1",
                        "temperature"],
             "data": [[1781996400, 44, 1.8, 3.2, 68.0]]}
        )


_WORLD = {"type": "Polygon",
          "coordinates": [[[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]]}


@pytest.fixture(autouse=True)
def _stub_polygon(monkeypatch):
    monkeypatch.setattr(
        "smoke_sense.providers.purpleair.county_polygon", lambda fips: _WORLD
    )


def test_epa_correction_hand_computed():
    # 0.524*100 - 0.0862*50 + 5.75 = 52.4 - 4.31 + 5.75 = 53.84
    assert epa_correct_pm25(100.0, 50.0) == pytest.approx(53.84, abs=1e-6)


def test_supported_pollutants():
    expected = {
        Metric.PM2_5, Metric.PM2_5_CF1, Metric.PM2_5_ATM,
        Metric.PM10, Metric.PM10_CF1, Metric.PM10_ATM,
        Metric.PM1_0_CF1, Metric.PM1_0_ATM,
        Metric.TEMP, Metric.RH, Metric.PRESSURE, Metric.VOC,
    }
    assert PurpleAirProvider.supported_metrics == expected


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
        wanted=[Metric.PM2_5, Metric.PM10], agg=60,
    )
    df = data.validate(df)
    pm25 = df[df["metric"] == Metric.PM2_5.value]
    assert pm25["value"].iloc[0] == pytest.approx(53.84, abs=1e-6)
    assert (df["source"] == "purpleair").all()
    assert df["station_id"].iloc[0] == "123"


def test_parse_history_emits_corrected_and_raw_and_converts_temp():
    payload = {
        "fields": ["time_stamp", "humidity", "pm2.5_cf_1", "pm2.5_atm", "temperature"],
        "data": [[1781996400, 50.0, 100.0, 60.0, 32.0]],
    }
    provider = PurpleAirProvider(purpleair_key="k")
    df = provider._parse_history(
        payload, sensor_id="123", lat=34.0, lon=-118.2, county_fips="06037",
        wanted=[Metric.PM2_5, Metric.PM2_5_CF1, Metric.PM2_5_ATM, Metric.TEMP], agg=10)
    by = {m: df[df["metric"] == m.value]["value"].iloc[0]
          for m in (Metric.PM2_5, Metric.PM2_5_CF1, Metric.PM2_5_ATM, Metric.TEMP)}
    assert by[Metric.PM2_5_CF1] == 100.0
    assert by[Metric.PM2_5_ATM] == 60.0
    assert by[Metric.PM2_5] == pytest.approx(0.524 * 100 - 0.0862 * 50 + 5.75, abs=1e-6)
    assert by[Metric.TEMP] == pytest.approx(0.0, abs=1e-9)
    assert df[df["metric"] == Metric.PM2_5.value]["aqi"].notna().all()
    assert df[df["metric"] == Metric.TEMP.value]["aqi"].isna().all()


def test_history_request_does_not_request_time_stamp_field():
    # PurpleAir returns time_stamp automatically and rejects it as a requested
    # history field with HTTP 400, so fetch must not include it in `fields`.
    session = _FakeSession()
    provider = PurpleAirProvider(purpleair_key="key", session=session)
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 16), date(2026, 6, 24),
        [Metric.PM2_5, Metric.PM10],
    ))
    df = pd.concat(chunks, ignore_index=True) if chunks else data.empty_frame()
    history_calls = [c for c in session.calls if "/history" in c["url"]]
    assert history_calls, "expected at least one history call"
    for call in history_calls:
        requested = call["params"]["fields"].split(",")
        assert "time_stamp" not in requested
    # response still carries time_stamp, so parsing produced rows
    assert not df.empty


def test_fetch_chunks_large_range_on_400():
    session = _FakeSession()
    provider = PurpleAirProvider(purpleair_key="key", session=session)
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 1), date(2026, 6, 5),
        [Metric.PM2_5], cadence=10,
    ))
    df = pd.concat(chunks, ignore_index=True) if chunks else data.empty_frame()
    history_calls = [c for c in session.calls if "/history" in c["url"]]
    assert len(history_calls) > 1
    assert not df.empty
    assert (df["agg_window"] == 10).all()


def test_get_retries_on_429_with_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr("smoke_sense.providers.purpleair.time.sleep", slept.append)
    responses = [
        _FakeResp({}, status_code=429, headers={"Retry-After": "7"}),
        _FakeResp({"ok": True}),
    ]

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return responses.pop(0)

    provider = PurpleAirProvider(purpleair_key="k", session=S())
    assert provider._get("https://x", {}) == {"ok": True}
    assert slept == [7.0]


def test_get_backoff_without_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr("smoke_sense.providers.purpleair.time.sleep", slept.append)
    responses = [
        _FakeResp({}, status_code=429),
        _FakeResp({}, status_code=429),
        _FakeResp({"ok": True}),
    ]

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return responses.pop(0)

    provider = PurpleAirProvider(purpleair_key="k", session=S())
    assert provider._get("https://x", {}) == {"ok": True}
    assert slept == [2.0, 4.0]


def test_get_raises_after_max_retries(monkeypatch):
    monkeypatch.setattr("smoke_sense.providers.purpleair.time.sleep", lambda *_: None)

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return _FakeResp({}, status_code=429)

    provider = PurpleAirProvider(purpleair_key="k", session=S())
    with pytest.raises(requests.HTTPError):
        provider._get("https://x", {})


def test_list_sensors_requests_outdoor_and_activity_fields():
    session = _FakeSession()
    provider = PurpleAirProvider(purpleair_key="k", session=session)
    from smoke_sense.geo import BBox
    provider._list_sensors(BBox(33.0, -119.0, 35.0, -117.0))
    params = session.calls[0]["params"]
    assert params["location_type"] == 0
    assert "last_seen" in params["fields"]
    assert "date_created" in params["fields"]


def test_fetch_excludes_out_of_polygon_sensor(monkeypatch):
    # A polygon that does NOT contain the canned sensor at (lat 33.75, lon -118.33).
    # Guards against a lon/lat argument swap that the world-polygon stub would hide.
    tiny = {"type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    monkeypatch.setattr(
        "smoke_sense.providers.purpleair.county_polygon", lambda fips: tiny
    )
    session = _FakeSession()
    provider = PurpleAirProvider(purpleair_key="k", session=session)
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 16), date(2026, 6, 17), [Metric.PM2_5], cadence=10
    ))
    df = pd.concat(chunks, ignore_index=True) if chunks else data.empty_frame()
    history_calls = [c for c in session.calls if "/history" in c["url"]]
    assert history_calls == []
    assert df.empty


def test_filter_sensors_drops_out_of_window_and_out_of_polygon():
    geom = {"type": "Polygon",
            "coordinates": [[[-119, 33], [-117, 33], [-117, 35], [-119, 35], [-119, 33]]]}
    in_county = {"sensor_index": 1, "latitude": 34.0, "longitude": -118.0,
                 "last_seen": 1782000000, "date_created": 1600000000}
    offline_before = {"sensor_index": 2, "latitude": 34.0, "longitude": -118.0,
                      "last_seen": 1600000000, "date_created": 1500000000}
    out_of_county = {"sensor_index": 3, "latitude": 0.0, "longitude": 0.0,
                     "last_seen": 1782000000, "date_created": 1600000000}
    kept = PurpleAirProvider._filter_sensors(
        [in_county, offline_before, out_of_county], geom,
        date(2026, 6, 1), date(2026, 6, 30),
    )
    assert [s["sensor_index"] for s in kept] == [1]
