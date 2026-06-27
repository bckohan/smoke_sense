"""`smoke-sense outliers` — list stations ranked by fraction of flagged readings."""

from __future__ import annotations

import json as _json
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from .. import outliers as outliers_core
from .. import store
from . import _outlier_cli

console = Console()


def _render(fips: str, ranked) -> None:
    if ranked.empty:
        console.print(f"[yellow]no data for {fips}[/]")
        return
    table = Table(title=f"{fips} — outlier stations")
    for col in ("#", "station_id", "readings", "flagged", "% flagged"):
        table.add_column(col)
    for i, (_, row) in enumerate(ranked.iterrows(), start=1):
        table.add_row(str(i), str(row["station_id"]), str(int(row["readings"])),
                      str(int(row["flagged"])), f"{row['fraction'] * 100:.1f}%")
    console.print(table)


def outliers(
    county_fips: List[str] = typer.Argument(..., help="One or more 5-digit county FIPS codes"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"),
    output: Path = typer.Option(Path("./data"), help="Data directory"),
    json: bool = typer.Option(False, "--json", help="Emit JSON instead of tables"),
    limit: int = typer.Option(10, "--limit", help="Max stations to list (0 = all)"),
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
        help="Drop a station from consideration (repeatable)"),
) -> None:
    """List stations ranked by the fraction of their readings flagged as outliers."""
    for fips in county_fips:
        if not (len(fips) == 5 and fips.isdigit()):
            raise typer.BadParameter(f"county FIPS must be 5-digit, got {fips!r}")

    start_date = start.date()
    end_date = end.date() if end else date.today()
    excluded = set(exclude_station or [])
    cfg = _outlier_cli.config_from_flags(
        no_range=no_outlier_range, zscore=outlier_zscore, iqr_on=outlier_iqr,
        iqr_k=outlier_iqr_k, bound=outlier_bound, exclude=None)

    payload: dict = {}
    for fips in county_fips:
        df = store.read_range(output, fips, start_date, end_date)
        if excluded and not df.empty:
            df = df[~df["station_id"].astype(str).isin(excluded)]
        ranked = outliers_core.station_outlier_counts(df, cfg)
        if limit and limit > 0:
            ranked = ranked.head(limit)
        if json:
            payload[fips] = {"stations": [
                {"station_id": r["station_id"], "readings": int(r["readings"]),
                 "flagged": int(r["flagged"]), "fraction": float(r["fraction"])}
                for _, r in ranked.iterrows()
            ]}
        else:
            _render(fips, ranked)

    if json:
        typer.echo(_json.dumps(payload))
