from datetime import date

import pandas as pd

from smoke_sense import data, store
from smoke_sense.data import Pollutant


def _row(ts, value, agg, source="purpleair", station="s1"):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037",
        "station_id": station,
        "latitude": 34.0,
        "longitude": -118.2,
        "pollutant": Pollutant.PM2_5.value,
        "value": value,
        "unit": "µg/m³",
        "aqi": 10,
        "agg_window": agg,
        "source": source,
    }


def test_write_splits_by_day(tmp_path):
    df = pd.DataFrame([
        _row("2026-06-16T01:00:00", 1.0, 10),
        _row("2026-06-17T01:00:00", 2.0, 10),
    ])
    store.write(tmp_path, "06037", df)
    assert (tmp_path / "06037" / "2026-06-16.parquet").exists()
    assert (tmp_path / "06037" / "2026-06-17.parquet").exists()


def test_merge_day_finer_cadence_wins(tmp_path):
    coarse = pd.DataFrame([_row("2026-06-16T01:00:00", 9.9, 60)])
    store.write(tmp_path, "06037", coarse)
    fine = pd.DataFrame([_row("2026-06-16T01:00:00", 1.1, 10)])
    store.write(tmp_path, "06037", fine)
    back = data.read_parquet(tmp_path / "06037" / "2026-06-16.parquet")
    assert len(back) == 1
    assert back["agg_window"].iloc[0] == 10
    assert back["value"].iloc[0] == 1.1


def test_merge_day_keeps_coarse_when_no_finer(tmp_path):
    fine = pd.DataFrame([_row("2026-06-16T01:00:00", 1.1, 10)])
    store.write(tmp_path, "06037", fine)
    coarse = pd.DataFrame([_row("2026-06-16T01:00:00", 9.9, 60)])
    store.write(tmp_path, "06037", coarse)
    back = data.read_parquet(tmp_path / "06037" / "2026-06-16.parquet")
    assert len(back) == 1
    assert back["agg_window"].iloc[0] == 10  # finer kept


def test_coverage_reports_finest_per_day_source(tmp_path):
    df = pd.DataFrame([
        _row("2026-06-16T01:00:00", 1.0, 10, source="purpleair"),
        _row("2026-06-16T02:00:00", 2.0, 60, source="aqs", station="a1"),
    ])
    store.write(tmp_path, "06037", df)
    cov = store.coverage(tmp_path, "06037")
    assert cov[(date(2026, 6, 16), "purpleair")] == 10
    assert cov[(date(2026, 6, 16), "aqs")] == 60


def test_coverage_empty_when_no_county_dir(tmp_path):
    assert store.coverage(tmp_path, "99999") == {}


def test_read_range_reads_only_in_range_days(tmp_path):
    for d, v in [("2026-06-16", 1.0), ("2026-06-17", 2.0), ("2026-06-18", 3.0)]:
        store.write(tmp_path, "06037", pd.DataFrame([_row(f"{d}T01:00:00", v, 10)]))
    df = store.read_range(tmp_path, "06037", date(2026, 6, 17), date(2026, 6, 18))
    assert sorted(df["value"].tolist()) == [2.0, 3.0]


def test_read_range_empty_when_county_absent(tmp_path):
    df = store.read_range(tmp_path, "99999", date(2026, 6, 1), date(2026, 6, 2))
    assert df.empty
    assert list(df.columns) == list(data.COLUMNS)
