"""Visualization helpers and the pluggable map-renderer registry."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path

import pandas as pd

from . import store
from .data import Metric

logger = logging.getLogger(__name__)

_MEAN_COLUMNS = ["station_id", "latitude", "longitude", "mean"]


def station_means(data_dir, fips: str, start: date, end: date,
                  metric: Metric) -> pd.DataFrame:
    """Per-station mean of `metric`'s value over [start, end], with coordinates.

    Returns columns station_id, latitude, longitude, mean. Empty (with those
    columns) if there is no matching data or no station table.
    """
    obs = store.read_range(data_dir, fips, start, end)
    obs = obs[obs["metric"] == metric.value]
    if obs.empty:
        return pd.DataFrame(columns=_MEAN_COLUMNS)
    means = (
        obs.groupby("station_id", observed=True)["value"].mean()
        .rename("mean").reset_index()
    )
    path = store.stations_path(data_dir, fips)
    if not path.exists():
        return pd.DataFrame(columns=_MEAN_COLUMNS)
    stations = (
        pd.read_parquet(path)[["station_id", "latitude", "longitude"]]
        .drop_duplicates("station_id")
    )
    merged = means.merge(stations, on="station_id", how="inner")
    return merged[_MEAN_COLUMNS]


_RENDERERS: dict[str, type["MapRenderer"]] = {}


class MapRenderer(ABC):
    """Renders a set of geographic points into a map artifact."""

    name: str

    @abstractmethod
    def render_point_map(self, points: pd.DataFrame, *, value_label: str,
                         palette: str, title: str, output, basemap: bool = True) -> Path:
        """Render `points` (latitude, longitude, mean) to `output`; return the path."""
        raise NotImplementedError


def register_renderer(cls: type[MapRenderer]) -> type[MapRenderer]:
    _RENDERERS[cls.name] = cls
    return cls


def get_renderer(name: str) -> MapRenderer:
    if name not in _RENDERERS:
        raise KeyError(f"unknown renderer: {name!r} (have {sorted(_RENDERERS)})")
    return _RENDERERS[name]()


@register_renderer
class MatplotlibRenderer(MapRenderer):
    name = "matplotlib"

    def render_point_map(self, points, *, value_label, palette, title, output,
                         basemap=True) -> Path:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        fig, ax = plt.subplots(figsize=(8, 8))
        scatter = ax.scatter(
            points["longitude"], points["latitude"], c=points["mean"],
            cmap=palette, s=40, edgecolor="black", linewidth=0.3)
        fig.colorbar(scatter, ax=ax, label=value_label)
        ax.set_title(title)
        ax.set_xlabel("longitude")
        ax.set_ylabel("latitude")
        if basemap:
            try:
                import contextily as cx
                cx.add_basemap(ax, crs="EPSG:4326",
                               source=cx.providers.OpenStreetMap.Mapnik)
            except Exception as exc:  # offline / tile error -> render without tiles
                logger.warning("basemap unavailable (%s); rendering without tiles", exc)
        fig.savefig(output, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return output


def mean_map(data_dir, fips: str, start: date, end: date, metric: Metric, *,
             palette: str = "YlOrRd", output, renderer: str = "matplotlib",
             basemap: bool = True) -> Path | None:
    """Render a per-station mean map for `metric`; return the path or None if no data."""
    points = station_means(data_dir, fips, start, end, metric)
    if points.empty:
        return None
    label = f"mean {metric.value} ({metric.unit})"
    title = f"{fips} {metric.value} {start.isoformat()}..{end.isoformat()}"
    return get_renderer(renderer).render_point_map(
        points, value_label=label, palette=palette, title=title,
        output=output, basemap=basemap)
