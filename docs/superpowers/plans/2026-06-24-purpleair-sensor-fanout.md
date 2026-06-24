# PurpleAir Sensor Fan-out Mitigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut and survive the PurpleAir per-sensor history fan-out by filtering the sensor list (outdoor + active-in-window + inside county polygon) before any history call, and by handling HTTP 429 with backoff.

**Architecture:** `geo.py` gains bundled county polygons + pure-Python point-in-polygon. `PurpleAirProvider` routes all GETs through a `_get` helper with 429 backoff, and filters sensors (server-side `location_type=0`, client-side staleness overlap + polygon membership) before fetching history.

**Tech Stack:** Python 3.12, pandas, requests, pytest.

**Reference spec:** `docs/superpowers/specs/2026-06-24-purpleair-sensor-fanout-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/smoke_sense/geo.py` | + `load_polygon_table`, `county_polygon`, `point_in_polygon`, `county_contains` |
| `scripts/build_county_polygons.py` | one-off builder: Census GeoJSON → county_polygons.parquet |
| `src/smoke_sense/_data/county_polygons.parquet` | bundled county FIPS → geometry |
| `src/smoke_sense/providers/purpleair.py` | `_get` 429 backoff; sensor filtering in `fetch` |

---

### Task 0: County polygons in `geo.py`

**Goal:** Bundle county polygons and add pure-Python point-in-polygon membership.

**Files:**
- Modify: `src/smoke_sense/geo.py`
- Create: `scripts/build_county_polygons.py`
- Create: `src/smoke_sense/_data/county_polygons.parquet` (built)
- Modify: `tests/test_geo.py`

**Acceptance Criteria:**
- [ ] `point_in_polygon` is correct for Polygon, MultiPolygon, holes, and bbox-but-outside cases
- [ ] `county_polygon` returns geometry from a table and raises `KeyError` for unknown FIPS
- [ ] `county_polygons.parquet` is built and `load_polygon_table` reads it

**Verify:** `uv run pytest tests/test_geo.py -v` → all pass

**Steps:**

- [ ] **Step 1: Append tests to `tests/test_geo.py`**

Add `import json` at the top if not present, then append:
```python
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
```

- [ ] **Step 2: Run, confirm FAIL.** `uv run pytest tests/test_geo.py -v`

- [ ] **Step 3: Add to `src/smoke_sense/geo.py`**

Add `import json` near the top (after `from __future__ import annotations`). Add a second bundled-name constant next to `_BUNDLED`:
```python
_BUNDLED = "county_bbox.parquet"
_BUNDLED_POLYGONS = "county_polygons.parquet"
```
Append these functions at the end of the module:
```python
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
```

- [ ] **Step 4: Create `scripts/build_county_polygons.py`**

```python
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
```

- [ ] **Step 5: Build the polygon parquet**

Run:
```bash
uv run python scripts/build_county_polygons.py
```
Expected: "wrote NNNN county polygons to .../county_polygons.parquet" (~3,233).

- [ ] **Step 6: Confirm packaging covers it**

Check `pyproject.toml` `[tool.setuptools.package-data]`. If it lists `"smoke_sense._data" = ["*.parquet"]`, the new file is already included (no change). If it names `county_bbox.parquet` explicitly, change it to the `["*.parquet"]` glob. Verify with:
```bash
uv build --wheel 2>/dev/null && python -c "import zipfile,glob; z=zipfile.ZipFile(sorted(glob.glob('dist/*.whl'))[-1]); print([n for n in z.namelist() if n.endswith('.parquet')])"
```
Expected: both `county_bbox.parquet` and `county_polygons.parquet` listed.

- [ ] **Step 7: Run, confirm PASS.** `uv run pytest tests/test_geo.py -v` then `uv run pytest -q`.

- [ ] **Step 8: Stage.** `git add src/smoke_sense/geo.py scripts/build_county_polygons.py src/smoke_sense/_data/county_polygons.parquet tests/test_geo.py pyproject.toml`

---

### Task 1: PurpleAir 429 backoff (`_get`)

**Goal:** Route every PurpleAir GET through a `_get` helper that retries on HTTP 429 with backoff.

**Files:**
- Modify: `src/smoke_sense/providers/purpleair.py`
- Modify: `tests/test_providers_purpleair.py`

**Acceptance Criteria:**
- [ ] `_get` retries on 429 honoring `Retry-After`, else exponential backoff; raises after max retries
- [ ] `_list_sensors` and `_get_history` route through `_get`; 400s still propagate to the chunker

**Verify:** `uv run pytest tests/test_providers_purpleair.py -v` → all pass

**Steps:**

- [ ] **Step 1: Add `import time`** to `src/smoke_sense/providers/purpleair.py` (top, with the other stdlib imports, before `import pandas as pd`):
```python
import time
import warnings
from datetime import date, datetime, timedelta, timezone
```

- [ ] **Step 2: Add the `_get` helper** to `PurpleAirProvider` (place it right after `_headers`):
```python
    _MAX_RETRIES = 5

    def _get(self, url: str, params: dict) -> dict:
        """GET with retry on HTTP 429 (honor Retry-After, else exp. backoff)."""
        delay = 2.0
        for attempt in range(self._MAX_RETRIES + 1):
            resp = self.session.get(
                url, headers=self._headers(), params=params, timeout=120)
            if resp.status_code == 429 and attempt < self._MAX_RETRIES:
                header = resp.headers.get("Retry-After")
                try:
                    wait = float(header) if header is not None else delay
                except (TypeError, ValueError):
                    wait = delay
                time.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 3: Route `_list_sensors` through `_get`.** Replace its body with:
```python
    def _list_sensors(self, bbox) -> list[dict]:
        payload = self._get(
            _SENSORS_URL,
            {
                "fields": "latitude,longitude",
                "nwlng": bbox.min_lon, "nwlat": bbox.max_lat,
                "selng": bbox.max_lon, "selat": bbox.min_lat,
            },
        )
        fields = payload["fields"]
        return [dict(zip(fields, row)) for row in payload["data"]]
```

- [ ] **Step 4: Route `_get_history` through `_get`.** Replace the `resp = self.session.get(...)`, `resp.raise_for_status()`, `return resp.json()` tail of `_get_history` with a single `_get` call, so the method reads:
```python
    def _get_history(self, sensor_id, start: date, end: date, average: int,
                     fields: list[str]) -> dict:
        start_ts = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        end_ts = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
        end_ts = min(end_ts, datetime.now(timezone.utc))
        return self._get(
            _HISTORY_URL.format(sensor_id=sensor_id),
            {
                "start_timestamp": int(start_ts.timestamp()),
                "end_timestamp": int(end_ts.timestamp()),
                "average": average,
                "fields": ",".join(fields),
            },
        )
```

- [ ] **Step 5: Update the test fake to carry headers.** In `tests/test_providers_purpleair.py`, replace the `_FakeResp` class with:
```python
class _FakeResp:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self._payload
```

- [ ] **Step 6: Add backoff tests** to `tests/test_providers_purpleair.py`:
```python
def test_get_retries_on_429_with_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr("smoke_sense.providers.purpleair.time.sleep", slept.append)
    responses = [
        _FakeResp({}, status_code=429, headers={"Retry-After": "7"}),
        _FakeResp({"ok": True}),
    ]

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return responses.pop(0)

    provider = PurpleAirProvider(purpleair_key="k", session=S())
    assert provider._get("https://x", {}) == {"ok": True}
    assert slept == [7.0]


def test_get_backoff_without_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr("smoke_sense.providers.purpleair.time.sleep", slept.append)
    responses = [
        _FakeResp({}, status_code=429),
        _FakeResp({}, status_code=429),
        _FakeResp({"ok": True}),
    ]

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return responses.pop(0)

    provider = PurpleAirProvider(purpleair_key="k", session=S())
    assert provider._get("https://x", {}) == {"ok": True}
    assert slept == [2.0, 4.0]  # exponential base 2, doubling


def test_get_raises_after_max_retries(monkeypatch):
    monkeypatch.setattr("smoke_sense.providers.purpleair.time.sleep", lambda *_: None)

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return _FakeResp({}, status_code=429)

    provider = PurpleAirProvider(purpleair_key="k", session=S())
    with pytest.raises(requests.HTTPError):
        provider._get("https://x", {})
```

- [ ] **Step 7: Run, confirm PASS.** `uv run pytest tests/test_providers_purpleair.py -v` then `uv run pytest -q`. The existing chunking/time_stamp tests still pass (their fake raises `HTTPError` on over-range, which propagates through `_get` to `_history_chunked` unchanged).

- [ ] **Step 8: Stage.** `git add src/smoke_sense/providers/purpleair.py tests/test_providers_purpleair.py`

---

### Task 2: PurpleAir sensor filtering (outdoor + staleness + polygon)

**Goal:** Trim the sensor list to outdoor, active-in-window, in-county sensors before any history call.

**Files:**
- Modify: `src/smoke_sense/providers/purpleair.py`
- Modify: `tests/test_providers_purpleair.py`

**Acceptance Criteria:**
- [ ] `_list_sensors` requests `location_type=0` and the `last_seen`/`date_created` fields
- [ ] `_filter_sensors` keeps only sensors whose `[date_created, last_seen]` overlaps `[start, end]` and lie inside the polygon
- [ ] `fetch` filters before fetching history; empty survivor set → `empty_frame()`

**Verify:** `uv run pytest tests/test_providers_purpleair.py -v` → all pass

**Steps:**

- [ ] **Step 1: Extend the geo import** in `src/smoke_sense/providers/purpleair.py`:
```python
from ..geo import bbox_for_county, county_polygon, point_in_polygon
```

- [ ] **Step 2: Add outdoor + fields to `_list_sensors`.** Replace its params so the method reads:
```python
    def _list_sensors(self, bbox) -> list[dict]:
        payload = self._get(
            _SENSORS_URL,
            {
                "fields": "latitude,longitude,last_seen,date_created",
                "location_type": 0,
                "nwlng": bbox.min_lon, "nwlat": bbox.max_lat,
                "selng": bbox.max_lon, "selat": bbox.min_lat,
            },
        )
        fields = payload["fields"]
        return [dict(zip(fields, row)) for row in payload["data"]]
```

- [ ] **Step 3: Add `_filter_sensors`** (place after `_list_sensors`):
```python
    @staticmethod
    def _filter_sensors(sensors: list[dict], geometry: dict,
                        start: date, end: date) -> list[dict]:
        """Keep outdoor sensors active in the window and inside the polygon.

        A sensor's active interval [date_created, last_seen] must overlap the
        requested [start, end] window, and its location must be in the county.
        """
        start_ts = int(
            datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp()
        )
        end_ts = int(
            (datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
             + timedelta(days=1)).timestamp()
        )
        kept = []
        for s in sensors:
            last_seen = s.get("last_seen")
            created = s.get("date_created")
            if last_seen is None or created is None:
                continue
            if not (last_seen >= start_ts and created <= end_ts):
                continue
            if not point_in_polygon(s["longitude"], s["latitude"], geometry):
                continue
            kept.append(s)
        return kept
```

- [ ] **Step 4: Wire filtering into `fetch`.** Replace the bbox/sensors lines so `fetch` reads (only the sensor-acquisition section changes; the per-sensor loop is unchanged):
```python
        average = self.resolve_cadence(cadence)
        bbox = bbox_for_county(county_fips)
        sensors = self._list_sensors(bbox)
        geometry = county_polygon(county_fips)
        sensors = self._filter_sensors(sensors, geometry, start, end)
        if not sensors:
            return empty_frame()
        # PurpleAir returns time_stamp automatically; do not request it.
        fields = ["humidity"] + [
            f for f, (p, _) in _FIELD_MAP.items() if p in wanted
        ]
        frames = []
        for sensor in sensors:
            rows, resp_fields = self._history_chunked(
                sensor["sensor_index"], start, end, average, fields)
            frames.append(
                self._parse_history(
                    {"fields": resp_fields, "data": rows},
                    sensor["sensor_index"],
                    sensor["latitude"], sensor["longitude"],
                    county_fips, wanted, average,
                )
            )
        return pd.concat(frames, ignore_index=True) if frames else empty_frame()
```

- [ ] **Step 5: Update the test fake + existing fetch tests.**

In `tests/test_providers_purpleair.py`, update `_FakeSession.get` so the sensor-list response includes the new fields and an active, in-range sensor (the history branch is unchanged):
```python
    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        if url.endswith("/v1/sensors"):
            return _FakeResp(
                {"fields": ["sensor_index", "latitude", "longitude",
                            "last_seen", "date_created"],
                 "data": [[262253, 33.75, -118.33, 1782000000, 1600000000]]}
            )
        span = params["end_timestamp"] - params["start_timestamp"]
        if span > 86400 + 3600:
            raise requests.HTTPError(response=_FakeResp({}, status_code=400))
        return _FakeResp(
            {"fields": ["time_stamp", "humidity", "pm2.5_cf_1", "pm10.0_cf_1"],
             "data": [[1781996400, 44, 1.8, 3.2]]}
        )
```

The two existing `fetch`-based tests (`test_history_request_does_not_request_time_stamp_field`, `test_fetch_chunks_large_range_on_400`) now call `county_polygon`, which would read the bundled file. Make them deterministic by monkeypatching `county_polygon` to a world-covering polygon. Add this fixture near the top of the file (after the imports):
```python
_WORLD = {"type": "Polygon",
          "coordinates": [[[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]]}


@pytest.fixture(autouse=True)
def _stub_polygon(monkeypatch):
    monkeypatch.setattr(
        "smoke_sense.providers.purpleair.county_polygon", lambda fips: _WORLD
    )
```
(The sensor at lon=-118.33, lat=33.75 with last_seen 1782000000 / date_created 1600000000 lies in `_WORLD` and overlaps the 2026 test ranges, so those tests still exercise history fetching.)

- [ ] **Step 6: Add filtering tests:**
```python
def test_list_sensors_requests_outdoor_and_activity_fields():
    session = _FakeSession()
    provider = PurpleAirProvider(purpleair_key="k", session=session)
    from smoke_sense.geo import BBox
    provider._list_sensors(BBox(33.0, -119.0, 35.0, -117.0))
    params = session.calls[0]["params"]
    assert params["location_type"] == 0
    assert "last_seen" in params["fields"]
    assert "date_created" in params["fields"]


def test_filter_sensors_drops_out_of_window_and_out_of_polygon():
    geom = {"type": "Polygon",
            "coordinates": [[[-119, 33], [-117, 33], [-117, 35], [-119, 35], [-119, 33]]]}
    in_county = {"sensor_index": 1, "latitude": 34.0, "longitude": -118.0,
                 "last_seen": 1782000000, "date_created": 1600000000}
    offline_before = {"sensor_index": 2, "latitude": 34.0, "longitude": -118.0,
                      "last_seen": 1600000000, "date_created": 1500000000}
    out_of_county = {"sensor_index": 3, "latitude": 0.0, "longitude": 0.0,
                     "last_seen": 1782000000, "date_created": 1600000000}
    kept = PurpleAirProvider._filter_sensors(
        [in_county, offline_before, out_of_county], geom,
        date(2026, 6, 1), date(2026, 6, 30),
    )
    assert [s["sensor_index"] for s in kept] == [1]
```

- [ ] **Step 7: Run, confirm PASS.** `uv run pytest tests/test_providers_purpleair.py -v` then `uv run pytest -q`.

- [ ] **Step 8: Stage.** `git add src/smoke_sense/providers/purpleair.py tests/test_providers_purpleair.py`

---

## Self-Review

**Spec coverage:**
- County polygons bundled + ray-casting point-in-polygon (Polygon/MultiPolygon/holes) → Task 0 ✓
- Outdoor filter (`location_type=0`, server-side) → Task 2 ✓
- Staleness overlap `[date_created, last_seen] ∩ [start, end]` → Task 2 ✓
- Polygon membership filter → Task 2 ✓
- 429 backoff (Retry-After, else exponential, max retries) wrapping both GETs → Task 1 ✓
- 400s still flow to the chunker → Task 1 (Step 7 note) ✓
- No cap; empty survivors → empty_frame → Task 2 ✓
- Packaging includes the new parquet → Task 0 Step 6 ✓
- Tests for polygon, filtering, backoff, list params, e2e fetch → Tasks 0–2 ✓

**Placeholder scan:** none — full code in every step.

**Type/name consistency:** `point_in_polygon(lon, lat, geometry)`, `county_polygon(fips, table=None)`, `county_contains(fips, lat, lon, geometry=None)`, `_get(url, params)`, `_filter_sensors(sensors, geometry, start, end)` used consistently across geo, purpleair, and tests. The `_get` helper is introduced in Task 1 and reused by `_list_sensors`/`_get_history`; Task 2 only changes `_list_sensors` params and adds filtering — both build on Task 1.

**Note:** AQS is untouched (it has no fan-out). The bundled `county_polygons.parquet` is committed with the implementation (batch) given this repo's pre-commit hook.
