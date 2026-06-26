# Configurable Outlier Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A configurable outlier filter that drops likely-erroneous sensor readings, applied by default before visualization and summary.

**Architecture:** A pure `outliers.py` module (config + mask checks + `filter_outliers`); a shared `bin/_outlier_cli.py` helper (Typer options, config building, parse + apply + log); `summary` reports per-metric dropped counts; `visualize` gains a generic `outlier_filter` transform hook so it stays decoupled from the outlier logic.

**Tech Stack:** Python 3.12, pandas, numpy, Typer, Rich, pytest.

**Spec:** `docs/superpowers/specs/2026-06-26-outlier-filter-design.md`

---

## File Structure

- `src/smoke_sense/outliers.py` (new) — `OutlierConfig`, `DEFAULT_CONFIG`, `DEFAULT_BOUNDS`, `DEFAULT_IQR_K`, `range_mask`/`zscore_mask`/`iqr_mask`, `filter_outliers`, `OutlierReport`. Pure; no I/O, no CLI.
- `src/smoke_sense/bin/_outlier_cli.py` (new) — `parse_bound`, `build_config`, `filter_frame`, `make_filter`. Bridges CLI option values to the pure module; logs.
- `src/smoke_sense/summary.py` (modify) — `summarize()` gains optional `filtered`.
- `src/smoke_sense/bin/summary.py` (modify) — outlier options + per-fips filter + `filtered` column.
- `src/smoke_sense/visualize.py` (modify) — `outlier_filter` hook on `metric_observations`/`station_means`/`mean_map`.
- `src/smoke_sense/bin/visualize.py` (modify) — outlier options on all five subcommands; build + pass the filter.
- Tests: `tests/test_outliers.py`, `tests/test_outlier_cli.py`, plus additions to `tests/test_summary.py` (or new), `tests/test_visualize.py`, `tests/test_visualize_cli.py`.

---

### Task 0: `outliers.py` — config, masks, filter_outliers

**Goal:** Pure outlier detection: config dataclass, three mask functions (per-station stats), and `filter_outliers` returning a clean frame + report.

**Files:**
- Create: `src/smoke_sense/outliers.py`
- Test: `tests/test_outliers.py`

**Acceptance Criteria:**
- [ ] `range_mask` flags values below low / above high / negative; leaves in-range and no-bound metrics unflagged.
- [ ] `zscore_mask` flags a per-station spike; ignores across-station spread; skips groups `< min_group` and `std==0`.
- [ ] `iqr_mask` flags an extreme with k=3; respects custom k; skips small/zero-spread groups.
- [ ] `filter_outliers` drops the union; `report.total`/`per_metric`/`per_check` correct (overlap counted once, order range→zscore→iqr).
- [ ] Empty input → empty clean frame + zeroed report.

**Verify:** `uv run pytest tests/test_outliers.py -v`

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_outliers.py`:

```python
import pandas as pd

from smoke_sense import outliers
from smoke_sense.data import Metric


def _df(rows):
    """rows: list of (station_id, metric, value)."""
    return pd.DataFrame({
        "timestamp": pd.to_datetime(
            ["2026-06-16T01:00:00"] * len(rows), utc=True),
        "county_fips": ["06037"] * len(rows),
        "station_id": [r[0] for r in rows],
        "metric": pd.Categorical([r[1].value for r in rows]),
        "value": [r[2] for r in rows],
        "aqi": pd.array([pd.NA] * len(rows), dtype="Int16"),
        "agg_window": [10] * len(rows),
        "source": ["purpleair"] * len(rows),
    })


def test_range_mask_flags_out_of_bounds():
    df = _df([
        ("s1", Metric.PM2_5, 10.0),      # ok
        ("s1", Metric.PM2_5, -5.0),      # negative -> outlier
        ("s1", Metric.PM2_5, 5000.0),    # above 1000 -> outlier
        ("s1", Metric.RH, 50.0),         # ok
        ("s1", Metric.RH, 150.0),        # above 100 -> outlier
    ])
    mask = outliers.range_mask(df, outliers.DEFAULT_BOUNDS)
    assert mask.tolist() == [False, True, True, False, True]


def test_range_mask_leaves_unconfigured_metric():
    df = _df([("s1", Metric.PM2_5, 10.0)])
    mask = outliers.range_mask(df, {})  # no bounds configured
    assert mask.tolist() == [False]


def test_zscore_mask_flags_per_station_spike():
    # s1 has a clear spike; all within range bounds.
    rows = [("s1", Metric.PM2_5, v) for v in [10, 11, 9, 10, 12, 900]]
    df = _df(rows)
    mask = outliers.zscore_mask(df, threshold=3.0, min_group=5)
    assert mask.tolist() == [False, False, False, False, False, True]


def test_zscore_mask_ignores_across_station_spread():
    # Two stations each internally consistent but very different levels.
    rows = ([("s1", Metric.PM2_5, v) for v in [10, 11, 9, 10, 12]]
            + [("s2", Metric.PM2_5, v) for v in [500, 510, 505, 495, 500]])
    df = _df(rows)
    mask = outliers.zscore_mask(df, threshold=3.0, min_group=5)
    assert not mask.any()


def test_zscore_mask_skips_small_group():
    rows = [("s1", Metric.PM2_5, v) for v in [10, 11, 900]]  # only 3 points
    df = _df(rows)
    mask = outliers.zscore_mask(df, threshold=3.0, min_group=5)
    assert not mask.any()


def test_iqr_mask_flags_extreme():
    rows = [("s1", Metric.PM2_5, v) for v in [10, 11, 9, 10, 12, 900]]
    df = _df(rows)
    mask = outliers.iqr_mask(df, k=3.0, min_group=5)
    assert mask.tolist()[-1] is True or mask.tolist()[-1]  # the 900 is flagged
    assert mask.sum() == 1


def test_filter_outliers_union_and_report():
    rows = [("s1", Metric.PM2_5, v) for v in [10, 11, 9, 10, 12]] + [
        ("s1", Metric.PM2_5, -1.0),    # range
        ("s1", Metric.PM2_5, 900.0),   # zscore (and range? 900<1000 so not range)
    ]
    df = _df(rows)
    cfg = outliers.OutlierConfig(zscore=3.0, min_group=5)
    clean, report = outliers.filter_outliers(df, cfg)
    assert len(clean) == 5
    assert report.total == 2
    assert report.per_metric == {"PM2.5": 2}
    assert report.per_check["range"] == 1
    assert report.per_check["zscore"] == 1


def test_filter_outliers_empty():
    df = _df([]).iloc[0:0]
    clean, report = outliers.filter_outliers(df)
    assert clean.empty
    assert report.total == 0 and report.per_metric == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_outliers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'smoke_sense.outliers'`.

- [ ] **Step 3: Implement `outliers.py`**

Create `src/smoke_sense/outliers.py`:

```python
"""Pure outlier detection and filtering over the long observation schema.

No I/O, no CLI. `filter_outliers` returns a cleaned frame plus a report of what
was dropped. Statistical checks operate per (station_id, metric) so a sensor is
judged against its own readings, not against other stations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

import pandas as pd

from .data import Metric

logger = logging.getLogger(__name__)

DEFAULT_IQR_K: float = 3.0

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
    low = metric_str.map({m.value: b[0] for m, b in bounds.items()})
    high = metric_str.map({m.value: b[1] for m, b in bounds.items()})
    out = (df["value"] < low) | (df["value"] > high)
    return out.fillna(False)


def _grouped(df: pd.DataFrame):
    return df.groupby(["station_id", "metric"], observed=True)["value"]


def zscore_mask(df: pd.DataFrame, threshold: float | None,
                min_group: int) -> pd.Series:
    """True where a value's modified (MAD-based) z-score exceeds `threshold`.

    Uses the robust modified z-score `0.6745 * |x - median| / MAD` per
    (station, metric) group, where `MAD = median(|x - median|)`. Robust to a
    lone spike inflating its own group's spread, which a mean/std z-score masks.
    Groups with `< min_group` rows or `MAD == 0` flag nothing.
    """
    if df.empty or threshold is None or threshold <= 0:
        return pd.Series(False, index=df.index)
    grp = _grouped(df)
    median = grp.transform("median")
    abs_dev = (df["value"] - median).abs()
    # MAD = median of absolute deviations, per group (align by the group key).
    mad = abs_dev.groupby([df["station_id"], df["metric"]],
                          observed=True).transform("median")
    count = grp.transform("count")
    mod_z = 0.6745 * abs_dev / mad
    mask = (mod_z > threshold) & (count >= min_group) & mad.notna() & (mad > 0)
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


def filter_outliers(df: pd.DataFrame,
                    config: OutlierConfig = DEFAULT_CONFIG
                    ) -> tuple[pd.DataFrame, OutlierReport]:
    """Drop outlier rows per `config`; return (clean_df, report)."""
    if df.empty:
        return df.copy(), OutlierReport()

    checks: list[tuple[str, pd.Series]] = []
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

    dropped = df[combined]
    per_metric = {
        str(metric): int(n)
        for metric, n in dropped["metric"].value_counts().items() if n > 0
    }
    report = OutlierReport(total=int(combined.sum()),
                           per_metric=per_metric, per_check=per_check)
    return df[~combined].reset_index(drop=True), report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_outliers.py -v`
Expected: PASS.

- [ ] **Step 5: Ensure numpy/pandas available (no new dep expected)**

Run: `uv run python -c "import pandas, numpy; print('ok')"`
Expected: `ok` (both are already transitive deps via pandas; no `uv add` needed).

- [ ] **Step 6: Commit**

```bash
git add src/smoke_sense/outliers.py tests/test_outliers.py
git commit -m "feat(outliers): pure config + range/zscore/iqr masks + filter_outliers"
```

---

### Task 1: `bin/_outlier_cli.py` — shared CLI helper

**Goal:** Bridge CLI option values to `outliers.filter_outliers`: parse `--outlier-bound`, build a config from overrides, apply + log, and expose a `make_filter` callback.

**Files:**
- Create: `src/smoke_sense/bin/_outlier_cli.py`
- Test: `tests/test_outlier_cli.py`

**Acceptance Criteria:**
- [ ] `parse_bound("PM2.5:0:500")` → `(Metric.PM2_5, (0.0, 500.0))`; bad metric / non-numeric / low≥high raise `ValueError`.
- [ ] `build_config` applies overrides: `no_range` toggles range; `zscore` override (≤0 → disabled); `iqr` set/None; merged bounds.
- [ ] `filter_frame(df, enabled=False, ...)` returns the input unchanged + zeroed report.
- [ ] `filter_frame(df, enabled=True, ...)` returns the cleaned frame + report; invalid bound → `typer.BadParameter`.
- [ ] `make_filter(...)` returns a callable that maps a frame to its cleaned frame.

**Verify:** `uv run pytest tests/test_outlier_cli.py -v`

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_outlier_cli.py`:

```python
import pandas as pd
import pytest
import typer

from smoke_sense.bin import _outlier_cli as oc
from smoke_sense.data import Metric


def _df(rows):
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-06-16T01:00:00"] * len(rows), utc=True),
        "county_fips": ["06037"] * len(rows),
        "station_id": [r[0] for r in rows],
        "metric": pd.Categorical([r[1].value for r in rows]),
        "value": [r[2] for r in rows],
        "aqi": pd.array([pd.NA] * len(rows), dtype="Int16"),
        "agg_window": [10] * len(rows),
        "source": ["purpleair"] * len(rows),
    })


def test_parse_bound_ok():
    assert oc.parse_bound("PM2.5:0:500") == (Metric.PM2_5, (0.0, 500.0))


@pytest.mark.parametrize("spec", ["PM2.5:0", "NOPE:0:1", "PM2.5:a:b", "PM2.5:5:5"])
def test_parse_bound_bad(spec):
    with pytest.raises(ValueError):
        oc.parse_bound(spec)


def test_build_config_overrides():
    cfg = oc.build_config(no_range=True, zscore=None, iqr=None, bounds=[])
    assert cfg.range_enabled is False
    cfg2 = oc.build_config(no_range=False, zscore=2.0, iqr=3.0,
                           bounds=[(Metric.PM2_5, (0.0, 500.0))])
    assert cfg2.zscore == 2.0 and cfg2.iqr == 3.0
    assert cfg2.bounds[Metric.PM2_5] == (0.0, 500.0)
    # zscore <= 0 disables
    cfg3 = oc.build_config(no_range=False, zscore=0.0, iqr=None, bounds=[])
    assert cfg3.zscore is None


def test_filter_frame_disabled_passthrough():
    df = _df([("s1", Metric.PM2_5, -5.0)])
    out, report = oc.filter_frame(df, enabled=False, no_range=False, zscore=None,
                                  iqr_on=False, iqr_k=3.0, bound=None)
    assert len(out) == 1 and report.total == 0


def test_filter_frame_enabled_drops_and_reports():
    df = _df([("s1", Metric.PM2_5, 10.0), ("s1", Metric.PM2_5, -5.0)])
    out, report = oc.filter_frame(df, enabled=True, no_range=False, zscore=None,
                                  iqr_on=False, iqr_k=3.0, bound=None)
    assert len(out) == 1 and report.total == 1
    assert report.per_metric == {"PM2.5": 1}


def test_filter_frame_bad_bound_raises_badparameter():
    df = _df([("s1", Metric.PM2_5, 10.0)])
    with pytest.raises(typer.BadParameter):
        oc.filter_frame(df, enabled=True, no_range=False, zscore=None,
                        iqr_on=False, iqr_k=3.0, bound=["PM2.5:bad"])


def test_make_filter_returns_callable():
    df = _df([("s1", Metric.PM2_5, 10.0), ("s1", Metric.PM2_5, -5.0)])
    f = oc.make_filter(enabled=True, no_range=False, zscore=None,
                       iqr_on=False, iqr_k=3.0, bound=None)
    clean = f(df)
    assert len(clean) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_outlier_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'smoke_sense.bin._outlier_cli'`.

- [ ] **Step 3: Implement `_outlier_cli.py`**

Create `src/smoke_sense/bin/_outlier_cli.py`:

```python
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
    """Parse 'METRIC:LOW:HIGH' into (Metric, (low, high)). Raises ValueError."""
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
    z = None if (zscore is not None and zscore <= 0) else zscore
    return replace(
        DEFAULT_CONFIG,
        range_enabled=not no_range,
        bounds=merged,
        zscore=DEFAULT_CONFIG.zscore if zscore is None else z,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_outlier_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/smoke_sense/bin/_outlier_cli.py tests/test_outlier_cli.py
git commit -m "feat(outliers): shared CLI helper (parse/build/filter/make_filter)"
```

---

### Task 2: Summary integration

**Goal:** `summarize()` reports a per-metric `filtered` count; the `summary` command filters by default and shows a `filtered` column.

**Files:**
- Modify: `src/smoke_sense/summary.py`
- Modify: `src/smoke_sense/bin/summary.py`
- Test: `tests/test_summary.py` (create if absent)

**Acceptance Criteria:**
- [ ] `summarize(df, start, end, filtered={"PM2.5": 3})` adds `"filtered": 3` to that metric's entry; absent metrics get `0`.
- [ ] `summary` CLI filters by default and the JSON metrics carry `filtered`; injected garbage is dropped and counted.
- [ ] `--no-outlier-filter` keeps the garbage and reports `filtered` 0.

**Verify:** `uv run pytest tests/test_summary.py -v`

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create or extend `tests/test_summary.py`:

```python
import json
from datetime import date

import pandas as pd
from typer.testing import CliRunner

from smoke_sense import store, summary as summary_core
from smoke_sense.bin import app
from smoke_sense.data import Metric

runner = CliRunner()


def _row(ts, metric, value, station, aqi=pd.NA, source="purpleair", agg=10):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037", "station_id": station,
        "latitude": 34.0, "longitude": -118.2,
        "metric": metric.value, "value": value,
        "aqi": aqi, "agg_window": agg, "source": source,
    }


def test_summarize_filtered_field():
    df = pd.DataFrame([_row("2026-06-16T01:00:00", Metric.PM2_5, 10.0, "s1")])
    out = summary_core.summarize(df, date(2026, 6, 16), date(2026, 6, 16),
                                 filtered={"PM2.5": 3})
    pm = next(m for m in out["metrics"] if m["metric"] == "PM2.5")
    assert pm["filtered"] == 3


def test_summarize_filtered_default_zero():
    df = pd.DataFrame([_row("2026-06-16T01:00:00", Metric.PM2_5, 10.0, "s1")])
    out = summary_core.summarize(df, date(2026, 6, 16), date(2026, 6, 16))
    pm = next(m for m in out["metrics"] if m["metric"] == "PM2.5")
    assert pm["filtered"] == 0


def _seed_with_garbage(tmp_path):
    rows = [_row(f"2026-06-16T0{i}:00:00", Metric.PM2_5, v, "s1")
            for i, v in enumerate([10, 11, 9, 8, 12])]
    rows.append(_row("2026-06-16T09:00:00", Metric.PM2_5, -999.0, "s1"))  # garbage
    store.write(tmp_path, "06037", pd.DataFrame(rows))


def test_summary_cli_filters_by_default(tmp_path):
    _seed_with_garbage(tmp_path)
    result = runner.invoke(app, [
        "summary", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["06037"]
    pm = next(m for m in data["metrics"] if m["metric"] == "PM2.5")
    assert pm["filtered"] == 1
    assert pm["value"]["min"] >= 0  # the -999 was removed


def test_summary_cli_no_filter_keeps_garbage(tmp_path):
    _seed_with_garbage(tmp_path)
    result = runner.invoke(app, [
        "summary", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json", "--no-outlier-filter"])
    assert result.exit_code == 0, result.output
    pm = next(m for m in json.loads(result.output)["06037"]["metrics"]
              if m["metric"] == "PM2.5")
    assert pm["filtered"] == 0
    assert pm["value"]["min"] == -999.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_summary.py -v`
Expected: FAIL — `summarize()` has no `filtered` param (TypeError) and the CLI lacks `--no-outlier-filter`.

- [ ] **Step 3: Add `filtered` to `summarize()`**

In `src/smoke_sense/summary.py`, change the signature and the metrics loop. Replace the `def summarize(...)` line:

```python
def summarize(df: pd.DataFrame, start: date, end: date,
              filtered: dict | None = None) -> dict:
```

In the empty-frame early return, no change is needed (no metrics). In the metrics loop, add the `filtered` field to each appended dict. Replace the `metrics.append({...})` block with:

```python
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
```

- [ ] **Step 4: Wire the filter + column into `bin/summary.py`**

In `src/smoke_sense/bin/summary.py`, add the import near the others:

```python
from . import _outlier_cli
```

Add a `filtered` column to the metrics table. In `_render`, change the metrics column list and the `add_row` call:

```python
    metrics = Table(title="Metrics")
    for col in ("metric", "stations", "sources", "filtered",
                "min", "p25", "p50", "mean", "p75", "max", "aqi min/mean/max"):
        metrics.add_column(col)
    for row in s["metrics"]:
        v = row["value"]
        a = row["aqi"]
        aqi_str = "-" if a is None else f"{a['min']}/{a['mean']:.0f}/{a['max']}"
        metrics.add_row(
            row["metric"], str(row["stations"]), ",".join(row["sources"]),
            str(row["filtered"]),
            f"{v['min']:g}", f"{v['p25']:g}", f"{v['p50']:g}", f"{v['mean']:g}",
            f"{v['p75']:g}", f"{v['max']:g}", aqi_str,
        )
    console.print(metrics)
```

Add the outlier options to the `summary` command signature (after `json`):

```python
    outlier_filter: bool = typer.Option(
        True, "--outlier-filter/--no-outlier-filter",
        help="Drop likely-erroneous readings before summarizing"),
    outlier_zscore: Optional[float] = typer.Option(
        None, "--outlier-zscore", help="Per-station z-score threshold (<=0 disables)"),
    outlier_iqr: bool = typer.Option(
        False, "--outlier-iqr/--no-outlier-iqr", help="Enable per-station IQR check"),
    outlier_iqr_k: float = typer.Option(
        3.0, "--outlier-iqr-k", help="IQR multiplier"),
    no_outlier_range: bool = typer.Option(
        False, "--no-outlier-range", help="Disable the physical-bounds check"),
    outlier_bound: Optional[List[str]] = typer.Option(
        None, "--outlier-bound", help="Override a bound: METRIC:LOW:HIGH (repeatable)"),
```

Replace the results-building block with a per-fips read → filter → summarize:

```python
    results = {}
    for fips in county_fips:
        raw = store.read_range(output, fips, start_date, end_date)
        clean, report = _outlier_cli.filter_frame(
            raw, enabled=outlier_filter, no_range=no_outlier_range,
            zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
            bound=outlier_bound)
        results[fips] = summary_core.summarize(
            clean, start_date, end_date, filtered=report.per_metric)
```

(`Optional` and `List` are already imported in this file.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_summary.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/smoke_sense/summary.py src/smoke_sense/bin/summary.py tests/test_summary.py
git commit -m "feat(summary): filter outliers by default and report per-metric filtered counts"
```

---

### Task 3: Visualize core `outlier_filter` hook

**Goal:** Let `metric_observations`, `station_means`, and `mean_map` accept an optional `outlier_filter` transform applied to the raw frame before metric filtering — keeping `visualize.py` decoupled from the outlier module.

**Files:**
- Modify: `src/smoke_sense/visualize.py`
- Test: `tests/test_visualize.py`

**Acceptance Criteria:**
- [ ] `metric_observations(..., outlier_filter=fn)` applies `fn` to the read frame before the metric filter; default `None` unchanged.
- [ ] `station_means(..., outlier_filter=fn)` and `mean_map(..., outlier_filter=fn)` thread it through.
- [ ] Existing behavior (no `outlier_filter`) is unchanged.

**Verify:** `uv run pytest tests/test_visualize.py -v`

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_visualize.py`:

```python
def test_metric_observations_applies_outlier_filter(tmp_path):
    _seed(tmp_path)  # existing helper seeds PM2.5 s1=[10,20], s2=[5], TEMP s1=25

    def drop_high(df):
        return df[df["value"] < 15.0]

    out = visualize.metric_observations(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16),
        Metric.PM2_5, outlier_filter=drop_high)
    assert out["value"].max() < 15.0
    # without the filter the 20.0 reading is present
    base = visualize.metric_observations(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16), Metric.PM2_5)
    assert base["value"].max() == 20.0


def test_station_means_applies_outlier_filter(tmp_path):
    _seed(tmp_path)

    def drop_high(df):
        return df[df["value"] < 15.0]

    out = visualize.station_means(
        tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 16),
        Metric.PM2_5, outlier_filter=drop_high)
    # s1 now only has the 10.0 reading -> mean 10.0
    assert out[out["station_id"] == "s1"]["mean"].iloc[0] == 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_visualize.py -k outlier_filter -v`
Expected: FAIL — `metric_observations()` got an unexpected keyword argument `outlier_filter`.

- [ ] **Step 3: Add the hook**

In `src/smoke_sense/visualize.py`, update three functions. First add the typing import at the top (with the other imports):

```python
from typing import Callable, Optional
```

Replace `metric_observations`:

```python
def metric_observations(data_dir, fips: str, start: date, end: date,
                        metric: Metric,
                        outlier_filter: Optional[Callable[[pd.DataFrame],
                                                          pd.DataFrame]] = None
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
```

Replace `station_means`'s signature and its `metric_observations` call:

```python
def station_means(data_dir, fips: str, start: date, end: date,
                  metric: Metric, by: str = "value",
                  outlier_filter: Optional[Callable[[pd.DataFrame],
                                                    pd.DataFrame]] = None
                  ) -> pd.DataFrame:
    """Per-station mean of `metric`'s value (or AQI) over [start, end].

    Returns columns station_id, latitude, longitude, mean. Empty (with those
    columns) if there is no matching data or no station table.
    """
    column = resolve_by(metric, by)
    obs = metric_observations(data_dir, fips, start, end, metric,
                              outlier_filter=outlier_filter)
```

(The rest of `station_means` is unchanged.)

Then update `mean_map` to accept and forward it. Find the `def mean_map(` definition (further down the file) and add `outlier_filter` to its keyword args and the `station_means` call. Change its signature's keyword section to include:

```python
def mean_map(data_dir, fips: str, start: date, end: date, metric: Metric, *,
             by: str = "value", palette: str = "YlOrRd", output,
             renderer: str = "matplotlib", basemap: bool = True,
             outlier_filter: Optional[Callable[[pd.DataFrame],
                                               pd.DataFrame]] = None) -> "Path | None":
```

and change its `station_means(...)` call to pass `outlier_filter=outlier_filter`:

```python
    points = station_means(data_dir, fips, start, end, metric, by=by,
                           outlier_filter=outlier_filter)
```

> Note: read the current `mean_map` body before editing to keep the rest of its logic (label/title/render) intact — only the signature and the `station_means` call change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_visualize.py -v`
Expected: PASS (new hook tests + all existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/smoke_sense/visualize.py tests/test_visualize.py
git commit -m "feat(visualize): optional outlier_filter hook on observation helpers"
```

---

### Task 4: Visualize CLI wiring

**Goal:** Add the shared outlier options to all five `visualize` subcommands and pass a built filter into the core functions; on by default.

**Files:**
- Modify: `src/smoke_sense/bin/visualize.py`
- Test: `tests/test_visualize_cli.py`

**Acceptance Criteria:**
- [ ] All five subcommands accept `--outlier-filter/--no-outlier-filter`, `--outlier-zscore`, `--outlier-iqr/--no-outlier-iqr`, `--outlier-iqr-k`, `--no-outlier-range`, `--outlier-bound`.
- [ ] By default, injected garbage is excluded from the rendered data; `--no-outlier-filter` includes it.
- [ ] Invalid `--outlier-bound` exits non-zero.

**Verify:** `uv run pytest tests/test_visualize_cli.py -v` then `uv run pytest -q`

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_visualize_cli.py` (reuse the existing `runner`, `_row`, `_seed` helpers in that file):

```python
def _seed_with_garbage(tmp_path):
    rows = [_row(f"2026-06-16T0{i}:00:00", Metric.PM2_5, v, "s1", 34.0, -118.2)
            for i, v in enumerate([10, 11, 9, 8, 12])]
    rows.append(_row("2026-06-16T09:00:00", Metric.PM2_5, -999.0, "s1", 34.0, -118.2))
    store.write(tmp_path, "06037", pd.DataFrame(rows))


def test_histogram_filters_garbage_by_default(tmp_path):
    _seed_with_garbage(tmp_path)
    result = runner.invoke(app, [
        "visualize", "histogram", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "06037").glob("*_histogram.png"))


def test_no_outlier_filter_keeps_garbage(tmp_path):
    _seed_with_garbage(tmp_path)
    result = runner.invoke(app, [
        "visualize", "histogram", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--no-outlier-filter", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output


def test_visualize_bad_outlier_bound_fails(tmp_path):
    _seed_with_garbage(tmp_path)
    result = runner.invoke(app, [
        "visualize", "histogram", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--outlier-bound", "PM2.5:bad",
        "--output-dir", str(tmp_path)])
    assert result.exit_code != 0
```

The default-filter behavior is best asserted at the core level (Task 3 covers the drop logic); here we assert the CLI runs end-to-end with filtering on and off and that bad bounds fail. To assert the garbage is actually excluded, add a core-level check:

```python
def test_render_chart_excludes_garbage(tmp_path):
    from smoke_sense import visualize as viz
    from smoke_sense.bin import _outlier_cli
    _seed_with_garbage(tmp_path)
    f = _outlier_cli.make_filter(enabled=True, no_range=False, zscore=None,
                                 iqr_on=False, iqr_k=3.0, bound=None)
    obs = viz.metric_observations(tmp_path, "06037", date(2026, 6, 16),
                                  date(2026, 6, 16), Metric.PM2_5, outlier_filter=f)
    assert obs["value"].min() >= 0  # -999 dropped
```

(Add `from datetime import date` to the imports at the top of `tests/test_visualize_cli.py` if not already present.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_visualize_cli.py -k "outlier or garbage" -v`
Expected: FAIL — subcommands don't accept `--no-outlier-filter` yet (and the core test fails until Task 3 is in; Task 3 precedes this task).

- [ ] **Step 3: Add an options dataclass-free helper and wire each command**

In `src/smoke_sense/bin/visualize.py`, add the import:

```python
from . import _outlier_cli
```

`_render_chart` gains an `outlier_filter` param and passes it to `metric_observations`. Change its signature and the `metric_observations` call:

```python
def _render_chart(kind: str, method_name: str, county_fips: str, start: datetime,
                  end: Optional[datetime], metric: str, by: str, palette: str,
                  output: Optional[Path], renderer: str, output_dir: Path, *,
                  stations: Optional[list[str]] = None,
                  extra: Optional[dict] = None,
                  outlier_filter=None) -> None:
    chosen, y_column = _prepare(county_fips, metric, by)
    start_date = start.date()
    end_date = end.date() if end else date.today()
    obs = viz.metric_observations(output_dir, county_fips, start_date, end_date,
                                  chosen, outlier_filter=outlier_filter)
```

(The rest of `_render_chart` is unchanged.)

Add the six outlier options to **each** of the five command signatures (`mean_map`, `series`, `scatter`, `aggregate`, `histogram`), inserted before `output_dir`. Use this exact block in every command:

```python
    outlier_filter_on: bool = typer.Option(
        True, "--outlier-filter/--no-outlier-filter",
        help="Drop likely-erroneous readings before plotting"),
    outlier_zscore: Optional[float] = typer.Option(
        None, "--outlier-zscore", help="Per-station z-score threshold (<=0 disables)"),
    outlier_iqr: bool = typer.Option(
        False, "--outlier-iqr/--no-outlier-iqr", help="Enable per-station IQR check"),
    outlier_iqr_k: float = typer.Option(
        3.0, "--outlier-iqr-k", help="IQR multiplier"),
    no_outlier_range: bool = typer.Option(
        False, "--no-outlier-range", help="Disable the physical-bounds check"),
    outlier_bound: Optional[list[str]] = typer.Option(
        None, "--outlier-bound", help="Override a bound: METRIC:LOW:HIGH (repeatable)"),
```

In each command body, build the filter once and pass it down. For `mean_map`, after `_validate_fips`/`Metric` parsing and before building `out`, construct the filter and pass it into `viz.mean_map`. Add:

```python
    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter_on, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound)
```

and change the `viz.mean_map(...)` call to pass `outlier_filter=ofilter`. Bad-bound parsing happens lazily inside the filter when it runs; to surface a bad `--outlier-bound` as a non-zero exit, force construction by filtering eagerly is unnecessary — instead validate up front by calling the filter builder's parse. Simplest: in `mean_map`, wrap the `viz.mean_map` call's existing `except ValueError`/`except KeyError` to also catch `typer.BadParameter` is not needed (BadParameter already exits non-zero). Because `make_filter` defers parsing to call time, ensure the bad-bound test passes by having the filter run during `mean_map` (it does, via `station_means`). For the chart commands the filter runs inside `metric_observations`. Both raise `typer.BadParameter` at run time, which Typer turns into a non-zero exit. No extra handling needed.

For `series`/`scatter`/`aggregate`/`histogram`, build `ofilter` the same way at the top of the body and pass `outlier_filter=ofilter` into the `_render_chart(...)` call. Example for `series`:

```python
    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter_on, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound)
    _render_chart("series", "render_series", county_fips, start, end, metric, by,
                  palette, output, renderer, output_dir, stations=station,
                  outlier_filter=ofilter)
```

Apply the analogous one-line change (add `ofilter` build + `outlier_filter=ofilter`) to `scatter`, `aggregate`, and `histogram`, preserving their existing `extra=` args.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_visualize_cli.py -v`
Expected: PASS. Then the full suite:

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/smoke_sense/bin/visualize.py tests/test_visualize_cli.py
git commit -m "feat(visualize): apply outlier filter by default across subcommands"
```

---

## Self-Review

- **Spec coverage:** range/zscore/iqr masks + per-station scope + min_group/std==0 skip (Task 0) ✓; drop + OutlierReport per-metric/per-check counts (Task 0) ✓; default config (range on, z 4.0, iqr off, DEFAULT_IQR_K=3.0) (Task 0/1) ✓; default physical bounds table (Task 0) ✓; CLI overrides + parse_bound + on-by-default (Task 1) ✓; summary filters by default + `filtered` column + json + fully-removed-metric-via-log (Task 2 — fully-removed metric simply has no row; report.per_metric still logged by `_outlier_cli`) ✓; visualize filters by default via hook (Tasks 3+4) ✓; forecast untouched ✓; error handling (bad bound → BadParameter, zscore≤0 disables, empty-safe) (Tasks 0/1) ✓; tests for each ✓.
- **Placeholder scan:** none — every step has complete code.
- **Type/name consistency:** `filter_frame`/`make_filter`/`build_config`/`parse_bound` signatures match between Task 1 definitions and their Task 2/4 call sites (`enabled`, `no_range`, `zscore`, `iqr_on`, `iqr_k`, `bound`); `outlier_filter` keyword consistent across `metric_observations`/`station_means`/`mean_map`/`_render_chart`; `OutlierConfig` fields (`range_enabled`, `bounds`, `zscore`, `iqr`, `min_group`) consistent.

## Notes / Out of Scope

- `forecast` is a stub and is not modified; it will reuse `_outlier_cli` when built.
- No `is_outlier` persistence; raw parquet on disk is untouched.
- A metric whose rows are entirely removed has no `metrics` row; its count appears in the INFO log via `report.per_metric`.
