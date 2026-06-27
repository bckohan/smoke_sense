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


def test_station_mask_flags_excluded():
    df = _df([
        ("s1", Metric.PM2_5, 10.0),
        ("s2", Metric.PM2_5, 11.0),
        ("s3", Metric.PM2_5, 12.0),
    ])
    mask = outliers.station_mask(df, frozenset({"s2", "s3"}))
    assert mask.tolist() == [False, True, True]


def test_station_mask_empty_set():
    df = _df([("s1", Metric.PM2_5, 10.0)])
    assert outliers.station_mask(df, frozenset()).tolist() == [False]


def test_station_mask_empty_frame():
    df = _df([]).iloc[0:0]
    mask = outliers.station_mask(df, frozenset({"s1"}))
    assert mask.tolist() == []


def test_filter_outliers_excludes_station():
    df = _df([
        ("s1", Metric.PM2_5, 10.0),
        ("s1", Metric.PM2_5, 11.0),
        ("s2", Metric.PM2_5, 12.0),   # excluded
    ])
    cfg = outliers.OutlierConfig(exclude_stations=frozenset({"s2"}))
    clean, report = outliers.filter_outliers(df, cfg)
    assert set(clean["station_id"]) == {"s1"}
    assert report.per_check["station"] == 1
    assert report.per_metric["PM2.5"] == 1
    assert report.total == 1


def test_filter_outliers_station_counts_once_for_range_outlier():
    # s2's row is BOTH excluded and out-of-range; it must count once, under station.
    df = _df([
        ("s1", Metric.PM2_5, 10.0),
        ("s2", Metric.PM2_5, 5000.0),   # excluded AND > 1000 bound
    ])
    cfg = outliers.OutlierConfig(exclude_stations=frozenset({"s2"}))
    clean, report = outliers.filter_outliers(df, cfg)
    assert set(clean["station_id"]) == {"s1"}
    assert report.total == 1
    assert report.per_check["station"] == 1
    assert report.per_check.get("range", 0) == 0


def test_station_outlier_counts_ranks_by_fraction():
    df = _df([
        ("s1", Metric.PM2_5, 10.0),
        ("s1", Metric.PM2_5, 5000.0),   # range outlier (> 1000)
        ("s2", Metric.PM2_5, 10.0),
        ("s2", Metric.PM2_5, 11.0),
        ("s2", Metric.PM2_5, 5000.0),   # range outlier
    ])
    out = outliers.station_outlier_counts(df)
    assert out["station_id"].tolist() == ["s1", "s2"]   # 0.5 > 0.333
    assert out["readings"].tolist() == [2, 3]
    assert out["flagged"].tolist() == [1, 1]
    assert out["fraction"].iloc[0] == 0.5


def test_station_outlier_counts_only_flagged_stations():
    df = _df([("s1", Metric.PM2_5, 10.0), ("s2", Metric.PM2_5, 5000.0)])
    out = outliers.station_outlier_counts(df)
    assert out["station_id"].tolist() == ["s2"]


def test_station_outlier_counts_empty():
    out = outliers.station_outlier_counts(_df([]).iloc[0:0])
    assert out.empty
    assert list(out.columns) == ["station_id", "readings", "flagged", "fraction"]
