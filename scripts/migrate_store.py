"""One-off: migrate existing day files to the metric/station-table schema.

Run once: uv run python scripts/migrate_store.py [DATA_DIR]   (default ./data)

For each {data_dir}/{fips}/{date}.parquet with the old schema (pollutant + lat/lon
[+ unit]): rename pollutant->metric, drop unit, split (station_id, source, lat, lon)
into {fips}/stations.parquet, and rewrite the day file (zstd, new schema). Idempotent:
files already in the new schema are skipped.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from smoke_sense import store


def migrate_file(path: Path, data_dir: Path, fips: str) -> bool:
    df = pd.read_parquet(path)
    if "pollutant" not in df.columns and "metric" in df.columns:
        return False  # already migrated
    if "pollutant" in df.columns:
        df = df.rename(columns={"pollutant": "metric"})
    df = df.drop(columns=[c for c in ("unit",) if c in df.columns])
    # Remove the old-schema file first: store.write would otherwise read it back
    # in merge_day and fail validation. The in-memory df holds all its rows.
    path.unlink()
    store.write(data_dir, fips, df)  # extracts station metadata, drops lat/lon
    return True


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("./data")
    migrated = 0
    for county_dir in sorted(p for p in data_dir.glob("*") if p.is_dir()):
        fips = county_dir.name
        for f in sorted(county_dir.glob("*.parquet")):
            if f.name == "stations.parquet":
                continue
            if migrate_file(f, data_dir, fips):
                migrated += 1
                print(f"migrated {f}")
    print(f"done: {migrated} file(s) migrated")


if __name__ == "__main__":
    main()
