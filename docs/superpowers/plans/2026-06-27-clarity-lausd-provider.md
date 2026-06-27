# Clarity (LAUSD OpenMap) Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `clarity` air-quality provider that pulls 30 days of hourly LAUSD sensor data from the Clarity OpenMap's unauthenticated REST/CSV API.

**Architecture:** A new `ClarityProvider(AQIProvider)` in `src/smoke_sense/providers/clarity.py`, registered via `@register`. It lists stations from `/api/v1/map/air-quality-markers?network=lausd`, downloads each station's `/api/v1/datasources/{id}/measurements.csv?networkId=lausd`, parses the CSV into the common schema, filters stations to the requested county polygon and rows to the requested date window. A browser `User-Agent` header is required (a non-browser UA gets the SPA's HTML fallback); no credentials. Mirrors the existing PurpleAir provider's structure (`_get` with 429/5xx backoff, polygon filtering, per-station fan-out).

**Tech Stack:** Python 3.12, `requests`, `pandas`, `pytest`. Reuses `smoke_sense.geo` (`county_polygon`, `point_in_polygon`), `smoke_sense.data` (`Metric`, `empty_frame`), and the `providers.base` registry.

**Reference spec:** `docs/superpowers/specs/2026-06-27-clarity-lausd-provider-design.md`

---

### Task 1: Provider skeleton — registration, metric map, `_get` (browser UA + backoff + HTML guard)

**Goal:** A registered `ClarityProvider` with its metric mapping and a shared `_get` helper that sends a browser User-Agent, retries 429/5xx, and rejects the SPA HTML fallback.

**Files:**
- Create: `src/smoke_sense/providers/clarity.py`
- Modify: `src/smoke_sense/providers/__init__.py:6` (add `clarity` to the side-effect import)
- Create: `tests/test_providers_clarity.py`

**Acceptance Criteria:**
- [ ] `get_provider("clarity")` returns a `ClarityProvider`; `"clarity"` is in `all_providers()`.
- [ ] `ClarityProvider.supported_metrics == {PM2_5, PM10, NO2, TEMP, RH, WIND_SPEED, WIND_DIR}` and `supported_cadences == [60]`.
- [ ] `_get` sends a `User-Agent` header containing `"Mozilla"`.
- [ ] `_get` retries on 429 (honoring `Retry-After`, else exponential 2s→4s…cap 60s, max 5), then raises.
- [ ] `_get` raises `ValueError` (mentioning User-Agent) when the response is `text/html`.
- [ ] Constructing with foreign kwargs (`email=`, `api_key=`, `purpleair_key=`) succeeds (kwargs ignored).

**Verify:** `uv run pytest tests/test_providers_clarity.py -v` → all pass.

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_providers_clarity.py`:

```python
from datetime import date

import pandas as pd
import pytest
import requests

from smoke_sense import data
from smoke_sense.data import Metric
from smoke_sense.providers import all_providers, get_provider
from smoke_sense.providers.clarity import ClarityProvider


class _FakeResp:
    def __init__(self, *, json_data=None, text="", status_code=200, headers=None):
        self._json = json_data
        self.text = text
        self.status_code = status_code
        # Default to JSON so json() paths work unless a test overrides it.
        self.headers = headers or {"Content-Type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self._json


def test_registered():
    assert "clarity" in all_providers()
    assert isinstance(get_provider("clarity"), ClarityProvider)


def test_supported_metrics_and_cadence():
    assert ClarityProvider.supported_metrics == {
        Metric.PM2_5, Metric.PM10, Metric.NO2,
        Metric.TEMP, Metric.RH, Metric.WIND_SPEED, Metric.WIND_DIR,
    }
    assert ClarityProvider.supported_cadences == [60]


def test_constructs_with_foreign_credentials():
    # The CLI passes a shared creds dict to every provider; clarity needs none
    # and must not choke on another provider's keys.
    provider = ClarityProvider(email="a@b.com", api_key="AQSKEY", purpleair_key="PK")
    assert isinstance(provider, ClarityProvider)


def test_get_sends_browser_user_agent():
    seen = {}

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            seen["headers"] = headers
            return _FakeResp(json_data={"ok": True})

    provider = ClarityProvider(session=S())
    assert provider._get("https://x", {}) == {"ok": True}
    assert "Mozilla" in seen["headers"]["User-Agent"]


def test_get_retries_on_429_with_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr("smoke_sense.providers.clarity.time.sleep", slept.append)
    responses = [
        _FakeResp(status_code=429, headers={"Retry-After": "7"}),
        _FakeResp(json_data={"ok": True}),
    ]

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return responses.pop(0)

    provider = ClarityProvider(session=S())
    assert provider._get("https://x", {}) == {"ok": True}
    assert slept == [7.0]


def test_get_backoff_without_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr("smoke_sense.providers.clarity.time.sleep", slept.append)
    responses = [
        _FakeResp(status_code=503),
        _FakeResp(status_code=429),
        _FakeResp(json_data={"ok": True}),
    ]

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return responses.pop(0)

    provider = ClarityProvider(session=S())
    assert provider._get("https://x", {}) == {"ok": True}
    assert slept == [2.0, 4.0]


def test_get_raises_after_max_retries(monkeypatch):
    monkeypatch.setattr("smoke_sense.providers.clarity.time.sleep", lambda *_: None)

    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return _FakeResp(status_code=429)

    provider = ClarityProvider(session=S())
    with pytest.raises(requests.HTTPError):
        provider._get("https://x", {})


def test_get_rejects_html_spa_fallback():
    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return _FakeResp(text="<!DOCTYPE html>",
                             headers={"Content-Type": "text/html; charset=utf-8"})

    provider = ClarityProvider(session=S())
    with pytest.raises(ValueError, match="User-Agent"):
        provider._get("https://x", {}, as_text=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers_clarity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smoke_sense.providers.clarity'`.

- [ ] **Step 3: Create the provider module**

Create `src/smoke_sense/providers/clarity.py`:

```python
"""Clarity OpenMap (LAUSD) provider — 30 days of hourly station data.

Pulls from the LAUSD Clarity OpenMap's unauthenticated REST/CSV API. The
endpoints require a browser User-Agent (a non-browser UA receives the SPA's
HTML shell instead of JSON/CSV); no credentials, cookies, or tokens are needed.
"""

from __future__ import annotations

import io
import logging
import time
from datetime import date

import pandas as pd
import requests

from ..data import Metric, empty_frame
from ..geo import county_polygon, point_in_polygon
from .base import AQIProvider, register

_BASE = "https://lausd.map.clarity.io"
_NETWORK = "lausd"
_MARKERS_URL = f"{_BASE}/api/v1/map/air-quality-markers"
_CSV_URL = f"{_BASE}/api/v1/datasources/{{datasource_id}}/measurements.csv"

# A desktop Chrome UA: the OpenMap edge serves JSON/CSV only to browser-like
# clients and otherwise returns its SPA index.html.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# CSV column -> (canonical Metric, AQI column or None). The AQI column carries
# Clarity's own EPA AQI; we use it directly because the concentration columns
# are already NowCast-smoothed (recomputing would double-smooth).
_COLUMNS: dict[str, tuple[Metric, str | None]] = {
    "pm2_5ConcMassNowcast":        (Metric.PM2_5, "pm2_5ConcMassNowcastUsEpaAqi"),
    "pm10ConcMassNowcast":         (Metric.PM10,  "pm10ConcMassNowcastUsEpaAqi"),
    "no2Conc1HourMean":            (Metric.NO2,   "no2Conc1HourMeanUsEpaAqi"),
    "temperatureAmbient1HourMean": (Metric.TEMP,  None),
    "relHumidAmbient1HourMean":    (Metric.RH,    None),
    "windSpeed1HourMean":          (Metric.WIND_SPEED, None),
    "windDirection1HourMean":      (Metric.WIND_DIR,   None),
}

# Clarity reports NO2 in µg/m³; the canonical NO2 unit is ppb. Convert at
# 25 °C / 1 atm where 1 ppb NO2 == 1.88 µg/m³.
_NO2_UGM3_PER_PPB = 1.88


def _to_canonical(metric: Metric, values: pd.Series) -> pd.Series:
    if metric is Metric.NO2:          # µg/m³ -> ppb
        return values / _NO2_UGM3_PER_PPB
    return values


def empty_frame_with_coords() -> pd.DataFrame:
    df = empty_frame()
    df["latitude"] = pd.Series(dtype="float64")
    df["longitude"] = pd.Series(dtype="float64")
    return df


logger = logging.getLogger(__name__)


@register
class ClarityProvider(AQIProvider):
    name = "clarity"
    supported_metrics = {m for m, _ in _COLUMNS.values()}
    supported_cadences = [60]

    _MAX_RETRIES = 5
    _RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

    def __init__(self, session: requests.Session | None = None,
                 user_agent: str | None = None, **kwargs) -> None:
        # clarity needs no credentials; other providers' creds arrive via the
        # shared CLI dict (**kwargs) and are intentionally ignored.
        self.session = session or requests.Session()
        self.user_agent = user_agent or _USER_AGENT

    def _get(self, url: str, params: dict, *, as_text: bool = False):
        """GET with a browser UA; retry 429/5xx; reject the SPA HTML fallback."""
        delay = 2.0
        resp = None
        for attempt in range(self._MAX_RETRIES + 1):
            started = time.monotonic()
            resp = self.session.get(
                url, headers={"User-Agent": self.user_agent},
                params=params, timeout=120)
            elapsed_ms = (time.monotonic() - started) * 1000
            logger.info("GET %s params=%s -> %s (%.0f ms)",
                        url, params, resp.status_code, elapsed_ms)
            if resp.status_code in self._RETRY_STATUS and attempt < self._MAX_RETRIES:
                header = resp.headers.get("Retry-After")
                try:
                    wait = float(header) if header is not None else delay
                except (TypeError, ValueError):
                    wait = delay
                logger.info("%s from %s; retrying in %.0fs (attempt %d/%d)",
                            resp.status_code, url, wait, attempt + 1, self._MAX_RETRIES)
                time.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            break
        resp.raise_for_status()
        if "text/html" in resp.headers.get("Content-Type", ""):
            raise ValueError(
                f"Clarity returned HTML from {url} (SPA fallback) — a browser "
                "User-Agent is required or the endpoint changed")
        return resp.text if as_text else resp.json()

    def fetch(self, county_fips, start, end, metrics, cadence: int = 60):
        # Concrete stub so the class isn't abstract and can be registered and
        # constructed now. Replaced with the real orchestration in Task 4.
        raise NotImplementedError  # replaced in Task 4
```

- [ ] **Step 4: Register the provider**

Modify `src/smoke_sense/providers/__init__.py` line 6 from:

```python
from . import aqs, purpleair  # noqa: F401  (import side effect: registration)
```

to:

```python
from . import aqs, clarity, purpleair  # noqa: F401  (import side effect: registration)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers_clarity.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add src/smoke_sense/providers/clarity.py src/smoke_sense/providers/__init__.py tests/test_providers_clarity.py
git commit --no-gpg-sign -m "feat(clarity): provider skeleton, registration, _get with browser UA + backoff"
```

---

### Task 2: Station discovery — `_list_stations` and `_filter_stations`

**Goal:** Parse the markers endpoint into station dicts and filter them to a county polygon.

**Files:**
- Modify: `src/smoke_sense/providers/clarity.py` (add two methods to `ClarityProvider`)
- Modify: `tests/test_providers_clarity.py` (add tests)

**Acceptance Criteria:**
- [ ] `_list_stations()` returns `[{"datasourceId", "name", "lon", "lat"}]`, unpacking `location.coordinates` as `[lon, lat]`.
- [ ] Markers missing `location.coordinates` (or with the wrong length) are skipped.
- [ ] `_filter_stations(stations, geometry)` keeps only stations inside the polygon.

**Verify:** `uv run pytest tests/test_providers_clarity.py -v` → all pass.

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers_clarity.py`:

```python
_MARKERS_PAYLOAD = {
    "data": {
        "markers": [
            {"datasourceId": "DAABL1560", "sourceType": "CLARITY_NODE",
             "datasourceName": "Gates ES",
             "location": {"type": "Point", "coordinates": [-118.33, 33.75]}},
            {"datasourceId": "NOCOORDS", "sourceType": "CLARITY_NODE",
             "datasourceName": "Broken", "location": None},
        ]
    }
}


def test_list_stations_parses_and_skips_missing_coords():
    class S:
        def get(self, url, headers=None, params=None, timeout=None):
            return _FakeResp(json_data=_MARKERS_PAYLOAD)

    provider = ClarityProvider(session=S())
    stations = provider._list_stations()
    assert stations == [
        {"datasourceId": "DAABL1560", "name": "Gates ES",
         "lon": -118.33, "lat": 33.75}
    ]


def test_filter_stations_keeps_only_in_polygon():
    stations = [
        {"datasourceId": "IN", "name": "in", "lon": -118.0, "lat": 34.0},
        {"datasourceId": "OUT", "name": "out", "lon": 0.0, "lat": 0.0},
    ]
    geom = {"type": "Polygon",
            "coordinates": [[[-119, 33], [-117, 33], [-117, 35], [-119, 35], [-119, 33]]]}
    kept = ClarityProvider._filter_stations(stations, geom)
    assert [s["datasourceId"] for s in kept] == ["IN"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers_clarity.py -k "list_stations or filter_stations" -v`
Expected: FAIL — `AttributeError: 'ClarityProvider' object has no attribute '_list_stations'`.

- [ ] **Step 3: Add the methods**

Append these methods to the `ClarityProvider` class in `src/smoke_sense/providers/clarity.py`:

```python
    def _list_stations(self) -> list[dict]:
        """Fetch the LAUSD markers and return station dicts with coordinates."""
        payload = self._get(
            _MARKERS_URL, {"network": _NETWORK, "aqiStandard": "US-EPA"})
        stations: list[dict] = []
        for marker in payload.get("data", {}).get("markers", []):
            coords = (marker.get("location") or {}).get("coordinates")
            if not coords or len(coords) != 2:
                continue
            lon, lat = coords
            stations.append({
                "datasourceId": marker["datasourceId"],
                "name": marker.get("datasourceName"),
                "lon": lon,
                "lat": lat,
            })
        return stations

    @staticmethod
    def _filter_stations(stations: list[dict], geometry: dict) -> list[dict]:
        """Keep stations whose location falls inside the county polygon."""
        return [s for s in stations
                if point_in_polygon(s["lon"], s["lat"], geometry)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers_clarity.py -k "list_stations or filter_stations" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/smoke_sense/providers/clarity.py tests/test_providers_clarity.py
git commit --no-gpg-sign -m "feat(clarity): station discovery + polygon filtering"
```

---

### Task 3: CSV parsing — `_parse_csv` (metric map, NO2 conversion, AQI from columns, dropna)

**Goal:** Convert a station's measurements CSV into a common-schema frame.

**Files:**
- Modify: `src/smoke_sense/providers/clarity.py` (add `_parse_csv` to `ClarityProvider`)
- Modify: `tests/test_providers_clarity.py` (add tests)

**Acceptance Criteria:**
- [ ] Each present, wanted column maps to its `Metric` with the canonical value; NO2 is divided by 1.88.
- [ ] `aqi` for AQI metrics comes from the matching `*UsEpaAqi` column (as `Int16`); temp/RH/wind rows have `aqi` NA.
- [ ] Empty CSV cells produce no rows for that (timestamp, metric) — a node reporting only PM2.5 yields only PM2.5.
- [ ] `"time (UTC)"` is parsed as a UTC timestamp.
- [ ] Only metrics in `wanted` are emitted; the frame passes `data.validate`.

**Verify:** `uv run pytest tests/test_providers_clarity.py -v` → all pass.

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers_clarity.py`:

```python
# Row 1: every sensor populated. Row 2: only PM2.5 (a typical school node).
_CSV = (
    '"time (UTC)","time (America/Los_Angeles)",'
    '"no2Conc1HourMean","no2Conc1HourMeanUsEpaAqi",'
    '"pm10ConcMassNowcast","pm10ConcMassNowcastUsEpaAqi",'
    '"pm2_5ConcMassNowcast","pm2_5ConcMassNowcastUsEpaAqi",'
    '"relHumidAmbient1HourMean","temperatureAmbient1HourMean",'
    '"windDirection1HourMean","windSpeed1HourMean"\n'
    "2026-06-24T07:00:00.000,2026-06-24T00:00:00.000,"
    "18.8,11,30.0,28,12.0,50,44.0,20.0,180.0,2.5\n"
    "2026-06-25T07:00:00.000,2026-06-25T00:00:00.000,"
    ",,,,4.5,24,,,,\n"
)

_STATION = {"datasourceId": "DAABL1560", "name": "Gates ES",
            "lon": -118.33, "lat": 33.75}

_ALL_METRICS = [Metric.PM2_5, Metric.PM10, Metric.NO2,
                Metric.TEMP, Metric.RH, Metric.WIND_SPEED, Metric.WIND_DIR]


def test_parse_csv_maps_metrics_aqi_and_units():
    provider = ClarityProvider()
    df = provider._parse_csv(_CSV, _STATION, "06037", _ALL_METRICS)
    df = data.validate(df)

    first = df[df["timestamp"] == pd.Timestamp("2026-06-24T07:00:00Z")]
    by_value = {Metric(m): first[first["metric"] == m]["value"].iloc[0]
                for m in first["metric"].unique()}
    assert by_value[Metric.PM2_5] == pytest.approx(12.0)
    assert by_value[Metric.PM10] == pytest.approx(30.0)
    assert by_value[Metric.NO2] == pytest.approx(18.8 / 1.88)  # µg/m³ -> ppb
    assert by_value[Metric.TEMP] == pytest.approx(20.0)
    assert by_value[Metric.RH] == pytest.approx(44.0)
    assert by_value[Metric.WIND_SPEED] == pytest.approx(2.5)
    assert by_value[Metric.WIND_DIR] == pytest.approx(180.0)

    # AQI taken from Clarity's columns for AQI metrics; NA otherwise.
    pm25 = first[first["metric"] == Metric.PM2_5.value]
    assert pm25["aqi"].iloc[0] == 50
    no2 = first[first["metric"] == Metric.NO2.value]
    assert no2["aqi"].iloc[0] == 11
    temp = first[first["metric"] == Metric.TEMP.value]
    assert pd.isna(temp["aqi"].iloc[0])

    assert (df["source"] == "clarity").all()
    assert (df["agg_window"] == 60).all()
    assert df["station_id"].iloc[0] == "DAABL1560"


def test_parse_csv_drops_empty_cells():
    provider = ClarityProvider()
    df = provider._parse_csv(_CSV, _STATION, "06037", _ALL_METRICS)
    second = df[df["timestamp"] == pd.Timestamp("2026-06-25T07:00:00Z")]
    # Only PM2.5 is populated in row 2.
    assert set(second["metric"]) == {Metric.PM2_5.value}
    assert second["value"].iloc[0] == pytest.approx(4.5)


def test_parse_csv_only_wanted_metrics():
    provider = ClarityProvider()
    df = provider._parse_csv(_CSV, _STATION, "06037", [Metric.PM2_5])
    assert set(df["metric"]) == {Metric.PM2_5.value}


def test_parse_csv_empty_when_no_time_column():
    provider = ClarityProvider()
    df = provider._parse_csv("garbage,header\n1,2\n", _STATION, "06037", _ALL_METRICS)
    assert df.empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers_clarity.py -k parse_csv -v`
Expected: FAIL — `AttributeError: 'ClarityProvider' object has no attribute '_parse_csv'`.

- [ ] **Step 3: Add `_parse_csv`**

Append this method to the `ClarityProvider` class in `src/smoke_sense/providers/clarity.py`:

```python
    def _parse_csv(self, text: str, station: dict, county_fips: str,
                   wanted: list[Metric]) -> pd.DataFrame:
        """Convert a station measurements CSV to a common-schema frame."""
        raw = pd.read_csv(io.StringIO(text))
        if "time (UTC)" not in raw.columns:
            return empty_frame_with_coords()
        timestamps = pd.to_datetime(raw["time (UTC)"], utc=True)
        parts: list[pd.DataFrame] = []
        for column, (metric, aqi_col) in _COLUMNS.items():
            if metric not in wanted or column not in raw.columns:
                continue
            values = _to_canonical(metric, raw[column].astype("float64"))
            if aqi_col and aqi_col in raw.columns:
                # round() keeps NaN as NaN; Int16 maps NaN -> pd.NA.
                aqi = raw[aqi_col].astype("float64").round().astype("Int16")
            else:
                aqi = pd.array([pd.NA] * len(raw), dtype="Int16")
            part = pd.DataFrame({
                "timestamp": timestamps,
                "county_fips": county_fips,
                "station_id": str(station["datasourceId"]),
                "latitude": float(station["lat"]),
                "longitude": float(station["lon"]),
                "metric": metric.value,
                "value": values,
                "aqi": aqi,
                "agg_window": 60,
                "source": "clarity",
            }).dropna(subset=["value"])
            if not part.empty:
                parts.append(part)
        if not parts:
            return empty_frame_with_coords()
        return pd.concat(parts, ignore_index=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers_clarity.py -k parse_csv -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/smoke_sense/providers/clarity.py tests/test_providers_clarity.py
git commit --no-gpg-sign -m "feat(clarity): CSV parsing with NO2 conversion and Clarity AQI"
```

---

### Task 4: `fetch` — end-to-end orchestration with county gating and date-window filtering

**Goal:** Implement `fetch()` tying discovery, download, parsing, county filtering, and window filtering together.

**Files:**
- Modify: `src/smoke_sense/providers/clarity.py` (add `fetch` to `ClarityProvider`)
- Modify: `tests/test_providers_clarity.py` (add integration tests)

**Acceptance Criteria:**
- [ ] No wanted metrics → yields nothing, makes no requests.
- [ ] Only in-polygon stations are downloaded; rows are filtered to `[start, end]` by UTC date; empty chunks are suppressed.
- [ ] An out-of-polygon station triggers no `measurements.csv` request and no yield.
- [ ] The concatenated output passes `data.validate`.

**Verify:** `uv run pytest tests/test_providers_clarity.py -v` → all pass.

**Steps:**

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers_clarity.py`:

```python
class _FullSession:
    """Routes markers vs CSV by URL and records calls."""

    def __init__(self, csv_text):
        self.calls = []
        self.csv_text = csv_text

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        if url.endswith("/air-quality-markers"):
            return _FakeResp(json_data=_MARKERS_PAYLOAD)
        if url.endswith("/measurements.csv"):
            return _FakeResp(text=self.csv_text,
                             headers={"Content-Type": "text/csv; charset=utf-8"})
        return _FakeResp(text="<!DOCTYPE html>",
                         headers={"Content-Type": "text/html"})


_WORLD = {"type": "Polygon",
          "coordinates": [[[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]]}


@pytest.fixture
def _stub_world(monkeypatch):
    monkeypatch.setattr(
        "smoke_sense.providers.clarity.county_polygon", lambda fips: _WORLD)


def test_fetch_no_wanted_metrics_makes_no_requests():
    session = _FullSession(_CSV)
    provider = ClarityProvider(session=session)
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 24), date(2026, 6, 25), [Metric.O3]))
    assert chunks == []
    assert session.calls == []


def test_fetch_downloads_and_filters_window(_stub_world):
    session = _FullSession(_CSV)
    provider = ClarityProvider(session=session)
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 24), date(2026, 6, 24),  # only the first row's date
        [Metric.PM2_5]))
    df = pd.concat(chunks, ignore_index=True) if chunks else data.empty_frame()
    df = data.validate(df)
    csv_calls = [c for c in session.calls if c["url"].endswith("/measurements.csv")]
    assert len(csv_calls) == 1
    assert csv_calls[0]["params"] == {"networkId": "lausd"}
    # 2026-06-25 row filtered out by the window.
    assert df["timestamp"].dt.date.unique().tolist() == [date(2026, 6, 24)]
    assert (df["metric"] == Metric.PM2_5.value).all()


def test_fetch_excludes_out_of_polygon_station(monkeypatch):
    # Polygon that does NOT contain the marker at (lon -118.33, lat 33.75);
    # guards against a lon/lat swap a world polygon would hide.
    tiny = {"type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    monkeypatch.setattr(
        "smoke_sense.providers.clarity.county_polygon", lambda fips: tiny)
    session = _FullSession(_CSV)
    provider = ClarityProvider(session=session)
    chunks = list(provider.fetch(
        "06037", date(2026, 6, 24), date(2026, 6, 25), [Metric.PM2_5]))
    csv_calls = [c for c in session.calls if c["url"].endswith("/measurements.csv")]
    assert csv_calls == []
    assert chunks == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers_clarity.py -k fetch -v`
Expected: FAIL — the Task 1 stub raises `NotImplementedError` (and `test_fetch_no_wanted_metrics_makes_no_requests` fails because the stub raises before the early return).

- [ ] **Step 3: Replace the `fetch` stub with the real implementation**

Use Edit to replace the Task 1 stub in `src/smoke_sense/providers/clarity.py`.

Replace:

```python
    def fetch(self, county_fips, start, end, metrics, cadence: int = 60):
        # Concrete stub so the class isn't abstract and can be registered and
        # constructed now. Replaced with the real orchestration in Task 4.
        raise NotImplementedError  # replaced in Task 4
```

with:

```python
    def fetch(self, county_fips, start, end, metrics, cadence: int = 60):
        wanted = [m for m in metrics if m in self.supported_metrics]
        if not wanted:
            return
        stations = self._filter_stations(
            self._list_stations(), county_polygon(county_fips))
        if not stations:
            return
        for station in stations:
            text = self._get(
                _CSV_URL.format(datasource_id=station["datasourceId"]),
                {"networkId": _NETWORK}, as_text=True)
            chunk = self._parse_csv(text, station, county_fips, wanted)
            if chunk.empty:
                continue
            in_window = (
                (chunk["timestamp"].dt.date >= start)
                & (chunk["timestamp"].dt.date <= end)
            )
            chunk = chunk[in_window]
            if not chunk.empty:
                yield chunk
```

- [ ] **Step 4: Run the full provider test file**

Run: `uv run pytest tests/test_providers_clarity.py -v`
Expected: PASS (all tasks' tests).

- [ ] **Step 5: Run the whole suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: PASS (existing tests unaffected; the new provider now appears in `all_providers()`).

- [ ] **Step 6: Commit**

```bash
git add src/smoke_sense/providers/clarity.py tests/test_providers_clarity.py
git commit --no-gpg-sign -m "feat(clarity): fetch orchestration with county + window filtering"
```

---

## Notes for the implementer

- **Live smoke test (optional, network):** after Task 4, confirm against the real API:
  `uv run smoke-sense fetch 06037 --start 2026-06-20 --source clarity --metric PM2.5 --verbose`
  (expects per-station GET logs and a written parquet under `./data/06037`).
- **GPG:** commits use `--no-gpg-sign` because this environment's GPG signing prompts interactively.
- **Coords columns:** like the AQS/PurpleAir providers, chunks carry extra `latitude`/`longitude` columns; `data.validate` selects only the canonical columns, so they are dropped on write — this matches existing behavior, no schema change.
- **30-day limit:** the CSV only ever returns the last ~30 days; requesting older `--start` dates simply yields no rows for those days (not an error).
