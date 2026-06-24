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
