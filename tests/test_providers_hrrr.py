from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from smoke_sense import data
from smoke_sense.data import Metric
from smoke_sense.providers import all_providers, get_provider
from smoke_sense.providers import hrrr


# A tiny square polygon around (lat 34, lon -118): GeoJSON Polygon (lon, lat).
_POLY = {
    "type": "Polygon",
    "coordinates": [[[-118.1, 33.9], [-117.9, 33.9],
                     [-117.9, 34.1], [-118.1, 34.1], [-118.1, 33.9]]],
}


def _sample():
    # three cells: two inside the polygon, one far outside
    lat = [34.00, 34.05, 40.00]
    lon = [-118.00, -118.05, -100.00]
    u10 = [0.0, 3.0, 9.0]
    v10 = [-3.0, 0.0, 9.0]
    u80 = [0.0, 4.0, 9.0]
    v80 = [-4.0, 0.0, 9.0]
    return hrrr.FieldSample(
        latitude=np.array(lat), longitude=np.array(lon),
        u={10: np.array(u10), 80: np.array(u80)},
        v={10: np.array(v10), 80: np.array(v80)})


class _FakeSource:
    def __init__(self, sample, raise_cycles=None):
        self._sample = sample
        self._raise = set(raise_cycles or [])
        self.calls = []

    def read(self, cycle, bbox, heights):
        self.calls.append((cycle, tuple(heights)))
        if cycle in self._raise:
            raise hrrr.FieldUnavailable("not posted")
        return self._sample


@pytest.fixture(autouse=True)
def _stub_geo(monkeypatch):
    monkeypatch.setattr(hrrr, "county_polygon", lambda fips: _POLY)
    monkeypatch.setattr(hrrr, "bbox_for_county", lambda fips: object())


def test_wind_speed_and_direction():
    assert hrrr.wind_speed(3.0, 4.0) == pytest.approx(5.0)
    assert hrrr.wind_direction(0.0, -1.0) == pytest.approx(0.0)    # from north
    assert hrrr.wind_direction(-1.0, 0.0) == pytest.approx(90.0)   # from east
    assert hrrr.wind_direction(0.0, 1.0) == pytest.approx(180.0)   # from south
    assert hrrr.wind_direction(1.0, 0.0) == pytest.approx(270.0)   # from west


def test_station_id_stable():
    assert hrrr.station_id(34.0, -118.0) == hrrr.station_id(34.0, -118.0)
    assert hrrr.station_id(34.0, -118.0) != hrrr.station_id(34.05, -118.0)


def test_cells_in_polygon_filters():
    s = _sample()
    cells = hrrr.cells_in_polygon(s.latitude, s.longitude, _POLY)
    assert [c[0] for c in cells] == [0, 1]   # third cell excluded


def test_registry_no_credentials():
    assert "hrrr" in all_providers()
    p = get_provider("hrrr", email="x", api_key="y", purpleair_key="z",
                     field_source=_FakeSource(_sample()))
    assert p.name == "hrrr"
    assert p.supported_cadences == [60]
    assert hrrr.Metric.WIND_SPEED_80M in p.supported_metrics


def test_fetch_yields_canonical_wind_chunks():
    src = _FakeSource(_sample())
    p = hrrr.HRRRProvider(field_source=src)
    chunks = list(p.fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                          list(Metric), cadence=60))
    assert chunks, "expected at least one chunk"
    df = pd.concat(chunks, ignore_index=True)
    assert list(data.validate(df).columns) == list(data.COLUMNS)
    assert set(df["source"]) == {"hrrr"}
    assert set(df["agg_window"]) == {60}
    assert df["aqi"].isna().all()
    assert {"latitude", "longitude"}.issubset(df.columns)
    assert set(df["metric"]) == {
        "wind_speed", "wind_dir", "wind_speed_80m", "wind_dir_80m"}
    assert df["timestamp"].nunique() == 24
    assert df["station_id"].nunique() == 2
    cell0 = df[(df["station_id"] == hrrr.station_id(34.0, -118.0))]
    sp = cell0[cell0["metric"] == "wind_speed"]["value"].iloc[0]
    di = cell0[cell0["metric"] == "wind_dir"]["value"].iloc[0]
    assert sp == pytest.approx(3.0)
    assert di == pytest.approx(0.0)


def test_fetch_metric_subset_reads_only_needed_heights():
    src = _FakeSource(_sample())
    p = hrrr.HRRRProvider(field_source=src)
    chunks = list(p.fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                          [Metric.WIND_SPEED], cadence=60))
    df = pd.concat(chunks, ignore_index=True)
    assert set(df["metric"]) == {"wind_speed"}
    assert all(heights == (10,) for _, heights in src.calls)


def test_fetch_skips_unavailable_cycle():
    miss = datetime(2026, 6, 16, 5, tzinfo=timezone.utc)
    src = _FakeSource(_sample(), raise_cycles=[miss])
    p = hrrr.HRRRProvider(field_source=src)
    chunks = list(p.fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                          [Metric.WIND_SPEED], cadence=60))
    df = pd.concat(chunks, ignore_index=True)
    assert miss not in set(df["timestamp"])
    assert df["timestamp"].nunique() == 23


def test_fetch_no_wanted_metrics_yields_nothing():
    p = hrrr.HRRRProvider(field_source=_FakeSource(_sample()))
    chunks = list(p.fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                          [Metric.PM2_5], cadence=60))
    assert chunks == []
