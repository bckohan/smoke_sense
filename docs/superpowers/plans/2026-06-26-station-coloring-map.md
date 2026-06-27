# Per-Station Coloring + Station Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `series`/`scatter` plot in a single color with no legend by default, and only color-by-station + show a legend + render a station map above the chart when an explicit `--station` list is given.

**Architecture:** A new pure color helper and a station-coordinate lookup live in `visualize.py`; the `series`/`scatter` chart renderers gain `color_by_station` and `station_points` parameters that switch between single-color and per-station rendering and optionally stack a color-matched station map above the chart. The CLI wires `--station` (new on `scatter`) into those parameters.

**Tech Stack:** Python 3.12, pandas, matplotlib (Agg), contextily, Typer, pytest.

Spec: `docs/superpowers/specs/2026-06-26-station-coloring-and-map-design.md`

---

### Task 1: `visualize.py` — color helper, coordinate lookup, renderer changes

**Goal:** Add `_assign_colors` + `station_coordinates`, and rework `render_series`/`render_scatter` to gate coloring and optionally render a station map.

**Files:**
- Modify: `src/smoke_sense/visualize.py`
- Test: `tests/test_visualize.py`

**Acceptance Criteria:**
- [ ] `_assign_colors(ids, palette)` returns a deterministic, order-independent `{id: color}` with distinct colors.
- [ ] `station_coordinates(dir, fips, ids)` returns the requested subset (`station_id, latitude, longitude`); empty frame when no parquet.
- [ ] `render_series`/`render_scatter` with `color_by_station=False` → single axis, no legend.
- [ ] With `color_by_station=True` and non-empty `station_points` → two axes (map above chart), legend on the chart axis.
- [ ] `render_aggregate`/`render_histogram` are unchanged.

**Verify:** `uv run pytest tests/test_visualize.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_visualize.py`:

```python
from pathlib import Path


def test_assign_colors_deterministic():
    c1 = visualize._assign_colors(["s2", "s1"], "viridis")
    c2 = visualize._assign_colors(["s1", "s2"], "viridis")
    assert set(c1) == {"s1", "s2"}
    assert c1 == c2                 # order-independent + deterministic
    assert c1["s1"] != c1["s2"]     # distinct colors


def test_station_coordinates_subset(tmp_path):
    _seed(tmp_path)
    out = visualize.station_coordinates(tmp_path, "06037", ["s1"])
    assert out["station_id"].tolist() == ["s1"]
    assert set(out.columns) == {"station_id", "latitude", "longitude"}


def test_station_coordinates_empty_when_no_parquet(tmp_path):
    out = visualize.station_coordinates(tmp_path, "99999", ["s1"])
    assert out.empty
    assert list(out.columns) == ["station_id", "latitude", "longitude"]


def _obs_df():
    return pd.DataFrame({
        "timestamp": pd.to_datetime(
            ["2026-06-16T01:00:00", "2026-06-16T02:00:00", "2026-06-16T01:00:00"],
            utc=True),
        "station_id": ["s1", "s1", "s2"],
        "value": [10.0, 20.0, 5.0],
        "aqi": pd.array([pd.NA, pd.NA, pd.NA], dtype="Int16"),
    })


def _capture_fig(monkeypatch):
    captured = {}

    def fake_save(plt, fig, output):
        captured["fig"] = fig
        return Path(output)

    monkeypatch.setattr(visualize.MatplotlibChartRenderer, "_save",
                        staticmethod(fake_save))
    return captured


def _points():
    return pd.DataFrame({
        "station_id": ["s1", "s2"],
        "latitude": [34.0, 33.9],
        "longitude": [-118.2, -118.1],
    })


def test_render_series_no_station_single_axis_no_legend(tmp_path, monkeypatch):
    cap = _capture_fig(monkeypatch)
    visualize.MatplotlibChartRenderer().render_series(
        _obs_df(), y_column="value", y_label="v", title="t",
        palette="viridis", output=tmp_path / "x.png")
    fig = cap["fig"]
    assert len(fig.axes) == 1
    assert fig.axes[0].get_legend() is None


def test_render_series_with_station_map_two_axes_legend(tmp_path, monkeypatch):
    cap = _capture_fig(monkeypatch)
    visualize.MatplotlibChartRenderer().render_series(
        _obs_df(), y_column="value", y_label="v", title="t",
        palette="viridis", output=tmp_path / "x.png",
        color_by_station=True, station_points=_points())
    fig = cap["fig"]
    assert len(fig.axes) == 2
    assert fig.axes[1].get_legend() is not None     # chart axis (below the map)


def test_render_scatter_no_station_single_axis_no_legend(tmp_path, monkeypatch):
    cap = _capture_fig(monkeypatch)
    visualize.MatplotlibChartRenderer().render_scatter(
        _obs_df(), y_column="value", y_label="v", title="t",
        palette="viridis", output=tmp_path / "x.png")
    fig = cap["fig"]
    assert len(fig.axes) == 1
    assert fig.axes[0].get_legend() is None


def test_render_scatter_with_station_map_two_axes_legend(tmp_path, monkeypatch):
    cap = _capture_fig(monkeypatch)
    visualize.MatplotlibChartRenderer().render_scatter(
        _obs_df(), y_column="value", y_label="v", title="t",
        palette="viridis", output=tmp_path / "x.png",
        color_by_station=True, station_points=_points())
    fig = cap["fig"]
    assert len(fig.axes) == 2
    assert fig.axes[1].get_legend() is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_visualize.py -k "assign_colors or station_coordinates or render_series or render_scatter" -v`
Expected: FAIL — `AttributeError: ... has no attribute '_assign_colors'` / `station_coordinates`, and `render_series()` rejecting `color_by_station`.

- [ ] **Step 3: Add the color helper, the coordinate lookup, and module constants**

In `src/smoke_sense/visualize.py`, add a module constant near the existing column constants (after `_OBS_COLUMNS`):

```python
_STATION_COLUMNS = ["station_id", "latitude", "longitude"]
_SINGLE_COLOR = "tab:blue"
```

Add these two functions (place `_assign_colors` just above the `_RENDERERS` registry block, and `station_coordinates` right after `station_means`):

```python
def _assign_colors(station_ids, palette: str) -> dict:
    """Deterministic per-station color map from `palette` over sorted IDs."""
    import matplotlib

    stations = sorted(set(station_ids))
    cmap = matplotlib.colormaps[palette]
    n = len(stations)
    return {sid: cmap(i / max(n - 1, 1)) for i, sid in enumerate(stations)}


def station_coordinates(data_dir, fips: str, station_ids) -> pd.DataFrame:
    """Coordinates for the requested stations from the station table.

    Returns columns station_id, latitude, longitude (empty if there is no
    station table or none of `station_ids` have coordinates).
    """
    path = store.stations_path(data_dir, fips)
    if not path.exists():
        return pd.DataFrame(columns=_STATION_COLUMNS)
    stations = (
        pd.read_parquet(path)[_STATION_COLUMNS].drop_duplicates("station_id")
    )
    wanted = set(station_ids)
    return stations[stations["station_id"].isin(wanted)].reset_index(drop=True)
```

- [ ] **Step 4: Update the `ChartRenderer` abstract signatures**

In `src/smoke_sense/visualize.py`, change the abstract `render_series` and `render_scatter` declarations to include the new keyword params:

```python
    @abstractmethod
    def render_series(self, obs: pd.DataFrame, *, y_column: str, y_label: str,
                      title: str, palette: str, output,
                      color_by_station: bool = False,
                      station_points: pd.DataFrame | None = None) -> Path:
        """One line per station over time; return the written path."""
        raise NotImplementedError

    @abstractmethod
    def render_scatter(self, obs: pd.DataFrame, *, y_column: str, y_label: str,
                       title: str, palette: str, output,
                       color_by_station: bool = False,
                       station_points: pd.DataFrame | None = None) -> Path:
        """All observations as points colored by station."""
        raise NotImplementedError
```

(Leave the abstract `render_aggregate` and `render_histogram` unchanged.)

- [ ] **Step 5: Rework the Matplotlib figure helpers**

In `MatplotlibChartRenderer`, replace the existing `_new_axes` static method with the following three helpers (`_new_axes` now delegates to `_open`, so `render_aggregate`/`render_histogram` keep working unchanged):

```python
    @staticmethod
    def _open(title: str, y_label: str, station_points, colors):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if station_points is not None and not station_points.empty:
            fig, (ax_map, ax_chart) = plt.subplots(
                2, 1, figsize=(10, 9), gridspec_kw={"height_ratios": [1, 1.6]})
            MatplotlibChartRenderer._draw_station_map(ax_map, station_points, colors)
        else:
            fig, ax_chart = plt.subplots(figsize=(10, 5))
        ax_chart.set_title(title)
        ax_chart.set_ylabel(y_label)
        return plt, fig, ax_chart

    @staticmethod
    def _new_axes(title: str, y_label: str):
        return MatplotlibChartRenderer._open(title, y_label, None, None)

    @staticmethod
    def _draw_station_map(ax, station_points, colors) -> None:
        for _, r in station_points.iterrows():
            sid = r["station_id"]
            color = colors[sid] if colors and sid in colors else _SINGLE_COLOR
            ax.scatter(r["longitude"], r["latitude"], color=color, s=60,
                       edgecolor="black", linewidth=0.3)
            ax.annotate(str(sid), (r["longitude"], r["latitude"]),
                        fontsize="x-small", xytext=(3, 3),
                        textcoords="offset points")
        ax.set_title("stations")
        ax.set_xlabel("longitude")
        ax.set_ylabel("latitude")
        try:
            import contextily as cx
            cx.add_basemap(ax, crs="EPSG:4326",
                           source=cx.providers.OpenStreetMap.Mapnik)
        except Exception as exc:  # offline / tile error -> render without tiles
            logger.warning("basemap unavailable (%s); rendering without tiles", exc)
```

- [ ] **Step 6: Rewrite `render_series` and `render_scatter`**

Replace the existing `render_series` and `render_scatter` methods with:

```python
    def render_series(self, obs, *, y_column, y_label, title, palette, output,
                      color_by_station=False, station_points=None) -> Path:
        colors = (_assign_colors(obs["station_id"].unique(), palette)
                  if color_by_station else None)
        plt, fig, ax = self._open(title, y_label, station_points, colors)
        ax.set_xlabel("time")
        for sid in sorted(obs["station_id"].unique()):
            sub = obs[obs["station_id"] == sid].sort_values("timestamp")
            if colors:
                ax.plot(sub["timestamp"], sub[y_column].astype("float64"),
                        label=str(sid), color=colors[sid])
            else:
                ax.plot(sub["timestamp"], sub[y_column].astype("float64"),
                        color=_SINGLE_COLOR)
        if colors:
            ax.legend(title="station", fontsize="small")
        return self._save(plt, fig, output)

    def render_scatter(self, obs, *, y_column, y_label, title, palette, output,
                       color_by_station=False, station_points=None) -> Path:
        colors = (_assign_colors(obs["station_id"].unique(), palette)
                  if color_by_station else None)
        plt, fig, ax = self._open(title, y_label, station_points, colors)
        ax.set_xlabel("time")
        if colors:
            for sid in sorted(obs["station_id"].unique()):
                sub = obs[obs["station_id"] == sid]
                ax.scatter(sub["timestamp"], sub[y_column].astype("float64"),
                           color=colors[sid], s=12, label=str(sid))
            ax.legend(title="station", fontsize="small")
        else:
            ax.scatter(obs["timestamp"], obs[y_column].astype("float64"),
                       color=_SINGLE_COLOR, s=12)
        return self._save(plt, fig, output)
```

(Remove the old `import matplotlib` / `cmap = matplotlib.colormaps[palette]` / colorbar code that these methods previously contained — color assignment now lives in `_assign_colors`.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_visualize.py -v`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 8: Commit**

```bash
git add src/smoke_sense/visualize.py tests/test_visualize.py
git commit -m "feat(visualize): station-gated coloring + stacked station map"
```

---

### Task 2: CLI wiring in `bin/visualize.py`

**Goal:** Add `--station` to `scatter`, and pass `color_by_station`/`station_points` from `series` and `scatter`.

**Files:**
- Modify: `src/smoke_sense/bin/visualize.py`
- Test: `tests/test_visualize_cli.py`

**Acceptance Criteria:**
- [ ] `scatter` accepts `--station` (repeatable) and filters to those stations.
- [ ] `series`/`scatter` without `--station` write a PNG (exit 0).
- [ ] `series`/`scatter` with `--station` write a PNG (exit 0).

**Verify:** `uv run pytest tests/test_visualize_cli.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_visualize_cli.py`:

```python
def test_series_no_station_writes_png(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "series", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_series.png"))


def test_series_with_station_map_writes_png(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "series", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--station", "s1", "--station", "s2",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_series.png"))


def test_scatter_no_station_writes_png(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "scatter", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_scatter.png"))


def test_scatter_with_station_filter_writes_png(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "scatter", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--station", "s1", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_scatter.png"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_visualize_cli.py -k "no_station or station_map or scatter_with_station or scatter_no_station" -v`
Expected: FAIL — `scatter` rejects the unknown option `--station` (the scatter tests error; the series ones may already pass).

- [ ] **Step 3: Add `--station` to `scatter` and wire `series` + `scatter`**

In `src/smoke_sense/bin/visualize.py`, add a `station` option to the `scatter` command signature, immediately after its `by` option:

```python
    by: str = typer.Option("value", "--by", help="Plot raw value or AQI [value|aqi]"),
    station: Optional[list[str]] = typer.Option(None, "--station", help="Limit to these station IDs (repeatable)"),
```

Then change the `series` command body so its `_render_chart` call computes and passes the station map data:

```python
    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter_on, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound, exclude=exclude_station)
    station_points = (
        viz.station_coordinates(output_dir, county_fips, station)
        if station else None)
    _render_chart("series", "render_series", county_fips, start, end, metric, by,
                  palette, output, renderer, output_dir, stations=station,
                  extra={"color_by_station": bool(station),
                         "station_points": station_points},
                  outlier_filter=ofilter)
```

And change the `scatter` command body the same way:

```python
    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter_on, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound, exclude=exclude_station)
    station_points = (
        viz.station_coordinates(output_dir, county_fips, station)
        if station else None)
    _render_chart("scatter", "render_scatter", county_fips, start, end, metric, by,
                  palette, output, renderer, output_dir, stations=station,
                  extra={"color_by_station": bool(station),
                         "station_points": station_points},
                  outlier_filter=ofilter)
```

(`_render_chart` already filters `obs` by `stations` and forwards `extra` to the renderer; no change needed there. `aggregate`/`histogram` are left as-is.)

- [ ] **Step 4: Run the targeted tests**

Run: `uv run pytest tests/test_visualize_cli.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add src/smoke_sense/bin/visualize.py tests/test_visualize_cli.py
git commit -m "feat(visualize): --station on scatter; wire station coloring + map"
```

---

## Self-Review

**Spec coverage:**
- No `--station` → single color, no legend → Task 1 `render_series`/`render_scatter` `else` branches + `test_render_*_no_station_*`. ✓
- `--station` → per-station colors + legend + stacked map → Task 1 `_open`/`_draw_station_map` + `test_render_*_with_station_map_two_axes_legend`; CLI in Task 2. ✓
- `_assign_colors` shared by chart and map → Task 1 Step 3/6; `test_assign_colors_deterministic`. ✓
- `station_coordinates` subset/empty → Task 1 Step 3 + tests. ✓
- `scatter` gains `--station` → Task 2 Step 3 + `test_scatter_*`. ✓
- `aggregate`/`histogram` unchanged → only `series`/`scatter` bodies and the two renderer methods touched; `_new_axes` preserved via delegation. ✓
- Graceful basemap fallback → `_draw_station_map` `try/except`. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows full code. ✓

**Type consistency:** `render_series`/`render_scatter` gain `color_by_station: bool` + `station_points: pd.DataFrame | None` in both the abstract base (Task 1 Step 4) and the impl (Step 6); the CLI passes exactly those keys via `extra` (Task 2 Step 3). `station_coordinates` returns `_STATION_COLUMNS`, consumed by `_draw_station_map` (uses `station_id`/`latitude`/`longitude`). `_assign_colors` returns a dict keyed by `station_id`, indexed by the same sorted ids used in the render loops. Consistent. ✓
