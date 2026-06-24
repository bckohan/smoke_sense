# Encrypted Credentials & Default End Date Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an encrypted, version-controllable `credentials.json` (managed via `smoke-sense credentials edit`) that supplies API credentials by per-credential fallback, and default `fetch --end` to today's date.

**Architecture:** A pure crypto/resolution core (`credentials.py`, Fernet + scrypt, no prompt coupling) is consumed by the CLI layer. A new `credentials edit` subcommand round-trips the file through `$EDITOR`. `fetch` resolves credentials via the core (flag/env → file, lazy decryption) and defaults `--end` to `date.today()`.

**Tech Stack:** Python 3.12, Typer, `cryptography` (Fernet + scrypt), pytest.

**Reference spec:** `docs/superpowers/specs/2026-06-24-encrypted-credentials-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/smoke_sense/credentials.py` | encrypt/decrypt, load/save, `resolve` (pure core) |
| `src/smoke_sense/bin/credentials.py` | `credentials edit` subcommand + `resolve_password` helper |
| `src/smoke_sense/bin/fetch.py` | modified: `--end` default, `--credentials`, resolve wiring |
| `src/smoke_sense/bin/__init__.py` | register the `credentials` sub-app |
| `tests/test_credentials.py` | core unit tests |
| `tests/test_credentials_cli.py` | `credentials edit` command tests |
| `tests/test_fetch_cli.py` | add `--end` default test; adjust existing test |

---

### Task 0: Add the `cryptography` dependency

**Goal:** Make `cryptography` available as a runtime dependency.

**Files:**
- Modify: `pyproject.toml` (and `uv.lock` via uv)

**Acceptance Criteria:**
- [ ] `cryptography` is a runtime dependency
- [ ] `uv run python -c "import cryptography.fernet"` succeeds

**Verify:** `uv run python -c "from cryptography.fernet import Fernet; print('ok')"` → `ok`

**Steps:**

- [ ] **Step 1: Add the dependency**

Run:
```bash
uv add cryptography
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from cryptography.fernet import Fernet; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Stage (do NOT commit — see commit policy in dispatch)**

```bash
git add pyproject.toml uv.lock
```

---

### Task 1: Crypto & resolution core (`credentials.py`)

**Goal:** Encrypt/decrypt the credentials envelope and resolve credentials per-key with lazy decryption.

**Files:**
- Create: `src/smoke_sense/credentials.py`
- Create: `tests/test_credentials.py`

**Acceptance Criteria:**
- [ ] `encrypt`/`decrypt` round-trip; wrong password raises `InvalidToken`; empty password raises `ValueError`
- [ ] `resolve` prefers flag values and never calls `get_password` when all three flags are present
- [ ] `resolve` fills only missing keys from the file, and returns flags unchanged when the file is absent

**Verify:** `uv run pytest tests/test_credentials.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `tests/test_credentials.py` with EXACTLY this content**

```python
import json

import pytest
from cryptography.fernet import InvalidToken

from smoke_sense import credentials


def test_encrypt_decrypt_round_trip():
    payload = {"aqs_email": "a@b.com", "aqs_api_key": "k"}
    blob = credentials.encrypt(payload, "pw")
    assert credentials.decrypt(blob, "pw") == payload


def test_decrypt_wrong_password_raises():
    blob = credentials.encrypt({"aqs_api_key": "k"}, "right")
    with pytest.raises(InvalidToken):
        credentials.decrypt(blob, "wrong")


def test_encrypt_empty_password_raises():
    with pytest.raises(ValueError):
        credentials.encrypt({"x": "y"}, "  ")


def test_envelope_is_committable_json():
    blob = credentials.encrypt({"aqs_api_key": "k"}, "pw")
    env = json.loads(blob)
    assert env["version"] == 1
    assert env["kdf"] == "scrypt"
    assert "salt" in env and "ciphertext" in env


def test_resolve_prefers_flags_and_skips_password(tmp_path):
    def boom():
        raise AssertionError("get_password must not be called when all flags present")

    flags = {"email": "e", "api_key": "k", "purpleair_key": "p"}
    out = credentials.resolve(flags, tmp_path / "none.json", boom)
    assert out == flags


def test_resolve_missing_file_returns_flags(tmp_path):
    flags = {"email": None, "api_key": "k", "purpleair_key": None}
    out = credentials.resolve(flags, tmp_path / "absent.json", lambda: "pw")
    assert out == flags


def test_resolve_fills_missing_from_file(tmp_path):
    path = tmp_path / "credentials.json"
    credentials.save_file(
        path,
        {"aqs_email": "file@b.com", "aqs_api_key": "fk", "purpleair_api_key": "fp"},
        "pw",
    )
    flags = {"email": "flag@b.com", "api_key": None, "purpleair_key": None}
    out = credentials.resolve(flags, path, lambda: "pw")
    assert out == {"email": "flag@b.com", "api_key": "fk", "purpleair_key": "fp"}
```

- [ ] **Step 2: Run tests, confirm they FAIL (module missing).**

Run: `uv run pytest tests/test_credentials.py -v`

- [ ] **Step 3: Create `src/smoke_sense/credentials.py` with EXACTLY this content**

```python
"""Encrypted credential storage and per-credential resolution.

`credentials.json` is a JSON envelope (random salt + Fernet ciphertext) safe to
commit to version control. The password derives a Fernet key via scrypt. This
module is pure crypto/resolution logic with no CLI or prompt coupling.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Callable

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

VERSION = 1
_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16

# Decrypted-payload key -> resolver output (provider credential) key.
_PAYLOAD_TO_FLAG: dict[str, str] = {
    "aqs_email": "email",
    "aqs_api_key": "api_key",
    "purpleair_api_key": "purpleair_key",
}


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt(payload: dict, password: str) -> bytes:
    """Encrypt a payload dict into a committable JSON envelope (bytes)."""
    if not password or not password.strip():
        raise ValueError("password must not be empty")
    salt = os.urandom(_SALT_BYTES)
    token = Fernet(_derive_key(password, salt)).encrypt(
        json.dumps(payload).encode("utf-8")
    )
    envelope = {
        "version": VERSION,
        "kdf": "scrypt",
        "salt": base64.b64encode(salt).decode("ascii"),
        "ciphertext": base64.b64encode(token).decode("ascii"),
    }
    return json.dumps(envelope, indent=2).encode("utf-8")


def decrypt(blob: bytes, password: str) -> dict:
    """Decrypt a JSON envelope back to the payload dict.

    Raises cryptography.fernet.InvalidToken on wrong password or tampering.
    """
    envelope = json.loads(blob)
    salt = base64.b64decode(envelope["salt"])
    token = base64.b64decode(envelope["ciphertext"])
    plaintext = Fernet(_derive_key(password, salt)).decrypt(token)
    return json.loads(plaintext)


def load_file(path: str | Path, password: str) -> dict:
    """Read and decrypt a credentials file."""
    return decrypt(Path(path).read_bytes(), password)


def save_file(path: str | Path, payload: dict, password: str) -> None:
    """Encrypt and write a credentials file (parent dirs created)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt(payload, password))


def resolve(
    flags: dict[str, str | None],
    path: str | Path,
    get_password: Callable[[], str],
) -> dict[str, str | None]:
    """Resolve credentials per-key: flag value first, else the file.

    The file is decrypted (and `get_password` called) only if at least one flag
    value is missing AND the file exists.
    """
    resolved = dict(flags)
    if all(resolved.get(k) is not None for k in _PAYLOAD_TO_FLAG.values()):
        return resolved
    path = Path(path)
    if not path.exists():
        return resolved
    payload = load_file(path, get_password())
    for payload_key, flag_key in _PAYLOAD_TO_FLAG.items():
        if resolved.get(flag_key) is None and payload.get(payload_key):
            resolved[flag_key] = payload[payload_key]
    return resolved
```

- [ ] **Step 4: Run tests, confirm PASS.**

Run: `uv run pytest tests/test_credentials.py -v` then `uv run pytest -q`. All pass.

- [ ] **Step 5: Stage (do NOT commit)**

```bash
git add src/smoke_sense/credentials.py tests/test_credentials.py
```

---

### Task 2: `credentials edit` subcommand (`bin/credentials.py`)

**Goal:** Add `smoke-sense credentials edit` that decrypts into `$EDITOR` and re-encrypts on save, plus a shared `resolve_password` helper.

**Files:**
- Create: `src/smoke_sense/bin/credentials.py`
- Modify: `src/smoke_sense/bin/__init__.py`
- Create: `tests/test_credentials_cli.py`

**Acceptance Criteria:**
- [ ] `credentials edit` on a new path creates an encrypted file from the editor's content
- [ ] invalid JSON from the editor leaves the existing file unchanged and exits non-zero
- [ ] the plaintext temp file is removed after the command

**Verify:** `uv run pytest tests/test_credentials_cli.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `tests/test_credentials_cli.py` with EXACTLY this content**

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from smoke_sense import credentials
from smoke_sense.bin import app

runner = CliRunner()


def test_edit_creates_and_encrypts(tmp_path, monkeypatch):
    monkeypatch.setenv("SMOKESENSE_CREDENTIAL_KEY", "pw")
    cred_path = tmp_path / "credentials.json"
    captured = {}

    def fake_run(argv, check):
        captured["tmp"] = argv[1]
        Path(argv[1]).write_text(
            json.dumps(
                {"aqs_email": "x@y.com", "aqs_api_key": "K", "purpleair_api_key": "P"}
            )
        )

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr("smoke_sense.bin.credentials.subprocess.run", fake_run)
    result = runner.invoke(
        app, ["credentials", "edit", "--credentials", str(cred_path)]
    )
    assert result.exit_code == 0, result.output
    assert cred_path.exists()
    assert credentials.load_file(cred_path, "pw")["aqs_api_key"] == "K"
    # temp file shredded
    assert not Path(captured["tmp"]).exists()


def test_edit_invalid_json_leaves_file_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("SMOKESENSE_CREDENTIAL_KEY", "pw")
    cred_path = tmp_path / "credentials.json"
    credentials.save_file(cred_path, {"aqs_api_key": "ORIG"}, "pw")

    def fake_run(argv, check):
        Path(argv[1]).write_text("{not valid json")

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr("smoke_sense.bin.credentials.subprocess.run", fake_run)
    result = runner.invoke(
        app, ["credentials", "edit", "--credentials", str(cred_path)]
    )
    assert result.exit_code != 0
    assert credentials.load_file(cred_path, "pw")["aqs_api_key"] == "ORIG"
```

- [ ] **Step 2: Run tests, confirm they FAIL.**

- [ ] **Step 3: Create `src/smoke_sense/bin/credentials.py` with EXACTLY this content**

```python
"""`smoke-sense credentials edit` — manage the encrypted credentials.json."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import typer
from cryptography.fernet import InvalidToken

from .. import credentials as core

app = typer.Typer(help="Manage encrypted API credentials.")

_TEMPLATE = {
    "aqs_email": "",
    "aqs_api_key": "",
    "purpleair_api_key": "",
}


def resolve_password() -> str:
    """Password from SMOKESENSE_CREDENTIAL_KEY, else an interactive prompt."""
    password = os.environ.get("SMOKESENSE_CREDENTIAL_KEY")
    if password:
        return password
    return typer.prompt("Credential password", hide_input=True)


def _editor() -> str:
    return os.environ.get("EDITOR") or "vi"


@app.command()
def edit(
    credentials: Path = typer.Option(
        Path("./credentials.json"), help="Path to credentials.json"
    ),
) -> None:
    """Decrypt credentials.json into $EDITOR and re-encrypt on save."""
    password = resolve_password()
    if not password.strip():
        raise typer.BadParameter("password must not be empty")

    if credentials.exists():
        try:
            payload = core.load_file(credentials, password)
        except (InvalidToken, json.JSONDecodeError, KeyError) as exc:
            raise typer.BadParameter(
                f"could not decrypt {credentials} — wrong password?"
            ) from exc
    else:
        payload = dict(_TEMPLATE)

    fd, tmp_name = tempfile.mkstemp(suffix=".json", text=True)
    tmp_path = Path(tmp_name)
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
        subprocess.run([_editor(), str(tmp_path)], check=True)
        try:
            new_payload = json.loads(tmp_path.read_text())
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(
                "edited content is not valid JSON; credentials.json left unchanged"
            ) from exc
        core.save_file(credentials, new_payload, password)
        typer.echo(f"Saved encrypted credentials to {credentials}")
    finally:
        tmp_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Update `src/smoke_sense/bin/__init__.py` to EXACTLY this content**

```python
from typer import Typer

from . import credentials, fetch, forecast, visualize

app = Typer()

app.command()(fetch.fetch)
app.command()(forecast.forecast)
app.command()(visualize.visualize)
app.add_typer(credentials.app, name="credentials")
```

- [ ] **Step 5: Run tests, confirm PASS.**

Run: `uv run pytest tests/test_credentials_cli.py -v`, then `uv run pytest -q`, then `uv run smoke-sense credentials edit --help` (shows `--credentials`). All pass.

- [ ] **Step 6: Stage (do NOT commit)**

```bash
git add src/smoke_sense/bin/credentials.py src/smoke_sense/bin/__init__.py tests/test_credentials_cli.py
```

---

### Task 3: `fetch` integration — default `--end`, `--credentials`, resolution

**Goal:** Make `--end` optional (default today), add `--credentials`, and resolve credentials via the core with lazy decryption.

**Files:**
- Modify: `src/smoke_sense/bin/fetch.py`
- Modify: `tests/test_fetch_cli.py`

**Acceptance Criteria:**
- [ ] omitting `--end` writes a file dated with `date.today()`
- [ ] credentials are resolved via `credentials.resolve` with `resolve_password`; no prompt when the file is absent
- [ ] existing fetch tests still pass

**Verify:** `uv run pytest tests/test_fetch_cli.py -v` → all pass; then `uv run pytest -q`

**Steps:**

- [ ] **Step 1: Add the `--end` default test and adjust the existing parquet test in `tests/test_fetch_cli.py`**

Add this import near the top (with the existing `from datetime import date` if not present):
```python
from datetime import date
```

Append this test:
```python
def test_end_defaults_to_today(tmp_path, monkeypatch):
    from smoke_sense.bin import fetch as fetch_mod

    class FakeProvider:
        def fetch(self, county_fips, start, end, pollutants):
            return _fake_frame(county_fips)

    monkeypatch.setattr(
        fetch_mod, "_resolve_providers", lambda sources, creds: [FakeProvider()]
    )
    result = runner.invoke(
        app,
        ["fetch", "06037", "--start", "2023-07-01",
         "--credentials", str(tmp_path / "absent.json"),
         "--output", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    assert (tmp_path / f"06037_2023-07-01_{today}.parquet").exists()
```

Then modify the existing `test_fetch_writes_parquet` invocation to pass an absent credentials path (so it never tries to decrypt a stray `./credentials.json`). Change its `runner.invoke(...)` args list to include `"--credentials", str(tmp_path / "absent.json")`:
```python
    result = runner.invoke(
        app,
        ["fetch", "06037", "--start", "2023-07-01", "--end", "2023-07-02",
         "--source", "aqs", "--credentials", str(tmp_path / "absent.json"),
         "--output", str(tmp_path)],
    )
```

- [ ] **Step 2: Run the new test, confirm it FAILS** (because `--end` is currently required / `--credentials` unknown).

Run: `uv run pytest tests/test_fetch_cli.py::test_end_defaults_to_today -v`

- [ ] **Step 3: Replace `src/smoke_sense/bin/fetch.py` with EXACTLY this content**

```python
"""`smoke-sense fetch` — download AQI series for counties into the common format."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
import typer
from rich.console import Console

from .. import credentials as credentials_core
from .. import data
from ..data import Pollutant
from ..providers import all_providers, get_provider
from .credentials import resolve_password

console = Console()

DEFAULT_POLLUTANTS = [Pollutant.PM2_5, Pollutant.PM10, Pollutant.O3]


def _resolve_providers(sources: list[str], creds: dict):
    """Construct provider instances for the requested source names."""
    providers = []
    for name in sources:
        providers.append(get_provider(name, **creds))
    return providers


def fetch(
    county_fips: List[str] = typer.Argument(..., help="One or more 5-digit county FIPS codes"),
    start: datetime = typer.Option(..., formats=["%Y-%m-%d"], help="Start date (inclusive)"),
    end: Optional[datetime] = typer.Option(
        None, formats=["%Y-%m-%d"], help="End date (inclusive); defaults to today"
    ),
    source: Optional[List[str]] = typer.Option(None, help="Provider(s); default: all"),
    pollutant: Optional[List[str]] = typer.Option(None, help="Pollutant(s); default: PM2.5,PM10,O3"),
    output: Path = typer.Option(Path("./data"), help="Output directory or .parquet path"),
    credentials: Path = typer.Option(
        Path("./credentials.json"), "--credentials", help="Encrypted credentials file"
    ),
    email: Optional[str] = typer.Option(None, envvar="AQS_EMAIL"),
    api_key: Optional[str] = typer.Option(None, envvar="AQS_API_KEY"),
    purpleair_key: Optional[str] = typer.Option(None, envvar="PURPLEAIR_API_KEY"),
) -> None:
    """Fetch AQI data for the given counties and time range into Parquet."""
    for fips in county_fips:
        if not (len(fips) == 5 and fips.isdigit()):
            raise typer.BadParameter(f"county FIPS must be 5-digit, got {fips!r}")

    start_date = start.date()
    end_date = end.date() if end else date.today()

    sources = source or all_providers()
    pollutants = (
        [Pollutant.from_str(p) for p in pollutant] if pollutant else DEFAULT_POLLUTANTS
    )
    creds = credentials_core.resolve(
        {"email": email, "api_key": api_key, "purpleair_key": purpleair_key},
        credentials,
        get_password=resolve_password,
    )
    providers = _resolve_providers(sources, creds)

    for fips in county_fips:
        frames = []
        for provider in providers:
            console.print(f"[cyan]Fetching[/] {fips} from {provider.__class__.__name__}…")
            frames.append(provider.fetch(fips, start_date, end_date, pollutants))
        combined = (
            pd.concat(frames, ignore_index=True) if frames else data.empty_frame()
        )
        combined = data.validate(combined)

        if output.suffix == ".parquet" and len(county_fips) == 1:
            out_path = output
        else:
            name = f"{fips}_{start_date:%Y-%m-%d}_{end_date:%Y-%m-%d}.parquet"
            out_path = output / name
        data.write_parquet(combined, out_path)
        console.print(f"[green]Wrote[/] {len(combined)} rows → {out_path}")
```

- [ ] **Step 4: Run tests, confirm PASS.**

Run: `uv run pytest tests/test_fetch_cli.py -v`, then `uv run pytest -q`, then `uv run smoke-sense fetch --help` (shows `--credentials`; `--end` optional). All pass.

- [ ] **Step 5: Stage (do NOT commit)**

```bash
git add src/smoke_sense/bin/fetch.py tests/test_fetch_cli.py
```

---

## Self-Review

**Spec coverage:**
- Encrypted committable file (Fernet + scrypt, salt+ciphertext envelope) → Task 1 ✓
- `credentials edit` (decrypt → `$EDITOR` → re-encrypt, 0600 temp, shred) → Task 2 ✓
- Per-credential resolution flag/env → file, lazy decryption → Task 1 (`resolve`) + Task 3 (wiring) ✓
- Password from `SMOKESENSE_CREDENTIAL_KEY` else prompt → Task 2 (`resolve_password`), reused by `fetch` ✓
- `--end` defaults to today → Task 3 ✓
- `--credentials` path option (default `./credentials.json`) on both commands → Tasks 2 & 3 ✓
- Error handling: wrong password → clear message; invalid JSON → file untouched, non-zero exit → Task 2 ✓
- `cryptography` dependency → Task 0 ✓
- Tests for round-trip, wrong password, precedence, no-prompt-when-present, edit round-trip, invalid-JSON, `--end` default → Tasks 1–3 ✓

**Placeholder scan:** none — every step has full code/commands.

**Type/name consistency:** `resolve(flags, path, get_password)` and `resolve_password` used consistently across Tasks 1–3; payload keys `aqs_email`/`aqs_api_key`/`purpleair_api_key` map to `email`/`api_key`/`purpleair_key` in `_PAYLOAD_TO_FLAG`; the `fetch` option is named `credentials` (Path) with explicit `--credentials`, distinct from the imported `credentials_core`.

**Note on env fallback:** environment variables (`AQS_EMAIL` etc.) are handled by Typer's `envvar=`, so values reaching `resolve` already encode "flag or env"; `resolve` only adds the file layer.
