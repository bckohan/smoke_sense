"""Common tidy data format for air-quality observations.

One row per (timestamp, station, metric) observation. This schema is the
contract every provider produces and every downstream consumer reads.
"""

from pathlib import Path
from typing import Annotated

import pandas as pd
from enum_properties import StrEnumProperties, Symmetric

# NOTE: ``from __future__ import annotations`` (PEP 563) is intentionally NOT
# used here. enum-properties reads the ``Annotated[..., Symmetric(...)]``
# directives from ``__annotations__`` at class-creation time; PEP 563 would
# turn those into strings and silently drop the Symmetric markers, breaking the
# case-insensitive lookup. All annotations in this module use native 3.10+
# syntax, so the future import is unnecessary.


class Metric(StrEnumProperties):
    """A measured quantity with its canonical unit and AQI eligibility.

    Provider-specific codes/fields live on the providers, not here.
    """

    label: Annotated[str, Symmetric(case_fold=True)]
    unit: str
    has_aqi: bool

    #          value          label          unit      has_aqi
    PM2_5      = "PM2.5",     "PM2.5",     "µg/m³",  True
    PM2_5_CF1  = "PM2.5_CF1", "PM2.5_CF1", "µg/m³",  False
    PM2_5_ATM  = "PM2.5_ATM", "PM2.5_ATM", "µg/m³",  False
    PM10       = "PM10",      "PM10",      "µg/m³",  True
    PM10_CF1   = "PM10_CF1",  "PM10_CF1",  "µg/m³",  False
    PM10_ATM   = "PM10_ATM",  "PM10_ATM",  "µg/m³",  False
    PM1_0_CF1  = "PM1.0_CF1", "PM1.0_CF1", "µg/m³",  False
    PM1_0_ATM  = "PM1.0_ATM", "PM1.0_ATM", "µg/m³",  False
    O3         = "O3",        "O3",        "ppm",    True
    CO         = "CO",        "CO",        "ppm",    False
    SO2        = "SO2",       "SO2",       "ppb",    False
    NO2        = "NO2",       "NO2",       "ppb",    False
    PB         = "Pb",        "Pb",        "µg/m³",  False
    TEMP       = "temperature", "temperature", "°C", False
    RH         = "humidity",    "humidity",    "%",  False
    PRESSURE   = "pressure",    "pressure",    "hPa", False
    WIND_SPEED = "wind_speed",  "wind_speed",  "m/s", False
    WIND_DIR   = "wind_dir",    "wind_dir",    "deg", False
    WIND_SPEED_80M = "wind_speed_80m", "wind_speed_80m", "m/s", False
    WIND_DIR_80M   = "wind_dir_80m",   "wind_dir_80m",   "deg", False
    VOC        = "VOC",        "VOC",        "iaq",  False


AQI_METRICS: frozenset[Metric] = frozenset(m for m in Metric if m.has_aqi)


# Canonical column -> pandas dtype. Single source of truth for the schema.
COLUMNS: dict[str, str] = {
    "timestamp": "datetime64[ns, UTC]",
    "county_fips": "string",
    "station_id": "string",
    "metric": "category",
    "value": "float64",
    "aqi": "Int16",
    "agg_window": "Int16",
    "source": "category",
}

# Fields that must never be null in a valid frame.
REQUIRED_NON_NULL: list[str] = [
    "timestamp",
    "county_fips",
    "station_id",
    "metric",
    "value",
    "agg_window",
    "source",
]


def empty_frame() -> pd.DataFrame:
    """Return an empty frame with the canonical columns and dtypes."""
    return pd.DataFrame(
        {name: pd.Series(dtype=dtype) for name, dtype in COLUMNS.items()}
    )


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a frame to the canonical schema and assert required invariants.

    Raises ValueError on missing columns or nulls in required fields.
    """
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")

    out = df[list(COLUMNS)].copy()
    for name, dtype in COLUMNS.items():
        if dtype.startswith("datetime64"):
            # Parse to tz-aware datetimes, then cast to the canonical
            # resolution declared in COLUMNS. pandas >= 3.0 infers the
            # resolution from the input (e.g. us), so we pin it explicitly
            # to keep the schema's nanosecond contract stable.
            out[name] = pd.to_datetime(out[name], utc=True).astype(dtype)
        else:
            out[name] = out[name].astype(dtype)

    null_cols = [c for c in REQUIRED_NON_NULL if out[c].isna().any()]
    if null_cols:
        raise ValueError(f"null values in required columns: {null_cols}")

    return out


def write_parquet(df: pd.DataFrame, path: str | Path) -> None:
    """Validate and persist a frame to Parquet (zstd), creating parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    validate(df).to_parquet(path, index=False, compression="zstd")


def read_parquet(path: str | Path) -> pd.DataFrame:
    """Read a Parquet file and coerce it back to the canonical schema."""
    return validate(pd.read_parquet(path))
