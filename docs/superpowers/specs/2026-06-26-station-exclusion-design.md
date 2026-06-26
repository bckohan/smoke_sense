# Station-Exclusion Filter Design

**Date:** 2026-06-26
**Status:** Approved

## Goal

Let a user manually exclude a known-bad set of sensor stations from any
visualization or summary operation, by station ID. This complements the existing
statistical/range outlier filter: some sensors are known to be broken and should
be dropped wholesale, not judged by their own distribution.

## Decisions

- **Part of the outlier filter.** Station exclusion lives inside the outlier
  pipeline and is gated by the existing `--outlier-filter/--no-outlier-filter`
  toggle. Disabling the outlier filter also stops excluding stations.
- **Repeatable CLI flag.** Stations are given via a repeatable `--exclude-station`
  option. No file input, no config file (consistent with the prior
  "defaults in code + CLI override, no config file" decision for outliers).
- **Exact, case-sensitive match** on `station_id`. No globs, no per-county
  scoping, no source filtering (YAGNI).

## Behavior

Station exclusion is the **first** check in the outlier pipeline, before
range/zscore/iqr. Every row whose `station_id` is in the user-given set is
dropped. The empty set (the default) drops nothing and is a fast no-op.

Because `filter_outliers` attributes each dropped row to the **first** matching
check, excluded-station rows are counted under the `"station"` check in
`OutlierReport.per_check`, and their per-metric counts flow into
`per_metric`/`total` like any other drop. No report schema change is needed: the
`filtered` column in `summary` and the INFO removal log already reflect total
drops.

## Components

### `src/smoke_sense/outliers.py` (pure module)

- `OutlierConfig` gains `exclude_stations: frozenset[str] = frozenset()`.
- New `station_mask(df, exclude_stations) -> pd.Series`: `True` where
  `station_id` is in the set. Empty set or empty frame → all-`False`.
- `filter_outliers` prepends `("station", station_mask(df, config.exclude_stations))`
  to the checks list, so it runs before range/zscore/iqr and owns attribution of
  the rows it drops.

### `src/smoke_sense/bin/_outlier_cli.py` (CLI plumbing)

- `build_config(...)` gains `exclude_stations: list[str]`, stored as a
  `frozenset` on the config.
- `filter_frame(...)` and `make_filter(...)` gain `exclude: Optional[list[str]]`
  and thread it into `build_config`.

### Command wiring

- A repeatable `--exclude-station` option is added to `summary` and all five
  `visualize` subcommands. The flag string is byte-identical across all
  commands. Each command passes the collected list into `make_filter` /
  `filter_frame`.

## Data flow

```
CLI --exclude-station S1 --exclude-station S2
  -> _outlier_cli.make_filter(..., exclude=["S1","S2"])
  -> build_config(..., exclude_stations=["S1","S2"])  # -> frozenset
  -> filter_outliers(df, cfg)
       checks = [("station", station_mask), ("range", ...), ("zscore", ...), ...]
  -> clean df with S1/S2 rows removed; per_check["station"] = N
```

## Error handling

- An exclude flag with no rows matching is not an error: it simply drops nothing
  (a station may legitimately be absent from a given county/date range).
- When the outlier filter is disabled (`--no-outlier-filter`), exclusion is
  skipped along with the rest of the filter (per the toggle-scope decision).

## Testing

- `station_mask`: match, no-match, empty set, empty frame.
- `filter_outliers`: excluded rows attributed to `per_check["station"]`; no
  double-counting when an excluded row would also be a range/zscore outlier
  (it counts once, under `station`); `total`/`per_metric` include them.
- `_outlier_cli`: `--exclude-station` values reach `OutlierConfig.exclude_stations`.
- CLI integration: one test on `summary` and one on a `visualize` subcommand
  asserting excluded stations' rows are gone from the result.
