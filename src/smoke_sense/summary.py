"""Summarize stored AQI data: coverage, breakdown, and per-metric stats.

Pure aggregation over a `data`-schema DataFrame. No I/O or CLI coupling.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def summarize(df: pd.DataFrame, start: date, end: date,
              filtered: dict | None = None) -> dict:
    """Return a JSON-serializable summary of `df` over [start, end]."""
    all_days = _days(start, end)
    rng = {"start": start.isoformat(), "end": end.isoformat()}

    if df.empty:
        return {
            "range": rng,
            "coverage": {
                "total_days": len(all_days),
                "days_present": 0,
                "days_missing": [d.isoformat() for d in all_days],
                "first_timestamp": None,
                "last_timestamp": None,
                "total_rows": 0,
            },
            "breakdown": [],
            "metrics": [],
        }

    present = set(df["timestamp"].dt.tz_convert("UTC").dt.date)
    coverage = {
        "total_days": len(all_days),
        "days_present": sum(1 for d in all_days if d in present),
        "days_missing": [d.isoformat() for d in all_days if d not in present],
        "first_timestamp": df["timestamp"].min().isoformat(),
        "last_timestamp": df["timestamp"].max().isoformat(),
        "total_rows": int(len(df)),
    }

    breakdown = [
        {"source": str(source), "metric": str(metric),
         "agg_window": int(agg), "rows": int(rows)}
        for (source, metric, agg), rows in
        df.groupby(["source", "metric", "agg_window"], observed=True).size().items()
    ]
    breakdown.sort(key=lambda r: (r["source"], r["metric"], r["agg_window"]))

    metrics = []
    for metric, group in df.groupby("metric", observed=True):
        values = group["value"]
        aqi = group["aqi"].dropna()
        metrics.append({
            "metric": str(metric),
            "stations": int(group["station_id"].nunique()),
            "sources": sorted({str(s) for s in group["source"].unique()}),
            "filtered": int((filtered or {}).get(str(metric), 0)),
            "value": {
                "min": float(values.min()),
                "p25": float(values.quantile(0.25)),
                "p50": float(values.quantile(0.50)),
                "mean": float(values.mean()),
                "p75": float(values.quantile(0.75)),
                "max": float(values.max()),
            },
            "aqi": None if aqi.empty else {
                "min": int(aqi.min()),
                "mean": float(aqi.mean()),
                "max": int(aqi.max()),
            },
        })
    metrics.sort(key=lambda r: r["metric"])

    return {"range": rng, "coverage": coverage,
            "breakdown": breakdown, "metrics": metrics}
