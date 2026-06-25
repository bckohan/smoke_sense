"""`smoke-sense summary` — report stored data coverage and statistics."""

from __future__ import annotations

import json as _json
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from .. import store
from .. import summary as summary_core

console = Console()


def _render(fips: str, s: dict) -> None:
    cov = s["coverage"]
    rng = s["range"]
    if cov["total_rows"] == 0:
        console.print(f"[yellow]no data for {fips} in {rng['start']}..{rng['end']}[/]")
        return

    console.print(f"[bold]{fips}[/]  {rng['start']}..{rng['end']}")

    coverage = Table(title="Coverage")
    coverage.add_column("metric")
    coverage.add_column("value")
    coverage.add_row("days present", f"{cov['days_present']}/{cov['total_days']}")
    missing = cov["days_missing"]
    coverage.add_row(
        "days missing",
        str(len(missing)) + (f" ({', '.join(missing)})" if missing else ""),
    )
    coverage.add_row("first", cov["first_timestamp"])
    coverage.add_row("last", cov["last_timestamp"])
    coverage.add_row("rows", str(cov["total_rows"]))
    console.print(coverage)

    breakdown = Table(title="Breakdown")
    for col in ("source", "metric", "agg_window", "rows"):
        breakdown.add_column(col)
    for row in s["breakdown"]:
        breakdown.add_row(row["source"], row["metric"],
                          str(row["agg_window"]), str(row["rows"]))
    console.print(breakdown)

    metrics = Table(title="Metrics")
    for col in ("metric", "stations", "sources",
                "min", "p25", "p50", "mean", "p75", "max", "aqi min/mean/max"):
        metrics.add_column(col)
    for row in s["metrics"]:
        v = row["value"]
        a = row["aqi"]
        aqi_str = "-" if a is None else f"{a['min']}/{a['mean']:.0f}/{a['max']}"
        # `:g` keeps real digits for small-magnitude metrics (e.g. O3 ~0.04 ppm)
        # that `:.1f` would collapse to 0.0.
        metrics.add_row(
            row["metric"], str(row["stations"]), ",".join(row["sources"]),
            f"{v['min']:g}", f"{v['p25']:g}", f"{v['p50']:g}", f"{v['mean']:g}",
            f"{v['p75']:g}", f"{v['max']:g}", aqi_str,
        )
    console.print(metrics)


def summary(
    county_fips: List[str] = typer.Argument(..., help="One or more 5-digit county FIPS codes"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"
    ),
    output: Path = typer.Option(Path("./data"), help="Data directory"),
    json: bool = typer.Option(False, "--json", help="Emit JSON instead of tables"),
) -> None:
    """Summarize stored AQI data for the given counties and time range."""
    for fips in county_fips:
        if not (len(fips) == 5 and fips.isdigit()):
            raise typer.BadParameter(f"county FIPS must be 5-digit, got {fips!r}")

    start_date = start.date()
    end_date = end.date() if end else date.today()

    results = {
        fips: summary_core.summarize(
            store.read_range(output, fips, start_date, end_date), start_date, end_date
        )
        for fips in county_fips
    }

    if json:
        typer.echo(_json.dumps(results))
        return
    for fips, s in results.items():
        _render(fips, s)
