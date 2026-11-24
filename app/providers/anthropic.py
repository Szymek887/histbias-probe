"""Anthropic Messages API adapter (raw httpx so the exact request/response is recorded; D6).

Notes for current Claude models (checked 2026-11 with the claude-api reference):
- `temperature`/`top_p`/`top_k` are rejected by current models, so `temperature` is only
  sent when the provider's `extra.send_temperature` is true; otherwise it's dropped and
  a note is recorded on the result.
- Thinking is on by default and counts toward `max_tokens`, hence the 16000 default.
- Safety declines come back as HTTP 200 with `stop_reason: "refusal"` (+ `stop_details`).
  That's a result, so it's returned with finish_reason "refusal".
- Server-side `fallbacks` are deliberately NOT used: a fallback would answer with a
  different model, which would corrupt a censorship measurement of *this* model.
"""

from typing import Any

from app.providers.base import (
    ChatRequest,
    ChatResult,
    HttpAdapter,
    ProviderAuthError,
    ProviderError,
    raise_for_status,
    response_json,
)

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 16000


class AnthropicProvider(HttpAdapter):
    name = "anthropic"

    def _headers(self) -> dict[str, str]:
        if not self.secret:
            raise ProviderAuthError("anthropic: no API key configured")
        return {
            "x-api-key": self.secret.reveal(),
            "anthropic-version": self.extra.get("api_version", API_VERSION),
            "content-type": "application/json",
        }

    def _url(self, path: str) -> str:
        base = self.base_url or "https://api.anthropic.com"
        return f"{base}/v1/{path}"

    def build_body(self, req: ChatRequest) -> tuple[dict[str, Any], list[str]]:
        notes: list[str] = []
        system = "\n\n".join(m.content for m in req.messages if m.role == "system")
        messages = [m.model_dump() for m in req.messages if m.role != "system"]
        body: dict[str, Any] = dict(self.extra.get("body_defaults") or {})
        body.update(
            model=req.model_id,
            max_tokens=req.max_tokens or DEFAULT_MAX_TOKENS,
            messages=messages,
        )
        if system:
            body["system"] = system
        if req.temperature is not None:
            if self.extra.get("send_temperature"):
                body["temperature"] = req.temperature
            else:
                notes.append(f"temperature {req.temperature} not sent (not supported by current Claude models)")
        return body, notes

    async def chat(self, req: ChatRequest) -> ChatResult:
        body, notes = self.build_body(req)
        response, latency = await self._send("POST", self._url("messages"), headers=self._headers(), json_body=body)
        raise_for_status(response, self.name, self.secret)
        data = response_json(response)
        if data.get("type") == "error":
            raise ProviderError(f"anthropic: {data.get('error')}", response.status_code, data)
        text = "".join(
            b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"
        )
        usage = data.get("usage") or {}
        return ChatResult(
            text=text,
            finish_reason=data.get("stop_reason"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            latency_ms=latency,
            raw_request=self.snapshot(body),
            raw_response=data,
            notes=notes,
        )

    async def list_models(self) -> list[str] | None:
        response, _ = await self._send("GET", self._url("models?limit=1000"), headers=self._headers())
        raise_for_status(response, self.name, self.secret)
        return sorted(m["id"] for m in response_json(response).get("data") or [] if m.get("id"))

    async def test_connection(self, model_id: str | None = None) -> tuple[bool, str]:
        try:
            models = await self.list_models()
        except ProviderError as e:
            return False, str(e)
        if model_id and models and model_id not in models:
            return True, f"Key OK, but model “{model_id}” is not in the list of {len(models)} models"
        return True, f"OK – {len(models or [])} models available"
