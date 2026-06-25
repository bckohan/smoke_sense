# Fetch All Available Metrics — Design

**Date:** 2026-06-25
**Status:** Approved (design phase)
**Scope:** Generalize the data model from three smoke pollutants to a full per-source
**metric** set, fetch everything available by default, let the CLI override the set, and
normalize storage (metric column, standard units, station-metadata table, zstd).

## Goal

Fetch all metrics each provider offers (AQS: criteria pollutants + basic meteorology;
PurpleAir: all measurement fields), with a provider-agnostic `Metric` enum and
provider-owned source mappings. Default is "everything available"; `--metric` overrides.
Store data in standardized units, with station coordinates normalized out of the
per-observation rows.

## Key Decisions

| Decision | Choice |
|---|---|
| Metric model | `enum-properties` enum: canonical name, standard `unit`, `has_aqi`; symmetric name lookup. No provider specifics on the enum. |
| Source mappings | Each provider owns its `Metric → code/field` map + `supported_metrics`, exposed via the interface. |
| Units | Standardized per metric; providers convert to `Metric.unit` before emitting. `unit` column removed from storage. |
| Layout | Long (one row per timestamp/station/metric); `pollutant`→`metric`. |
| Coordinates | Per-county `stations.parquet` (station_id, source, latitude, longitude); lat/lon removed from rows. |
| Compression | Parquet `zstd`. |
| AQS scope | Curated comprehensive: criteria pollutants (PM2.5 FRM+non-FRM→PM2.5, PM10, O3, CO, SO2, NO2, Pb) + basic meteorology (temperature, humidity, pressure, wind speed/dir). |
| PurpleAir scope | All documented measurement fields. |
| Default | Fetch all metrics each provider supports; `--metric` overrides. |
| AQI | Computed only for `has_aqi` metrics; `aqi` null otherwise. |
| Dependency | add `enum-properties`. |
| Migration | one-off script converts existing day files to the new schema. |

## Metric model (`data.py`)

A single `Metric` enum (via `enum-properties`) carries domain facts only:

```python
from enum_properties import EnumProperties, Symmetric


class Metric(EnumProperties):
    label: Annotated[str, Symmetric(case_fold=True)]
    unit: str
    has_aqi: bool

    #        value          label          unit     has_aqi
    PM2_5     = "PM2.5",     "PM2.5",     "µg/m³",  True   # canonical (AQS FRM / PA EPA-corrected)
    PM2_5_CF1 = "PM2.5_CF1", "PM2.5_CF1", "µg/m³",  False  # PurpleAir raw cf_1
    PM2_5_ATM = "PM2.5_ATM", "PM2.5_ATM", "µg/m³",  False  # PurpleAir raw atm
    PM10      = "PM10",      "PM10",      "µg/m³",  True   # canonical (AQS FRM / PA uncorrected cf_1)
    PM10_CF1  = "PM10_CF1",  "PM10_CF1",  "µg/m³",  False
    PM10_ATM  = "PM10_ATM",  "PM10_ATM",  "µg/m³",  False
    PM1_0_CF1 = "PM1.0_CF1", "PM1.0_CF1", "µg/m³",  False
    PM1_0_ATM = "PM1.0_ATM", "PM1.0_ATM", "µg/m³",  False
    O3        = "O3",        "O3",        "ppm",    True
    CO        = "CO",        "CO",        "ppm",    False
    SO2       = "SO2",       "SO2",       "ppb",    False
    NO2       = "NO2",       "NO2",       "ppb",    False
    PB        = "Pb",        "Pb",        "µg/m³",  False
    TEMP      = "temperature", "temperature", "°C", False
    RH        = "humidity",    "humidity",    "%",  False
    PRESSURE  = "pressure",    "pressure",    "hPa", False
    WIND_SPEED = "wind_speed", "wind_speed",  "m/s", False
    WIND_DIR   = "wind_dir",   "wind_dir",    "deg", False
    VOC       = "VOC",       "VOC",       "iaq",    False
```

**PurpleAir PM family:** raw variants are stored alongside a corrected canonical:

| Metric | PurpleAir source | has_aqi |
|---|---|---|
| `PM2.5` | `pm2.5_cf_1` + humidity → EPA-corrected | ✓ |
| `PM2.5_CF1` / `PM2.5_ATM` | `pm2.5_cf_1` / `pm2.5_atm` raw | ✗ |
| `PM10` | `pm10.0_cf_1` (uncorrected — no standard PA PM10 correction) | ✓ |
| `PM10_CF1` / `PM10_ATM` | `pm10.0_cf_1` / `pm10.0_atm` raw | ✗ |
| `PM1.0_CF1` / `PM1.0_ATM` | `pm1.0_cf_1` / `pm1.0_atm` raw | ✗ |

AQS populates the canonical `PM2.5`/`PM10`/`O3` (FRM mass, already comparable) + gases +
meteorology. Canonical `PM10` therefore mixes AQS-grade and uncorrected-PurpleAir values,
distinguished by the `source` column; PM2.5 is the only PurpleAir metric with a real
correction.

- `Metric.unit` is the only unit ever stored; AQI breakpoints assume these units.
- Symmetric `label` gives case-insensitive lookup (`Metric("pm2.5")`), replacing the
  hand-rolled `from_str`.
- `has_aqi` marks metrics for which NowCast/breakpoint AQI is computed (PM2.5, PM10, O3).
- The exact enum membership/units are finalized in the plan; this is the agreed shape.
  (`enum-properties` API will be pinned during planning.)

`aqi.py` keys breakpoints/NowCast on the `has_aqi` metrics (rename `Pollutant`→`Metric`).

## Schema (`data.py`)

Observation columns:
```
timestamp(datetime64[ns,UTC]) | county_fips(string) | station_id(string)
| metric(category) | value(float64) | aqi(Int16, nullable) | agg_window(Int16) | source(category)
```
- `pollutant` → `metric`; `latitude`/`longitude`/`unit` **removed**.
- `REQUIRED_NON_NULL`: timestamp, county_fips, station_id, metric, value, agg_window, source.
- `write_parquet` uses `compression="zstd"`.

## Station table (`store.py`)

`{data_dir}/{fips}/stations.parquet`: `station_id, source, latitude, longitude` (zstd),
deduped on `(station_id, source)`.

Providers emit in-memory frames that still carry `latitude`/`longitude` alongside the
observation columns. `store.write`:
1. extracts unique `(station_id, source, latitude, longitude)` → merges into
   `stations.parquet`;
2. `data.validate` (selects only canonical columns, dropping lat/lon) → split by UTC day
   → `merge_day` (zstd).

Dedup identity in `merge_day` becomes `(timestamp, station_id, metric, source)`.
`coverage` and `read_range` are unchanged except for the `metric` rename. Downstream
consumers that need coordinates join `stations.parquet` on `station_id`.

## Providers

`base.AQIProvider`:
- `supported_metrics: set[Metric]` (replaces `supported`).
- `fetch(county_fips, start, end, metrics: list[Metric], cadence) -> Iterator[pd.DataFrame]`
  (generator; `metrics` replaces `pollutants`). `resolve_cadence` unchanged.
- Each provider holds a private `Metric → code/field` map and a unit-conversion step;
  only `supported_metrics` is part of the public interface.

**AQS (`aqs.py`):**
- Private `Metric → AQS parameter code` map; `supported_metrics` = its keys.
- `fetch`: wanted = metrics ∩ supported; map to codes; **batch codes ≤5 per request**
  (AQS limit); per calendar year; yield a chunk per request.
- `_parse`: reverse-map `parameter_code → Metric`, convert each value to `Metric.unit`,
  set `aqi` only for `has_aqi` metrics, carry station_id/lat/lon/source.

**PurpleAir (`purpleair.py`):**
- Private `Metric → field` map; `supported_metrics` = its keys.
- `fetch`: request all wanted fields (plus `humidity` when PM2.5 is wanted, for the EPA
  correction), per surviving sensor (filtering/backoff/chunking unchanged); yield a chunk
  per sensor.
- `_parse_history`: **melt** the wide response into one row per (timestamp, metric),
  **convert units** (e.g. °F→°C), apply the EPA PM2.5 correction for the PM2.5 metric,
  set `aqi` for `has_aqi` metrics, carry the sensor lat/lon.

## CLI (`bin/fetch.py`) & summary

- `--metric` (repeatable, symmetric `Metric` parse) replaces `--pollutant`. **Default =
  all metrics**; each provider fetches the subset it supports. `--cadence`/`--refetch`/
  `-v` unchanged.
- `summary` / `store`: `pollutant` → `metric` (groupby `metric`, per-metric stats); drop
  `unit` and lat/lon references.

## Migration

`scripts/migrate_store.py`: for each existing `{data_dir}/{fips}/{date}.parquet`, read the
old schema (`pollutant` + lat/lon [+ unit]), rename `pollutant`→`metric`, drop `unit`,
extract `(station_id, source, latitude, longitude)` into `{fips}/stations.parquet`, write
the new observation schema with zstd. Idempotent; run once.

## Error Handling

- Unknown/unsupported `--metric` value → clear `typer.BadParameter` via symmetric lookup
  failure.
- A provider simply skips metrics it doesn't support (intersection with
  `supported_metrics`); requesting a metric no provider supports yields nothing.
- Unit-conversion for an unmapped provider unit raises (loud), not silent passthrough.

## Testing

- `Metric`: properties (unit/has_aqi), symmetric case-insensitive lookup, membership.
- Providers: `supported_metrics`; `Metric ↔ code/field` round-trip; unit conversion
  (e.g. PurpleAir °F→°C, AQS gas units); AQS ≤5-param batching; PurpleAir wide→long melt
  + PM2.5 correction; `aqi` only for `has_aqi`.
- `store`: station-table write/merge/dedup; obs files contain no `unit`/lat-lon; merge
  dedup on `(timestamp, station_id, metric, source)`; zstd round-trip.
- `summary`: groups by `metric`; per-metric stats unaffected by the rename.
- CLI: default fetches all metrics; `--metric PM2.5 --metric temperature` filters;
  invalid metric exits non-zero.
- `migrate_store`: a fixture old-schema file converts to the new schema + stations table.

## Out of Scope

- Literal full ~500-code AQS parameter list (curated set instead).
- A PurpleAir-specific PM10 correction (none is published; canonical PM10 from PurpleAir
  is its uncorrected `cf_1`).
- Wide layout; cross-source merging beyond the existing per-day store.
- Backfilling AQI for non-`has_aqi` metrics.
