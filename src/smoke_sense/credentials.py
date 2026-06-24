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
# scrypt cost. The encrypted file is meant to be committed to version control,
# so security rests on resisting an offline brute-force of the committed
# ciphertext. n=2**17 (~128 MiB) is a stronger-than-interactive work factor
# chosen for that threat model; still sub-second on modern hardware.
_SCRYPT_N = 2 ** 17
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
