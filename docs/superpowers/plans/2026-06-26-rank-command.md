# Rank-Stations Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a top-level `smoke-sense rank` command that lists a county's stations ordered by an aggregation (min/max/mean) of a chosen metric, ascending or descending.

**Architecture:** A new pure module `ranking.py` aggregates per station and sorts; a new Typer command `bin/rank.py` reads observations via the existing `visualize.metric_observations` (so the outlier filter + `--exclude-station` come for free), ranks, and renders a Rich table or JSON. The command is registered in `bin/__init__.py`.

**Tech Stack:** Python 3.12, pandas, Typer, Rich, pytest.

Spec: `docs/superpowers/specs/2026-06-26-rank-command-design.md`

---

### Task 1: Pure `ranking.py` module

**Goal:** Implement `rank_stations` — group observations per station, aggregate, sort, limit.

**Files:**
- Create: `src/smoke_sense/ranking.py`
- Test: `tests/test_ranking.py`

**Acceptance Criteria:**
- [ ] `rank_stations(obs, column=, agg=, descending=, limit=)` returns columns `station_id, value, count`.
- [ ] `agg` of `"min"`/`"max"`/`"mean"` computes correctly per station.
- [ ] Default `descending=True` sorts highest-first; `descending=False` sorts lowest-first; ties are stable (station_id ascending).
- [ ] `limit > 0` truncates to that many; `limit <= 0` returns all.
- [ ] Rows with null `column` are dropped; `count` reflects non-null observations.
- [ ] Empty `obs` (or all-null `column`) → empty frame with the three columns.

**Verify:** `uv run pytest tests/test_ranking.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ranking.py`:

```python
import pandas as pd

from smoke_sense import ranking


def _obs(rows):
    """rows: list of (station_id, value, aqi)."""
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-06-16T01:00:00"] * len(rows), utc=True),
        "station_id": [r[0] for r in rows],
        "value": [r[1] for r in rows],
        "aqi": pd.array([r[2] for r in rows], dtype="Int16"),
    })


def test_rank_mean_desc_default():
    obs = _obs([
        ("s1", 10.0, 1), ("s1", 20.0, 3),   # mean 15
        ("s2", 30.0, 5), ("s2", 50.0, 7),   # mean 40
        ("s3", 5.0, 1),                      # mean 5
    ])
    out = ranking.rank_stations(obs, column="value", agg="mean")
    assert out["station_id"].tolist() == ["s2", "s1", "s3"]
    assert out["value"].tolist() == [40.0, 15.0, 5.0]
    assert out["count"].tolist() == [2, 2, 1]


def test_rank_ascending():
    obs = _obs([("s1", 10.0, 1), ("s2", 30.0, 5), ("s3", 5.0, 1)])
    out = ranking.rank_stations(obs, column="value", agg="mean", descending=False)
    assert out["station_id"].tolist() == ["s3", "s1", "s2"]


def test_rank_min_and_max():
    obs = _obs([("s1", 10.0, 1), ("s1", 20.0, 3), ("s2", 30.0, 5)])
    mins = ranking.rank_stations(obs, column="value", agg="min", descending=False)
    assert mins["value"].tolist() == [10.0, 30.0]
    maxs = ranking.rank_stations(obs, column="value", agg="max")
    assert maxs["value"].tolist() == [30.0, 20.0]


def test_rank_ties_stable_by_station_id():
    obs = _obs([("s3", 10.0, 1), ("s1", 10.0, 1), ("s2", 10.0, 1)])
    out = ranking.rank_stations(obs, column="value", agg="mean")
    # equal means -> stations in ascending station_id order
    assert out["station_id"].tolist() == ["s1", "s2", "s3"]


def test_rank_limit_truncates():
    obs = _obs([("s1", 10.0, 1), ("s2", 30.0, 5), ("s3", 5.0, 1)])
    out = ranking.rank_stations(obs, column="value", agg="mean", limit=2)
    assert out["station_id"].tolist() == ["s2", "s1"]


def test_rank_limit_zero_returns_all():
    obs = _obs([("s1", 10.0, 1), ("s2", 30.0, 5), ("s3", 5.0, 1)])
    out = ranking.rank_stations(obs, column="value", agg="mean", limit=0)
    assert len(out) == 3


def test_rank_drops_nulls_and_counts():
    obs = _obs([("s1", 10.0, 1), ("s1", None, 3), ("s2", 30.0, 5)])
    out = ranking.rank_stations(obs, column="value", agg="mean")
    s1 = out[out["station_id"] == "s1"].iloc[0]
    assert s1["count"] == 1            # the null value row excluded
    assert s1["value"] == 10.0


def test_rank_by_aqi_column():
    obs = _obs([("s1", 10.0, 20), ("s2", 30.0, 80)])
    out = ranking.rank_stations(obs, column="aqi", agg="max")
    assert out["station_id"].tolist() == ["s2", "s1"]
    assert out["value"].tolist() == [80.0, 20.0]


def test_rank_empty():
    obs = _obs([("s1", 10.0, 1)]).iloc[0:0]
    out = ranking.rank_stations(obs, column="value", agg="mean")
    assert list(out.columns) == ["station_id", "value", "count"]
    assert out.empty


def test_rank_all_null_column():
    obs = _obs([("s1", None, 1), ("s2", None, 5)])
    out = ranking.rank_stations(obs, column="value", agg="mean")
    assert out.empty
    assert list(out.columns) == ["station_id", "value", "count"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ranking.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smoke_sense.ranking'`.

- [ ] **Step 3: Implement `ranking.py`**

Create `src/smoke_sense/ranking.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ranking.py -v`
Expected: PASS (all 10)

- [ ] **Step 5: Commit**

```bash
git add src/smoke_sense/ranking.py tests/test_ranking.py
git commit -m "feat(rank): pure rank_stations module"
```

---

### Task 2: `rank` CLI command

**Goal:** Add the `smoke-sense rank` Typer command and register it.

**Files:**
- Create: `src/smoke_sense/bin/rank.py`
- Modify: `src/smoke_sense/bin/__init__.py`
- Test: `tests/test_rank_cli.py`

**Acceptance Criteria:**
- [ ] `rank <fips> --metric <m>` prints a Rich table (default desc, limit 10).
- [ ] `--json` emits `{fips: {metric, by, agg, order, stations:[{station_id, value, count}]}}`.
- [ ] `--asc` reverses order; `--limit` truncates; bad `--agg` exits non-zero.
- [ ] `--exclude-station` removes a station from the ranking.
- [ ] Multiple FIPS → one table per county; a no-data county prints "no data", exit 0.

**Verify:** `uv run pytest tests/test_rank_cli.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rank_cli.py`:

```python
import json

import pandas as pd
from typer.testing import CliRunner

from smoke_sense import store
from smoke_sense.bin import app
from smoke_sense.data import Metric

runner = CliRunner()


def _row(ts, value, station, aqi=pd.NA, metric=Metric.PM2_5, source="purpleair", agg=10):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037", "station_id": station,
        "metric": metric.value, "value": value,
        "aqi": aqi, "agg_window": agg, "source": source,
    }


def _seed(tmp_path, fips="06037"):
    rows = [
        _row("2026-06-16T01:00:00", 10.0, "s1", aqi=20),
        _row("2026-06-16T02:00:00", 20.0, "s1", aqi=40),
        _row("2026-06-16T01:00:00", 50.0, "s2", aqi=90),
        _row("2026-06-16T01:00:00", 5.0, "s3", aqi=10),
    ]
    df = pd.DataFrame([{**r, "county_fips": fips} for r in rows])
    store.write(tmp_path, fips, df)


def test_rank_table_default(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--output", str(tmp_path)])
    assert result.exit_code == 0, result.output
    # s2 (mean 50) ranks above s1 (mean 15) above s3 (mean 5) by default desc
    assert result.output.index("s2") < result.output.index("s1") < result.output.index("s3")


def test_rank_json_shape(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--agg", "mean", "--json", "--output", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["06037"]
    assert payload["metric"] == "PM2.5"
    assert payload["by"] == "value"
    assert payload["agg"] == "mean"
    assert payload["order"] == "desc"
    stations = payload["stations"]
    assert [s["station_id"] for s in stations] == ["s2", "s1", "s3"]
    assert stations[0]["value"] == 50.0
    assert stations[1]["count"] == 2


def test_rank_ascending(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--asc", "--json", "--output", str(tmp_path)])
    payload = json.loads(result.output)["06037"]
    assert payload["order"] == "asc"
    assert [s["station_id"] for s in payload["stations"]] == ["s3", "s1", "s2"]


def test_rank_limit(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--limit", "1", "--json", "--output", str(tmp_path)])
    payload = json.loads(result.output)["06037"]
    assert [s["station_id"] for s in payload["stations"]] == ["s2"]


def test_rank_bad_agg(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--agg", "median", "--output", str(tmp_path)])
    assert result.exit_code != 0


def test_rank_excludes_station(tmp_path):
    _seed(tmp_path)
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--exclude-station", "s2", "--json",
        "--output", str(tmp_path)])
    payload = json.loads(result.output)["06037"]
    assert "s2" not in [s["station_id"] for s in payload["stations"]]


def test_rank_multi_county_json(tmp_path):
    _seed(tmp_path, "06037")
    _seed(tmp_path, "06059")
    result = runner.invoke(app, [
        "rank", "06037", "06059", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--json", "--output", str(tmp_path)])
    payload = json.loads(result.output)
    assert set(payload) == {"06037", "06059"}


def test_rank_no_data_message(tmp_path):
    result = runner.invoke(app, [
        "rank", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--metric", "PM2.5", "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert "no data" in result.output


def test_rank_invalid_fips(tmp_path):
    result = runner.invoke(app, [
        "rank", "6037", "--start", "2026-06-16", "--metric", "PM2.5",
        "--output", str(tmp_path)])
    assert result.exit_code != 0
    assert "5-digit" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rank_cli.py -v`
Expected: FAIL — the `rank` command does not exist (Typer reports no such command / nonzero exit).

- [ ] **Step 3: Implement `bin/rank.py`**

Create `src/smoke_sense/bin/rank.py`:

```python
"""`smoke-sense rank` — list stations ordered by an aggregate of a metric."""

from __future__ import annotations

import json as _json
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from .. import ranking
from .. import visualize as viz
from ..data import Metric
from . import _outlier_cli

console = Console()

_AGGS = ("min", "max", "mean")


def _render(fips: str, metric: Metric, by: str, agg: str, order: str,
            ranked: pd.DataFrame) -> None:
    if ranked.empty:
        console.print(
            f"[yellow]no data for {fips}/{metric.value}[/]")
        return
    table = Table(title=f"{fips} — {metric.value} ({by}) by {agg} [{order}]")
    table.add_column("#")
    table.add_column("station_id")
    table.add_column(agg)
    table.add_column("count")
    for i, (_, row) in enumerate(ranked.iterrows(), start=1):
        table.add_row(str(i), str(row["station_id"]),
                      f"{row['value']:g}", str(int(row["count"])))
    console.print(table)


def rank(
    county_fips: List[str] = typer.Argument(..., help="One or more 5-digit county FIPS codes"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"),
    metric: str = typer.Option(..., "--metric", help="Metric to rank by"),
    agg: str = typer.Option("mean", "--agg", help="Aggregation: min|max|mean"),
    by: str = typer.Option("value", "--by", help="Rank by raw value or AQI [value|aqi]"),
    descending: bool = typer.Option(
        True, "--desc/--asc", help="Sort highest-first (default) or lowest-first"),
    limit: int = typer.Option(10, "--limit", help="Max stations to list (0 = all)"),
    output: Path = typer.Option(Path("./data"), help="Data directory"),
    json: bool = typer.Option(False, "--json", help="Emit JSON instead of tables"),
    outlier_filter: bool = typer.Option(
        True, "--outlier-filter/--no-outlier-filter",
        help="Drop likely-erroneous readings before ranking"),
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
        help="Drop all rows from this station ID (repeatable)"),
) -> None:
    """List stations ordered by an aggregate of a metric, per county."""
    for fips in county_fips:
        if not (len(fips) == 5 and fips.isdigit()):
            raise typer.BadParameter(f"county FIPS must be 5-digit, got {fips!r}")
    if agg not in _AGGS:
        raise typer.BadParameter(f"--agg must be one of {', '.join(_AGGS)}, got {agg!r}")
    try:
        chosen = Metric(metric)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        column = viz.resolve_by(chosen, by)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    start_date = start.date()
    end_date = end.date() if end else date.today()
    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound, exclude=exclude_station)

    order = "desc" if descending else "asc"
    payload: dict = {}
    for fips in county_fips:
        obs = viz.metric_observations(
            output, fips, start_date, end_date, chosen, outlier_filter=ofilter)
        ranked = ranking.rank_stations(
            obs, column=column, agg=agg, descending=descending, limit=limit)
        if json:
            payload[fips] = {
                "metric": chosen.value, "by": by, "agg": agg, "order": order,
                "stations": [
                    {"station_id": r["station_id"],
                     "value": float(r["value"]),
                     "count": int(r["count"])}
                    for _, r in ranked.iterrows()
                ],
            }
        else:
            _render(fips, chosen, by, agg, order, ranked)

    if json:
        typer.echo(_json.dumps(payload))
```

- [ ] **Step 4: Register the command**

Modify `src/smoke_sense/bin/__init__.py` to import and register `rank`:

```python
from typer import Typer

from . import credentials, fetch, forecast, rank, summary, visualize

app = Typer()

app.command()(fetch.fetch)
app.command()(forecast.forecast)
app.command()(summary.summary)
app.command()(rank.rank)
app.add_typer(credentials.app, name="credentials")
app.add_typer(visualize.app, name="visualize")
```

- [ ] **Step 5: Run the targeted tests**

Run: `uv run pytest tests/test_rank_cli.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
git add src/smoke_sense/bin/rank.py src/smoke_sense/bin/__init__.py tests/test_rank_cli.py
git commit -m "feat(rank): smoke-sense rank command"
```

---

## Self-Review

**Spec coverage:**
- New top-level `rank`, registered in `bin/__init__.py` → Task 2, Steps 3–4. ✓
- `--desc/--asc` default desc → Task 2 signature; `order` in JSON. ✓
- `--limit` default 10, `0` = all → Task 1 (`rank_stations` limit logic) + Task 2 default. ✓
- Multiple FIPS, per-county tables → Task 2 loop + `test_rank_multi_county_json`. ✓
- `--agg min|max|mean` default mean, validated → Task 2 `_AGGS` check + `test_rank_bad_agg`. ✓
- `--by value|aqi` via `resolve_by` → Task 2; `test_rank_by_aqi_column` covers the column path in Task 1. ✓
- Outlier filter + `--exclude-station` on by default → Task 2 reuses `_outlier_cli.make_filter`; `test_rank_excludes_station`. ✓
- `rank_stations` (agg/order/ties/limit/null-drop/empty) → Task 1 tests. ✓
- No data → message (table) / empty list (JSON), exit 0 → `test_rank_no_data_message`. ✓
- JSON shape `{fips: {metric, by, agg, order, stations:[...]}}` → `test_rank_json_shape`. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows full code. ✓

**Type consistency:** `rank_stations(obs, *, column, agg, descending, limit)` defined in Task 1 is called with exactly those keywords in Task 2. Result columns `station_id, value, count` produced in Task 1 and consumed (by name) in Task 2's `_render` and JSON builder. `resolve_by` (from `visualize`) returns the `column` string passed to `rank_stations`. Consistent. ✓
