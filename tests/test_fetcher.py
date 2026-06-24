from datetime import date

import pandas as pd

from smoke_sense import data, fetcher, store
from smoke_sense.data import Pollutant


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
        days = pd.date_range(start, end, freq="D", tz="UTC")
        rows = [{
            "timestamp": d + pd.Timedelta(hours=1),
            "county_fips": county_fips,
            "station_id": "s1",
            "latitude": 34.0, "longitude": -118.2,
            "pollutant": Pollutant.PM2_5.value,
            "value": 1.0, "unit": "µg/m³",
            "aqi": 5, "agg_window": cadence, "source": self.name,
        } for d in days]
        return data.validate(pd.DataFrame(rows))


def test_hybrid_skips_covered_and_fetches_gaps(tmp_path):
    existing = FakeProvider().fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                                    [Pollutant.PM2_5], 10)
    store.write(tmp_path, "06037", existing)

    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 18),
                         [Pollutant.PM2_5], 10, [p],
                         today=date(2026, 6, 20))
    fetched_days = {d for (s, e) in p.requested_ranges
                    for d in pd.date_range(s, e, freq="D").date}
    assert date(2026, 6, 16) not in fetched_days
    assert date(2026, 6, 17) in fetched_days
    assert date(2026, 6, 18) in fetched_days


def test_always_refetches_today(tmp_path):
    existing = FakeProvider().fetch("06037", date(2026, 6, 20), date(2026, 6, 20),
                                    [Pollutant.PM2_5], 10)
    store.write(tmp_path, "06037", existing)
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 20), date(2026, 6, 20),
                         [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 20))
    assert p.requested_ranges  # today re-fetched despite coverage


def test_refetch_fetches_everything(tmp_path):
    existing = FakeProvider().fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                                    [Pollutant.PM2_5], 10)
    store.write(tmp_path, "06037", existing)
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 17),
                         [Pollutant.PM2_5], 10, [p],
                         today=date(2026, 6, 30), refetch=True)
    fetched_days = {d for (s, e) in p.requested_ranges
                    for d in pd.date_range(s, e, freq="D").date}
    assert date(2026, 6, 16) in fetched_days
