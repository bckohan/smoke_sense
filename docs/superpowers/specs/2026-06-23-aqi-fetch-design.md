# AQI Data Fetch & Common Format — Design

**Date:** 2026-06-23
**Status:** Approved (design phase)
**Scope:** The `fetch` CLI command, its data providers, and the common on-disk/in-memory
data format. Visualization and forecasting are out of scope for this spec.

## Goal

Download air-quality time series for one or more US counties over a time range from
multiple public sources, normalize them into a single tidy format, and persist them as
Parquet for downstream visualization and smoke-movement forecasting.

The command accepts **county FIPS codes** and a **time range**, fetches from all
configured providers, computes AQI where the source does not supply it, and writes a
Parquet file per county.

## Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Architecture | Pluggable multi-provider behind one interface | Sources differ wildly in API/geo/shape; isolate them |
| First providers | EPA AQS **and** PurpleAir | Regulatory baseline + high-cadence smoke data |
| Geography input | 5-digit county FIPS | Native to AQS; resolvable to bbox for PurpleAir |
| Cadence | Finest available (AQS hourly; PurpleAir ~2 min) | Smoke forecasting benefits from fine resolution |
| In-memory format | pandas DataFrame, tidy long format | Easy concat/filter/pivot; ML- and viz-friendly |
| On-disk format | Parquet | Compact, typed, lossless round-trip |
| Pollutants | PM2.5, PM10, O3 (the "smoke set") | PM2.5 is primary; PM10/O3 add context |
| AQI | Computed via EPA NowCast + breakpoints | Hourly sources carry no precomputed AQI |

## Architecture

The pipeline separates *where data comes from* (providers) from *what shape it takes*
(the common format) and *how it is stored* (persistence). Each unit is independently
testable.

```
src/smoke_sense/
  bin/fetch.py            CLI: parse FIPS + time range + options, orchestrate, write
  providers/
    __init__.py           provider registry
    base.py               AQIProvider ABC + registration helper
    aqs.py                EPAAQSProvider
    purpleair.py          PurpleAirProvider (uses geo.py + EPA correction)
  geo.py                  county FIPS -> bounding box (bundled data file)
  aqi.py                  NowCast + breakpoint AQI computation (shared)
  data.py                 schema, Pollutant enum, validate(), parquet I/O
  data/
    county_bbox.parquet   bundled county FIPS -> bbox lookup (~3,200 rows)
```

### Provider interface (`providers/base.py`)

```python
class AQIProvider(ABC):
    name: str                       # e.g. "aqs", "purpleair"
    supported: set[Pollutant]       # which pollutants this source can serve

    @abstractmethod
    def fetch(
        self,
        county_fips: str,
        start: date,
        end: date,
        pollutants: list[Pollutant],
    ) -> pd.DataFrame:
        """Return a DataFrame conforming to the common schema (data.validate'd)."""
```

A module-level registry maps provider name -> class so the CLI can resolve
`--source` values and default to "all registered". Adding a future provider
(AirNow, OpenAQ) means adding one file and registering it.

Each provider:
- Filters the requested `pollutants` down to its `supported` set; if a requested
  pollutant is unsupported (e.g. PurpleAir + O3) it warns and skips that pollutant.
- Returns rows conforming to the common schema and calls `data.validate()` before
  returning, so malformed source data fails at the boundary.

## Common Data Format (`data.py`)

Tidy long format: one row per (timestamp, station, pollutant) observation.

| column | dtype | description |
|---|---|---|
| `timestamp` | datetime64[ns, UTC] | observation time (UTC) |
| `county_fips` | string (5 chars) | requested county, e.g. `06037` |
| `station_id` | string | provider's monitor/site ID |
| `latitude` | float64 | monitor latitude |
| `longitude` | float64 | monitor longitude |
| `pollutant` | category | `PM2.5` \| `PM10` \| `O3` |
| `value` | float64 | concentration (EPA-corrected for PurpleAir PM2.5) |
| `unit` | category | e.g. `µg/m³`, `ppm` |
| `aqi` | Int16 (nullable) | NowCast AQI for the observation's hour |
| `source` | category | provider name, e.g. `aqs`, `purpleair` |

`data.py` provides:

- `Pollutant` enum (`PM2_5`, `PM10`, `O3`) mapping friendly names <-> AQS parameter
  codes (PM2.5 `88101`, PM10 `81102`, O3 `44201`) and canonical unit. Shared
  vocabulary for CLI and providers.
- The canonical column spec + dtypes as a single source of truth (e.g. `SCHEMA`).
- `validate(df) -> pd.DataFrame`: coerces dtypes, asserts required columns present and
  required fields non-null. Providers call this before returning.
- `write_parquet(df, path)` / `read_parquet(path)`: persistence with schema enforced.

**Why long format:** trivial to concat across counties/sources, filter by pollutant,
and pivot to wide (time x station) when a model needs it. Parquet preserves
dtypes/categories for lossless round-trips. A single output file may contain rows from
multiple sources (distinguished by `source`), enabling regulatory-vs-sensor comparison.

## AQI Computation (`aqi.py`)

Hourly sources do not supply AQI, so it is computed here and reused by both providers.

- **NowCast**: EPA's weighted trailing-window algorithm — 12-hour window for PM2.5/PM10,
  8-hour for O3 — producing an hourly NowCast concentration per pollutant per station.
- **Breakpoint conversion**: piecewise-linear map from NowCast concentration to the
  0–500 AQI scale using EPA breakpoint tables per pollutant.
- Raw `value` retains native sample resolution; `aqi` is the NowCast AQI of each row's
  containing hour (sub-hourly rows inherit their hour's AQI).
- Pure functions over a pollutant + a time-indexed concentration series; no I/O.

## Geo Resolution (`geo.py`)

PurpleAir queries by bounding box, not FIPS. To avoid heavy GIS dependencies, a small
lookup table (county FIPS -> min/max lat/lon, ~3,200 rows, derived once from Census
TIGER and committed as `data/county_bbox.parquet`) is loaded and queried.

- `bbox_for_county(fips: str) -> BBox` returns the bounding box; unknown FIPS raises a
  clear error.

## Provider Specifics

### EPA AQS (`providers/aqs.py`)

- Endpoint: `sampleData/byCounty` (hourly samples).
- County FIPS split into `state` (digits 1–2) + `county` (digits 3–5).
- API limits each request to a single calendar year and up to 5 parameter codes;
  the provider splits a multi-year range into per-year requests and concatenates.
- Credentials: `--email`/`--api-key` or env `AQS_EMAIL` / `AQS_API_KEY`.
- Supports PM2.5, PM10, O3.

### PurpleAir (`providers/purpleair.py`)

- Resolve county FIPS -> bbox via `geo.py`.
- `GET /v1/sensors` (bbox) to list sensors in the county, then
  `GET /v1/sensors/:id/history` for time series (selectable averaging: 2/10/30/60 min).
- Apply EPA US correction to PM2.5: `0.524·PA_cf1 − 0.0862·RH + 5.75` (RH from the
  sensor's humidity field). Stored `value` is the corrected, AQS-comparable number.
- Credentials: `--purpleair-key` or env `PURPLEAIR_API_KEY`.
- Supports PM2.5, PM10 (no O3).

## CLI (`bin/fetch.py`)

```
smoke-sense fetch COUNTY_FIPS... \
    --start 2023-06-01 --end 2023-09-30 \
    [--source aqs --source purpleair]          # default: all registered \
    [--pollutant PM2.5 --pollutant PM10 ...]    # default: PM2.5, PM10, O3 \
    [--output ./data/] \
    [--email ... --api-key ... --purpleair-key ...]   # else env vars
```

- `COUNTY_FIPS`: one or more 5-digit county FIPS (variadic positional).
- `--start` / `--end`: ISO dates (Typer-native `datetime`).
- Resolves credentials flags -> env vars; missing required creds for a selected source
  fail fast before any network call.
- `rich` progress feedback during fetching (already a dependency).
- For each county: run each selected provider, concat results, `data.validate()`, write
  one Parquet file.

## Storage

- One Parquet file per county per run, written via `data.write_parquet`.
- Default name `{fips}_{start}_{end}.parquet` under `--output` (default `./data/`).
- If `--output` ends in `.parquet` and exactly one county is requested, write that exact
  path.

## Error Handling

- Invalid FIPS (not 5 digits) -> fail fast with a clear message.
- Unknown FIPS (not in bbox table, for PurpleAir) -> clear error.
- HTTP/network errors -> retry with backoff, then surface a message naming the
  county/year/source that failed.
- A county with zero monitors for a pollutant -> warn and continue (not a crash).
- Unsupported pollutant for a provider -> warn and skip that pollutant for that source.

## Testing

- `data.validate` and Parquet round-trip (dtypes/categories preserved).
- NowCast + breakpoint conversion tested against EPA's published worked example
  (known input concentrations -> known AQI).
- PurpleAir EPA correction formula tested against known input.
- `geo.bbox_for_county` tested for a known county and an unknown FIPS error.
- Each provider tested against a recorded/sample API JSON fixture — no live network in
  tests.

## Dependencies

Add: `pandas`, `pyarrow`, `requests` (or `httpx`). Dev: `pytest`. (`typer`, `rich`
already present.)

## Out of Scope

- Visualization (`bin/visualize.py`) and forecasting (`bin/forecast.py`).
- Additional providers beyond AQS and PurpleAir (interface leaves room for them).
- Sub-county (place-code) geography and non-US sources.
