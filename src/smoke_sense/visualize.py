"""Visualization helpers and the pluggable map-renderer registry."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Callable

import pandas as pd

from . import store
from .data import AQI_METRICS, Metric

logger = logging.getLogger(__name__)

_MEAN_COLUMNS = ["station_id", "latitude", "longitude", "mean"]
_OBS_COLUMNS = ["timestamp", "station_id", "value", "aqi"]
_STATION_COLUMNS = ["station_id", "latitude", "longitude"]
_SINGLE_COLOR = "tab:blue"


def resolve_by(metric: Metric, by: str) -> str:
    """Map a --by choice to the data column; validate AQI eligibility."""
    if by == "value":
        return "value"
    if by == "aqi":
        if metric not in AQI_METRICS:
            raise ValueError(
                f"AQI not available for {metric.value}; "
                "AQI only for PM2.5/PM10/O3")
        return "aqi"
    raise ValueError(f"invalid by={by!r}; expected 'value' or 'aqi'")


def y_label(metric: Metric, by: str) -> str:
    """Axis/colorbar label for the chosen quantity."""
    if by == "aqi":
        return "AQI"
    return f"{metric.value} ({metric.unit})"


def metric_observations(data_dir, fips: str, start: date, end: date,
                        metric: Metric,
                        outlier_filter: Callable[[pd.DataFrame],
                                                 pd.DataFrame] | None = None
                        ) -> pd.DataFrame:
    """Long observations for `metric` over [start, end].

    Returns columns timestamp, station_id, value, aqi. Empty (with those
    columns) if there is no matching data. If `outlier_filter` is given it is
    applied to the full read frame before the metric filter.
    """
    obs = store.read_range(data_dir, fips, start, end)
    if outlier_filter is not None:
        obs = outlier_filter(obs)
    obs = obs[obs["metric"] == metric.value]
    if obs.empty:
        return pd.DataFrame(columns=_OBS_COLUMNS)
    return obs[_OBS_COLUMNS].reset_index(drop=True)


def station_means(data_dir, fips: str, start: date, end: date,
                  metric: Metric, by: str = "value",
                  outlier_filter: Callable[[pd.DataFrame],
                                           pd.DataFrame] | None = None
                  ) -> pd.DataFrame:
    """Per-station mean of `metric`'s value (or AQI) over [start, end].

    Returns columns station_id, latitude, longitude, mean. Empty (with those
    columns) if there is no matching data or no station table.
    """
    column = resolve_by(metric, by)
    obs = metric_observations(data_dir, fips, start, end, metric,
                              outlier_filter=outlier_filter)
    if obs.empty:
        return pd.DataFrame(columns=_MEAN_COLUMNS)
    means = (
        obs.groupby("station_id", observed=True)[column].mean()
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


def station_coordinates(data_dir, fips: str, station_ids) -> pd.DataFrame:
    """Coordinates for the requested stations from the station table.

    Returns columns station_id, latitude, longitude (empty if there is no
    station table or none of `station_ids` have coordinates).
    """
    path = store.stations_path(data_dir, fips)
    if not path.exists():
        return pd.DataFrame(columns=_STATION_COLUMNS)
    stations = (
        pd.read_parquet(path)[_STATION_COLUMNS].drop_duplicates("station_id")
    )
    wanted = set(station_ids)
    return stations[stations["station_id"].isin(wanted)].reset_index(drop=True)


def _assign_colors(station_ids, palette: str) -> dict:
    """Deterministic per-station color map from `palette` over sorted IDs."""
    import matplotlib

    stations = sorted(set(station_ids))
    cmap = matplotlib.colormaps[palette]
    n = len(stations)
    return {sid: cmap(i / max(n - 1, 1)) for i, sid in enumerate(stations)}


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


_CHART_RENDERERS: dict[str, type["ChartRenderer"]] = {}


class ChartRenderer(ABC):
    """Renders metric observations into a chart artifact."""

    name: str

    @abstractmethod
    def render_series(self, obs: pd.DataFrame, *, y_column: str, y_label: str,
                      title: str, palette: str, output,
                      color_by_station: bool = False,
                      station_points: pd.DataFrame | None = None) -> Path:
        """One line per station over time; return the written path."""
        raise NotImplementedError

    @abstractmethod
    def render_scatter(self, obs: pd.DataFrame, *, y_column: str, y_label: str,
                       title: str, palette: str, output,
                       color_by_station: bool = False,
                       station_points: pd.DataFrame | None = None) -> Path:
        """All observations as points colored by station."""
        raise NotImplementedError

    @abstractmethod
    def render_aggregate(self, obs: pd.DataFrame, *, y_column: str, y_label: str,
                         title: str, palette: str, output, band: bool = True) -> Path:
        """Mean across stations per timestamp, optional min/max band."""
        raise NotImplementedError

    @abstractmethod
    def render_histogram(self, obs: pd.DataFrame, *, y_column: str, y_label: str,
                         title: str, palette: str, output, bins: int = 30) -> Path:
        """Distribution of the chosen quantity over all observations."""
        raise NotImplementedError


def register_chart_renderer(cls: type[ChartRenderer]) -> type[ChartRenderer]:
    _CHART_RENDERERS[cls.name] = cls
    return cls


def get_chart_renderer(name: str) -> ChartRenderer:
    if name not in _CHART_RENDERERS:
        raise KeyError(
            f"unknown chart renderer: {name!r} (have {sorted(_CHART_RENDERERS)})")
    return _CHART_RENDERERS[name]()


@register_chart_renderer
class MatplotlibChartRenderer(ChartRenderer):
    name = "matplotlib"

    @staticmethod
    def _open(title: str, y_label: str, station_points, colors):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if station_points is not None and not station_points.empty:
            fig, (ax_map, ax_chart) = plt.subplots(
                2, 1, figsize=(10, 9), gridspec_kw={"height_ratios": [1, 1.6]})
            MatplotlibChartRenderer._draw_station_map(ax_map, station_points, colors)
        else:
            fig, ax_chart = plt.subplots(figsize=(10, 5))
        ax_chart.set_title(title)
        ax_chart.set_ylabel(y_label)
        return plt, fig, ax_chart

    @staticmethod
    def _new_axes(title: str, y_label: str):
        return MatplotlibChartRenderer._open(title, y_label, None, None)

    @staticmethod
    def _draw_station_map(ax, station_points, colors) -> None:
        for _, r in station_points.iterrows():
            sid = r["station_id"]
            color = colors[sid] if colors and sid in colors else _SINGLE_COLOR
            ax.scatter(r["longitude"], r["latitude"], color=color, s=60,
                       edgecolor="black", linewidth=0.3)
            ax.annotate(str(sid), (r["longitude"], r["latitude"]),
                        fontsize="x-small", xytext=(3, 3),
                        textcoords="offset points")
        ax.set_title("stations")
        ax.set_xlabel("longitude")
        ax.set_ylabel("latitude")
        try:
            import contextily as cx
            cx.add_basemap(ax, crs="EPSG:4326",
                           source=cx.providers.OpenStreetMap.Mapnik)
        except Exception as exc:  # offline / tile error -> render without tiles
            logger.warning("basemap unavailable (%s); rendering without tiles", exc)

    @staticmethod
    def _save(plt, fig, output) -> Path:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            fig.savefig(output, dpi=150, bbox_inches="tight")
        finally:
            plt.close(fig)
        return output

    def render_series(self, obs, *, y_column, y_label, title, palette, output,
                      color_by_station=False, station_points=None) -> Path:
        colors = (_assign_colors(obs["station_id"].unique(), palette)
                  if color_by_station else None)
        plt, fig, ax = self._open(title, y_label, station_points, colors)
        ax.set_xlabel("time")
        for sid in sorted(obs["station_id"].unique()):
            sub = obs[obs["station_id"] == sid].sort_values("timestamp")
            if colors:
                ax.plot(sub["timestamp"], sub[y_column].astype("float64"),
                        label=str(sid), color=colors[sid])
            else:
                ax.plot(sub["timestamp"], sub[y_column].astype("float64"),
                        color=_SINGLE_COLOR)
        if colors:
            ax.legend(title="station", fontsize="small")
        return self._save(plt, fig, output)

    def render_scatter(self, obs, *, y_column, y_label, title, palette, output,
                       color_by_station=False, station_points=None) -> Path:
        colors = (_assign_colors(obs["station_id"].unique(), palette)
                  if color_by_station else None)
        plt, fig, ax = self._open(title, y_label, station_points, colors)
        ax.set_xlabel("time")
        if colors:
            for sid in sorted(obs["station_id"].unique()):
                sub = obs[obs["station_id"] == sid]
                ax.scatter(sub["timestamp"], sub[y_column].astype("float64"),
                           color=colors[sid], s=12, label=str(sid))
            ax.legend(title="station", fontsize="small")
        else:
            ax.scatter(obs["timestamp"], obs[y_column].astype("float64"),
                       color=_SINGLE_COLOR, s=12)
        return self._save(plt, fig, output)

    def render_aggregate(self, obs, *, y_column, y_label, title, palette, output,
                         band=True) -> Path:
        plt, fig, ax = self._new_axes(title, y_label)
        ax.set_xlabel("time")
        vals = obs.assign(_v=obs[y_column].astype("float64")).groupby("timestamp")["_v"]
        mean = vals.mean()
        ax.plot(mean.index, mean.values, label="mean")
        if band:
            ax.fill_between(mean.index, vals.min().values, vals.max().values,
                            alpha=0.2, label="min-max")
        ax.legend(fontsize="small")
        return self._save(plt, fig, output)

    def render_histogram(self, obs, *, y_column, y_label, title, palette, output,
                         bins=30) -> Path:
        plt, fig, ax = self._new_axes(title, y_label)
        ax.set_xlabel(y_label)
        ax.set_ylabel("count")
        ax.hist(obs[y_column].astype("float64").dropna(), bins=bins)
        return self._save(plt, fig, output)


def mean_map(data_dir, fips: str, start: date, end: date, metric: Metric, *,
             by: str = "value", palette: str = "YlOrRd", output,
             renderer: str = "matplotlib", basemap: bool = True,
             outlier_filter: Callable[[pd.DataFrame],
                                      pd.DataFrame] | None = None) -> Path | None:
    """Render a per-station mean map for `metric`; return the path or None if no data."""
    points = station_means(data_dir, fips, start, end, metric, by=by,
                           outlier_filter=outlier_filter)
    if points.empty or points["mean"].dropna().empty:
        return None
    label = f"mean {y_label(metric, by)}"
    title = f"{fips} {metric.value} ({by}) {start.isoformat()}..{end.isoformat()}"
    return get_renderer(renderer).render_point_map(
        points, value_label=label, palette=palette, title=title,
        output=output, basemap=basemap)
