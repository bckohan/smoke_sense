# Visualize: --by toggle + chart subcommands — Design

**Date:** 2026-06-25
**Status:** Approved (design phase)
**Scope:** Add a `--by value|aqi` toggle to all `visualize` subcommands, and four non-map
chart subcommands (`series`, `scatter`, `aggregate`, `histogram`) behind a new
`ChartRenderer` registry parallel to the existing `MapRenderer`.

## Goal

Let users plot stored metrics as charts (not just maps), choosing whether to plot the raw
value or the computed AQI, with the same provider/store data and a swappable chart engine.

## Key Decisions

| Decision | Choice |
|---|---|
| `--by value\|aqi` | On all visualize subcommands; default `value`; `aqi` only for AQI metrics |
| Chart engine | New `ChartRenderer` registry (matplotlib default), parallel to `MapRenderer` |
| Chart kinds | `series` (line per station, station filter), `scatter` (all points), `aggregate` (mean line + min/max band), `histogram` |
| Shared data | `metric_observations` helper feeds charts; `station_means` gains a `by` param |
| Deps | none new (matplotlib already present; charts don't use contextily) |

## Shared infrastructure (`visualize.py`)

- `metric_observations(data_dir, fips, start, end, metric) -> pd.DataFrame` with columns
  `timestamp, station_id, value, aqi` (= `store.read_range` filtered to `metric.value`).
  Empty (those columns) when there's no data.
- `station_means(..., by="value")` gains a `by` parameter selecting which column to mean
  (`value` or `aqi`); default `value` (back-compatible). Used by `mean-map`.
- `resolve_by(metric, by) -> str` helper: returns the column name; raises `ValueError` if
  `by == "aqi"` and `metric not in AQI_METRICS`.
- `y_label(metric, by) -> str`: `f"{metric.value} ({metric.unit})"` for value, `"AQI"` for
  aqi (used as axis/colorbar label).

## ChartRenderer registry (`visualize.py`)

```python
class ChartRenderer(ABC):
    name: str
    @abstractmethod
    def render_series(self, obs, *, y_column, y_label, title, palette, output) -> Path: ...
    @abstractmethod
    def render_scatter(self, obs, *, y_column, y_label, title, palette, output) -> Path: ...
    @abstractmethod
    def render_aggregate(self, obs, *, y_column, y_label, title, palette, output, band=True) -> Path: ...
    @abstractmethod
    def render_histogram(self, obs, *, y_column, y_label, title, palette, output, bins=30) -> Path: ...
```
- `register_chart_renderer` / `get_chart_renderer(name)` (mirrors the map registry;
  unknown name raises `KeyError`).
- `MatplotlibChartRenderer` (name `"matplotlib"`, default), Agg backend, saves PNG,
  `plt.close(fig)`:
  - **series:** one line per `station_id` (x=`timestamp`, y=`y_column`), colors sampled
    from the palette colormap; legend of station IDs.
  - **scatter:** all observations as points (x=`timestamp`, y=`y_column`), colored by
    station.
  - **aggregate:** mean of `y_column` across stations per timestamp as a line; when
    `band`, a shaded min/max band.
  - **histogram:** distribution of `y_column` over all observations, `bins` bins.
- `obs` is the long frame from `metric_observations` (already filtered to the metric and,
  for `series`, to selected stations).

`MapRenderer`/`mean_map` are unchanged except `mean_map` forwards `by` into `station_means`
and uses `y_label` for the colorbar.

## CLI (`bin/visualize.py`)

Shared options on every subcommand: `FIPS`, `--start` (required), `--end` (default today),
`--metric` (required, symmetric `Metric`), `--by value|aqi` (default `value`),
`--palette` (default `YlOrRd`), `--output` (PNG path), `--renderer` (default
`matplotlib`), `--output-dir` (default `./data`). Default output:
`{output_dir}/{fips}/{metric}_{by}_{start}_{end}_{kind}.png` (kind ∈
mean/series/scatter/aggregate/histogram).

Subcommands:
- `mean-map` *(existing)* — add `--by`; keep `--basemap/--no-basemap`. Colorbar labeled via
  `y_label`; colors by mean of the chosen column.
- `series` — `--station` (repeatable; default all). Per-station lines.
- `scatter` — all points.
- `aggregate` — `--band/--no-band` (default on).
- `histogram` — `--bins` (default 30).

Each: validate FIPS; `Metric(metric)` (invalid → `BadParameter`); `resolve_by` (bad combo
→ `BadParameter`); load data; empty → `no data for {fips}/{metric} in {range}` message, no
file; else render via the chart/map renderer and print the written path. Unknown renderer
→ `BadParameter`.

## Error Handling

- Invalid FIPS / metric / renderer → `typer.BadParameter`.
- `--by aqi` with a non-AQI metric → `typer.BadParameter` ("AQI only for PM2.5/PM10/O3").
- `series --station` with IDs absent from the data → those drop out; if nothing remains,
  the no-data message.
- Empty data → message, no file, exit 0.

## Testing

- `metric_observations`: filtered columns; empty when absent.
- `resolve_by`: returns `value`/`aqi`; raises for `aqi` + non-AQI metric.
- `station_means(by="aqi")`: means the aqi column.
- `MatplotlibChartRenderer`: each of the four methods writes a non-empty PNG (Agg);
  `series` honors the station subset; `aggregate` works with and without band;
  `histogram` respects `bins`. `get_chart_renderer` unknown → raises.
- CLI: each subcommand writes the expected PNG for a seeded multi-station store; `--by aqi`
  on PM2.5 works, on temperature exits non-zero; invalid metric/renderer exit non-zero;
  no-data prints the message; `mean-map --by aqi` colors by mean AQI.

## Out of Scope

- Interactive/HTML chart renderers (registry leaves room).
- Multi-metric overlays; per-series custom styling beyond palette/bins/band.
- Changing map rendering.
