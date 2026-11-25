"""Provider adapter interface (docs/02-architecture.md, "Provider adapter interface")."""

import copy
import json
import time
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field


class Secret:
    """An API token plus extra secret fields (e.g. Yandex folder ID).

    The value is only reachable via `reveal()`; repr/str never show it, so it can't
    end up in logs, tracebacks or templates by accident."""

    __slots__ = ("_value", "_extra")

    def __init__(self, value: str | None, extra: dict[str, str] | None = None):
        self._value = value or ""
        self._extra = dict(extra or {})

    def reveal(self) -> str:
        return self._value

    def extra(self, name: str) -> str:
        return self._extra.get(name, "")

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret(****)" if self._value else "Secret(<empty>)"

    __str__ = __repr__


NO_SECRET = Secret(None)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model_id: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    response_format_json: bool = False  # used by the judge


class ChatResult(BaseModel):
    text: str
    finish_reason: str | None = None  # "stop", "length", "content_filter", "refusal", ...
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0
    raw_request: dict[str, Any] = Field(default_factory=dict)  # body only, never headers
    raw_response: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)  # e.g. "temperature not sent"


# ── errors ───────────────────────────────────────────────────────────────────


class ProviderError(Exception):
    """Non-retryable provider failure. Messages must never contain secrets."""

    retryable = False

    def __init__(self, message: str, status_code: int | None = None, raw: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.raw = raw or {}


class ProviderAuthError(ProviderError):
    pass


class ProviderRateLimit(ProviderError):
    retryable = True

    def __init__(self, message: str, retry_after: float | None = None, **kw: Any):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class ProviderTransient(ProviderError):
    """5xx, timeouts, connection problems."""

    retryable = True


class ProviderRefused(ProviderError):
    """The API blocked the content (moderation). This is a *result* for the study,
    not an operational error: the run engine stores it as a refusal."""


# ── shared helpers for HTTP adapters ─────────────────────────────────────────

_REFUSAL_HINTS = (
    "content_filter", "content filter", "content_policy", "content policy", "moderation",
    "safety", "sensitive", "risk", "inappropriate", "blocked", "violat",
)  # fmt: skip


# Narrow on purpose: a 403 is normally an auth/permission error.
# OpenRouter: moderation flags ("…requires moderation… flagged…", `content_policy_violation`).
_BLOCK_403_HINTS = (
    "unsafe", "content_filter", "sensitive content", "moderation", "flagged", "content_policy_violation",
)  # fmt: skip

# finish_reason values that mean the provider blocked or refused the content (a result,
# not an error). Used by the refusal heuristic: OpenAI-style, Anthropic, GigaChat.
REFUSAL_FINISH_REASONS = {"content_filter", "refusal", "blacklist"}


def scrub(text: str, secret: Secret | None = None) -> str:
    """Remove the secret itself (if an API echoes it back) and anything key-like."""
    from app.log import redact

    if secret:
        for value in (secret.reveal(), *secret._extra.values()):
            if value and len(value) >= 4:
                text = text.replace(value, "****")
    return redact(text)


def body_snippet(response: httpx.Response, limit: int = 300, secret: Secret | None = None) -> str:
    text = scrub(response.text.strip().replace("\n", " "), secret)
    return text[:limit] + ("…" if len(text) > limit else "")


def response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        return {"_non_json_body": response.text[:2000]}
    return data if isinstance(data, dict) else {"_body": data}


def raise_for_status(response: httpx.Response, provider: str, secret: Secret | None = None) -> None:
    """Map HTTP errors to the provider error classes."""
    code = response.status_code
    if code < 400:
        return
    # Scrub the stored copy too: some APIs echo the key back in error bodies.
    scrubbed = scrub(json.dumps(response_json(response), ensure_ascii=False), secret)
    try:
        raw = json.loads(scrubbed)
    except json.JSONDecodeError:
        raw = {"_scrubbed_body": scrubbed[:2000]}
    detail = body_snippet(response, secret=secret)
    lowered = detail.lower()
    # Some APIs block content with 403 (e.g. Baidu Qianfan `unsafe_request`), so check that
    # before treating 403 as an auth problem.
    if code == 403 and any(h in lowered for h in _BLOCK_403_HINTS):
        raise ProviderRefused(f"{provider}: content blocked ({code}). {detail}", code, raw)
    if code in (401, 403):
        raise ProviderAuthError(f"{provider}: {code} {response.reason_phrase} – check the API key. {detail}", code, raw)
    if code == 429:
        retry_after = None
        header = response.headers.get("retry-after")
        if header:
            try:
                retry_after = float(header)
            except ValueError:
                retry_after = None
        raise ProviderRateLimit(f"{provider}: 429 rate limited. {detail}", retry_after=retry_after, status_code=code, raw=raw)
    if code >= 500 or code == 408:
        raise ProviderTransient(f"{provider}: {code} {response.reason_phrase}. {detail}", code, raw)
    if code in (400, 422, 451) and any(h in lowered for h in _REFUSAL_HINTS):
        raise ProviderRefused(f"{provider}: content blocked ({code}). {detail}", code, raw)
    raise ProviderError(f"{provider}: {code} {response.reason_phrase}. {detail}", code, raw)


def raise_for_body_error(error: Any, provider: str, secret: Secret | None = None) -> None:
    """Some gateways (OpenRouter) report errors inside an HTTP 200 body, as a top-level
    `error` or `choices[0].error`. Map them like the matching HTTP status; unknown codes
    count as transient (the upstream model failed mid-generation)."""
    if not error:
        return
    err = error if isinstance(error, dict) else {"message": str(error)}
    try:
        code = int(err.get("code"))
    except (TypeError, ValueError):
        code = 502
    if not 400 <= code <= 599:
        code = 502
    raise_for_status(httpx.Response(code, json={"error": err}), provider, secret)


class HttpAdapter:
    """Base for httpx-based adapters: timing, error mapping, and safe raw-request copies."""

    name = "provider"

    def __init__(
        self,
        base_url: str,
        secret: Secret = NO_SECRET,
        extra: dict[str, Any] | None = None,
        timeout: float = 120,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.secret = secret
        self.extra = dict(extra or {})
        self.timeout = timeout

    def _verify(self) -> bool | str:
        return True

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, verify=self._verify())

    async def _send(
        self, method: str, url: str, *, headers: dict[str, str], json_body: dict | None = None,
        data: dict | None = None,
    ) -> tuple[httpx.Response, int]:
        start = time.perf_counter()
        try:
            async with self._client() as client:
                response = await client.request(method, url, headers=headers, json=json_body, data=data)
        except httpx.TimeoutException as e:
            raise ProviderTransient(f"{self.name}: request timed out after {self.timeout:.0f}s") from e
        except httpx.ConnectError as e:
            raise self._connect_error(e) from e
        except httpx.TransportError as e:
            raise ProviderTransient(f"{self.name}: network error ({type(e).__name__})") from e
        latency_ms = int((time.perf_counter() - start) * 1000)
        return response, latency_ms

    def _connect_error(self, e: httpx.ConnectError) -> ProviderError:
        msg = str(e)
        if "CERTIFICATE_VERIFY_FAILED" in msg or "certificate verify failed" in msg:
            return ProviderError(
                f"{self.name}: TLS certificate verification failed for {self.base_url}. "
                "The server's certificate is not trusted by this computer."
            )
        return ProviderTransient(f"{self.name}: can't connect to {self.base_url} ({type(e).__name__})")

    @staticmethod
    def snapshot(body: dict[str, Any]) -> dict[str, Any]:
        """Deep copy of the request *body* for storage (no headers, no secrets)."""
        return copy.deepcopy(body)


class ChatProvider(Protocol):
    def __init__(self, base_url: str, secret: Secret, extra: dict, timeout: float = 120): ...
    async def chat(self, req: ChatRequest) -> ChatResult: ...
    async def test_connection(self, model_id: str | None = None) -> tuple[bool, str]: ...
    async def list_models(self) -> list[str] | None: ...
