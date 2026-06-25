import pandas as pd
import pytest

from smoke_sense import data


def test_validate_coerces_dtypes(sample_rows):
    out = data.validate(sample_rows)
    assert str(out["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert out["county_fips"].dtype == "string"
    assert out["metric"].dtype == "category"
    assert str(out["aqi"].dtype) == "Int16"


def test_validate_missing_column_raises(sample_rows):
    with pytest.raises(ValueError, match="missing columns"):
        data.validate(sample_rows.drop(columns=["value"]))


def test_validate_null_required_raises(sample_rows):
    bad = sample_rows.copy()
    bad.loc[0, "value"] = None
    with pytest.raises(ValueError, match="null values"):
        data.validate(bad)


def test_empty_frame_validates():
    out = data.validate(data.empty_frame())
    assert list(out.columns) == list(data.COLUMNS)
    assert len(out) == 0


def test_validate_includes_agg_window(sample_rows):
    out = data.validate(sample_rows)
    assert "agg_window" in out.columns
    assert str(out["agg_window"].dtype) == "Int16"


def test_parquet_round_trip(tmp_path, sample_rows):
    df = data.validate(sample_rows)
    path = tmp_path / "out.parquet"
    data.write_parquet(df, path)
    back = data.read_parquet(path)
    assert str(back["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert back["value"].tolist() == df["value"].tolist()
    assert back["county_fips"].tolist() == df["county_fips"].tolist()
