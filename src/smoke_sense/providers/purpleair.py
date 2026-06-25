"""PurpleAir provider — sub-hourly low-cost sensor data within a county bbox."""

from __future__ import annotations

import logging
import time
import warnings
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from ..aqi import compute_aqi
from ..data import AQI_METRICS, Metric, empty_frame
from ..geo import bbox_for_county, county_polygon, point_in_polygon
from .base import AQIProvider, register

_SENSORS_URL = "https://api.purpleair.com/v1/sensors"
_HISTORY_URL = "https://api.purpleair.com/v1/sensors/{sensor_id}/history"


def epa_correct_pm25(pa_cf1: float, humidity: float) -> float:
    """EPA US-wide correction for PurpleAir PM2.5 (Barkjohn et al.)."""
    return 0.524 * pa_cf1 - 0.0862 * humidity + 5.75


# Metric -> PurpleAir history field (raw passthrough unless converted)
_FIELD_MAP: dict[Metric, str] = {
    Metric.PM2_5_CF1: "pm2.5_cf_1",
    Metric.PM2_5_ATM: "pm2.5_atm",
    Metric.PM10:      "pm10.0_cf_1",   # canonical PM10 (uncorrected)
    Metric.PM10_CF1:  "pm10.0_cf_1",
    Metric.PM10_ATM:  "pm10.0_atm",
    Metric.PM1_0_CF1: "pm1.0_cf_1",
    Metric.PM1_0_ATM: "pm1.0_atm",
    Metric.TEMP:      "temperature",   # °F -> °C
    Metric.RH:        "humidity",      # %
    Metric.PRESSURE:  "pressure",      # millibars == hPa
    Metric.VOC:       "voc",           # iaq
}
# PM2.5 (corrected) is derived from pm2.5_cf_1 + humidity, handled specially.
_CORRECTED_PM25 = Metric.PM2_5


def _to_canonical(metric: Metric, values):
    if metric is Metric.TEMP:        # °F -> °C
        return (values - 32.0) * 5.0 / 9.0
    return values


logger = logging.getLogger(__name__)


@register
class PurpleAirProvider(AQIProvider):
    name = "purpleair"
    supported_metrics = set(_FIELD_MAP) | {_CORRECTED_PM25}
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
            started = time.monotonic()
            resp = self.session.get(
                url, headers=self._headers(), params=params, timeout=120)
            elapsed_ms = (time.monotonic() - started) * 1000
            logger.info("GET %s params=%s -> %s (%.0f ms)",
                        url, params, resp.status_code, elapsed_ms)
            if resp.status_code == 429 and attempt < self._MAX_RETRIES:
                header = resp.headers.get("Retry-After")
                try:
                    wait = float(header) if header is not None else delay
                except (TypeError, ValueError):
                    wait = delay
                logger.info("429 from %s; retrying in %.0fs (attempt %d/%d)",
                            url, wait, attempt + 1, self._MAX_RETRIES)
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

    def _parse_history(self, payload, sensor_id, lat, lon, county_fips, wanted, agg):
        fields = payload["fields"]
        rows = payload["data"]
        if not rows:
            return empty_frame()
        raw = pd.DataFrame(rows, columns=fields)
        ts = pd.to_datetime(raw["time_stamp"], unit="s", utc=True)
        humidity = raw.get("humidity")
        parts = []

        def _emit(metric: Metric, values):
            part = pd.DataFrame({
                "timestamp": ts,
                "county_fips": county_fips,
                "station_id": str(sensor_id),
                "latitude": float(lat),
                "longitude": float(lon),
                "metric": metric.value,
                "value": _to_canonical(metric, values.astype("float64")),
                "aqi": pd.NA,
                "agg_window": agg,
                "source": "purpleair",
            }).dropna(subset=["value"]).sort_values("timestamp")
            if metric in AQI_METRICS and not part.empty:
                series = part.set_index("timestamp")["value"]
                part["aqi"] = compute_aqi(series, metric).to_numpy()
            parts.append(part)

        for metric in wanted:
            if metric is _CORRECTED_PM25:
                if "pm2.5_cf_1" in raw.columns and humidity is not None:
                    corrected = epa_correct_pm25(
                        raw["pm2.5_cf_1"].astype("float64"),
                        humidity.astype("float64"))
                    _emit(metric, corrected)
            else:
                field = _FIELD_MAP.get(metric)
                if field and field in raw.columns:
                    _emit(metric, raw[field])

        nonempty = [p for p in parts if not p.empty]
        return pd.concat(nonempty, ignore_index=True) if nonempty else empty_frame()

    def fetch(self, county_fips, start, end, metrics, cadence: int = 60):
        wanted = [m for m in metrics if m in self.supported_metrics]
        if not wanted:
            return
        average = self.resolve_cadence(cadence)
        bbox = bbox_for_county(county_fips)
        sensors = self._list_sensors(bbox)
        geometry = county_polygon(county_fips)
        sensors = self._filter_sensors(sensors, geometry, start, end)
        if not sensors:
            return
        fields = {_FIELD_MAP[m] for m in wanted if m in _FIELD_MAP}
        if _CORRECTED_PM25 in wanted:
            fields |= {"pm2.5_cf_1", "humidity"}  # humidity needed for the EPA correction
        field_list = sorted(fields)
        for sensor in sensors:
            rows, resp_fields = self._history_chunked(
                sensor["sensor_index"], start, end, average, field_list)
            chunk = self._parse_history(
                {"fields": resp_fields, "data": rows},
                sensor["sensor_index"], sensor["latitude"], sensor["longitude"],
                county_fips, wanted, average)
            if not chunk.empty:
                yield chunk
