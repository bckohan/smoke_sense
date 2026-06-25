# Visualize: mean-map Subcommand — Design

**Date:** 2026-06-25
**Status:** Approved (design phase)
**Scope:** Turn `visualize` into a subcommand group and add the first subcommand,
`mean-map`: a per-county map with each sensor as a dot color-coded by the mean value of a
chosen metric over a time period, with a pluggable rendering engine and configurable
palette. (Other visualization types come in later specs.)

## Goal

`smoke-sense visualize mean-map <fips> --start … --metric …` reads the per-day store,
computes each station's mean value of the metric over the window, and renders a map with
dots colored by that mean (matplotlib + basemap → PNG by default), behind a renderer
abstraction so engines can be swapped at runtime.

## Key Decisions

| Decision | Choice |
|---|---|
| Color basis | Mean raw `value` of the chosen metric per station (uniform; not the AQI column) |
| Renderer | Abstraction (registry) with a default matplotlib(+contextily)→PNG impl; `--renderer` selects |
| Basemap | contextily OpenStreetMap tiles; `--no-basemap` to skip; graceful fallback on tile failure |
| Palette | Named matplotlib colormap via `--palette` (default `YlOrRd`) |
| Command shape | `visualize` becomes a Typer sub-app; `mean-map` is its first subcommand |
| Aggregation | Pure, testable (`station_means`); plotting isolated in the renderer |
| Dependencies | add `matplotlib`, `contextily` |

## Architecture

```
src/smoke_sense/visualize.py        # renderer-agnostic helpers + MapRenderer registry
src/smoke_sense/bin/visualize.py    # Typer sub-app; `mean-map` subcommand
src/smoke_sense/bin/__init__.py     # app.add_typer(visualize.app, name="visualize")
```

`visualize.py`:
- `station_means(data_dir, fips, start, end, metric) -> pd.DataFrame` with columns
  `station_id, latitude, longitude, mean`: `store.read_range` → filter to `metric` →
  `groupby station_id` mean of `value` → join `store`'s `stations.parquet` for coordinates.
  Returns empty frame if no data.
- `MapRenderer` (ABC) + `register`/`get_renderer(name)` registry (mirrors the provider
  registry). Method:
  `render_point_map(points, *, value_label, palette, title, output, basemap=True) -> Path`
  where `points` has `latitude, longitude, mean`.
- `MatplotlibRenderer` (name `"matplotlib"`, default): Agg backend; scatter dots colored by
  `mean` via the palette colormap; colorbar labeled `value_label`; title; optional
  contextily basemap (`add_basemap(crs="EPSG:4326")`); save PNG to `output`.
- `mean_map(data_dir, fips, start, end, metric, *, palette, output, renderer, basemap)`:
  orchestrates `station_means` → `get_renderer(renderer).render_point_map(...)`; raises a
  domain error / returns a sentinel when there's no data so the CLI can message cleanly.

## CLI (`bin/visualize.py`)

`bin/visualize.py` becomes a `typer.Typer()` sub-app (it is currently a single stub
command). Registered in `bin/__init__.py` via `app.add_typer(visualize.app, name="visualize")`
(replacing the current `app.command()(visualize.visualize)`).

```
smoke-sense visualize mean-map FIPS --start DATE [--end DATE] --metric METRIC \
    [--palette YlOrRd] [--output PATH] [--renderer matplotlib] [--no-basemap] [--output-dir ./data]
```

- `FIPS`: single 5-digit county code (validated like fetch).
- `--start` required; `--end` defaults to `date.today()`.
- `--metric` required; parsed via symmetric `Metric(...)` (invalid → `BadParameter`).
- `--palette`: matplotlib colormap name; default `YlOrRd`.
- `--output`: PNG path; default `{output-dir}/{fips}/{metric}_{start}_{end}_mean.png`.
- `--renderer`: registered renderer name; default `matplotlib`; unknown → `BadParameter`.
- `--no-basemap`: disable tiles (default on).
- `--output-dir`: data directory to read from (default `./data`).
- Behavior: compute means → render → print the written path. Empty data → print
  `no data for {fips}/{metric} in {start}..{end}` and write nothing.

## Color & legend

Dots colored by per-station mean value using the palette colormap (continuous, normalized
to the data range). A colorbar legend labeled `mean {metric} ({Metric.unit})` (e.g.
`mean PM2.5 (µg/m³)`). Title includes fips, metric, and the date range.

## Basemap

Points are plotted in lon/lat (WGS84). When `basemap` is on, `contextily.add_basemap(ax,
crs="EPSG:4326", source=contextily.providers.OpenStreetMap.Mapnik)` overlays tiles
(contextily reprojects tiles to the axes CRS, so points need no reprojection). If tile
fetch raises (offline/network error), warn and render without a basemap rather than
failing.

## Error Handling

- Invalid FIPS → `typer.BadParameter`.
- Invalid `--metric` → `Metric(...)` ValueError wrapped as `typer.BadParameter`.
- Unknown `--renderer` → `get_renderer` raises `KeyError`, surfaced as `BadParameter`.
- No data for fips/metric/range → friendly message, no file, exit 0.
- Basemap tile failure → warning + basemap-less render (no crash).

## Testing

- `station_means` (pure): synthetic store + `stations.parquet` → correct per-station means,
  coordinate join, metric filter; empty range → empty frame.
- `MatplotlibRenderer.render_point_map`: Agg backend + `basemap=False` → writes a
  non-empty PNG to a tmp path (smoke test; no pixel/network assertions).
- `get_renderer`: returns the matplotlib renderer; unknown name raises.
- CLI `mean-map`: writes the expected PNG for a small fixture store; invalid FIPS /
  invalid metric / unknown renderer exit non-zero; no-data prints the message and writes
  nothing. Tests pass `--no-basemap` to stay offline; matplotlib uses the `Agg` backend.

## Out of Scope

- Other visualization subcommands (separate specs); the abstraction leaves room.
- folium / plotly renderers (registry leaves room to add them).
- Time-series/animation, multi-county composites, AQI-category discrete coloring.
