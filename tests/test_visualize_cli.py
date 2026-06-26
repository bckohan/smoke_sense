from datetime import date

import pandas as pd
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
