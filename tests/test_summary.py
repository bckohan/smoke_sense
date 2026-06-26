import json
from datetime import date

import pandas as pd
from typer.testing import CliRunner

from smoke_sense import data, store, summary
from smoke_sense.bin import app
from smoke_sense.data import Metric

runner = CliRunner()


def _row(ts, metric, value, aqi, source, agg, station):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037",
        "station_id": station,
        "metric": metric,
        "value": value,
        "aqi": aqi,
        "agg_window": agg,
        "source": source,
    }


def _frame(rows):
    return data.validate(pd.DataFrame(rows))


def test_summarize_empty_frame():
    s = summary.summarize(data.empty_frame(), date(2026, 6, 1), date(2026, 6, 3))
    assert s["coverage"]["total_days"] == 3
    assert s["coverage"]["days_present"] == 0
    assert s["coverage"]["days_missing"] == ["2026-06-01", "2026-06-02", "2026-06-03"]
    assert s["coverage"]["total_rows"] == 0
    assert s["coverage"]["first_timestamp"] is None
    assert s["breakdown"] == []
    assert s["metrics"] == []


def test_summarize_coverage_breakdown_and_stats():
    rows = [
        _row("2026-06-01T01:00:00", Metric.PM2_5.value, 10.0, 50, "purpleair", 10, "s1"),
        _row("2026-06-01T02:00:00", Metric.PM2_5.value, 20.0, 70, "purpleair", 10, "s2"),
        _row("2026-06-03T01:00:00", Metric.O3.value, 0.04, None, "aqs", 60, "a1"),
    ]
    s = summary.summarize(_frame(rows), date(2026, 6, 1), date(2026, 6, 3))

    assert s["coverage"]["total_days"] == 3
    assert s["coverage"]["days_present"] == 2
    assert s["coverage"]["days_missing"] == ["2026-06-02"]
    assert s["coverage"]["total_rows"] == 3

    combos = {(b["source"], b["metric"], b["agg_window"]) for b in s["breakdown"]}
    assert combos == {("purpleair", "PM2.5", 10), ("aqs", "O3", 60)}

    pm = next(p for p in s["metrics"] if p["metric"] == "PM2.5")
    assert pm["stations"] == 2
    assert pm["sources"] == ["purpleair"]
    assert pm["value"]["min"] == 10.0
    assert pm["value"]["max"] == 20.0
    assert pm["aqi"]["min"] == 50
    assert pm["aqi"]["max"] == 70

    o3 = next(p for p in s["metrics"] if p["metric"] == "O3")
    assert o3["aqi"] is None  # the only O3 row had a null AQI


# ---------------------------------------------------------------------------
# Task 2: filtered field + CLI outlier integration
# ---------------------------------------------------------------------------

def _cli_row(ts, metric, value, station, aqi=pd.NA, source="purpleair", agg=10):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037", "station_id": station,
        "latitude": 34.0, "longitude": -118.2,
        "metric": metric.value, "value": value,
        "aqi": aqi, "agg_window": agg, "source": source,
    }


def test_summarize_filtered_field():
    df = pd.DataFrame([_cli_row("2026-06-16T01:00:00", Metric.PM2_5, 10.0, "s1")])
    out = summary.summarize(df, date(2026, 6, 16), date(2026, 6, 16),
                            filtered={"PM2.5": 3})
    pm = next(m for m in out["metrics"] if m["metric"] == "PM2.5")
    assert pm["filtered"] == 3


def test_summarize_filtered_default_zero():
    df = pd.DataFrame([_cli_row("2026-06-16T01:00:00", Metric.PM2_5, 10.0, "s1")])
    out = summary.summarize(df, date(2026, 6, 16), date(2026, 6, 16))
    pm = next(m for m in out["metrics"] if m["metric"] == "PM2.5")
    assert pm["filtered"] == 0


def _seed_with_garbage(tmp_path):
    rows = [_cli_row(f"2026-06-16T0{i}:00:00", Metric.PM2_5, v, "s1")
            for i, v in enumerate([10, 11, 9, 8, 12])]
    rows.append(_cli_row("2026-06-16T09:00:00", Metric.PM2_5, -999.0, "s1"))  # garbage
    store.write(tmp_path, "06037", pd.DataFrame(rows))


def test_summary_cli_filters_by_default(tmp_path):
    _seed_with_garbage(tmp_path)
    result = runner.invoke(app, [
        "summary", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    data_out = json.loads(result.output)["06037"]
    pm = next(m for m in data_out["metrics"] if m["metric"] == "PM2.5")
    assert pm["filtered"] == 1
    assert pm["value"]["min"] >= 0  # the -999 was removed


def test_summary_cli_no_filter_keeps_garbage(tmp_path):
    _seed_with_garbage(tmp_path)
    result = runner.invoke(app, [
        "summary", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json", "--no-outlier-filter"])
    assert result.exit_code == 0, result.output
    pm = next(m for m in json.loads(result.output)["06037"]["metrics"]
              if m["metric"] == "PM2.5")
    assert pm["filtered"] == 0
    assert pm["value"]["min"] == -999.0


def test_summary_cli_fully_removed_metric_absent(tmp_path):
    """When ALL rows of a metric are dropped by the outlier filter, that metric
    must have NO entry in the summary ``metrics`` list (documented contract)."""
    # healthy PM2.5 rows — all within [0, 1000]
    healthy = [
        _cli_row(f"2026-06-16T0{i}:00:00", Metric.PM2_5, v, "s1")
        for i, v in enumerate([10, 11, 9, 8, 12])
    ]
    # fully-bad PM10 rows — all outside physical bounds [0, 2000]
    bad_pm10 = [
        _cli_row("2026-06-16T01:00:00", Metric.PM10, -5.0, "s1"),
        _cli_row("2026-06-16T02:00:00", Metric.PM10, -10.0, "s1"),
        _cli_row("2026-06-16T03:00:00", Metric.PM10, 5000.0, "s1"),
    ]
    store.write(tmp_path, "06037", pd.DataFrame(healthy + bad_pm10))

    result = runner.invoke(app, [
        "summary", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json",
    ])
    assert result.exit_code == 0, result.output
    metrics = json.loads(result.output)["06037"]["metrics"]
    metric_names = [m["metric"] for m in metrics]
    assert "PM2.5" in metric_names, "healthy PM2.5 must appear in metrics"
    assert "PM10" not in metric_names, "fully-filtered PM10 must be absent from metrics"
