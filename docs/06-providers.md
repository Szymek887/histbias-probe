# 06 – Providers

All models are reached through **4 adapter kinds**. Anything else that offers an OpenAI-compatible `/chat/completions` endpoint can be added by the user in the UI (base URL + key + model ID), with no code changes.

| Adapter kind | Covers |
| :--- | :--- |
| `openai_compatible` | DeepSeek, Mistral, xAI (Grok), OpenAI, OpenRouter, Yandex AI Studio, Baidu Qianfan v2, Hugging Face router, Ollama, vLLM, LM Studio |
| `anthropic` | Claude (Messages API) |
| `gigachat` | GigaChat (OAuth token exchange before chat calls) |
| `mock` | Demo and tests. Returns canned or echo answers, needs no key. |

## Study models → presets

Verified against official provider documentation on **2026-11-24** (M2). Model IDs change often, so presets stay editable and loading presets never overwrites existing rows.

| Study model | Country | Adapter | Base URL | Auth | Preset model ID | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| DeepSeek | CN | openai_compatible | `https://api.deepseek.com` | `Authorization: Bearer` | `deepseek-flash` (+ `deepseek-v4-pro`) | `deepseek-chat` was announced for discontinuation (2026-07-24) and is no longer listed. JSON mode needs the word "json" in the prompt. Blocks: `finish_reason: content_filter`. |
| Baidu ERNIE | CN | openai_compatible | `https://qianfan.baidubce.com/v2` | `Authorization: Bearer bce-v3/ALTAK-…` | `ernie-5.1` | No documented `/models`, so the key test falls back to a 1-token chat call. Blocked input: **HTTP 403 `unsafe_request`** (mapped to a refusal, not an auth error); blocked output: `content_filter` and a `flag` field. The study used the Wenxin consumer app. |
| Alice / YandexGPT | RU | openai_compatible | `https://ai.api.cloud.yandex.net/v1` | `Authorization: Api-Key` + `OpenAI-Project: {folder_id}` | `gpt://{folder_id}/aliceai-llm` (+ `yandexgpt-5.1`) | The folder ID is an encrypted extra field; `{folder_id}` is filled into the model ID and headers. The docs disagree on the folder header for `/models` (`x-project` vs `x-folder-id`); the preset sends `x-folder-id` too. |
| GigaChat | RU | gigachat | OAuth `https://ngw.devices.sberbank.ru:9443/api/v2/oauth`, API `https://api.giga.chat/v1` | Basic authorization key + `RqUID` + `scope` → 30-min Bearer token | `GigaChat-2-Max` | Chat host moved on 2026-07-17 (the old host still works for existing users). Needs the Минцифры "Russian Trusted Root CA" (https://www.gosuslugi.ru/crt) in `ca_bundle_path`. No `json_object` mode. Blocks: `finish_reason: blacklist`. |
| Bielik | PL | openai_compatible | `http://localhost:11434/v1` (Ollama) | none | `SpeakLeash/bielik-11b-v3.1-instruct:Q4_K_M` | No `latest` tag: the tag must be explicit. |
| Le Chat / Mistral | FR | openai_compatible | `https://api.mistral.ai/v1` | `Authorization: Bearer` | `mistral-large-latest` | Points to Mistral Large 4, which always reasons and returns content chunks; only `text` chunks are kept. |
| Grok | US | openai_compatible | `https://api.x.ai/v1` | `Authorization: Bearer` | `grok-4.7` | `/chat/completions` is labelled "legacy" by xAI but works; `max_tokens` is deprecated, so the preset sets `max_tokens_field: max_completion_tokens`. |
| Claude | US | anthropic | `https://api.anthropic.com` | `x-api-key` + `anthropic-version: 2023-06-01` | `claude-sonnet-5-5` | See the anthropic adapter notes below (temperature, refusals, no fallbacks). |
| Falcon (Arabic) | ARAB (UAE) | openai_compatible | local vLLM / Ollama | none | — | **Not available on the Hugging Face router** (no inference provider serves any tiiuae model, checked 2026-11-24). The Hugging Face preset is kept for other models (`org/model[:provider]`). |
| *(any, via OpenRouter)* | per model | openai_compatible | `https://openrouter.ai/api/v1` | `Authorization: Bearer sk-or-v1-…` | none (add models in the UI as `<vendor>/<model>`) | Gateway to many vendors' models with one key. `extra.key_check_path: /key` makes the key test call `GET /key`. Moderation blocks are **403** with "moderation"/"flagged" (a refusal, not an auth error); errors during generation can arrive as **HTTP 200** with an `error` object. OpenRouter picks an upstream host per call and may fall back to another one; to fix it, set `body_defaults: {provider: {order: ["<host>"], allow_fallbacks: false}}` on the provider. Set each model's country by hand. |
| **Control group** (open weights / uncensored) | — | openai_compatible | local Ollama / vLLM | none | — | The study planned this but never ran it. |

Sources: api-docs.deepseek.com (chat, models, JSON mode, updates); openrouter.ai/docs (quickstart, API overview, errors, limits, provider routing; read 2026-11-25); cloud.baidu.com/doc/qianfan-api; aistudio.yandex.ru/docs/en/ai-studio; developers.sber.ru/docs/ru/gigachat; docs.mistral.ai; docs.x.ai; huggingface.co/docs/inference-providers; docs.ollama.com/api/openai-compatibility; platform.claude.com/docs (versioning, migration-2026-10-21, handling-stop-reasons).

**Country of origin:** the seed bank uses `ARAB` for "Arab states" topics. Falcon (UAE) should get `country_code: ARAB` so automatic criss-cross pairing works. Users can define finer codes later.

**Content blocks are results, not errors.** The refusal heuristic (M3) treats these as refusals: `finish_reason` in `content_filter` / `refusal` / `blacklist` (`REFUSAL_FINISH_REASONS` in `providers/base.py`), and `ProviderRefused` (HTTP 400/422/451 with a moderation message, or 403 `unsafe…`).

## Adapter details

### openai_compatible
- `POST {base_url}/chat/completions` with `{model, messages, temperature, max_tokens}`.
- JSON mode: `response_format: {"type": "json_object"}` only if the provider supports it, through the provider `extra.supports_json_mode` flag. Otherwise the app relies on prompt instructions and parsing.
- Usage tokens come from `usage.prompt_tokens` and `usage.completion_tokens` when present.
- Auth header defaults to `Authorization: Bearer {key}`. It can be overridden with `extra.auth_header` and `extra.auth_prefix`, e.g. Yandex `Api-Key`. `extra.no_auth` sends no header.
- Other `extra` options: `secret_extra_fields` (encrypted extra secrets such as `folder_id`, usable as `{folder_id}` in the model ID and in `extra_headers`), `extra_headers`, `body_defaults` (merged into every request body), `max_tokens_field`, and `key_check_path` (an endpoint that `test_connection` calls first to verify the key).
- An `error` object in an HTTP 200 body (top level with no `choices`, or `choices[0].error`) is mapped like the HTTP status in its `code`; without a valid code it counts as transient.
- Content parts (a `content` list) are reduced to their `text` parts.
- `test_connection`: `GET {base_url}/models` if available, otherwise a 1-token chat call.

### anthropic
- `POST /v1/messages` with raw httpx (D6, D25). The system prompt goes in the top-level `system` field. `max_tokens` is required; the default is **16000** because current models think by default and thinking counts toward it.
- `anthropic-version: 2023-06-01`. Current Claude models accept only default sampling values, and API version `2026-10-21` rejects `temperature` altogether, so **temperature is not sent** unless the provider sets `extra.send_temperature: true`. A dropped temperature is recorded in `ChatResult.notes`.
- Refusals come back as HTTP 200 with `stop_reason: "refusal"` and `stop_details`; they are returned as a normal result with `finish_reason: "refusal"`.
- Server-side refusal **fallbacks are never enabled**: a fallback model would answer instead of the tested model and corrupt the measurement.
- Thinking blocks are ignored; only `text` blocks form the answer.

### gigachat
1. Exchange the authorization key for an access token. Cache it until expiry, minus a 60 s margin.
2. Call the OpenAI-like chat endpoint.
3. TLS uses the configured CA bundle (`ca_bundle_path`). **Never** disable verification. If verification fails, or the file is missing, the error explains the CA requirement.

### mock
- Models `mock-echo` (repeats the prompt), `mock-refuse` (always refuses), and `mock-random` (picks a canned answer). `extra.delay_ms` adds latency, `extra.seed` makes `mock-random`/`mock-judge` deterministic.
- `mock-judge` returns valid random judge JSON.
- With these, the whole app (wizard → run → results) works offline. This is also the basis for tests.

## Adding a new provider type

1. Implement `ChatProvider` in `app/providers/<name>.py`.
2. Register it in `providers/registry.py`.
3. Add a preset to `seed/providers.yaml`.
4. Add `respx`-mocked tests for success, auth error, rate limit, and content block.
