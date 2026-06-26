# Configurable Outlier Filter — Design

**Date:** 2026-06-26
**Status:** Approved (design phase)
**Scope:** A configurable outlier filter that removes likely-erroneous sensor readings,
applied by default before visualization, summary, and (future) simulation operations.

## Goal

Drop sensor readings that "look so strange they are probably in error" from the long
observation frame, before any analysis consumes it. Two detection families — physical
range bounds and per-station statistical checks (z-score / IQR) — each individually
toggleable, with sensible code defaults overridable from the CLI. On by default; opt out
with `--no-outlier-filter`.

## Key Decisions

| Decision | Choice |
|---|---|
| Detection methods | Physical range bounds + statistical (modified z-score, IQR), each toggleable |
| Action on outliers | Drop the rows; log how many were removed |
| Statistical scope | Per `(station_id, metric)` over the window (a sensor vs. *itself*) |
| Defaults | Range ON; modified z-score ON at 3.5; IQR OFF, k=3.0 when enabled |
| Configuration | Code defaults (`DEFAULT_CONFIG`), overridable via CLI flags (no config file) |
| Default behavior | Applied by default on visualize/summary/forecast; `--no-outlier-filter` disables |
| Architecture | Dedicated pure `outliers.py` module + a shared CLI helper |

## Why per-station statistical scope

Aggressive cross-station statistical filtering would delete *real* extreme pollution (a
wildfire spike is a true high value, not an error) — the opposite of what a smoke-sensing
tool wants. Judging each reading against the same sensor's own distribution in the window
catches a sensor disagreeing with itself (stuck/jumpy hardware) while preserving genuinely
high-but-consistent readings.

## Architecture

```
src/smoke_sense/outliers.py        # pure detection + filtering (no I/O, no CLI)
src/smoke_sense/bin/_outlier_cli.py # shared Typer options + apply helper
src/smoke_sense/bin/{visualize,summary}.py  # call the helper after read_range
src/smoke_sense/summary.py         # summarize() gains optional `filtered` counts
```

`forecast` is currently a bare stub that reads no data, so this feature does **not** modify
it. The shared helper in `_outlier_cli.py` is designed to be reused there unchanged once the
simulation is built (see Out of Scope).

### `outliers.py` (pure)

- **`OutlierConfig`** dataclass:
  - `range_enabled: bool = True`
  - `bounds: dict[Metric, tuple[float, float]]` — per-metric physical limits
  - `zscore: float | None = 3.5` — per-station modified-z threshold; `None` disables
  - `iqr: float | None = None` — per-station IQR multiplier; a number enables it
  - `min_group: int = 5` — statistical checks skip `(station, metric)` groups smaller than this
- **`DEFAULT_CONFIG`** — module-level instance with the bounds below.
- **`DEFAULT_IQR_K: float = 3.0`** — the multiplier used when IQR is enabled without an
  explicit value.
- **Pure checks**, each returning a boolean `pd.Series` aligned to `df.index`, `True` =
  outlier:
  - `range_mask(df, bounds) -> Series` — `value < low` or `value > high` for the row's
    metric. Rows whose metric has no configured bound are never flagged by this check.
  - `zscore_mask(df, threshold, min_group) -> Series` — per `(station_id, metric)` group:
    a **modified (MAD-based) z-score**, `0.6745 * abs(value - median) / MAD > threshold`,
    where `MAD = median(abs(value - median))`. This is robust to a lone spike inflating its
    own group's spread (standard mean/std z-score masks such spikes). Groups with
    `< min_group` rows or `MAD == 0` (or NaN) flag nothing.
  - `iqr_mask(df, k, min_group) -> Series` — per `(station_id, metric)` group: outside
    `[Q1 - k*IQR, Q3 + k*IQR]`. Same small-group / zero-spread skip rule.
- **`filter_outliers(df, config=DEFAULT_CONFIG) -> tuple[pd.DataFrame, OutlierReport]`**:
  - Builds the union (logical OR) of the enabled checks' masks.
  - Returns `(clean_df, report)` where `clean_df` is `df[~mask]` (index reset) and
    `report` is an `OutlierReport`.
  - On empty input, returns the empty frame and a zeroed report.
- **`OutlierReport`** dataclass:
  - `total: int` — rows dropped.
  - `per_metric: dict[str, int]` — dropped count keyed by metric value string.
  - `per_check: dict[str, int]` — dropped count attributable to each check (range/zscore/
    iqr); informational. A row flagged by more than one check is counted once, under the
    first check that flagged it, in the order range → zscore → iqr.

### Default physical bounds

Starting values (all overridable); `(low, high)` in each metric's canonical unit:

| Metric(s) | low | high |
|---|---|---|
| PM2.5, PM2.5_CF1, PM2.5_ATM | 0 | 1000 |
| PM10, PM10_CF1, PM10_ATM, PM1.0_CF1, PM1.0_ATM | 0 | 2000 |
| O3 (ppm) | 0 | 0.5 |
| CO (ppm) | 0 | 50 |
| SO2, NO2 (ppb) | 0 | 2000 |
| Pb (µg/m³) | 0 | 10 |
| temperature (°C) | -50 | 60 |
| humidity (%) | 0 | 100 |
| pressure (hPa) | 800 | 1100 |
| wind_speed (m/s) | 0 | 120 |
| wind_dir (deg) | 0 | 360 |
| VOC (iaq) | 0 | 1000 |

### Shared CLI helper (`bin/_outlier_cli.py`)

- A function `apply_outlier_filter(df, *, enabled, zscore, iqr, no_range, bounds) ->
  tuple[pd.DataFrame, OutlierReport]` that:
  - If `not enabled`, returns `(df, zeroed report)` unchanged.
  - Else builds an `OutlierConfig` from `DEFAULT_CONFIG` with overrides applied
    (`range_enabled = not no_range`; `zscore`/`iqr` replaced when provided; `bounds`
    merged with parsed overrides) and calls `filter_outliers`.
  - Logs `"filtered {total} outlier rows ({per_metric})"` at INFO via the module logger.
- Override parsing: `parse_bound("PM2.5:0:500") -> (Metric.PM2_5, (0.0, 500.0))`; invalid
  forms raise `ValueError` (surfaced by callers as `typer.BadParameter`).
- The shared option set, added to each consuming command:
  - `--outlier-filter / --no-outlier-filter` (default on)
  - `--outlier-zscore FLOAT` (override z threshold; `0`/negative disables the z check)
  - `--outlier-iqr / --no-outlier-iqr` (enable the IQR check; off by default)
  - `--outlier-iqr-k FLOAT` (IQR multiplier; default 3.0 = `DEFAULT_IQR_K`)
  - `--no-outlier-range` (disable the physical-bounds check)
  - `--outlier-bound METRIC:LOW:HIGH` (repeatable; override/add a per-metric bound)

  CLI note: IQR is off by default; `--outlier-iqr` turns it on at k = `DEFAULT_IQR_K` (3.0);
  `--outlier-iqr --outlier-iqr-k 1.5` sets k explicitly.

## Data flow

```
store.read_range(...) -> raw_df
  -> apply_outlier_filter(raw_df, <flags>) -> (clean_df, report)
     -> visualize: metric_observations/station_means consume clean_df
     -> summary: summarize(clean_df, start, end, filtered=report.per_metric)
     -> forecast (future): reuses the same helper when the simulation is built
```

Each command calls the filter immediately after `read_range`, before any metric-specific
processing. `visualize`'s helpers (`metric_observations`, `station_means`) keep reading via
`store.read_range` today; to insert the filter without rereading, the CLI layer reads the
range once, filters, and passes the clean frame down. (Implementation detail for the plan:
add frame-accepting variants or have the CLI filter then call the existing helpers with the
already-read range — resolved during planning; the public helper contract in `visualize.py`
is not part of this spec's surface.)

## Summary integration

- `summarize(df, start, end, filtered: dict[str, int] | None = None)`:
  - Each entry in the `metrics` list gains `"filtered": int` — the dropped count for that
    metric from `filtered` (0 when absent).
  - `--json` output includes the new field automatically.
- The Rich metrics table in `bin/summary.py` gains a `filtered` column.
- `summary` filters by default using the shared options.
- **Edge case:** a metric whose rows are *entirely* removed has no clean rows and therefore
  no `metrics` entry; its dropped count is not shown as a table row. Such fully-removed
  metrics are reported in the INFO log line (`per_metric` includes them). We do not
  fabricate an empty-stats row.

## Error Handling

- Invalid `--outlier-bound` (bad metric, non-numeric, low ≥ high) → `typer.BadParameter`.
- `--outlier-zscore <= 0` disables the z check (documented), not an error.
- Empty range / all-rows-filtered → existing "no data" messaging in each command; the
  filter never raises on empty input.
- Statistical checks never raise on degenerate groups (single point, zero variance, NaNs);
  they simply flag nothing for those groups.

## Testing

- **Unit (`tests/test_outliers.py`):**
  - `range_mask`: flags below-low, above-high, and negative values; leaves in-range and
    no-bound-configured metrics untouched.
  - `zscore_mask`: flags a clear per-station spike; does not flag across-station spread
    (two stations, each internally consistent); skips groups `< min_group`; `std==0` →
    none.
  - `iqr_mask`: flags an extreme value with k=3; respects custom k; small-group/zero-spread
    skip.
  - `filter_outliers`: drops the union, `clean_df` excludes flagged rows, `report.total` /
    `per_metric` / `per_check` counts correct (including overlap counted once, range first).
  - `OutlierConfig` override merge; `parse_bound` valid + invalid forms.
  - Empty input → empty clean frame + zeroed report.
- **Integration:**
  - `apply_outlier_filter` with each flag combination produces the expected config/result;
    `--no-outlier-filter` returns input unchanged.
  - `summary` on a seeded store with injected garbage: drops it by default, shows correct
    `filtered` counts per metric, and `--no-outlier-filter` keeps the garbage (counts 0).
  - `visualize` (e.g. `aggregate`) on the same store: default run excludes the garbage from
    the rendered data; `--no-outlier-filter` includes it. (Assert on the cleaned frame /
    no-crash + file written, not on pixels.)

## Out of Scope

- Temporal-spike and spatial-neighbor detection (deferred; the check structure leaves room
  to add more masks later).
- Persisting an `is_outlier` flag back to the store (we drop in-memory only; raw data on
  disk is untouched).
- A config *file* (code defaults + CLI overrides only, per decision).
- Any change to `forecast`; it is a bare stub that reads no data. When the simulation is
  built it will reuse `_outlier_cli.apply_outlier_filter` unchanged.
