# Robust Fetch & Request Logging — Design

**Date:** 2026-06-25
**Status:** Approved (design phase)
**Scope:** Make `fetch` durable — flush in-memory data to disk on any interceptable exit
(unhandled error or Ctrl-C) instead of losing it — and add opt-in request logging.

## Goals

1. **Durability:** never silently lose collected data. Write each day file once when all
   its data is gathered, OR flush whatever has been collected if an unhandled error or
   `KeyboardInterrupt` ends the run. (Uncatchable exits — segfault, `SIGKILL` — are out
   of scope.)
2. **Observability:** a `-v/--verbose` flag that logs each provider HTTP request (and
   retries/splits) to stderr, with credentials redacted.

## Key Decisions

| Decision | Choice |
|---|---|
| Provider output | `fetch` becomes a generator yielding DataFrame chunks (per sensor / per year) |
| Write strategy | Fetcher buffers chunks per county; one `store.write` at the end |
| Durability | `try/except BaseException` around gathering → flush buffer → re-raise |
| Logging | stdlib `logging`; `-v/--verbose` attaches a RichHandler to stderr at INFO |
| Redaction | AQS `email`/`key` params replaced with `***` before logging |

## Streaming Providers (`providers/`)

`AQIProvider.fetch(county_fips, start, end, pollutants, cadence) -> Iterator[pd.DataFrame]`
becomes a generator. It yields non-empty `data`-schema chunks at natural units:

- **AQS** (`aqs.py`): yield one chunk per per-calendar-year request (`_parse` result).
- **PurpleAir** (`purpleair.py`): yield one chunk per sensor (`_parse_history` result).
- Empty chunks are not yielded; "no wanted pollutants" yields nothing (bare `return`).

The generator is what lets the fetcher capture partial output: any chunk yielded before
an error is already in the fetcher's buffer.

## Fetcher Buffering & Flush (`fetcher.py`)

```python
def fetch_county(data_dir, fips, start, end, pollutants, requested_cadence,
                 providers, today, refetch=False) -> None:
    cov = store.coverage(data_dir, fips)
    buffer: list[pd.DataFrame] = []
    try:
        for provider in providers:
            actual = provider.resolve_cadence(requested_cadence)
            missing = _missing_days(...)          # unchanged gap logic
            for run_start, run_end in _contiguous_ranges(missing):
                for chunk in provider.fetch(fips, run_start, run_end, pollutants, actual):
                    buffer.append(chunk)
    except BaseException:
        _flush(data_dir, fips, buffer)            # error / Ctrl-C: persist partial
        raise
    _flush(data_dir, fips, buffer)                # normal: write once, all gathered


def _flush(data_dir, fips, buffer):
    if buffer:
        store.write(data_dir, fips, pd.concat(buffer, ignore_index=True))
        buffer.clear()
```

- **Write-once:** on the normal path each day file is written exactly once
  (`store.write` → one `merge_day` per day) — no repeated rewrites.
- **Durability:** `except BaseException` covers unhandled `Exception` and
  `KeyboardInterrupt` (Ctrl-C); the buffer is flushed, then the exception re-raises so the
  command still exits non-zero (failures are not swallowed).
- **Per-county isolation:** buffering is per `fetch_county`, so a completed county is
  already persisted when a later county fails.

### Trade-offs (documented, accepted)

- **Memory:** a county's full run is held in memory until flush.
- **Partial-on-error:** a day flushed partially counts as "covered" for the next
  incremental run and won't auto-heal; `--refetch` completes it. (The current UTC day is
  always re-fetched regardless.)

## Request Logging

- Library logger `logging.getLogger("smoke_sense")`; modules log via
  `logging.getLogger(__name__)`. No handler attached by the library (quiet by default).
- `bin/fetch.py` adds `-v/--verbose`. When set, attach a `rich.logging.RichHandler`
  to the `smoke_sense` logger at `INFO`, writing to **stderr** (stdout stays clean).
- **Logged at INFO:**
  - Each provider HTTP request: `GET <url> params=<redacted> -> <status> (<ms> ms)`
    (timed around the call in PurpleAir `_get` and AQS `_request`).
  - 429 backoff: `429 from <url>; retrying in <n>s (attempt k/<max>)`.
  - PurpleAir adaptive chunk splits.
  - Fetcher: sensors kept after filtering, and per-flush row counts
    (`wrote <n> rows for <fips>`).
- **Redaction:** a `_redact(params)` helper replaces AQS `email` and `key` values with
  `***` before logging. PurpleAir's key travels in the `X-API-Key` header, which is not
  logged (only URL + params are).

## Error Handling

- The flush-then-re-raise preserves existing error surfacing (wrong-password,
  429-exhaustion, single-day 400) — they now persist partial data first.
- A flush failure (disk error during `store.write`) propagates; nothing masks it.

## Testing

- **Streaming providers:** `fetch(...)` is iterable; AQS yields per-year chunks,
  PurpleAir per-sensor chunks; empty/no-wanted yields nothing. Existing tests that
  consumed a DataFrame switch to `pd.concat(list(provider.fetch(...)))`.
- **Fetcher durability:** a fake provider yielding two chunks then raising `RuntimeError`
  → both chunks are written and the error propagates; a fake raising `KeyboardInterrupt`
  → same flush behavior; happy path writes once (spy on `store.write`/`merge_day` for a
  single call per day).
- **Logging:** with `--verbose`, a fake session emits a request log line that does NOT
  contain the AQS key/email (redaction asserted); without `--verbose`, no log output.
- Full suite remains green (provider/fetcher/CLI fakes updated to generators).

## Out of Scope

- Uncatchable exits (segfault, `SIGKILL`).
- Structured/JSON logs, log files, log rotation.
- Per-day mid-run flushing (we flush per county).
- Changes to non-fetch commands.
