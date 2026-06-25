# Robust Fetch & Request Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `fetch` durable (flush in-memory data on any unhandled error or Ctrl-C, write each day file once otherwise) and add opt-in `-v/--verbose` request logging with credential redaction.

**Architecture:** Providers' `fetch` becomes a generator yielding DataFrame chunks (per sensor / per year). `fetcher.fetch_county` buffers chunks per county and writes once via `store.write`, wrapping the gather loop in `try/except BaseException` to flush partial data before re-raising. A stdlib logger, enabled by `--verbose`, logs redacted HTTP requests to stderr.

**Tech Stack:** Python 3.12, Typer, Rich, pandas, requests, pytest.

**Reference spec:** `docs/superpowers/specs/2026-06-25-robust-fetch-and-logging-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/smoke_sense/providers/base.py` | `fetch` typed as generator (`Iterator[pd.DataFrame]`) |
| `src/smoke_sense/providers/aqs.py` | `fetch` yields per-year chunks |
| `src/smoke_sense/providers/purpleair.py` | `fetch` yields per-sensor chunks |
| `src/smoke_sense/fetcher.py` | buffer per county; flush-on-`BaseException`; write once |
| `src/smoke_sense/logutil.py` | (new, Task 1) `redact(params, secret_keys)` |
| `src/smoke_sense/bin/fetch.py` | (Task 1) `-v/--verbose` → RichHandler on stderr |

---

### Task 0: Streaming providers + durable buffered fetcher

**Goal:** Providers yield chunks; the fetcher buffers per county and flushes (writes) on success OR on any interceptable exit, writing each day file once.

**Files:**
- Modify: `src/smoke_sense/providers/base.py`
- Modify: `src/smoke_sense/providers/aqs.py`
- Modify: `src/smoke_sense/providers/purpleair.py`
- Modify: `src/smoke_sense/fetcher.py`
- Modify: `tests/test_providers_purpleair.py`
- Modify: `tests/test_fetcher.py`
- Modify: `tests/test_fetch_cli.py`

**Acceptance Criteria:**
- [ ] `provider.fetch` is a generator yielding non-empty chunks (AQS per year, PurpleAir per sensor)
- [ ] `fetch_county` writes once on success (one `store.write`) and flushes the partial buffer on `Exception` AND `KeyboardInterrupt`, then re-raises
- [ ] full suite passes (fakes/consumers updated to the generator contract)

**Verify:** `uv run pytest tests/test_fetcher.py tests/test_providers_purpleair.py tests/test_fetch_cli.py -v` → all pass

**Steps:**

- [ ] **Step 1: Update `tests/test_fetcher.py`**

Replace the whole file with:
```python
from datetime import date

import pandas as pd
import pytest

from smoke_sense import data, fetcher, store
from smoke_sense.data import Pollutant


def _frame(county_fips, day, source="purpleair", agg=10):
    return data.validate(pd.DataFrame([{
        "timestamp": pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=1),
        "county_fips": county_fips, "station_id": "s1",
        "latitude": 34.0, "longitude": -118.2,
        "pollutant": Pollutant.PM2_5.value, "value": 1.0, "unit": "µg/m³",
        "aqi": 5, "agg_window": agg, "source": source,
    }]))


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
        day = start
        while day <= end:
            yield _frame(county_fips, day, agg=cadence)
            day += pd.Timedelta(days=1).to_pytimedelta()


def test_hybrid_skips_covered_and_fetches_gaps(tmp_path):
    store.write(tmp_path, "06037", _frame("06037", date(2026, 6, 16)))
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 18),
                         [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 20))
    fetched_days = {d for (s, e) in p.requested_ranges
                    for d in pd.date_range(s, e, freq="D").date}
    assert date(2026, 6, 16) not in fetched_days
    assert date(2026, 6, 17) in fetched_days
    assert date(2026, 6, 18) in fetched_days


def test_always_refetches_today(tmp_path):
    store.write(tmp_path, "06037", _frame("06037", date(2026, 6, 20)))
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 20), date(2026, 6, 20),
                         [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 20))
    assert p.requested_ranges


def test_refetch_fetches_everything(tmp_path):
    store.write(tmp_path, "06037", _frame("06037", date(2026, 6, 16)))
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 17),
                         [Pollutant.PM2_5], 10, [p],
                         today=date(2026, 6, 30), refetch=True)
    fetched_days = {d for (s, e) in p.requested_ranges
                    for d in pd.date_range(s, e, freq="D").date}
    assert date(2026, 6, 16) in fetched_days


def test_writes_once_on_success(tmp_path, monkeypatch):
    calls = {"n": 0}
    real_write = store.write
    monkeypatch.setattr(
        store, "write",
        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), real_write(*a, **k))[1],
    )
    p = FakeProvider()
    fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 17),
                         [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 30))
    assert calls["n"] == 1  # single flush write for the county


class _YieldThenFail:
    name = "purpleair"
    supported_cadences = [10]

    def __init__(self, exc):
        self._exc = exc

    def resolve_cadence(self, requested):
        return 10

    def fetch(self, county_fips, start, end, pollutants, cadence):
        yield _frame(county_fips, date(2026, 6, 16))
        yield _frame(county_fips, date(2026, 6, 17))
        raise self._exc


def test_flush_on_exception(tmp_path):
    p = _YieldThenFail(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 17),
                             [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 30))
    cov = store.coverage(tmp_path, "06037")
    assert (date(2026, 6, 16), "purpleair") in cov
    assert (date(2026, 6, 17), "purpleair") in cov


def test_flush_on_keyboard_interrupt(tmp_path):
    p = _YieldThenFail(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        fetcher.fetch_county(tmp_path, "06037", date(2026, 6, 16), date(2026, 6, 17),
                             [Pollutant.PM2_5], 10, [p], today=date(2026, 6, 30))
    cov = store.coverage(tmp_path, "06037")
    assert (date(2026, 6, 16), "purpleair") in cov
    assert (date(2026, 6, 17), "purpleair") in cov
```

- [ ] **Step 2: Run, confirm FAIL.** `uv run pytest tests/test_fetcher.py -v` (generator/flush behavior not yet implemented).

- [ ] **Step 3: Rewrite `src/smoke_sense/fetcher.py`**

```python
"""Fetch orchestration: gap detection, provider streaming, durable store writes.

No Typer/CLI coupling so the incremental logic is unit-testable.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from . import store

logger = logging.getLogger(__name__)


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


def _flush(data_dir, fips, buffer: list[pd.DataFrame]) -> None:
    if buffer:
        combined = pd.concat(buffer, ignore_index=True)
        store.write(data_dir, fips, combined)
        logger.info("wrote %d rows for %s", len(combined), fips)
        buffer.clear()


def fetch_county(data_dir, fips, start, end, pollutants, requested_cadence,
                 providers, today, refetch=False) -> None:
    """Stream provider chunks into a per-county buffer; write once at the end.

    On any interceptable exit (unhandled exception or KeyboardInterrupt) the
    partial buffer is flushed to the store before the exception propagates.
    """
    cov = store.coverage(data_dir, fips)
    buffer: list[pd.DataFrame] = []
    try:
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
                for chunk in provider.fetch(fips, run_start, run_end, pollutants, actual):
                    buffer.append(chunk)
    except BaseException:
        _flush(data_dir, fips, buffer)
        raise
    _flush(data_dir, fips, buffer)
```

- [ ] **Step 4: Update `src/smoke_sense/providers/base.py`**

Add the import and change the `fetch` annotation/docstring to a generator contract:
```python
from collections.abc import Iterator
```
```python
    @abstractmethod
    def fetch(
        self,
        county_fips: str,
        start: date,
        end: date,
        pollutants: list[Pollutant],
        cadence: int = 60,
    ) -> Iterator[pd.DataFrame]:
        """Yield `data`-schema DataFrame chunks for the county/range/pollutants."""
        raise NotImplementedError
```

- [ ] **Step 5: Make AQS `fetch` a generator** (`src/smoke_sense/providers/aqs.py`)

Replace the `fetch` method with:
```python
    def fetch(self, county_fips, start, end, pollutants, cadence: int = 60):
        wanted = [p for p in pollutants if p in self.supported]
        for p in pollutants:
            if p not in self.supported:
                warnings.warn(f"{self.name}: pollutant {p.value} not supported, skipping")
        if not wanted:
            return

        agg = self.resolve_cadence(cadence)
        state, county = county_fips[:2], county_fips[2:]
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
            chunk = self._parse(payload, county_fips, agg)
            if not chunk.empty:
                yield chunk
```

- [ ] **Step 6: Make PurpleAir `fetch` a generator** (`src/smoke_sense/providers/purpleair.py`)

Replace the `fetch` method with:
```python
    def fetch(self, county_fips, start, end, pollutants, cadence: int = 60):
        wanted = [p for p in pollutants if p in self.supported]
        for p in pollutants:
            if p not in self.supported:
                warnings.warn(f"{self.name}: pollutant {p.value} not supported, skipping")
        if not wanted:
            return

        average = self.resolve_cadence(cadence)
        bbox = bbox_for_county(county_fips)
        sensors = self._list_sensors(bbox)
        geometry = county_polygon(county_fips)
        sensors = self._filter_sensors(sensors, geometry, start, end)
        if not sensors:
            return
        # PurpleAir returns time_stamp automatically; do not request it.
        fields = ["humidity"] + [
            f for f, (p, _) in _FIELD_MAP.items() if p in wanted
        ]
        for sensor in sensors:
            rows, resp_fields = self._history_chunked(
                sensor["sensor_index"], start, end, average, fields)
            chunk = self._parse_history(
                {"fields": resp_fields, "data": rows},
                sensor["sensor_index"],
                sensor["latitude"], sensor["longitude"],
                county_fips, wanted, average,
            )
            if not chunk.empty:
                yield chunk
```

- [ ] **Step 7: Update PurpleAir fetch-based tests** (`tests/test_providers_purpleair.py`)

The three tests that call `provider.fetch(...)` must consume the generator. Replace each `df = provider.fetch(...)` usage with a concat of the yielded chunks. Specifically:

In `test_history_request_does_not_request_time_stamp_field`:
```python
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 16), date(2026, 6, 24), [Pollutant.PM2_5], cadence=10
    ))
    df = pd.concat(chunks, ignore_index=True) if chunks else data.empty_frame()
```
In `test_fetch_chunks_large_range_on_400`:
```python
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 1), date(2026, 6, 5), [Pollutant.PM2_5], cadence=10
    ))
    df = pd.concat(chunks, ignore_index=True) if chunks else data.empty_frame()
```
In `test_fetch_excludes_out_of_polygon_sensor`:
```python
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 16), date(2026, 6, 17), [Pollutant.PM2_5], cadence=10
    ))
    df = pd.concat(chunks, ignore_index=True) if chunks else data.empty_frame()
```
(Each file already imports `pd`, `data`, `date`, `Pollutant`. The subsequent assertions on `df` are unchanged; for the excluded-sensor case `chunks` is empty so `df` is empty and `df.empty` holds.)

- [ ] **Step 8: Update CLI fakes** (`tests/test_fetch_cli.py`)

The `FakeProvider.fetch` methods in `test_fetch_writes_day_files` and `test_cadence_option_accepted` must yield instead of return. Change each `def fetch(...)` body from `return _fake_frame(county_fips)` to:
```python
        def fetch(self, county_fips, start, end, pollutants, cadence):
            yield _fake_frame(county_fips)
```

- [ ] **Step 9: Run, confirm PASS.** `uv run pytest tests/test_fetcher.py tests/test_providers_purpleair.py tests/test_fetch_cli.py -v` then `uv run pytest -q`.

- [ ] **Step 10: Stage.** `git add src/smoke_sense/providers/base.py src/smoke_sense/providers/aqs.py src/smoke_sense/providers/purpleair.py src/smoke_sense/fetcher.py tests/test_fetcher.py tests/test_providers_purpleair.py tests/test_fetch_cli.py`

---

### Task 1: Request logging with redaction + `--verbose`

**Goal:** Log each provider HTTP request (redacted) and retries to stderr when `-v/--verbose` is set.

**Files:**
- Create: `src/smoke_sense/logutil.py`
- Create: `tests/test_logutil.py`
- Modify: `src/smoke_sense/providers/aqs.py`
- Modify: `src/smoke_sense/providers/purpleair.py`
- Modify: `src/smoke_sense/bin/fetch.py`
- Modify: `tests/test_providers_aqs.py`
- Modify: `tests/test_fetch_cli.py`

**Acceptance Criteria:**
- [ ] `logutil.redact` masks the given secret keys, leaves others unchanged, and does not mutate the input
- [ ] AQS `_request` logs a request line that does NOT contain the api key/email (redacted)
- [ ] `bin/fetch._configure_logging(True)` raises the `smoke_sense` logger to INFO with a stderr handler; `(False)` does not

**Verify:** `uv run pytest tests/test_logutil.py tests/test_providers_aqs.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `tests/test_logutil.py`**

```python
from smoke_sense.logutil import redact


def test_redact_masks_secret_keys():
    params = {"email": "me@example.com", "key": "SECRET", "state": "06"}
    out = redact(params, {"email", "key"})
    assert out == {"email": "***", "key": "***", "state": "06"}


def test_redact_does_not_mutate_input():
    params = {"key": "SECRET", "x": 1}
    redact(params, {"key"})
    assert params == {"key": "SECRET", "x": 1}


def test_redact_ignores_absent_keys():
    out = redact({"a": 1}, {"key"})
    assert out == {"a": 1}
```

- [ ] **Step 2: Run, confirm FAIL.** `uv run pytest tests/test_logutil.py -v`

- [ ] **Step 3: Create `src/smoke_sense/logutil.py`**

```python
"""Small logging helpers shared by providers."""

from __future__ import annotations

from collections.abc import Iterable


def redact(params: dict, secret_keys: Iterable[str]) -> dict:
    """Return a copy of `params` with `secret_keys` values replaced by '***'."""
    secret = set(secret_keys)
    return {k: ("***" if k in secret else v) for k, v in params.items()}
```

- [ ] **Step 4: Add request logging to AQS** (`src/smoke_sense/providers/aqs.py`)

Add near the top (after the existing imports):
```python
import logging
import time

from ..logutil import redact

logger = logging.getLogger(__name__)
```
Replace `_request` with a timed, logged version:
```python
    def _request(self, params: dict) -> dict:
        if not self.email or not self.api_key:
            raise ValueError(
                "EPA AQS requires credentials (AQS_EMAIL / AQS_API_KEY)"
            )
        started = time.monotonic()
        resp = self.session.get(_BASE_URL, params=params, timeout=120)
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.info("GET %s params=%s -> %s (%.0f ms)", _BASE_URL,
                    redact(params, {"email", "key"}), resp.status_code, elapsed_ms)
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 5: Add request logging to PurpleAir** (`src/smoke_sense/providers/purpleair.py`)

Add near the top (after existing imports):
```python
import logging

logger = logging.getLogger(__name__)
```
(`time` is already imported in this module.) In `_get`, time and log each attempt, and log 429 backoff. Replace `_get` with:
```python
    def _get(self, url: str, params: dict) -> dict:
        """GET with retry on HTTP 429 (honor Retry-After, else exp. backoff)."""
        delay = 2.0
        for attempt in range(self._MAX_RETRIES + 1):
            started = time.monotonic()
            resp = self.session.get(
                url, headers=self._headers(), params=params, timeout=120)
            elapsed_ms = (time.monotonic() - started) * 1000
            logger.info("GET %s params=%s -> %s (%.0f ms)",
                        url, params, resp.status_code, elapsed_ms)
            if resp.status_code == 429 and attempt < self._MAX_RETRIES:
                header = resp.headers.get("Retry-After")
                try:
                    wait = float(header) if header is not None else delay
                except (TypeError, ValueError):
                    wait = delay
                logger.info("429 from %s; retrying in %.0fs (attempt %d/%d)",
                            url, wait, attempt + 1, self._MAX_RETRIES)
                time.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()
```
(PurpleAir params carry no secrets — the key is the `X-API-Key` header, which is not logged — so params are logged as-is.)

- [ ] **Step 6: Add `--verbose` to the CLI** (`src/smoke_sense/bin/fetch.py`)

Add imports near the top:
```python
import logging

from rich.logging import RichHandler
```
Add a helper above `fetch`:
```python
def _configure_logging(verbose: bool) -> None:
    """Attach a stderr Rich handler at INFO to the package logger when verbose."""
    if not verbose:
        return
    pkg_logger = logging.getLogger("smoke_sense")
    pkg_logger.setLevel(logging.INFO)
    pkg_logger.addHandler(
        RichHandler(console=Console(stderr=True), show_path=False, show_time=False)
    )
```
Add a `verbose` option to the `fetch` signature (after `purpleair_key`):
```python
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log requests to stderr"),
```
And call it first thing in the body, before FIPS validation:
```python
    _configure_logging(verbose)
```

- [ ] **Step 7: Add logging tests**

In `tests/test_providers_aqs.py`, add (the file imports `EPAAQSProvider`, `Pollutant`; add `import logging` at the top):
```python
class _LogResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"Data": []}


class _LogSession:
    def get(self, url, params=None, timeout=None):
        return _LogResp()


def test_request_logs_redact_credentials(caplog):
    provider = EPAAQSProvider(email="me@example.com", api_key="SECRETKEY",
                              session=_LogSession())
    with caplog.at_level(logging.INFO, logger="smoke_sense"):
        provider._request({"email": "me@example.com", "key": "SECRETKEY", "state": "06"})
    assert "GET" in caplog.text
    assert "SECRETKEY" not in caplog.text
    assert "me@example.com" not in caplog.text
```

In `tests/test_fetch_cli.py`, add (imports `app`, `runner` already present; add `import logging`):
```python
def test_configure_logging_toggles_handler():
    from smoke_sense.bin import fetch as fetch_mod

    pkg = logging.getLogger("smoke_sense")
    before = list(pkg.handlers)
    try:
        fetch_mod._configure_logging(False)
        assert list(pkg.handlers) == before
        fetch_mod._configure_logging(True)
        assert len(pkg.handlers) == len(before) + 1
        assert pkg.level == logging.INFO
    finally:
        pkg.handlers = before
        pkg.setLevel(logging.WARNING)
```

- [ ] **Step 8: Run, confirm PASS.** `uv run pytest tests/test_logutil.py tests/test_providers_aqs.py tests/test_fetch_cli.py -v`, then `uv run pytest -q`, then `uv run smoke-sense fetch --help` (shows `-v/--verbose`).

- [ ] **Step 9: Stage.** `git add src/smoke_sense/logutil.py tests/test_logutil.py src/smoke_sense/providers/aqs.py src/smoke_sense/providers/purpleair.py src/smoke_sense/bin/fetch.py tests/test_providers_aqs.py tests/test_fetch_cli.py`

---

## Self-Review

**Spec coverage:**
- Providers yield chunks (AQS per year, PurpleAir per sensor) → Task 0 Steps 4–6 ✓
- Fetcher buffers per county, writes once, flushes on `BaseException` (Exception + KeyboardInterrupt) then re-raises → Task 0 Step 3 ✓
- Write-once on success (single `store.write`) → tested (`test_writes_once_on_success`) ✓
- `-v/--verbose` → stderr RichHandler at INFO → Task 1 Step 6 ✓
- Per-request logging with timing + 429 retries → Task 1 Steps 4–5 ✓
- Redaction of AQS email/key; PurpleAir key is a header (not logged) → Task 1 Steps 3–5 ✓
- Tests: streaming, durability (exception + Ctrl-C), write-once, redaction, verbose toggle → Tasks 0–1 ✓

**Placeholder scan:** none — full code in every step.

**Type/name consistency:** `fetch(..., cadence=60) -> Iterator[pd.DataFrame]` consistent across base/aqs/purpleair and all fakes (which now `yield`); `fetcher._flush(data_dir, fips, buffer)` and `_configure_logging(verbose)` and `logutil.redact(params, secret_keys)` used consistently between implementation and tests.

**Notes:** AQS keeps per-year request splitting; PurpleAir keeps adaptive chunking and filtering — only the return shape changes (yield vs concat-return). The `_parse`/`_parse_history`/`_history_chunked` helpers are unchanged. Per the spec, a day flushed partially on error is "covered" next run; `--refetch` heals it.
