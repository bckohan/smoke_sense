"""EPA Air Quality System (AQS) provider — hourly sample data by county."""

from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
import requests

from ..aqi import compute_aqi
from ..data import AQI_METRICS, Metric, empty_frame
from ..logutil import redact
from .base import AQIProvider, register

_BASE_URL = "https://aqs.epa.gov/data/api/sampleData/byCounty"

# Metric -> AQS parameter code(s). Multiple codes collapse to one metric.
_AQS_CODES: dict[Metric, tuple[str, ...]] = {
    Metric.PM2_5: ("88101", "88502"),   # FRM + non-FRM -> canonical PM2.5
    Metric.PM10:  ("81102",),
    Metric.O3:    ("44201",),
    Metric.CO:    ("42101",),
    Metric.SO2:   ("42401",),
    Metric.NO2:   ("42602",),
    Metric.PB:    ("14129",),
    Metric.TEMP:  ("62101",),       # outdoor temperature, reported °F -> °C
    Metric.RH:    ("62201",),       # relative humidity, %
    Metric.PRESSURE: ("64101",),    # barometric pressure, millibars -> hPa (1:1)
    Metric.WIND_SPEED: ("61103",),  # resultant wind speed, knots -> m/s
    Metric.WIND_DIR:   ("61104",),  # resultant wind direction, degrees
}
_CODE_TO_METRIC = {code: m for m, codes in _AQS_CODES.items() for code in codes}


def _to_canonical(metric: Metric, value: float) -> float:
    if metric is Metric.TEMP:        # °F -> °C
        return (value - 32.0) * 5.0 / 9.0
    if metric is Metric.WIND_SPEED:  # knots -> m/s
        return value * 0.514444
    return value


def empty_frame_with_coords() -> pd.DataFrame:
    df = empty_frame()
    df["latitude"] = pd.Series(dtype="float64")
    df["longitude"] = pd.Series(dtype="float64")
    return df


logger = logging.getLogger(__name__)


@register
class EPAAQSProvider(AQIProvider):
    name = "aqs"
    supported_metrics = set(_AQS_CODES)
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
        started = time.monotonic()
        resp = self.session.get(_BASE_URL, params=params, timeout=120)
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.info("GET %s params=%s -> %s (%.0f ms)", _BASE_URL,
                    redact(params, {"email", "key"}), resp.status_code, elapsed_ms)
        resp.raise_for_status()
        return resp.json()

    def _parse(self, payload: dict, county_fips: str, agg: int = 60) -> pd.DataFrame:
        """Convert an AQS sampleData payload to a common-schema frame."""
        records = payload.get("Data") or []
        if not records:
            return empty_frame_with_coords()
        raw = pd.DataFrame(records)
        # AQS may return parameter codes beyond the ones we requested (e.g.
        # non-FRM PM2.5 code 88502). Keep only codes we can map, so an
        # unexpected code is dropped instead of crashing the fetch.
        raw = raw[raw["parameter_code"].isin(_CODE_TO_METRIC)]
        if raw.empty:
            return empty_frame_with_coords()
        metric_series = [_CODE_TO_METRIC[c] for c in raw["parameter_code"]]
        values = [
            _to_canonical(m, float(v))
            for m, v in zip(metric_series, raw["sample_measurement"])
        ]
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(raw["date_gmt"] + " " + raw["time_gmt"], utc=True),
            "county_fips": county_fips,
            "station_id": raw["state_code"] + raw["county_code"] + raw["site_number"],
            "latitude": raw["latitude"].astype("float64"),
            "longitude": raw["longitude"].astype("float64"),
            "metric": [m.value for m in metric_series],
            "value": values,
            "aqi": pd.NA,
            "agg_window": agg,
            "source": "aqs",
        }).dropna(subset=["value"])
        return self._add_aqi(df)

    @staticmethod
    def _add_aqi(df: pd.DataFrame) -> pd.DataFrame:
        """Compute NowCast AQI per (station, metric) group, AQI metrics only."""
        if df.empty:
            df["aqi"] = pd.array([], dtype="Int16")
            return df
        parts = []
        for (_, metric_name), group in df.groupby(["station_id", "metric"]):
            group = group.sort_values("timestamp")
            metric = Metric(metric_name)
            if metric in AQI_METRICS:
                series = group.set_index("timestamp")["value"]
                group["aqi"] = compute_aqi(series, metric).to_numpy()
            else:
                group["aqi"] = pd.array([pd.NA] * len(group), dtype="Int16")
            parts.append(group)
        return pd.concat(parts, ignore_index=True)

    def fetch(self, county_fips, start, end, metrics, cadence: int = 60):
        wanted = [m for m in metrics if m in self.supported_metrics]
        if not wanted:
            return
        codes = [c for m in wanted for c in _AQS_CODES[m]]
        agg = self.resolve_cadence(cadence)
        state, county = county_fips[:2], county_fips[2:]
        for sub_start, sub_end in self._year_ranges(start, end):
            for i in range(0, len(codes), 5):
                payload = self._request({
                    "email": self.email, "key": self.api_key,
                    "param": ",".join(codes[i:i + 5]),
                    "bdate": sub_start.strftime("%Y%m%d"),
                    "edate": sub_end.strftime("%Y%m%d"),
                    "state": state, "county": county,
                })
                chunk = self._parse(payload, county_fips, agg)
                if not chunk.empty:
                    yield chunk
