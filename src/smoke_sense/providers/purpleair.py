"""PurpleAir provider — sub-hourly low-cost sensor data within a county bbox."""

from __future__ import annotations

import time
import warnings
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from ..aqi import compute_aqi
from ..data import Pollutant, empty_frame
from ..geo import bbox_for_county, county_polygon, point_in_polygon
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
    supported_cadences = [0, 10, 30, 60, 360, 1440]

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

    _MAX_RETRIES = 5

    def _get(self, url: str, params: dict) -> dict:
        """GET with retry on HTTP 429 (honor Retry-After, else exp. backoff)."""
        delay = 2.0
        for attempt in range(self._MAX_RETRIES + 1):
            resp = self.session.get(
                url, headers=self._headers(), params=params, timeout=120)
            if resp.status_code == 429 and attempt < self._MAX_RETRIES:
                header = resp.headers.get("Retry-After")
                try:
                    wait = float(header) if header is not None else delay
                except (TypeError, ValueError):
                    wait = delay
                time.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    def _list_sensors(self, bbox) -> list[dict]:
        payload = self._get(
            _SENSORS_URL,
            {
                "fields": "latitude,longitude,last_seen,date_created",
                "location_type": 0,
                "nwlng": bbox.min_lon, "nwlat": bbox.max_lat,
                "selng": bbox.max_lon, "selat": bbox.min_lat,
            },
        )
        fields = payload["fields"]
        return [dict(zip(fields, row)) for row in payload["data"]]

    @staticmethod
    def _filter_sensors(sensors: list[dict], geometry: dict,
                        start: date, end: date) -> list[dict]:
        """Keep sensors active in the window and inside the county polygon.

        A sensor's active interval [date_created, last_seen] must overlap the
        requested [start, end] window, and its location must be in the polygon.
        """
        start_ts = int(
            datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp()
        )
        end_ts = int(
            (datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
             + timedelta(days=1)).timestamp()
        )
        kept = []
        dropped_incomplete = 0
        for s in sensors:
            last_seen = s.get("last_seen")
            created = s.get("date_created")
            lat = s.get("latitude")
            lon = s.get("longitude")
            if None in (last_seen, created, lat, lon):
                # We requested these fields; a missing value means an unexpected
                # API response shape, not a normal sensor — surface it.
                dropped_incomplete += 1
                continue
            if not (last_seen >= start_ts and created <= end_ts):
                continue
            if not point_in_polygon(lon, lat, geometry):
                continue
            kept.append(s)
        if dropped_incomplete:
            warnings.warn(
                f"purpleair: dropped {dropped_incomplete} sensor(s) missing "
                "last_seen/date_created/location fields"
            )
        return kept

    def _get_history(self, sensor_id, start: date, end: date, average: int,
                     fields: list[str]) -> dict:
        start_ts = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        # end date is inclusive: request through the end of that day, capped at now.
        end_ts = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
        end_ts = min(end_ts, datetime.now(timezone.utc))
        return self._get(
            _HISTORY_URL.format(sensor_id=sensor_id),
            {
                "start_timestamp": int(start_ts.timestamp()),
                "end_timestamp": int(end_ts.timestamp()),
                "average": average,
                "fields": ",".join(fields),
            },
        )

    def _history_chunked(self, sensor_id, start: date, end: date, average: int,
                         fields: list[str]):
        """Fetch history, splitting the date range on an over-range 400.

        Recursively halves down to single-calendar-day requests (which the API
        accepts at every cadence); the two halves never overlap. A 400 on a
        single day is a real error and is surfaced.
        """
        try:
            payload = self._get_history(sensor_id, start, end, average, fields)
            return payload.get("data", []), payload.get("fields", fields)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status == 400 and end > start:
                half = max(1, (end - start).days // 2)
                mid = start + timedelta(days=half)
                left_data, left_fields = self._history_chunked(
                    sensor_id, start, mid - timedelta(days=1), average, fields)
                right_data, right_fields = self._history_chunked(
                    sensor_id, mid, end, average, fields)
                return left_data + right_data, left_fields or right_fields
            raise

    def _parse_history(self, payload, sensor_id, lat, lon, county_fips, pollutants,
                       agg: int = 60):
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
                    "agg_window": agg,
                    "source": "purpleair",
                }
            ).dropna(subset=["value"]).sort_values("timestamp")
            series = part.set_index("timestamp")["value"]
            part["aqi"] = compute_aqi(series, pollutant).to_numpy()
            frames.append(part)

        return pd.concat(frames, ignore_index=True) if frames else empty_frame()

    def fetch(self, county_fips, start, end, pollutants, cadence: int = 60):
        wanted = [p for p in pollutants if p in self.supported]
        for p in pollutants:
            if p not in self.supported:
                warnings.warn(f"{self.name}: pollutant {p.value} not supported, skipping")
        if not wanted:
            return empty_frame()

        average = self.resolve_cadence(cadence)
        bbox = bbox_for_county(county_fips)
        sensors = self._list_sensors(bbox)
        geometry = county_polygon(county_fips)
        sensors = self._filter_sensors(sensors, geometry, start, end)
        if not sensors:
            return empty_frame()
        # PurpleAir returns time_stamp automatically; do not request it.
        fields = ["humidity"] + [
            f for f, (p, _) in _FIELD_MAP.items() if p in wanted
        ]
        frames = []
        for sensor in sensors:
            rows, resp_fields = self._history_chunked(
                sensor["sensor_index"], start, end, average, fields)
            frames.append(
                self._parse_history(
                    {"fields": resp_fields, "data": rows},
                    sensor["sensor_index"],
                    sensor["latitude"], sensor["longitude"],
                    county_fips, wanted, average,
                )
            )
        return pd.concat(frames, ignore_index=True) if frames else empty_frame()
