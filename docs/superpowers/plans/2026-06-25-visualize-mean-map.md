# Visualize mean-map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `smoke-sense visualize mean-map` — a per-county map with each sensor as a dot colored by the mean value of a chosen metric over a time period, behind a pluggable renderer abstraction with a configurable palette.

**Architecture:** A renderer-agnostic `visualize.py` (pure `station_means` aggregation + a `MapRenderer` registry with a default matplotlib(+contextily)→PNG impl). `bin/visualize.py` becomes a Typer sub-app whose `mean-map` subcommand calls `visualize.mean_map`.

**Tech Stack:** Python 3.12, Typer, Rich, pandas, matplotlib, contextily, pytest.

**Reference spec:** `docs/superpowers/specs/2026-06-25-visualize-mean-map-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/smoke_sense/visualize.py` | `station_means` aggregation; `MapRenderer` registry + `MatplotlibRenderer`; `mean_map` orchestration |
| `src/smoke_sense/bin/visualize.py` | Typer sub-app; `mean-map` subcommand |
| `src/smoke_sense/bin/__init__.py` | register the visualize sub-app |

---

### Task 0: Dependencies + `station_means` aggregation

**Goal:** Add matplotlib/contextily and a pure per-station mean helper joined to coordinates.

**Files:**
- Modify: `pyproject.toml` (add `matplotlib`, `contextily`)
- Create: `src/smoke_sense/visualize.py`
- Create: `tests/test_visualize.py`

**Acceptance Criteria:**
- [ ] `station_means` returns `station_id, latitude, longitude, mean` per station for the metric
- [ ] filters to the requested metric; joins `stations.parquet`; empty range → empty frame

**Verify:** `uv run pytest tests/test_visualize.py -v` → all pass

**Steps:**

- [ ] **Step 1: Add deps.** Run `uv add matplotlib contextily`. Confirm import: `uv run python -c "import matplotlib, contextily; print('ok')"`.

- [ ] **Step 2: Create `tests/test_visualize.py`**

```python
from datetime import date

import pandas as pd

from smoke_sense import store, visualize
from smoke_sense.data import Metric


def _row(ts, metric, value, station, lat, lon, source="purpleair", agg=10):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037", "station_id": station,
        "latitude": lat, "longitude": lon,
        "metric": metric.value, "value": value,
        "aqi": pd.NA, "agg_window": agg, "source": source,
    }


def _seed(tmp_path):
    df = pd.DataFrame([
        _row("2026-06-16T01:00:00", Metric.PM2_5, 10.0, "s1", 34.0, -118.2),
        _row("2026-06-16T02:00:00", Metric.PM2_5, 20.0, "s1", 34.0, -118.2),
        _row("2026-06-16T01:00:00", Metric.PM2_5, 5.0, "s2", 33.9, -118.1),
        _row("2026-06-16T01:00:00", Metric.TEMP, 25.0, "s1", 34.0, -118.2),
    ])
    store.write(tmp_path, "06037", df)


def test_station_means_per_station(tmp_path):
    _seed(tmp_path)
    out = visualize.station_means(tmp_path, "06037",
                                  date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5)
    by = dict(zip(out["station_id"], out["mean"]))
    assert by["s1"] == 15.0   # mean of 10 and 20
    assert by["s2"] == 5.0
    assert set(out.columns) == {"station_id", "latitude", "longitude", "mean"}
    s1 = out[out["station_id"] == "s1"].iloc[0]
    assert (s1["latitude"], s1["longitude"]) == (34.0, -118.2)


def test_station_means_filters_metric(tmp_path):
    _seed(tmp_path)
    out = visualize.station_means(tmp_path, "06037",
                                  date(2026, 6, 16), date(2026, 6, 16), Metric.TEMP)
    assert out["station_id"].tolist() == ["s1"]
    assert out["mean"].iloc[0] == 25.0


def test_station_means_empty_when_no_data(tmp_path):
    out = visualize.station_means(tmp_path, "99999",
                                  date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5)
    assert out.empty
    assert list(out.columns) == ["station_id", "latitude", "longitude", "mean"]
```

- [ ] **Step 3: Run, confirm FAIL.**

- [ ] **Step 4: Create `src/smoke_sense/visualize.py`**

```python
"""Visualization helpers and the pluggable map-renderer registry."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from . import store
from .data import Metric

_MEAN_COLUMNS = ["station_id", "latitude", "longitude", "mean"]


def station_means(data_dir, fips: str, start: date, end: date,
                  metric: Metric) -> pd.DataFrame:
    """Per-station mean of `metric`'s value over [start, end], with coordinates.

    Returns columns station_id, latitude, longitude, mean. Empty (with those
    columns) if there is no matching data or no station table.
    """
    obs = store.read_range(data_dir, fips, start, end)
    obs = obs[obs["metric"] == metric.value]
    if obs.empty:
        return pd.DataFrame(columns=_MEAN_COLUMNS)
    means = (
        obs.groupby("station_id", observed=True)["value"].mean()
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

- [ ] **Step 5: Run, confirm PASS.** `uv run pytest tests/test_visualize.py -v` then `uv run pytest -q`.

- [ ] **Step 6: Stage.** `git add pyproject.toml uv.lock src/smoke_sense/visualize.py tests/test_visualize.py`

---

### Task 1: Renderer abstraction + MatplotlibRenderer + `mean_map`

**Goal:** A `MapRenderer` registry with a default matplotlib(+contextily) PNG renderer, and a `mean_map` orchestrator.

**Files:**
- Modify: `src/smoke_sense/visualize.py`
- Modify: `tests/test_visualize.py`

**Acceptance Criteria:**
- [ ] `get_renderer("matplotlib")` returns the renderer; unknown name raises `KeyError`
- [ ] `MatplotlibRenderer.render_point_map(..., basemap=False)` writes a non-empty PNG
- [ ] `mean_map` returns the output path, or `None` when there's no data

**Verify:** `uv run pytest tests/test_visualize.py -v` → all pass

**Steps:**

- [ ] **Step 1: Append tests to `tests/test_visualize.py`**

```python
from pathlib import Path


def test_get_renderer_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        visualize.get_renderer("nope")


def test_matplotlib_renderer_writes_png(tmp_path):
    points = pd.DataFrame({
        "station_id": ["s1", "s2"],
        "latitude": [34.0, 33.9],
        "longitude": [-118.2, -118.1],
        "mean": [15.0, 5.0],
    })
    out = tmp_path / "map.png"
    renderer = visualize.get_renderer("matplotlib")
    result = renderer.render_point_map(
        points, value_label="mean PM2.5 (µg/m³)", palette="YlOrRd",
        title="06037 PM2.5", output=out, basemap=False)
    assert result == out
    assert out.exists() and out.stat().st_size > 0


def test_mean_map_returns_path_and_none(tmp_path):
    _seed(tmp_path)
    out = tmp_path / "mean.png"
    result = visualize.mean_map(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5,
        palette="YlOrRd", output=out, renderer="matplotlib", basemap=False)
    assert result == out and out.exists()
    none_result = visualize.mean_map(
        tmp_path, "99999", date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5,
        palette="YlOrRd", output=tmp_path / "x.png", renderer="matplotlib",
        basemap=False)
    assert none_result is None
```

- [ ] **Step 2: Run, confirm FAIL.**

- [ ] **Step 3: Append to `src/smoke_sense/visualize.py`**

Add imports at the top (with the existing ones):
```python
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)
```
Append:
```python
_RENDERERS: dict[str, type["MapRenderer"]] = {}


class MapRenderer(ABC):
    """Renders a set of geographic points into a map artifact."""

    name: str

    @abstractmethod
    def render_point_map(self, points: pd.DataFrame, *, value_label: str,
                         palette: str, title: str, output, basemap: bool = True) -> Path:
        """Render `points` (latitude, longitude, mean) to `output`; return the path."""
        raise NotImplementedError


def register_renderer(cls: type[MapRenderer]) -> type[MapRenderer]:
    _RENDERERS[cls.name] = cls
    return cls


def get_renderer(name: str) -> MapRenderer:
    if name not in _RENDERERS:
        raise KeyError(f"unknown renderer: {name!r} (have {sorted(_RENDERERS)})")
    return _RENDERERS[name]()


@register_renderer
class MatplotlibRenderer(MapRenderer):
    name = "matplotlib"

    def render_point_map(self, points, *, value_label, palette, title, output,
                         basemap=True) -> Path:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        fig, ax = plt.subplots(figsize=(8, 8))
        scatter = ax.scatter(
            points["longitude"], points["latitude"], c=points["mean"],
            cmap=palette, s=40, edgecolor="black", linewidth=0.3)
        fig.colorbar(scatter, ax=ax, label=value_label)
        ax.set_title(title)
        ax.set_xlabel("longitude")
        ax.set_ylabel("latitude")
        if basemap:
            try:
                import contextily as cx
                cx.add_basemap(ax, crs="EPSG:4326",
                               source=cx.providers.OpenStreetMap.Mapnik)
            except Exception as exc:  # offline / tile error -> render without tiles
                logger.warning("basemap unavailable (%s); rendering without tiles", exc)
        fig.savefig(output, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return output


def mean_map(data_dir, fips: str, start: date, end: date, metric: Metric, *,
             palette: str = "YlOrRd", output, renderer: str = "matplotlib",
             basemap: bool = True) -> Path | None:
    """Render a per-station mean map for `metric`; return the path or None if no data."""
    points = station_means(data_dir, fips, start, end, metric)
    if points.empty:
        return None
    label = f"mean {metric.value} ({metric.unit})"
    title = f"{fips} {metric.value} {start.isoformat()}..{end.isoformat()}"
    return get_renderer(renderer).render_point_map(
        points, value_label=label, palette=palette, title=title,
        output=output, basemap=basemap)
```

- [ ] **Step 4: Run, confirm PASS.** `uv run pytest tests/test_visualize.py -v` then `uv run pytest -q`.

- [ ] **Step 5: Stage.** `git add src/smoke_sense/visualize.py tests/test_visualize.py`

---

### Task 2: `visualize` sub-app + `mean-map` subcommand

**Goal:** Make `visualize` a Typer sub-app and add the `mean-map` subcommand wired to `visualize.mean_map`.

**Files:**
- Modify: `src/smoke_sense/bin/visualize.py`
- Modify: `src/smoke_sense/bin/__init__.py`
- Create: `tests/test_visualize_cli.py`

**Acceptance Criteria:**
- [ ] `smoke-sense visualize mean-map …` writes a PNG for a seeded store
- [ ] invalid FIPS / invalid metric / unknown renderer exit non-zero
- [ ] empty data prints a no-data message and writes nothing

**Verify:** `uv run pytest tests/test_visualize_cli.py -v` → all pass

**Steps:**

- [ ] **Step 1: Create `tests/test_visualize_cli.py`**

```python
from datetime import date

import pandas as pd
from typer.testing import CliRunner

from smoke_sense import store
from smoke_sense.bin import app
from smoke_sense.data import Metric

runner = CliRunner()


def _seed(tmp_path):
    df = pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-06-16T01:00:00", tz="UTC"),
        "county_fips": "06037", "station_id": "s1",
        "latitude": 34.0, "longitude": -118.2,
        "metric": Metric.PM2_5.value, "value": 12.0,
        "aqi": pd.NA, "agg_window": 10, "source": "purpleair",
    }])
    store.write(tmp_path, "06037", df)


def test_mean_map_writes_png(tmp_path):
    _seed(tmp_path)
    out = tmp_path / "m.png"
    result = runner.invoke(app, [
        "visualize", "mean-map", "06037", "--start", "2026-06-16",
        "--end", "2026-06-16", "--metric", "PM2.5", "--no-basemap",
        "--output", str(out), "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert out.exists() and out.stat().st_size > 0


def test_mean_map_no_data_message(tmp_path):
    result = runner.invoke(app, [
        "visualize", "mean-map", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--no-basemap", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "no data" in result.output


def test_mean_map_invalid_fips(tmp_path):
    result = runner.invoke(app, [
        "visualize", "mean-map", "6037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--no-basemap", "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_mean_map_invalid_metric(tmp_path):
    result = runner.invoke(app, [
        "visualize", "mean-map", "06037", "--start", "2026-06-16",
        "--metric", "nope", "--no-basemap", "--output-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_mean_map_unknown_renderer(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "visualize", "mean-map", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--renderer", "nope", "--no-basemap",
        "--output-dir", str(tmp_path)])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run, confirm FAIL.**

- [ ] **Step 3: Replace `src/smoke_sense/bin/visualize.py`**

```python
"""`smoke-sense visualize` subcommands."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .. import visualize as viz
from ..data import Metric

app = typer.Typer(help="Visualizations of stored AQI data.")
console = Console()


@app.command("mean-map")
def mean_map(
    county_fips: str = typer.Argument(..., help="5-digit county FIPS code"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"),
    metric: str = typer.Option(..., "--metric", help="Metric to render"),
    palette: str = typer.Option("YlOrRd", help="matplotlib colormap name"),
    output: Optional[Path] = typer.Option(None, help="Output PNG path"),
    renderer: str = typer.Option("matplotlib", help="Rendering engine"),
    basemap: bool = typer.Option(True, "--basemap/--no-basemap", help="Overlay map tiles"),
    output_dir: Path = typer.Option(Path("./data"), "--output-dir", help="Data directory"),
) -> None:
    """Map each sensor as a dot colored by the mean of a metric over a period."""
    if not (len(county_fips) == 5 and county_fips.isdigit()):
        raise typer.BadParameter(f"county FIPS must be 5-digit, got {county_fips!r}")
    try:
        chosen = Metric(metric)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    start_date = start.date()
    end_date = end.date() if end else date.today()
    out = output or (
        output_dir / county_fips / f"{chosen.value}_{start_date}_{end_date}_mean.png")

    try:
        result = viz.mean_map(
            output_dir, county_fips, start_date, end_date, chosen,
            palette=palette, output=out, renderer=renderer, basemap=basemap)
    except KeyError as exc:  # unknown renderer
        raise typer.BadParameter(str(exc)) from exc

    if result is None:
        console.print(
            f"[yellow]no data for {county_fips}/{chosen.value} in "
            f"{start_date}..{end_date}[/]")
        return
    console.print(f"[green]Wrote[/] {result}")
```

- [ ] **Step 4: Register the sub-app — update `src/smoke_sense/bin/__init__.py` to EXACTLY:**

```python
from typer import Typer

from . import credentials, fetch, forecast, summary, visualize

app = Typer()

app.command()(fetch.fetch)
app.command()(forecast.forecast)
app.command()(summary.summary)
app.add_typer(credentials.app, name="credentials")
app.add_typer(visualize.app, name="visualize")
```
(Note: this replaces the old `app.command()(visualize.visualize)` line — `visualize` is now a sub-app, not a single command.)

- [ ] **Step 5: Run, confirm PASS.** `uv run pytest tests/test_visualize_cli.py -v`, then `uv run pytest -q`, then `uv run smoke-sense visualize mean-map --help` (shows the options) and `uv run smoke-sense visualize --help` (lists `mean-map`).

- [ ] **Step 6: Stage.** `git add src/smoke_sense/bin/visualize.py src/smoke_sense/bin/__init__.py tests/test_visualize_cli.py`

---

## Self-Review

**Spec coverage:**
- `station_means` (read_range + metric filter + per-station mean + stations join) → Task 0 ✓
- `MapRenderer` registry + matplotlib PNG renderer + colorbar/palette/title + basemap with graceful fallback → Task 1 ✓
- `mean_map` orchestration (path or None) → Task 1 ✓
- `visualize` sub-app + `mean-map` subcommand; inputs fips/start/end/metric; `--palette`/`--output`/`--renderer`/`--no-basemap`/`--output-dir`; default output path; no-data message → Task 2 ✓
- Errors: invalid fips/metric/renderer; empty data → Task 2 ✓
- Deps matplotlib + contextily → Task 0 ✓
- Tests for aggregation, renderer smoke (Agg, no basemap), CLI → Tasks 0–2 ✓

**Placeholder scan:** none — full code in every step.

**Type/name consistency:** `station_means(data_dir, fips, start, end, metric) -> DataFrame[_MEAN_COLUMNS]`; `MapRenderer.render_point_map(points, *, value_label, palette, title, output, basemap=True)`; `get_renderer`/`register_renderer`; `mean_map(..., palette, output, renderer, basemap)`; CLI imports the core module as `viz` (bin module is `visualize`, core module is `smoke_sense.visualize`); colorbar label uses `Metric.unit`. Tests force `--no-basemap`/`basemap=False` and matplotlib `Agg` to stay offline/headless.

**Note:** contextily (and its geo deps) is only imported inside `MatplotlibRenderer` when `basemap=True`, so tests and basemap-less runs don't touch it; it remains a declared dependency.
