"""OpenAI-compatible `/chat/completions` adapter (DeepSeek, Mistral, xAI, Qianfan, Yandex,
Hugging Face router, Ollama, vLLM, ...). Provider `extra` options:

- `supports_json_mode`: send `response_format: {"type": "json_object"}` for the judge
- `no_auth`: send no auth header (local servers)
- `auth_header` (default `Authorization`) and `auth_prefix` (default `Bearer`)
- `secret_extra_fields`: names of extra secret fields (e.g. `folder_id`). They can be used
  as `{folder_id}` in the model ID and in `extra_headers` values.
- `extra_headers`: additional non-secret headers, e.g. `{"OpenAI-Project": "{folder_id}"}`
- `body_defaults`: extra JSON fields merged into every chat request body
- `max_tokens_field`: name of the output-limit field (default `max_tokens`; xAI wants
  `max_completion_tokens`)
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


class OpenAICompatibleProvider(HttpAdapter):
    name = "openai_compatible"

    def _fill(self, template: str) -> str:
        for field in self.extra.get("secret_extra_fields", []) or []:
            template = template.replace("{" + field + "}", self.secret.extra(field))
        return template

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if not self.extra.get("no_auth"):
            if not self.secret:
                raise ProviderAuthError(f"{self.name}: no API key configured")
            prefix = self.extra.get("auth_prefix", "Bearer")
            value = f"{prefix} {self.secret.reveal()}" if prefix else self.secret.reveal()
            headers[self.extra.get("auth_header", "Authorization")] = value
        for k, v in (self.extra.get("extra_headers") or {}).items():
            headers[k] = self._fill(str(v))
        return headers

    async def _request_headers(self) -> dict[str, str]:
        """Hook for adapters that need async auth (GigaChat's OAuth token)."""
        return self._headers()

    def build_body(self, req: ChatRequest) -> dict[str, Any]:
        body: dict[str, Any] = dict(self.extra.get("body_defaults") or {})
        body["model"] = self._fill(req.model_id)
        body["messages"] = [m.model_dump() for m in req.messages]
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.max_tokens is not None:
            body[self.extra.get("max_tokens_field") or "max_tokens"] = req.max_tokens
        if req.response_format_json and self.extra.get("supports_json_mode"):
            body["response_format"] = {"type": "json_object"}
        return body

    async def chat(self, req: ChatRequest) -> ChatResult:
        if not self.base_url:
            raise ProviderError(f"{self.name}: no base URL configured for this provider")
        body = self.build_body(req)
        response, latency = await self._send(
            "POST", f"{self.base_url}/chat/completions", headers=await self._request_headers(), json_body=body
        )
        raise_for_status(response, self.name, self.secret)
        data = response_json(response)
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.name}: response has no choices", response.status_code, data)
        choice = choices[0] or {}
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            # Content parts (e.g. Mistral Large 4 returns thinking chunks + text chunks):
            # keep only the answer text.
            content = "".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type", "text") == "text"
            )
        usage = data.get("usage") or {}
        return ChatResult(
            text=content or "",
            finish_reason=choice.get("finish_reason"),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency,
            raw_request=self.snapshot(body),
            raw_response=data,
        )

    async def list_models(self) -> list[str] | None:
        if not self.base_url:
            return None
        response, _ = await self._send("GET", f"{self.base_url}/models", headers=await self._request_headers())
        if response.status_code in (404, 405, 501):
            return None
        raise_for_status(response, self.name, self.secret)
        data = response_json(response).get("data") or []
        return sorted(str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id"))

    async def test_connection(self, model_id: str | None = None) -> tuple[bool, str]:
        try:
            models = await self.list_models()
            if models is not None:
                return True, f"OK – {len(models)} models available"
            if not model_id:
                return False, "The provider has no /models endpoint; set a model ID and use “Send test prompt”."
            await self.chat(ChatRequest(model_id=model_id, messages=[{"role": "user", "content": "Hi"}], max_tokens=1))
            return True, "OK – test chat call succeeded"
        except ProviderError as e:
            return False, str(e)
