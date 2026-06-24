"""Common tidy data format for air-quality observations.

One row per (timestamp, station, pollutant) observation. This schema is the
contract every provider produces and every downstream consumer reads.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import pandas as pd


class Pollutant(str, Enum):
    """Smoke-relevant pollutants with their AQS parameter codes and units."""

    PM2_5 = "PM2.5"
    PM10 = "PM10"
    O3 = "O3"

    @property
    def aqs_code(self) -> str:
        return _AQS_CODES[self]

    @property
    def unit(self) -> str:
        return _UNITS[self]

    @classmethod
    def from_str(cls, value: str) -> "Pollutant":
        key = value.strip().upper().replace("PM2_5", "PM2.5")
        for member in cls:
            if member.value.upper() == key:
                return member
        raise ValueError(f"unknown pollutant: {value!r}")


_AQS_CODES: dict[Pollutant, str] = {
    Pollutant.PM2_5: "88101",
    Pollutant.PM10: "81102",
    Pollutant.O3: "44201",
}

_UNITS: dict[Pollutant, str] = {
    Pollutant.PM2_5: "µg/m³",
    Pollutant.PM10: "µg/m³",
    Pollutant.O3: "ppm",
}

# Canonical column -> pandas dtype. Single source of truth for the schema.
COLUMNS: dict[str, str] = {
    "timestamp": "datetime64[ns, UTC]",
    "county_fips": "string",
    "station_id": "string",
    "latitude": "float64",
    "longitude": "float64",
    "pollutant": "category",
    "value": "float64",
    "unit": "category",
    "aqi": "Int16",
    "source": "category",
}

# Fields that must never be null in a valid frame.
REQUIRED_NON_NULL: list[str] = [
    "timestamp",
    "county_fips",
    "station_id",
    "pollutant",
    "value",
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
    """Validate and persist a frame to Parquet, creating parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    validate(df).to_parquet(path, index=False)


def read_parquet(path: str | Path) -> pd.DataFrame:
    """Read a Parquet file and coerce it back to the canonical schema."""
    return validate(pd.read_parquet(path))
