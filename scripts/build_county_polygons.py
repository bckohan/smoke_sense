"""Build the bundled county FIPS → polygon parquet from Census GeoJSON.

Run once (network required):
    uv run python scripts/build_county_polygons.py

Stores each county's GeoJSON geometry as a JSON string, consumed by
smoke_sense.geo for point-in-polygon sensor filtering. Pure json/pandas/requests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import requests

GEOJSON_URL = (
    "https://raw.githubusercontent.com/uscensusbureau/citysdk/master/"
    "v2/GeoJSON/500k/2019/county.json"
)
OUT = Path(__file__).resolve().parents[1] / "src/smoke_sense/_data/county_polygons.parquet"


def main() -> None:
    resp = requests.get(GEOJSON_URL, timeout=120)
    resp.raise_for_status()
    features = resp.json()["features"]

    rows = []
    for feat in features:
        props = feat["properties"]
        fips = props.get("GEOID") or f"{props['STATEFP']}{props['COUNTYFP']}"
        rows.append({"county_fips": fips, "geometry": json.dumps(feat["geometry"])})

    df = pd.DataFrame(rows).astype({"county_fips": "string"})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"wrote {len(df)} county polygons to {OUT}")


if __name__ == "__main__":
    main()
