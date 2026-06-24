# PurpleAir Sensor Fan-out Mitigation — Design

**Date:** 2026-06-24
**Status:** Approved (design phase)
**Scope:** Reduce and survive the PurpleAir per-sensor history fan-out by filtering the
sensor list (outdoor, active-in-window, inside the county polygon) before making history
calls, and by handling rate limits with 429 backoff. PurpleAir-only; no schema changes.

## Problem

`PurpleAirProvider.fetch` lists every sensor in a county's bounding box, then makes one
history call per sensor. A dense county (LA `06037` → ~1,295 sensors) becomes ~1,300
sequential calls, which hit PurpleAir's rate limit (HTTP 429, currently unhandled →
aborts), waste quota, and over-include indoor/offline/out-of-county sensors.

## Key Decisions

| Decision | Choice |
|---|---|
| County membership | Bundle county polygons; pure-Python ray-casting point-in-polygon |
| Outdoor filter | `location_type=0` on the `/v1/sensors` query (server-side) |
| Staleness | Keep sensors whose `[date_created, last_seen]` overlaps `[start, end]` |
| Rate limits | Reactive 429 backoff (honor `Retry-After`, else exponential, max 5 retries) |
| Capping | None (rely on filters + backoff) |

## County Polygons (`geo.py`)

- **`scripts/build_county_polygons.py`**: from the same Census county GeoJSON used by the
  bbox builder, write `src/smoke_sense/_data/county_polygons.parquet` with columns
  `county_fips` (string) and `geometry` (JSON string of the GeoJSON geometry: `type` +
  `coordinates`). ~a few MB at the 500k resolution for ~3,233 counties.
- **`geo.py` additions:**
  - `load_polygon_table() -> pd.DataFrame` (packaged resource, like `load_bbox_table`).
  - `county_polygon(fips: str, table=None) -> dict` — the GeoJSON geometry; raises
    `KeyError` for unknown FIPS.
  - `point_in_polygon(lon: float, lat: float, geometry: dict) -> bool` — ray-casting with
    the even-odd rule over every ring of `Polygon`/`MultiPolygon`, so interior holes are
    handled (a point in a hole counts as outside).
  - `county_contains(fips, lat, lon, geometry=None) -> bool` — convenience wrapper; the
    caller loads the geometry once and reuses it across many points.

`BBox` and `bbox_for_county` are unchanged (still used to query PurpleAir).

## PurpleAir Sensor Filtering (`purpleair.py`)

`_list_sensors(bbox)`:
- Add `location_type=0` (outdoor) to the `/v1/sensors` params.
- Request `fields = "latitude,longitude,last_seen,date_created"` (PurpleAir prepends
  `sensor_index`). Returns dicts with those keys.

`_filter_sensors(sensors, geometry, start, end) -> list[dict]` (pure, testable):
- **Staleness/overlap:** keep when `last_seen >= start_ts and date_created <= end_ts`,
  where `start_ts`/`end_ts` are the UTC epoch bounds of `[start, end]` (end taken as the
  end of the end day).
- **Polygon:** keep when `geo.point_in_polygon(lon, lat, geometry)` is true.

`fetch(...)` order: resolve cadence → `bbox_for_county` → `_list_sensors` →
`geo.county_polygon(fips)` → `_filter_sensors` → per-surviving-sensor history (with the
existing adaptive chunking) → `_parse_history` → concat. Empty survivor list →
`empty_frame()`.

## Rate-limit Backoff (`purpleair.py`)

A single `_get(url, params)` helper routes every PurpleAir GET (`_list_sensors` and
`_get_history`):
- Perform `session.get(...)`.
- On HTTP **429** (and attempts remain): wait `Retry-After` seconds if the header is
  present and numeric, else exponential backoff starting 2s, doubling, capped at 60s;
  retry. Max 5 retries, then `raise_for_status` (surface the 429).
- Any other status: `raise_for_status()` then return `.json()`. (So `_history_chunked`
  still receives `400`s and splits the date range.)
- Waiting uses `time.sleep` (monkeypatched in tests).

## Error Handling

- 429 past max retries → surfaced (no silent drop).
- Non-400/429 history failure → raises (no silent per-sensor skip).
- County with no qualifying sensors → `empty_frame()` (not an error).
- Unknown FIPS in `county_polygon` → `KeyError` (clear).

## Testing

- `geo.point_in_polygon`: inside/outside a square `Polygon`; `MultiPolygon`; a point in
  the bbox but outside an L-shaped polygon; a point inside a polygon hole (→ outside).
- `geo.county_polygon`: returns geometry for a known FIPS (from an injected table);
  `KeyError` for unknown.
- `_filter_sensors`: staleness overlap keeps in-window and drops pre-install/already-
  offline sensors for both historical and recent ranges; polygon membership drops
  out-of-county points.
- `_list_sensors`: query includes `location_type=0` and requests
  `last_seen`/`date_created`.
- `_get` backoff: `429` once (with `Retry-After`, and without) then `200` → retried and
  slept the expected duration (monkeypatched `time.sleep`); `429` beyond max retries →
  raises.
- End-to-end `fetch` with a fake session + fake geometry: only in-county, in-window
  sensors receive history calls.

## Out of Scope

- Parallel requests (stays sequential under backoff).
- Sensor sampling / caps.
- Any non-PurpleAir provider or schema change.

## Build / Packaging

Add `county_polygons.parquet` to the wheel package-data alongside `county_bbox.parquet`.
Run `scripts/build_county_polygons.py` once to generate it (network required).
