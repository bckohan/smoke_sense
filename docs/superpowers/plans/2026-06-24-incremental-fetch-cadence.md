# Incremental Fetch & Cadence Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--cadence` enum, an `agg_window` schema column, a per-day Parquet store, incremental "fetch only the gaps" orchestration with `--refetch`, provider-side cadence selection, and adaptive request chunking.

**Architecture:** New `cadence.py` (enum + selection), `store.py` (per-day layout, merge, coverage), and `fetcher.py` (Typer-free orchestration). `data.py` gains an `agg_window` column. Providers become cadence-aware and chunk adaptively. `bin/fetch.py` becomes a thin CLI over `fetcher`.

**Tech Stack:** Python 3.12, Typer, pandas, requests, pytest.

**Reference spec:** `docs/superpowers/specs/2026-06-24-incremental-fetch-cadence-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/smoke_sense/cadence.py` | `Cadence` enum (`.minutes`) + `resolve_cadence` |
| `src/smoke_sense/data.py` | + `agg_window` column |
| `src/smoke_sense/store.py` | per-day file layout, `merge_day`/`write`/`coverage` |
| `src/smoke_sense/fetcher.py` | gap detection + provider calls + store writes (no Typer) |
| `src/smoke_sense/providers/base.py` | `supported_cadences`, `resolve_cadence`, `fetch(..., cadence)` |
| `src/smoke_sense/providers/aqs.py` | record `agg_window`; cadence param |
| `src/smoke_sense/providers/purpleair.py` | cadence→`average`, adaptive chunking |
| `src/smoke_sense/bin/fetch.py` | thin CLI: `--cadence`, `--refetch`, data-dir output |

---

### Task 0: Cadence enum (`cadence.py`)

**Goal:** A name-valued `Cadence` enum with `.minutes` and the `resolve_cadence` selection rule.

**Files:**
- Create: `src/smoke_sense/cadence.py`
- Create: `tests/test_cadence.py`

**Acceptance Criteria:**
- [ ] `Cadence.TEN_MIN.minutes == 10`, `Cadence.RAW.minutes == 0`, `Cadence.DAILY.minutes == 1440`
- [ ] `resolve_cadence` picks `max(supported ≤ requested)`, else `min(supported)`

**Verify:** `uv run pytest tests/test_cadence.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `tests/test_cadence.py`**

```python
import pytest

from smoke_sense.cadence import Cadence, resolve_cadence


def test_minutes_mapping():
    assert Cadence.RAW.minutes == 0
    assert Cadence.TEN_MIN.minutes == 10
    assert Cadence.THIRTY_MIN.minutes == 30
    assert Cadence.HOURLY.minutes == 60
    assert Cadence.SIX_HOURLY.minutes == 360
    assert Cadence.DAILY.minutes == 1440


def test_enum_value_is_name():
    assert Cadence.TEN_MIN.value == "TEN_MIN"


def test_resolve_exact_match():
    assert resolve_cadence([0, 10, 30, 60, 360, 1440], 10) == 10


def test_resolve_rounds_down_to_finest_not_coarser():
    # request 20 -> largest supported <= 20 is 10
    assert resolve_cadence([0, 10, 30, 60], 20) == 10


def test_resolve_fallback_when_provider_cannot_go_finer():
    # AQS supports only 60; request 10 -> fallback to min(supported) = 60
    assert resolve_cadence([60], 10) == 60


def test_resolve_raw_request():
    assert resolve_cadence([0, 10, 60], 0) == 0
```

- [ ] **Step 2: Run, confirm FAIL.** `uv run pytest tests/test_cadence.py -v`

- [ ] **Step 3: Create `src/smoke_sense/cadence.py`**

```python
"""Data cadence (averaging window) selection.

A `Cadence` is a named averaging window. Its `.minutes` value is both the
PurpleAir `average` query value and the `agg_window` recorded in the data.
"""

from __future__ import annotations

from enum import Enum


class Cadence(str, Enum):
    RAW = "RAW"            # real-time, ~2 min
    TEN_MIN = "TEN_MIN"
    THIRTY_MIN = "THIRTY_MIN"
    HOURLY = "HOURLY"
    SIX_HOURLY = "SIX_HOURLY"
    DAILY = "DAILY"

    @property
    def minutes(self) -> int:
        return _CADENCE_MINUTES[self]


_CADENCE_MINUTES: dict[Cadence, int] = {
    Cadence.RAW: 0,
    Cadence.TEN_MIN: 10,
    Cadence.THIRTY_MIN: 30,
    Cadence.HOURLY: 60,
    Cadence.SIX_HOURLY: 360,
    Cadence.DAILY: 1440,
}


def resolve_cadence(supported: list[int], requested: int) -> int:
    """Finest supported window no coarser than `requested`; else the finest.

    Returns the largest supported window <= requested (so data is never coarser
    than asked) or, if the provider cannot go that fine, its finest window.
    """
    candidates = [c for c in supported if c <= requested]
    return max(candidates) if candidates else min(supported)
```

- [ ] **Step 4: Run, confirm PASS.** `uv run pytest tests/test_cadence.py -v` then `uv run pytest -q`.

- [ ] **Step 5: Stage (do NOT commit).** `git add src/smoke_sense/cadence.py tests/test_cadence.py`

---

### Task 1: Add `agg_window` column to the schema (`data.py`)

**Goal:** Add the required `agg_window` column and update every frame producer/fixture so the suite stays green.

**Files:**
- Modify: `src/smoke_sense/data.py`
- Modify: `src/smoke_sense/providers/aqs.py`
- Modify: `src/smoke_sense/providers/purpleair.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_fetch_cli.py`
- Modify: `tests/test_data.py`

**Acceptance Criteria:**
- [ ] `agg_window` is in `COLUMNS` (Int16) and `REQUIRED_NON_NULL`
- [ ] AQS `_parse` and PurpleAir `_parse_history` emit `agg_window`
- [ ] full suite passes

**Verify:** `uv run pytest -q` → all pass

**Steps:**

- [ ] **Step 1: Add an `agg_window` assertion to `tests/test_data.py`**

Append:
```python
def test_validate_includes_agg_window(sample_rows):
    out = data.validate(sample_rows)
    assert "agg_window" in out.columns
    assert str(out["agg_window"].dtype) == "Int16"
```

- [ ] **Step 2: Update `src/smoke_sense/data.py`**

In `COLUMNS`, add `agg_window` after `aqi`:
```python
COLUMNS: dict[str, str] = {
    "timestamp": "datetime64[ns, UTC]",
    "county_fips": "string",
    "station_id": "string",
    "latitude": "float64",
    "longitude": "float64",
    "pollutant": "category",
    "value": "float64",
    "unit": "category",
    "aqi": "Int16",
    "agg_window": "Int16",
    "source": "category",
}
```
And add it to `REQUIRED_NON_NULL`:
```python
REQUIRED_NON_NULL: list[str] = [
    "timestamp",
    "county_fips",
    "station_id",
    "pollutant",
    "value",
    "agg_window",
    "source",
]
```

- [ ] **Step 3: Update `tests/conftest.py`** — add `agg_window` to the `sample_rows` dict (after `aqi`):
```python
            "aqi": [52, 58],
            "agg_window": [60, 60],
            "source": ["aqs", "aqs"],
```

- [ ] **Step 4: Update `tests/test_fetch_cli.py`** — in `_fake_frame`, add `agg_window` (after `aqi`):
```python
                    "aqi": [50],
                    "agg_window": [60],
                    "source": ["aqs"],
```

- [ ] **Step 5: Update AQS `_parse`** (`src/smoke_sense/providers/aqs.py`) — in the `pd.DataFrame({...})` built inside `_parse`, add `agg_window` (after `aqi`):
```python
                "aqi": pd.NA,
                "agg_window": 60,
                "source": "aqs",
```

- [ ] **Step 6: Update PurpleAir `_parse_history`** (`src/smoke_sense/providers/purpleair.py`) — in the per-pollutant `pd.DataFrame({...})`, add `agg_window` (after `aqi`):
```python
                    "aqi": pd.NA,
                    "agg_window": 60,
                    "source": "purpleair",
```

- [ ] **Step 7: Run, confirm PASS.** `uv run pytest -q` → all pass.

- [ ] **Step 8: Stage.** `git add src/smoke_sense/data.py src/smoke_sense/providers/aqs.py src/smoke_sense/providers/purpleair.py tests/conftest.py tests/test_fetch_cli.py tests/test_data.py`

---

### Task 2: Per-day store (`store.py`)

**Goal:** Per-day file layout with merge (finer-cadence-wins), multi-day split on write, and coverage reporting.

**Files:**
- Create: `src/smoke_sense/store.py`
- Create: `tests/test_store.py`

**Acceptance Criteria:**
- [ ] `write` splits a multi-day frame into `{data_dir}/{fips}/{date}.parquet` files
- [ ] `merge_day` keeps the finer `agg_window` per `(timestamp, station_id, pollutant, source)`
- [ ] `coverage` reports the finest `agg_window` per `(date, source)`

**Verify:** `uv run pytest tests/test_store.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `tests/test_store.py`**

```python
from datetime import date

import pandas as pd

from smoke_sense import data, store
from smoke_sense.data import Pollutant


def _row(ts, value, agg, source="purpleair", station="s1"):
    return {
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "county_fips": "06037",
        "station_id": station,
        "latitude": 34.0,
        "longitude": -118.2,
        "pollutant": Pollutant.PM2_5.value,
        "value": value,
        "unit": "µg/m³",
        "aqi": 10,
        "agg_window": agg,
        "source": source,
    }


def test_write_splits_by_day(tmp_path):
    df = pd.DataFrame([
        _row("2026-06-16T01:00:00", 1.0, 10),
        _row("2026-06-17T01:00:00", 2.0, 10),
    ])
    store.write(tmp_path, "06037", df)
    assert (tmp_path / "06037" / "2026-06-16.parquet").exists()
    assert (tmp_path / "06037" / "2026-06-17.parquet").exists()


def test_merge_day_finer_cadence_wins(tmp_path):
    coarse = pd.DataFrame([_row("2026-06-16T01:00:00", 9.9, 60)])
    store.write(tmp_path, "06037", coarse)
    fine = pd.DataFrame([_row("2026-06-16T01:00:00", 1.1, 10)])
    store.write(tmp_path, "06037", fine)
    back = data.read_parquet(tmp_path / "06037" / "2026-06-16.parquet")
    assert len(back) == 1
    assert back["agg_window"].iloc[0] == 10
    assert back["value"].iloc[0] == 1.1


def test_merge_day_keeps_coarse_when_no_finer(tmp_path):
    fine = pd.DataFrame([_row("2026-06-16T01:00:00", 1.1, 10)])
    store.write(tmp_path, "06037", fine)
    coarse = pd.DataFrame([_row("2026-06-16T01:00:00", 9.9, 60)])
    store.write(tmp_path, "06037", coarse)
    back = data.read_parquet(tmp_path / "06037" / "2026-06-16.parquet")
    assert len(back) == 1
    assert back["agg_window"].iloc[0] == 10  # finer kept


def test_coverage_reports_finest_per_day_source(tmp_path):
    df = pd.DataFrame([
        _row("2026-06-16T01:00:00", 1.0, 10, source="purpleair"),
        _row("2026-06-16T02:00:00", 2.0, 60, source="aqs", station="a1"),
    ])
    store.write(tmp_path, "06037", df)
    cov = store.coverage(tmp_path, "06037")
    assert cov[(date(2026, 6, 16), "purpleair")] == 10
    assert cov[(date(2026, 6, 16), "aqs")] == 60


def test_coverage_empty_when_no_county_dir(tmp_path):
    assert store.coverage(tmp_path, "99999") == {}
```

- [ ] **Step 2: Run, confirm FAIL.** `uv run pytest tests/test_store.py -v`

- [ ] **Step 3: Create `src/smoke_sense/store.py`**

```python
"""Per-day Parquet store with finer-cadence-wins merge and coverage queries."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from . import data

# Identity of an observation; finer agg_window wins on conflict.
_IDENTITY = ["timestamp", "station_id", "pollutant", "source"]


def day_path(data_dir: str | Path, fips: str, day: date) -> Path:
    return Path(data_dir) / fips / f"{day.isoformat()}.parquet"


def _dedup_finer_wins(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per identity, preferring the finest agg_window (0 finest)."""
    ordered = df.sort_values("agg_window", kind="stable")
    return ordered.drop_duplicates(subset=_IDENTITY, keep="first")


def merge_day(data_dir: str | Path, fips: str, day: date, df: pd.DataFrame) -> None:
    """Merge `df` into the day file, keeping the finer cadence on conflict."""
    path = day_path(data_dir, fips, day)
    frames = []
    if path.exists():
        frames.append(data.read_parquet(path))
    frames.append(df)
    combined = _dedup_finer_wins(pd.concat(frames, ignore_index=True))
    data.write_parquet(combined, path)


def write(data_dir: str | Path, fips: str, df: pd.DataFrame) -> None:
    """Validate `df`, split it by UTC day, and merge each day into its file."""
    if df.empty:
        return
    df = data.validate(df)
    days = df["timestamp"].dt.tz_convert("UTC").dt.date
    for day, group in df.groupby(days):
        merge_day(data_dir, fips, day, group)


def coverage(data_dir: str | Path, fips: str) -> dict[tuple[date, str], int]:
    """Finest `agg_window` already stored per (day, source) for a county."""
    county_dir = Path(data_dir) / fips
    result: dict[tuple[date, str], int] = {}
    if not county_dir.exists():
        return result
    for f in sorted(county_dir.glob("*.parquet")):
        day = date.fromisoformat(f.stem)
        df = data.read_parquet(f)
        for source, group in df.groupby("source", observed=True):
            result[(day, str(source))] = int(group["agg_window"].min())
    return result
```

- [ ] **Step 4: Run, confirm PASS.** `uv run pytest tests/test_store.py -v` then `uv run pytest -q`.

- [ ] **Step 5: Stage.** `git add src/smoke_sense/store.py tests/test_store.py`

---

### Task 3: Cadence-aware providers + adaptive chunking

**Goal:** Providers expose `supported_cadences`/`resolve_cadence`, accept a `cadence`, record the real `agg_window`, and (PurpleAir) chunk adaptively on over-range 400s.

**Files:**
- Modify: `src/smoke_sense/providers/base.py`
- Modify: `src/smoke_sense/providers/aqs.py`
- Modify: `src/smoke_sense/providers/purpleair.py`
- Modify: `tests/test_providers_aqs.py`
- Modify: `tests/test_providers_purpleair.py`

**Acceptance Criteria:**
- [ ] `provider.resolve_cadence` works (AQS→60, PurpleAir→requested)
- [ ] PurpleAir maps cadence to the `average` param and tags `agg_window` with it
- [ ] PurpleAir splits a too-large range (400) down to 1 day and still returns data

**Verify:** `uv run pytest tests/test_providers_aqs.py tests/test_providers_purpleair.py -v` → all pass

**Steps:**

- [ ] **Step 1: Update `src/smoke_sense/providers/base.py`**

```python
"""Provider interface and registry.

Each provider adapts a public data source to the common tidy format. Providers
register by name so the CLI can resolve `--source` values and default to all.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from ..cadence import resolve_cadence
from ..data import Pollutant

_REGISTRY: dict[str, type["AQIProvider"]] = {}


class AQIProvider(ABC):
    """Base class for air-quality data providers."""

    name: str
    supported: set[Pollutant]
    supported_cadences: list[int]

    def __init__(self, **kwargs) -> None:
        # Concrete providers accept credentials/sessions via kwargs.
        pass

    def resolve_cadence(self, requested: int) -> int:
        """Actual cadence this provider will use for a requested window."""
        return resolve_cadence(self.supported_cadences, requested)

    @abstractmethod
    def fetch(
        self,
        county_fips: str,
        start: date,
        end: date,
        pollutants: list[Pollutant],
        cadence: int = 60,
    ) -> pd.DataFrame:
        """Return a `data`-schema DataFrame for the county/range/pollutants."""
        raise NotImplementedError


def register(cls: type[AQIProvider]) -> type[AQIProvider]:
    """Class decorator registering a provider by its `name`."""
    _REGISTRY[cls.name] = cls
    return cls


def all_providers() -> list[str]:
    """Return the names of all registered providers, sorted."""
    return sorted(_REGISTRY)


def get_provider(name: str, **kwargs) -> AQIProvider:
    """Construct a registered provider by name."""
    if name not in _REGISTRY:
        raise KeyError(f"unknown provider: {name!r} (have {all_providers()})")
    return _REGISTRY[name](**kwargs)
```

- [ ] **Step 2: Update AQS** (`src/smoke_sense/providers/aqs.py`)

Add `supported_cadences = [60]` to the class (next to `supported`):
```python
    name = "aqs"
    supported = {Pollutant.PM2_5, Pollutant.PM10, Pollutant.O3}
    supported_cadences = [60]
```
Change `fetch` to accept and apply cadence (replace the `fetch` signature line and pass the resolved window into `_parse`):
```python
    def fetch(self, county_fips, start, end, pollutants, cadence: int = 60):
        wanted = [p for p in pollutants if p in self.supported]
        for p in pollutants:
            if p not in self.supported:
                warnings.warn(f"{self.name}: pollutant {p.value} not supported, skipping")
        if not wanted:
            return empty_frame()

        agg = self.resolve_cadence(cadence)
        state, county = county_fips[:2], county_fips[2:]
        frames = []
        for sub_start, sub_end in self._year_ranges(start, end):
            payload = self._request(
                {
                    "email": self.email,
                    "key": self.api_key,
                    "param": ",".join(p.aqs_code for p in wanted),
                    "bdate": sub_start.strftime("%Y%m%d"),
                    "edate": sub_end.strftime("%Y%m%d"),
                    "state": state,
                    "county": county,
                }
            )
            frames.append(self._parse(payload, county_fips, agg))
        return pd.concat(frames, ignore_index=True) if frames else empty_frame()
```
Change `_parse` to take and apply `agg`:
```python
    def _parse(self, payload: dict, county_fips: str, agg: int = 60) -> pd.DataFrame:
        """Convert an AQS sampleData payload to a common-schema frame."""
        records = payload.get("Data", [])
        if not records:
            return empty_frame()

        raw = pd.DataFrame(records)
        raw = raw[raw["parameter_code"].isin(_CODE_TO_POLLUTANT)]
        if raw.empty:
            return empty_frame()
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    raw["date_gmt"] + " " + raw["time_gmt"], utc=True
                ),
                "county_fips": county_fips,
                "station_id": raw["state_code"] + raw["county_code"] + raw["site_number"],
                "latitude": raw["latitude"].astype("float64"),
                "longitude": raw["longitude"].astype("float64"),
                "pollutant": raw["parameter_code"].map(
                    lambda c: _CODE_TO_POLLUTANT[c].value
                ),
                "value": raw["sample_measurement"].astype("float64"),
                "unit": raw["parameter_code"].map(
                    lambda c: _CODE_TO_POLLUTANT[c].unit
                ),
                "aqi": pd.NA,
                "agg_window": agg,
                "source": "aqs",
            }
        )
        df = df.dropna(subset=["value"])
        return self._add_aqi(df)
```
> The AQS `_parse` test calls `_parse(payload, county_fips="06037")` — the new `agg` defaults to 60, so it still passes.

- [ ] **Step 3: Update PurpleAir** (`src/smoke_sense/providers/purpleair.py`)

Add cadence support, parametrize `average`, thread `agg_window`, and chunk adaptively. Replace the class body from `supported` through `fetch` with:

```python
    name = "purpleair"
    supported = {Pollutant.PM2_5, Pollutant.PM10}
    supported_cadences = [0, 10, 30, 60, 360, 1440]

    def __init__(self, purpleair_key: str | None = None,
                 session: requests.Session | None = None, **kwargs) -> None:
        # Only PurpleAir's own key authenticates here; other providers'
        # credentials arrive via **kwargs and are deliberately ignored.
        self.api_key = purpleair_key
        self.session = session or requests.Session()

    def _headers(self) -> dict:
        if not self.api_key:
            raise ValueError("PurpleAir requires credentials (PURPLEAIR_API_KEY)")
        return {"X-API-Key": self.api_key}

    def _list_sensors(self, bbox) -> list[dict]:
        resp = self.session.get(
            _SENSORS_URL,
            headers=self._headers(),
            params={
                "fields": "latitude,longitude",
                "nwlng": bbox.min_lon, "nwlat": bbox.max_lat,
                "selng": bbox.max_lon, "selat": bbox.min_lat,
            },
            timeout=120,
        )
        resp.raise_for_status()
        payload = resp.json()
        fields = payload["fields"]
        return [dict(zip(fields, row)) for row in payload["data"]]

    def _get_history(self, sensor_id, start: date, end: date, average: int,
                     fields: list[str]) -> dict:
        start_ts = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        # end date is inclusive: request through the end of that day, capped at now.
        end_ts = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
        end_ts = min(end_ts, datetime.now(timezone.utc))
        resp = self.session.get(
            _HISTORY_URL.format(sensor_id=sensor_id),
            headers=self._headers(),
            params={
                "start_timestamp": int(start_ts.timestamp()),
                "end_timestamp": int(end_ts.timestamp()),
                "average": average,
                "fields": ",".join(fields),
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def _history_chunked(self, sensor_id, start: date, end: date, average: int,
                         fields: list[str]):
        """Fetch history, halving the date range on an over-range 400."""
        try:
            payload = self._get_history(sensor_id, start, end, average, fields)
            return payload.get("data", []), payload.get("fields", fields)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status == 400 and (end - start).days > 1:
                half = (end - start).days // 2
                mid = start + timedelta(days=half)
                left_data, left_fields = self._history_chunked(
                    sensor_id, start, mid, average, fields)
                right_data, right_fields = self._history_chunked(
                    sensor_id, mid, end, average, fields)
                return left_data + right_data, left_fields or right_fields
            raise

    def _parse_history(self, payload, sensor_id, lat, lon, county_fips, pollutants,
                       agg: int = 60):
        fields = payload["fields"]
        rows = payload["data"]
        if not rows:
            return empty_frame()
        raw = pd.DataFrame(rows, columns=fields)
        humidity = raw.get("humidity")

        frames = []
        for field, (pollutant, needs_correction) in _FIELD_MAP.items():
            if pollutant not in pollutants or field not in raw.columns:
                continue
            values = raw[field].astype("float64")
            if needs_correction:
                values = epa_correct_pm25(values, humidity.astype("float64"))
            part = pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(raw["time_stamp"], unit="s", utc=True),
                    "county_fips": county_fips,
                    "station_id": str(sensor_id),
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "pollutant": pollutant.value,
                    "value": values,
                    "unit": pollutant.unit,
                    "aqi": pd.NA,
                    "agg_window": agg,
                    "source": "purpleair",
                }
            ).dropna(subset=["value"]).sort_values("timestamp")
            series = part.set_index("timestamp")["value"]
            part["aqi"] = compute_aqi(series, pollutant).to_numpy()
            frames.append(part)

        return pd.concat(frames, ignore_index=True) if frames else empty_frame()

    def fetch(self, county_fips, start, end, pollutants, cadence: int = 60):
        wanted = [p for p in pollutants if p in self.supported]
        for p in pollutants:
            if p not in self.supported:
                warnings.warn(f"{self.name}: pollutant {p.value} not supported, skipping")
        if not wanted:
            return empty_frame()

        average = self.resolve_cadence(cadence)
        bbox = bbox_for_county(county_fips)
        sensors = self._list_sensors(bbox)
        # PurpleAir returns time_stamp automatically; do not request it.
        fields = ["humidity"] + [
            f for f, (p, _) in _FIELD_MAP.items() if p in wanted
        ]
        frames = []
        for sensor in sensors:
            rows, resp_fields = self._history_chunked(
                sensor["sensor_index"], start, end, average, fields)
            frames.append(
                self._parse_history(
                    {"fields": resp_fields, "data": rows},
                    sensor["sensor_index"],
                    sensor["latitude"], sensor["longitude"],
                    county_fips, wanted, average,
                )
            )
        return pd.concat(frames, ignore_index=True) if frames else empty_frame()
```

Ensure the imports at the top of `purpleair.py` include `timedelta` (the file already imports `from datetime import date, datetime, timezone`; change it to):
```python
from datetime import date, datetime, timedelta, timezone
```

- [ ] **Step 4: Update provider tests**

In `tests/test_providers_aqs.py`, the existing `_parse` calls use the default `agg`, so no change is required for them. Add a cadence test:
```python
def test_resolve_cadence_always_hourly():
    provider = EPAAQSProvider(email="a@b.com", api_key="key")
    assert provider.resolve_cadence(10) == 60
    assert provider.resolve_cadence(1440) == 60
```

In `tests/test_providers_purpleair.py`, add to the `_FakeSession.get` history branch an over-range behavior and add a chunking + cadence test. Replace the existing `_FakeSession.get` method with:
```python
    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        if url.endswith("/v1/sensors"):
            return _FakeResp(
                {"fields": ["sensor_index", "latitude", "longitude"],
                 "data": [[262253, 33.75, -118.33]]}
            )
        # history: reject ranges longer than ~1 day with a 400, like PurpleAir.
        span = params["end_timestamp"] - params["start_timestamp"]
        if span > 86400 + 3600:
            raise requests.HTTPError(response=_FakeResp({}, status_code=400))
        return _FakeResp(
            {"fields": ["time_stamp", "humidity", "pm2.5_cf_1", "pm10.0_cf_1"],
             "data": [[1781996400, 44, 1.8, 3.2]]}
        )
```
Update `_FakeResp` to carry a status code and raise on `raise_for_status`:
```python
class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self._payload
```
Add `import requests` at the top of the test file, and add a chunking test:
```python
def test_fetch_chunks_large_range_on_400():
    session = _FakeSession()
    provider = PurpleAirProvider(purpleair_key="key", session=session)
    df = provider.fetch(
        "06037", date(2026, 6, 1), date(2026, 6, 5),
        [Pollutant.PM2_5], cadence=10,
    )
    history_calls = [c for c in session.calls if "/history" in c["url"]]
    # the 4-day range was split into multiple <=1-day requests
    assert len(history_calls) > 1
    assert not df.empty
    assert (df["agg_window"] == 10).all()
```

- [ ] **Step 5: Run, confirm PASS.** `uv run pytest tests/test_providers_aqs.py tests/test_providers_purpleair.py -v` then `uv run pytest -q`.

- [ ] **Step 6: Stage.** `git add src/smoke_sense/providers/ tests/test_providers_aqs.py tests/test_providers_purpleair.py`

---

### Task 4: Orchestration (`fetcher.py`) + CLI rewrite (`bin/fetch.py`)

**Goal:** Gap-detecting orchestration with `--refetch` and always-refetch-today, plus a CLI exposing `--cadence`/`--refetch` and writing to the per-day store.

**Files:**
- Create: `src/smoke_sense/fetcher.py`
- Create: `tests/test_fetcher.py`
- Modify: `src/smoke_sense/bin/fetch.py`
- Modify: `tests/test_fetch_cli.py`

**Acceptance Criteria:**
- [ ] hybrid: covered days (cadence-or-finer) skipped; gaps fetched; current day always fetched
- [ ] `--refetch` fetches the full range regardless of coverage
- [ ] CLI writes per-day files under the data dir; `--cadence` accepted

**Verify:** `uv run pytest tests/test_fetcher.py tests/test_fetch_cli.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `tests/test_fetcher.py`**

```python
from datetime import date

import pandas as pd

from smoke_sense import data, fetcher, store
from smoke_sense.data import Pollutant


class FakeProvider:
    name = "purpleair"
    supported_cadences = [0, 10, 30, 60]

    def __init__(self):
        self.requested_ranges = []

    def resolve_cadence(self, requested):
        cands = [c for c in self.supported_cadences if c <= requested]
        return max(cands) if cands else min(self.supported_cadences)

    def fetch(self, county_fips, start, end, pollutants, cadence):
        self.requested_ranges.append((start, end))
        days = pd.date_range(start, end, freq="D", tz="UTC")
        rows = [{
            "timestamp": d + pd.Timedelta(hours=1),
            "county_fips": county_fips,
            "station_id": "s1",
            "latitude": 34.0, "longitude": -118.2,
            "pollutant": Pollutant.PM2_5.value,
            "value": 1.0, "unit": "µg/m³",
            "aqi": 5, "agg_window": cadence, "source": self.name,
        } for d in days]
        return data.validate(pd.DataFrame(rows))


def test_hybrid_skips_covered_and_fetches_gaps(tmp_path):
    # Pre-store 2026-06-16 at cadence 10 (already covered).
    existing = FakeProvider().fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                                    [Pollutant.PM2_5], 10)
    store.write(tmp_path, "06037", existing)

    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 18),
                         [Pollutant.PM2_5], 10, [p],
                         today=date(2026, 6, 20))
    # 06-16 already covered at 10 -> skipped; 06-17 and 06-18 fetched.
    fetched_days = {d for (s, e) in p.requested_ranges
                    for d in pd.date_range(s, e, freq="D").date}
    assert date(2026, 6, 16) not in fetched_days
    assert date(2026, 6, 17) in fetched_days
    assert date(2026, 6, 18) in fetched_days


def test_always_refetches_today(tmp_path):
    existing = FakeProvider().fetch("06037", date(2026, 6, 20), date(2026, 6, 20),
                                    [Pollutant.PM2_5], 10)
    store.write(tmp_path, "06037", existing)
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 20), date(2026, 6, 20),
                         [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 20))
    assert p.requested_ranges  # today re-fetched despite coverage


def test_refetch_fetches_everything(tmp_path):
    existing = FakeProvider().fetch("06037", date(2026, 6, 16), date(2026, 6, 16),
                                    [Pollutant.PM2_5], 10)
    store.write(tmp_path, "06037", existing)
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 17),
                         [Pollutant.PM2_5], 10, [p],
                         today=date(2026, 6, 30), refetch=True)
    fetched_days = {d for (s, e) in p.requested_ranges
                    for d in pd.date_range(s, e, freq="D").date}
    assert date(2026, 6, 16) in fetched_days
```

- [ ] **Step 2: Run, confirm FAIL.** `uv run pytest tests/test_fetcher.py -v`

- [ ] **Step 3: Create `src/smoke_sense/fetcher.py`**

```python
"""Fetch orchestration: gap detection, provider calls, and store writes.

No Typer/CLI coupling so the incremental logic is unit-testable.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import store


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _contiguous_ranges(days: list[date]) -> list[tuple[date, date]]:
    if not days:
        return []
    days = sorted(days)
    ranges: list[tuple[date, date]] = []
    run_start = prev = days[0]
    for d in days[1:]:
        if d == prev + timedelta(days=1):
            prev = d
        else:
            ranges.append((run_start, prev))
            run_start = prev = d
    ranges.append((run_start, prev))
    return ranges


def fetch_county(data_dir, fips, start, end, pollutants, requested_cadence,
                 providers, today, refetch=False) -> None:
    """Fetch missing days per provider and merge results into the store."""
    cov = store.coverage(data_dir, fips)
    for provider in providers:
        actual = provider.resolve_cadence(requested_cadence)
        if refetch:
            missing = _days(start, end)
        else:
            missing = [
                d for d in _days(start, end)
                if d == today
                or cov.get((d, provider.name), 10 ** 9) > actual
            ]
        for run_start, run_end in _contiguous_ranges(missing):
            df = provider.fetch(fips, run_start, run_end, pollutants, actual)
            store.write(data_dir, fips, df)
```

- [ ] **Step 4: Rewrite `src/smoke_sense/bin/fetch.py`**

```python
"""`smoke-sense fetch` — download AQI series for counties into the per-day store."""

from __future__ import annotations

import binascii
import json
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import typer
from cryptography.fernet import InvalidToken
from rich.console import Console

from .. import credentials as credentials_core
from .. import fetcher
from ..cadence import Cadence
from ..data import Pollutant
from ..providers import all_providers, get_provider
from .credentials import resolve_password

console = Console()

DEFAULT_POLLUTANTS = [Pollutant.PM2_5, Pollutant.PM10, Pollutant.O3]


def _resolve_providers(sources: list[str], creds: dict):
    """Construct provider instances for the requested source names."""
    return [get_provider(name, **creds) for name in sources]


def fetch(
    county_fips: List[str] = typer.Argument(..., help="One or more 5-digit county FIPS codes"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"
    ),
    cadence: Cadence = typer.Option(Cadence.TEN_MIN, help="Averaging window"),
    refetch: bool = typer.Option(False, help="Re-fetch days already stored"),
    source: Optional[List[str]] = typer.Option(None, help="Provider(s); default: all"),
    pollutant: Optional[List[str]] = typer.Option(None, help="Pollutant(s); default: PM2.5,PM10,O3"),
    output: Path = typer.Option(Path("./data"), help="Data directory"),
    credentials: Path = typer.Option(
        Path("./credentials.json"), "--credentials", help="Encrypted credentials file"
    ),
    email: Optional[str] = typer.Option(None, envvar="AQS_EMAIL"),
    api_key: Optional[str] = typer.Option(None, envvar="AQS_API_KEY"),
    purpleair_key: Optional[str] = typer.Option(None, envvar="PURPLEAIR_API_KEY"),
) -> None:
    """Fetch AQI data for the given counties and time range into the store."""
    for fips in county_fips:
        if not (len(fips) == 5 and fips.isdigit()):
            raise typer.BadParameter(f"county FIPS must be 5-digit, got {fips!r}")

    start_date = start.date()
    end_date = end.date() if end else date.today()

    sources = source or all_providers()
    pollutants = (
        [Pollutant.from_str(p) for p in pollutant] if pollutant else DEFAULT_POLLUTANTS
    )
    try:
        creds = credentials_core.resolve(
            {"email": email, "api_key": api_key, "purpleair_key": purpleair_key},
            credentials,
            get_password=resolve_password,
        )
    except (InvalidToken, json.JSONDecodeError, KeyError, binascii.Error) as exc:
        raise typer.BadParameter(
            f"could not decrypt {credentials} — wrong password?"
        ) from exc

    providers = _resolve_providers(sources, creds)

    for fips in county_fips:
        console.print(f"[cyan]Fetching[/] {fips} ({cadence.value}) …")
        fetcher.fetch_county(
            output, fips, start_date, end_date, pollutants, cadence.minutes,
            providers, today=date.today(), refetch=refetch,
        )
        console.print(f"[green]Updated[/] {output}/{fips}")
```

- [ ] **Step 5: Update `tests/test_fetch_cli.py`**

The CLI no longer writes a single `{fips}_{start}_{end}.parquet`; it writes per-day files via the store, and `_resolve_providers` returns cadence-aware providers. Replace the two write/end-default tests' provider fakes and assertions. Replace `test_fetch_writes_parquet` and `test_end_defaults_to_today` with:

```python
def test_fetch_writes_day_files(tmp_path, monkeypatch):
    from smoke_sense.bin import fetch as fetch_mod

    class FakeProvider:
        name = "aqs"
        supported_cadences = [60]

        def resolve_cadence(self, requested):
            return 60

        def fetch(self, county_fips, start, end, pollutants, cadence):
            return _fake_frame(county_fips)

    monkeypatch.setattr(
        fetch_mod, "_resolve_providers", lambda sources, creds: [FakeProvider()]
    )
    result = runner.invoke(
        app,
        ["fetch", "06037", "--start", "2023-07-01", "--end", "2023-07-02",
         "--source", "aqs", "--credentials", str(tmp_path / "absent.json"),
         "--output", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    out = tmp_path / "06037" / "2023-07-01.parquet"
    assert out.exists()
    back = data.read_parquet(out)
    assert back["county_fips"].iloc[0] == "06037"


def test_cadence_option_accepted(tmp_path, monkeypatch):
    from smoke_sense.bin import fetch as fetch_mod

    seen = {}

    class FakeProvider:
        name = "aqs"
        supported_cadences = [60]

        def resolve_cadence(self, requested):
            seen["requested"] = requested
            return 60

        def fetch(self, county_fips, start, end, pollutants, cadence):
            return _fake_frame(county_fips)

    monkeypatch.setattr(
        fetch_mod, "_resolve_providers", lambda sources, creds: [FakeProvider()]
    )
    result = runner.invoke(
        app,
        ["fetch", "06037", "--start", "2023-07-01", "--end", "2023-07-01",
         "--cadence", "THIRTY_MIN", "--credentials", str(tmp_path / "absent.json"),
         "--output", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert seen["requested"] == 30
```

Note: `_fake_frame` already includes `agg_window` (from Task 1). The `(timestamp 2023-07-01)` row lands in `06037/2023-07-01.parquet`. The wrong-password test (`test_wrong_password_surfaces_clean_error`) and `test_invalid_fips_exits_nonzero` are unchanged and still pass.

- [ ] **Step 6: Run, confirm PASS.** `uv run pytest tests/test_fetcher.py tests/test_fetch_cli.py -v`, then `uv run pytest -q`, then `uv run smoke-sense fetch --help` (shows `--cadence`, `--refetch`).

- [ ] **Step 7: Stage.** `git add src/smoke_sense/fetcher.py src/smoke_sense/bin/fetch.py tests/test_fetcher.py tests/test_fetch_cli.py`

---

## Self-Review

**Spec coverage:**
- Cadence enum + `--cadence` default `TEN_MIN` → Task 0 + Task 4 ✓
- `resolve_cadence` (max ≤ requested else min) → Task 0; per-provider → Task 3 ✓
- `agg_window` column recording aggregation → Task 1 ✓
- Per-day store `{data_dir}/{fips}/{date}.parquet`, read-merge-rewrite, finer-wins → Task 2 ✓
- Hybrid incremental + `--refetch` + always-refetch-today → Task 4 ✓
- Current day through *now* (end-day inclusive, capped at now) → Task 3 (`_get_history`) ✓
- Adaptive chunking (halve on 400 to 1 day) → Task 3 ✓
- Provider cadence→`average` mapping → Task 3 ✓
- CLI `--output` as data dir, single-file mode removed → Task 4 ✓
- Tests for each unit → Tasks 0–4 ✓

**Placeholder scan:** none — full code in every step.

**Type/name consistency:** `resolve_cadence(supported, requested)` signature shared (cadence.py free function and base method); `agg_window` Int16 everywhere; provider `fetch(county_fips, start, end, pollutants, cadence=60)` consistent across base/aqs/purpleair/fakes; `store.write/merge_day/coverage` and `fetcher.fetch_county(..., today, refetch)` used consistently; `Cadence.minutes` used by the CLI to pass an int into `fetcher`.

**Note:** AQS keeps per-year request splitting (its only span limit); adaptive halving applies to PurpleAir, whose limit varies with `average`.
