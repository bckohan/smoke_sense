"""`smoke-sense visualize` subcommands."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .. import visualize as viz
from ..data import Metric
from . import _outlier_cli

app = typer.Typer(help="Visualizations of stored AQI data.")
console = Console()


@app.command("mean-map")
def mean_map(
    county_fips: str = typer.Argument(..., help="5-digit county FIPS code"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"),
    metric: str = typer.Option(..., "--metric", help="Metric to render"),
    by: str = typer.Option("value", "--by", help="Plot raw value or AQI [value|aqi]"),
    palette: str = typer.Option("YlOrRd", help="matplotlib colormap name"),
    output: Optional[Path] = typer.Option(None, help="Output PNG path"),
    renderer: str = typer.Option("matplotlib", help="Rendering engine"),
    basemap: bool = typer.Option(True, "--basemap/--no-basemap", help="Overlay map tiles"),
    outlier_filter_on: bool = typer.Option(
        True, "--outlier-filter/--no-outlier-filter",
        help="Drop likely-erroneous readings before plotting"),
    outlier_zscore: Optional[float] = typer.Option(
        None, "--outlier-zscore", help="Per-station z-score threshold (<=0 disables)"),
    outlier_iqr: bool = typer.Option(
        False, "--outlier-iqr/--no-outlier-iqr", help="Enable per-station IQR check"),
    outlier_iqr_k: float = typer.Option(
        3.0, "--outlier-iqr-k", help="IQR multiplier"),
    no_outlier_range: bool = typer.Option(
        False, "--no-outlier-range", help="Disable the physical-bounds check"),
    outlier_bound: Optional[list[str]] = typer.Option(
        None, "--outlier-bound", help="Override a bound: METRIC:LOW:HIGH (repeatable)"),
    output_dir: Path = typer.Option(Path("./data"), "--output-dir", help="Data directory"),
) -> None:
    """Map each sensor as a dot colored by the mean of a metric over a period."""
    _validate_fips(county_fips)
    try:
        chosen = Metric(metric)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    start_date = start.date()
    end_date = end.date() if end else date.today()
    out = output or (
        output_dir / county_fips
        / f"{chosen.value}_{by}_{start_date}_{end_date}_mean.png")

    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter_on, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound)

    try:
        result = viz.mean_map(
            output_dir, county_fips, start_date, end_date, chosen,
            by=by, palette=palette, output=out, renderer=renderer, basemap=basemap,
            outlier_filter=ofilter)
    except KeyError as exc:  # unknown renderer
        raise typer.BadParameter(str(exc)) from exc
    except ValueError as exc:  # invalid --by combo
        raise typer.BadParameter(str(exc)) from exc

    if result is None:
        console.print(
            f"[yellow]no data for {county_fips}/{chosen.value} in "
            f"{start_date}..{end_date}[/]")
        return
    console.print(f"[green]Wrote[/] {result}")


def _validate_fips(county_fips: str) -> None:
    if not (len(county_fips) == 5 and county_fips.isdigit()):
        raise typer.BadParameter(f"county FIPS must be 5-digit, got {county_fips!r}")


def _prepare(county_fips: str, metric: str, by: str) -> tuple[Metric, str]:
    """Validate inputs; return (chosen Metric, y_column str)."""
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


def _render_chart(kind: str, method_name: str, county_fips: str, start: datetime,
                  end: Optional[datetime], metric: str, by: str, palette: str,
                  output: Optional[Path], renderer: str, output_dir: Path, *,
                  stations: Optional[list[str]] = None,
                  extra: Optional[dict] = None,
                  outlier_filter=None) -> None:
    chosen, y_column = _prepare(county_fips, metric, by)
    start_date = start.date()
    end_date = end.date() if end else date.today()
    obs = viz.metric_observations(output_dir, county_fips, start_date, end_date,
                                  chosen, outlier_filter=outlier_filter)
    if stations:
        obs = obs[obs["station_id"].isin(set(stations))]
    if obs.empty:
        console.print(
            f"[yellow]no data for {county_fips}/{chosen.value} in "
            f"{start_date}..{end_date}[/]")
        return
    if obs[y_column].dropna().empty:
        console.print(
            f"[yellow]no {by} data for {county_fips}/{chosen.value} in "
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
    outlier_filter_on: bool = typer.Option(
        True, "--outlier-filter/--no-outlier-filter",
        help="Drop likely-erroneous readings before plotting"),
    outlier_zscore: Optional[float] = typer.Option(
        None, "--outlier-zscore", help="Per-station z-score threshold (<=0 disables)"),
    outlier_iqr: bool = typer.Option(
        False, "--outlier-iqr/--no-outlier-iqr", help="Enable per-station IQR check"),
    outlier_iqr_k: float = typer.Option(
        3.0, "--outlier-iqr-k", help="IQR multiplier"),
    no_outlier_range: bool = typer.Option(
        False, "--no-outlier-range", help="Disable the physical-bounds check"),
    outlier_bound: Optional[list[str]] = typer.Option(
        None, "--outlier-bound", help="Override a bound: METRIC:LOW:HIGH (repeatable)"),
    output_dir: Path = typer.Option(Path("./data"), "--output-dir", help="Data directory"),
) -> None:
    """One line per station over time."""
    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter_on, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound)
    _render_chart("series", "render_series", county_fips, start, end, metric, by,
                  palette, output, renderer, output_dir, stations=station,
                  outlier_filter=ofilter)


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
    outlier_filter_on: bool = typer.Option(
        True, "--outlier-filter/--no-outlier-filter",
        help="Drop likely-erroneous readings before plotting"),
    outlier_zscore: Optional[float] = typer.Option(
        None, "--outlier-zscore", help="Per-station z-score threshold (<=0 disables)"),
    outlier_iqr: bool = typer.Option(
        False, "--outlier-iqr/--no-outlier-iqr", help="Enable per-station IQR check"),
    outlier_iqr_k: float = typer.Option(
        3.0, "--outlier-iqr-k", help="IQR multiplier"),
    no_outlier_range: bool = typer.Option(
        False, "--no-outlier-range", help="Disable the physical-bounds check"),
    outlier_bound: Optional[list[str]] = typer.Option(
        None, "--outlier-bound", help="Override a bound: METRIC:LOW:HIGH (repeatable)"),
    output_dir: Path = typer.Option(Path("./data"), "--output-dir", help="Data directory"),
) -> None:
    """All observations as points colored by station."""
    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter_on, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound)
    _render_chart("scatter", "render_scatter", county_fips, start, end, metric, by,
                  palette, output, renderer, output_dir, outlier_filter=ofilter)


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
    outlier_filter_on: bool = typer.Option(
        True, "--outlier-filter/--no-outlier-filter",
        help="Drop likely-erroneous readings before plotting"),
    outlier_zscore: Optional[float] = typer.Option(
        None, "--outlier-zscore", help="Per-station z-score threshold (<=0 disables)"),
    outlier_iqr: bool = typer.Option(
        False, "--outlier-iqr/--no-outlier-iqr", help="Enable per-station IQR check"),
    outlier_iqr_k: float = typer.Option(
        3.0, "--outlier-iqr-k", help="IQR multiplier"),
    no_outlier_range: bool = typer.Option(
        False, "--no-outlier-range", help="Disable the physical-bounds check"),
    outlier_bound: Optional[list[str]] = typer.Option(
        None, "--outlier-bound", help="Override a bound: METRIC:LOW:HIGH (repeatable)"),
    output_dir: Path = typer.Option(Path("./data"), "--output-dir", help="Data directory"),
) -> None:
    """Mean across stations per timestamp, optional min/max band."""
    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter_on, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound)
    _render_chart("aggregate", "render_aggregate", county_fips, start, end, metric, by,
                  palette, output, renderer, output_dir, extra={"band": band},
                  outlier_filter=ofilter)


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
    outlier_filter_on: bool = typer.Option(
        True, "--outlier-filter/--no-outlier-filter",
        help="Drop likely-erroneous readings before plotting"),
    outlier_zscore: Optional[float] = typer.Option(
        None, "--outlier-zscore", help="Per-station z-score threshold (<=0 disables)"),
    outlier_iqr: bool = typer.Option(
        False, "--outlier-iqr/--no-outlier-iqr", help="Enable per-station IQR check"),
    outlier_iqr_k: float = typer.Option(
        3.0, "--outlier-iqr-k", help="IQR multiplier"),
    no_outlier_range: bool = typer.Option(
        False, "--no-outlier-range", help="Disable the physical-bounds check"),
    outlier_bound: Optional[list[str]] = typer.Option(
        None, "--outlier-bound", help="Override a bound: METRIC:LOW:HIGH (repeatable)"),
    output_dir: Path = typer.Option(Path("./data"), "--output-dir", help="Data directory"),
) -> None:
    """Distribution of the chosen quantity over all observations."""
    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter_on, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound)
    _render_chart("histogram", "render_histogram", county_fips, start, end, metric, by,
                  palette, output, renderer, output_dir, extra={"bins": bins},
                  outlier_filter=ofilter)
