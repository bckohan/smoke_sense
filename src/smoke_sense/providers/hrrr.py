"""HRRR wind provider: near-surface wind from NOAA's High-Resolution Rapid
Refresh model, sampled per grid cell within a county.

HRRR is a gridded forecast model (GRIB2 on AWS open data). We read the F00
analysis from each hourly cycle, keep grid cells whose centroid is inside the
county polygon, and emit each cell as a synthetic station with 10 m / 80 m wind
speed and direction. The GRIB read is isolated behind an injectable
``field_source`` so the provider logic is unit-testable without network/ecCodes.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd

from ..data import Metric
from ..geo import bbox_for_county, county_polygon, point_in_polygon
from .base import AQIProvider, register

logger = logging.getLogger(__name__)

# metric -> (height in metres, "speed"|"dir")
_METRIC_SPEC: dict[Metric, tuple[int, str]] = {
    Metric.WIND_SPEED: (10, "speed"),
    Metric.WIND_DIR: (10, "dir"),
    Metric.WIND_SPEED_80M: (80, "speed"),
    Metric.WIND_DIR_80M: (80, "dir"),
}


class FieldUnavailable(Exception):
    """Raised when an HRRR cycle's wind field cannot be read (e.g. not posted)."""


@dataclass(frozen=True)
class FieldSample:
    """Flattened per-cell wind field for one cycle, cropped to a county bbox.

    ``latitude``/``longitude`` are equal-length 1-D sequences; ``u``/``v`` map a
    height (metres) to a 1-D component sequence aligned with lat/lon.
    """

    latitude: object
    longitude: object
    u: dict
    v: dict


def wind_speed(u: float, v: float) -> float:
    """Wind speed magnitude from u/v components (m/s)."""
    return math.sqrt(u * u + v * v)


def wind_direction(u: float, v: float) -> float:
    """Meteorological wind direction in degrees (direction the wind blows FROM)."""
    return (270.0 - math.degrees(math.atan2(v, u))) % 360.0


def station_id(lat: float, lon: float) -> str:
    """Stable per-cell station id (the HRRR grid never moves)."""
    return f"hrrr-{lat:.4f}_{lon:.4f}"


def cells_in_polygon(latitudes, longitudes, geometry) -> list[tuple[int, float, float]]:
    """(index, lat, lon) for cells whose centroid is inside `geometry`."""
    out: list[tuple[int, float, float]] = []
    for i, (lat, lon) in enumerate(zip(latitudes, longitudes)):
        if point_in_polygon(float(lon), float(lat), geometry):
            out.append((i, float(lat), float(lon)))
    return out


def _hourly_cycles(start: date, end: date) -> Iterator[datetime]:
    cur = datetime.combine(start, time(0), tzinfo=timezone.utc)
    last = datetime.combine(end, time(23), tzinfo=timezone.utc)
    while cur <= last:
        yield cur
        cur += timedelta(hours=1)


@register
class HRRRProvider(AQIProvider):
    """Near-surface wind from HRRR, one synthetic station per in-county grid cell."""

    name = "hrrr"
    supported_metrics = set(_METRIC_SPEC)
    supported_cadences = [60]

    def __init__(self, field_source=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._source = field_source if field_source is not None else HerbieFieldSource()

    def fetch(self, county_fips, start, end, metrics, cadence: int = 60):
        wanted = [m for m in metrics if m in self.supported_metrics]
        if not wanted:
            return
        heights = sorted({_METRIC_SPEC[m][0] for m in wanted})
        bbox = bbox_for_county(county_fips)
        geometry = county_polygon(county_fips)
        for cycle in _hourly_cycles(start, end):
            try:
                sample = self._source.read(cycle, bbox, heights)
            except FieldUnavailable as exc:
                logger.info("HRRR cycle %s unavailable: %s", cycle, exc)
                continue
            cells = cells_in_polygon(sample.latitude, sample.longitude, geometry)
            if not cells:
                continue
            rows: list[dict] = []
            for idx, lat, lon in cells:
                sid = station_id(lat, lon)
                for m in wanted:
                    height, kind = _METRIC_SPEC[m]
                    u = float(sample.u[height][idx])
                    v = float(sample.v[height][idx])
                    value = wind_speed(u, v) if kind == "speed" else wind_direction(u, v)
                    rows.append({
                        "timestamp": cycle,
                        "county_fips": county_fips,
                        "station_id": sid,
                        "latitude": lat,
                        "longitude": lon,
                        "metric": m.value,
                        "value": value,
                        "aqi": pd.NA,
                        "agg_window": 60,
                        "source": "hrrr",
                    })
            if rows:
                chunk = pd.DataFrame(rows)
                chunk["timestamp"] = pd.to_datetime(chunk["timestamp"], utc=True)
                chunk["aqi"] = chunk["aqi"].astype("Int16")
                yield chunk


class HerbieFieldSource:
    """Reads HRRR 10 m/80 m wind via Herbie, byte-range-subset from AWS.

    `herbie` is imported lazily so this module imports without the GRIB stack.
    """

    def read(self, cycle: datetime, bbox, heights) -> FieldSample:
        try:
            from herbie import Herbie
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise FieldUnavailable("herbie-data not installed") from exc
        levels = "|".join(f"{h} m above ground" for h in heights)
        search = rf":(UGRD|VGRD):({levels})"
        # Herbie expects a tz-naive UTC timestamp; our cycles are tz-aware.
        run_time = cycle.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            run = Herbie(run_time, model="hrrr", product="sfc", fxx=0)
            ds = run.xarray(search)
        except Exception as exc:  # pragma: no cover - network/file dependent
            raise FieldUnavailable(str(exc)) from exc
        return _sample_from_xarray(ds, bbox, heights)


def _sample_from_xarray(ds, bbox, heights) -> FieldSample:  # pragma: no cover
    """Crop xarray HRRR wind to `bbox` and flatten to a FieldSample.

    cfgrib returns one dataset per height level (a list when levels differ).
    Refined against real data in the integration task.
    """
    import numpy as np

    datasets = ds if isinstance(ds, list) else [ds]
    lat = lon = None
    u: dict[int, object] = {}
    v: dict[int, object] = {}
    for d in datasets:
        latv = np.asarray(d["latitude"].values)
        lonv = np.asarray(d["longitude"].values)
        lonv = np.where(lonv > 180.0, lonv - 360.0, lonv)  # 0..360 -> -180..180
        mask = ((latv >= bbox.min_lat) & (latv <= bbox.max_lat)
                & (lonv >= bbox.min_lon) & (lonv <= bbox.max_lon))
        height = int(round(float(np.asarray(d["heightAboveGround"].values))))
        uname = "u10" if "u10" in d else "u"
        vname = "v10" if "v10" in d else "v"
        uv = np.asarray(d[uname].values)[mask]
        vv = np.asarray(d[vname].values)[mask]
        if lat is None:
            lat, lon = latv[mask], lonv[mask]
        u[height] = uv
        v[height] = vv
    return FieldSample(latitude=lat, longitude=lon, u=u, v=v)
