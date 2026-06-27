"""Clarity OpenMap (LAUSD) provider — 30 days of hourly station data.

Pulls from the LAUSD Clarity OpenMap's unauthenticated REST/CSV API. The
endpoints require a browser User-Agent (a non-browser UA receives the SPA's
HTML shell instead of JSON/CSV); no credentials, cookies, or tokens are needed.
"""

from __future__ import annotations

import io
import logging
import time
from datetime import date

import pandas as pd
import requests

from ..data import Metric, empty_frame
from ..geo import county_polygon, point_in_polygon
from .base import AQIProvider, register

_BASE = "https://lausd.map.clarity.io"
_NETWORK = "lausd"
_MARKERS_URL = f"{_BASE}/api/v1/map/air-quality-markers"
_CSV_URL = f"{_BASE}/api/v1/datasources/{{datasource_id}}/measurements.csv"

# A desktop Chrome UA: the OpenMap edge serves JSON/CSV only to browser-like
# clients and otherwise returns its SPA index.html.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# CSV column -> (canonical Metric, AQI column or None). The AQI column carries
# Clarity's own EPA AQI; we use it directly because the concentration columns
# are already NowCast-smoothed (recomputing would double-smooth).
_COLUMNS: dict[str, tuple[Metric, str | None]] = {
    "pm2_5ConcMassNowcast":        (Metric.PM2_5, "pm2_5ConcMassNowcastUsEpaAqi"),
    "pm10ConcMassNowcast":         (Metric.PM10,  "pm10ConcMassNowcastUsEpaAqi"),
    "no2Conc1HourMean":            (Metric.NO2,   "no2Conc1HourMeanUsEpaAqi"),
    "temperatureAmbient1HourMean": (Metric.TEMP,  None),
    "relHumidAmbient1HourMean":    (Metric.RH,    None),
    "windSpeed1HourMean":          (Metric.WIND_SPEED, None),
    "windDirection1HourMean":      (Metric.WIND_DIR,   None),
}

# Clarity reports NO2 in µg/m³; the canonical NO2 unit is ppb. Convert at
# 25 °C / 1 atm where 1 ppb NO2 == 1.88 µg/m³.
_NO2_UGM3_PER_PPB = 1.88


def _to_canonical(metric: Metric, values: pd.Series) -> pd.Series:
    if metric is Metric.NO2:          # µg/m³ -> ppb
        return values / _NO2_UGM3_PER_PPB
    return values


def empty_frame_with_coords() -> pd.DataFrame:
    df = empty_frame()
    df["latitude"] = pd.Series(dtype="float64")
    df["longitude"] = pd.Series(dtype="float64")
    return df


logger = logging.getLogger(__name__)


@register
class ClarityProvider(AQIProvider):
    name = "clarity"
    supported_metrics = {m for m, _ in _COLUMNS.values()}
    supported_cadences = [60]

    _MAX_RETRIES = 5
    _RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

    def __init__(self, session: requests.Session | None = None,
                 user_agent: str | None = None, **kwargs) -> None:
        # clarity needs no credentials; other providers' creds arrive via the
        # shared CLI dict (**kwargs) and are intentionally ignored.
        self.session = session or requests.Session()
        self.user_agent = user_agent or _USER_AGENT

    def _get(self, url: str, params: dict, *, as_text: bool = False):
        """GET with a browser UA; retry 429/5xx; reject the SPA HTML fallback."""
        delay = 2.0
        resp = None
        for attempt in range(self._MAX_RETRIES + 1):
            started = time.monotonic()
            resp = self.session.get(
                url, headers={"User-Agent": self.user_agent},
                params=params, timeout=120)
            elapsed_ms = (time.monotonic() - started) * 1000
            logger.info("GET %s params=%s -> %s (%.0f ms)",
                        url, params, resp.status_code, elapsed_ms)
            if resp.status_code in self._RETRY_STATUS and attempt < self._MAX_RETRIES:
                header = resp.headers.get("Retry-After")
                try:
                    wait = float(header) if header is not None else delay
                except (TypeError, ValueError):
                    wait = delay
                logger.info("%s from %s; retrying in %.0fs (attempt %d/%d)",
                            resp.status_code, url, wait, attempt + 1, self._MAX_RETRIES)
                time.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            break
        resp.raise_for_status()
        if "text/html" in resp.headers.get("Content-Type", ""):
            raise ValueError(
                f"Clarity returned HTML from {url} (SPA fallback) — a browser "
                "User-Agent is required or the endpoint changed")
        return resp.text if as_text else resp.json()

    def _list_stations(self) -> list[dict]:
        """Fetch the LAUSD markers and return station dicts with coordinates."""
        payload = self._get(
            _MARKERS_URL, {"network": _NETWORK, "aqiStandard": "US-EPA"})
        stations: list[dict] = []
        for marker in payload.get("data", {}).get("markers", []):
            coords = (marker.get("location") or {}).get("coordinates")
            if not coords or len(coords) != 2:
                continue
            lon, lat = coords
            stations.append({
                "datasourceId": marker["datasourceId"],
                "name": marker.get("datasourceName"),
                "lon": lon,
                "lat": lat,
            })
        return stations

    @staticmethod
    def _filter_stations(stations: list[dict], geometry: dict) -> list[dict]:
        """Keep stations whose location falls inside the county polygon."""
        return [s for s in stations
                if point_in_polygon(s["lon"], s["lat"], geometry)]

    def _parse_csv(self, text: str, station: dict, county_fips: str,
                   wanted: list[Metric]) -> pd.DataFrame:
        """Convert a station measurements CSV to a common-schema frame."""
        raw = pd.read_csv(io.StringIO(text))
        if "time (UTC)" not in raw.columns:
            return empty_frame_with_coords()
        timestamps = pd.to_datetime(raw["time (UTC)"], utc=True)
        parts: list[pd.DataFrame] = []
        for column, (metric, aqi_col) in _COLUMNS.items():
            if metric not in wanted or column not in raw.columns:
                continue
            values = _to_canonical(metric, raw[column].astype("float64"))
            if aqi_col and aqi_col in raw.columns:
                # round() keeps NaN as NaN; Int16 maps NaN -> pd.NA.
                aqi = raw[aqi_col].astype("float64").round().astype("Int16")
            else:
                aqi = pd.array([pd.NA] * len(raw), dtype="Int16")
            part = pd.DataFrame({
                "timestamp": timestamps,
                "county_fips": county_fips,
                "station_id": str(station["datasourceId"]),
                "latitude": float(station["lat"]),
                "longitude": float(station["lon"]),
                "metric": metric.value,
                "value": values,
                "aqi": aqi,
                "agg_window": 60,
                "source": "clarity",
            }).dropna(subset=["value"])
            if not part.empty:
                parts.append(part)
        if not parts:
            return empty_frame_with_coords()
        return pd.concat(parts, ignore_index=True)

    def fetch(self, county_fips, start, end, metrics, cadence: int = 60):
        wanted = [m for m in metrics if m in self.supported_metrics]
        if not wanted:
            return
        stations = self._filter_stations(
            self._list_stations(), county_polygon(county_fips))
        if not stations:
            return
        for station in stations:
            text = self._get(
                _CSV_URL.format(datasource_id=station["datasourceId"]),
                {"networkId": _NETWORK}, as_text=True)
            chunk = self._parse_csv(text, station, county_fips, wanted)
            if chunk.empty:
                continue
            in_window = (
                (chunk["timestamp"].dt.date >= start)
                & (chunk["timestamp"].dt.date <= end)
            )
            chunk = chunk[in_window]
            if not chunk.empty:
                yield chunk
