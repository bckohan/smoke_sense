"""EPA NowCast and AQI breakpoint computation.

Pure functions over time-indexed concentration series. For PM, `nowcast` applies
EPA's 12-hour weighted NowCast. For ozone, EPA's AQI is based on the 8-hour
average concentration (not the PM-style weighted NowCast), so `nowcast` returns
the 8-hour trailing mean for O3. Breakpoints then map the resulting
concentration to the 0–500 AQI scale.
"""

from __future__ import annotations

import math

import pandas as pd

from .data import Pollutant

# Breakpoints: (C_low, C_high, I_low, I_high). Concentration truncated before use.
# PM tables in µg/m³ (EPA, 2024 revision); O3 in ppm (8-hour).
_BREAKPOINTS: dict[Pollutant, list[tuple[float, float, int, int]]] = {
    Pollutant.PM2_5: [
        (0.0, 9.0, 0, 50),
        (9.1, 35.4, 51, 100),
        (35.5, 55.4, 101, 150),
        (55.5, 125.4, 151, 200),
        (125.5, 225.4, 201, 300),
        (225.5, 325.4, 301, 500),
    ],
    Pollutant.PM10: [
        (0, 54, 0, 50),
        (55, 154, 51, 100),
        (155, 254, 101, 150),
        (255, 354, 151, 200),
        (355, 424, 201, 300),
        (425, 604, 301, 500),
    ],
    Pollutant.O3: [
        (0.000, 0.054, 0, 50),
        (0.055, 0.070, 51, 100),
        (0.071, 0.085, 101, 150),
        (0.086, 0.105, 151, 200),
        (0.106, 0.200, 201, 300),
    ],
}

# Truncation precision (decimal places) per pollutant before breakpoint lookup.
_TRUNC: dict[Pollutant, int] = {
    Pollutant.PM2_5: 1,
    Pollutant.PM10: 0,
    Pollutant.O3: 3,
}


def concentration_to_aqi(concentration: float, pollutant: Pollutant) -> int | None:
    """Convert a concentration to AQI via piecewise-linear breakpoints.

    Returns None for missing/negative values or values above the top breakpoint.
    """
    if concentration is None or math.isnan(concentration) or concentration < 0:
        return None

    factor = 10 ** _TRUNC[pollutant]
    conc = math.floor(concentration * factor) / factor

    for c_low, c_high, i_low, i_high in _BREAKPOINTS[pollutant]:
        if c_low <= conc <= c_high:
            aqi = (i_high - i_low) / (c_high - c_low) * (conc - c_low) + i_low
            return round(aqi)
    return None


def nowcast(series: pd.Series, pollutant: Pollutant) -> pd.Series:
    """Compute the NowCast concentration for each timestamp in `series`.

    `series` must be hourly and time-indexed (ascending). PM uses a 12-hour
    weighted window; O3 uses an 8-hour trailing mean.
    """
    series = series.sort_index()
    if pollutant is Pollutant.O3:
        return series.rolling(window=8, min_periods=6).mean()

    window = 12
    values = series.to_numpy(dtype="float64")
    out = []
    for end in range(len(values)):
        start = max(0, end - window + 1)
        # most-recent-first window
        recent = values[start : end + 1][::-1]
        valid = recent[~pd.isna(recent)]
        # require at least 2 of the most recent 3 hours present
        if pd.isna(recent[:3]).sum() > 1 or len(valid) == 0:
            out.append(float("nan"))
            continue
        c_min = float(min(valid))
        c_max = float(max(valid))
        weight = 1.0 if c_max == 0 else c_min / c_max
        weight = max(weight, 0.5)
        num = 0.0
        den = 0.0
        for i, c in enumerate(recent):
            if pd.isna(c):
                continue
            num += (weight ** i) * c
            den += weight ** i
        out.append(num / den if den else float("nan"))
    return pd.Series(out, index=series.index, dtype="float64")


def compute_aqi(series: pd.Series, pollutant: Pollutant) -> pd.Series:
    """Map an hourly concentration series to a nullable Int16 AQI series."""
    nc = nowcast(series, pollutant)
    aqi_values = [concentration_to_aqi(c, pollutant) for c in nc]
    return pd.Series(aqi_values, index=series.index, dtype="Int16")
