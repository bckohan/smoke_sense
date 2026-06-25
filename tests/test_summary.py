from datetime import date

import pandas as pd

from smoke_sense import data, summary
from smoke_sense.data import Metric


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
