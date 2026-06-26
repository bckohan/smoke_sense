"""`smoke-sense rank` — list stations ordered by an aggregate of a metric."""

from __future__ import annotations

import json as _json
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from .. import ranking
from .. import visualize as viz
from ..data import Metric
from . import _outlier_cli

console = Console()

_AGGS = ("min", "max", "mean")


def _render(fips: str, metric: Metric, by: str, agg: str, order: str,
            ranked: pd.DataFrame) -> None:
    if ranked.empty:
        console.print(f"[yellow]no data for {fips}/{metric.value}[/]")
        return
    table = Table(title=f"{fips} — {metric.value} ({by}) by {agg} [{order}]")
    table.add_column("#")
    table.add_column("station_id")
    table.add_column(agg)
    table.add_column("count")
    for i, (_, row) in enumerate(ranked.iterrows(), start=1):
        table.add_row(str(i), str(row["station_id"]),
                      f"{row['value']:g}", str(int(row["count"])))
    console.print(table)


def rank(
    county_fips: List[str] = typer.Argument(..., help="One or more 5-digit county FIPS codes"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"),
    metric: str = typer.Option(..., "--metric", help="Metric to rank by"),
    agg: str = typer.Option("mean", "--agg", help="Aggregation: min|max|mean"),
    by: str = typer.Option("value", "--by", help="Rank by raw value or AQI [value|aqi]"),
    descending: bool = typer.Option(
        True, "--desc/--asc", help="Sort highest-first (default) or lowest-first"),
    limit: int = typer.Option(10, "--limit", help="Max stations to list (0 = all)"),
    output: Path = typer.Option(Path("./data"), help="Data directory"),
    json: bool = typer.Option(False, "--json", help="Emit JSON instead of tables"),
    outlier_filter: bool = typer.Option(
        True, "--outlier-filter/--no-outlier-filter",
        help="Drop likely-erroneous readings before ranking"),
    outlier_zscore: Optional[float] = typer.Option(
        None, "--outlier-zscore", help="Per-station z-score threshold (<=0 disables)"),
    outlier_iqr: bool = typer.Option(
        False, "--outlier-iqr/--no-outlier-iqr", help="Enable per-station IQR check"),
    outlier_iqr_k: float = typer.Option(
        3.0, "--outlier-iqr-k", help="IQR multiplier"),
    no_outlier_range: bool = typer.Option(
        False, "--no-outlier-range", help="Disable the physical-bounds check"),
    outlier_bound: Optional[List[str]] = typer.Option(
        None, "--outlier-bound", help="Override a bound: METRIC:LOW:HIGH (repeatable)"),
    exclude_station: Optional[List[str]] = typer.Option(
        None, "--exclude-station",
        help="Drop all rows from this station ID (repeatable)"),
) -> None:
    """List stations ordered by an aggregate of a metric, per county."""
    for fips in county_fips:
        if not (len(fips) == 5 and fips.isdigit()):
            raise typer.BadParameter(f"county FIPS must be 5-digit, got {fips!r}")
    if agg not in _AGGS:
        raise typer.BadParameter(f"--agg must be one of {', '.join(_AGGS)}, got {agg!r}")
    try:
        chosen = Metric(metric)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        column = viz.resolve_by(chosen, by)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    start_date = start.date()
    end_date = end.date() if end else date.today()
    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound, exclude=exclude_station)

    order = "desc" if descending else "asc"
    payload: dict = {}
    for fips in county_fips:
        obs = viz.metric_observations(
            output, fips, start_date, end_date, chosen, outlier_filter=ofilter)
        ranked = ranking.rank_stations(
            obs, column=column, agg=agg, descending=descending, limit=limit)
        if json:
            payload[fips] = {
                "metric": chosen.value, "by": by, "agg": agg, "order": order,
                "stations": [
                    {"station_id": r["station_id"],
                     "value": float(r["value"]),
                     "count": int(r["count"])}
                    for _, r in ranked.iterrows()
                ],
            }
        else:
            _render(fips, chosen, by, agg, order, ranked)

    if json:
        typer.echo(_json.dumps(payload))
