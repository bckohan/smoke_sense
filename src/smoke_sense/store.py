"""Per-day Parquet store with finer-cadence-wins merge and coverage queries."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from . import data

# Identity of an observation; finer agg_window wins on conflict.
_IDENTITY = ["timestamp", "station_id", "pollutant", "source"]


def day_path(data_dir: str | Path, fips: str, day: date) -> Path:
    return Path(data_dir) / fips / f"{day.isoformat()}.parquet"


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
        day = date.fromisoformat(f.stem)
        df = data.read_parquet(f)
        for source, group in df.groupby("source", observed=True):
            result[(day, str(source))] = int(group["agg_window"].min())
    return result
