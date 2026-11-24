"""GigaChat (Sber) adapter: OAuth token exchange, then an OpenAI-like chat API.

Provider `extra`:
- `oauth_url`: token endpoint
- `scope`: e.g. GIGACHAT_API_PERS (personal), GIGACHAT_API_B2B, GIGACHAT_API_CORP
- `ca_bundle_path`: path to the Russian Ministry of Digital Development root CA (PEM).
Chat host since 2026-07-17: https://api.giga.chat/v1 (the old gigachat.devices.sberbank.ru
host still works for existing users). `expires_at` is documented in ms; seconds handled too.
Blocked topics come back as finish_reason "blacklist" (a result, not an error).
  TLS verification is never disabled; without the CA the connection fails with a hint.
The stored secret is the "authorization key" (Base64 of client_id:client_secret).
"""

import hashlib
import time
import uuid
from pathlib import Path
from typing import ClassVar

import httpx

from app.providers.base import ProviderAuthError, ProviderError, raise_for_status, response_json
from app.providers.openai_compat import OpenAICompatibleProvider

CA_HINT = (
    "GigaChat uses certificates issued by the Russian Ministry of Digital Development "
    "(Минцифры) “Russian Trusted Root CA”. Download it from https://www.gosuslugi.ru/crt "
    "(on Linux also the sub CA), combine them into one PEM file, and set its path as "
    "`ca_bundle_path` in the provider's advanced settings."
)
TOKEN_MARGIN_S = 60


class GigaChatProvider(OpenAICompatibleProvider):
    name = "gigachat"
    # (key hash, scope, oauth_url) -> (token, expires_at_epoch_s). Process-local cache.
    _tokens: ClassVar[dict[tuple[str, str, str], tuple[str, float]]] = {}

    def _verify(self) -> bool | str:
        path = (self.extra.get("ca_bundle_path") or "").strip()
        if path:
            if not Path(path).expanduser().is_file():
                raise ProviderError(f"gigachat: CA bundle not found at {path}. {CA_HINT}")
            return str(Path(path).expanduser())
        return True

    def _connect_error(self, e: httpx.ConnectError) -> ProviderError:
        err = super()._connect_error(e)
        if "TLS certificate verification failed" in str(err):
            return ProviderError(f"{err} {CA_HINT}")
        return err

    def _cache_key(self) -> tuple[str, str, str]:
        digest = hashlib.sha256(self.secret.reveal().encode()).hexdigest()
        return digest, self.extra.get("scope", "GIGACHAT_API_PERS"), self.extra.get("oauth_url", "")

    async def _access_token(self) -> str:
        if not self.secret:
            raise ProviderAuthError("gigachat: no authorization key configured")
        oauth_url = self.extra.get("oauth_url")
        if not oauth_url:
            raise ProviderError("gigachat: no OAuth URL configured")
        key = self._cache_key()
        cached = self._tokens.get(key)
        if cached and cached[1] - TOKEN_MARGIN_S > time.time():
            return cached[0]
        headers = {
            "Authorization": f"Basic {self.secret.reveal()}",
            "RqUID": str(uuid.uuid4()),
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        response, _ = await self._send("POST", oauth_url, headers=headers, data={"scope": key[1]})
        raise_for_status(response, "gigachat (oauth)", self.secret)
        data = response_json(response)
        token = data.get("access_token")
        if not token:
            raise ProviderAuthError("gigachat: OAuth response has no access_token")
        expires_at = data.get("expires_at")
        if isinstance(expires_at, (int, float)):
            expires = expires_at / 1000 if expires_at > 1e11 else float(expires_at)  # ms or s
        else:
            expires = time.time() + 30 * 60
        self._tokens[key] = (token, expires)
        return token

    async def _request_headers(self) -> dict[str, str]:
        token = await self._access_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}
