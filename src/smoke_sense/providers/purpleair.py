"""PurpleAir provider — sub-hourly low-cost sensor data within a county bbox."""

from __future__ import annotations

import warnings
from datetime import date, datetime, timezone

import pandas as pd
import requests

from ..aqi import compute_aqi
from ..data import Pollutant, empty_frame
from ..geo import bbox_for_county
from .base import AQIProvider, register

_SENSORS_URL = "https://api.purpleair.com/v1/sensors"
_HISTORY_URL = "https://api.purpleair.com/v1/sensors/{sensor_id}/history"


def epa_correct_pm25(pa_cf1: float, humidity: float) -> float:
    """EPA US-wide correction for PurpleAir PM2.5 (Barkjohn et al.)."""
    return 0.524 * pa_cf1 - 0.0862 * humidity + 5.75


# PurpleAir history field name -> (Pollutant, needs_correction)
_FIELD_MAP = {
    "pm2.5_cf_1": (Pollutant.PM2_5, True),
    "pm10.0_cf_1": (Pollutant.PM10, False),
}


@register
class PurpleAirProvider(AQIProvider):
    name = "purpleair"
    supported = {Pollutant.PM2_5, Pollutant.PM10}

    def __init__(self, purpleair_key: str | None = None,
                 session: requests.Session | None = None, **kwargs) -> None:
        # Only PurpleAir's own key authenticates here. Other providers'
        # credentials (e.g. the AQS api_key) arrive via **kwargs from the
        # CLI's shared creds dict and are deliberately ignored, so a user
        # with only AQS credentials fails fast rather than sending the wrong
        # key to PurpleAir.
        self.api_key = purpleair_key
        self.session = session or requests.Session()

    def _headers(self) -> dict:
        if not self.api_key:
            raise ValueError("PurpleAir requires credentials (PURPLEAIR_API_KEY)")
        return {"X-API-Key": self.api_key}

    def _list_sensors(self, bbox) -> list[dict]:
        resp = self.session.get(
            _SENSORS_URL,
            headers=self._headers(),
            params={
                "fields": "latitude,longitude",
                "nwlng": bbox.min_lon, "nwlat": bbox.max_lat,
                "selng": bbox.max_lon, "selat": bbox.min_lat,
            },
            timeout=120,
        )
        resp.raise_for_status()
        payload = resp.json()
        fields = payload["fields"]
        return [dict(zip(fields, row)) for row in payload["data"]]

    def _get_history(self, sensor_id, start: date, end: date, fields: list[str]) -> dict:
        resp = self.session.get(
            _HISTORY_URL.format(sensor_id=sensor_id),
            headers=self._headers(),
            params={
                "start_timestamp": int(
                    datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp()
                ),
                "end_timestamp": int(
                    datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp()
                ),
                "average": 60,
                "fields": ",".join(fields),
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def _parse_history(self, payload, sensor_id, lat, lon, county_fips, pollutants):
        fields = payload["fields"]
        rows = payload["data"]
        if not rows:
            return empty_frame()
        raw = pd.DataFrame(rows, columns=fields)
        humidity = raw.get("humidity")

        frames = []
        for field, (pollutant, needs_correction) in _FIELD_MAP.items():
            if pollutant not in pollutants or field not in raw.columns:
                continue
            values = raw[field].astype("float64")
            if needs_correction:
                values = epa_correct_pm25(values, humidity.astype("float64"))
            part = pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(raw["time_stamp"], unit="s", utc=True),
                    "county_fips": county_fips,
                    "station_id": str(sensor_id),
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "pollutant": pollutant.value,
                    "value": values,
                    "unit": pollutant.unit,
                    "aqi": pd.NA,
                    "source": "purpleair",
                }
            ).dropna(subset=["value"]).sort_values("timestamp")
            series = part.set_index("timestamp")["value"]
            part["aqi"] = compute_aqi(series, pollutant).to_numpy()
            frames.append(part)

        return pd.concat(frames, ignore_index=True) if frames else empty_frame()

    def fetch(self, county_fips, start, end, pollutants):
        wanted = [p for p in pollutants if p in self.supported]
        for p in pollutants:
            if p not in self.supported:
                warnings.warn(f"{self.name}: pollutant {p.value} not supported, skipping")
        if not wanted:
            return empty_frame()

        bbox = bbox_for_county(county_fips)
        sensors = self._list_sensors(bbox)
        # PurpleAir returns `time_stamp` automatically as the first history
        # column and rejects it as a requested field (HTTP 400), so we must not
        # ask for it explicitly.
        fields = ["humidity"] + [
            f for f, (p, _) in _FIELD_MAP.items() if p in wanted
        ]
        frames = []
        for sensor in sensors:
            payload = self._get_history(sensor["sensor_index"], start, end, fields)
            frames.append(
                self._parse_history(
                    payload, sensor["sensor_index"],
                    sensor["latitude"], sensor["longitude"],
                    county_fips, wanted,
                )
            )
        return pd.concat(frames, ignore_index=True) if frames else empty_frame()
