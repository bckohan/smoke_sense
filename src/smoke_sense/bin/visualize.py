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
