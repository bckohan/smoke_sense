import logging
from datetime import date

import pandas as pd
from typer.testing import CliRunner

from smoke_sense import credentials, data
from smoke_sense.bin import app
from smoke_sense.data import Metric

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
                    "metric": [Metric.PM2_5.value],
                    "value": [9.0],
                    "aqi": [50],
                    "agg_window": [60],
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


def test_fetch_writes_day_files(tmp_path, monkeypatch):
    from smoke_sense.bin import fetch as fetch_mod

    class FakeProvider:
        name = "aqs"
        supported_cadences = [60]

        def resolve_cadence(self, requested):
            return 60

        def fetch(self, county_fips, start, end, metrics, cadence):
            yield _fake_frame(county_fips)

    monkeypatch.setattr(
        fetch_mod, "_resolve_providers", lambda sources, creds: [FakeProvider()]
    )
    result = runner.invoke(
        app,
        ["fetch", "06037", "--start", "2023-07-01", "--end", "2023-07-02",
         "--source", "aqs", "--credentials", str(tmp_path / "absent.json"),
         "--output", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    out = tmp_path / "06037" / "2023-07-01.parquet"
    assert out.exists()
    back = data.read_parquet(out)
    assert back["county_fips"].iloc[0] == "06037"


def test_cadence_option_accepted(tmp_path, monkeypatch):
    from smoke_sense.bin import fetch as fetch_mod

    seen = {}

    class FakeProvider:
        name = "aqs"
        supported_cadences = [60]

        def resolve_cadence(self, requested):
            seen["requested"] = requested
            return 60

        def fetch(self, county_fips, start, end, metrics, cadence):
            yield _fake_frame(county_fips)

    monkeypatch.setattr(
        fetch_mod, "_resolve_providers", lambda sources, creds: [FakeProvider()]
    )
    result = runner.invoke(
        app,
        ["fetch", "06037", "--start", "2023-07-01", "--end", "2023-07-01",
         "--cadence", "THIRTY_MIN", "--credentials", str(tmp_path / "absent.json"),
         "--output", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert seen["requested"] == 30


def test_metric_default_all_and_override(tmp_path, monkeypatch):
    from smoke_sense.bin import fetch as fetch_mod
    seen = {}

    class FakeProvider:
        name = "aqs"
        supported_metrics = {Metric.PM2_5, Metric.TEMP}
        def resolve_cadence(self, r): return 60
        def fetch(self, county_fips, start, end, metrics, cadence):
            seen["metrics"] = list(metrics)
            yield _fake_frame(county_fips)

    monkeypatch.setattr(fetch_mod, "_resolve_providers",
                        lambda sources, creds: [FakeProvider()])
    runner.invoke(app, ["fetch", "06037", "--start", "2023-07-01", "--end", "2023-07-01",
                        "--credentials", str(tmp_path / "absent.json"),
                        "--output", str(tmp_path)])
    assert set(seen["metrics"]) == set(Metric)  # default = all

    runner.invoke(app, ["fetch", "06037", "--start", "2023-07-01", "--end", "2023-07-01",
                        "--metric", "PM2.5", "--refetch",
                        "--credentials", str(tmp_path / "absent.json"),
                        "--output", str(tmp_path)])
    assert seen["metrics"] == [Metric.PM2_5]


def test_invalid_metric_exits_nonzero(tmp_path):
    result = runner.invoke(
        app,
        ["fetch", "06037", "--start", "2023-07-01", "--metric", "nope",
         "--credentials", str(tmp_path / "absent.json"), "--output", str(tmp_path)],
    )
    assert result.exit_code != 0


def test_wrong_password_surfaces_clean_error(tmp_path, monkeypatch):
    cred_path = tmp_path / "credentials.json"
    credentials.save_file(cred_path, {"aqs_api_key": "K"}, "right")
    monkeypatch.setenv("SMOKESENSE_CREDENTIAL_KEY", "wrong")
    result = runner.invoke(
        app,
        ["fetch", "06037", "--start", "2023-07-01",
         "--credentials", str(cred_path), "--output", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "could not decrypt" in result.output


def test_configure_logging_toggles_handler():
    from smoke_sense.bin import fetch as fetch_mod

    pkg = logging.getLogger("smoke_sense")
    before = list(pkg.handlers)
    try:
        fetch_mod._configure_logging(False)
        assert list(pkg.handlers) == before
        fetch_mod._configure_logging(True)
        assert len(pkg.handlers) == len(before) + 1
        assert pkg.level == logging.INFO
    finally:
        pkg.handlers = before
        pkg.setLevel(logging.WARNING)
