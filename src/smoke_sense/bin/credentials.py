"""`smoke-sense credentials edit` — manage the encrypted credentials.json."""

from __future__ import annotations

import binascii
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
        except (InvalidToken, json.JSONDecodeError, KeyError, binascii.Error) as exc:
            raise typer.BadParameter(
                f"could not decrypt {credentials} — wrong password?"
            ) from exc
    else:
        payload = dict(_TEMPLATE)

    # Keep the decrypted plaintext beside the encrypted file rather than in the
    # shared world-traversable system temp dir.
    fd, tmp_name = tempfile.mkstemp(
        suffix=".json", dir=credentials.parent or None, text=True
    )
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
