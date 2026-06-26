import json

import pandas as pd
from typer.testing import CliRunner

from smoke_sense import store
from smoke_sense.bin import app
from smoke_sense.data import Metric

runner = CliRunner()


def _row(ts, value, station, aqi=pd.NA, metric=Metric.PM2_5, source="purpleair", agg=10):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037", "station_id": station,
        "metric": metric.value, "value": value,
        "aqi": aqi, "agg_window": agg, "source": source,
    }


def _seed(tmp_path, fips="06037"):
    rows = [
        _row("2026-06-16T01:00:00", 10.0, "s1", aqi=20),
        _row("2026-06-16T02:00:00", 20.0, "s1", aqi=40),
        _row("2026-06-16T01:00:00", 50.0, "s2", aqi=90),
        _row("2026-06-16T01:00:00", 5.0, "s3", aqi=10),
    ]
    df = pd.DataFrame([{**r, "county_fips": fips} for r in rows])
    store.write(tmp_path, fips, df)


def test_rank_table_default(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--output", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert result.output.index("s2") < result.output.index("s1") < result.output.index("s3")


def test_rank_json_shape(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--agg", "mean", "--json", "--output", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["06037"]
    assert payload["metric"] == "PM2.5"
    assert payload["by"] == "value"
    assert payload["agg"] == "mean"
    assert payload["order"] == "desc"
    stations = payload["stations"]
    assert [s["station_id"] for s in stations] == ["s2", "s1", "s3"]
    assert stations[0]["value"] == 50.0
    assert stations[1]["count"] == 2


def test_rank_ascending(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--asc", "--json", "--output", str(tmp_path)])
    payload = json.loads(result.output)["06037"]
    assert payload["order"] == "asc"
    assert [s["station_id"] for s in payload["stations"]] == ["s3", "s1", "s2"]


def test_rank_limit(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--limit", "1", "--json", "--output", str(tmp_path)])
    payload = json.loads(result.output)["06037"]
    assert [s["station_id"] for s in payload["stations"]] == ["s2"]


def test_rank_bad_agg(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--agg", "median", "--output", str(tmp_path)])
    assert result.exit_code != 0


def test_rank_excludes_station(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--exclude-station", "s2", "--json",
        "--output", str(tmp_path)])
    payload = json.loads(result.output)["06037"]
    assert "s2" not in [s["station_id"] for s in payload["stations"]]


def test_rank_multi_county_json(tmp_path):
    _seed(tmp_path, "06037")
    _seed(tmp_path, "06059")
    result = runner.invoke(app, [
        "rank", "06037", "06059", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--json", "--output", str(tmp_path)])
    payload = json.loads(result.output)
    assert set(payload) == {"06037", "06059"}


def test_rank_no_data_message(tmp_path):
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert "no data" in result.output


def test_rank_invalid_fips(tmp_path):
    result = runner.invoke(app, [
        "rank", "6037", "--start", "2026-06-16", "--metric", "PM2.5",
        "--output", str(tmp_path)])
    assert result.exit_code != 0
    assert "5-digit" in result.output
