# Clarity (LAUSD OpenMap) Provider — Design

**Date:** 2026-06-27
**Status:** Approved (design phase)
**Scope:** A new `AQIProvider` named `clarity` that pulls hourly air-quality data for the
LAUSD Clean Air Coalition sensor network from the Clarity OpenMap at
`lausd.map.clarity.io`. No schema changes, no credentials, no CLI changes beyond
registration.

## Problem

LAUSD publishes air-quality data through Clarity's OpenMap. There is no documented public
API key path for us (the sensors are owned by the Clean Air Coalition). The data is
reachable only through the OpenMap web app, where each station's "Download" button yields a
CSV of the last 30 days of hourly measurements.

## Reverse-Engineered API (verified)

The OpenMap is a Firebase-backed SPA, but its data endpoints are same-origin REST and
**unauthenticated** — they require only a browser-style `User-Agent` header (a non-browser
UA gets the SPA's `index.html` fallback instead of JSON/CSV). No cookies, no Firebase token.

| Purpose | Request |
|---|---|
| Station list | `GET https://lausd.map.clarity.io/api/v1/map/air-quality-markers?network=lausd&aqiStandard=US-EPA` |
| Time series (download) | `GET https://lausd.map.clarity.io/api/v1/datasources/{datasourceId}/measurements.csv?networkId=lausd` |

- **Markers** response: `data.markers[]`, each with `datasourceId` (e.g. `DAABL1560`),
  `datasourceName`, `sourceType` (LAUSD school nodes are `CLARITY_NODE`), and
  `location.coordinates` as `[lon, lat]`. ~224 active LAUSD stations.
- **CSV** response: 720 rows = exactly 30 days × 24 hours, ascending time. Header:
  `"time (UTC)","time (America/Los_Angeles)","no2Conc1HourMean","no2Conc1HourMeanUsEpaAqi",`
  `"pm10ConcMassNowcast","pm10ConcMassNowcastUsEpaAqi","pm2_5ConcMassNowcast",`
  `"pm2_5ConcMassNowcastUsEpaAqi","relHumidAmbient1HourMean","temperatureAmbient1HourMean",`
  `"windDirection1HourMean","windSpeed1HourMean"`. Empty cells where a node lacks a sensor.

This is option 2 (reverse-engineered HTTP API) from the acquisition discussion; it delivers
30+ days of hourly data, so browser automation is **not** needed.

## Key Decisions

| Decision | Choice |
|---|---|
| Acquisition | Direct HTTPS to the OpenMap REST/CSV endpoints with a browser `User-Agent` |
| Network / host | Constants: host `lausd.map.clarity.io`, `networkId = "lausd"` (LAUSD-specific provider) |
| Station discovery | `air-quality-markers?network=lausd`; filter to the county polygon |
| County membership | Reuse `geo.county_polygon` + `geo.point_in_polygon` (only LA `06037` yields data) |
| Cadence | Hourly only: `supported_cadences = [60]`, `agg_window = 60` |
| Date window | CSV always returns last ~30 days; provider filters rows to `[start, end]`. Older days are unavailable (documented limitation) |
| NO2 units | Convert Clarity µg/m³ → ppb: `ppb = µg/m³ / 1.88` (25 °C / 1 atm) |
| AQI | Use Clarity's provided `*UsEpaAqi` columns directly (values are already NowCast — recomputing would double-smooth) |
| Rate limits | Reactive retry on 429/5xx with exponential backoff (max 5), reusing the PurpleAir `_get` pattern; sequential downloads |
| Credentials | None; `__init__` accepts an optional `session` and ignores other providers' `**kwargs` |

## Module: `src/smoke_sense/providers/clarity.py`

Constants:
- `_BASE = "https://lausd.map.clarity.io"`, `_NETWORK = "lausd"`.
- `_MARKERS_URL = f"{_BASE}/api/v1/map/air-quality-markers"`.
- `_CSV_URL = f"{_BASE}/api/v1/datasources/{{datasource_id}}/measurements.csv"`.
- `_USER_AGENT` — a desktop Chrome UA string (required to receive JSON/CSV, not HTML).

Metric mapping — CSV column → `(Metric, aqi_column_or_None, converter)`:

| CSV column | Metric | Unit | AQI column | Conversion |
|---|---|---|---|---|
| `pm2_5ConcMassNowcast` | `PM2_5` | µg/m³ | `pm2_5ConcMassNowcastUsEpaAqi` | none |
| `pm10ConcMassNowcast` | `PM10` | µg/m³ | `pm10ConcMassNowcastUsEpaAqi` | none |
| `no2Conc1HourMean` | `NO2` | ppb | `no2Conc1HourMeanUsEpaAqi` | `/ 1.88` |
| `temperatureAmbient1HourMean` | `TEMP` | °C | — | none |
| `relHumidAmbient1HourMean` | `RH` | % | — | none |
| `windSpeed1HourMean` | `WIND_SPEED` | m/s | — | none |
| `windDirection1HourMean` | `WIND_DIR` | deg | — | none |

`@register class ClarityProvider(AQIProvider)`:
- `name = "clarity"`, `supported_metrics = set(mapping keys)`, `supported_cadences = [60]`.
- `__init__(self, session: requests.Session | None = None, user_agent: str | None = None, **kwargs)`.
- `_get(url, params, *, as_text=False)` — shared GET with browser UA header and 429/5xx
  backoff (honor `Retry-After`, else exponential from 2s, cap 60s, max 5 retries); returns
  `.json()` or `.text`. Mirrors `purpleair._get`. Logs method/url/status/elapsed.

Methods:
- `_list_stations() -> list[dict]` — GET markers; return `[{datasourceId, name, lat, lon}]`
  (unpacking `location.coordinates` as `[lon, lat]`).
- `_filter_stations(stations, geometry) -> list[dict]` — keep `point_in_polygon(lon, lat)`.
- `_parse_csv(text, station, county_fips, wanted) -> pd.DataFrame` (pure, testable):
  - Read CSV; parse `"time (UTC)"` as UTC timestamps.
  - For each wanted metric whose column is present: build a part with `timestamp`,
    `county_fips`, `station_id = datasourceId`, `latitude`, `longitude`, `metric`,
    `value` (converted), `aqi` (from the AQI column as `Int16`, else `pd.NA`),
    `agg_window = 60`, `source = "clarity"`; `dropna(subset=["value"])`.
  - Concat non-empty parts; else `empty_frame_with_coords()`.
- `fetch(self, county_fips, start, end, metrics, cadence=60)`:
  1. `wanted = [m for m in metrics if m in supported_metrics]`; empty → return.
  2. `stations = _filter_stations(_list_stations(), county_polygon(county_fips))`;
     empty → return.
  3. For each station: download CSV, `_parse_csv`, filter rows to
     `start <= timestamp.date() <= end`; if non-empty, `yield` the chunk.

The frame includes `latitude`/`longitude` columns beyond the canonical schema (same as the
AQS/PurpleAir providers); `empty_frame_with_coords()` is reused/duplicated from `aqs.py`.

## Registration & Wiring

- `providers/__init__.py`: add `clarity` to the side-effect import
  (`from . import aqs, clarity, purpleair`).
- No new CLI option or credential — `clarity` simply appears in `all_providers()` and runs
  by default. The `fetcher` already keys coverage by `provider.name`, so incremental fetch
  works unchanged.

## Error Handling

- Non-browser UA / SPA-fallback HTML where JSON/CSV expected → raise a clear `ValueError`
  (detect by content-type or a parse failure) rather than silently producing empty data.
- 429/5xx past max retries → surfaced (no silent drop).
- County with no in-polygon stations → return nothing (not an error).
- A single station's CSV failure → surfaced (no silent per-station skip), consistent with
  AQS/PurpleAir behavior; the fetcher flushes partial buffers on error.
- Requested range older than the 30-day window → those days are simply absent.

## Testing

All tests use a fake `requests.Session` (no network), mirroring
`tests/test_providers_purpleair.py`:
- `_list_stations`: parses markers JSON; unpacks `[lon, lat]`; carries `datasourceId`/name.
- `_filter_stations`: drops out-of-polygon stations (injected geometry).
- `_parse_csv`:
  - Maps each column to the right metric and canonical value; NO2 µg/m³→ppb conversion.
  - AQI taken from the provided `*UsEpaAqi` column for AQI metrics; `pd.NA` for
    temp/RH/wind.
  - Empty cells dropped (`dropna` on value); a node reporting only PM2.5 yields only PM2.5.
  - Timestamps parsed from `"time (UTC)"` as UTC.
- `_get` backoff: 429 (with and without `Retry-After`) then 200 → retried with expected
  `time.sleep` (monkeypatched); beyond max retries → raises. UA header present on requests.
- `fetch` end-to-end: only in-county stations are downloaded; rows outside `[start, end]`
  are filtered; empties suppressed; non-LA county → no requests/yields.
- Browser-UA requirement: a fake session returning SPA HTML → clear `ValueError`.

## Out of Scope

- Other Clarity OpenMap instances / networks (host and `networkId` are LAUSD constants).
- Parallel downloads (stays sequential under backoff).
- Backfill beyond the 30-day window or sub-hourly cadence.
- Any schema change or change to other providers.
