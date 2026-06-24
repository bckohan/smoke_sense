"""EPA Air Quality System (AQS) provider — hourly sample data by county."""

from __future__ import annotations

import warnings
from datetime import date

import pandas as pd
import requests

from ..aqi import compute_aqi
from ..data import COLUMNS, Pollutant, empty_frame
from .base import AQIProvider, register

_BASE_URL = "https://aqs.epa.gov/data/api/sampleData/byCounty"
_CODE_TO_POLLUTANT = {p.aqs_code: p for p in Pollutant}


@register
class EPAAQSProvider(AQIProvider):
    name = "aqs"
    supported = {Pollutant.PM2_5, Pollutant.PM10, Pollutant.O3}
    supported_cadences = [60]

    def __init__(self, email: str | None = None, api_key: str | None = None,
                 session: requests.Session | None = None, **kwargs) -> None:
        self.email = email
        self.api_key = api_key
        self.session = session or requests.Session()

    @staticmethod
    def _year_ranges(start: date, end: date) -> list[tuple[date, date]]:
        """Split [start, end] into per-calendar-year sub-ranges (AQS limit)."""
        ranges: list[tuple[date, date]] = []
        cursor = start
        while cursor <= end:
            year_end = date(cursor.year, 12, 31)
            ranges.append((cursor, min(year_end, end)))
            cursor = date(cursor.year + 1, 1, 1)
        return ranges

    def _request(self, params: dict) -> dict:
        if not self.email or not self.api_key:
            raise ValueError(
                "EPA AQS requires credentials (AQS_EMAIL / AQS_API_KEY)"
            )
        resp = self.session.get(_BASE_URL, params=params, timeout=120)
        resp.raise_for_status()
        return resp.json()

    def _parse(self, payload: dict, county_fips: str, agg: int = 60) -> pd.DataFrame:
        """Convert an AQS sampleData payload to a common-schema frame."""
        records = payload.get("Data", [])
        if not records:
            return empty_frame()

        raw = pd.DataFrame(records)
        # AQS may return parameter codes beyond the ones we requested (e.g.
        # non-FRM PM2.5 code 88502). Keep only codes we can map, so an
        # unexpected code warns-and-continues instead of crashing the fetch.
        raw = raw[raw["parameter_code"].isin(_CODE_TO_POLLUTANT)]
        if raw.empty:
            return empty_frame()
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    raw["date_gmt"] + " " + raw["time_gmt"], utc=True
                ),
                "county_fips": county_fips,
                "station_id": raw["state_code"] + raw["county_code"] + raw["site_number"],
                "latitude": raw["latitude"].astype("float64"),
                "longitude": raw["longitude"].astype("float64"),
                "pollutant": raw["parameter_code"].map(
                    lambda c: _CODE_TO_POLLUTANT[c].value
                ),
                "value": raw["sample_measurement"].astype("float64"),
                "unit": raw["parameter_code"].map(
                    lambda c: _CODE_TO_POLLUTANT[c].unit
                ),
                "aqi": pd.NA,
                "agg_window": agg,
                "source": "aqs",
            }
        )
        df = df.dropna(subset=["value"])
        return self._add_aqi(df)

    @staticmethod
    def _add_aqi(df: pd.DataFrame) -> pd.DataFrame:
        """Compute NowCast AQI per (station, pollutant) group."""
        if df.empty:
            df["aqi"] = pd.array([], dtype="Int16")
            return df
        parts = []
        for (_, pollutant_name), group in df.groupby(["station_id", "pollutant"]):
            group = group.sort_values("timestamp")
            pollutant = Pollutant(pollutant_name)
            series = group.set_index("timestamp")["value"]
            group["aqi"] = compute_aqi(series, pollutant).to_numpy()
            parts.append(group)
        return pd.concat(parts, ignore_index=True)

    def fetch(self, county_fips, start, end, pollutants, cadence: int = 60):
        wanted = [p for p in pollutants if p in self.supported]
        for p in pollutants:
            if p not in self.supported:
                warnings.warn(f"{self.name}: pollutant {p.value} not supported, skipping")
        if not wanted:
            return empty_frame()

        agg = self.resolve_cadence(cadence)
        state, county = county_fips[:2], county_fips[2:]
        frames = []
        for sub_start, sub_end in self._year_ranges(start, end):
            payload = self._request(
                {
                    "email": self.email,
                    "key": self.api_key,
                    "param": ",".join(p.aqs_code for p in wanted),
                    "bdate": sub_start.strftime("%Y%m%d"),
                    "edate": sub_end.strftime("%Y%m%d"),
                    "state": state,
                    "county": county,
                }
            )
            frames.append(self._parse(payload, county_fips, agg))
        return pd.concat(frames, ignore_index=True) if frames else empty_frame()
