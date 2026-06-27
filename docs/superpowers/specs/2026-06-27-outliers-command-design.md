# Outliers Command Design

**Date:** 2026-06-27
**Status:** Approved

## Goal

Add an `outliers` command that runs the existing outlier filter (with its CLI
overrides) and prints a per-county list of stations ranked by the fraction of
their readings flagged as outliers — a quick way to find misbehaving sensors.

## Decisions

- **Rank by % flagged** (flagged / total readings), worst-first; `--limit`
  (default 10, `0` = all).
- **Summary columns:** station_id, readings, flagged, % flagged.
- **Multiple counties**, a separate ranked table per county (like `summary`/`rank`).
- Reuses the existing within-station filter and the six override flags. No new
  cross-station detection (so a uniformly-miscalibrated station whose readings
  are each self-consistent and within physical bounds will NOT appear — a known
  limitation of reusing the current filter).

## Components

### `src/smoke_sense/outliers.py`

- Refactor: extract the check assembly + mask combination from `filter_outliers`
  into `_evaluate_checks(df, config) -> tuple[pd.Series, dict[str, int]]`
  returning `(combined_mask, per_check)`. `filter_outliers` keeps identical
  behavior by calling it (it still computes `per_metric` and the report).
- New pure `station_outlier_counts(df, config=DEFAULT_CONFIG) -> pd.DataFrame`:
  - Columns `station_id, readings, flagged, fraction`.
  - `readings` = total rows per station; `flagged` = combined-mask rows per
    station; `fraction = flagged / readings`.
  - Keeps only stations with `flagged > 0`.
  - Sorted by `fraction` desc, then `station_id` asc (stable).
  - Empty input → empty frame with those four columns.

### `src/smoke_sense/bin/_outlier_cli.py`

- Add `config_from_flags(*, no_range, zscore, iqr_on, iqr_k, bound, exclude)
  -> OutlierConfig`: parse `--outlier-bound` specs (`ValueError ->
  typer.BadParameter`), derive `iqr=(iqr_k if iqr_on else None)`, and call
  `build_config(..., exclude_stations=exclude)`.
- Refactor `filter_frame` to build its config via `config_from_flags` (no
  behavior change).

### `src/smoke_sense/bin/outliers.py` (new)

`outliers(...)` Typer command:
- `county_fips: List[str]` (validated 5-digit), `--start` (required), `--end`
  (defaults to today), `--output` (data dir, default `./data`), `--json`,
  `--limit` (default 10).
- Override flags, byte-identical to `summary`: `--outlier-zscore`,
  `--outlier-iqr/--no-outlier-iqr`, `--outlier-iqr-k`, `--no-outlier-range`,
  `--outlier-bound` (repeatable), `--exclude-station` (repeatable). NOT
  `--outlier-filter/--no-outlier-filter` (the command is the filter).
- `--exclude-station` rows are **dropped from the frame before counting** (so an
  already-known-bad station is removed from the list, not reported at 100%
  flagged via the station check). The detection config is therefore built with
  `exclude=None`; exclusion is a pre-filter here, not a flag.
- Per county: `store.read_range` → drop excluded stations →
  `config_from_flags(..., exclude=None)` →
  `station_outlier_counts(df, config)` → `head(limit)` if `limit > 0` → render.
- Table columns: `#` (1-based), `station_id`, `readings`, `flagged`,
  `% flagged` (e.g. `12.3%`). "no data" yellow message when the county has no
  flagged stations (or no data). `--json` emits
  `{fips: {"stations": [{"station_id", "readings", "flagged", "fraction"}]}}`.

### `src/smoke_sense/bin/__init__.py`

Register: `app.command()(outliers.outliers)`.

## Data flow

```
outliers 06037 --start .. --outlier-bound PM2.5:0:50 --limit 10 --exclude-station sX
  -> df = store.read_range(output, fips, start, end)
  -> df = df[~df.station_id.isin({sX})]                   # excluded stations pre-dropped
  -> cfg = _outlier_cli.config_from_flags(no_range, zscore, iqr_on, iqr_k, bound, exclude=None)
  -> ranked = outliers.station_outlier_counts(df, cfg)   # flagged>0, sorted by fraction desc
  -> head(limit); Rich table or JSON per county
```

## Error handling

- Invalid FIPS / bad `--outlier-bound` → `typer.BadParameter` (non-zero exit),
  same as `summary`.
- County with no data or no flagged stations → "no data" message (table) or
  empty `stations` list (JSON); exit 0.

## Testing

`tests/test_outliers.py` (pure):
- `station_outlier_counts`: correct `readings`/`flagged`/`fraction`; sorted by
  fraction desc then station_id; only `flagged > 0` rows; empty frame → empty
  with the four columns.
- `_evaluate_checks` parity: `filter_outliers` behavior unchanged (existing
  tests must still pass).

`tests/test_outlier_cli.py`:
- `config_from_flags` maps flags to an `OutlierConfig` (zscore/iqr/bounds/
  exclude); bad bound spec → `typer.BadParameter`.

`tests/test_outliers_command.py` (CLI):
- seed a station with an out-of-range reading (forces a range flag); assert the
  table lists it and `--json` shape is `{fips: {"stations": [...]}}`.
- `--limit` truncates; `--exclude-station` removes a station; multi-FIPS → keys
  per county; no-data county → "no data", exit 0.
