import importlib.util
from pathlib import Path

import pandas as pd

from smoke_sense import data, store

_SPEC = importlib.util.spec_from_file_location(
    "migrate_store",
    Path(__file__).resolve().parents[1] / "scripts" / "migrate_store.py",
)
migrate_store = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migrate_store)


def test_migrate_old_schema_file(tmp_path):
    old = pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-06-16T01:00:00", tz="UTC"),
        "county_fips": "06037", "station_id": "s1",
        "latitude": 34.0, "longitude": -118.2,
        "pollutant": "PM2.5", "value": 12.0, "unit": "µg/m³",
        "aqi": 52, "agg_window": 10, "source": "purpleair",
    }])
    day = tmp_path / "06037" / "2026-06-16.parquet"
    day.parent.mkdir(parents=True)
    old.to_parquet(day, index=False)

    migrate_store.migrate_file(day, tmp_path, "06037")

    new = data.read_parquet(day)
    assert "metric" in new.columns
    assert "unit" not in new.columns and "latitude" not in new.columns
    assert new["metric"].iloc[0] == "PM2.5"
    stations = pd.read_parquet(store.stations_path(tmp_path, "06037"))
    assert stations["station_id"].tolist() == ["s1"]
