import sqlite3

import pytest
from cryptography.fernet import Fernet
from sqlmodel import select

from app.crypto import Cipher, DecryptionError
from app.models import Credential, Provider
from app.services import credentials as creds

KEY = "sk-live-SECRET-0123456789abcdef"


@pytest.fixture
def cipher():
    return Cipher(Fernet.generate_key())


@pytest.fixture
def provider(session):
    p = Provider(name="DeepSeek", kind="openai_compatible", base_url="https://api.deepseek.com")
    session.add(p)
    session.commit()
    return p


def test_env_var_names():
    assert creds.env_var_name("Anthropic") == "HISTBIAS_KEY_ANTHROPIC"
    assert creds.env_var_name("Yandex AI Studio") == "HISTBIAS_KEY_YANDEX_AI_STUDIO"
    assert creds.env_var_name("Yandex AI Studio", "folder_id") == "HISTBIAS_KEY_YANDEX_AI_STUDIO__FOLDER_ID"


def test_save_resolve_and_status(session, cipher, provider, migrated):
    assert creds.key_status(session, cipher, provider).source == "none"
    creds.save_credential(session, cipher, provider, f"  {KEY}  ")
    status = creds.key_status(session, cipher, provider)
    assert (status.source, status.display, status.usable) == ("stored", "••••cdef", True)
    assert creds.resolve_secret(session, cipher, provider).reveal() == KEY
    # Stored encrypted: not in any column and not in the DB file.
    cred = session.exec(select(Credential)).one()
    assert KEY.encode() not in cred.secret_enc and cred.hint == "cdef"
    session.close()
    con = sqlite3.connect(migrated.db_path)
    con.execute("PRAGMA wal_checkpoint(FULL)")
    con.close()
    assert KEY.encode() not in migrated.db_path.read_bytes()


def test_extra_secret_fields(session, cipher):
    p = Provider(name="Yandex AI Studio", kind="openai_compatible", base_url="https://x.example/v1",
                 extra={"secret_extra_fields": ["folder_id"]})
    session.add(p)
    session.commit()
    with pytest.raises(creds.CredentialError, match="folder_id"):
        creds.save_credential(session, cipher, p, KEY, {})
    creds.save_credential(session, cipher, p, KEY, {"folder_id": "b1gFOLDER", "other": "ignored"})
    secret = creds.resolve_secret(session, cipher, p)
    assert secret.extra("folder_id") == "b1gFOLDER" and secret.extra("other") == ""


def test_environment_override(session, cipher, provider, monkeypatch):
    creds.save_credential(session, cipher, provider, KEY)
    monkeypatch.setenv("HISTBIAS_KEY_DEEPSEEK", "env-key-9999999999999")
    status = creds.key_status(session, cipher, provider)
    assert (status.source, status.display) == ("environment", "from environment")
    assert creds.resolve_secret(session, cipher, provider).reveal() == "env-key-9999999999999"


def test_lost_key_marks_broken_without_crashing(session, cipher, provider):
    creds.save_credential(session, cipher, provider, KEY)
    other = Cipher(Fernet.generate_key())
    status = creds.key_status(session, other, provider)
    assert status.broken and not status.usable
    with pytest.raises(DecryptionError):
        creds.resolve_secret(session, other, provider)
    assert creds.key_status(session, None, provider).broken


def test_keyless_providers(session, cipher):
    mock = Provider(name="Mock", kind="mock")
    ollama = Provider(name="Ollama", kind="openai_compatible", base_url="http://localhost:11434/v1", extra={"no_auth": True})
    session.add_all([mock, ollama])
    session.commit()
    for p in (mock, ollama):
        assert creds.key_status(session, cipher, p).source == "not_needed"
        assert not creds.resolve_secret(session, cipher, p)
        with pytest.raises(creds.CredentialError, match="doesn't use an API key"):
            creds.save_credential(session, cipher, p, KEY)


@pytest.mark.parametrize("bad", ["", "   ", "a\nb", "x" * 5000])
def test_rejects_bad_keys(session, cipher, provider, bad):
    with pytest.raises(creds.CredentialError):
        creds.save_credential(session, cipher, provider, bad)


def test_delete(session, cipher, provider):
    creds.save_credential(session, cipher, provider, KEY)
    assert creds.delete_credential(session, provider)
    assert creds.key_status(session, cipher, provider).source == "none"
    assert not creds.delete_credential(session, provider)


async def test_test_provider_records_result(session, cipher, provider):
    import httpx
    import respx

    creds.save_credential(session, cipher, provider, KEY)
    with respx.mock:
        respx.get("https://api.deepseek.com/models").mock(return_value=httpx.Response(401, json={"error": "bad"}))
        ok, msg = await creds.test_provider(session, cipher, provider)
    assert not ok and "401" in msg
    cred = creds.get_credential(session, provider)
    assert cred.last_test_ok is False and cred.last_test_at is not None
    assert KEY not in (cred.last_test_message or "")
