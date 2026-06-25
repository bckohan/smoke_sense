"""Per-day Parquet store with finer-cadence-wins merge and coverage queries."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import data

# Identity of an observation; finer agg_window wins on conflict.
_IDENTITY = ["timestamp", "station_id", "metric", "source"]

# Station metadata columns split out of provider frames into stations.parquet.
_STATION_COLS = ["station_id", "source", "latitude", "longitude"]


def day_path(data_dir: str | Path, fips: str, day: date) -> Path:
    return Path(data_dir) / fips / f"{day.isoformat()}.parquet"


def stations_path(data_dir: str | Path, fips: str) -> Path:
    return Path(data_dir) / fips / "stations.parquet"


def _merge_stations(data_dir: str | Path, fips: str, df: pd.DataFrame) -> None:
    """Extract station coordinates into stations.parquet, deduped per station."""
    if not {"latitude", "longitude"} <= set(df.columns):
        return
    new = df[_STATION_COLS].drop_duplicates()
    path = stations_path(data_dir, fips)
    frames = [pd.read_parquet(path)] if path.exists() else []
    frames.append(new)
    merged = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["station_id", "source"], keep="last"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.astype({"station_id": "string", "source": "string"}).to_parquet(
        path, index=False, compression="zstd"
    )


def _dedup_finer_wins(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per identity, preferring the finest agg_window (0 finest)."""
    ordered = df.sort_values("agg_window", kind="stable")
    return ordered.drop_duplicates(subset=_IDENTITY, keep="first")


def merge_day(data_dir: str | Path, fips: str, day: date, df: pd.DataFrame) -> None:
    """Merge `df` into the day file, keeping the finer cadence on conflict."""
    path = day_path(data_dir, fips, day)
    frames = []
    if path.exists():
        frames.append(data.read_parquet(path))
    frames.append(df)
    combined = _dedup_finer_wins(pd.concat(frames, ignore_index=True))
    data.write_parquet(combined, path)


def write(data_dir: str | Path, fips: str, df: pd.DataFrame) -> None:
    """Validate `df`, split it by UTC day, and merge each day into its file."""
    if df.empty:
        return
    _merge_stations(data_dir, fips, df)
    df = data.validate(df)
    days = df["timestamp"].dt.tz_convert("UTC").dt.date
    for day, group in df.groupby(days):
        merge_day(data_dir, fips, day, group)


def coverage(data_dir: str | Path, fips: str) -> dict[tuple[date, str], int]:
    """Finest `agg_window` already stored per (day, source) for a county."""
    county_dir = Path(data_dir) / fips
    result: dict[tuple[date, str], int] = {}
    if not county_dir.exists():
        return result
    for f in sorted(county_dir.glob("*.parquet")):
        if f.name == "stations.parquet":
            continue
        day = date.fromisoformat(f.stem)
        df = data.read_parquet(f)
        for source, group in df.groupby("source", observed=True):
            result[(day, str(source))] = int(group["agg_window"].min())
    return result


def read_range(data_dir: str | Path, fips: str, start: date, end: date) -> pd.DataFrame:
    """Concatenate the county's day files for dates in [start, end].

    Reads {data_dir}/{fips}/{day}.parquet for each day in the inclusive range
    that has a file, concatenates them, and returns the validated frame
    restricted to timestamps in [start 00:00 UTC, (end + 1 day) 00:00 UTC).
    Returns an empty schema frame if nothing is present.
    """
    frames = []
    day = start
    while day <= end:
        path = day_path(data_dir, fips, day)
        if path.exists():
            frames.append(data.read_parquet(path))
        day += timedelta(days=1)
    if not frames:
        return data.empty_frame()
    df = pd.concat(frames, ignore_index=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    window = (df["timestamp"] >= start_ts) & (df["timestamp"] < end_ts)
    return data.validate(df[window])
