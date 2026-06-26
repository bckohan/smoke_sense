# Station-Exclusion Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users drop a known-bad set of sensor stations by ID, as the first check in the existing outlier-filter pipeline, exposed via a repeatable `--exclude-station` flag on `summary` and all five `visualize` subcommands.

**Architecture:** A new `station_mask` and an `exclude_stations` field on `OutlierConfig` make station exclusion a first-class outlier check inside the pure `outliers` module. The `_outlier_cli` plumbing threads a `--exclude-station` list into the config, and each command passes its collected list through the existing `make_filter`/`filter_frame` entry points. Exclusion is gated by the existing `--outlier-filter/--no-outlier-filter` toggle.

**Tech Stack:** Python 3.12, pandas, Typer, Rich, pytest.

Spec: `docs/superpowers/specs/2026-06-26-station-exclusion-design.md`

---

### Task 1: Core station exclusion in `outliers.py`

**Goal:** Add `exclude_stations` to `OutlierConfig`, a `station_mask` helper, and wire it as the first check in `filter_outliers`.

**Files:**
- Modify: `src/smoke_sense/outliers.py`
- Test: `tests/test_outliers.py`

**Acceptance Criteria:**
- [ ] `OutlierConfig` has `exclude_stations: frozenset[str] = frozenset()`.
- [ ] `station_mask(df, exclude_stations)` returns `True` where `station_id` is in the set; all-`False` for an empty set or empty frame.
- [ ] `filter_outliers` drops excluded-station rows and attributes them to `per_check["station"]`, counting each dropped row once even if it would also be a range/zscore/iqr outlier.
- [ ] When `exclude_stations` is empty, no `"station"` key appears in `per_check` (behavior unchanged from today).

**Verify:** `uv run pytest tests/test_outliers.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_outliers.py`:

```python
def test_station_mask_flags_excluded():
    df = _df([
        ("s1", Metric.PM2_5, 10.0),
        ("s2", Metric.PM2_5, 11.0),
        ("s3", Metric.PM2_5, 12.0),
    ])
    mask = outliers.station_mask(df, frozenset({"s2", "s3"}))
    assert mask.tolist() == [False, True, True]


def test_station_mask_empty_set():
    df = _df([("s1", Metric.PM2_5, 10.0)])
    assert outliers.station_mask(df, frozenset()).tolist() == [False]


def test_station_mask_empty_frame():
    df = _df([]).iloc[0:0]
    mask = outliers.station_mask(df, frozenset({"s1"}))
    assert mask.tolist() == []


def test_filter_outliers_excludes_station():
    df = _df([
        ("s1", Metric.PM2_5, 10.0),
        ("s1", Metric.PM2_5, 11.0),
        ("s2", Metric.PM2_5, 12.0),   # excluded
    ])
    cfg = outliers.OutlierConfig(exclude_stations=frozenset({"s2"}))
    clean, report = outliers.filter_outliers(df, cfg)
    assert set(clean["station_id"]) == {"s1"}
    assert report.per_check["station"] == 1
    assert report.per_metric["PM2.5"] == 1
    assert report.total == 1


def test_filter_outliers_station_counts_once_for_range_outlier():
    # s2's row is BOTH excluded and out-of-range; it must count once, under station.
    df = _df([
        ("s1", Metric.PM2_5, 10.0),
        ("s2", Metric.PM2_5, 5000.0),   # excluded AND > 1000 bound
    ])
    cfg = outliers.OutlierConfig(exclude_stations=frozenset({"s2"}))
    clean, report = outliers.filter_outliers(df, cfg)
    assert set(clean["station_id"]) == {"s1"}
    assert report.total == 1
    assert report.per_check["station"] == 1
    assert report.per_check.get("range", 0) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_outliers.py -k "station or excludes" -v`
Expected: FAIL — `AttributeError: module 'smoke_sense.outliers' has no attribute 'station_mask'` and `OutlierConfig` rejecting `exclude_stations`.

- [ ] **Step 3: Add the `exclude_stations` field to `OutlierConfig`**

In `src/smoke_sense/outliers.py`, edit the dataclass (after the `min_group` line):

```python
@dataclass(frozen=True)
class OutlierConfig:
    """Knobs for the outlier filter. Defaults are the code defaults."""

    range_enabled: bool = True
    bounds: dict[Metric, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_BOUNDS))
    zscore: float | None = 3.5           # per-station modified-z threshold; None disables
    iqr: float | None = None             # per-station IQR multiplier; None disables
    min_group: int = 5                   # skip stat checks for smaller groups
    exclude_stations: frozenset[str] = frozenset()   # drop these station IDs wholesale
```

(`frozenset()` is immutable, so a bare default is safe — no `default_factory` needed.)

- [ ] **Step 4: Add `station_mask`**

In `src/smoke_sense/outliers.py`, add after `range_mask` (before `_grouped`):

```python
def station_mask(df: pd.DataFrame,
                 exclude_stations: frozenset[str]) -> pd.Series:
    """True where `station_id` is in the user-given exclusion set.

    An empty set (the default) or empty frame drops nothing.
    """
    if df.empty or not exclude_stations:
        return pd.Series(False, index=df.index)
    return df["station_id"].isin(exclude_stations)
```

- [ ] **Step 5: Prepend the station check in `filter_outliers`**

In `src/smoke_sense/outliers.py`, in `filter_outliers`, change the check assembly so `station` runs first:

```python
    checks: list[tuple[str, pd.Series]] = []
    if config.exclude_stations:
        checks.append(("station", station_mask(df, config.exclude_stations)))
    if config.range_enabled:
        checks.append(("range", range_mask(df, config.bounds)))
    if config.zscore is not None and config.zscore > 0:
        checks.append(("zscore", zscore_mask(df, config.zscore, config.min_group)))
    if config.iqr is not None:
        checks.append(("iqr", iqr_mask(df, config.iqr, config.min_group)))
```

The existing per-check loop already attributes each dropped row to the first matching check via the `already` mask, so station-excluded rows count once under `"station"`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_outliers.py -v`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 7: Commit**

```bash
git add src/smoke_sense/outliers.py tests/test_outliers.py
git commit -m "feat(outliers): station-exclusion check in filter_outliers"
```

---

### Task 2: CLI plumbing in `_outlier_cli.py`

**Goal:** Thread an exclude-station list through `build_config`, `filter_frame`, and `make_filter` into `OutlierConfig.exclude_stations`.

**Files:**
- Modify: `src/smoke_sense/bin/_outlier_cli.py`
- Test: `tests/test_outlier_cli.py`

**Acceptance Criteria:**
- [ ] `build_config(..., exclude_stations=["s1","s2"])` yields `cfg.exclude_stations == frozenset({"s1","s2"})`.
- [ ] `build_config` with no `exclude_stations` yields an empty frozenset (existing callers unbroken).
- [ ] `filter_frame(df, ..., exclude=["s2"])` removes s2's rows and reports them.

**Verify:** `uv run pytest tests/test_outlier_cli.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_outlier_cli.py`:

```python
def test_build_config_exclude_stations():
    cfg = oc.build_config(no_range=False, zscore=None, iqr=None, bounds=[],
                          exclude_stations=["s1", "s2"])
    assert cfg.exclude_stations == frozenset({"s1", "s2"})


def test_build_config_exclude_default_empty():
    cfg = oc.build_config(no_range=False, zscore=None, iqr=None, bounds=[])
    assert cfg.exclude_stations == frozenset()


def test_filter_frame_excludes_stations():
    df = _df([
        ("s1", Metric.PM2_5, 10.0),
        ("s2", Metric.PM2_5, 11.0),
    ])
    clean, report = oc.filter_frame(
        df, enabled=True, no_range=False, zscore=None, iqr_on=False,
        iqr_k=3.0, bound=None, exclude=["s2"])
    assert set(clean["station_id"]) == {"s1"}
    assert report.total == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_outlier_cli.py -k exclude -v`
Expected: FAIL — `build_config()`/`filter_frame()` got an unexpected keyword argument.

- [ ] **Step 3: Add `exclude_stations` to `build_config`**

In `src/smoke_sense/bin/_outlier_cli.py`, update the signature and the `replace` call:

```python
def build_config(*, no_range: bool, zscore: Optional[float], iqr: Optional[float],
                 bounds: list[tuple[Metric, tuple[float, float]]],
                 exclude_stations: Optional[list[str]] = None) -> OutlierConfig:
    """Build an OutlierConfig from DEFAULT_CONFIG plus CLI overrides."""
    merged = dict(DEFAULT_BOUNDS)
    for metric, limits in bounds:
        merged[metric] = limits
    if zscore is None:
        z = DEFAULT_CONFIG.zscore      # keep default
    elif zscore <= 0:
        z = None                       # disable
    else:
        z = zscore                     # set
    return replace(
        DEFAULT_CONFIG,
        range_enabled=not no_range,
        bounds=merged,
        zscore=z,
        # iqr has no "keep default" sentinel: its default is None and callers
        # always derive it from iqr_on/iqr_k.
        iqr=iqr,
        exclude_stations=frozenset(exclude_stations or []),
    )
```

- [ ] **Step 4: Thread `exclude` through `filter_frame` and `make_filter`**

In the same file, update `filter_frame`:

```python
def filter_frame(df: pd.DataFrame, *, enabled: bool, no_range: bool,
                 zscore: Optional[float], iqr_on: bool, iqr_k: float,
                 bound: Optional[list[str]],
                 exclude: Optional[list[str]] = None
                 ) -> tuple[pd.DataFrame, OutlierReport]:
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
                       iqr=(iqr_k if iqr_on else None), bounds=parsed,
                       exclude_stations=exclude)
    clean, report = filter_outliers(df, cfg)
    if report.total:
        logger.info("filtered %d outlier rows %s", report.total, report.per_metric)
    return clean, report
```

And `make_filter`:

```python
def make_filter(*, enabled: bool, no_range: bool, zscore: Optional[float],
                iqr_on: bool, iqr_k: float, bound: Optional[list[str]],
                exclude: Optional[list[str]] = None
                ) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """Return a frame->clean-frame callback capturing the CLI flags."""
    def _filter(df: pd.DataFrame) -> pd.DataFrame:
        clean, _ = filter_frame(df, enabled=enabled, no_range=no_range,
                                zscore=zscore, iqr_on=iqr_on, iqr_k=iqr_k,
                                bound=bound, exclude=exclude)
        return clean
    return _filter
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_outlier_cli.py -v`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 6: Commit**

```bash
git add src/smoke_sense/bin/_outlier_cli.py tests/test_outlier_cli.py
git commit -m "feat(outliers): thread --exclude-station through _outlier_cli"
```

---

### Task 3: `--exclude-station` flag on `summary` and `visualize`

**Goal:** Add a repeatable `--exclude-station` option to `summary` and all five `visualize` subcommands, passing the list into the filter; verify end-to-end.

**Files:**
- Modify: `src/smoke_sense/bin/summary.py`
- Modify: `src/smoke_sense/bin/visualize.py`
- Test: `tests/test_summary.py`
- Test: `tests/test_visualize_cli.py`

**Acceptance Criteria:**
- [ ] `summary ... --exclude-station s2 --json` drops s2's rows and counts them in `filtered`.
- [ ] Each `visualize` subcommand accepts `--exclude-station` and the resulting filter removes those stations' rows.
- [ ] The flag string `--exclude-station` is byte-identical across all six commands.

**Verify:** `uv run pytest tests/test_summary.py tests/test_visualize_cli.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_summary.py`:

```python
def test_summary_cli_excludes_station(tmp_path):
    rows = [
        _cli_row("2026-06-16T01:00:00", Metric.PM2_5, 10.0, "s1"),
        _cli_row("2026-06-16T02:00:00", Metric.PM2_5, 11.0, "s1"),
        _cli_row("2026-06-16T01:00:00", Metric.PM2_5, 12.0, "s2"),
    ]
    store.write(tmp_path, "06037", pd.DataFrame(rows))
    result = runner.invoke(app, [
        "summary", "06037", "--start", "2026-06-16", "--end", "2026-06-16",
        "--output", str(tmp_path), "--json", "--exclude-station", "s2"])
    assert result.exit_code == 0, result.output
    pm = next(m for m in json.loads(result.output)["06037"]["metrics"]
              if m["metric"] == "PM2.5")
    assert pm["stations"] == 1     # only s1 remains
    assert pm["filtered"] == 1     # s2's row counted as filtered
```

Add to `tests/test_visualize_cli.py`:

```python
def test_exclude_station_filter_drops_rows(tmp_path):
    from smoke_sense import visualize as viz
    from smoke_sense.bin import _outlier_cli
    _seed_rich(tmp_path)   # s1 (2 PM2.5 rows) + s2 (1 PM2.5 row)
    f = _outlier_cli.make_filter(enabled=True, no_range=False, zscore=None,
                                 iqr_on=False, iqr_k=3.0, bound=None,
                                 exclude=["s2"])
    obs = viz.metric_observations(tmp_path, "06037", date(2026, 6, 16),
                                  date(2026, 6, 16), Metric.PM2_5, outlier_filter=f)
    assert set(obs["station_id"]) == {"s1"}


def test_series_exclude_station_cli(tmp_path):
    _seed_rich(tmp_path)
    result = runner.invoke(app, [
        "visualize", "series", "06037", "--start", "2026-06-16",
        "--metric", "PM2.5", "--exclude-station", "s2",
        "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_summary.py -k exclude tests/test_visualize_cli.py -k exclude -v`
Expected: FAIL — `summary`/`series` reject the unknown option `--exclude-station`, and `make_filter` rejects `exclude=`.

(If Task 2 is already merged, the `make_filter` failure won't appear; the CLI option failures still will.)

- [ ] **Step 3: Add the option to `summary`**

In `src/smoke_sense/bin/summary.py`, add a parameter to `summary(...)` after `outlier_bound`:

```python
    outlier_bound: Optional[List[str]] = typer.Option(
        None, "--outlier-bound", help="Override a bound: METRIC:LOW:HIGH (repeatable)"),
    exclude_station: Optional[List[str]] = typer.Option(
        None, "--exclude-station",
        help="Drop all rows from this station ID (repeatable)"),
) -> None:
```

Then pass it into the `filter_frame` call:

```python
        clean, report = _outlier_cli.filter_frame(
            raw, enabled=outlier_filter, no_range=no_outlier_range,
            zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
            bound=outlier_bound, exclude=exclude_station)
```

- [ ] **Step 4: Add the option to all five `visualize` subcommands**

In `src/smoke_sense/bin/visualize.py`, for EACH of `mean_map`, `series`, `scatter`, `aggregate`, `histogram`, add this parameter immediately after that command's `outlier_bound` option (before `output_dir`):

```python
    exclude_station: Optional[list[str]] = typer.Option(
        None, "--exclude-station",
        help="Drop all rows from this station ID (repeatable)"),
```

Then, in each command's `make_filter(...)` call, add `exclude=exclude_station`. The five call sites become:

```python
    ofilter = _outlier_cli.make_filter(
        enabled=outlier_filter_on, no_range=no_outlier_range,
        zscore=outlier_zscore, iqr_on=outlier_iqr, iqr_k=outlier_iqr_k,
        bound=outlier_bound, exclude=exclude_station)
```

(`mean_map` uses the same call; apply the identical `exclude=exclude_station` addition there too.)

- [ ] **Step 5: Run the targeted tests**

Run: `uv run pytest tests/test_summary.py tests/test_visualize_cli.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
git add src/smoke_sense/bin/summary.py src/smoke_sense/bin/visualize.py \
        tests/test_summary.py tests/test_visualize_cli.py
git commit -m "feat(outliers): --exclude-station flag on summary and visualize"
```

---

## Self-Review

**Spec coverage:**
- `exclude_stations` on `OutlierConfig` → Task 1, Step 3. ✓
- `station_mask` (match/no-match/empty set/empty frame) → Task 1, Steps 4 + tests. ✓
- First-check attribution under `per_check["station"]`, count-once → Task 1, Step 5 + `test_filter_outliers_station_counts_once_for_range_outlier`. ✓
- `build_config`/`filter_frame`/`make_filter` thread the list → Task 2. ✓
- Repeatable `--exclude-station` on summary + 5 visualize, byte-identical flag → Task 3. ✓
- Gated by `--no-outlier-filter` (exclusion lives inside `filter_frame`, which returns early when `enabled` is False) → inherent in Task 2 plumbing; no extra code. ✓
- No file input, no config file, exact case-sensitive match → satisfied by `isin` on raw strings; no globbing code added. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows full code. ✓

**Type consistency:** `exclude_stations: frozenset[str]` (config) ← `frozenset(exclude_stations or [])` (build_config) ← `exclude: Optional[list[str]]` (filter_frame/make_filter) ← `exclude_station: Optional[List[str]]` (CLI). `station_mask(df, frozenset)` called with `config.exclude_stations` (frozenset). Consistent. ✓
