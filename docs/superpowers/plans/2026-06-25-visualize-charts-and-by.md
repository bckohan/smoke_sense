# Visualize: --by toggle + chart subcommands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--by value|aqi` toggle to all `visualize` subcommands and four non-map chart subcommands (`series`, `scatter`, `aggregate`, `histogram`) behind a new `ChartRenderer` registry.

**Architecture:** A shared `metric_observations` helper returns the long observation frame for a metric; `station_means` gains a `by` param; `resolve_by`/`y_label` centralize the value-vs-AQI choice. Charts go through a `ChartRenderer` ABC + registry (parallel to the existing `MapRenderer`), with a default `MatplotlibChartRenderer`. The Typer sub-app gains `--by` on `mean-map` and four new subcommands.

**Tech Stack:** Python 3.12, pandas, matplotlib (Agg), Typer, Rich, pytest.

**Spec:** `docs/superpowers/specs/2026-06-25-visualize-charts-and-by-design.md`

---

## File Structure

- `src/smoke_sense/visualize.py` — add `metric_observations`, `resolve_by`, `y_label`; extend `station_means`/`mean_map` with `by`; add `ChartRenderer` ABC + `register_chart_renderer`/`get_chart_renderer` + `MatplotlibChartRenderer`.
- `src/smoke_sense/bin/visualize.py` — add `--by` to `mean-map`; add `series`/`scatter`/`aggregate`/`histogram` subcommands.
- `tests/test_visualize.py` — unit tests for helpers + ChartRenderer.
- `tests/test_visualize_cli.py` — CLI tests for the new subcommands and `--by`.

---

### Task 0: Shared observation helpers + `by` on station_means/mean_map

**Goal:** Add `metric_observations`, `resolve_by`, `y_label`; thread a `by` parameter through `station_means` and `mean_map`.

**Files:**
- Modify: `src/smoke_sense/visualize.py`
- Test: `tests/test_visualize.py`

**Acceptance Criteria:**
- [ ] `metric_observations` returns columns `timestamp, station_id, value, aqi`; empty (those columns) when no data.
- [ ] `resolve_by(metric, "value")` → `"value"`; `resolve_by(aqi_metric, "aqi")` → `"aqi"`; `resolve_by(non_aqi_metric, "aqi")` raises `ValueError`; bad `by` raises `ValueError`.
- [ ] `y_label(metric, "value")` → `"{metric} ({unit})"`; `y_label(metric, "aqi")` → `"AQI"`.
- [ ] `station_means(..., by="aqi")` means the `aqi` column; default `by="value"` is unchanged.
- [ ] `mean_map(..., by=...)` forwards `by` and labels the colorbar accordingly.

**Verify:** `uv run pytest tests/test_visualize.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_visualize.py`:

```python
import pytest

from smoke_sense.data import AQI_METRICS


def test_metric_observations_columns(tmp_path):
    _seed(tmp_path)
    obs = visualize.metric_observations(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5)
    assert list(obs.columns) == ["timestamp", "station_id", "value", "aqi"]
    assert sorted(obs["value"].tolist()) == [5.0, 10.0, 20.0]
    assert set(obs["station_id"]) == {"s1", "s2"}


def test_metric_observations_empty(tmp_path):
    obs = visualize.metric_observations(
        tmp_path, "99999", date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5)
    assert obs.empty
    assert list(obs.columns) == ["timestamp", "station_id", "value", "aqi"]


def test_resolve_by_value_and_aqi():
    assert visualize.resolve_by(Metric.PM2_5, "value") == "value"
    assert visualize.resolve_by(Metric.PM2_5, "aqi") == "aqi"
    assert Metric.PM2_5 in AQI_METRICS


def test_resolve_by_rejects_aqi_for_non_aqi_metric():
    with pytest.raises(ValueError):
        visualize.resolve_by(Metric.TEMP, "aqi")


def test_resolve_by_rejects_unknown():
    with pytest.raises(ValueError):
        visualize.resolve_by(Metric.PM2_5, "nonsense")


def test_y_label():
    assert visualize.y_label(Metric.PM2_5, "value") == "PM2.5 (µg/m³)"
    assert visualize.y_label(Metric.PM2_5, "aqi") == "AQI"


def test_station_means_by_aqi(tmp_path):
    df = pd.DataFrame([
        {**_row("2026-06-16T01:00:00", Metric.PM2_5, 10.0, "s1", 34.0, -118.2),
         "aqi": 42},
        {**_row("2026-06-16T02:00:00", Metric.PM2_5, 20.0, "s1", 34.0, -118.2),
         "aqi": 60},
    ])
    store.write(tmp_path, "06037", df)
    out = visualize.station_means(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16),
        Metric.PM2_5, by="aqi")
    assert out[out["station_id"] == "s1"]["mean"].iloc[0] == 51.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_visualize.py -k "metric_observations or resolve_by or y_label or by_aqi" -v`
Expected: FAIL with `AttributeError: module 'smoke_sense.visualize' has no attribute 'metric_observations'` (and similar).

- [ ] **Step 3: Add the helpers and extend station_means/mean_map**

In `src/smoke_sense/visualize.py`, add the import for `AQI_METRICS`:

```python
from .data import AQI_METRICS, Metric
```

Add a module constant near `_MEAN_COLUMNS`:

```python
_OBS_COLUMNS = ["timestamp", "station_id", "value", "aqi"]
```

Add these functions (place `resolve_by`/`y_label`/`metric_observations` above `station_means`):

```python
def resolve_by(metric: Metric, by: str) -> str:
    """Map a --by choice to the data column; validate AQI eligibility."""
    if by == "value":
        return "value"
    if by == "aqi":
        if metric not in AQI_METRICS:
            raise ValueError(
                f"AQI not available for {metric.value}; "
                "AQI only for PM2.5/PM10/O3")
        return "aqi"
    raise ValueError(f"invalid by={by!r}; expected 'value' or 'aqi'")


def y_label(metric: Metric, by: str) -> str:
    """Axis/colorbar label for the chosen quantity."""
    if by == "aqi":
        return "AQI"
    return f"{metric.value} ({metric.unit})"


def metric_observations(data_dir, fips: str, start: date, end: date,
                        metric: Metric) -> pd.DataFrame:
    """Long observations for `metric` over [start, end].

    Returns columns timestamp, station_id, value, aqi. Empty (with those
    columns) if there is no matching data.
    """
    obs = store.read_range(data_dir, fips, start, end)
    obs = obs[obs["metric"] == metric.value]
    if obs.empty:
        return pd.DataFrame(columns=_OBS_COLUMNS)
    return obs[_OBS_COLUMNS].reset_index(drop=True)
```

Replace `station_means` with a `by`-aware version:

```python
def station_means(data_dir, fips: str, start: date, end: date,
                  metric: Metric, by: str = "value") -> pd.DataFrame:
    """Per-station mean of `metric`'s value (or AQI) over [start, end].

    Returns columns station_id, latitude, longitude, mean. Empty (with those
    columns) if there is no matching data or no station table.
    """
    column = resolve_by(metric, by)
    obs = store.read_range(data_dir, fips, start, end)
    obs = obs[obs["metric"] == metric.value]
    if obs.empty:
        return pd.DataFrame(columns=_MEAN_COLUMNS)
    means = (
        obs.groupby("station_id", observed=True)[column].mean()
        .rename("mean").reset_index()
    )
    path = store.stations_path(data_dir, fips)
    if not path.exists():
        return pd.DataFrame(columns=_MEAN_COLUMNS)
    stations = (
        pd.read_parquet(path)[["station_id", "latitude", "longitude"]]
        .drop_duplicates("station_id")
    )
    merged = means.merge(stations, on="station_id", how="inner")
    return merged[_MEAN_COLUMNS]
```

Update `mean_map` to accept and forward `by`:

```python
def mean_map(data_dir, fips: str, start: date, end: date, metric: Metric, *,
             by: str = "value", palette: str = "YlOrRd", output,
             renderer: str = "matplotlib", basemap: bool = True) -> Path | None:
    """Render a per-station mean map for `metric`; return the path or None if no data."""
    points = station_means(data_dir, fips, start, end, metric, by=by)
    if points.empty:
        return None
    label = f"mean {y_label(metric, by)}"
    title = f"{fips} {metric.value} ({by}) {start.isoformat()}..{end.isoformat()}"
    return get_renderer(renderer).render_point_map(
        points, value_label=label, palette=palette, title=title,
        output=output, basemap=basemap)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_visualize.py -v`
Expected: PASS (existing tests still green; new helper tests pass).

- [ ] **Step 5: Commit**

```bash
git add src/smoke_sense/visualize.py tests/test_visualize.py
git commit -m "feat(visualize): shared observation helpers + by on station_means/mean_map"
```

---

### Task 1: ChartRenderer registry + MatplotlibChartRenderer

**Goal:** Add a `ChartRenderer` ABC, a registry mirroring the map registry, and a default matplotlib implementation rendering series/scatter/aggregate/histogram PNGs.

**Files:**
- Modify: `src/smoke_sense/visualize.py`
- Test: `tests/test_visualize.py`

**Acceptance Criteria:**
- [ ] `get_chart_renderer("matplotlib")` returns the renderer; unknown name raises `KeyError`.
- [ ] Each of `render_series`, `render_scatter`, `render_aggregate`, `render_histogram` writes a non-empty PNG (Agg backend).
- [ ] `render_aggregate` works with `band=True` and `band=False`.
- [ ] `render_histogram` respects `bins`.

**Verify:** `uv run pytest tests/test_visualize.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_visualize.py`:

```python
def _obs_frame():
    return pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-06-16T01:00:00", "2026-06-16T02:00:00",
            "2026-06-16T01:00:00", "2026-06-16T02:00:00"], utc=True),
        "station_id": ["s1", "s1", "s2", "s2"],
        "value": [10.0, 20.0, 5.0, 15.0],
        "aqi": pd.array([42, 60, 21, 53], dtype="Int16"),
    })


def test_get_chart_renderer_unknown_raises():
    with pytest.raises(KeyError):
        visualize.get_chart_renderer("nope")


def test_chart_series_writes_png(tmp_path):
    out = tmp_path / "series.png"
    r = visualize.get_chart_renderer("matplotlib")
    result = r.render_series(
        _obs_frame(), y_column="value", y_label="PM2.5 (µg/m³)",
        title="t", palette="YlOrRd", output=out)
    assert result == out and out.exists() and out.stat().st_size > 0


def test_chart_scatter_writes_png(tmp_path):
    out = tmp_path / "scatter.png"
    r = visualize.get_chart_renderer("matplotlib")
    result = r.render_scatter(
        _obs_frame(), y_column="value", y_label="PM2.5 (µg/m³)",
        title="t", palette="YlOrRd", output=out)
    assert result == out and out.exists() and out.stat().st_size > 0


def test_chart_aggregate_with_and_without_band(tmp_path):
    r = visualize.get_chart_renderer("matplotlib")
    for band in (True, False):
        out = tmp_path / f"agg_{band}.png"
        result = r.render_aggregate(
            _obs_frame(), y_column="value", y_label="PM2.5 (µg/m³)",
            title="t", palette="YlOrRd", output=out, band=band)
        assert result == out and out.exists() and out.stat().st_size > 0


def test_chart_histogram_respects_bins(tmp_path):
    out = tmp_path / "hist.png"
    r = visualize.get_chart_renderer("matplotlib")
    result = r.render_histogram(
        _obs_frame(), y_column="value", y_label="PM2.5 (µg/m³)",
        title="t", palette="YlOrRd", output=out, bins=5)
    assert result == out and out.exists() and out.stat().st_size > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_visualize.py -k "chart" -v`
Expected: FAIL with `AttributeError: module 'smoke_sense.visualize' has no attribute 'get_chart_renderer'`.

- [ ] **Step 3: Implement the registry and renderer**

In `src/smoke_sense/visualize.py`, after the `MatplotlibRenderer` block, add:

```python
_CHART_RENDERERS: dict[str, type["ChartRenderer"]] = {}


class ChartRenderer(ABC):
    """Renders metric observations into a chart artifact."""

    name: str

    @abstractmethod
    def render_series(self, obs: pd.DataFrame, *, y_column: str, y_label: str,
                      title: str, palette: str, output) -> Path:
        """One line per station over time; return the written path."""
        raise NotImplementedError

    @abstractmethod
    def render_scatter(self, obs: pd.DataFrame, *, y_column: str, y_label: str,
                       title: str, palette: str, output) -> Path:
        """All observations as points colored by station."""
        raise NotImplementedError

    @abstractmethod
    def render_aggregate(self, obs: pd.DataFrame, *, y_column: str, y_label: str,
                         title: str, palette: str, output, band: bool = True) -> Path:
        """Mean across stations per timestamp, optional min/max band."""
        raise NotImplementedError

    @abstractmethod
    def render_histogram(self, obs: pd.DataFrame, *, y_column: str, y_label: str,
                         title: str, palette: str, output, bins: int = 30) -> Path:
        """Distribution of the chosen quantity over all observations."""
        raise NotImplementedError


def register_chart_renderer(cls: type[ChartRenderer]) -> type[ChartRenderer]:
    _CHART_RENDERERS[cls.name] = cls
    return cls


def get_chart_renderer(name: str) -> ChartRenderer:
    if name not in _CHART_RENDERERS:
        raise KeyError(
            f"unknown chart renderer: {name!r} (have {sorted(_CHART_RENDERERS)})")
    return _CHART_RENDERERS[name]()


@register_chart_renderer
class MatplotlibChartRenderer(ChartRenderer):
    name = "matplotlib"

    @staticmethod
    def _new_axes(title: str, y_label: str):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.set_title(title)
        ax.set_ylabel(y_label)
        return plt, fig, ax

    @staticmethod
    def _save(plt, fig, output) -> Path:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return output

    def render_series(self, obs, *, y_column, y_label, title, palette, output) -> Path:
        import matplotlib
        plt, fig, ax = self._new_axes(title, y_label)
        ax.set_xlabel("time")
        stations = sorted(obs["station_id"].unique())
        cmap = matplotlib.colormaps[palette]
        for i, sid in enumerate(stations):
            sub = obs[obs["station_id"] == sid].sort_values("timestamp")
            color = cmap(i / max(len(stations) - 1, 1))
            ax.plot(sub["timestamp"], sub[y_column].astype("float64"),
                    label=str(sid), color=color)
        ax.legend(title="station", fontsize="small")
        return self._save(plt, fig, output)

    def render_scatter(self, obs, *, y_column, y_label, title, palette, output) -> Path:
        plt, fig, ax = self._new_axes(title, y_label)
        ax.set_xlabel("time")
        codes = pd.Categorical(obs["station_id"]).codes
        sc = ax.scatter(obs["timestamp"], obs[y_column].astype("float64"),
                        c=codes, cmap=palette, s=12)
        fig.colorbar(sc, ax=ax, label="station")
        return self._save(plt, fig, output)

    def render_aggregate(self, obs, *, y_column, y_label, title, palette, output,
                         band=True) -> Path:
        plt, fig, ax = self._new_axes(title, y_label)
        ax.set_xlabel("time")
        vals = obs.assign(_v=obs[y_column].astype("float64")).groupby("timestamp")["_v"]
        mean = vals.mean()
        ax.plot(mean.index, mean.values, label="mean")
        if band:
            ax.fill_between(mean.index, vals.min().values, vals.max().values,
                            alpha=0.2, label="min-max")
        ax.legend(fontsize="small")
        return self._save(plt, fig, output)

    def render_histogram(self, obs, *, y_column, y_label, title, palette, output,
                         bins=30) -> Path:
        plt, fig, ax = self._new_axes(title, y_label)
        ax.set_xlabel(y_label)
        ax.set_ylabel("count")
        ax.hist(obs[y_column].astype("float64").dropna(), bins=bins)
        return self._save(plt, fig, output)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_visualize.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/smoke_sense/visualize.py tests/test_visualize.py
git commit -m "feat(visualize): ChartRenderer registry + matplotlib charts"
```

---

### Task 2: CLI — `--by` on mean-map + series/scatter/aggregate/histogram

**Goal:** Add `--by` to `mean-map` and four new chart subcommands that load observations and render via the chart registry.

**Files:**
- Modify: `src/smoke_sense/bin/visualize.py`
- Test: `tests/test_visualize_cli.py`

**Acceptance Criteria:**
- [ ] `series`, `scatter`, `aggregate`, `histogram` each write the expected PNG for a seeded multi-station store.
- [ ] `series --station s1` restricts to listed stations; absent IDs drop out; nothing left → no-data message.
- [ ] `--by aqi` works on PM2.5; `--by aqi` on temperature exits non-zero.
- [ ] invalid metric / unknown renderer exit non-zero; no-data prints the message and writes nothing.
- [ ] `mean-map --by aqi` colors by mean AQI.

**Verify:** `uv run pytest tests/test_visualize_cli.py -v` then `uv run pytest -q`

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create/extend `tests/test_visualize_cli.py`. If the file exists, append the new tests and reuse its existing seed helper; otherwise create it with this content:

```python
from datetime import date

import pandas as pd
from typer.testing import CliRunner

from smoke_sense import store
from smoke_sense.bin import app
from smoke_sense.data import Metric

runner = CliRunner()


def _row(ts, metric, value, station, lat, lon, aqi=pd.NA, source="purpleair", agg=10):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037", "station_id": station,
        "latitude": lat, "longitude": lon,
        "metric": metric.value, "value": value,
        "aqi": aqi, "agg_window": agg, "source": source,
    }


def _seed(tmp_path):
    df = pd.DataFrame([
        _row("2026-06-16T01:00:00", Metric.PM2_5, 10.0, "s1", 34.0, -118.2, aqi=42),
        _row("2026-06-16T02:00:00", Metric.PM2_5, 20.0, "s1", 34.0, -118.2, aqi=60),
        _row("2026-06-16T01:00:00", Metric.PM2_5, 5.0, "s2", 33.9, -118.1, aqi=21),
        _row("2026-06-16T01:00:00", Metric.TEMP, 25.0, "s1", 34.0, -118.2),
    ])
    store.write(tmp_path, "06037", df)


import pytest


@pytest.mark.parametrize("kind", ["series", "scatter", "aggregate", "histogram"])
def test_chart_subcommands_write_png(tmp_path, kind):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "visualize", kind, "06037", "--start", "2026-06-16",
        "--end", "2026-06-16", "--metric", "PM2.5",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    pngs = list((tmp_path / "06037").glob(f"*_{kind}.png"))
    assert pngs and pngs[0].stat().st_size > 0


def test_series_station_filter(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "visualize", "series", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--station", "s1",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output


def test_series_station_filter_no_match_messages(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "visualize", "series", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--station", "ghost",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "no data" in result.output


def test_by_aqi_on_pm25_ok(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "visualize", "aggregate", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--by", "aqi", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_aqi_*_aggregate.png"))


def test_by_aqi_on_temperature_fails(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "visualize", "histogram", "06037", "--start", "2026-06-16",
        "--metric", "temperature", "--by", "aqi",
        "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_invalid_metric_fails(tmp_path):
    result = runner.invoke(app, [
        "visualize", "scatter", "06037", "--start", "2026-06-16",
        "--metric", "BOGUS", "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_unknown_renderer_fails(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "visualize", "scatter", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--renderer", "nope",
        "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_no_data_message(tmp_path):
    result = runner.invoke(app, [
        "visualize", "series", "99999", "--start", "2026-06-16",
        "--metric", "PM2.5", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "no data" in result.output


def test_mean_map_by_aqi(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "visualize", "mean-map", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--by", "aqi", "--no-basemap",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_aqi_*_mean.png"))
```

Note: the `app` import is `from smoke_sense.bin import app` — confirm the root Typer app is exported there (it is, since other CLI tests use it). If the existing `test_visualize_cli.py` already defines `_seed`/`_row`/`runner`, reuse them and only append the new test functions.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_visualize_cli.py -v`
Expected: FAIL — the chart subcommands don't exist yet (`No such command 'series'`), and `mean-map` rejects `--by`.

- [ ] **Step 3: Add `--by` to mean-map and implement the chart subcommands**

In `src/smoke_sense/bin/visualize.py`, add a `--by` option to `mean_map` and pass it through. Change the signature to include (after `metric`):

```python
    by: str = typer.Option("value", "--by", help="Plot raw value or AQI [value|aqi]"),
```

Update the output default and the call inside `mean_map`:

```python
    out = output or (
        output_dir / county_fips
        / f"{chosen.value}_{by}_{start_date}_{end_date}_mean.png")

    try:
        result = viz.mean_map(
            output_dir, county_fips, start_date, end_date, chosen,
            by=by, palette=palette, output=out, renderer=renderer, basemap=basemap)
    except KeyError as exc:  # unknown renderer
        raise typer.BadParameter(str(exc)) from exc
    except ValueError as exc:  # invalid --by combo
        raise typer.BadParameter(str(exc)) from exc
```

Add a shared helper and the four subcommands at the end of the file:

```python
def _validate_fips(county_fips: str) -> None:
    if not (len(county_fips) == 5 and county_fips.isdigit()):
        raise typer.BadParameter(f"county FIPS must be 5-digit, got {county_fips!r}")


def _prepare(county_fips, metric, by, start, end):
    """Validate inputs and load observations; returns (Metric, y_column, obs, start_date, end_date)."""
    _validate_fips(county_fips)
    try:
        chosen = Metric(metric)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        y_column = viz.resolve_by(chosen, by)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return chosen, y_column


def _render_chart(kind, method_name, county_fips, start, end, metric, by, palette,
                  output, renderer, output_dir, *, stations=None, extra=None):
    chosen, y_column = _prepare(county_fips, metric, by, start, end)
    start_date = start.date()
    end_date = end.date() if end else date.today()
    obs = viz.metric_observations(output_dir, county_fips, start_date, end_date, chosen)
    if stations:
        obs = obs[obs["station_id"].isin(set(stations))]
    if obs.empty:
        console.print(
            f"[yellow]no data for {county_fips}/{chosen.value} in "
            f"{start_date}..{end_date}[/]")
        return
    out = output or (
        output_dir / county_fips
        / f"{chosen.value}_{by}_{start_date}_{end_date}_{kind}.png")
    try:
        engine = viz.get_chart_renderer(renderer)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    label = viz.y_label(chosen, by)
    title = f"{county_fips} {chosen.value} ({by}) {start_date}..{end_date}"
    method = getattr(engine, method_name)
    result = method(obs, y_column=y_column, y_label=label, title=title,
                    palette=palette, output=out, **(extra or {}))
    console.print(f"[green]Wrote[/] {result}")


@app.command("series")
def series(
    county_fips: str = typer.Argument(..., help="5-digit county FIPS code"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"),
    metric: str = typer.Option(..., "--metric", help="Metric to plot"),
    by: str = typer.Option("value", "--by", help="Plot raw value or AQI [value|aqi]"),
    station: Optional[list[str]] = typer.Option(None, "--station", help="Limit to these station IDs (repeatable)"),
    palette: str = typer.Option("YlOrRd", help="matplotlib colormap name"),
    output: Optional[Path] = typer.Option(None, help="Output PNG path"),
    renderer: str = typer.Option("matplotlib", help="Rendering engine"),
    output_dir: Path = typer.Option(Path("./data"), "--output-dir", help="Data directory"),
) -> None:
    """One line per station over time."""
    _render_chart("series", "render_series", county_fips, start, end, metric, by,
                  palette, output, renderer, output_dir, stations=station)


@app.command("scatter")
def scatter(
    county_fips: str = typer.Argument(..., help="5-digit county FIPS code"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"),
    metric: str = typer.Option(..., "--metric", help="Metric to plot"),
    by: str = typer.Option("value", "--by", help="Plot raw value or AQI [value|aqi]"),
    palette: str = typer.Option("YlOrRd", help="matplotlib colormap name"),
    output: Optional[Path] = typer.Option(None, help="Output PNG path"),
    renderer: str = typer.Option("matplotlib", help="Rendering engine"),
    output_dir: Path = typer.Option(Path("./data"), "--output-dir", help="Data directory"),
) -> None:
    """All observations as points colored by station."""
    _render_chart("scatter", "render_scatter", county_fips, start, end, metric, by,
                  palette, output, renderer, output_dir)


@app.command("aggregate")
def aggregate(
    county_fips: str = typer.Argument(..., help="5-digit county FIPS code"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"),
    metric: str = typer.Option(..., "--metric", help="Metric to plot"),
    by: str = typer.Option("value", "--by", help="Plot raw value or AQI [value|aqi]"),
    band: bool = typer.Option(True, "--band/--no-band", help="Shade min/max band"),
    palette: str = typer.Option("YlOrRd", help="matplotlib colormap name"),
    output: Optional[Path] = typer.Option(None, help="Output PNG path"),
    renderer: str = typer.Option("matplotlib", help="Rendering engine"),
    output_dir: Path = typer.Option(Path("./data"), "--output-dir", help="Data directory"),
) -> None:
    """Mean across stations per timestamp, optional min/max band."""
    _render_chart("aggregate", "render_aggregate", county_fips, start, end, metric, by,
                  palette, output, renderer, output_dir, extra={"band": band})


@app.command("histogram")
def histogram(
    county_fips: str = typer.Argument(..., help="5-digit county FIPS code"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"),
    metric: str = typer.Option(..., "--metric", help="Metric to plot"),
    by: str = typer.Option("value", "--by", help="Plot raw value or AQI [value|aqi]"),
    bins: int = typer.Option(30, "--bins", help="Histogram bin count"),
    palette: str = typer.Option("YlOrRd", help="matplotlib colormap name"),
    output: Optional[Path] = typer.Option(None, help="Output PNG path"),
    renderer: str = typer.Option("matplotlib", help="Rendering engine"),
    output_dir: Path = typer.Option(Path("./data"), "--output-dir", help="Data directory"),
) -> None:
    """Distribution of the chosen quantity over all observations."""
    _render_chart("histogram", "render_histogram", county_fips, start, end, metric, by,
                  palette, output, renderer, output_dir, extra={"bins": bins})
```

The `_prepare` helper's `start`/`end` params are unused inside it after refactor — keep its signature minimal by removing them: it should be `def _prepare(county_fips, metric, by):` and the call sites pass `(county_fips, metric, by)`. (Adjust `_render_chart` accordingly: `chosen, y_column = _prepare(county_fips, metric, by)`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_visualize_cli.py -v`
Expected: PASS. Then run the full suite:

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/smoke_sense/bin/visualize.py tests/test_visualize_cli.py
git commit -m "feat(visualize): --by on mean-map + series/scatter/aggregate/histogram subcommands"
```

---

## Self-Review

- **Spec coverage:** `--by value|aqi` (Tasks 0+2) ✓; `metric_observations` (Task 0) ✓; `station_means(by=)` (Task 0) ✓; `resolve_by`/`y_label` (Task 0) ✓; ChartRenderer registry + matplotlib impl with all four methods (Task 1) ✓; four subcommands with `--station`/`--band`/`--bins` (Task 2) ✓; default output path `{fips}/{metric}_{by}_{start}_{end}_{kind}.png` (Tasks 0 mean-map + 2) ✓; error handling for invalid FIPS/metric/renderer/`--by` combo + no-data message (Task 2) ✓; tests for each (all tasks) ✓; no new deps ✓.
- **Placeholder scan:** none — every step has full code.
- **Type consistency:** `resolve_by`/`y_label`/`metric_observations`/`get_chart_renderer`/`render_*` names match across tasks; chart methods share the `(obs, *, y_column, y_label, title, palette, output, ...)` signature used by `_render_chart`.

## Out of Scope

- Interactive/HTML chart renderers (registry leaves room).
- Multi-metric overlays; styling beyond palette/bins/band.
- Changes to map rendering beyond the `--by` colorbar label.
