import pandas as pd
import pytest

from smoke_sense import data
from smoke_sense.data import Pollutant


def test_pollutant_codes_and_units():
    assert Pollutant.PM2_5.aqs_code == "88101"
    assert Pollutant.PM10.aqs_code == "81102"
    assert Pollutant.O3.aqs_code == "44201"
    assert Pollutant.PM2_5.unit == "µg/m³"
    assert Pollutant.O3.unit == "ppm"


def test_pollutant_from_str_accepts_variants():
    assert Pollutant.from_str("PM2.5") is Pollutant.PM2_5
    assert Pollutant.from_str("pm2.5") is Pollutant.PM2_5
    assert Pollutant.from_str("O3") is Pollutant.O3
    with pytest.raises(ValueError):
        Pollutant.from_str("CO2")


def test_validate_coerces_dtypes(sample_rows):
    out = data.validate(sample_rows)
    assert str(out["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert out["county_fips"].dtype == "string"
    assert out["pollutant"].dtype == "category"
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


def test_parquet_round_trip(tmp_path, sample_rows):
    df = data.validate(sample_rows)
    path = tmp_path / "out.parquet"
    data.write_parquet(df, path)
    back = data.read_parquet(path)
    assert str(back["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert back["value"].tolist() == df["value"].tolist()
    assert back["county_fips"].tolist() == df["county_fips"].tolist()
