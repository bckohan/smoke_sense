"""`smoke-sense fetch` — download AQI series for counties into the common format."""

from __future__ import annotations

import binascii
import json
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
import typer
from cryptography.fernet import InvalidToken
from rich.console import Console

from .. import credentials as credentials_core
from .. import data
from ..data import Pollutant
from ..providers import all_providers, get_provider
from .credentials import resolve_password

console = Console()

DEFAULT_POLLUTANTS = [Pollutant.PM2_5, Pollutant.PM10, Pollutant.O3]


def _resolve_providers(sources: list[str], creds: dict):
    """Construct provider instances for the requested source names."""
    providers = []
    for name in sources:
        providers.append(get_provider(name, **creds))
    return providers


def fetch(
    county_fips: List[str] = typer.Argument(..., help="One or more 5-digit county FIPS codes"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"
    ),
    source: Optional[List[str]] = typer.Option(None, help="Provider(s); default: all"),
    pollutant: Optional[List[str]] = typer.Option(None, help="Pollutant(s); default: PM2.5,PM10,O3"),
    output: Path = typer.Option(Path("./data"), help="Output directory or .parquet path"),
    credentials: Path = typer.Option(
        Path("./credentials.json"), "--credentials", help="Encrypted credentials file"
    ),
    email: Optional[str] = typer.Option(None, envvar="AQS_EMAIL"),
    api_key: Optional[str] = typer.Option(None, envvar="AQS_API_KEY"),
    purpleair_key: Optional[str] = typer.Option(None, envvar="PURPLEAIR_API_KEY"),
) -> None:
    """Fetch AQI data for the given counties and time range into Parquet."""
    for fips in county_fips:
        if not (len(fips) == 5 and fips.isdigit()):
            raise typer.BadParameter(f"county FIPS must be 5-digit, got {fips!r}")

    start_date = start.date()
    end_date = end.date() if end else date.today()

    sources = source or all_providers()
    pollutants = (
        [Pollutant.from_str(p) for p in pollutant] if pollutant else DEFAULT_POLLUTANTS
    )
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
        frames = []
        for provider in providers:
            console.print(f"[cyan]Fetching[/] {fips} from {provider.__class__.__name__}…")
            frames.append(provider.fetch(fips, start_date, end_date, pollutants))
        combined = (
            pd.concat(frames, ignore_index=True) if frames else data.empty_frame()
        )
        combined = data.validate(combined)

        if output.suffix == ".parquet" and len(county_fips) == 1:
            out_path = output
        else:
            name = f"{fips}_{start_date:%Y-%m-%d}_{end_date:%Y-%m-%d}.parquet"
            out_path = output / name
        data.write_parquet(combined, out_path)
        console.print(f"[green]Wrote[/] {len(combined)} rows → {out_path}")
