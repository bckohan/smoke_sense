import math

import pandas as pd
import pytest

from smoke_sense import aqi
from smoke_sense.data import Metric


def _hourly(values, start="2023-07-01T00:00:00Z"):
    idx = pd.date_range(start, periods=len(values), freq="h", tz="UTC")
    return pd.Series(values, index=idx, dtype="float64")


def test_nowcast_constant_series_returns_constant():
    series = _hourly([20.0] * 12)
    result = aqi.nowcast(series, Metric.PM2_5)
    assert result.iloc[-1] == pytest.approx(20.0, abs=1e-6)


def test_nowcast_weighted_recent_spike():
    # most-recent-LAST in time index: 11 zeros then a 12.0 spike.
    # min=0, max=12 -> weight w*=0 -> clamped to 0.5.
    # NowCast = sum(0.5^i * c_i)/sum(0.5^i), i=0 is most recent (the 12.0).
    # = 12 / (sum_{i=0}^{11} 0.5^i) = 12 / 1.99951... ≈ 6.0
    series = _hourly([0.0] * 11 + [12.0])
    result = aqi.nowcast(series, Metric.PM2_5)
    assert result.iloc[-1] == pytest.approx(6.0, abs=0.05)


def test_concentration_to_aqi_endpoints_pm25():
    assert aqi.concentration_to_aqi(9.0, Metric.PM2_5) == 50
    assert aqi.concentration_to_aqi(35.4, Metric.PM2_5) == 100


def test_concentration_to_aqi_interpolated_pm25():
    # bin 9.1–35.4 -> 51–100. conc truncated to 27.2.
    # AQI = (100-51)/(35.4-9.1)*(27.2-9.1)+51
    #     = 49/26.3*18.1 + 51 = 33.72 + 51 = 84.72 -> round 85
    assert aqi.concentration_to_aqi(27.25, Metric.PM2_5) == 85


def test_concentration_to_aqi_o3_endpoint():
    assert aqi.concentration_to_aqi(0.054, Metric.O3) == 50


def test_concentration_to_aqi_none_for_negative():
    assert aqi.concentration_to_aqi(-1.0, Metric.PM2_5) is None


def test_compute_aqi_series_dtype_and_values():
    series = _hourly([9.0] * 12)
    out = aqi.compute_aqi(series, Metric.PM2_5)
    assert str(out.dtype) == "Int16"
    assert out.iloc[-1] == 50
