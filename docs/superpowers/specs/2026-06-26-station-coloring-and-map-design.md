# Per-Station Coloring + Station Map Design

**Date:** 2026-06-26
**Status:** Approved

## Goal

Make the `series` and `scatter` visualizations stop color-coding by station by
default (the station set can be enormous, producing an unreadable legend). Only
when the user passes an explicit `--station` list do we color by station, show a
legend, and additionally render a station location map stacked above the chart in
the same image.

## Decisions

- **Scope:** `series` and `scatter` only. `aggregate` and `histogram` are
  unchanged (they do not color per station). `mean-map` is unchanged.
- **No `--station`:** plot all data in a single uniform color, no legend, no
  colorbar. (`scatter` loses its current per-station colorbar in this case.)
- **With `--station`:** color each station distinctly (palette colormap), show a
  legend, and render a station location map **stacked above the chart in one
  PNG**, with map dots color-matched to the chart's per-station colors.
- `scatter` gains a repeatable `--station` filter, identical to `series`.
- Reuse the existing graceful basemap fallback; no new basemap flag (YAGNI).

## Components

### `src/smoke_sense/visualize.py`

**`station_coordinates(data_dir, fips, station_ids) -> pd.DataFrame`**
- Reads the stations parquet via `store.stations_path` (like `station_means`),
  selects `["station_id", "latitude", "longitude"]`, de-dupes by `station_id`,
  filters to `station_ids`. Returns an empty frame with those columns if the
  parquet is absent or no requested station has coordinates.

**`_assign_colors(station_ids, palette) -> dict[str, tuple]`**
- Sorts the unique `station_ids`, maps each to `colormap[palette](i / max(n-1, 1))`.
  Pure and deterministic; used for both the chart series and the map dots so the
  colors line up.

**`ChartRenderer.render_series` / `render_scatter`** (abstract + Matplotlib impl)
gain two keyword params:
- `color_by_station: bool = False`
  - `False` → draw all lines/points in a single uniform color; no legend, no
    colorbar.
  - `True` → per-station colors (from `_assign_colors`) + a legend.
- `station_points: pd.DataFrame | None = None`
  - Non-empty (`station_id, latitude, longitude`) → build a 2-row figure: a
    station map on top (dots color-matched via `_assign_colors`, each labeled
    with its `station_id`, basemap attempted with the existing graceful offline
    fallback) and the chart below.
  - `None`/empty → single-axis chart (today's layout).

`render_aggregate` and `render_histogram` signatures are unchanged.

The Matplotlib implementation factors figure creation into a shared helper that
returns the chart axis (and, when `station_points` is provided, first draws the
map axis above it). `_save` continues to close the figure.

### `src/smoke_sense/bin/visualize.py`

- Add `--station` (repeatable `Optional[list[str]]`) to the `scatter` command,
  matching `series`.
- In `series` and `scatter`: when a station list is given, compute
  `station_points = viz.station_coordinates(output_dir, county_fips, station)`
  and pass `color_by_station=bool(station)` and `station_points` to the renderer
  via `_render_chart`'s `extra` dict. The existing obs-filtering by `--station`
  in `_render_chart` is retained.
- `aggregate`/`histogram` calls are unchanged.

## Data flow

```
visualize series 06037 --metric PM2.5 --station s1 --station s2
  -> ofilter = make_filter(...)
  -> station_points = station_coordinates(dir, fips, ["s1","s2"])
  -> _render_chart("series", "render_series", ..., stations=["s1","s2"],
                   extra={"color_by_station": True, "station_points": station_points},
                   outlier_filter=ofilter)
       obs filtered to s1,s2
  -> render_series(obs, ..., color_by_station=True, station_points=station_points)
       colors = _assign_colors(["s1","s2"], palette)
       [map axis: dots at coords colored by `colors`, labeled]
       [chart axis: one line per station colored by `colors`, legend]
  -> one PNG
```

Without `--station`, `_render_chart` passes `color_by_station=False`,
`station_points=None`; a single-axis, single-color chart is produced.

## Error handling

- `--station` given but no stations parquet / no coordinates → `station_points`
  empty → color by station + legend, but no map subplot (graceful).
- `--station` given but those stations have no observations → existing "no data"
  message path in `_render_chart` (returns before rendering).
- Basemap unavailable (offline / tile error) → existing `try/except` logs a
  warning and renders the map without tiles.

## Testing

`tests/test_visualize.py` (pure + renderer):
- `_assign_colors`: deterministic, one color per sorted station, stable.
- `station_coordinates`: returns the requested subset; empty frame when the
  parquet is absent.
- `render_series` / `render_scatter` via a monkeypatched `_save` that captures
  the `Figure`:
  - `station_points` non-empty → `len(fig.axes) == 2`; `None` → `1`.
  - legend present on the chart axis only when `color_by_station=True`.

`tests/test_visualize_cli.py` (CLI):
- `series`/`scatter` without `--station` → exit 0, PNG written.
- `series`/`scatter` with `--station` → exit 0, PNG written.
- `scatter --station <one>` filters to that station (no error, file written).
