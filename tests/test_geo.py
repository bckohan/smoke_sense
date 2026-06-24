import json
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


def test_point_in_polygon_square():
    square = {"type": "Polygon",
              "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}
    assert geo.point_in_polygon(5, 5, square) is True
    assert geo.point_in_polygon(15, 5, square) is False


def test_point_in_polygon_multipolygon():
    geom = {"type": "MultiPolygon", "coordinates": [
        [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        [[[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]],
    ]}
    assert geo.point_in_polygon(0.5, 0.5, geom) is True
    assert geo.point_in_polygon(5.5, 5.5, geom) is True
    assert geo.point_in_polygon(3, 3, geom) is False


def test_point_in_polygon_hole():
    geom = {"type": "Polygon", "coordinates": [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
    ]}
    assert geo.point_in_polygon(1, 1, geom) is True
    assert geo.point_in_polygon(5, 5, geom) is False  # inside the hole


def test_point_in_polygon_bbox_but_outside_l_shape():
    l_shape = {"type": "Polygon", "coordinates": [
        [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10], [0, 0]],
    ]}
    assert geo.point_in_polygon(1, 1, l_shape) is True
    assert geo.point_in_polygon(8, 8, l_shape) is False  # in bbox, outside L


def test_county_polygon_lookup_and_unknown():
    table = pd.DataFrame({
        "county_fips": ["06037"],
        "geometry": [json.dumps(
            {"type": "Polygon",
             "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]})],
    }).astype({"county_fips": "string"})
    geom = geo.county_polygon("06037", table=table)
    assert geom["type"] == "Polygon"
    with pytest.raises(KeyError):
        geo.county_polygon("99999", table=table)


def test_county_contains_uses_geometry():
    square = {"type": "Polygon",
              "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}
    assert geo.county_contains("06037", lat=5, lon=5, geometry=square) is True
    assert geo.county_contains("06037", lat=50, lon=50, geometry=square) is False


def test_load_bundled_polygons_if_present():
    try:
        table = geo.load_polygon_table()
    except (FileNotFoundError, ModuleNotFoundError):
        pytest.skip("bundled county_polygons.parquet not built yet")
    assert {"county_fips", "geometry"} <= set(table.columns)
