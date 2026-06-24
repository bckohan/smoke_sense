# Encrypted Credentials & Default End Date — Design

**Date:** 2026-06-24
**Status:** Approved (design phase)
**Scope:** Encrypted, version-controllable `credentials.json` for API credentials, a
`smoke-sense credentials edit` subcommand to manage it, per-credential resolution into
the `fetch` command, and defaulting `--end` to the current date.

## Goal

Let users store AQS/PurpleAir API credentials in an encrypted `credentials.json` that is
safe to commit to version control. When a credential is not supplied via CLI flag or
environment variable, fall back to this file. Decrypt with a password from the
`SMOKESENSE_CREDENTIAL_KEY` environment variable, or prompt for it on the command line.
Also default `fetch --end` to today's date when omitted.

## Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| File management | `smoke-sense credentials edit` (decrypt → `$EDITOR` → re-encrypt) | Flexible editing; no long-lived plaintext |
| Encryption | `cryptography` Fernet + scrypt KDF | Vetted, authenticated encryption; memory-hard KDF |
| Precedence | Per-credential: CLI flag → env var → file | Most flexible; mix sources per credential |
| Decryption timing | Lazy — only if a credential is missing and the file exists | Avoid needless password prompts |
| Password source | env `SMOKESENSE_CREDENTIAL_KEY`, else interactive prompt | Per request |
| `--end` default | `date.today()` when omitted | Per request |
| Crypto/CLI split | Crypto core in `credentials.py`; prompting in `bin/` | Keep core unit-testable |

## Architecture

```
src/smoke_sense/credentials.py        # encrypt/decrypt, load/save, resolve (core, no prompts)
src/smoke_sense/bin/credentials.py    # `credentials edit` subcommand (password + $EDITOR)
src/smoke_sense/bin/fetch.py          # modified: resolve(), --credentials, --end default
src/smoke_sense/bin/__init__.py       # register the credentials command
```

The crypto core has no Typer or prompt coupling. The CLI layer chooses the password
source and the `$EDITOR` round-trip, and injects a `get_password` callable into the core.

### `credentials.py` interface

```python
def encrypt(payload: dict, password: str) -> bytes
def decrypt(blob: bytes, password: str) -> dict          # raises InvalidToken on wrong pw/tamper
def load_file(path: Path, password: str) -> dict
def save_file(path: Path, payload: dict, password: str) -> None

def resolve(
    flags: dict[str, str | None],        # {"email":..., "api_key":..., "purpleair_key":...}
    path: Path,
    get_password: Callable[[], str],
) -> dict[str, str | None]
```

`resolve` returns the merged credential dict. For each key it takes the flag value, else
leaves it `None`. If any value is still `None` **and** `path` exists, it calls
`get_password()` once, decrypts the file, and fills missing keys from the file's payload.
If all flag values are present, `get_password` is never called and the file is never read.

> Note: environment-variable fallback is handled by Typer's `envvar=` on the `fetch`
> options, so by the time values reach `resolve`, "flag" already means "flag or env".
> The `credentials edit` command resolves its own password directly (env → prompt).

## Encrypted File Format

`credentials.json` — a JSON envelope, safe to commit:

```json
{
  "version": 1,
  "kdf": "scrypt",
  "salt": "<base64, 16 random bytes>",
  "ciphertext": "<base64 Fernet token>"
}
```

- Fresh random 16-byte `salt` on every save.
- Key derivation: `scrypt(password, salt, n=2**15, r=8, p=1, dklen=32)` → urlsafe-base64
  → Fernet key.
- `ciphertext`: Fernet token (AES-128-CBC + HMAC) of the UTF-8 JSON of the payload.
  A wrong password or tampering raises `cryptography.fernet.InvalidToken`.

### Decrypted payload schema

```json
{
  "aqs_email": "you@example.com",
  "aqs_api_key": "…",
  "purpleair_api_key": "…"
}
```

Payload keys map to resolver outputs: `aqs_email`→`email`, `aqs_api_key`→`api_key`,
`purpleair_api_key`→`purpleair_key`. Absent keys are treated as not provided.

## `credentials edit` Command

1. Resolve password: env `SMOKESENSE_CREDENTIAL_KEY`, else `getpass` prompt.
2. If `--credentials` file exists, decrypt to a dict; else start from a template with the
   three keys present and blank.
3. Write plaintext JSON to a `tempfile` created with mode `0600`; open `$EDITOR`
   (fallback order: `$EDITOR` → `vi` → `nano`); wait for it to exit.
4. Read and parse the edited file. On invalid JSON: report error, leave the existing
   encrypted file untouched, exit non-zero.
5. Re-encrypt with a new salt and write `credentials.json`.
6. `finally`: delete the temp file even on error/exception.

## `fetch` Changes

- `--end` becomes optional; defaults to `date.today()` when omitted. `--start` stays
  required.
- Add `--credentials PATH` (default `./credentials.json`), shared with `edit`.
- Replace the inline `creds` dict with:
  ```python
  creds = credentials.resolve(
      {"email": email, "api_key": api_key, "purpleair_key": purpleair_key},
      credentials_path,
      get_password=_password_getter(),  # env SMOKESENSE_CREDENTIAL_KEY else prompt
  )
  ```
- Behavior otherwise unchanged: providers still receive the merged creds dict and fail
  fast when a credential they need is missing.

## Error Handling

- Wrong password / corrupt file → catch `InvalidToken`, re-raise as a clear CLI error
  ("could not decrypt credentials.json — wrong password?"). No silent fallback.
- `--credentials` path missing: on `edit` → treat as new file; on `fetch` → ignored
  (no file-sourced creds), not an error.
- Invalid JSON after editing → message + non-zero exit; original file untouched.
- Empty/whitespace password → reject with a clear message before deriving a key.

## Testing

- `credentials.py`: encrypt→decrypt round-trip; wrong password raises `InvalidToken`;
  `resolve` precedence (flag beats file) per credential; `resolve` does NOT call
  `get_password` when all flags present (assert via a callable that raises if called);
  lazy decryption only when a credential is missing and file exists; missing file →
  flags returned unchanged.
- `fetch` CLI: `--end` omitted defaults to today (monkeypatch providers; assert output
  filename date); credentials sourced from a file fixture via a stubbed password getter.
- `credentials edit`: monkeypatch `$EDITOR` to a script that rewrites the temp JSON;
  assert the re-encrypted file round-trips and the temp file is removed; invalid-JSON
  path leaves the original file intact.

## Dependencies

Add: `cryptography`. No changes to the data schema or Parquet I/O.

## Out of Scope

- Multiple credential profiles / key rotation tooling.
- Storing non-credential configuration in the file.
- Changing provider credential semantics beyond their source.
