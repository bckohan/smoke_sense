import pandas as pd

from smoke_sense import outliers
from smoke_sense.data import Metric


def _df(rows):
    """rows: list of (station_id, metric, value)."""
    return pd.DataFrame({
        "timestamp": pd.to_datetime(
            ["2026-06-16T01:00:00"] * len(rows), utc=True),
        "county_fips": ["06037"] * len(rows),
        "station_id": [r[0] for r in rows],
        "metric": pd.Categorical([r[1].value for r in rows]),
        "value": [r[2] for r in rows],
        "aqi": pd.array([pd.NA] * len(rows), dtype="Int16"),
        "agg_window": [10] * len(rows),
        "source": ["purpleair"] * len(rows),
    })


def test_range_mask_flags_out_of_bounds():
    df = _df([
        ("s1", Metric.PM2_5, 10.0),      # ok
        ("s1", Metric.PM2_5, -5.0),      # negative -> outlier
        ("s1", Metric.PM2_5, 5000.0),    # above 1000 -> outlier
        ("s1", Metric.RH, 50.0),         # ok
        ("s1", Metric.RH, 150.0),        # above 100 -> outlier
    ])
    mask = outliers.range_mask(df, outliers.DEFAULT_BOUNDS)
    assert mask.tolist() == [False, True, True, False, True]


def test_range_mask_leaves_unconfigured_metric():
    df = _df([("s1", Metric.PM2_5, 10.0)])
    mask = outliers.range_mask(df, {})  # no bounds configured
    assert mask.tolist() == [False]


def test_zscore_mask_flags_per_station_spike():
    # s1 has a clear spike; all within range bounds.
    rows = [("s1", Metric.PM2_5, v) for v in [10, 11, 9, 10, 12, 900]]
    df = _df(rows)
    mask = outliers.zscore_mask(df, threshold=3.0, min_group=5)
    assert mask.tolist() == [False, False, False, False, False, True]


def test_zscore_mask_ignores_across_station_spread():
    # Two stations each internally consistent but very different levels.
    rows = ([("s1", Metric.PM2_5, v) for v in [10, 11, 9, 10, 12]]
            + [("s2", Metric.PM2_5, v) for v in [500, 510, 505, 495, 500]])
    df = _df(rows)
    mask = outliers.zscore_mask(df, threshold=3.0, min_group=5)
    assert not mask.any()


def test_zscore_mask_skips_small_group():
    rows = [("s1", Metric.PM2_5, v) for v in [10, 11, 900]]  # only 3 points
    df = _df(rows)
    mask = outliers.zscore_mask(df, threshold=3.0, min_group=5)
    assert not mask.any()


def test_iqr_mask_flags_extreme():
    rows = [("s1", Metric.PM2_5, v) for v in [10, 11, 9, 10, 12, 900]]
    df = _df(rows)
    mask = outliers.iqr_mask(df, k=3.0, min_group=5)
    assert bool(mask.iloc[-1])  # the 900 is flagged
    assert mask.sum() == 1


def test_iqr_mask_skips_small_group():
    rows = [("s1", Metric.PM2_5, v) for v in [10, 11, 9, 10, 12, 900]]
    df = _df(rows)
    # min_group larger than the group size -> nothing flagged
    mask = outliers.iqr_mask(df, k=3.0, min_group=10)
    assert not mask.any()


def test_filter_outliers_union_and_report():
    rows = [("s1", Metric.PM2_5, v) for v in [10, 11, 9, 10, 12]] + [
        ("s1", Metric.PM2_5, -1.0),    # range
        ("s1", Metric.PM2_5, 900.0),   # zscore (and range? 900<1000 so not range)
    ]
    df = _df(rows)
    cfg = outliers.OutlierConfig(zscore=3.0, min_group=5)
    clean, report = outliers.filter_outliers(df, cfg)
    assert len(clean) == 5
    assert report.total == 2
    assert report.per_metric == {"PM2.5": 2}
    assert report.per_check["range"] == 1
    assert report.per_check["zscore"] == 1


def test_filter_outliers_empty():
    df = _df([]).iloc[0:0]
    clean, report = outliers.filter_outliers(df)
    assert clean.empty
    assert report.total == 0 and report.per_metric == {}
