from datetime import date

import pandas as pd
import pytest

from smoke_sense import data, fetcher, store
from smoke_sense.data import Pollutant


def _frame(county_fips, day, source="purpleair", agg=10):
    return data.validate(pd.DataFrame([{
        "timestamp": pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=1),
        "county_fips": county_fips, "station_id": "s1",
        "latitude": 34.0, "longitude": -118.2,
        "pollutant": Pollutant.PM2_5.value, "value": 1.0, "unit": "µg/m³",
        "aqi": 5, "agg_window": agg, "source": source,
    }]))


class FakeProvider:
    name = "purpleair"
    supported_cadences = [0, 10, 30, 60]

    def __init__(self):
        self.requested_ranges = []

    def resolve_cadence(self, requested):
        cands = [c for c in self.supported_cadences if c <= requested]
        return max(cands) if cands else min(self.supported_cadences)

    def fetch(self, county_fips, start, end, pollutants, cadence):
        self.requested_ranges.append((start, end))
        day = start
        while day <= end:
            yield _frame(county_fips, day, agg=cadence)
            day += pd.Timedelta(days=1).to_pytimedelta()


def test_hybrid_skips_covered_and_fetches_gaps(tmp_path):
    store.write(tmp_path, "06037", _frame("06037", date(2026, 6, 16)))
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 18),
                         [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 20))
    fetched_days = {d for (s, e) in p.requested_ranges
                    for d in pd.date_range(s, e, freq="D").date}
    assert date(2026, 6, 16) not in fetched_days
    assert date(2026, 6, 17) in fetched_days
    assert date(2026, 6, 18) in fetched_days


def test_always_refetches_today(tmp_path):
    store.write(tmp_path, "06037", _frame("06037", date(2026, 6, 20)))
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 20), date(2026, 6, 20),
                         [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 20))
    assert p.requested_ranges


def test_refetch_fetches_everything(tmp_path):
    store.write(tmp_path, "06037", _frame("06037", date(2026, 6, 16)))
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 17),
                         [Pollutant.PM2_5], 10, [p],
                         today=date(2026, 6, 30), refetch=True)
    fetched_days = {d for (s, e) in p.requested_ranges
                    for d in pd.date_range(s, e, freq="D").date}
    assert date(2026, 6, 16) in fetched_days


def test_writes_once_on_success(tmp_path, monkeypatch):
    calls = {"n": 0}
    real_write = store.write
    monkeypatch.setattr(
        store, "write",
        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), real_write(*a, **k))[1],
    )
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 17),
                         [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 30))
    assert calls["n"] == 1  # single flush write for the county


class _YieldThenFail:
    name = "purpleair"
    supported_cadences = [10]

    def __init__(self, exc):
        self._exc = exc

    def resolve_cadence(self, requested):
        return 10

    def fetch(self, county_fips, start, end, pollutants, cadence):
        yield _frame(county_fips, date(2026, 6, 16))
        yield _frame(county_fips, date(2026, 6, 17))
        raise self._exc


def test_flush_on_exception(tmp_path):
    p = _YieldThenFail(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 17),
                             [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 30))
    cov = store.coverage(tmp_path, "06037")
    assert (date(2026, 6, 16), "purpleair") in cov
    assert (date(2026, 6, 17), "purpleair") in cov


def test_flush_on_keyboard_interrupt(tmp_path):
    p = _YieldThenFail(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 17),
                             [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 30))
    cov = store.coverage(tmp_path, "06037")
    assert (date(2026, 6, 16), "purpleair") in cov
    assert (date(2026, 6, 17), "purpleair") in cov
