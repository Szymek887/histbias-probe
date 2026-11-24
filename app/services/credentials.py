"""API keys: encrypted storage, environment override, status, and connection tests.

The plain token only ever exists in memory: it is encrypted on save, decrypted right
before a provider call, and never returned to templates or endpoints (docs/07-security.md).
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session, select

from app.crypto import Cipher, DecryptionError, hint_for, mask
from app.models import Credential, Provider, ProviderKind
from app.models.base import utcnow
from app.providers.base import NO_SECRET, Secret
from app.providers.registry import make_adapter

ENV_PREFIX = "HISTBIAS_KEY_"


class CredentialError(ValueError):
    """User-facing validation error."""


def env_var_name(provider_name: str, field_name: str | None = None) -> str:
    base = ENV_PREFIX + re.sub(r"[^A-Z0-9]+", "_", provider_name.upper()).strip("_")
    return f"{base}__{field_name.upper()}" if field_name else base


def secret_extra_fields(provider: Provider) -> list[str]:
    return [str(f) for f in (provider.extra or {}).get("secret_extra_fields", []) or []]


def needs_key(provider: Provider) -> bool:
    return provider.kind != ProviderKind.MOCK and not (provider.extra or {}).get("no_auth")


def _env_secret(provider: Provider) -> Secret | None:
    value = os.environ.get(env_var_name(provider.name))
    if not value:
        return None
    extras = {
        f: os.environ.get(env_var_name(provider.name, f), "") for f in secret_extra_fields(provider)
    }
    return Secret(value.strip(), extras)


def get_credential(session: Session, provider: Provider) -> Credential | None:
    return session.exec(select(Credential).where(Credential.provider_id == provider.id)).first()


@dataclass
class KeyStatus:
    provider: Provider
    source: str  # "stored" | "environment" | "none" | "not_needed"
    display: str  # masked key / label; never the key itself
    broken: bool = False  # stored but can't be decrypted with the current key
    missing_extras: list[str] = field(default_factory=list)
    last_test_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_message: str | None = None

    @property
    def usable(self) -> bool:
        return self.source in ("stored", "environment", "not_needed") and not self.broken

    @property
    def env_var(self) -> str:
        return env_var_name(self.provider.name)


def key_status(session: Session, cipher: Cipher | None, provider: Provider) -> KeyStatus:
    cred = get_credential(session, provider)
    tests = {}
    if cred is not None:
        tests = dict(
            last_test_at=cred.last_test_at,
            last_test_ok=cred.last_test_ok,
            last_test_message=cred.last_test_message,
        )
    if not needs_key(provider):
        return KeyStatus(provider, "not_needed", "not needed", **tests)
    env = _env_secret(provider)
    if env is not None:
        missing = [f for f in secret_extra_fields(provider) if not env.extra(f)]
        return KeyStatus(provider, "environment", "from environment", missing_extras=missing, **tests)
    if cred is None:
        return KeyStatus(provider, "none", "—")
    broken = False
    missing: list[str] = []
    if cipher is None:
        broken = True
    else:
        try:
            cipher.decrypt(cred.secret_enc)
            extras = cipher.decrypt_json(cred.extra_enc) if cred.extra_enc else {}
            missing = [f for f in secret_extra_fields(provider) if not extras.get(f)]
        except DecryptionError:
            broken = True
    return KeyStatus(provider, "stored", mask(cred.hint), broken=broken, missing_extras=missing, **tests)


def list_key_statuses(session: Session, cipher: Cipher | None) -> list[KeyStatus]:
    providers = session.exec(select(Provider).order_by(Provider.name)).all()
    return [key_status(session, cipher, p) for p in providers]


def save_credential(
    session: Session, cipher: Cipher, provider: Provider, token: str, extras: dict[str, str] | None = None
) -> Credential:
    if not needs_key(provider):
        raise CredentialError(
            f"{provider.name} doesn't use an API key (mock, or “no API key needed” is set on the provider)"
        )
    token = (token or "").strip()
    if not token:
        raise CredentialError("enter the API key")
    if len(token) > 4096 or any(c in token for c in "\r\n"):
        raise CredentialError("that doesn't look like an API key (too long or several lines)")
    wanted = secret_extra_fields(provider)
    extras = {k: (v or "").strip() for k, v in (extras or {}).items() if k in wanted}
    missing = [f for f in wanted if not extras.get(f)]
    if missing:
        raise CredentialError(f"also fill in: {', '.join(missing)}")
    cred = get_credential(session, provider) or Credential(provider_id=provider.id, secret_enc=b"")
    cred.secret_enc = cipher.encrypt(token)
    cred.extra_enc = cipher.encrypt_json(extras) if extras else None
    cred.hint = hint_for(token)
    cred.last_test_at = cred.last_test_ok = cred.last_test_message = None
    cred.updated_at = utcnow()
    session.add(cred)
    session.commit()
    return cred


def delete_credential(session: Session, provider: Provider) -> bool:
    cred = get_credential(session, provider)
    if cred is None:
        return False
    session.delete(cred)
    session.commit()
    return True


def resolve_secret(session: Session, cipher: Cipher | None, provider: Provider) -> Secret:
    """The secret to call `provider` with. Environment wins over the stored key."""
    if not needs_key(provider):
        return NO_SECRET
    env = _env_secret(provider)
    if env is not None:
        return env
    cred = get_credential(session, provider)
    if cred is None:
        raise CredentialError(f"no API key for {provider.name}; add one in Settings → API keys")
    if cipher is None:
        raise DecryptionError("the encryption key is not available")
    extras = cipher.decrypt_json(cred.extra_enc) if cred.extra_enc else {}
    return Secret(cipher.decrypt(cred.secret_enc), extras)


def adapter_for(session: Session, cipher: Cipher | None, provider: Provider, timeout: float = 120):
    return make_adapter(
        provider.kind, provider.base_url, resolve_secret(session, cipher, provider), provider.extra, timeout
    )


async def test_provider(
    session: Session, cipher: Cipher | None, provider: Provider, model_id: str | None = None,
    timeout: float = 30,
) -> tuple[bool, str]:
    """Run the adapter's connection test and record the result on the stored credential."""
    try:
        adapter = adapter_for(session, cipher, provider, timeout)
        ok, message = await adapter.test_connection(model_id)
    except (CredentialError, DecryptionError, ValueError) as e:
        ok, message = False, str(e)
    cred = get_credential(session, provider)
    if cred is not None:
        cred.last_test_at = utcnow()
        cred.last_test_ok = ok
        cred.last_test_message = message[:500]
        session.add(cred)
        session.commit()
    return ok, message
