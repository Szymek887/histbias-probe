"""API-token encryption at rest (docs/07-security.md).

Key source, in order: `HISTBIAS_SECRET_KEY`, else `data/secret.key` (created with mode
0600 on first use). Losing the key means stored tokens can't be decrypted; callers get
`DecryptionError` and the UI asks the user to re-enter keys.
"""

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings, get_settings

KEY_FILENAME = "secret.key"
MASK = "••••"


class CryptoKeyError(RuntimeError):
    """The configured key is malformed or the key file is unreadable."""


class DecryptionError(RuntimeError):
    """Stored data can't be decrypted with the current key (key lost or changed)."""


def key_path(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).data_dir / KEY_FILENAME


def generate_key() -> bytes:
    return Fernet.generate_key()


def _write_key_file(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = generate_key()
    # O_EXCL: never overwrite an existing key (that would make stored tokens unreadable).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key + b"\n")
    return key


def load_key(settings: Settings | None = None, create: bool = True) -> bytes:
    settings = settings or get_settings()
    if settings.secret_key is not None:
        key = settings.secret_key.get_secret_value().strip().encode()
        source = "HISTBIAS_SECRET_KEY"
    else:
        path = key_path(settings)
        if not path.exists():
            if not create:
                raise CryptoKeyError(f"no encryption key at {path}")
            try:
                return _write_key_file(path)
            except FileExistsError:
                pass  # created concurrently; read it below
        key = path.read_bytes().strip()
        source = str(path)
    try:
        Fernet(key)
    except (ValueError, TypeError) as e:
        raise CryptoKeyError(
            f"the encryption key from {source} is not a valid Fernet key "
            "(32 url-safe base64-encoded bytes)"
        ) from e
    return key


class Cipher:
    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "Cipher":
        return cls(load_key(settings))

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as e:
            raise DecryptionError(
                "stored secret can't be decrypted with the current key – re-enter the API key"
            ) from e

    def encrypt_json(self, data: dict[str, str]) -> bytes:
        return self.encrypt(json.dumps(data, sort_keys=True))

    def decrypt_json(self, token: bytes) -> dict[str, str]:
        return json.loads(self.decrypt(token))

    def __repr__(self) -> str:
        return "Cipher(****)"


def hint_for(secret: str) -> str:
    """Last 4 characters for the masked display. Short secrets get no hint at all."""
    secret = secret.strip()
    return secret[-4:] if len(secret) >= 12 else ""


def mask(hint: str | None) -> str:
    return f"{MASK}{hint}" if hint else MASK
