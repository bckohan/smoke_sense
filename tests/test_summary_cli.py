import json

import pandas as pd
from typer.testing import CliRunner

from smoke_sense import data, store
from smoke_sense.bin import app
from smoke_sense.data import Pollutant

runner = CliRunner()


def _write_day(tmp_path):
    rows = [{
        "timestamp": pd.Timestamp("2026-06-01T01:00:00", tz="UTC"),
        "county_fips": "06037", "station_id": "s1",
        "latitude": 34.0, "longitude": -118.2,
        "pollutant": Pollutant.PM2_5.value, "value": 12.0, "unit": "µg/m³",
        "aqi": 52, "agg_window": 10, "source": "purpleair",
    }]
    store.write(tmp_path, "06037", data.validate(pd.DataFrame(rows)))


def test_summary_json_output(tmp_path):
    _write_day(tmp_path)
    result = runner.invoke(
        app,
        ["summary", "06037", "--start", "2026-06-01", "--end", "2026-06-01",
         "--output", str(tmp_path), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "06037" in payload
    assert payload["06037"]["coverage"]["total_rows"] == 1
    assert payload["06037"]["pollutants"][0]["pollutant"] == "PM2.5"


def test_summary_tables_no_data(tmp_path):
    result = runner.invoke(
        app,
        ["summary", "06037", "--start", "2026-06-01", "--output", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "no data" in result.output


def test_summary_invalid_fips(tmp_path):
    result = runner.invoke(
        app,
        ["summary", "6037", "--start", "2026-06-01", "--output", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "5-digit" in result.output
