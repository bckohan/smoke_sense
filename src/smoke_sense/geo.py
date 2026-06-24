"""County FIPS → bounding box resolution.

A small bundled lookup table avoids a heavy GIS dependency. PurpleAir queries
by bounding box, so this maps a county FIPS to its geographic extent.
"""

from __future__ import annotations

import importlib.resources as resources
from dataclasses import dataclass

import pandas as pd

_BUNDLED = "county_bbox.parquet"


@dataclass(frozen=True)
class BBox:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float


def load_bbox_table() -> pd.DataFrame:
    """Load the bundled county-bbox parquet shipped inside the package."""
    ref = resources.files("smoke_sense._data").joinpath(_BUNDLED)
    with resources.as_file(ref) as path:
        return pd.read_parquet(path).astype({"county_fips": "string"})


def bbox_for_county(fips: str, table: pd.DataFrame | None = None) -> BBox:
    """Return the bounding box for a 5-digit county FIPS.

    Raises KeyError if the FIPS is not present in the lookup table.
    """
    if table is None:
        table = load_bbox_table()
    rows = table.loc[table["county_fips"] == fips]
    if rows.empty:
        raise KeyError(f"no bounding box for county FIPS {fips}")
    r = rows.iloc[0]
    return BBox(
        min_lat=float(r["min_lat"]),
        min_lon=float(r["min_lon"]),
        max_lat=float(r["max_lat"]),
        max_lon=float(r["max_lon"]),
    )


def bbox_from_geometry(geometry: dict) -> tuple[float, float, float, float]:
    """Compute (min_lat, min_lon, max_lat, max_lon) from a GeoJSON geometry."""
    lons: list[float] = []
    lats: list[float] = []

    def walk(coords) -> None:
        if (
            len(coords) == 2
            and isinstance(coords[0], (int, float))
            and isinstance(coords[1], (int, float))
        ):
            lons.append(float(coords[0]))
            lats.append(float(coords[1]))
        else:
            for item in coords:
                walk(item)

    walk(geometry["coordinates"])
    return (min(lats), min(lons), max(lats), max(lons))
