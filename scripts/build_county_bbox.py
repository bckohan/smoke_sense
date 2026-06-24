"""Build the bundled county FIPS → bbox parquet from Census GeoJSON.

Run once (network required):
    uv run python scripts/build_county_bbox.py

Downloads the Census cartographic boundary file for counties (500k), computes
each county's bounding box from its geometry, and writes the parquet consumed
by smoke_sense.geo. Uses only requests + json + pandas (no GIS libraries).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from smoke_sense.geo import bbox_from_geometry

# Census cartographic boundary, counties, 1:500k, GeoJSON.
GEOJSON_URL = (
    "https://raw.githubusercontent.com/uscensusbureau/citysdk/master/"
    "v2/GeoJSON/500k/2019/county.json"
)
OUT = Path(__file__).resolve().parents[1] / "src/smoke_sense/_data/county_bbox.parquet"


def main() -> None:
    resp = requests.get(GEOJSON_URL, timeout=120)
    resp.raise_for_status()
    features = resp.json()["features"]

    rows = []
    for feat in features:
        props = feat["properties"]
        # Census GeoJSON exposes the 5-digit county FIPS as GEOID
        # (state FIPS = STATEFP, county FIPS = COUNTYFP).
        fips = props.get("GEOID") or f"{props['STATEFP']}{props['COUNTYFP']}"
        min_lat, min_lon, max_lat, max_lon = bbox_from_geometry(feat["geometry"])
        rows.append(
            {
                "county_fips": fips,
                "min_lat": min_lat,
                "min_lon": min_lon,
                "max_lat": max_lat,
                "max_lon": max_lon,
            }
        )

    df = pd.DataFrame(rows).astype({"county_fips": "string"})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"wrote {len(df)} counties to {OUT}")


if __name__ == "__main__":
    main()
