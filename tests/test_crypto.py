import os
import stat

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.crypto import Cipher, CryptoKeyError, DecryptionError, hint_for, load_key, mask


def test_key_file_created_0600_and_reused(settings):
    key = load_key(settings)
    path = settings.data_dir / "secret.key"
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert load_key(settings) == key  # never regenerated


def test_env_key_wins_and_no_file_written(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("HISTBIAS_SECRET_KEY", key)
    s = Settings()
    assert load_key(s) == key.encode()
    assert not (s.data_dir / "secret.key").exists()


def test_invalid_env_key(monkeypatch):
    monkeypatch.setenv("HISTBIAS_SECRET_KEY", "not-a-fernet-key")
    with pytest.raises(CryptoKeyError, match="not a valid Fernet key"):
        load_key(Settings())


def test_roundtrip_and_wrong_key():
    a, b = Cipher(Fernet.generate_key()), Cipher(Fernet.generate_key())
    token = a.encrypt("sk-secret-ąę")
    assert b"sk-secret" not in token
    assert a.decrypt(token) == "sk-secret-ąę"
    assert a.decrypt_json(a.encrypt_json({"folder_id": "b1g"})) == {"folder_id": "b1g"}
    with pytest.raises(DecryptionError, match="re-enter"):
        b.decrypt(token)
    assert "sk-" not in repr(a)


def test_hint_and_mask():
    assert hint_for("sk-ant-0123456789abcd") == "abcd"
    assert hint_for("short") == ""  # don't reveal most of a short secret
    assert mask("abcd") == "••••abcd"
    assert mask("") == "••••"
