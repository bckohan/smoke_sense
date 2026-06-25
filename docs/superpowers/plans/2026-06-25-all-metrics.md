# Fetch All Available Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize from three pollutants to a full per-source metric set fetched by default, with a provider-agnostic `Metric` (enum-properties), provider-owned source mappings + unit conversion, a normalized schema (metric column, no unit/lat-lon, zstd), a per-county station table, and a `--metric` override.

**Architecture:** `Metric` (enum-properties) holds canonical name/unit/has_aqi. Each provider owns its `Metric→code/field` map, `supported_metrics`, and unit conversion, and yields rows in canonical units (still carrying lat/lon for the station table). `store.write` splits station metadata into `{fips}/stations.parquet` and writes unit-less, lat/lon-less day files with zstd. A migration script converts existing data.

**Tech Stack:** Python 3.12, Typer, Rich, pandas, requests, enum-properties, pytest.

**Reference spec:** `docs/superpowers/specs/2026-06-25-all-metrics-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/smoke_sense/data.py` | `Metric` enum (enum-properties); schema (metric, no unit/lat-lon); zstd |
| `src/smoke_sense/aqi.py` | NowCast/breakpoints keyed on `Metric` (has_aqi members) |
| `src/smoke_sense/store.py` | station-table split; merge dedup on metric |
| `src/smoke_sense/providers/base.py` | `supported_metrics`; `fetch(..., metrics, ...)` |
| `src/smoke_sense/providers/aqs.py` | `Metric→code` map; ≤5-param batching; unit conversion |
| `src/smoke_sense/providers/purpleair.py` | `Metric→field` map; wide→long melt; conversion; PM2.5 correction |
| `src/smoke_sense/fetcher.py` | pass `metrics` |
| `src/smoke_sense/bin/fetch.py` | `--metric` (default all) |
| `src/smoke_sense/summary.py` | group by `metric` |
| `scripts/migrate_store.py` | convert existing day files to the new schema |

---

### Task 0: `Metric` enum (enum-properties) + dependency

**Goal:** Replace `Pollutant` with a provider-agnostic `Metric` enum carrying `unit`/`has_aqi` and symmetric name lookup.

**Files:**
- Modify: `pyproject.toml` (add `enum-properties`)
- Modify: `src/smoke_sense/data.py` (add `Metric`, keep schema for now)
- Create: `tests/test_metric.py`

**Acceptance Criteria:**
- [ ] `Metric.PM2_5.unit == "µg/m³"`, `.has_aqi is True`; `Metric.TEMP.has_aqi is False`
- [ ] symmetric case-insensitive lookup: `Metric("pm2.5") is Metric.PM2_5`
- [ ] `AQI_METRICS` is exactly the `has_aqi` members

**Verify:** `uv run pytest tests/test_metric.py -v` → all pass

**Steps:**

- [ ] **Step 1: Add the dependency.** Run `uv add enum-properties`. Then confirm the API of the installed version:
  `uv run python -c "import enum_properties as e; print(e.__version__); from enum_properties import StrEnumProperties, Symmetric; print('ok')"`.
  If `StrEnumProperties`/`Symmetric` import names differ in the installed version, adjust the imports in Step 3 accordingly (the property/Symmetric concept is stable; only the import surface may differ). Report any adjustment.

- [ ] **Step 2: Write `tests/test_metric.py`**

```python
import pytest

from smoke_sense.data import AQI_METRICS, Metric


def test_properties():
    assert Metric.PM2_5.unit == "µg/m³"
    assert Metric.PM2_5.has_aqi is True
    assert Metric.O3.unit == "ppm"
    assert Metric.TEMP.unit == "°C"
    assert Metric.TEMP.has_aqi is False


def test_symmetric_case_insensitive_lookup():
    assert Metric("PM2.5") is Metric.PM2_5
    assert Metric("pm2.5") is Metric.PM2_5
    assert Metric("O3") is Metric.O3
    with pytest.raises(ValueError):
        Metric("nope")


def test_aqi_metrics_are_has_aqi_members():
    assert AQI_METRICS == {m for m in Metric if m.has_aqi}
    assert AQI_METRICS == {Metric.PM2_5, Metric.PM10, Metric.O3}
```

- [ ] **Step 3: Add `Metric` to `src/smoke_sense/data.py`**

Add imports at the top:
```python
from typing import Annotated

from enum_properties import StrEnumProperties, Symmetric
```
Add the enum (place above the existing `Pollutant` for now; `Pollutant` is removed in Task 1):
```python
class Metric(StrEnumProperties):
    """A measured quantity with its canonical unit and AQI eligibility.

    Provider-specific codes/fields live on the providers, not here.
    """

    label: Annotated[str, Symmetric(case_fold=True)]
    unit: str
    has_aqi: bool

    #          value          label          unit      has_aqi
    PM2_5      = "PM2.5",     "PM2.5",     "µg/m³",  True
    PM2_5_CF1  = "PM2.5_CF1", "PM2.5_CF1", "µg/m³",  False
    PM2_5_ATM  = "PM2.5_ATM", "PM2.5_ATM", "µg/m³",  False
    PM10       = "PM10",      "PM10",      "µg/m³",  True
    PM10_CF1   = "PM10_CF1",  "PM10_CF1",  "µg/m³",  False
    PM10_ATM   = "PM10_ATM",  "PM10_ATM",  "µg/m³",  False
    PM1_0_CF1  = "PM1.0_CF1", "PM1.0_CF1", "µg/m³",  False
    PM1_0_ATM  = "PM1.0_ATM", "PM1.0_ATM", "µg/m³",  False
    O3         = "O3",        "O3",        "ppm",    True
    CO         = "CO",        "CO",        "ppm",    False
    SO2        = "SO2",       "SO2",       "ppb",    False
    NO2        = "NO2",       "NO2",       "ppb",    False
    PB         = "Pb",        "Pb",        "µg/m³",  False
    TEMP       = "temperature", "temperature", "°C", False
    RH         = "humidity",    "humidity",    "%",  False
    PRESSURE   = "pressure",    "pressure",    "hPa", False
    WIND_SPEED = "wind_speed",  "wind_speed",  "m/s", False
    WIND_DIR   = "wind_dir",    "wind_dir",    "deg", False
    VOC        = "VOC",        "VOC",        "iaq",  False


AQI_METRICS: frozenset[Metric] = frozenset(m for m in Metric if m.has_aqi)
```

> If the installed enum-properties spells the base as `EnumProperties` (not `StrEnumProperties`), use `class Metric(str, EnumProperties):` instead — same members/properties. Verify `Metric("pm2.5") is Metric.PM2_5` works (symmetric case_fold); if the value itself isn't matched case-insensitively, the `label` Symmetric property provides it.

- [ ] **Step 4: Run, confirm PASS.** `uv run pytest tests/test_metric.py -v` then `uv run pytest -q` (the rest of the suite still uses `Pollutant`, which is untouched in this task — it should remain green).

- [ ] **Step 5: Stage.** `git add pyproject.toml uv.lock src/smoke_sense/data.py tests/test_metric.py`

---

### Task 1: Schema → metric/no-unit/no-latlon + zstd; aqi.py on Metric

**Goal:** Switch the canonical schema to `metric` (drop `unit`, `latitude`, `longitude`), zstd compression, and rekey `aqi.py` on `Metric`; update all producers/fixtures so the suite is green. (Providers temporarily emit constant placeholders for the new contract; full provider rewrites are Tasks 3–4.)

**Files:**
- Modify: `src/smoke_sense/data.py`, `src/smoke_sense/aqi.py`, `src/smoke_sense/store.py`,
  `src/smoke_sense/summary.py`, `src/smoke_sense/providers/aqs.py`,
  `src/smoke_sense/providers/purpleair.py`, `src/smoke_sense/fetcher.py`,
  `src/smoke_sense/bin/fetch.py` (it imports the removed `Pollutant` — interim: set
  `DEFAULT_POLLUTANTS = [Metric.PM2_5, Metric.PM10, Metric.O3]` and parse with `Metric(...)`;
  Task 5 makes the default "all metrics")
- Modify: `tests/conftest.py`, `tests/test_data.py`, `tests/test_aqi.py`,
  `tests/test_store.py`, `tests/test_summary.py`, `tests/test_summary_cli.py`,
  `tests/test_fetcher.py`, `tests/test_providers_aqs.py`,
  `tests/test_providers_purpleair.py`, `tests/test_fetch_cli.py`

**Acceptance Criteria:**
- [ ] `data.COLUMNS` keys are exactly: timestamp, county_fips, station_id, metric, value, aqi, agg_window, source
- [ ] `write_parquet` uses zstd; round-trip works
- [ ] full suite passes

**Verify:** `uv run pytest -q` → all pass

**Steps:**

- [ ] **Step 1: Update `data.py` schema.** Replace `COLUMNS`, `REQUIRED_NON_NULL`, and `write_parquet`; delete the `Pollutant` enum and its `_AQS_CODES`/`_UNITS` dicts (superseded by `Metric`).

```python
COLUMNS: dict[str, str] = {
    "timestamp": "datetime64[ns, UTC]",
    "county_fips": "string",
    "station_id": "string",
    "metric": "category",
    "value": "float64",
    "aqi": "Int16",
    "agg_window": "Int16",
    "source": "category",
}

REQUIRED_NON_NULL: list[str] = [
    "timestamp", "county_fips", "station_id", "metric", "value", "agg_window", "source",
]
```
```python
def write_parquet(df: pd.DataFrame, path: str | Path) -> None:
    """Validate and persist a frame to Parquet (zstd), creating parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    validate(df).to_parquet(path, index=False, compression="zstd")
```
`validate`/`empty_frame`/`read_parquet` are otherwise unchanged (they derive from `COLUMNS`). Remove the `Pollutant` class entirely.

- [ ] **Step 2: Rekey `aqi.py` on `Metric`.** Replace `from .data import Pollutant` with `from .data import Metric`, and change every `Pollutant` reference to `Metric` (the breakpoint/trunc dict keys `Pollutant.PM2_5/PM10/O3` become `Metric.PM2_5/PM10/O3`). Signatures stay `nowcast(series, metric)` / `concentration_to_aqi(c, metric)` / `compute_aqi(series, metric)` (param renamed `pollutant`→`metric`; logic identical).

- [ ] **Step 3: Update producers to the new schema (interim).** In `providers/aqs.py` `_parse` and `providers/purpleair.py` `_parse_history`, the constructed `pd.DataFrame` must (a) use `"metric"` instead of `"pollutant"`, (b) drop the `"unit"` key, and (c) KEEP `latitude`/`longitude` columns (store extracts them — see Task 2; until Task 2, `validate` drops them, which is fine). Replace `Pollutant` references with `Metric` and `.value`. Concretely, in each builder dict change:
  - `"pollutant": <X>.value` → `"metric": <X>.value`
  - remove the `"unit": ...` line
  - keep `"latitude"`, `"longitude"`
  Also update `_CODE_TO_POLLUTANT`→`_CODE_TO_METRIC` in aqs.py and `_FIELD_MAP` pollutant refs in purpleair.py to use `Metric` (Tasks 3–4 expand these maps; here just rename `Pollutant`→`Metric` to keep imports valid). Update `from ..data import ... Pollutant ...` imports to `Metric` across both providers, `fetcher.py` is unaffected, and `_add_aqi`/`_parse_history` AQI gating changes `Pollutant(...)`→`Metric(...)` and "is it a pollutant" to `metric in` `AQI_METRICS` (import `AQI_METRICS`).
  > These providers are rewritten in Tasks 3–4; this step only keeps them compiling and green under the new schema.

- [ ] **Step 4: Update `store.py`.** Change the dedup identity list `_IDENTITY` from `["timestamp", "station_id", "pollutant", "source"]` to `["timestamp", "station_id", "metric", "source"]`. (Station-table split is Task 2.)

- [ ] **Step 5: Update `summary.py`.** Replace `pollutant` with `metric` everywhere: the breakdown groupby `["source", "pollutant", "agg_window"]` → `["source", "metric", "agg_window"]`; the per-`pollutant` groupby/key → per-`metric`; the output keys `"pollutant"` → `"metric"` (in `breakdown` items and `pollutants`→`metrics` list). Rename the output list key `"pollutants"` → `"metrics"`.

- [ ] **Step 6: Update all test fixtures/data builders.** Everywhere tests construct a row dict or frame with `"pollutant"`, `"unit"`, `"latitude"`, `"longitude"`, switch `"pollutant"`→`"metric"`, drop `"unit"`, and drop `"latitude"`/`"longitude"` for rows that go through `data.validate`/`store.write` directly (keep lat/lon only where a test exercises a provider that needs them — see Tasks 3–4 tests). Update imports `Pollutant`→`Metric` and `Pollutant.PM2_5.value`→`Metric.PM2_5.value`. Update `summary` tests that read `s["pollutants"]` → `s["metrics"]` and `p["pollutant"]` → `p["metric"]`.
  Files needing these edits: `tests/conftest.py` (`sample_rows`), `tests/test_data.py`, `tests/test_aqi.py` (Pollutant→Metric), `tests/test_store.py` (`_row`), `tests/test_summary.py` (`_row` + assertions), `tests/test_summary_cli.py` (`_write_day`), `tests/test_fetcher.py` (`_frame`), `tests/test_providers_aqs.py`, `tests/test_providers_purpleair.py`, `tests/test_fetch_cli.py` (`_fake_frame`).

- [ ] **Step 7: Run, confirm PASS.** `uv run pytest -q`. Fix any lingering `pollutant`/`unit`/`Pollutant` references the error messages point to (search: `git grep -n "pollutant\|Pollutant\|\"unit\"" src tests`). All green.

- [ ] **Step 8: Stage.** `git add -A -- src tests` (do not stage the stray `inst.txt` files).

---

### Task 2: Station-metadata table (`store.py`)

**Goal:** Split `latitude`/`longitude` out of day files into `{data_dir}/{fips}/stations.parquet`.

**Files:**
- Modify: `src/smoke_sense/store.py`
- Modify: `tests/test_store.py`

**Acceptance Criteria:**
- [ ] `store.write` writes `stations.parquet` with `station_id, source, latitude, longitude`, deduped on `(station_id, source)`
- [ ] day files contain no `latitude`/`longitude`
- [ ] `read_range`/`coverage` unaffected

**Verify:** `uv run pytest tests/test_store.py -v` → all pass

**Steps:**

- [ ] **Step 1: Add station tests to `tests/test_store.py`.** Update `_row` to also accept `lat`/`lon` and include `latitude`/`longitude` in the dict it returns (these are the provider-frame columns, NOT canonical schema). Then:
```python
def test_write_splits_station_metadata(tmp_path):
    df = pd.DataFrame([
        _row("2026-06-16T01:00:00", 1.0, 10, station="s1", lat=34.0, lon=-118.2),
        _row("2026-06-16T02:00:00", 2.0, 10, station="s1", lat=34.0, lon=-118.2),
        _row("2026-06-16T03:00:00", 3.0, 10, station="s2", lat=33.9, lon=-118.1),
    ])
    store.write(tmp_path, "06037", df)
    stations = pd.read_parquet(tmp_path / "06037" / "stations.parquet")
    assert set(stations["station_id"]) == {"s1", "s2"}
    assert {"station_id", "source", "latitude", "longitude"} <= set(stations.columns)
    # day file has no coordinates
    day = pd.read_parquet(tmp_path / "06037" / "2026-06-16.parquet")
    assert "latitude" not in day.columns and "longitude" not in day.columns
```
(`_row` must include `latitude`/`longitude` keys now; existing store tests still pass because `store.write` extracts then drops them.)

- [ ] **Step 2: Run, confirm the new test FAILS.**

- [ ] **Step 3: Implement in `src/smoke_sense/store.py`.** Add a stations path + merge, and call it from `write`:
```python
_STATION_COLS = ["station_id", "source", "latitude", "longitude"]


def stations_path(data_dir: str | Path, fips: str) -> Path:
    return Path(data_dir) / fips / "stations.parquet"


def _merge_stations(data_dir, fips, df) -> None:
    if not {"latitude", "longitude"} <= set(df.columns):
        return
    new = df[_STATION_COLS].drop_duplicates()
    path = stations_path(data_dir, fips)
    frames = [pd.read_parquet(path)] if path.exists() else []
    frames.append(new)
    merged = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["station_id", "source"], keep="last"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.astype({"station_id": "string", "source": "string"}).to_parquet(
        path, index=False, compression="zstd"
    )
```
In `write`, call `_merge_stations(data_dir, fips, df)` BEFORE `data.validate` (validate drops lat/lon):
```python
def write(data_dir, fips, df):
    if df.empty:
        return
    _merge_stations(data_dir, fips, df)
    df = data.validate(df)
    days = df["timestamp"].dt.tz_convert("UTC").dt.date
    for day, group in df.groupby(days):
        merge_day(data_dir, fips, day, group)
```

- [ ] **Step 4: Run, confirm PASS.** `uv run pytest tests/test_store.py -v` then `uv run pytest -q`.

- [ ] **Step 5: Stage.** `git add src/smoke_sense/store.py tests/test_store.py`

---

### Task 3: AQS all-metrics + ≤5-param batching + unit conversion

**Goal:** AQS fetches its full supported metric set (criteria + meteorology), batching parameter codes ≤5/request and converting to canonical units.

**Files:**
- Modify: `src/smoke_sense/providers/base.py`, `src/smoke_sense/providers/aqs.py`
- Modify: `tests/test_providers_aqs.py`

**Acceptance Criteria:**
- [ ] `EPAAQSProvider.supported_metrics` covers criteria pollutants + meteorology
- [ ] `fetch` batches codes into requests of ≤5 params
- [ ] `_parse` maps codes→Metric, converts units (e.g. temp °F→°C, wind knots→m/s), sets aqi only for AQI metrics

**Verify:** `uv run pytest tests/test_providers_aqs.py -v` → all pass

**Steps:**

- [ ] **Step 1: base.py interface.** Rename `supported`→`supported_metrics`; change `fetch` param `pollutants`→`metrics`. Keep `resolve_cadence`. Update the abstract signature:
```python
    def fetch(self, county_fips: str, start: date, end: date,
              metrics: list["Metric"], cadence: int = 60) -> Iterator[pd.DataFrame]:
        ...
```
(Import `Metric` from `..data`.)

- [ ] **Step 2: aqs.py maps + conversion.** Replace the code map and add a code→metric reverse map and unit conversion. AQS parameter codes (verify each against the AQS parameter list during this task — the well-known criteria codes are stable; confirm the meteorology codes/units before finalizing):
```python
from ..data import AQI_METRICS, Metric, empty_frame

# Metric -> AQS parameter code(s). Multiple codes collapse to one metric.
_AQS_CODES: dict[Metric, tuple[str, ...]] = {
    Metric.PM2_5: ("88101", "88502"),   # FRM + non-FRM -> canonical PM2.5
    Metric.PM10:  ("81102",),
    Metric.O3:    ("44201",),
    Metric.CO:    ("42101",),
    Metric.SO2:   ("42401",),
    Metric.NO2:   ("42602",),
    Metric.PB:    ("14129",),
    Metric.TEMP:  ("62101",),           # outdoor temperature, °F -> °C
    Metric.RH:    ("62201",),           # relative humidity, %
    Metric.PRESSURE: ("64101",),        # barometric pressure, mbar -> hPa (1:1)
    Metric.WIND_SPEED: ("61103",),      # resultant wind speed, knots -> m/s
    Metric.WIND_DIR:   ("61104",),      # resultant wind direction, degrees
}
_CODE_TO_METRIC = {code: m for m, codes in _AQS_CODES.items() for code in codes}

# value conversions to canonical units, keyed by metric (default: identity)
def _to_canonical(metric: Metric, value: float) -> float:
    if metric is Metric.TEMP:               # °F -> °C
        return (value - 32.0) * 5.0 / 9.0
    if metric is Metric.WIND_SPEED:         # knots -> m/s
        return value * 0.514444
    return value
```
`supported_metrics`:
```python
    supported_metrics = set(_AQS_CODES)
```
> Verification step: before completing, confirm the meteorology parameter codes (62101/62201/64101/61103/61104) and their AQS-reported units against the AQS parameter list; adjust `_AQS_CODES`/`_to_canonical` if a unit differs. The criteria codes (88101/88502/81102/44201/42101/42401/42602) are stable.

- [ ] **Step 3: aqs.py fetch with batching.** Replace `fetch` to map wanted metrics → the union of their codes, then request in batches of ≤5 codes per call, per year:
```python
    def fetch(self, county_fips, start, end, metrics, cadence: int = 60):
        wanted = [m for m in metrics if m in self.supported_metrics]
        if not wanted:
            return
        codes = [c for m in wanted for c in _AQS_CODES[m]]
        agg = self.resolve_cadence(cadence)
        state, county = county_fips[:2], county_fips[2:]
        for sub_start, sub_end in self._year_ranges(start, end):
            for i in range(0, len(codes), 5):
                batch = codes[i:i + 5]
                payload = self._request({
                    "email": self.email, "key": self.api_key,
                    "param": ",".join(batch),
                    "bdate": sub_start.strftime("%Y%m%d"),
                    "edate": sub_end.strftime("%Y%m%d"),
                    "state": state, "county": county,
                })
                chunk = self._parse(payload, county_fips, agg)
                if not chunk.empty:
                    yield chunk
```

- [ ] **Step 4: aqs.py `_parse` with metric mapping + conversion.** Update `_parse` to map codes→Metric, convert units, set aqi only for AQI metrics, keep lat/lon:
```python
    def _parse(self, payload: dict, county_fips: str, agg: int = 60) -> pd.DataFrame:
        records = payload.get("Data") or []
        if not records:
            return empty_frame_with_coords()
        raw = pd.DataFrame(records)
        raw = raw[raw["parameter_code"].isin(_CODE_TO_METRIC)]
        if raw.empty:
            return empty_frame_with_coords()
        metric_series = raw["parameter_code"].map(lambda c: _CODE_TO_METRIC[c])
        values = [
            _to_canonical(m, float(v))
            for m, v in zip(metric_series, raw["sample_measurement"])
        ]
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(raw["date_gmt"] + " " + raw["time_gmt"], utc=True),
            "county_fips": county_fips,
            "station_id": raw["state_code"] + raw["county_code"] + raw["site_number"],
            "latitude": raw["latitude"].astype("float64"),
            "longitude": raw["longitude"].astype("float64"),
            "metric": [m.value for m in metric_series],
            "value": values,
            "aqi": pd.NA,
            "agg_window": agg,
            "source": "aqs",
        }).dropna(subset=["value"])
        return self._add_aqi(df)
```
Add a small helper near the top of aqs.py (a coords-carrying empty frame keeps station extraction happy):
```python
def empty_frame_with_coords() -> pd.DataFrame:
    df = empty_frame()
    df["latitude"] = pd.Series(dtype="float64")
    df["longitude"] = pd.Series(dtype="float64")
    return df
```
Update `_add_aqi` so it only computes AQI for `has_aqi` metrics:
```python
    @staticmethod
    def _add_aqi(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            df["aqi"] = pd.array([], dtype="Int16")
            return df
        parts = []
        for (_, metric_name), group in df.groupby(["station_id", "metric"]):
            group = group.sort_values("timestamp")
            metric = Metric(metric_name)
            if metric in AQI_METRICS:
                series = group.set_index("timestamp")["value"]
                group["aqi"] = compute_aqi(series, metric).to_numpy()
            else:
                group["aqi"] = pd.array([pd.NA] * len(group), dtype="Int16")
            parts.append(group)
        return pd.concat(parts, ignore_index=True)
```

- [ ] **Step 5: Update `tests/test_providers_aqs.py`.** Use `Metric`; update the fixture/`_parse` expectations to the new columns (metric, no unit, lat/lon present pre-validate). Add a batching test and a conversion test:
```python
def test_fetch_batches_params_max_5(monkeypatch):
    calls = []

    class S:
        def get(self, url, params=None, timeout=None):
            calls.append(params["param"])
            class R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {"Data": []}
            return R()

    provider = EPAAQSProvider(email="a@b.com", api_key="k", session=S())
    list(provider.fetch("06037", date(2026, 1, 1), date(2026, 1, 2),
                        list(provider.supported_metrics), cadence=60))
    assert calls, "expected requests"
    assert all(len(p.split(",")) <= 5 for p in calls)


def test_parse_converts_temperature_f_to_c():
    payload = {"Data": [{
        "state_code": "06", "county_code": "037", "site_number": "0001",
        "parameter_code": "62101", "latitude": 34.0, "longitude": -118.2,
        "date_gmt": "2026-01-01", "time_gmt": "00:00", "sample_measurement": 32.0,
    }]}
    provider = EPAAQSProvider(email="a@b.com", api_key="k")
    df = provider._parse(payload, "06037")
    assert df["metric"].iloc[0] == Metric.TEMP.value
    assert df["value"].iloc[0] == pytest.approx(0.0, abs=1e-9)  # 32°F -> 0°C
    assert pd.isna(df["aqi"].iloc[0])
```

- [ ] **Step 6: Run, confirm PASS.** `uv run pytest tests/test_providers_aqs.py -v` then `uv run pytest -q`.

- [ ] **Step 7: Stage.** `git add src/smoke_sense/providers/base.py src/smoke_sense/providers/aqs.py tests/test_providers_aqs.py`

---

### Task 4: PurpleAir all-metrics + melt + conversion + corrected PM2.5

**Goal:** PurpleAir fetches all fields, melts the wide response into per-metric rows, converts units, and emits both raw variants and corrected PM2.5.

**Files:**
- Modify: `src/smoke_sense/providers/purpleair.py`
- Modify: `tests/test_providers_purpleair.py`

**Acceptance Criteria:**
- [ ] `supported_metrics` covers PM variants + temp/humidity/pressure/VOC
- [ ] `_parse_history` yields one row per (timestamp, metric); converts temp °F→°C; PM2.5 is EPA-corrected; raw cf_1/atm stored
- [ ] aqi set only for AQI metrics

**Verify:** `uv run pytest tests/test_providers_purpleair.py -v` → all pass

**Steps:**

- [ ] **Step 1: Replace the field map + add metric specs.** In `purpleair.py`:
```python
from ..data import AQI_METRICS, Metric, empty_frame

# Metric -> (PurpleAir history field, needs_pm25_correction)
_FIELD_MAP: dict[Metric, str] = {
    Metric.PM2_5_CF1: "pm2.5_cf_1",
    Metric.PM2_5_ATM: "pm2.5_atm",
    Metric.PM10:      "pm10.0_cf_1",   # canonical PM10 (uncorrected)
    Metric.PM10_CF1:  "pm10.0_cf_1",
    Metric.PM10_ATM:  "pm10.0_atm",
    Metric.PM1_0_CF1: "pm1.0_cf_1",
    Metric.PM1_0_ATM: "pm1.0_atm",
    Metric.TEMP:      "temperature",   # °F -> °C
    Metric.RH:        "humidity",      # %
    Metric.PRESSURE:  "pressure",      # mbar == hPa
    Metric.VOC:       "voc",           # iaq
}
# PM2.5 (corrected) is derived from pm2.5_cf_1 + humidity, handled specially.
_CORRECTED_PM25 = Metric.PM2_5


def _to_canonical(metric: Metric, values):
    if metric is Metric.TEMP:                 # °F -> °C
        return (values - 32.0) * 5.0 / 9.0
    return values
```
`supported_metrics`:
```python
    supported_metrics = set(_FIELD_MAP) | {_CORRECTED_PM25}
```

- [ ] **Step 2: fetch — request all wanted fields.** Replace `fetch` to build the field list from wanted metrics (+humidity when PM2.5 wanted), and pass wanted to `_parse_history`:
```python
    def fetch(self, county_fips, start, end, metrics, cadence: int = 60):
        wanted = [m for m in metrics if m in self.supported_metrics]
        if not wanted:
            return
        average = self.resolve_cadence(cadence)
        bbox = bbox_for_county(county_fips)
        sensors = self._list_sensors(bbox)
        geometry = county_polygon(county_fips)
        sensors = self._filter_sensors(sensors, geometry, start, end)
        if not sensors:
            return
        fields = {_FIELD_MAP[m] for m in wanted if m in _FIELD_MAP}
        if _CORRECTED_PM25 in wanted:
            fields |= {"pm2.5_cf_1", "humidity"}
        field_list = ["humidity"] + sorted(fields - {"humidity"})
        for sensor in sensors:
            rows, resp_fields = self._history_chunked(
                sensor["sensor_index"], start, end, average, field_list)
            chunk = self._parse_history(
                {"fields": resp_fields, "data": rows},
                sensor["sensor_index"], sensor["latitude"], sensor["longitude"],
                county_fips, wanted, average)
            if not chunk.empty:
                yield chunk
```

- [ ] **Step 3: `_parse_history` — melt + convert + correct.** Replace it:
```python
    def _parse_history(self, payload, sensor_id, lat, lon, county_fips, wanted, agg):
        fields = payload["fields"]
        rows = payload["data"]
        if not rows:
            return empty_frame()
        raw = pd.DataFrame(rows, columns=fields)
        ts = pd.to_datetime(raw["time_stamp"], unit="s", utc=True)
        humidity = raw.get("humidity")
        parts = []

        def _emit(metric: Metric, values):
            part = pd.DataFrame({
                "timestamp": ts,
                "county_fips": county_fips,
                "station_id": str(sensor_id),
                "latitude": float(lat),
                "longitude": float(lon),
                "metric": metric.value,
                "value": _to_canonical(metric, values.astype("float64")),
                "aqi": pd.NA,
                "agg_window": agg,
                "source": "purpleair",
            }).dropna(subset=["value"]).sort_values("timestamp")
            if metric in AQI_METRICS and not part.empty:
                series = part.set_index("timestamp")["value"]
                part["aqi"] = compute_aqi(series, metric).to_numpy()
            parts.append(part)

        for metric in wanted:
            if metric is _CORRECTED_PM25:
                if "pm2.5_cf_1" in raw.columns and humidity is not None:
                    corrected = epa_correct_pm25(
                        raw["pm2.5_cf_1"].astype("float64"), humidity.astype("float64"))
                    _emit(metric, corrected)
            else:
                field = _FIELD_MAP.get(metric)
                if field and field in raw.columns:
                    _emit(metric, raw[field])

        nonempty = [p for p in parts if not p.empty]
        return pd.concat(nonempty, ignore_index=True) if nonempty else empty_frame()
```
(`epa_correct_pm25` is unchanged. `_to_canonical(metric, corrected)` is a no-op for PM2.5.)

- [ ] **Step 4: Update `tests/test_providers_purpleair.py`.** Switch `Pollutant`→`Metric`; the `_FakeSession` history payload should include the relevant fields (`time_stamp`, `humidity`, `pm2.5_cf_1`, `pm10.0_cf_1`, `temperature`). Update the existing fetch tests to pass `metrics=[...]` (Metric) and consume the generator (already list+concat from the prior feature). Add a melt/conversion test:
```python
def test_parse_history_emits_corrected_and_raw_and_converts_temp():
    payload = {
        "fields": ["time_stamp", "humidity", "pm2.5_cf_1", "pm2.5_atm", "temperature"],
        "data": [[1781996400, 50.0, 100.0, 60.0, 32.0]],
    }
    provider = PurpleAirProvider(purpleair_key="k")
    df = provider._parse_history(
        payload, sensor_id="123", lat=34.0, lon=-118.2, county_fips="06037",
        wanted=[Metric.PM2_5, Metric.PM2_5_CF1, Metric.PM2_5_ATM, Metric.TEMP], agg=10)
    by = {m: df[df["metric"] == m.value]["value"].iloc[0]
          for m in (Metric.PM2_5, Metric.PM2_5_CF1, Metric.PM2_5_ATM, Metric.TEMP)}
    assert by[Metric.PM2_5_CF1] == 100.0
    assert by[Metric.PM2_5_ATM] == 60.0
    assert by[Metric.PM2_5] == pytest.approx(0.524 * 100 - 0.0862 * 50 + 5.75, abs=1e-6)
    assert by[Metric.TEMP] == pytest.approx(0.0, abs=1e-9)  # 32°F
    # AQI only on the corrected PM2.5
    assert df[df["metric"] == Metric.PM2_5.value]["aqi"].notna().all()
    assert df[df["metric"] == Metric.TEMP.value]["aqi"].isna().all()
```

- [ ] **Step 5: Run, confirm PASS.** `uv run pytest tests/test_providers_purpleair.py -v` then `uv run pytest -q`.

- [ ] **Step 6: Stage.** `git add src/smoke_sense/providers/purpleair.py tests/test_providers_purpleair.py`

---

### Task 5: CLI default-all-metrics + migration script

**Goal:** `--metric` (default = all metrics) on `fetch`, threaded through the fetcher; plus a one-off migration script for existing data.

**Files:**
- Modify: `src/smoke_sense/fetcher.py`, `src/smoke_sense/bin/fetch.py`
- Modify: `tests/test_fetch_cli.py`
- Create: `scripts/migrate_store.py`, `tests/test_migrate_store.py`

**Acceptance Criteria:**
- [ ] `fetch` with no `--metric` requests all metrics (each provider fetches its supported subset); `--metric` filters
- [ ] `fetcher.fetch_county` passes `metrics` through to providers
- [ ] `migrate_store` converts an old-schema day file to the new schema + stations.parquet

**Verify:** `uv run pytest tests/test_fetch_cli.py tests/test_migrate_store.py -v` → all pass

**Steps:**

- [ ] **Step 1: fetcher passes metrics.** In `fetcher.fetch_county`, rename the `pollutants` parameter to `metrics` and pass it to `provider.fetch(fips, run_start, run_end, metrics, actual)`. (Only the name/threading changes.)

- [ ] **Step 2: CLI.** In `bin/fetch.py`: import `from ..data import Metric`; remove `DEFAULT_POLLUTANTS`; replace the `pollutant` option with:
```python
    metric: Optional[List[str]] = typer.Option(None, "--metric", help="Metric(s); default: all available"),
```
In the body, resolve metrics:
```python
    metrics = [Metric(m) for m in metric] if metric else list(Metric)
```
and pass `metrics` to `fetcher.fetch_county(..., metrics, ...)`. Invalid `--metric` raises `ValueError` from `Metric(...)`; wrap to a clear error:
```python
    try:
        metrics = [Metric(m) for m in metric] if metric else list(Metric)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
```
(Each provider intersects `metrics` with its `supported_metrics`, so passing all of `Metric` is correct — unsupported ones are skipped per provider.)

- [ ] **Step 3: Update `tests/test_fetch_cli.py`.** The CLI fakes' `fetch(self, county_fips, start, end, metrics, cadence)` already yield `_fake_frame`; ensure the param is named `metrics`. Add a default-all + override test:
```python
def test_metric_default_all_and_override(tmp_path, monkeypatch):
    from smoke_sense.bin import fetch as fetch_mod
    seen = {}

    class FakeProvider:
        name = "aqs"
        supported_metrics = {Metric.PM2_5, Metric.TEMP}
        def resolve_cadence(self, r): return 60
        def fetch(self, county_fips, start, end, metrics, cadence):
            seen["metrics"] = list(metrics)
            yield _fake_frame(county_fips)

    monkeypatch.setattr(fetch_mod, "_resolve_providers",
                        lambda sources, creds: [FakeProvider()])
    runner.invoke(app, ["fetch", "06037", "--start", "2023-07-01", "--end", "2023-07-01",
                        "--credentials", str(tmp_path / "absent.json"),
                        "--output", str(tmp_path)])
    assert set(seen["metrics"]) == set(Metric)  # default = all

    runner.invoke(app, ["fetch", "06037", "--start", "2023-07-01", "--end", "2023-07-01",
                        "--metric", "PM2.5", "--credentials", str(tmp_path / "absent.json"),
                        "--output", str(tmp_path)])
    assert seen["metrics"] == [Metric.PM2_5]
```
(`_fake_frame` already builds a valid new-schema row from Task 1; `Metric` must be imported in the test file.)

- [ ] **Step 4: Migration script `scripts/migrate_store.py`.**
```python
"""One-off: migrate existing day files to the metric/station-table schema.

Run once: uv run python scripts/migrate_store.py [DATA_DIR]   (default ./data)

For each {data_dir}/{fips}/{date}.parquet with the old schema (pollutant + lat/lon
[+ unit]): rename pollutant->metric, drop unit, split (station_id, source, lat, lon)
into {fips}/stations.parquet, and rewrite the day file (zstd, new schema). Idempotent:
files already in the new schema are skipped.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from smoke_sense import store


def migrate_file(path: Path, data_dir: Path, fips: str) -> bool:
    df = pd.read_parquet(path)
    if "pollutant" not in df.columns and "metric" in df.columns:
        return False  # already migrated
    if "pollutant" in df.columns:
        df = df.rename(columns={"pollutant": "metric"})
    df = df.drop(columns=[c for c in ("unit",) if c in df.columns])
    # store.write extracts station metadata (needs lat/lon) and drops them
    store.write(data_dir, fips, df)
    return True


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("./data")
    migrated = 0
    for county_dir in sorted(p for p in data_dir.glob("*") if p.is_dir()):
        fips = county_dir.name
        for f in sorted(county_dir.glob("*.parquet")):
            if f.name == "stations.parquet":
                continue
            if migrate_file(f, data_dir, fips):
                migrated += 1
                print(f"migrated {f}")
    print(f"done: {migrated} file(s) migrated")


if __name__ == "__main__":
    main()
```
Note: `store.write` merges into the day file (read-merge-rewrite). Since `migrate_file` reads the same day and writes it back, merging is idempotent (finer-cadence dedup keeps one row per identity).

- [ ] **Step 5: `tests/test_migrate_store.py`.**
```python
import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd

from smoke_sense import data, store

_SPEC = importlib.util.spec_from_file_location(
    "migrate_store",
    Path(__file__).resolve().parents[1] / "scripts" / "migrate_store.py",
)
migrate_store = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migrate_store)


def test_migrate_old_schema_file(tmp_path):
    old = pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-06-16T01:00:00", tz="UTC"),
        "county_fips": "06037", "station_id": "s1",
        "latitude": 34.0, "longitude": -118.2,
        "pollutant": "PM2.5", "value": 12.0, "unit": "µg/m³",
        "aqi": 52, "agg_window": 10, "source": "purpleair",
    }])
    day = tmp_path / "06037" / "2026-06-16.parquet"
    day.parent.mkdir(parents=True)
    old.to_parquet(day, index=False)

    migrate_store.migrate_file(day, tmp_path, "06037")

    new = data.read_parquet(day)
    assert "metric" in new.columns
    assert "unit" not in new.columns and "latitude" not in new.columns
    assert new["metric"].iloc[0] == "PM2.5"
    stations = pd.read_parquet(store.stations_path(tmp_path, "06037"))
    assert stations["station_id"].tolist() == ["s1"]
```

- [ ] **Step 6: Run, confirm PASS.** `uv run pytest tests/test_fetch_cli.py tests/test_migrate_store.py -v`, then `uv run pytest -q`, then `uv run smoke-sense fetch --help` (shows `--metric`).

- [ ] **Step 7: Stage.** `git add src/smoke_sense/fetcher.py src/smoke_sense/bin/fetch.py tests/test_fetch_cli.py scripts/migrate_store.py tests/test_migrate_store.py`

---

## Self-Review

**Spec coverage:**
- `Metric` (enum-properties), unit, has_aqi, symmetric lookup → Task 0 ✓
- Schema metric/no-unit/no-lat-lon + zstd; aqi on Metric → Task 1 ✓
- Per-county `stations.parquet`; lat/lon split → Task 2 ✓
- AQS curated set + ≤5-param batching + unit conversion → Task 3 ✓
- PurpleAir all fields + melt + conversion + corrected/raw PM2.5 + raw variants → Task 4 ✓
- Default all metrics + `--metric` override; fetcher threading → Task 5 ✓
- Migration script → Task 5 ✓
- summary by metric → Task 1 (Step 5) ✓
- enum-properties dependency → Task 0 ✓

**Placeholder scan:** none. Two explicit verification steps (enum-properties import surface in Task 0; AQS meteorology codes/units in Task 3) are real execution checks against installed package / AQS docs, not deferred work.

**Type/name consistency:** `Metric` everywhere (replaces `Pollutant`); `supported_metrics`; `fetch(..., metrics, cadence)` generator across base/aqs/purpleair/fakes; `_AQS_CODES`/`_CODE_TO_METRIC`, `_FIELD_MAP`/`_CORRECTED_PM25`; `store.stations_path`/`_merge_stations`; summary output key `metrics`. The day-file schema (no unit/lat-lon) vs the provider frame (carries lat/lon) distinction is handled by `store.write` extracting before `validate`.

**Note:** This is a large refactor; Task 1 is the cross-cutting rename and must leave the suite green before Tasks 2–5 build on it. The AQS meteorology parameter codes/units are the one external-data detail to verify during Task 3.
