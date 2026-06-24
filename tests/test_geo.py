from pathlib import Path

import pandas as pd
import pytest

from smoke_sense import geo
from smoke_sense.geo import BBox


@pytest.fixture
def table():
    return pd.DataFrame(
        {
            "county_fips": ["06037", "53033"],
            "min_lat": [33.7, 47.1],
            "min_lon": [-118.9, -122.5],
            "max_lat": [34.8, 47.8],
            "max_lon": [-117.6, -121.0],
        }
    ).astype({"county_fips": "string"})


def test_bbox_for_county_returns_bbox(table):
    box = geo.bbox_for_county("06037", table=table)
    assert box == BBox(min_lat=33.7, min_lon=-118.9, max_lat=34.8, max_lon=-117.6)


def test_bbox_for_county_unknown_raises(table):
    with pytest.raises(KeyError, match="99999"):
        geo.bbox_for_county("99999", table=table)


def test_bbox_from_geometry_polygon():
    geom = {
        "type": "Polygon",
        "coordinates": [[[-118.9, 33.7], [-117.6, 33.7], [-117.6, 34.8], [-118.9, 34.8]]],
    }
    assert geo.bbox_from_geometry(geom) == (33.7, -118.9, 34.8, -117.6)


def test_bbox_from_geometry_multipolygon():
    geom = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[-118.0, 33.0], [-117.0, 33.5]]],
            [[[-119.0, 34.0], [-118.5, 34.8]]],
        ],
    }
    assert geo.bbox_from_geometry(geom) == (33.0, -119.0, 34.8, -117.0)


def test_load_bundled_table_if_present():
    try:
        table = geo.load_bbox_table()
    except (FileNotFoundError, ModuleNotFoundError):
        pytest.skip("bundled county_bbox.parquet not built yet")
    assert {"county_fips", "min_lat", "min_lon", "max_lat", "max_lon"} <= set(table.columns)
