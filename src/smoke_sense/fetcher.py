"""Fetch orchestration: gap detection, provider calls, and store writes.

No Typer/CLI coupling so the incremental logic is unit-testable.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import store


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _contiguous_ranges(days: list[date]) -> list[tuple[date, date]]:
    if not days:
        return []
    days = sorted(days)
    ranges: list[tuple[date, date]] = []
    run_start = prev = days[0]
    for d in days[1:]:
        if d == prev + timedelta(days=1):
            prev = d
        else:
            ranges.append((run_start, prev))
            run_start = prev = d
    ranges.append((run_start, prev))
    return ranges


def fetch_county(data_dir, fips, start, end, pollutants, requested_cadence,
                 providers, today, refetch=False) -> None:
    """Fetch missing days per provider and merge results into the store."""
    cov = store.coverage(data_dir, fips)
    for provider in providers:
        actual = provider.resolve_cadence(requested_cadence)
        if refetch:
            missing = _days(start, end)
        else:
            missing = [
                d for d in _days(start, end)
                if d == today
                or cov.get((d, provider.name), 10 ** 9) > actual
            ]
        for run_start, run_end in _contiguous_ranges(missing):
            df = provider.fetch(fips, run_start, run_end, pollutants, actual)
            store.write(data_dir, fips, df)
