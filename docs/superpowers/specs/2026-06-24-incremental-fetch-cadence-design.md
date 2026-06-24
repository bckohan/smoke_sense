# Incremental Fetch & Cadence Control — Design

**Date:** 2026-06-24
**Status:** Approved (design phase)
**Scope:** A CLI `--cadence` enum, an `agg_window` schema column recording aggregation,
a per-day on-disk store, incremental "fetch only the gaps" orchestration with a
`--refetch` override, provider-side cadence selection, and adaptive request chunking.

## Goal

Make `fetch` efficient and resumable: store accumulated data per county per day, fetch
only the days not already present at the requested cadence (or finer), record how each
value was aggregated, let providers fetch the finest cadence that fits the requested
window, and have providers chop oversized requests automatically.

## Key Decisions

| Decision | Choice |
|---|---|
| Cadence input | `--cadence` enum (minutes), default `TEN_MIN` |
| Cadence selection | `actual = max(supported ≤ requested)`, else `min(supported)` |
| Aggregation record | single `agg_window` column (minutes; `0` = raw) |
| Storage | `{data_dir}/{fips}/{date}.parquet`, one file per county per UTC day |
| Merge identity | `(timestamp, station_id, pollutant, source)`; finer `agg_window` wins |
| Fetch strategy | hybrid skip-covered by default; `--refetch` forces full fetch |
| Current day | always (re)fetched, through *now* |
| Chunking | adaptive: halve on over-range failure down to 1 day |

## Cadence (`cadence.py`)

```python
class Cadence(str, Enum):
    RAW = "RAW"            # real-time, ~2 min
    TEN_MIN = "TEN_MIN"
    THIRTY_MIN = "THIRTY_MIN"
    HOURLY = "HOURLY"
    SIX_HOURLY = "SIX_HOURLY"
    DAILY = "DAILY"

    @property
    def minutes(self) -> int:
        return _CADENCE_MINUTES[self]   # RAW=0, TEN_MIN=10, ... DAILY=1440
```

A `str`-valued enum (like `Pollutant`) so Typer exposes the names as `--cadence`
choices; `.minutes` yields the integer used by selection/merge logic.

- CLI `--cadence` accepts the names; default `TEN_MIN`.
- `resolve_cadence(supported: list[int], requested: int) -> int`:
  - candidates = `[c for c in supported if c <= requested]`
  - return `max(candidates)` if any, else `min(supported)`.
- Examples: PurpleAir `{0,10,30,60,360,1440}` @10 → 10; AQS `{60}` @10 → 60 (fallback).
- `0` (raw) is the finest; for both selection (`max ≤ requested`) and merge ordering,
  smaller minutes = finer, with `0` smallest/finest.

## Schema change (`data.py`)

Add one required column to `COLUMNS` and `REQUIRED_NON_NULL`:

| column | dtype | meaning |
|---|---|---|
| `agg_window` | `Int16` | minutes the value is aggregated over; `0` = raw/unaggregated |

This records both *whether* (`>0`) and *over what range* a value was aggregated. The
aggregation *method* (mean vs sample) is intentionally not recorded separately.

Schema is bumped: `read_parquet`/`validate` require `agg_window`. Existing day files
without it are treated as incompatible (no production data exists yet); they would need
to be re-fetched.

## Storage (`store.py`)

Layout: `{data_dir}/{fips}/{date}.parquet` (date = `YYYY-MM-DD`, UTC day). One file per
county per day holds all sources and cadences for that day. The old single-`.parquet`
and `{fips}_{start}_{end}.parquet` output modes are removed.

Functions:
- `day_path(data_dir, fips, day) -> Path`
- `merge_day(data_dir, fips, day, df)` — read existing day file (if present), `concat`,
  drop duplicates on `(timestamp, station_id, pollutant, source)` keeping the finer
  `agg_window`, `validate`, rewrite.
- `write(data_dir, fips, df)` — group an arbitrary-range frame by UTC day and
  `merge_day` each group.
- `coverage(data_dir, fips) -> dict[tuple[date, str], int]` — read existing day files and
  report, per `(day, source)`, the finest `agg_window` already stored. Drives gap
  detection.

Finer-wins dedup: sort so the finest `agg_window` is kept per identity key
(`0` finest, then ascending minutes), then `drop_duplicates(keep="first")`.

## Provider changes (`providers/`)

`base.py`:
- Add class attribute `supported_cadences: list[int]`.
- Add concrete `resolve_cadence(self, requested: int) -> int` using the rule above.
- Change the abstract signature to
  `fetch(self, county_fips, start, end, pollutants, cadence: int) -> pd.DataFrame`.

Each provider:
- Tags returned rows with `agg_window = cadence` it actually used.
- Performs **adaptive chunking**: begin from a per-provider initial span (AQS: per
  calendar year, as today; PurpleAir: per day), and on an over-range failure halve the
  span and retry, down to a single day. A 1-day request that still fails raises a clear
  error (does not loop). Detection is span-based: a `400` on a range longer than one day
  triggers a halve; a `400` on a one-day range is surfaced.

`aqs.py`: `supported_cadences = [60]`; cadence only sets `agg_window = 60`; keeps
per-year request splitting; hourly sample timestamps unchanged.

`purpleair.py`: `supported_cadences = [0, 10, 30, 60, 360, 1440]`; map the resolved
cadence to the `average` query param; `agg_window` = resolved cadence; keep the
time_stamp-field fix.

## Orchestration (`fetcher.py`, no Typer)

`fetch_county(data_dir, fips, start, end, pollutants, requested_cadence, providers, refetch)`:

1. For each provider P: `actual = P.resolve_cadence(requested_cadence)`.
2. Determine the UTC days spanning `[start, end]` (end defaults to today upstream).
3. **Hybrid (default):** using `store.coverage`, drop days where `(day, P.name)` is
   already stored at `agg_window <= actual`. The **current UTC day is never dropped**
   (it is incomplete).
4. **`--refetch`:** skip step 3; request all days.
5. Group the remaining days into contiguous ranges; for each range call
   `P.fetch(fips, range_start, range_end, pollutants, actual)`. For the current day the
   range end is *now* (not midnight), fixing the prior "`--end` excludes today" gap.
6. `store.write(data_dir, fips, result)` for each provider's frames.

`bin/fetch.py` stays thin: parse options, resolve credentials (unchanged), call
`fetcher.fetch_county`.

## CLI (`bin/fetch.py`)

```
smoke-sense fetch COUNTY_FIPS... --start DATE [--end DATE] \
    [--cadence TEN_MIN] [--refetch] \
    [--source ...] [--pollutant ...] \
    [--output ./data] [--credentials ...] [creds/env]
```

- `--cadence`: `Cadence` enum, default `TEN_MIN`.
- `--refetch`: force full fetch + merge (ignores coverage).
- `--output`: data directory (default `./data`); single-file mode removed.
- `--end` default (today), credential resolution, and FIPS validation unchanged.

## Error Handling

- Over-range API failures are handled by adaptive chunking; a 1-day failure surfaces a
  clear message naming county/day/source.
- A corrupt/unreadable day file raises during `coverage`/`merge_day` rather than being
  silently treated as "no coverage."
- Unknown `--cadence` value rejected by Typer enum parsing.

## Testing

- `resolve_cadence`: exact match, round-down to finest ≤ requested, AQS fallback to 60.
- `store`: round-trip; per-day split of a multi-day frame; finer-`agg_window`-wins dedup;
  `coverage` reports finest per `(day, source)`; merge into an existing day file.
- `fetcher`: hybrid skips covered days and fetches only gaps; always re-fetches the
  current day; `--refetch` requests everything; results land in the right day files
  (fake providers, no network).
- Adaptive chunking: a fake session that 400s on ranges > 1 day then succeeds → provider
  splits and still returns merged data; a 1-day 400 raises.
- CLI: `--cadence`/`--refetch` wiring; `--output` as data dir.
- Schema: `validate`/`read_parquet` require `agg_window`.

## Out of Scope

- Recording aggregation method (mean vs sample).
- Compaction of per-day files into larger partitions.
- Back-filling/migrating pre-existing `agg_window`-less files.
- The PurpleAir large-county sensor fan-out (1,295 sensors) performance work — tracked
  separately.
