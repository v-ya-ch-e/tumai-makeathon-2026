"""Fernet helpers for encrypting wg-gesucht credentials at rest."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_DIR = Path.home() / ".wg_hunter"
_KEY_FILE = _DIR / "secret.key"


def ensure_key() -> bytes:
    env_raw = os.environ.get("WG_SECRET_KEY")
    if env_raw is not None and env_raw.strip():
        key = env_raw.strip().encode("ascii")
        try:
            Fernet(key)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "WG_SECRET_KEY must be a url-safe base64-encoded 32-byte Fernet key "
                "(e.g. output of Fernet.generate_key().decode())."
            ) from exc
        return key

    if _KEY_FILE.is_file():
        return _KEY_FILE.read_bytes().strip()

    _DIR.mkdir(mode=0o700, exist_ok=True)
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    _KEY_FILE.chmod(0o600)
    logger.info(
        "Generated new Fernet key at %s — back this up!",
        _KEY_FILE.expanduser().as_posix(),
    )
    return key


def _fernet() -> Fernet:
    return Fernet(ensure_key())


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    try:
        return _fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Could not decrypt payload (wrong key or corrupt data).") from exc
