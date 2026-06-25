# Data Summary Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `smoke-sense summary` command that reports coverage/gaps, a source/cadence/pollutant breakdown, and per-pollutant station + value/AQI stats for stored data over a fips + time range, as rich tables or JSON.

**Architecture:** `store.read_range` reads the per-day files for a fips/range; a pure `summary.summarize` aggregates a frame into a summary dict; a thin `bin/summary.py` renders it (rich tables default, `--json` optional). Mirrors the existing `store`/`fetcher` split.

**Tech Stack:** Python 3.12, Typer, Rich, pandas, pytest.

**Reference spec:** `docs/superpowers/specs/2026-06-24-data-summary-command-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/smoke_sense/store.py` | + `read_range(data_dir, fips, start, end) -> DataFrame` |
| `src/smoke_sense/summary.py` | (new) `summarize(df, start, end) -> dict` (pure) |
| `src/smoke_sense/bin/summary.py` | (new) `summary` CLI: read store → summarize → render |
| `src/smoke_sense/bin/__init__.py` | register the summary command |

---

### Task 0: `store.read_range`

**Goal:** Read and concatenate a county's day files for the dates in a range, restricted to the window.

**Files:**
- Modify: `src/smoke_sense/store.py`
- Modify: `tests/test_store.py`

**Acceptance Criteria:**
- [ ] reads only the in-range day files and concatenates them
- [ ] restricts rows to `[start 00:00 UTC, (end+1) 00:00 UTC)`
- [ ] returns an empty (schema-shaped) frame when the county dir is absent

**Verify:** `uv run pytest tests/test_store.py -v` → all pass

**Steps:**

- [ ] **Step 1: Append tests to `tests/test_store.py`**

(`tests/test_store.py` already has `_row(ts, value, agg, source=..., station=...)`, and imports `date`, `pd`, `data`, `store`.)
```python
def test_read_range_reads_only_in_range_days(tmp_path):
    for d, v in [("2026-06-16", 1.0), ("2026-06-17", 2.0), ("2026-06-18", 3.0)]:
        store.write(tmp_path, "06037", pd.DataFrame([_row(f"{d}T01:00:00", v, 10)]))
    df = store.read_range(tmp_path, "06037", date(2026, 6, 17), date(2026, 6, 18))
    assert sorted(df["value"].tolist()) == [2.0, 3.0]


def test_read_range_empty_when_county_absent(tmp_path):
    df = store.read_range(tmp_path, "99999", date(2026, 6, 1), date(2026, 6, 2))
    assert df.empty
    assert list(df.columns) == list(data.COLUMNS)
```

- [ ] **Step 2: Run, confirm FAIL.** `uv run pytest tests/test_store.py -v`

- [ ] **Step 3: Edit `src/smoke_sense/store.py`**

Change the datetime import line `from datetime import date` to:
```python
from datetime import date, timedelta
```
Append this function at the end of the module:
```python
def read_range(data_dir: str | Path, fips: str, start: date, end: date) -> pd.DataFrame:
    """Concatenate the county's day files for dates in [start, end].

    Reads {data_dir}/{fips}/{day}.parquet for each day in the inclusive range
    that has a file, concatenates them, and returns the validated frame
    restricted to timestamps in [start 00:00 UTC, (end + 1 day) 00:00 UTC).
    Returns an empty schema frame if nothing is present.
    """
    frames = []
    day = start
    while day <= end:
        path = day_path(data_dir, fips, day)
        if path.exists():
            frames.append(data.read_parquet(path))
        day += timedelta(days=1)
    if not frames:
        return data.empty_frame()
    df = pd.concat(frames, ignore_index=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    window = (df["timestamp"] >= start_ts) & (df["timestamp"] < end_ts)
    return data.validate(df[window])
```

- [ ] **Step 4: Run, confirm PASS.** `uv run pytest tests/test_store.py -v` then `uv run pytest -q`.

- [ ] **Step 5: Stage.** `git add src/smoke_sense/store.py tests/test_store.py`

---

### Task 1: `summary.summarize`

**Goal:** Pure aggregation of a data frame into the summary dict (coverage, breakdown, per-pollutant stats).

**Files:**
- Create: `src/smoke_sense/summary.py`
- Create: `tests/test_summary.py`

**Acceptance Criteria:**
- [ ] coverage reports total/present days, missing dates, first/last timestamp, total rows
- [ ] breakdown groups by (source, pollutant, agg_window) with counts
- [ ] per-pollutant: station count, sources, value stats, AQI stats (None when all AQI null)
- [ ] empty frame → zero rows, all days missing, empty breakdown/pollutants

**Verify:** `uv run pytest tests/test_summary.py -v` → all pass

**Steps:**

- [ ] **Step 1: Create `tests/test_summary.py`**

```python
from datetime import date

import pandas as pd

from smoke_sense import data, summary
from smoke_sense.data import Pollutant


def _row(ts, pollutant, value, aqi, source, agg, station):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037",
        "station_id": station,
        "latitude": 34.0,
        "longitude": -118.2,
        "pollutant": pollutant,
        "value": value,
        "unit": "µg/m³",
        "aqi": aqi,
        "agg_window": agg,
        "source": source,
    }


def _frame(rows):
    return data.validate(pd.DataFrame(rows))


def test_summarize_empty_frame():
    s = summary.summarize(data.empty_frame(), date(2026, 6, 1), date(2026, 6, 3))
    assert s["coverage"]["total_days"] == 3
    assert s["coverage"]["days_present"] == 0
    assert s["coverage"]["days_missing"] == ["2026-06-01", "2026-06-02", "2026-06-03"]
    assert s["coverage"]["total_rows"] == 0
    assert s["coverage"]["first_timestamp"] is None
    assert s["breakdown"] == []
    assert s["pollutants"] == []


def test_summarize_coverage_breakdown_and_stats():
    rows = [
        _row("2026-06-01T01:00:00", Pollutant.PM2_5.value, 10.0, 50, "purpleair", 10, "s1"),
        _row("2026-06-01T02:00:00", Pollutant.PM2_5.value, 20.0, 70, "purpleair", 10, "s2"),
        _row("2026-06-03T01:00:00", Pollutant.O3.value, 0.04, None, "aqs", 60, "a1"),
    ]
    s = summary.summarize(_frame(rows), date(2026, 6, 1), date(2026, 6, 3))

    assert s["coverage"]["total_days"] == 3
    assert s["coverage"]["days_present"] == 2
    assert s["coverage"]["days_missing"] == ["2026-06-02"]
    assert s["coverage"]["total_rows"] == 3

    combos = {(b["source"], b["pollutant"], b["agg_window"]) for b in s["breakdown"]}
    assert combos == {("purpleair", "PM2.5", 10), ("aqs", "O3", 60)}

    pm = next(p for p in s["pollutants"] if p["pollutant"] == "PM2.5")
    assert pm["stations"] == 2
    assert pm["sources"] == ["purpleair"]
    assert pm["value"]["min"] == 10.0
    assert pm["value"]["max"] == 20.0
    assert pm["aqi"]["min"] == 50
    assert pm["aqi"]["max"] == 70

    o3 = next(p for p in s["pollutants"] if p["pollutant"] == "O3")
    assert o3["aqi"] is None  # the only O3 row had a null AQI
```

- [ ] **Step 2: Run, confirm FAIL.** `uv run pytest tests/test_summary.py -v`

- [ ] **Step 3: Create `src/smoke_sense/summary.py`**

```python
"""Summarize stored AQI data: coverage, breakdown, and per-pollutant stats.

Pure aggregation over a `data`-schema DataFrame. No I/O or CLI coupling.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def summarize(df: pd.DataFrame, start: date, end: date) -> dict:
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
            "pollutants": [],
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
        {"source": str(source), "pollutant": str(pollutant),
         "agg_window": int(agg), "rows": int(rows)}
        for (source, pollutant, agg), rows in
        df.groupby(["source", "pollutant", "agg_window"], observed=True).size().items()
    ]
    breakdown.sort(key=lambda r: (r["source"], r["pollutant"], r["agg_window"]))

    pollutants = []
    for pollutant, group in df.groupby("pollutant", observed=True):
        values = group["value"]
        aqi = group["aqi"].dropna()
        pollutants.append({
            "pollutant": str(pollutant),
            "stations": int(group["station_id"].nunique()),
            "sources": sorted({str(s) for s in group["source"].unique()}),
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
    pollutants.sort(key=lambda r: r["pollutant"])

    return {"range": rng, "coverage": coverage,
            "breakdown": breakdown, "pollutants": pollutants}
```

- [ ] **Step 4: Run, confirm PASS.** `uv run pytest tests/test_summary.py -v` then `uv run pytest -q`.

- [ ] **Step 5: Stage.** `git add src/smoke_sense/summary.py tests/test_summary.py`

---

### Task 2: `summary` CLI (`bin/summary.py`)

**Goal:** A `smoke-sense summary` command that reads the store, summarizes, and renders rich tables or JSON.

**Files:**
- Create: `src/smoke_sense/bin/summary.py`
- Modify: `src/smoke_sense/bin/__init__.py`
- Create: `tests/test_summary_cli.py`

**Acceptance Criteria:**
- [ ] `--json` emits one parseable `{fips: summary}` object
- [ ] default run renders without error and shows row counts; empty county prints a no-data line
- [ ] invalid FIPS exits non-zero

**Verify:** `uv run pytest tests/test_summary_cli.py -v` → all pass

**Steps:**

- [ ] **Step 1: Create `tests/test_summary_cli.py`**

```python
import json

import pandas as pd
from typer.testing import CliRunner

from smoke_sense import data, store
from smoke_sense.bin import app
from smoke_sense.data import Pollutant

runner = CliRunner()


def _write_day(tmp_path):
    rows = [{
        "timestamp": pd.Timestamp("2026-06-01T01:00:00", tz="UTC"),
        "county_fips": "06037", "station_id": "s1",
        "latitude": 34.0, "longitude": -118.2,
        "pollutant": Pollutant.PM2_5.value, "value": 12.0, "unit": "µg/m³",
        "aqi": 52, "agg_window": 10, "source": "purpleair",
    }]
    store.write(tmp_path, "06037", data.validate(pd.DataFrame(rows)))


def test_summary_json_output(tmp_path):
    _write_day(tmp_path)
    result = runner.invoke(
        app,
        ["summary", "06037", "--start", "2026-06-01", "--end", "2026-06-01",
         "--output", str(tmp_path), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "06037" in payload
    assert payload["06037"]["coverage"]["total_rows"] == 1
    assert payload["06037"]["pollutants"][0]["pollutant"] == "PM2.5"


def test_summary_tables_no_data(tmp_path):
    result = runner.invoke(
        app,
        ["summary", "06037", "--start", "2026-06-01",
         "--output", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "no data" in result.output


def test_summary_invalid_fips(tmp_path):
    result = runner.invoke(
        app,
        ["summary", "6037", "--start", "2026-06-01", "--output", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "5-digit" in result.output
```

- [ ] **Step 2: Run, confirm FAIL.** `uv run pytest tests/test_summary_cli.py -v`

- [ ] **Step 3: Create `src/smoke_sense/bin/summary.py`**

```python
"""`smoke-sense summary` — report stored data coverage and statistics."""

from __future__ import annotations

import json as _json
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from .. import store
from .. import summary as summary_core

console = Console()


def _render(fips: str, s: dict) -> None:
    cov = s["coverage"]
    rng = s["range"]
    if cov["total_rows"] == 0:
        console.print(f"[yellow]no data for {fips} in {rng['start']}..{rng['end']}[/]")
        return

    console.print(f"[bold]{fips}[/]  {rng['start']}..{rng['end']}")

    coverage = Table(title="Coverage")
    coverage.add_column("metric")
    coverage.add_column("value")
    coverage.add_row("days present", f"{cov['days_present']}/{cov['total_days']}")
    missing = cov["days_missing"]
    coverage.add_row(
        "days missing",
        str(len(missing)) + (f" ({', '.join(missing)})" if missing else ""),
    )
    coverage.add_row("first", cov["first_timestamp"])
    coverage.add_row("last", cov["last_timestamp"])
    coverage.add_row("rows", str(cov["total_rows"]))
    console.print(coverage)

    breakdown = Table(title="Breakdown")
    for col in ("source", "pollutant", "agg_window", "rows"):
        breakdown.add_column(col)
    for row in s["breakdown"]:
        breakdown.add_row(row["source"], row["pollutant"],
                          str(row["agg_window"]), str(row["rows"]))
    console.print(breakdown)

    pollutants = Table(title="Pollutants")
    for col in ("pollutant", "stations", "sources",
                "min", "p50", "mean", "max", "aqi min/mean/max"):
        pollutants.add_column(col)
    for row in s["pollutants"]:
        v = row["value"]
        a = row["aqi"]
        aqi_str = "-" if a is None else f"{a['min']}/{a['mean']:.0f}/{a['max']}"
        pollutants.add_row(
            row["pollutant"], str(row["stations"]), ",".join(row["sources"]),
            f"{v['min']:.1f}", f"{v['p50']:.1f}", f"{v['mean']:.1f}",
            f"{v['max']:.1f}", aqi_str,
        )
    console.print(pollutants)


def summary(
    county_fips: List[str] = typer.Argument(..., help="One or more 5-digit county FIPS codes"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"
    ),
    output: Path = typer.Option(Path("./data"), help="Data directory"),
    json: bool = typer.Option(False, "--json", help="Emit JSON instead of tables"),
) -> None:
    """Summarize stored AQI data for the given counties and time range."""
    for fips in county_fips:
        if not (len(fips) == 5 and fips.isdigit()):
            raise typer.BadParameter(f"county FIPS must be 5-digit, got {fips!r}")

    start_date = start.date()
    end_date = end.date() if end else date.today()

    results = {
        fips: summary_core.summarize(
            store.read_range(output, fips, start_date, end_date), start_date, end_date
        )
        for fips in county_fips
    }

    if json:
        typer.echo(_json.dumps(results))
        return
    for fips, s in results.items():
        _render(fips, s)
```

- [ ] **Step 4: Register the command — update `src/smoke_sense/bin/__init__.py` to EXACTLY:**

```python
from typer import Typer

from . import credentials, fetch, forecast, summary, visualize

app = Typer()

app.command()(fetch.fetch)
app.command()(forecast.forecast)
app.command()(visualize.visualize)
app.command()(summary.summary)
app.add_typer(credentials.app, name="credentials")
```

- [ ] **Step 5: Run, confirm PASS.** `uv run pytest tests/test_summary_cli.py -v`, then `uv run pytest -q`, then `uv run smoke-sense summary --help` (shows `--json`, `--output`, `--start`, `--end`).

- [ ] **Step 6: Stage.** `git add src/smoke_sense/bin/summary.py src/smoke_sense/bin/__init__.py tests/test_summary_cli.py`

---

## Self-Review

**Spec coverage:**
- Read store by fips+range → Task 0 (`store.read_range`) ✓
- Coverage & gaps (present/missing days, first/last ts, rows) → Task 1 ✓
- Breakdown by source/cadence/pollutant → Task 1 ✓
- Per-pollutant stations + value (min/p25/p50/mean/p75/max) + AQI (min/mean/max, null-safe) → Task 1 ✓
- Rich tables default + `--json` → Task 2 ✓
- Multiple FIPS, `--start` required, `--end` default today, `--output` data dir, FIPS validation → Task 2 ✓
- Empty county → no-data line / total_rows 0 → Tasks 1 & 2 ✓
- Tests for read_range, summarize, CLI (json/tables/invalid) → Tasks 0–2 ✓

**Placeholder scan:** none — full code in every step.

**Type/name consistency:** `store.read_range(data_dir, fips, start, end)`, `summary.summarize(df, start, end) -> dict`, the CLI imports the core module as `summary_core` (the command function is named `summary`, the bin module is `summary`, and `bin/__init__` registers `summary.summary`). The `--json` boolean shadows the stdlib name inside the command, so the module is imported as `_json`. Summary dict keys (`coverage`, `breakdown`, `pollutants`, nested fields) match between Task 1 producer and Task 2 renderer/tests.

**Note:** read-only command; no credentials, no schema changes. AQI/value stats skip null AQI per the spec.
