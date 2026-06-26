"""Shared CLI plumbing for the outlier filter.

Bridges Typer option values to the pure `outliers` module: parses
`--outlier-bound` specs, builds an `OutlierConfig` from overrides, applies the
filter (logging what was removed), and exposes a frame->frame callback for
callers that only want the cleaned data.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Callable, Optional

import pandas as pd
import typer

from ..data import Metric
from ..outliers import (DEFAULT_BOUNDS, DEFAULT_CONFIG, OutlierConfig,
                        OutlierReport, filter_outliers)

logger = logging.getLogger(__name__)


def parse_bound(spec: str) -> tuple[Metric, tuple[float, float]]:
    """Parse 'METRIC:LOW:HIGH' into (Metric, (low, high)). Raises ValueError.

    Metric name matching is case-insensitive.
    """
    parts = spec.split(":")
    if len(parts) != 3:
        raise ValueError(f"--outlier-bound must be METRIC:LOW:HIGH, got {spec!r}")
    name, low_s, high_s = parts
    try:
        metric = Metric(name)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    try:
        low, high = float(low_s), float(high_s)
    except ValueError as exc:
        raise ValueError(f"bound limits must be numbers, got {spec!r}") from exc
    if low >= high:
        raise ValueError(f"bound low must be < high, got {spec!r}")
    return metric, (low, high)


def build_config(*, no_range: bool, zscore: Optional[float], iqr: Optional[float],
                 bounds: list[tuple[Metric, tuple[float, float]]]) -> OutlierConfig:
    """Build an OutlierConfig from DEFAULT_CONFIG plus CLI overrides."""
    merged = dict(DEFAULT_BOUNDS)
    for metric, limits in bounds:
        merged[metric] = limits
    if zscore is None:
        z = DEFAULT_CONFIG.zscore      # keep default
    elif zscore <= 0:
        z = None                       # disable
    else:
        z = zscore                     # set
    return replace(
        DEFAULT_CONFIG,
        range_enabled=not no_range,
        bounds=merged,
        zscore=z,
        # iqr has no "keep default" sentinel: its default is None and callers
        # always derive it from iqr_on/iqr_k.
        iqr=iqr,
    )


def filter_frame(df: pd.DataFrame, *, enabled: bool, no_range: bool,
                 zscore: Optional[float], iqr_on: bool, iqr_k: float,
                 bound: Optional[list[str]]) -> tuple[pd.DataFrame, OutlierReport]:
    """Apply the outlier filter to `df` per the CLI flags; log removals."""
    if not enabled:
        return df, OutlierReport()
    parsed: list[tuple[Metric, tuple[float, float]]] = []
    for spec in (bound or []):
        try:
            parsed.append(parse_bound(spec))
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    cfg = build_config(no_range=no_range, zscore=zscore,
                       iqr=(iqr_k if iqr_on else None), bounds=parsed)
    clean, report = filter_outliers(df, cfg)
    if report.total:
        logger.info("filtered %d outlier rows %s", report.total, report.per_metric)
    return clean, report


def make_filter(*, enabled: bool, no_range: bool, zscore: Optional[float],
                iqr_on: bool, iqr_k: float, bound: Optional[list[str]]
                ) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """Return a frame->clean-frame callback capturing the CLI flags."""
    def _filter(df: pd.DataFrame) -> pd.DataFrame:
        clean, _ = filter_frame(df, enabled=enabled, no_range=no_range,
                                zscore=zscore, iqr_on=iqr_on, iqr_k=iqr_k,
                                bound=bound)
        return clean
    return _filter
