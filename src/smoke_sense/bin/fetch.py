"""`smoke-sense fetch` — download AQI series for counties into the per-day store."""

from __future__ import annotations

import binascii
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import typer
from cryptography.fernet import InvalidToken
from rich.console import Console
from rich.logging import RichHandler

from .. import credentials as credentials_core
from .. import fetcher
from ..cadence import Cadence
from ..data import Metric
from ..providers import all_providers, get_provider
from .credentials import resolve_password

console = Console()


def _resolve_providers(sources: list[str], creds: dict):
    """Construct provider instances for the requested source names."""
    return [get_provider(name, **creds) for name in sources]


def _configure_logging(verbose: bool) -> None:
    """Attach a stderr Rich handler at INFO to the package logger when verbose."""
    if not verbose:
        return
    pkg_logger = logging.getLogger("smoke_sense")
    pkg_logger.setLevel(logging.INFO)
    # Idempotent: don't stack duplicate handlers if called more than once.
    if any(isinstance(h, RichHandler) for h in pkg_logger.handlers):
        return
    pkg_logger.addHandler(
        RichHandler(console=Console(stderr=True), show_path=False, show_time=False)
    )


def fetch(
    county_fips: List[str] = typer.Argument(..., help="One or more 5-digit county FIPS codes"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"
    ),
    cadence: Cadence = typer.Option(Cadence.TEN_MIN, help="Averaging window"),
    refetch: bool = typer.Option(False, help="Re-fetch days already stored"),
    source: Optional[List[str]] = typer.Option(None, help="Provider(s); default: all"),
    metric: Optional[List[str]] = typer.Option(None, "--metric", help="Metric(s); default: all available"),
    output: Path = typer.Option(Path("./data"), help="Data directory"),
    credentials: Path = typer.Option(
        Path("./credentials.json"), "--credentials", help="Encrypted credentials file"
    ),
    email: Optional[str] = typer.Option(None, envvar="AQS_EMAIL"),
    api_key: Optional[str] = typer.Option(None, envvar="AQS_API_KEY"),
    purpleair_key: Optional[str] = typer.Option(None, envvar="PURPLEAIR_API_KEY"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log requests to stderr"),
) -> None:
    """Fetch AQI data for the given counties and time range into the store."""
    _configure_logging(verbose)
    for fips in county_fips:
        if not (len(fips) == 5 and fips.isdigit()):
            raise typer.BadParameter(f"county FIPS must be 5-digit, got {fips!r}")

    start_date = start.date()
    end_date = end.date() if end else date.today()

    sources = source or all_providers()
    try:
        metrics = [Metric(m) for m in metric] if metric else list(Metric)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        creds = credentials_core.resolve(
            {"email": email, "api_key": api_key, "purpleair_key": purpleair_key},
            credentials,
            get_password=resolve_password,
        )
    except (InvalidToken, json.JSONDecodeError, KeyError, binascii.Error) as exc:
        raise typer.BadParameter(
            f"could not decrypt {credentials} — wrong password?"
        ) from exc

    providers = _resolve_providers(sources, creds)

    for fips in county_fips:
        console.print(f"[cyan]Fetching[/] {fips} ({cadence.value}) …")
        fetcher.fetch_county(
            output, fips, start_date, end_date, metrics, cadence.minutes,
            providers, today=date.today(), refetch=refetch,
        )
        console.print(f"[green]Updated[/] {output}/{fips}")
