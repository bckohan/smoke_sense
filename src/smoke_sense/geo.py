"""County FIPS → bounding box resolution.

A small bundled lookup table avoids a heavy GIS dependency. PurpleAir queries
by bounding box, so this maps a county FIPS to its geographic extent.
"""

from __future__ import annotations

import json
import importlib.resources as resources
from dataclasses import dataclass

import pandas as pd

_BUNDLED = "county_bbox.parquet"
_BUNDLED_POLYGONS = "county_polygons.parquet"


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


def load_polygon_table() -> pd.DataFrame:
    """Load the bundled county-polygon parquet shipped inside the package."""
    ref = resources.files("smoke_sense._data").joinpath(_BUNDLED_POLYGONS)
    with resources.as_file(ref) as path:
        return pd.read_parquet(path).astype({"county_fips": "string"})


def county_polygon(fips: str, table: pd.DataFrame | None = None) -> dict:
    """Return the GeoJSON geometry for a county FIPS.

    Raises KeyError if the FIPS is not present in the polygon table.
    """
    if table is None:
        table = load_polygon_table()
    rows = table.loc[table["county_fips"] == fips]
    if rows.empty:
        raise KeyError(f"no polygon for county FIPS {fips}")
    return json.loads(rows.iloc[0]["geometry"])


def _rings(geometry: dict):
    """Yield each linear ring ([[lon, lat], ...]) of a Polygon/MultiPolygon."""
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    if gtype == "Polygon":
        yield from coords
    elif gtype == "MultiPolygon":
        for polygon in coords:
            yield from polygon
    else:
        raise ValueError(f"unsupported geometry type: {gtype}")


def point_in_polygon(lon: float, lat: float, geometry: dict) -> bool:
    """Even-odd ray-casting across all rings (interior holes count as outside)."""
    inside = False
    for ring in _rings(geometry):
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if ((yi > lat) != (yj > lat)) and (
                lon < (xj - xi) * (lat - yi) / (yj - yi) + xi
            ):
                inside = not inside
            j = i
    return inside


def county_contains(fips: str, lat: float, lon: float,
                    geometry: dict | None = None) -> bool:
    """Whether (lat, lon) lies within the county's polygon."""
    if geometry is None:
        geometry = county_polygon(fips)
    return point_in_polygon(lon, lat, geometry)
