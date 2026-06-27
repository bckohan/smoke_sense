import json

import pandas as pd
import pytest
from typer.testing import CliRunner

from smoke_sense import store
from smoke_sense.bin import app
from smoke_sense.data import Metric

runner = CliRunner()


def _row(ts, value, station):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"), "county_fips": "06037",
        "station_id": station, "metric": Metric.PM2_5.value, "value": value,
        "aqi": pd.NA, "agg_window": 10, "source": "purpleair",
    }


def _seed(tmp_path, fips="06037"):
    rows = [_row(f"2026-06-16T0{i}:00:00", v, "s1")
            for i, v in enumerate([10, 11, 9, 8, 12])]
    rows.append(_row("2026-06-16T09:00:00", 5000.0, "s1"))   # range outlier
    rows += [_row("2026-06-16T01:00:00", 10.0, "s2"),
             _row("2026-06-16T02:00:00", 11.0, "s2")]
    df = pd.DataFrame([{**r, "county_fips": fips} for r in rows])
    store.write(tmp_path, fips, df)


def test_outliers_table(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "s1" in result.output


def test_outliers_json_shape(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["06037"]
    sids = [s["station_id"] for s in payload["stations"]]
    assert "s1" in sids and "s2" not in sids       # s2 has no flagged readings
    s1 = next(s for s in payload["stations"] if s["station_id"] == "s1")
    assert s1["flagged"] == 1 and s1["readings"] == 6
    assert s1["fraction"] == pytest.approx(1 / 6)


def test_outliers_limit(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json", "--limit", "1"])
    payload = json.loads(result.output)["06037"]
    assert len(payload["stations"]) == 1


def test_outliers_exclude_station(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json", "--exclude-station", "s1"])
    payload = json.loads(result.output)["06037"]
    assert "s1" not in [s["station_id"] for s in payload["stations"]]


def test_outliers_bad_bound_exits_nonzero(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--outlier-bound", "PM2.5:bad"])
    assert result.exit_code != 0


def test_outliers_no_flagged_message(tmp_path):
    df = pd.DataFrame([_row("2026-06-16T01:00:00", 10.0, "s2"),
                       _row("2026-06-16T02:00:00", 11.0, "s2")])
    store.write(tmp_path, "06037", df)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert "no data" in result.output
