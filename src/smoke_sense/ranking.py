"""Pure per-station ranking over the long observation schema.

No I/O, no CLI. `rank_stations` aggregates a metric column per station and
returns the stations ordered by that aggregate.
"""

from __future__ import annotations

import pandas as pd

_RESULT_COLUMNS = ["station_id", "value", "count"]


def rank_stations(obs: pd.DataFrame, *, column: str, agg: str,
                  descending: bool = True, limit: int = 10) -> pd.DataFrame:
    """Rank stations by `agg` of `column`.

    `obs` has columns timestamp, station_id, value, aqi. Rows with a null
    `column` are dropped; the rest are grouped by station_id, aggregated with
    `agg` (one of "min"/"max"/"mean"), and sorted by the aggregate using a
    stable sort (ties keep station_id-ascending order). `limit <= 0` returns
    all stations. Returns columns station_id, value, count (value holds the
    aggregate regardless of which agg was used).
    """
    if obs.empty:
        return pd.DataFrame(columns=_RESULT_COLUMNS)
    valid = obs.dropna(subset=[column])
    if valid.empty:
        return pd.DataFrame(columns=_RESULT_COLUMNS)
    grouped = valid.groupby("station_id", observed=True)[column]
    result = pd.DataFrame({
        "value": grouped.agg(agg).astype("float64"),
        "count": grouped.count(),
    }).reset_index()
    result = result.sort_values(
        "value", ascending=not descending, kind="mergesort"
    ).reset_index(drop=True)
    if limit and limit > 0:
        result = result.head(limit).reset_index(drop=True)
    return result[_RESULT_COLUMNS]
