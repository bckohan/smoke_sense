# Rank-Stations Command Design

**Date:** 2026-06-26
**Status:** Approved

## Goal

Add a top-level `smoke-sense rank` command that lists a county's sensor stations
ordered by an aggregation (min / max / mean) of a chosen metric, ascending or
descending. This answers "which stations read highest/lowest for metric X over
this period?" without producing a plot.

## Decisions

- **New top-level command** `rank` (alongside `fetch`/`summary`/`forecast`),
  registered in `bin/__init__.py`.
- **Sort:** two-sided `--desc/--asc` flag, defaulting to `--desc` (highest first).
- **Result size:** `--limit`, default `10`; `--limit 0` lists all stations.
- **Scope:** one or more FIPS, printing a separate ranked table per county
  (like `summary`). No cross-county combined ranking (YAGNI).
- **Aggregation:** `--agg min|max|mean`, default `mean`.
- **Quantity:** `--by value|aqi`, default `value`, AQI-eligibility validated via
  the existing `visualize.resolve_by`.
- **Filtering:** the outlier filter and `--exclude-station` are on by default,
  identical to `summary` (reuses `_outlier_cli`).
- No lat/lon column, no per-source breakdown (YAGNI).

## Components

### `src/smoke_sense/ranking.py` (new, pure module)

```python
def rank_stations(obs, *, column, agg, descending=True, limit=10) -> pd.DataFrame
```

- `obs` is the long frame returned by `visualize.metric_observations`
  (columns `timestamp, station_id, value, aqi`).
- `column` is `"value"` or `"aqi"`; `agg` is one of `"min"`, `"max"`, `"mean"`.
- Drops rows where `column` is null, groups by `station_id`, computes the
  aggregate and a `count` (number of non-null observations behind each
  station's value), sorts by the aggregate value using a **stable** sort
  (`kind="mergesort"`) ascending or descending, then truncates to `limit`
  (a `limit <= 0` means "all").
- Returns columns `station_id, value, count`, where `value` holds the
  aggregated statistic regardless of which `agg` was used.
- An empty `obs` (or one with no non-null values in `column`) yields an empty
  frame with those three columns.

This module is pure (no I/O, no Typer), mirroring `summary.py`.

### `src/smoke_sense/bin/rank.py` (new CLI)

`rank(...)` Typer command with options:

- `county_fips: List[str]` (argument) — validated 5-digit FIPS.
- `--start` (required), `--end` (defaults to today).
- `--metric` (required) — parsed to `Metric`; invalid → `typer.BadParameter`.
- `--agg` (default `mean`) — validated against `{min, max, mean}`; invalid →
  `typer.BadParameter`.
- `--by` (default `value`) — resolved via `visualize.resolve_by`; invalid combo
  (e.g. AQI for a non-AQI metric) → `typer.BadParameter`.
- `--desc/--asc` (default `--desc`).
- `--limit` (default `10`).
- `--json` (default off).
- `--output` (data directory, default `./data`).
- The six outlier options (`--outlier-filter/--no-outlier-filter`,
  `--outlier-zscore`, `--outlier-iqr/--no-outlier-iqr`, `--outlier-iqr-k`,
  `--no-outlier-range`, `--outlier-bound`) plus `--exclude-station`, with flag
  strings byte-identical to `summary`.

Flow per county: build the outlier filter via `_outlier_cli.make_filter`, call
`visualize.metric_observations` (which applies the filter), resolve the column
via `resolve_by`, call `ranking.rank_stations`, then render.

Rendering:
- Rich table per county with columns `#` (1-based rank), `station_id`,
  `<agg>` (the aggregate value), `count`. Standard yellow "no data" message
  when a county has no ranked stations.
- `--json` emits `{fips: {"metric": ..., "by": ..., "agg": ..., "order":
  "desc"|"asc", "stations": [{"station_id", "value", "count"}, ...]}}`.

### `src/smoke_sense/bin/__init__.py`

Register: `app.command()(rank.rank)`.

## Data flow

```
rank 06037 --metric PM2.5 --agg mean --desc --limit 10 --exclude-station S9
  -> make_filter(..., exclude=["S9"])
  -> metric_observations(data_dir, fips, start, end, PM2.5, outlier_filter=f)
  -> rank_stations(obs, column="value", agg="mean", descending=True, limit=10)
  -> Rich table (or JSON)
```

## Error handling

- Invalid FIPS / metric / agg / by → `typer.BadParameter` (non-zero exit), same
  pattern as `summary` and `visualize`.
- A county with no data (or all-null in the chosen column) → "no data" message
  (table mode) or an empty `stations` list (JSON mode); exit 0.
- `--limit 0` (or negative) → all stations.

## Testing

`tests/test_ranking.py` (pure):
- mean/min/max correctness for a small multi-station frame.
- descending (default) vs ascending order.
- stable order on ties (equal aggregates keep input station order).
- `limit` truncation; `limit=0` returns all.
- null-dropping: rows with null `column` excluded; `count` reflects non-null obs.
- empty `obs` → empty result with the three columns.

`tests/test_rank_cli.py` (CLI):
- default run prints a table (top 10, desc).
- `--json` shape: metric/by/agg/order keys + ordered `stations` list.
- `--asc` reverses order.
- `--limit` truncates; bad `--agg` exits non-zero.
- `--exclude-station` removes a station from the ranking.
- multiple FIPS → one table per county.
- no-data county → "no data" message, exit 0.
