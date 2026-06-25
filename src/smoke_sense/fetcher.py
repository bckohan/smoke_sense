"""Fetch orchestration: gap detection, provider streaming, durable store writes.

No Typer/CLI coupling so the incremental logic is unit-testable.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from . import store

logger = logging.getLogger(__name__)


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


def _flush(data_dir, fips, buffer: list[pd.DataFrame]) -> None:
    if buffer:
        # If store.write itself fails mid-way (e.g. disk error while persisting
        # a partial buffer after an upstream error), that exception propagates
        # and is not retried — an accepted limit of the flush-on-error path.
        combined = pd.concat(buffer, ignore_index=True)
        store.write(data_dir, fips, combined)
        logger.info("wrote %d rows for %s", len(combined), fips)
        buffer.clear()


def fetch_county(data_dir, fips, start, end, pollutants, requested_cadence,
                 providers, today, refetch=False) -> None:
    """Stream provider chunks into a per-county buffer; write once at the end.

    On any interceptable exit (unhandled exception or KeyboardInterrupt) the
    partial buffer is flushed to the store before the exception propagates.
    """
    cov = store.coverage(data_dir, fips)
    buffer: list[pd.DataFrame] = []
    try:
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
                for chunk in provider.fetch(fips, run_start, run_end, pollutants, actual):
                    buffer.append(chunk)
    except BaseException:
        _flush(data_dir, fips, buffer)
        raise
    _flush(data_dir, fips, buffer)
