# HRRR Wind Provider Design

**Date:** 2026-06-27
**Status:** Approved

## Goal

Add an `hrrr` data provider that ingests near-surface wind from NOAA's HRRR model
(High-Resolution Rapid Refresh) into the existing county/observation store, so
wind data is available alongside the air-quality metrics for analysis and (later)
smoke forecasting.

## Background

HRRR is a gridded NOAA forecast model: CONUS at 3 km on a Lambert Conformal grid,
a new run every hour, GRIB2 files on the open AWS bucket `noaa-hrrr-bdp-pds`
(anonymous, full archive since 2014-07-30). Near-surface wind (`UGRD`/`VGRD` at
10 m and 80 m above ground) is in the `wrfsfc` 2-D product. The `.idx` sidecar
files allow HTTP byte-range subsetting, so only the wind messages are fetched
(~1-3 MB instead of ~130 MB). The `herbie-data` library handles source
discovery, `.idx` subsetting, and lat/lon-aware reads; it (and any GRIB reader)
needs the **ecCodes** native library.

## Decisions

- **Spatial:** full 3 km field — every grid cell whose centroid falls inside the
  county polygon becomes a synthetic station.
- **Fields:** 10 m and 80 m wind, each as speed + direction (four metrics).
- **Temporal:** F00 analysis only, from each hourly cycle → an hourly historical
  wind series. No forecast lead hours.
- **Dependency:** Herbie + ecCodes (lowest-effort, robust).
- CONUS only; no surface gust (YAGNI).

## Components

### `data.py` — two new metrics

`WIND_SPEED` / `WIND_DIR` already exist and are treated as 10 m. Add:

| Member | `.value` | unit | has_aqi |
|---|---|---|---|
| `WIND_SPEED_80M` | `"wind_speed_80m"` | m/s | False |
| `WIND_DIR_80M` | `"wind_dir_80m"` | deg | False |

Height is encoded in the metric name because the store keys observations by
`metric` and has no height dimension. (Reminder: `data.py` must NOT add
`from __future__ import annotations` — it breaks `enum_properties`.)

### `src/smoke_sense/providers/hrrr.py` (new)

**Pure helpers (no I/O):**
- `wind_speed(u, v) -> float`: `sqrt(u**2 + v**2)`.
- `wind_direction(u, v) -> float`: meteorological "from" direction,
  `(270 - degrees(atan2(v, u))) % 360`.
- `cells_in_polygon(lat2d, lon2d, geometry) -> list[tuple[float, float]]`:
  returns the (lat, lon) of cells whose centroid satisfies
  `geo.point_in_polygon(lon, lat, geometry)` (note the lon, lat order).
- `station_id(lat, lon) -> str`: `f"hrrr-{lat:.4f}_{lon:.4f}"` — stable because
  the HRRR grid never moves.

**`HRRRProvider(AQIProvider)`:**
- `name = "hrrr"`.
- `supported_metrics = {WIND_SPEED, WIND_DIR, WIND_SPEED_80M, WIND_DIR_80M}`.
- `supported_cadences = [60]`.
- `__init__(self, field_source=None, **kwargs)`: stores `field_source or
  HerbieFieldSource()`; absorbs the shared CLI creds dict via `**kwargs`
  (needs none).
- `fetch(self, county_fips, start, end, metrics, cadence=60)`: generator.
  - Intersect `metrics` with `supported_metrics`; if empty, yield nothing.
  - Determine which heights are needed (10 m and/or 80 m) from the requested
    metrics so only those levels are read.
  - `bbox = geo.bbox_for_county(fips)`; `geometry = geo.county_polygon(fips)`.
  - For each hourly cycle timestamp in `[start 00:00 UTC, end 23:00 UTC]`:
    - `sample = field_source.read(cycle, bbox, heights)` → cropped arrays
      `latitude, longitude` and `u/v` per requested height. On a missing/unreadable
      cycle, log at INFO and continue (gap in the series).
    - Keep in-polygon cells; for each, compute the requested speed/dir metrics.
    - Build a long-format chunk: one row per (cell, metric) with `timestamp`=cycle
      (tz-aware UTC), `county_fips`, `station_id`, `latitude`, `longitude`,
      `metric` (`.value`), `value` (float64), `aqi`=`pd.NA`, `agg_window`=60,
      `source`="hrrr". Yield the chunk (skip empty).

**`HerbieFieldSource` (the GRIB boundary, injectable):**
- `read(self, cycle, bbox, heights) -> FieldSample`: lazily imports `herbie`,
  builds `Herbie(cycle, model="hrrr", product="sfc", fxx=0)`, subsets
  `":(UGRD|VGRD):(10|80) m above ground"` (only requested heights), reads into
  xarray, crops to `bbox`, and returns plain numpy arrays
  (`latitude`, `longitude`, and `u10/v10/u80/v80` as available). Raises a
  provider-local error subclass on absent files so `fetch` can skip the cycle.
- `herbie` is imported inside the method so the module imports without GRIB libs
  installed (tests never import herbie).

### `src/smoke_sense/providers/__init__.py`

Add `hrrr` to the import line so `@register` fires.

### Dependencies

`uv add herbie-data eccodes` (Herbie pulls cfgrib/xarray; ecCodes is the native
GRIB library).

## Data flow

```
fetch_county(..., providers=[HRRRProvider()])
  -> HRRRProvider.fetch("06037", start, end, metrics, 60)
       bbox = geo.bbox_for_county("06037"); geom = geo.county_polygon("06037")
       for cycle in hourly(start..end):
         sample = field_source.read(cycle, bbox, heights={10,80})   # Herbie + .idx subset
         cells = cells_in_polygon(sample.lat, sample.lon, geom)
         for (lat,lon) in cells:
           speed10 = wind_speed(u10,v10); dir10 = wind_direction(u10,v10); ...(80m)
           emit rows (station_id(lat,lon), metric, value, ...)
         yield chunk
  -> store.write: lat/lon -> stations.parquet (keyed by station_id, source="hrrr");
     observations -> day files; coverage keyed by source="hrrr"
```

## Error handling

- Missing/late cycle file (recent dates, ~1.5 h posting latency) or read error:
  log at INFO and skip that cycle; the series simply has a gap. Mirrors AQS's
  empty-response tolerance.
- No credentials, so nothing to fail on; `__init__(**kwargs)` ignores foreign keys.
- Non-CONUS county (no HRRR coverage) → no in-polygon cells → empty chunks (no rows).

## Testing

All tests inject a fake `field_source`; none touch the network or ecCodes.

`tests/test_providers_hrrr.py`:
- pure helpers: `wind_speed` / `wind_direction` against known vectors (N/E/S/W),
  `station_id` stability, `cells_in_polygon` includes/excludes correctly (tiny
  polygon, guards lon/lat order).
- `fetch` with a fake source returning a small synthetic grid: assert the yielded
  frame has exactly `data.COLUMNS` (after `data.validate`) plus lat/lon, `source
  == "hrrr"`, the four metrics present, `agg_window == 60`, `aqi` is NA, hourly
  timestamps across the range, and only in-polygon cells appear.
- metric subset: requesting only `WIND_SPEED` reads only 10 m and emits only that
  metric.
- a cycle whose `field_source.read` raises the "absent" error is skipped (gap),
  not fatal.
- registry: `get_provider("hrrr")` builds with the shared creds dict and needs no
  keys; appears in `all_providers()`.

`tests/test_data.py` (or metric tests): the two new metrics exist with the right
`.value`/`unit`, and case-insensitive label lookup works.
