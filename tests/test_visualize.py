from datetime import date

import pandas as pd
import pytest

from smoke_sense import store, visualize
from smoke_sense.data import AQI_METRICS, Metric


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


def test_metric_observations_columns(tmp_path):
    _seed(tmp_path)
    obs = visualize.metric_observations(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5)
    assert list(obs.columns) == ["timestamp", "station_id", "value", "aqi"]
    assert sorted(obs["value"].tolist()) == [5.0, 10.0, 20.0]
    assert set(obs["station_id"]) == {"s1", "s2"}


def test_metric_observations_empty(tmp_path):
    obs = visualize.metric_observations(
        tmp_path, "99999", date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5)
    assert obs.empty
    assert list(obs.columns) == ["timestamp", "station_id", "value", "aqi"]


def test_resolve_by_value_and_aqi():
    assert visualize.resolve_by(Metric.PM2_5, "value") == "value"
    assert visualize.resolve_by(Metric.PM2_5, "aqi") == "aqi"
    assert Metric.PM2_5 in AQI_METRICS


def test_resolve_by_rejects_aqi_for_non_aqi_metric():
    with pytest.raises(ValueError):
        visualize.resolve_by(Metric.TEMP, "aqi")


def test_resolve_by_rejects_unknown():
    with pytest.raises(ValueError):
        visualize.resolve_by(Metric.PM2_5, "nonsense")


def test_y_label():
    assert visualize.y_label(Metric.PM2_5, "value") == "PM2.5 (µg/m³)"
    assert visualize.y_label(Metric.PM2_5, "aqi") == "AQI"


def test_mean_map_by_aqi_all_null_returns_none(tmp_path):
    """mean_map returns None when aqi column is entirely NA for an AQI-eligible metric."""
    _seed(tmp_path)  # all rows have aqi=pd.NA
    out = tmp_path / "map.png"
    result = visualize.mean_map(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5,
        by="aqi", palette="YlOrRd", output=out, renderer="matplotlib", basemap=False)
    assert result is None
    assert not out.exists()


def test_station_means_by_aqi(tmp_path):
    df = pd.DataFrame([
        {**_row("2026-06-16T01:00:00", Metric.PM2_5, 10.0, "s1", 34.0, -118.2),
         "aqi": 42},
        {**_row("2026-06-16T02:00:00", Metric.PM2_5, 20.0, "s1", 34.0, -118.2),
         "aqi": 60},
    ])
    store.write(tmp_path, "06037", df)
    out = visualize.station_means(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16),
        Metric.PM2_5, by="aqi")
    assert out[out["station_id"] == "s1"]["mean"].iloc[0] == 51.0


def _obs_frame():
    return pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-06-16T01:00:00", "2026-06-16T02:00:00",
            "2026-06-16T01:00:00", "2026-06-16T02:00:00"], utc=True),
        "station_id": ["s1", "s1", "s2", "s2"],
        "value": [10.0, 20.0, 5.0, 15.0],
        "aqi": pd.array([42, 60, 21, 53], dtype="Int16"),
    })


def test_get_chart_renderer_unknown_raises():
    with pytest.raises(KeyError):
        visualize.get_chart_renderer("nope")


def test_chart_series_writes_png(tmp_path):
    out = tmp_path / "series.png"
    r = visualize.get_chart_renderer("matplotlib")
    result = r.render_series(
        _obs_frame(), y_column="value", y_label="PM2.5 (µg/m³)",
        title="t", palette="YlOrRd", output=out)
    assert result == out and out.exists() and out.stat().st_size > 0


def test_chart_scatter_writes_png(tmp_path):
    out = tmp_path / "scatter.png"
    r = visualize.get_chart_renderer("matplotlib")
    result = r.render_scatter(
        _obs_frame(), y_column="value", y_label="PM2.5 (µg/m³)",
        title="t", palette="YlOrRd", output=out)
    assert result == out and out.exists() and out.stat().st_size > 0


def test_chart_aggregate_with_and_without_band(tmp_path):
    r = visualize.get_chart_renderer("matplotlib")
    for band in (True, False):
        out = tmp_path / f"agg_{band}.png"
        result = r.render_aggregate(
            _obs_frame(), y_column="value", y_label="PM2.5 (µg/m³)",
            title="t", palette="YlOrRd", output=out, band=band)
        assert result == out and out.exists() and out.stat().st_size > 0


def test_chart_histogram_respects_bins(tmp_path):
    out = tmp_path / "hist.png"
    r = visualize.get_chart_renderer("matplotlib")
    result = r.render_histogram(
        _obs_frame(), y_column="value", y_label="PM2.5 (µg/m³)",
        title="t", palette="YlOrRd", output=out, bins=5)
    assert result == out and out.exists() and out.stat().st_size > 0


def test_metric_observations_applies_outlier_filter(tmp_path):
    _seed(tmp_path)  # existing helper seeds PM2.5 s1=[10,20], s2=[5], TEMP s1=25

    def drop_high(df):
        return df[df["value"] < 15.0]

    out = visualize.metric_observations(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16),
        Metric.PM2_5, outlier_filter=drop_high)
    assert out["value"].max() < 15.0
    # without the filter the 20.0 reading is present
    base = visualize.metric_observations(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5)
    assert base["value"].max() == 20.0


def test_station_means_applies_outlier_filter(tmp_path):
    _seed(tmp_path)

    def drop_high(df):
        return df[df["value"] < 15.0]

    out = visualize.station_means(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16),
        Metric.PM2_5, outlier_filter=drop_high)
    # s1 now only has the 10.0 reading -> mean 10.0
    assert out[out["station_id"] == "s1"]["mean"].iloc[0] == 10.0
