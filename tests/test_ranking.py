import pandas as pd

from smoke_sense import ranking


def _obs(rows):
    """rows: list of (station_id, value, aqi)."""
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-06-16T01:00:00"] * len(rows), utc=True),
        "station_id": [r[0] for r in rows],
        "value": [r[1] for r in rows],
        "aqi": pd.array([r[2] for r in rows], dtype="Int16"),
    })


def test_rank_mean_desc_default():
    obs = _obs([
        ("s1", 10.0, 1), ("s1", 20.0, 3),   # mean 15
        ("s2", 30.0, 5), ("s2", 50.0, 7),   # mean 40
        ("s3", 5.0, 1),                      # mean 5
    ])
    out = ranking.rank_stations(obs, column="value", agg="mean")
    assert out["station_id"].tolist() == ["s2", "s1", "s3"]
    assert out["value"].tolist() == [40.0, 15.0, 5.0]
    assert out["count"].tolist() == [2, 2, 1]


def test_rank_ascending():
    obs = _obs([("s1", 10.0, 1), ("s2", 30.0, 5), ("s3", 5.0, 1)])
    out = ranking.rank_stations(obs, column="value", agg="mean", descending=False)
    assert out["station_id"].tolist() == ["s3", "s1", "s2"]


def test_rank_min_and_max():
    obs = _obs([("s1", 10.0, 1), ("s1", 20.0, 3), ("s2", 30.0, 5)])
    mins = ranking.rank_stations(obs, column="value", agg="min", descending=False)
    assert mins["value"].tolist() == [10.0, 30.0]
    maxs = ranking.rank_stations(obs, column="value", agg="max")
    assert maxs["value"].tolist() == [30.0, 20.0]


def test_rank_ties_stable_by_station_id():
    obs = _obs([("s3", 10.0, 1), ("s1", 10.0, 1), ("s2", 10.0, 1)])
    out = ranking.rank_stations(obs, column="value", agg="mean")
    # equal means -> stations in ascending station_id order
    assert out["station_id"].tolist() == ["s1", "s2", "s3"]


def test_rank_limit_truncates():
    obs = _obs([("s1", 10.0, 1), ("s2", 30.0, 5), ("s3", 5.0, 1)])
    out = ranking.rank_stations(obs, column="value", agg="mean", limit=2)
    assert out["station_id"].tolist() == ["s2", "s1"]


def test_rank_limit_zero_returns_all():
    obs = _obs([("s1", 10.0, 1), ("s2", 30.0, 5), ("s3", 5.0, 1)])
    out = ranking.rank_stations(obs, column="value", agg="mean", limit=0)
    assert len(out) == 3


def test_rank_drops_nulls_and_counts():
    obs = _obs([("s1", 10.0, 1), ("s1", None, 3), ("s2", 30.0, 5)])
    out = ranking.rank_stations(obs, column="value", agg="mean")
    s1 = out[out["station_id"] == "s1"].iloc[0]
    assert s1["count"] == 1            # the null value row excluded
    assert s1["value"] == 10.0


def test_rank_by_aqi_column():
    obs = _obs([("s1", 10.0, 20), ("s2", 30.0, 80)])
    out = ranking.rank_stations(obs, column="aqi", agg="max")
    assert out["station_id"].tolist() == ["s2", "s1"]
    assert out["value"].tolist() == [80.0, 20.0]


def test_rank_empty():
    obs = _obs([("s1", 10.0, 1)]).iloc[0:0]
    out = ranking.rank_stations(obs, column="value", agg="mean")
    assert list(out.columns) == ["station_id", "value", "count"]
    assert out.empty


def test_rank_all_null_column():
    obs = _obs([("s1", None, 1), ("s2", None, 5)])
    out = ranking.rank_stations(obs, column="value", agg="mean")
    assert out.empty
    assert list(out.columns) == ["station_id", "value", "count"]
