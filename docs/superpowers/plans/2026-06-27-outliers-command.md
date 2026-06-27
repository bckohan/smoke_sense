# Outliers Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `outliers` command that runs the existing outlier filter (with its CLI overrides) and prints, per county, the stations ranked by the fraction of their readings flagged.

**Architecture:** Extract the mask-combining out of `filter_outliers` into `_evaluate_checks`, reuse it for a new pure `station_outlier_counts`; add a `config_from_flags` helper to the CLI plumbing; add a thin `outliers` Typer command that reads data, drops excluded stations, ranks, and prints.

**Tech Stack:** Python 3.12, pandas, Typer, Rich, pytest.

Spec: `docs/superpowers/specs/2026-06-27-outliers-command-design.md`

---

### Task 1: Core — `station_outlier_counts` + `config_from_flags`

**Goal:** Add a per-station outlier aggregation (reusing the existing checks) and a flags→config helper, without changing `filter_outliers` behavior.

**Files:**
- Modify: `src/smoke_sense/outliers.py`
- Modify: `src/smoke_sense/bin/_outlier_cli.py`
- Test: `tests/test_outliers.py`
- Test: `tests/test_outlier_cli.py`

**Acceptance Criteria:**
- [ ] `station_outlier_counts(df, config)` returns `station_id, readings, flagged, fraction`; only `flagged > 0`; sorted by `fraction` desc then `station_id`; empty frame → empty with those columns.
- [ ] `filter_outliers` behavior is unchanged (existing tests pass).
- [ ] `config_from_flags(...)` builds an `OutlierConfig` from the flags; bad bound spec → `typer.BadParameter`.

**Verify:** `uv run pytest tests/test_outliers.py tests/test_outlier_cli.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_outliers.py` (it has `_df(rows)` where rows are `(station_id, Metric, value)`):

```python
def test_station_outlier_counts_ranks_by_fraction():
    df = _df([
        ("s1", Metric.PM2_5, 10.0),
        ("s1", Metric.PM2_5, 5000.0),   # range outlier (> 1000)
        ("s2", Metric.PM2_5, 10.0),
        ("s2", Metric.PM2_5, 11.0),
        ("s2", Metric.PM2_5, 5000.0),   # range outlier
    ])
    out = outliers.station_outlier_counts(df)
    assert out["station_id"].tolist() == ["s1", "s2"]   # 0.5 > 0.333
    assert out["readings"].tolist() == [2, 3]
    assert out["flagged"].tolist() == [1, 1]
    assert out["fraction"].iloc[0] == 0.5


def test_station_outlier_counts_only_flagged_stations():
    df = _df([("s1", Metric.PM2_5, 10.0), ("s2", Metric.PM2_5, 5000.0)])
    out = outliers.station_outlier_counts(df)
    assert out["station_id"].tolist() == ["s2"]


def test_station_outlier_counts_empty():
    out = outliers.station_outlier_counts(_df([]).iloc[0:0])
    assert out.empty
    assert list(out.columns) == ["station_id", "readings", "flagged", "fraction"]
```

Add to `tests/test_outlier_cli.py` (it imports `_outlier_cli as oc`, `Metric`, `pytest`, `typer`):

```python
def test_config_from_flags_maps_overrides():
    cfg = oc.config_from_flags(no_range=False, zscore=2.0, iqr_on=True, iqr_k=4.0,
                               bound=["PM2.5:0:500"], exclude=["s9"])
    assert cfg.zscore == 2.0
    assert cfg.iqr == 4.0
    assert cfg.bounds[Metric.PM2_5] == (0.0, 500.0)
    assert cfg.exclude_stations == frozenset({"s9"})


def test_config_from_flags_bad_bound():
    with pytest.raises(typer.BadParameter):
        oc.config_from_flags(no_range=False, zscore=None, iqr_on=False, iqr_k=3.0,
                             bound=["PM2.5:bad"], exclude=None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_outliers.py -k station_outlier tests/test_outlier_cli.py -k config_from_flags -v`
Expected: FAIL — `station_outlier_counts` / `config_from_flags` don't exist.

- [ ] **Step 3: Refactor `filter_outliers` and add `station_outlier_counts`**

In `src/smoke_sense/outliers.py`, replace the body of `filter_outliers` (the check-assembly + combine loop) by extracting it into `_evaluate_checks`, and add the new function + a columns constant. Concretely:

Add near the top (after `DEFAULT_IQR_K`/constants is fine):

```python
_STATION_OUTLIER_COLUMNS = ["station_id", "readings", "flagged", "fraction"]
```

Add this function just above `filter_outliers`:

```python
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
```

Then change `filter_outliers` to use it (keeping identical output):

```python
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
```

Add the new aggregation (place after `filter_outliers`):

```python
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
```

- [ ] **Step 4: Add `config_from_flags` and refactor `filter_frame`**

In `src/smoke_sense/bin/_outlier_cli.py`, add this function (place after `build_config`):

```python
def config_from_flags(*, no_range: bool, zscore: Optional[float], iqr_on: bool,
                      iqr_k: float, bound: Optional[list[str]],
                      exclude: Optional[list[str]] = None) -> OutlierConfig:
    """Build an OutlierConfig from raw CLI flag values (parses --outlier-bound)."""
    parsed: list[tuple[Metric, tuple[float, float]]] = []
    for spec in (bound or []):
        try:
            parsed.append(parse_bound(spec))
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    return build_config(no_range=no_range, zscore=zscore,
                        iqr=(iqr_k if iqr_on else None), bounds=parsed,
                        exclude_stations=exclude)
```

Then refactor `filter_frame` to reuse it (no behavior change):

```python
def filter_frame(df: pd.DataFrame, *, enabled: bool, no_range: bool,
                 zscore: Optional[float], iqr_on: bool, iqr_k: float,
                 bound: Optional[list[str]],
                 exclude: Optional[list[str]] = None
                 ) -> tuple[pd.DataFrame, OutlierReport]:
    """Apply the outlier filter to `df` per the CLI flags; log removals."""
    if not enabled:
        return df, OutlierReport()
    cfg = config_from_flags(no_range=no_range, zscore=zscore, iqr_on=iqr_on,
                            iqr_k=iqr_k, bound=bound, exclude=exclude)
    clean, report = filter_outliers(df, cfg)
    if report.total:
        logger.info("filtered %d outlier rows %s", report.total, report.per_metric)
    return clean, report
```

- [ ] **Step 5: Run the targeted tests**

Run: `uv run pytest tests/test_outliers.py tests/test_outlier_cli.py -v`
Expected: PASS (including all pre-existing tests — `filter_outliers` behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/smoke_sense/outliers.py src/smoke_sense/bin/_outlier_cli.py \
        tests/test_outliers.py tests/test_outlier_cli.py
git commit -m "feat(outliers): station_outlier_counts + config_from_flags"
```

---

### Task 2: `outliers` CLI command

**Goal:** Add the `smoke-sense outliers` command and register it.

**Files:**
- Create: `src/smoke_sense/bin/outliers.py`
- Modify: `src/smoke_sense/bin/__init__.py`
- Test: `tests/test_outliers_command.py`

**Acceptance Criteria:**
- [ ] `outliers <fips> --start ...` prints a per-county table of flagged stations.
- [ ] `--json` emits `{fips: {"stations": [{station_id, readings, flagged, fraction}]}}`.
- [ ] `--limit` truncates; `--exclude-station` removes a station from the list; bad `--outlier-bound` exits non-zero; multi-FIPS → key per county; no flagged stations → "no data", exit 0.

**Verify:** `uv run pytest tests/test_outliers_command.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_outliers_command.py`:

```python
import json

import pandas as pd
import pytest
from typer.testing import CliRunner

from smoke_sense import store
from smoke_sense.bin import app
from smoke_sense.data import Metric

runner = CliRunner()


def _row(ts, value, station):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"), "county_fips": "06037",
        "station_id": station, "metric": Metric.PM2_5.value, "value": value,
        "aqi": pd.NA, "agg_window": 10, "source": "purpleair",
    }


def _seed(tmp_path, fips="06037"):
    rows = [_row(f"2026-06-16T0{i}:00:00", v, "s1")
            for i, v in enumerate([10, 11, 9, 8, 12])]
    rows.append(_row("2026-06-16T09:00:00", 5000.0, "s1"))   # range outlier
    rows += [_row("2026-06-16T01:00:00", 10.0, "s2"),
             _row("2026-06-16T02:00:00", 11.0, "s2")]
    df = pd.DataFrame([{**r, "county_fips": fips} for r in rows])
    store.write(tmp_path, fips, df)


def test_outliers_table(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "s1" in result.output


def test_outliers_json_shape(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["06037"]
    sids = [s["station_id"] for s in payload["stations"]]
    assert "s1" in sids and "s2" not in sids       # s2 has no flagged readings
    s1 = next(s for s in payload["stations"] if s["station_id"] == "s1")
    assert s1["flagged"] == 1 and s1["readings"] == 6
    assert s1["fraction"] == pytest.approx(1 / 6)


def test_outliers_limit(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json", "--limit", "1"])
    payload = json.loads(result.output)["06037"]
    assert len(payload["stations"]) == 1


def test_outliers_exclude_station(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json", "--exclude-station", "s1"])
    payload = json.loads(result.output)["06037"]
    assert "s1" not in [s["station_id"] for s in payload["stations"]]


def test_outliers_bad_bound_exits_nonzero(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--outlier-bound", "PM2.5:bad"])
    assert result.exit_code != 0


def test_outliers_no_flagged_message(tmp_path):
    df = pd.DataFrame([_row("2026-06-16T01:00:00", 10.0, "s2"),
                       _row("2026-06-16T02:00:00", 11.0, "s2")])
    store.write(tmp_path, "06037", df)
    result = runner.invoke(app, [
        "outliers", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert "no data" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_outliers_command.py -v`
Expected: FAIL — no `outliers` command registered.

- [ ] **Step 3: Create the command**

Create `src/smoke_sense/bin/outliers.py`:

```python
"""`smoke-sense outliers` — list stations ranked by fraction of flagged readings."""

from __future__ import annotations

import json as _json
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from .. import outliers as outliers_core
from .. import store
from . import _outlier_cli

console = Console()


def _render(fips: str, ranked) -> None:
    if ranked.empty:
        console.print(f"[yellow]no data for {fips}[/]")
        return
    table = Table(title=f"{fips} — outlier stations")
    for col in ("#", "station_id", "readings", "flagged", "% flagged"):
        table.add_column(col)
    for i, (_, row) in enumerate(ranked.iterrows(), start=1):
        table.add_row(str(i), str(row["station_id"]), str(int(row["readings"])),
                      str(int(row["flagged"])), f"{row['fraction'] * 100:.1f}%")
    console.print(table)


def outliers(
    county_fips: List[str] = typer.Argument(..., help="One or more 5-digit county FIPS codes"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"),
    output: Path = typer.Option(Path("./data"), help="Data directory"),
    json: bool = typer.Option(False, "--json", help="Emit JSON instead of tables"),
    limit: int = typer.Option(10, "--limit", help="Max stations to list (0 = all)"),
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
    exclude_station: Optional[List[str]] = typer.Option(
        None, "--exclude-station",
        help="Drop a station from consideration (repeatable)"),
) -> None:
    """List stations ranked by the fraction of their readings flagged as outliers."""
    for fips in county_fips:
        if not (len(fips) == 5 and fips.isdigit()):
            raise typer.BadParameter(f"county FIPS must be 5-digit, got {fips!r}")

    start_date = start.date()
    end_date = end.date() if end else date.today()
    excluded = set(exclude_station or [])
    cfg = _outlier_cli.config_from_flags(
        no_range=no_outlier_range, zscore=outlier_zscore, iqr_on=outlier_iqr,
        iqr_k=outlier_iqr_k, bound=outlier_bound, exclude=None)

    payload: dict = {}
    for fips in county_fips:
        df = store.read_range(output, fips, start_date, end_date)
        if excluded and not df.empty:
            df = df[~df["station_id"].astype(str).isin(excluded)]
        ranked = outliers_core.station_outlier_counts(df, cfg)
        if limit and limit > 0:
            ranked = ranked.head(limit)
        if json:
            payload[fips] = {"stations": [
                {"station_id": r["station_id"], "readings": int(r["readings"]),
                 "flagged": int(r["flagged"]), "fraction": float(r["fraction"])}
                for _, r in ranked.iterrows()
            ]}
        else:
            _render(fips, ranked)

    if json:
        typer.echo(_json.dumps(payload))
```

- [ ] **Step 4: Register the command**

In `src/smoke_sense/bin/__init__.py`, add `outliers` to the import line and register it:

```python
from . import credentials, fetch, forecast, outliers, rank, summary, visualize

app = Typer()

app.command()(fetch.fetch)
app.command()(forecast.forecast)
app.command()(summary.summary)
app.command()(rank.rank)
app.command()(outliers.outliers)
app.add_typer(credentials.app, name="credentials")
app.add_typer(visualize.app, name="visualize")
```

- [ ] **Step 5: Run the targeted tests**

Run: `uv run pytest tests/test_outliers_command.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
git add src/smoke_sense/bin/outliers.py src/smoke_sense/bin/__init__.py \
        tests/test_outliers_command.py
git commit -m "feat(outliers): smoke-sense outliers command"
```

---

## Self-Review

**Spec coverage:**
- Rank by % flagged, only flagged>0, sorted → Task 1 `station_outlier_counts` + tests. ✓
- `filter_outliers` unchanged via `_evaluate_checks` → Task 1 Step 3 (existing tests pass). ✓
- `config_from_flags` + `filter_frame` refactor → Task 1 Step 4 + tests. ✓
- Command: multi-FIPS per-county, `--limit`, `--json` shape, override flags, no `--no-outlier-filter` → Task 2. ✓
- `--exclude-station` pre-drops (not 100%-flagged) → Task 2 Step 3 + `test_outliers_exclude_station`. ✓
- bad bound → BadParameter; no-data message → Task 2 tests. ✓
- registration → Task 2 Step 4. ✓

**Placeholder scan:** No TBD/TODO; every code step is complete. ✓

**Type consistency:** `_evaluate_checks(df, config) -> (Series, dict)` used by both `filter_outliers` and `station_outlier_counts`. `station_outlier_counts` returns `_STATION_OUTLIER_COLUMNS` consumed by the command's `_render`/JSON. `config_from_flags(...)` signature matches both the command call (`exclude=None`) and `filter_frame`'s call. Consistent. ✓
