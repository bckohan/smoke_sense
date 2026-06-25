import pytest

from smoke_sense.data import AQI_METRICS, Metric


def test_properties():
    assert Metric.PM2_5.unit == "µg/m³"
    assert Metric.PM2_5.has_aqi is True
    assert Metric.O3.unit == "ppm"
    assert Metric.TEMP.unit == "°C"
    assert Metric.TEMP.has_aqi is False


def test_symmetric_case_insensitive_lookup():
    assert Metric("PM2.5") is Metric.PM2_5
    assert Metric("pm2.5") is Metric.PM2_5
    assert Metric("O3") is Metric.O3
    with pytest.raises(ValueError):
        Metric("nope")


def test_aqi_metrics_are_has_aqi_members():
    assert AQI_METRICS == {m for m in Metric if m.has_aqi}
    assert AQI_METRICS == {Metric.PM2_5, Metric.PM10, Metric.O3}
