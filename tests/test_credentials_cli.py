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
        captured["mode"] = Path(argv[1]).stat().st_mode & 0o777
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
    # plaintext temp file was 0600 while it existed, and removed afterward
    assert captured["mode"] == 0o600
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
