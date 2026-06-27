# HRRR Wind Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `hrrr` provider that ingests 10 m / 80 m wind (speed + direction) from NOAA HRRR, sampling every grid cell inside a county polygon as a synthetic station, from each hourly F00 analysis.

**Architecture:** Pure helpers (wind math, polygon cell filtering, station id) + an `HRRRProvider` that loops hourly cycles and emits canonical chunks, with the GRIB/Herbie read isolated behind an injectable `field_source` so all logic is unit-tested with synthetic arrays (no network, no ecCodes). A real `HerbieFieldSource` (lazy `herbie` import) backs it in production.

**Tech Stack:** Python 3.12, pandas, Herbie (`herbie-data`) + cfgrib/ecCodes, pytest.

Spec: `docs/superpowers/specs/2026-06-27-hrrr-wind-provider-design.md`

---

### Task 1: Add 80 m wind metrics

**Goal:** Add `WIND_SPEED_80M` and `WIND_DIR_80M` to the `Metric` enum.

**Files:**
- Modify: `src/smoke_sense/data.py`
- Test: `tests/test_metric.py`

**Acceptance Criteria:**
- [ ] `Metric.WIND_SPEED_80M.value == "wind_speed_80m"`, unit `"m/s"`, `has_aqi` False.
- [ ] `Metric.WIND_DIR_80M.value == "wind_dir_80m"`, unit `"deg"`, `has_aqi` False.
- [ ] Case-insensitive label lookup works (`Metric("wind_speed_80m")`).
- [ ] Neither appears in `AQI_METRICS`.

**Verify:** `uv run pytest tests/test_metric.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metric.py`:

```python
def test_wind_80m_metrics_exist():
    from smoke_sense.data import AQI_METRICS, Metric

    assert Metric.WIND_SPEED_80M.value == "wind_speed_80m"
    assert Metric.WIND_SPEED_80M.unit == "m/s"
    assert Metric.WIND_SPEED_80M.has_aqi is False
    assert Metric.WIND_DIR_80M.value == "wind_dir_80m"
    assert Metric.WIND_DIR_80M.unit == "deg"
    assert Metric.WIND_DIR_80M.has_aqi is False
    # case-insensitive label lookup (enum-properties Symmetric)
    assert Metric("WIND_SPEED_80M") is Metric.WIND_SPEED_80M
    assert Metric.WIND_SPEED_80M not in AQI_METRICS
    assert Metric.WIND_DIR_80M not in AQI_METRICS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_metric.py::test_wind_80m_metrics_exist -v`
Expected: FAIL — `AttributeError: WIND_SPEED_80M`.

- [ ] **Step 3: Add the members**

In `src/smoke_sense/data.py`, in the `Metric` class, add two members immediately after the `WIND_DIR` line (keep the column alignment / comment style):

```python
    WIND_SPEED_80M = "wind_speed_80m", "wind_speed_80m", "m/s", False
    WIND_DIR_80M   = "wind_dir_80m",   "wind_dir_80m",   "deg", False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_metric.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/smoke_sense/data.py tests/test_metric.py
git commit -m "feat(hrrr): add 80m wind metrics"
```

---

### Task 2: HRRR provider core (helpers + provider + registry)

**Goal:** Implement the pure helpers and `HRRRProvider.fetch`, fully unit-tested with an injected fake field source.

**Files:**
- Create: `src/smoke_sense/providers/hrrr.py`
- Modify: `src/smoke_sense/providers/__init__.py`
- Test: `tests/test_providers_hrrr.py`

**Acceptance Criteria:**
- [ ] `wind_speed`/`wind_direction` correct for cardinal vectors; `station_id` stable; `cells_in_polygon` filters by centroid (lon, lat order).
- [ ] `HRRRProvider` registers as `"hrrr"`, needs no credentials, `supported_cadences == [60]`, the four wind metrics in `supported_metrics`.
- [ ] `fetch` yields canonical chunks (`data.validate` accepts them) with `source=="hrrr"`, `agg_window==60`, `aqi` NA, lat/lon present, hourly timestamps, only in-polygon cells, only requested metrics/heights.
- [ ] A cycle whose `field_source.read` raises `FieldUnavailable` is skipped, not fatal.

**Verify:** `uv run pytest tests/test_providers_hrrr.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_providers_hrrr.py`:

```python
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from smoke_sense import data
from smoke_sense.data import Metric
from smoke_sense.providers import all_providers, get_provider
from smoke_sense.providers import hrrr


# A tiny square polygon around (lat 34, lon -118): GeoJSON Polygon (lon, lat).
_POLY = {
    "type": "Polygon",
    "coordinates": [[[-118.1, 33.9], [-117.9, 33.9],
                     [-117.9, 34.1], [-118.1, 34.1], [-118.1, 33.9]]],
}


def _sample():
    # three cells: two inside the polygon, one far outside
    lat = [34.00, 34.05, 40.00]
    lon = [-118.00, -118.05, -100.00]
    # u,v chosen so 10m speed/dir are easy to check at cell 0:
    # u=0, v=-3 -> speed 3, direction 0 (from north)
    u10 = [0.0, 3.0, 9.0]
    v10 = [-3.0, 0.0, 9.0]
    u80 = [0.0, 4.0, 9.0]
    v80 = [-4.0, 0.0, 9.0]
    return hrrr.FieldSample(
        latitude=np.array(lat), longitude=np.array(lon),
        u={10: np.array(u10), 80: np.array(u80)},
        v={10: np.array(v10), 80: np.array(v80)})


class _FakeSource:
    def __init__(self, sample, raise_cycles=None):
        self._sample = sample
        self._raise = set(raise_cycles or [])
        self.calls = []

    def read(self, cycle, bbox, heights):
        self.calls.append((cycle, tuple(heights)))
        if cycle in self._raise:
            raise hrrr.FieldUnavailable("not posted")
        return self._sample


@pytest.fixture(autouse=True)
def _stub_geo(monkeypatch):
    monkeypatch.setattr(hrrr, "county_polygon", lambda fips: _POLY)
    monkeypatch.setattr(hrrr, "bbox_for_county", lambda fips: object())


def test_wind_speed_and_direction():
    assert hrrr.wind_speed(3.0, 4.0) == pytest.approx(5.0)
    # meteorological "from" direction
    assert hrrr.wind_direction(0.0, -1.0) == pytest.approx(0.0)    # from north
    assert hrrr.wind_direction(-1.0, 0.0) == pytest.approx(90.0)   # from east
    assert hrrr.wind_direction(0.0, 1.0) == pytest.approx(180.0)   # from south
    assert hrrr.wind_direction(1.0, 0.0) == pytest.approx(270.0)   # from west


def test_station_id_stable():
    assert hrrr.station_id(34.0, -118.0) == hrrr.station_id(34.0, -118.0)
    assert hrrr.station_id(34.0, -118.0) != hrrr.station_id(34.05, -118.0)


def test_cells_in_polygon_filters():
    s = _sample()
    cells = hrrr.cells_in_polygon(s.latitude, s.longitude, _POLY)
    assert [c[0] for c in cells] == [0, 1]   # third cell excluded


def test_registry_no_credentials():
    assert "hrrr" in all_providers()
    p = get_provider("hrrr", email="x", api_key="y", purpleair_key="z",
                     field_source=_FakeSource(_sample()))
    assert p.name == "hrrr"
    assert p.supported_cadences == [60]
    assert hrrr.Metric.WIND_SPEED_80M in p.supported_metrics


def test_fetch_yields_canonical_wind_chunks():
    src = _FakeSource(_sample())
    p = hrrr.HRRRProvider(field_source=src)
    chunks = list(p.fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                          list(Metric), cadence=60))
    assert chunks, "expected at least one chunk"
    df = pd.concat(chunks, ignore_index=True)
    # canonical schema (validate selects/coerces COLUMNS)
    assert list(data.validate(df).columns) == list(data.COLUMNS)
    assert set(df["source"]) == {"hrrr"}
    assert set(df["agg_window"]) == {60}
    assert df["aqi"].isna().all()
    assert {"latitude", "longitude"}.issubset(df.columns)
    assert set(df["metric"]) == {
        "wind_speed", "wind_dir", "wind_speed_80m", "wind_dir_80m"}
    # 24 hourly cycles x 2 in-polygon cells
    assert df["timestamp"].nunique() == 24
    assert df["station_id"].nunique() == 2
    # cell 0: u10=0,v10=-3 -> speed 3, dir 0
    cell0 = df[(df["station_id"] == hrrr.station_id(34.0, -118.0))]
    sp = cell0[cell0["metric"] == "wind_speed"]["value"].iloc[0]
    di = cell0[cell0["metric"] == "wind_dir"]["value"].iloc[0]
    assert sp == pytest.approx(3.0)
    assert di == pytest.approx(0.0)


def test_fetch_metric_subset_reads_only_needed_heights():
    src = _FakeSource(_sample())
    p = hrrr.HRRRProvider(field_source=src)
    chunks = list(p.fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                          [Metric.WIND_SPEED], cadence=60))
    df = pd.concat(chunks, ignore_index=True)
    assert set(df["metric"]) == {"wind_speed"}
    # only the 10 m height was requested of the source
    assert all(heights == (10,) for _, heights in src.calls)


def test_fetch_skips_unavailable_cycle():
    miss = datetime(2026, 6, 16, 5, tzinfo=timezone.utc)
    src = _FakeSource(_sample(), raise_cycles=[miss])
    p = hrrr.HRRRProvider(field_source=src)
    chunks = list(p.fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                          [Metric.WIND_SPEED], cadence=60))
    df = pd.concat(chunks, ignore_index=True)
    assert miss not in set(df["timestamp"])     # gap, not crash
    assert df["timestamp"].nunique() == 23


def test_fetch_no_wanted_metrics_yields_nothing():
    p = hrrr.HRRRProvider(field_source=_FakeSource(_sample()))
    chunks = list(p.fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                          [Metric.PM2_5], cadence=60))
    assert chunks == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers_hrrr.py -v`
Expected: FAIL — `ModuleNotFoundError: smoke_sense.providers.hrrr`.

- [ ] **Step 3: Create the provider module**

Create `src/smoke_sense/providers/hrrr.py`:

```python
"""HRRR wind provider: near-surface wind from NOAA's High-Resolution Rapid
Refresh model, sampled per grid cell within a county.

HRRR is a gridded forecast model (GRIB2 on AWS open data). We read the F00
analysis from each hourly cycle, keep grid cells whose centroid is inside the
county polygon, and emit each cell as a synthetic station with 10 m / 80 m wind
speed and direction. The GRIB read is isolated behind an injectable
``field_source`` so the provider logic is unit-testable without network/ecCodes.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd

from ..data import Metric
from ..geo import bbox_for_county, county_polygon, point_in_polygon
from .base import AQIProvider, register

logger = logging.getLogger(__name__)

# metric -> (height in metres, "speed"|"dir")
_METRIC_SPEC: dict[Metric, tuple[int, str]] = {
    Metric.WIND_SPEED: (10, "speed"),
    Metric.WIND_DIR: (10, "dir"),
    Metric.WIND_SPEED_80M: (80, "speed"),
    Metric.WIND_DIR_80M: (80, "dir"),
}


class FieldUnavailable(Exception):
    """Raised when an HRRR cycle's wind field cannot be read (e.g. not posted)."""


@dataclass(frozen=True)
class FieldSample:
    """Flattened per-cell wind field for one cycle, cropped to a county bbox.

    ``latitude``/``longitude`` are equal-length 1-D sequences; ``u``/``v`` map a
    height (metres) to a 1-D component sequence aligned with lat/lon.
    """

    latitude: object
    longitude: object
    u: dict
    v: dict


def wind_speed(u: float, v: float) -> float:
    """Wind speed magnitude from u/v components (m/s)."""
    return math.sqrt(u * u + v * v)


def wind_direction(u: float, v: float) -> float:
    """Meteorological wind direction in degrees (direction the wind blows FROM)."""
    return (270.0 - math.degrees(math.atan2(v, u))) % 360.0


def station_id(lat: float, lon: float) -> str:
    """Stable per-cell station id (the HRRR grid never moves)."""
    return f"hrrr-{lat:.4f}_{lon:.4f}"


def cells_in_polygon(latitudes, longitudes, geometry) -> list[tuple[int, float, float]]:
    """(index, lat, lon) for cells whose centroid is inside `geometry`."""
    out: list[tuple[int, float, float]] = []
    for i, (lat, lon) in enumerate(zip(latitudes, longitudes)):
        if point_in_polygon(float(lon), float(lat), geometry):
            out.append((i, float(lat), float(lon)))
    return out


def _hourly_cycles(start: date, end: date) -> Iterator[datetime]:
    cur = datetime.combine(start, time(0), tzinfo=timezone.utc)
    last = datetime.combine(end, time(23), tzinfo=timezone.utc)
    while cur <= last:
        yield cur
        cur += timedelta(hours=1)


@register
class HRRRProvider(AQIProvider):
    """Near-surface wind from HRRR, one synthetic station per in-county grid cell."""

    name = "hrrr"
    supported_metrics = set(_METRIC_SPEC)
    supported_cadences = [60]

    def __init__(self, field_source=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._source = field_source if field_source is not None else HerbieFieldSource()

    def fetch(self, county_fips, start, end, metrics, cadence: int = 60):
        wanted = [m for m in metrics if m in self.supported_metrics]
        if not wanted:
            return
        heights = sorted({_METRIC_SPEC[m][0] for m in wanted})
        bbox = bbox_for_county(county_fips)
        geometry = county_polygon(county_fips)
        for cycle in _hourly_cycles(start, end):
            try:
                sample = self._source.read(cycle, bbox, heights)
            except FieldUnavailable as exc:
                logger.info("HRRR cycle %s unavailable: %s", cycle, exc)
                continue
            cells = cells_in_polygon(sample.latitude, sample.longitude, geometry)
            if not cells:
                continue
            rows: list[dict] = []
            for idx, lat, lon in cells:
                sid = station_id(lat, lon)
                for m in wanted:
                    height, kind = _METRIC_SPEC[m]
                    u = float(sample.u[height][idx])
                    v = float(sample.v[height][idx])
                    value = wind_speed(u, v) if kind == "speed" else wind_direction(u, v)
                    rows.append({
                        "timestamp": cycle,
                        "county_fips": county_fips,
                        "station_id": sid,
                        "latitude": lat,
                        "longitude": lon,
                        "metric": m.value,
                        "value": value,
                        "aqi": pd.NA,
                        "agg_window": 60,
                        "source": "hrrr",
                    })
            if rows:
                chunk = pd.DataFrame(rows)
                chunk["timestamp"] = pd.to_datetime(chunk["timestamp"], utc=True)
                chunk["aqi"] = chunk["aqi"].astype("Int16")
                yield chunk


class HerbieFieldSource:
    """Reads HRRR 10 m/80 m wind via Herbie, byte-range-subset from AWS.

    `herbie` is imported lazily so this module imports without the GRIB stack.
    """

    def read(self, cycle: datetime, bbox, heights) -> FieldSample:
        try:
            from herbie import Herbie
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise FieldUnavailable("herbie-data not installed") from exc
        levels = "|".join(f"{h} m above ground" for h in heights)
        search = rf":(UGRD|VGRD):({levels})"
        try:
            run = Herbie(cycle, model="hrrr", product="sfc", fxx=0)
            ds = run.xarray(search)
        except Exception as exc:  # pragma: no cover - network/file dependent
            raise FieldUnavailable(str(exc)) from exc
        return _sample_from_xarray(ds, bbox, heights)


def _sample_from_xarray(ds, bbox, heights) -> FieldSample:  # pragma: no cover
    """Crop xarray HRRR wind to `bbox` and flatten to a FieldSample.

    cfgrib returns one dataset per height level (a list when levels differ).
    Refined against real data in the integration task.
    """
    import numpy as np

    datasets = ds if isinstance(ds, list) else [ds]
    lat = lon = None
    u: dict[int, object] = {}
    v: dict[int, object] = {}
    for d in datasets:
        latv = np.asarray(d["latitude"].values)
        lonv = np.asarray(d["longitude"].values)
        lonv = np.where(lonv > 180.0, lonv - 360.0, lonv)  # 0..360 -> -180..180
        mask = ((latv >= bbox.min_lat) & (latv <= bbox.max_lat)
                & (lonv >= bbox.min_lon) & (lonv <= bbox.max_lon))
        height = int(round(float(np.asarray(d["heightAboveGround"].values))))
        uname = "u10" if "u10" in d else "u"
        vname = "v10" if "v10" in d else "v"
        uv = np.asarray(d[uname].values)[mask]
        vv = np.asarray(d[vname].values)[mask]
        if lat is None:
            lat, lon = latv[mask], lonv[mask]
        u[height] = uv
        v[height] = vv
    return FieldSample(latitude=lat, longitude=lon, u=u, v=v)
```

- [ ] **Step 4: Register the provider**

In `src/smoke_sense/providers/__init__.py`, add `hrrr` to the side-effect import line so `@register` runs. Change:

```python
from . import aqs, clarity, purpleair
```
to:
```python
from . import aqs, clarity, hrrr, purpleair
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers_hrrr.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS (no regressions). `herbie` is imported lazily, so the suite stays green without it installed.

- [ ] **Step 7: Commit**

```bash
git add src/smoke_sense/providers/hrrr.py src/smoke_sense/providers/__init__.py tests/test_providers_hrrr.py
git commit -m "feat(hrrr): HRRR wind provider (grid-cell sampling, injectable source)"
```

---

### Task 3: Herbie dependency + real-source verification

**Goal:** Add the GRIB stack and verify `HerbieFieldSource` against a real past cycle, fixing the xarray glue if needed.

**Files:**
- Modify: `pyproject.toml` / `uv.lock` (via `uv add`)
- Modify (if needed): `src/smoke_sense/providers/hrrr.py` (`_sample_from_xarray` / search string)

**Acceptance Criteria:**
- [ ] `herbie-data` and `eccodes` are added as dependencies.
- [ ] The full unit suite still passes (lazy import keeps it green).
- [ ] A real fetch of a recent past cycle returns in-polygon wind for a small county, hand-verified.

**Verify:** `uv run pytest` → all pass, plus the manual snippet below returns non-empty wind.

**Steps:**

- [ ] **Step 1: Add dependencies**

Run:
```bash
uv add herbie-data eccodes
```
If `eccodes` wheels fail to build in this environment, try `uv add herbie-data cfgrib eccodes`; if the native library still cannot be installed here, record that in the commit message and proceed — the unit suite does not need it (lazy import).

- [ ] **Step 2: Confirm the suite is still green**

Run: `uv run pytest -q`
Expected: same pass count as after Task 2 (no module imports `herbie` at load time).

- [ ] **Step 3: Real-source smoke check (manual)**

Pick a cycle ~1 day old (so it is posted) and a small county; run:

```bash
uv run python - <<'PY'
from datetime import date
from smoke_sense.providers.hrrr import HRRRProvider
from smoke_sense.data import Metric
import pandas as pd

p = HRRRProvider()  # real HerbieFieldSource
# a small county; pick one within CONUS, e.g. 06037 (Los Angeles)
chunks = list(p.fetch("06037", date(2026, 6, 25), date(2026, 6, 25),
                      [Metric.WIND_SPEED, Metric.WIND_DIR], cadence=60))
df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
print("rows", len(df), "stations", df["station_id"].nunique() if len(df) else 0)
print(df.head())
print("speed range", (df["value"][df["metric"]=="wind_speed"].min(),
                      df["value"][df["metric"]=="wind_speed"].max()) if len(df) else None)
PY
```

Expected: non-empty rows, multiple stations, plausible wind speeds (roughly 0–30 m/s), directions 0–360. If the columns/level handling differ from `_sample_from_xarray` (e.g. cfgrib variable names, `heightAboveGround` access, or list-vs-single dataset), fix `_sample_from_xarray`/the search string until this returns correct data, then re-run `uv run pytest` to confirm units stay green.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock src/smoke_sense/providers/hrrr.py
git commit -m "feat(hrrr): add herbie-data/eccodes deps; verify real wind read"
```

---

## Self-Review

**Spec coverage:**
- Two 80 m metrics → Task 1. ✓
- Provider `name`/cadence/metrics/no-creds → Task 2 (`test_registry_no_credentials`). ✓
- Pure helpers (speed/dir/station_id/cells_in_polygon) → Task 2 tests. ✓
- `fetch`: hourly F00, in-polygon cells, canonical chunk, metric/height subset, skip-on-unavailable → Task 2 tests. ✓
- Injectable `field_source`; lazy Herbie → Task 2 (`HerbieFieldSource`), suite green without GRIB. ✓
- Dependency + real verification → Task 3. ✓
- Registration in `providers/__init__.py` → Task 2 Step 4. ✓

**Placeholder scan:** `_sample_from_xarray` is marked `# pragma: no cover` and explicitly "refined against real data in the integration task" — it is real code (not a stub) verified in Task 3. No TBD/TODO. ✓

**Type consistency:** `FieldSample(latitude, longitude, u: dict[height]->seq, v: ...)` produced by the fake source in Task 2 tests and by `_sample_from_xarray`; consumed by `fetch` via `sample.u[height][idx]`. `_METRIC_SPEC` keys are the four Metric members (two from Task 1). `FieldUnavailable` raised by the source and caught in `fetch`. `cells_in_polygon` returns `(idx, lat, lon)` consumed in `fetch`. Consistent. ✓
