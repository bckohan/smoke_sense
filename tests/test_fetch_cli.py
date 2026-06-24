from datetime import date

import pandas as pd
from typer.testing import CliRunner

from smoke_sense import data
from smoke_sense.bin import app
from smoke_sense.data import Pollutant

runner = CliRunner()


def _fake_frame(county_fips: str) -> pd.DataFrame:
    df = data.empty_frame()
    return pd.concat(
        [
            df,
            pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(["2023-07-01T00:00:00Z"], utc=True),
                    "county_fips": [county_fips],
                    "station_id": ["s1"],
                    "latitude": [34.0],
                    "longitude": [-118.2],
                    "pollutant": [Pollutant.PM2_5.value],
                    "value": [9.0],
                    "unit": ["µg/m³"],
                    "aqi": [50],
                    "source": ["aqs"],
                }
            ),
        ],
        ignore_index=True,
    )


def test_invalid_fips_exits_nonzero(tmp_path):
    result = runner.invoke(
        app,
        ["fetch", "6037", "--start", "2023-07-01", "--end", "2023-07-02",
         "--output", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "5-digit" in result.output


def test_fetch_writes_parquet(tmp_path, monkeypatch):
    from smoke_sense.bin import fetch as fetch_mod

    class FakeProvider:
        def fetch(self, county_fips, start, end, pollutants):
            return _fake_frame(county_fips)

    monkeypatch.setattr(
        fetch_mod, "_resolve_providers", lambda sources, creds: [FakeProvider()]
    )

    result = runner.invoke(
        app,
        ["fetch", "06037", "--start", "2023-07-01", "--end", "2023-07-02",
         "--source", "aqs", "--output", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    out = tmp_path / "06037_2023-07-01_2023-07-02.parquet"
    assert out.exists()
    back = data.read_parquet(out)
    assert back["county_fips"].iloc[0] == "06037"
