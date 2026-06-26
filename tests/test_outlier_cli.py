import pandas as pd
import pytest
import typer

from smoke_sense.bin import _outlier_cli as oc
from smoke_sense.data import Metric


def _df(rows):
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-06-16T01:00:00"] * len(rows), utc=True),
        "county_fips": ["06037"] * len(rows),
        "station_id": [r[0] for r in rows],
        "metric": pd.Categorical([r[1].value for r in rows]),
        "value": [r[2] for r in rows],
        "aqi": pd.array([pd.NA] * len(rows), dtype="Int16"),
        "agg_window": [10] * len(rows),
        "source": ["purpleair"] * len(rows),
    })


def test_parse_bound_ok():
    assert oc.parse_bound("PM2.5:0:500") == (Metric.PM2_5, (0.0, 500.0))


@pytest.mark.parametrize("spec", ["PM2.5:0", "NOPE:0:1", "PM2.5:a:b", "PM2.5:5:5"])
def test_parse_bound_bad(spec):
    with pytest.raises(ValueError):
        oc.parse_bound(spec)


def test_build_config_overrides():
    cfg = oc.build_config(no_range=True, zscore=None, iqr=None, bounds=[])
    assert cfg.range_enabled is False
    cfg2 = oc.build_config(no_range=False, zscore=2.0, iqr=3.0,
                           bounds=[(Metric.PM2_5, (0.0, 500.0))])
    assert cfg2.zscore == 2.0 and cfg2.iqr == 3.0
    assert cfg2.bounds[Metric.PM2_5] == (0.0, 500.0)
    # zscore <= 0 disables
    cfg3 = oc.build_config(no_range=False, zscore=0.0, iqr=None, bounds=[])
    assert cfg3.zscore is None


def test_filter_frame_disabled_passthrough():
    df = _df([("s1", Metric.PM2_5, -5.0)])
    out, report = oc.filter_frame(df, enabled=False, no_range=False, zscore=None,
                                  iqr_on=False, iqr_k=3.0, bound=None)
    assert len(out) == 1 and report.total == 0


def test_filter_frame_enabled_drops_and_reports():
    df = _df([("s1", Metric.PM2_5, 10.0), ("s1", Metric.PM2_5, -5.0)])
    out, report = oc.filter_frame(df, enabled=True, no_range=False, zscore=None,
                                  iqr_on=False, iqr_k=3.0, bound=None)
    assert len(out) == 1 and report.total == 1
    assert report.per_metric == {"PM2.5": 1}


def test_filter_frame_bad_bound_raises_badparameter():
    df = _df([("s1", Metric.PM2_5, 10.0)])
    with pytest.raises(typer.BadParameter):
        oc.filter_frame(df, enabled=True, no_range=False, zscore=None,
                        iqr_on=False, iqr_k=3.0, bound=["PM2.5:bad"])


def test_filter_frame_bound_override_drops():
    df = _df([("s1", Metric.PM2_5, 10.0), ("s1", Metric.PM2_5, 100.0)])
    out, report = oc.filter_frame(df, enabled=True, no_range=False, zscore=None,
                                  iqr_on=False, iqr_k=3.0, bound=["PM2.5:0:50"])
    assert len(out) == 1 and report.total == 1
    assert out["value"].tolist() == [10.0]


def test_build_config_exclude_stations():
    cfg = oc.build_config(no_range=False, zscore=None, iqr=None, bounds=[],
                          exclude_stations=["s1", "s2"])
    assert cfg.exclude_stations == frozenset({"s1", "s2"})


def test_build_config_exclude_default_empty():
    cfg = oc.build_config(no_range=False, zscore=None, iqr=None, bounds=[])
    assert cfg.exclude_stations == frozenset()


def test_filter_frame_excludes_stations():
    df = _df([
        ("s1", Metric.PM2_5, 10.0),
        ("s2", Metric.PM2_5, 11.0),
    ])
    clean, report = oc.filter_frame(
        df, enabled=True, no_range=False, zscore=None, iqr_on=False,
        iqr_k=3.0, bound=None, exclude=["s2"])
    assert set(clean["station_id"]) == {"s1"}
    assert report.total == 1


def test_make_filter_returns_callable():
    df = _df([("s1", Metric.PM2_5, 10.0), ("s1", Metric.PM2_5, -5.0)])
    f = oc.make_filter(enabled=True, no_range=False, zscore=None,
                       iqr_on=False, iqr_k=3.0, bound=None)
    clean = f(df)
    assert len(clean) == 1
