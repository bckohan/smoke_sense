# Data Summary Command — Design

**Date:** 2026-06-24
**Status:** Approved (design phase)
**Scope:** A `smoke-sense summary` command that reports what data is stored for given
county FIPS over a time range — coverage/gaps, a source/cadence/pollutant breakdown, and
per-pollutant station + value/AQI statistics — as rich tables or JSON.

## Goal

Let a user see, at a glance, what they have on disk for a county and date range: which
days are present vs missing, which sources/cadences/pollutants, how many rows and
stations, and value/AQI ranges. Read-only over the existing per-day store.

## Key Decisions

| Decision | Choice |
|---|---|
| Data source | The per-day store, by fips + range (`{data_dir}/{fips}/{date}.parquet`) |
| Content | Coverage & gaps; breakdown by source/cadence/pollutant; per-pollutant stations + value/AQI stats |
| Output | Rich tables by default; `--json` for machine-readable |
| Aggregation logic | Pure, Typer-free (`summary.summarize`), unit-testable |

## Architecture

```
store.py        + read_range(data_dir, fips, start, end) -> DataFrame
summary.py      (new) summarize(df, start, end) -> dict   # pure aggregation
bin/summary.py  (new) `summary` CLI: read store -> summarize -> render
bin/__init__.py register the summary command
```

- `store.read_range` reads the day files for dates in `[start, end]`, concatenates,
  validates, and filters rows to the window. The read-side counterpart to `store.write`.
- `summary.summarize` is a pure function over a frame returning the summary dict.
- `bin/summary.py` is a thin CLI rendering tables or emitting JSON.

## `store.read_range`

```python
def read_range(data_dir, fips, start: date, end: date) -> pd.DataFrame:
    """Concatenate the county's day files for dates in [start, end].

    Reads {data_dir}/{fips}/{day}.parquet for each day with a file, concatenates,
    and returns the validated frame restricted to timestamps within the window
    [start 00:00 UTC, (end + 1 day) 00:00 UTC). Returns empty_frame() if none.
    """
```

## `summary.summarize`

`summarize(df, start, end) -> dict` returns a per-county dict (JSON-serializable):

```python
{
  "range": {"start": "2026-06-16", "end": "2026-06-24"},
  "coverage": {
    "total_days": 9,
    "days_present": 7,
    "days_missing": ["2026-06-19", "2026-06-20"],
    "first_timestamp": "2026-06-16T00:00:00+00:00" | None,
    "last_timestamp": "2026-06-24T18:00:00+00:00" | None,
    "total_rows": 120345
  },
  "breakdown": [
    {"source": "purpleair", "pollutant": "PM2.5", "agg_window": 10, "rows": 98000},
    ...
  ],
  "pollutants": [
    {"pollutant": "PM2.5", "stations": 312, "sources": ["aqs", "purpleair"],
     "value": {"min": .., "p25": .., "p50": .., "mean": .., "p75": .., "max": ..},
     "aqi": {"min": .., "mean": .., "max": ..}}
  ]
}
```

- **Coverage/gaps:** `total_days` = days in `[start, end]` inclusive; `days_present` =
  distinct UTC dates with at least one row; `days_missing` = the in-range dates with no
  rows (sorted ISO strings); `first/last_timestamp` from the data (None if empty);
  `total_rows`.
- **Breakdown:** group by `(source, pollutant, agg_window)` → row counts; sorted for
  stable output.
- **Per-pollutant:** distinct `station_id` count, contributing sources (sorted), value
  stats (min/p25/p50/mean/p75/max) and AQI stats (min/mean/max). AQI stats computed over
  non-null `aqi` only; if all AQI null for a pollutant, its `aqi` is `None`.
- **Empty frame:** `total_rows: 0`, all days in `days_missing`, `first/last_timestamp`
  None, empty `breakdown` and `pollutants`.

The `fips` key is added by the CLI (the pure function is per-frame).

## CLI (`bin/summary.py`)

```
smoke-sense summary COUNTY_FIPS... --start DATE [--end DATE] [--output ./data] [--json]
```

- `COUNTY_FIPS`: one or more 5-digit FIPS (validated like `fetch`).
- `--start` required; `--end` optional, defaults to `date.today()`.
- `--output`: data directory (default `./data`), same option name as `fetch`.
- `--json`: emit machine-readable output instead of tables.
- No credentials (read-only, local).

Flow: for each FIPS → `store.read_range(output, fips, start_date, end_date)` →
`summary.summarize(df, start_date, end_date)` → render.

**Rich rendering (default), per county:**
- Header: `fips` and the range.
- Coverage table: days present/total, missing-day count + list, first/last timestamp,
  total rows.
- Breakdown table: source × pollutant × agg_window → rows.
- Per-pollutant table: pollutant, stations, sources, value min/p25/p50/mean/p75/max,
  AQI min/mean/max.
- Empty county → a clear `no data for {fips} in {start}..{end}` line.

**`--json`:** print `json.dumps({fips: summary, ...})` for all requested counties — only
that object on stdout, so it pipes cleanly.

## Error Handling

- Invalid FIPS (not 5 digits) → `typer.BadParameter`.
- Missing/empty data dir or county dir → reported as no data, not an error.
- A corrupt/unreadable day file surfaces via `store.read_range` (loud) rather than being
  silently skipped.

## Testing

- `store.read_range`: reads only in-range day files; concatenates; filters rows to the
  window; empty when the county dir is absent.
- `summary.summarize`: coverage with correct `days_missing`; breakdown grouping/counts;
  per-pollutant station counts, sources, value stats, AQI stats with null handling;
  empty-frame case.
- `bin/summary`: default (rich) run exits 0 and prints row counts; `--json` emits valid
  JSON parseable into the documented keys; invalid FIPS exits non-zero; a county with no
  data prints the no-data line (rich) / `total_rows: 0` (json).

## Out of Scope

- Plotting/visualization (the separate `visualize` command).
- Cross-county aggregation or trends over time.
- Writing summaries to disk.
- Summarizing arbitrary file paths outside the store layout.
