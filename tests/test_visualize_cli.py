from datetime import date

import pandas as pd
import pytest
from typer.testing import CliRunner

from smoke_sense import store
from smoke_sense.bin import app
from smoke_sense.data import Metric

runner = CliRunner()


def _seed(tmp_path):
    df = pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-06-16T01:00:00", tz="UTC"),
        "county_fips": "06037", "station_id": "s1",
        "latitude": 34.0, "longitude": -118.2,
        "metric": Metric.PM2_5.value, "value": 12.0,
        "aqi": pd.NA, "agg_window": 10, "source": "purpleair",
    }])
    store.write(tmp_path, "06037", df)


def _row(ts, metric, value, station, lat, lon, aqi=pd.NA, source="purpleair", agg=10):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037", "station_id": station,
        "latitude": lat, "longitude": lon,
        "metric": metric.value, "value": value,
        "aqi": aqi, "agg_window": agg, "source": source,
    }


def _seed_rich(tmp_path):
    df = pd.DataFrame([
        _row("2026-06-16T01:00:00", Metric.PM2_5, 10.0, "s1", 34.0, -118.2, aqi=42),
        _row("2026-06-16T02:00:00", Metric.PM2_5, 20.0, "s1", 34.0, -118.2, aqi=60),
        _row("2026-06-16T01:00:00", Metric.PM2_5, 5.0, "s2", 33.9, -118.1, aqi=21),
        _row("2026-06-16T01:00:00", Metric.TEMP, 25.0, "s1", 34.0, -118.2),
    ])
    store.write(tmp_path, "06037", df)


def test_exclude_station_filter_drops_rows(tmp_path):
    from smoke_sense import visualize as viz
    from smoke_sense.bin import _outlier_cli
    _seed_rich(tmp_path)   # s1 (2 PM2.5 rows) + s2 (1 PM2.5 row)
    f = _outlier_cli.make_filter(enabled=True, no_range=False, zscore=None,
                                 iqr_on=False, iqr_k=3.0, bound=None,
                                 exclude=["s2"])
    obs = viz.metric_observations(tmp_path, "06037", date(2026, 6, 16),
                                  date(2026, 6, 16), Metric.PM2_5, outlier_filter=f)
    assert set(obs["station_id"]) == {"s1"}


def test_series_exclude_station_cli(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "series", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--exclude-station", "s2",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output


def test_mean_map_writes_png(tmp_path):
    _seed(tmp_path)
    out = tmp_path / "m.png"
    result = runner.invoke(app, [
        "visualize", "mean-map", "06037", "--start", "2026-06-16",
        "--end", "2026-06-16", "--metric", "PM2.5", "--no-basemap",
        "--output", str(out), "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert out.exists() and out.stat().st_size > 0


def test_mean_map_no_data_message(tmp_path):
    result = runner.invoke(app, [
        "visualize", "mean-map", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--no-basemap", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "no data" in result.output


def test_mean_map_invalid_fips(tmp_path):
    result = runner.invoke(app, [
        "visualize", "mean-map", "6037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--no-basemap", "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_mean_map_invalid_metric(tmp_path):
    result = runner.invoke(app, [
        "visualize", "mean-map", "06037", "--start", "2026-06-16",
        "--metric", "nope", "--no-basemap", "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_mean_map_unknown_renderer(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "visualize", "mean-map", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--renderer", "nope", "--no-basemap",
        "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


@pytest.mark.parametrize("kind", ["series", "scatter", "aggregate", "histogram"])
def test_chart_subcommands_write_png(tmp_path, kind):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", kind, "06037", "--start", "2026-06-16",
        "--end", "2026-06-16", "--metric", "PM2.5",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    pngs = list((tmp_path / "06037").glob(f"*_{kind}.png"))
    assert pngs and pngs[0].stat().st_size > 0


def test_series_station_filter(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "series", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--station", "s1",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output


def test_series_station_filter_no_match_messages(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "series", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--station", "ghost",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "no data" in result.output


def test_by_aqi_on_pm25_ok(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "aggregate", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--by", "aqi", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_aqi_*_aggregate.png"))


def test_by_aqi_on_temperature_fails(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "histogram", "06037", "--start", "2026-06-16",
        "--metric", "temperature", "--by", "aqi",
        "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_invalid_metric_fails(tmp_path):
    result = runner.invoke(app, [
        "visualize", "scatter", "06037", "--start", "2026-06-16",
        "--metric", "BOGUS", "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_unknown_renderer_fails(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "scatter", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--renderer", "nope",
        "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_no_data_message(tmp_path):
    result = runner.invoke(app, [
        "visualize", "series", "99999", "--start", "2026-06-16",
        "--metric", "PM2.5", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "no data" in result.output


def test_mean_map_by_aqi(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "mean-map", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--by", "aqi", "--no-basemap",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_aqi_*_mean.png"))


def test_histogram_by_aqi_all_null_no_data_message(tmp_path):
    """When --by aqi is used but aqi column is all-NA, show no-data message, write no PNG."""
    _seed(tmp_path)  # seeds PM2.5 rows with aqi=pd.NA
    result = runner.invoke(app, [
        "visualize", "histogram", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--by", "aqi",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "no" in result.output
    assert not list((tmp_path / "06037").glob("*_aqi_*_histogram.png"))


def test_mean_map_by_aqi_all_null_no_data_message(tmp_path):
    """When mean-map --by aqi used but aqi is all-NA, show no-data message, write no PNG."""
    _seed(tmp_path)  # seeds PM2.5 rows with aqi=pd.NA
    result = runner.invoke(app, [
        "visualize", "mean-map", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--by", "aqi", "--no-basemap",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "no" in result.output
    assert not list((tmp_path / "06037").glob("*_aqi_*_mean.png"))


def _seed_with_garbage(tmp_path):
    rows = [_row(f"2026-06-16T0{i}:00:00", Metric.PM2_5, v, "s1", 34.0, -118.2)
            for i, v in enumerate([10, 11, 9, 8, 12])]
    rows.append(_row("2026-06-16T09:00:00", Metric.PM2_5, -999.0, "s1", 34.0, -118.2))
    store.write(tmp_path, "06037", pd.DataFrame(rows))


def test_histogram_filters_garbage_by_default(tmp_path):
    _seed_with_garbage(tmp_path)
    result = runner.invoke(app, [
        "visualize", "histogram", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_histogram.png"))


def test_no_outlier_filter_keeps_garbage(tmp_path):
    _seed_with_garbage(tmp_path)
    result = runner.invoke(app, [
        "visualize", "histogram", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--no-outlier-filter", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output


def test_visualize_bad_outlier_bound_fails(tmp_path):
    _seed_with_garbage(tmp_path)
    result = runner.invoke(app, [
        "visualize", "histogram", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--outlier-bound", "PM2.5:bad",
        "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_render_chart_excludes_garbage(tmp_path):
    from smoke_sense import visualize as viz
    from smoke_sense.bin import _outlier_cli
    _seed_with_garbage(tmp_path)
    f = _outlier_cli.make_filter(enabled=True, no_range=False, zscore=None,
                                 iqr_on=False, iqr_k=3.0, bound=None)
    obs = viz.metric_observations(tmp_path, "06037", date(2026, 6, 16),
                                  date(2026, 6, 16), Metric.PM2_5, outlier_filter=f)
    assert obs["value"].min() >= 0  # -999 dropped


def test_series_no_station_writes_png(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "series", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_series.png"))


def test_series_with_station_map_writes_png(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "series", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--station", "s1", "--station", "s2",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_series.png"))


def test_scatter_no_station_writes_png(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "scatter", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_scatter.png"))


def test_scatter_with_station_filter_writes_png(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "scatter", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--station", "s1", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_scatter.png"))
