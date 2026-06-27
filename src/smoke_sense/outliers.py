"""Pure outlier detection and filtering over the long observation schema.

No I/O, no CLI. `filter_outliers` returns a cleaned frame plus a report of what
was dropped. Statistical checks operate per (station_id, metric) so a sensor is
judged against its own readings, not against other stations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from .data import Metric

logger = logging.getLogger(__name__)

DEFAULT_IQR_K: float = 3.0

_STATION_OUTLIER_COLUMNS = ["station_id", "readings", "flagged", "fraction"]

# (low, high) physical bounds per metric, in each metric's canonical unit.
DEFAULT_BOUNDS: dict[Metric, tuple[float, float]] = {
    Metric.PM2_5: (0, 1000),
    Metric.PM2_5_CF1: (0, 1000),
    Metric.PM2_5_ATM: (0, 1000),
    Metric.PM10: (0, 2000),
    Metric.PM10_CF1: (0, 2000),
    Metric.PM10_ATM: (0, 2000),
    Metric.PM1_0_CF1: (0, 2000),
    Metric.PM1_0_ATM: (0, 2000),
    Metric.O3: (0, 0.5),
    Metric.CO: (0, 50),
    Metric.SO2: (0, 2000),
    Metric.NO2: (0, 2000),
    Metric.PB: (0, 10),
    Metric.TEMP: (-50, 60),
    Metric.RH: (0, 100),
    Metric.PRESSURE: (800, 1100),
    Metric.WIND_SPEED: (0, 120),
    Metric.WIND_DIR: (0, 360),
    Metric.VOC: (0, 1000),
}


@dataclass(frozen=True)
class OutlierConfig:
    """Knobs for the outlier filter. Defaults are the code defaults."""

    range_enabled: bool = True
    bounds: dict[Metric, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_BOUNDS))
    zscore: float | None = 3.5           # per-station modified-z threshold; None disables
    iqr: float | None = None             # per-station IQR multiplier; None disables
    min_group: int = 5                   # skip stat checks for smaller groups
    exclude_stations: frozenset[str] = frozenset()   # drop these station IDs wholesale


DEFAULT_CONFIG = OutlierConfig()


@dataclass(frozen=True)
class OutlierReport:
    """Summary of what `filter_outliers` removed."""

    total: int = 0
    per_metric: dict[str, int] = field(default_factory=dict)
    per_check: dict[str, int] = field(default_factory=dict)


def range_mask(df: pd.DataFrame,
               bounds: dict[Metric, tuple[float, float]]) -> pd.Series:
    """True where `value` is outside the metric's configured [low, high]."""
    if df.empty:
        return pd.Series([], dtype=bool, index=df.index)
    # .astype(str) first: .map on a Categorical returns a Categorical, which
    # breaks the numeric comparison below.
    metric_str = df["metric"].astype(str)
    low = metric_str.map({m.value: b[0] for m, b in bounds.items()}).astype(float)
    high = metric_str.map({m.value: b[1] for m, b in bounds.items()}).astype(float)
    out = (df["value"] < low) | (df["value"] > high)
    return out.fillna(False)


def station_mask(df: pd.DataFrame,
                 exclude_stations: frozenset[str]) -> pd.Series:
    """True where `station_id` is in the user-given exclusion set.

    An empty set (the default) or empty frame drops nothing.
    """
    if df.empty or not exclude_stations:
        return pd.Series(False, index=df.index)
    return df["station_id"].isin(exclude_stations)


def _grouped(df: pd.DataFrame):
    return df.groupby(["station_id", "metric"], observed=True)["value"]


def zscore_mask(df: pd.DataFrame, threshold: float | None,
                min_group: int) -> pd.Series:
    """True where a value is a per-station spike (modified z-score > threshold).

    Uses the MAD-based modified z-score (0.6745 * |x - median| / MAD) so that
    the outlier itself does not inflate the center estimate. Groups with fewer
    than `min_group` observations or zero MAD are skipped.
    """
    if df.empty or threshold is None or threshold <= 0:
        return pd.Series(False, index=df.index)
    grp = _grouped(df)
    count = grp.transform("count")

    def _mad_z(s: pd.Series) -> pd.Series:
        median = s.median()
        mad = (s - median).abs().median()
        if mad == 0:
            return pd.Series(0.0, index=s.index)
        return 0.6745 * (s - median).abs() / mad

    z = grp.transform(_mad_z)
    mask = (z > threshold) & (count >= min_group)
    return mask.fillna(False)


def iqr_mask(df: pd.DataFrame, k: float | None, min_group: int) -> pd.Series:
    """True where a value is outside [Q1 - k*IQR, Q3 + k*IQR] for its group."""
    if df.empty or k is None:
        return pd.Series(False, index=df.index)
    grp = _grouped(df)
    q1 = grp.transform(lambda s: s.quantile(0.25))
    q3 = grp.transform(lambda s: s.quantile(0.75))
    count = grp.transform("count")
    iqr = q3 - q1
    mask = (((df["value"] < q1 - k * iqr) | (df["value"] > q3 + k * iqr))
            & (count >= min_group) & (iqr > 0))
    return mask.fillna(False)


def _evaluate_checks(df: pd.DataFrame,
                     config: OutlierConfig) -> tuple[pd.Series, dict[str, int]]:
    """Combined outlier mask over the enabled checks, plus per-check counts.

    Each dropped row is attributed to the FIRST matching check (order:
    station, range, zscore, iqr).
    """
    checks: list[tuple[str, pd.Series]] = []
    if config.exclude_stations:
        checks.append(("station", station_mask(df, config.exclude_stations)))
    if config.range_enabled:
        checks.append(("range", range_mask(df, config.bounds)))
    if config.zscore is not None and config.zscore > 0:
        checks.append(("zscore", zscore_mask(df, config.zscore, config.min_group)))
    if config.iqr is not None:
        checks.append(("iqr", iqr_mask(df, config.iqr, config.min_group)))

    combined = pd.Series(False, index=df.index)
    already = pd.Series(False, index=df.index)
    per_check: dict[str, int] = {}
    for name, mask in checks:
        mask = mask.fillna(False)
        per_check[name] = int((mask & ~already).sum())
        already = already | mask
        combined = combined | mask
    return combined, per_check


def filter_outliers(df: pd.DataFrame,
                    config: OutlierConfig = DEFAULT_CONFIG
                    ) -> tuple[pd.DataFrame, OutlierReport]:
    """Drop outlier rows per `config`; return (clean_df, report)."""
    if df.empty:
        return df.copy(), OutlierReport()

    combined, per_check = _evaluate_checks(df, config)
    dropped = df[combined]
    per_metric = {
        str(metric): int(n)
        for metric, n in dropped["metric"].value_counts().items() if n > 0
    }
    report = OutlierReport(total=int(combined.sum()),
                           per_metric=per_metric, per_check=per_check)
    return df[~combined].reset_index(drop=True), report


def station_outlier_counts(df: pd.DataFrame,
                           config: OutlierConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """Per-station outlier tally using the same checks as `filter_outliers`.

    Returns columns station_id, readings, flagged, fraction for stations with at
    least one flagged reading, sorted by fraction (desc) then station_id (asc).
    """
    if df.empty:
        return pd.DataFrame(columns=_STATION_OUTLIER_COLUMNS)
    combined, _ = _evaluate_checks(df, config)
    tab = pd.DataFrame({
        "station_id": df["station_id"].astype(str).to_numpy(),
        "_flagged": combined.astype(int).to_numpy(),
    })
    grp = tab.groupby("station_id", observed=True)["_flagged"]
    out = pd.DataFrame({"readings": grp.size(), "flagged": grp.sum()}).reset_index()
    out = out[out["flagged"] > 0]
    if out.empty:
        return pd.DataFrame(columns=_STATION_OUTLIER_COLUMNS)
    out["fraction"] = out["flagged"] / out["readings"]
    out = out.sort_values(["fraction", "station_id"],
                          ascending=[False, True]).reset_index(drop=True)
    return out[_STATION_OUTLIER_COLUMNS]
