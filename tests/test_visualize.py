from datetime import date

import pandas as pd

from smoke_sense import store, visualize
from smoke_sense.data import Metric


def _row(ts, metric, value, station, lat, lon, source="purpleair", agg=10):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037", "station_id": station,
        "latitude": lat, "longitude": lon,
        "metric": metric.value, "value": value,
        "aqi": pd.NA, "agg_window": agg, "source": source,
    }


def _seed(tmp_path):
    df = pd.DataFrame([
        _row("2026-06-16T01:00:00", Metric.PM2_5, 10.0, "s1", 34.0, -118.2),
        _row("2026-06-16T02:00:00", Metric.PM2_5, 20.0, "s1", 34.0, -118.2),
        _row("2026-06-16T01:00:00", Metric.PM2_5, 5.0, "s2", 33.9, -118.1),
        _row("2026-06-16T01:00:00", Metric.TEMP, 25.0, "s1", 34.0, -118.2),
    ])
    store.write(tmp_path, "06037", df)


def test_station_means_per_station(tmp_path):
    _seed(tmp_path)
    out = visualize.station_means(tmp_path, "06037",
                                  date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5)
    by = dict(zip(out["station_id"], out["mean"]))
    assert by["s1"] == 15.0
    assert by["s2"] == 5.0
    assert set(out.columns) == {"station_id", "latitude", "longitude", "mean"}
    s1 = out[out["station_id"] == "s1"].iloc[0]
    assert (s1["latitude"], s1["longitude"]) == (34.0, -118.2)


def test_station_means_filters_metric(tmp_path):
    _seed(tmp_path)
    out = visualize.station_means(tmp_path, "06037",
                                  date(2026, 6, 16), date(2026, 6, 16), Metric.TEMP)
    assert out["station_id"].tolist() == ["s1"]
    assert out["mean"].iloc[0] == 25.0


def test_station_means_empty_when_no_data(tmp_path):
    out = visualize.station_means(tmp_path, "99999",
                                  date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5)
    assert out.empty
    assert list(out.columns) == ["station_id", "latitude", "longitude", "mean"]


def test_get_renderer_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        visualize.get_renderer("nope")


def test_matplotlib_renderer_writes_png(tmp_path):
    points = pd.DataFrame({
        "station_id": ["s1", "s2"],
        "latitude": [34.0, 33.9],
        "longitude": [-118.2, -118.1],
        "mean": [15.0, 5.0],
    })
    out = tmp_path / "map.png"
    renderer = visualize.get_renderer("matplotlib")
    result = renderer.render_point_map(
        points, value_label="mean PM2.5 (µg/m³)", palette="YlOrRd",
        title="06037 PM2.5", output=out, basemap=False)
    assert result == out
    assert out.exists() and out.stat().st_size > 0


def test_mean_map_returns_path_and_none(tmp_path):
    _seed(tmp_path)
    out = tmp_path / "mean.png"
    result = visualize.mean_map(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5,
        palette="YlOrRd", output=out, renderer="matplotlib", basemap=False)
    assert result == out and out.exists()
    none_result = visualize.mean_map(
        tmp_path, "99999", date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5,
        palette="YlOrRd", output=tmp_path / "x.png", renderer="matplotlib",
        basemap=False)
    assert none_result is None


def test_matplotlib_renderer_basemap_failure_falls_back(tmp_path, monkeypatch):
    import contextily

    def _boom(*args, **kwargs):
        raise RuntimeError("no tiles")

    monkeypatch.setattr(contextily, "add_basemap", _boom)
    points = pd.DataFrame({
        "station_id": ["s1"], "latitude": [34.0], "longitude": [-118.2], "mean": [15.0],
    })
    out = tmp_path / "fallback.png"
    result = visualize.get_renderer("matplotlib").render_point_map(
        points, value_label="mean PM2.5 (µg/m³)", palette="YlOrRd",
        title="t", output=out, basemap=True)
    # basemap failed but the map still rendered
    assert result == out
    assert out.exists() and out.stat().st_size > 0
