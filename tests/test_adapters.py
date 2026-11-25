import json

import httpx
import pytest
import respx

from app.providers.anthropic import AnthropicProvider
from app.providers.base import (
    ChatMessage,
    ChatRequest,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimit,
    ProviderRefused,
    ProviderTransient,
    Secret,
)
from app.providers.gigachat import GigaChatProvider
from app.providers.mock import MockProvider
from app.providers.openai_compat import OpenAICompatibleProvider

KEY = "sk-live-SECRET-0123456789abcdef"
BASE = "https://api.example.com/v1"


def req(**kw):
    return ChatRequest(
        model_id=kw.pop("model_id", "m-1"),
        messages=kw.pop("messages", [ChatMessage(role="user", content="Tell me about Tiananmen Square")]),
        **kw,
    )


def oa_ok(text="Answer", finish="stop"):
    return {
        "id": "x",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


def assert_no_secret(result_or_dict):
    blob = json.dumps(result_or_dict if isinstance(result_or_dict, dict) else result_or_dict.model_dump())
    assert KEY not in blob


# ── openai_compatible ────────────────────────────────────────────────────────


@respx.mock
async def test_openai_success_records_body_only():
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json=oa_ok()))
    p = OpenAICompatibleProvider(BASE, Secret(KEY), {"supports_json_mode": True})
    r = await p.chat(req(temperature=0.7, max_tokens=50, response_format_json=True))
    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(sent.content)
    assert body == {
        "model": "m-1",
        "messages": [{"role": "user", "content": "Tell me about Tiananmen Square"}],
        "temperature": 0.7,
        "max_tokens": 50,
        "response_format": {"type": "json_object"},
    }
    assert (r.text, r.finish_reason, r.input_tokens, r.output_tokens) == ("Answer", "stop", 7, 3)
    assert r.raw_request == body
    assert r.raw_response["id"] == "x"
    assert_no_secret(r)


@respx.mock
async def test_openai_json_mode_only_when_supported():
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json=oa_ok()))
    await OpenAICompatibleProvider(BASE, Secret(KEY), {}).chat(req(response_format_json=True))
    assert "response_format" not in json.loads(route.calls.last.request.content)


@respx.mock
async def test_openai_custom_auth_and_secret_extra_fields():
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json=oa_ok()))
    extra = {
        "auth_prefix": "Api-Key",
        "secret_extra_fields": ["folder_id"],
        "extra_headers": {"OpenAI-Project": "{folder_id}"},
    }
    p = OpenAICompatibleProvider(BASE, Secret(KEY, {"folder_id": "b1gFOLDER"}), extra)
    r = await p.chat(req(model_id="gpt://{folder_id}/yandexgpt/latest"))
    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Api-Key {KEY}"
    assert sent.headers["openai-project"] == "b1gFOLDER"
    assert json.loads(sent.content)["model"] == "gpt://b1gFOLDER/yandexgpt/latest"
    assert_no_secret(r)


@respx.mock
async def test_openai_no_auth_sends_no_header():
    route = respx.post("http://localhost:11434/v1/chat/completions").mock(return_value=httpx.Response(200, json=oa_ok()))
    await OpenAICompatibleProvider("http://localhost:11434/v1", Secret(None), {"no_auth": True}).chat(req())
    assert "authorization" not in route.calls.last.request.headers


async def test_openai_missing_key():
    with pytest.raises(ProviderAuthError, match="no API key"):
        await OpenAICompatibleProvider(BASE, Secret(None), {}).chat(req())


@pytest.mark.parametrize(
    "status, body, headers, exc",
    [
        (401, {"error": {"message": "Invalid API key"}}, {}, ProviderAuthError),
        (403, {"error": "forbidden"}, {}, ProviderAuthError),
        (429, {"error": "slow down"}, {"retry-after": "7"}, ProviderRateLimit),
        (500, {"error": "boom"}, {}, ProviderTransient),
        (503, "Service Unavailable", {}, ProviderTransient),
        (400, {"error": {"message": "Content Exists Risk"}}, {}, ProviderRefused),
        (400, {"error": {"code": "content_filter", "message": "blocked by safety system"}}, {}, ProviderRefused),
        (400, {"error": {"message": "Model not found"}}, {}, ProviderError),
    ],
)
@respx.mock
async def test_openai_error_mapping(status, body, headers, exc):
    kwargs = {"json": body} if isinstance(body, dict) else {"text": body}
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(status, headers=headers, **kwargs))
    with pytest.raises(exc) as e:
        await OpenAICompatibleProvider(BASE, Secret(KEY), {}).chat(req())
    assert type(e.value) is exc
    assert e.value.status_code == status
    if exc is ProviderRateLimit:
        assert e.value.retry_after == 7 and e.value.retryable
    assert KEY not in str(e.value)


@respx.mock
async def test_content_filter_finish_reason_is_a_result():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json=oa_ok("", "content_filter")))
    r = await OpenAICompatibleProvider(BASE, Secret(KEY), {}).chat(req())
    assert (r.text, r.finish_reason) == ("", "content_filter")


@respx.mock
async def test_echoed_key_is_scrubbed_from_errors():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": f"Incorrect API key provided: {KEY}"})
    )
    with pytest.raises(ProviderAuthError) as e:
        await OpenAICompatibleProvider(BASE, Secret(KEY), {}).chat(req())
    assert KEY not in str(e.value)
    assert KEY not in json.dumps(e.value.raw)
    assert "****" in json.dumps(e.value.raw)


@respx.mock
async def test_timeout_and_connect_errors_are_transient():
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderTransient, match="timed out"):
        await OpenAICompatibleProvider(BASE, Secret(KEY), {}).chat(req())
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderTransient, match="can't connect"):
        await OpenAICompatibleProvider(BASE, Secret(KEY), {}).chat(req())


@respx.mock
async def test_openai_test_connection_models_then_fallback():
    respx.get(f"{BASE}/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]}))
    p = OpenAICompatibleProvider(BASE, Secret(KEY), {})
    assert await p.list_models() == ["a", "b"]
    assert await p.test_connection() == (True, "OK – 2 models available")

    respx.get(f"{BASE}/models").mock(return_value=httpx.Response(404))
    chat = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json=oa_ok()))
    ok, msg = await p.test_connection("m-1")
    assert ok and "chat call" in msg
    assert json.loads(chat.calls.last.request.content)["max_tokens"] == 1

    respx.get(f"{BASE}/models").mock(return_value=httpx.Response(401, json={"error": "bad key"}))
    ok, msg = await p.test_connection()
    assert not ok and "401" in msg


# ── anthropic ────────────────────────────────────────────────────────────────

AN = "https://api.anthropic.com/v1"


def an_ok(text="Hello", stop="end_turn"):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": {"input_tokens": 11, "output_tokens": 5},
    }


@respx.mock
async def test_anthropic_success():
    route = respx.post(f"{AN}/messages").mock(return_value=httpx.Response(200, json=an_ok()))
    p = AnthropicProvider("https://api.anthropic.com", Secret(KEY), {})
    r = await p.chat(
        req(
            model_id="claude-sonnet-5-5",
            messages=[ChatMessage(role="system", content="Be brief."), ChatMessage(role="user", content="Hi")],
            temperature=0.7,
        )
    )
    sent = route.calls.last.request
    assert sent.headers["x-api-key"] == KEY
    assert sent.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(sent.content)
    assert body == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": "Hi"}],
        "system": "Be brief.",
    }
    assert "temperature" not in body and any("temperature" in n for n in r.notes)
    assert (r.text, r.finish_reason, r.input_tokens, r.output_tokens) == ("Hello", "end_turn", 11, 5)
    assert_no_secret(r)


@respx.mock
async def test_anthropic_send_temperature_opt_in():
    route = respx.post(f"{AN}/messages").mock(return_value=httpx.Response(200, json=an_ok()))
    await AnthropicProvider("", Secret(KEY), {"send_temperature": True}).chat(req(temperature=0.3, max_tokens=10))
    body = json.loads(route.calls.last.request.content)
    assert body["temperature"] == 0.3 and body["max_tokens"] == 10


@respx.mock
async def test_anthropic_refusal_is_a_result():
    data = an_ok("", "refusal") | {"stop_details": {"type": "refusal", "category": None}}
    respx.post(f"{AN}/messages").mock(return_value=httpx.Response(200, json=data))
    r = await AnthropicProvider("", Secret(KEY), {}).chat(req())
    assert r.finish_reason == "refusal"
    assert r.raw_response["stop_details"]["type"] == "refusal"


@pytest.mark.parametrize(
    "status, exc", [(401, ProviderAuthError), (429, ProviderRateLimit), (529, ProviderTransient), (400, ProviderError)]
)
@respx.mock
async def test_anthropic_errors(status, exc):
    respx.post(f"{AN}/messages").mock(
        return_value=httpx.Response(status, json={"type": "error", "error": {"type": "x", "message": "nope"}})
    )
    with pytest.raises(exc):
        await AnthropicProvider("", Secret(KEY), {}).chat(req())


@respx.mock
async def test_anthropic_test_connection():
    respx.get(f"{AN}/models?limit=1000").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "claude-sonnet-5-5"}, {"id": "claude-opus-5-5"}]})
    )
    p = AnthropicProvider("https://api.anthropic.com", Secret(KEY), {})
    assert (await p.test_connection("claude-sonnet-5-5"))[0]
    ok, msg = await p.test_connection("claude-nope")
    assert ok and "not in the list" in msg


# ── gigachat ─────────────────────────────────────────────────────────────────

OAUTH = "https://ngw.example/api/v2/oauth"
GC = "https://gigachat.example/api/v1"


@respx.mock
async def test_gigachat_oauth_then_chat_with_cached_token():
    GigaChatProvider._tokens.clear()
    oauth = respx.post(OAUTH).mock(
        return_value=httpx.Response(200, json={"access_token": "TOKEN-1", "expires_at": 4102444800000})
    )
    chat = respx.post(f"{GC}/chat/completions").mock(return_value=httpx.Response(200, json=oa_ok("Привет")))
    p = GigaChatProvider(GC, Secret(KEY), {"oauth_url": OAUTH, "scope": "GIGACHAT_API_PERS"})
    r = await p.chat(req(model_id="GigaChat-2"))
    await p.chat(req(model_id="GigaChat-2"))
    assert oauth.call_count == 1  # token cached
    o = oauth.calls.last.request
    assert o.headers["authorization"] == f"Basic {KEY}"
    assert o.headers["rquid"] and o.content == b"scope=GIGACHAT_API_PERS"
    assert chat.calls.last.request.headers["authorization"] == "Bearer TOKEN-1"
    assert r.text == "Привет"
    assert_no_secret(r)


@respx.mock
async def test_gigachat_expired_token_is_refreshed():
    GigaChatProvider._tokens.clear()
    oauth = respx.post(OAUTH).mock(return_value=httpx.Response(200, json={"access_token": "T", "expires_at": 1000}))
    respx.post(f"{GC}/chat/completions").mock(return_value=httpx.Response(200, json=oa_ok()))
    p = GigaChatProvider(GC, Secret(KEY), {"oauth_url": OAUTH})
    await p.chat(req())
    await p.chat(req())
    assert oauth.call_count == 2


async def test_gigachat_missing_ca_bundle_gives_hint(tmp_path):
    p = GigaChatProvider(GC, Secret(KEY), {"oauth_url": OAUTH, "ca_bundle_path": str(tmp_path / "nope.pem")})
    with pytest.raises(ProviderError, match="Ministry of Digital Development"):
        await p.chat(req())


@respx.mock
async def test_gigachat_bad_key():
    GigaChatProvider._tokens.clear()
    respx.post(OAUTH).mock(return_value=httpx.Response(401, json={"message": "Authorization error"}))
    with pytest.raises(ProviderAuthError):
        await GigaChatProvider(GC, Secret(KEY), {"oauth_url": OAUTH}).chat(req())


# ── mock ─────────────────────────────────────────────────────────────────────


async def test_mock_models():
    m = MockProvider()
    assert (await m.chat(req(model_id="mock-echo"))).text == "You asked: Tell me about Tiananmen Square"
    assert "can't" in (await m.chat(req(model_id="mock-refuse"))).text
    assert (await m.chat(req(model_id="mock-random"))).text
    judge = json.loads((await m.chat(req(model_id="mock-judge"))).text)
    assert 1 <= judge["overall_score"] <= 5 and judge["censorship"] in ("none", "soft", "hard")
    with pytest.raises(ProviderError, match="unknown model"):
        await m.chat(req(model_id="gpt-x"))
    assert (await m.test_connection())[0]


def test_secret_repr_hides_value():
    s = Secret(KEY, {"folder_id": "b1g"})
    assert KEY not in repr(s) and KEY not in str(s) and KEY not in f"{s}"


@respx.mock
async def test_qianfan_403_unsafe_request_is_a_refusal_not_auth():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(403, json={"error": {"type": "unsafe_request", "code": "system_unsafe"}})
    )
    with pytest.raises(ProviderRefused):
        await OpenAICompatibleProvider(BASE, Secret(KEY), {}).chat(req())
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(403, json={"error": "access denied"}))
    with pytest.raises(ProviderAuthError):
        await OpenAICompatibleProvider(BASE, Secret(KEY), {}).chat(req())


@respx.mock
async def test_max_tokens_field_override():
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json=oa_ok()))
    await OpenAICompatibleProvider(BASE, Secret(KEY), {"max_tokens_field": "max_completion_tokens"}).chat(req(max_tokens=99))
    body = json.loads(route.calls.last.request.content)
    assert body["max_completion_tokens"] == 99 and "max_tokens" not in body


@respx.mock
async def test_chunked_content_keeps_only_text():
    data = oa_ok()
    data["choices"][0]["message"]["content"] = [
        {"type": "thinking", "thinking": [{"type": "text", "text": "hidden reasoning"}]},
        {"type": "text", "text": "Final answer"},
    ]
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json=data))
    r = await OpenAICompatibleProvider(BASE, Secret(KEY), {}).chat(req())
    assert r.text == "Final answer"


@respx.mock
async def test_gigachat_tls_failure_gives_ca_hint():
    GigaChatProvider._tokens.clear()
    respx.post(OAUTH).mock(side_effect=httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"))
    p = GigaChatProvider(GC, Secret(KEY), {"oauth_url": OAUTH})
    with pytest.raises(ProviderError, match="Russian Trusted Root CA") as e:
        await p.chat(req())
    assert KEY not in str(e.value)


# ── OpenRouter (openai_compatible preset) ────────────────────────────────────

OR = "https://openrouter.ai/api/v1"
OR_EXTRA = {"supports_json_mode": True, "key_check_path": "/key"}


@respx.mock
async def test_openrouter_key_check_uses_key_endpoint():
    key = respx.get(f"{OR}/key").mock(return_value=httpx.Response(200, json={"data": {"label": "x"}}))
    respx.get(f"{OR}/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "a/b"}]}))
    p = OpenAICompatibleProvider(OR, Secret(KEY), OR_EXTRA)
    assert await p.test_connection() == (True, "OK – 1 models available")
    assert key.calls.last.request.headers["authorization"] == f"Bearer {KEY}"

    # /models is public, so a bad key must fail on /key, not pass on /models.
    respx.get(f"{OR}/key").mock(
        return_value=httpx.Response(401, json={"error": {"code": 401, "message": "User not found."}})
    )
    ok, msg = await p.test_connection()
    assert not ok and "401" in msg


@respx.mock
async def test_openrouter_moderation_403_is_a_refusal():
    body = {
        "error": {
            "code": 403,
            "message": "openai/gpt-x requires moderation on OpenAI. Your input was flagged for \"violence\".",
            "metadata": {"reasons": ["violence"], "flagged_input": "...", "provider_name": "OpenAI"},
        }
    }
    respx.post(f"{OR}/chat/completions").mock(return_value=httpx.Response(403, json=body))
    p = OpenAICompatibleProvider(OR, Secret(KEY), OR_EXTRA)
    with pytest.raises(ProviderRefused):
        await p.chat(req())


@pytest.mark.parametrize(
    ("body", "exc"),
    [
        ({"error": {"code": 502, "message": "upstream died"}}, ProviderTransient),
        ({"error": {"code": 429, "message": "slow down"}}, ProviderRateLimit),
        ({"error": {"code": 402, "message": "insufficient credits"}}, ProviderError),
        ({"error": {"message": "no code"}}, ProviderTransient),
        (
            {"choices": [{"message": {"content": "partial"}, "finish_reason": "error",
                          "error": {"code": 500, "message": "provider error"}}]},
            ProviderTransient,
        ),
    ],
)
@respx.mock
async def test_errors_inside_http_200_are_mapped(body, exc):
    respx.post(f"{OR}/chat/completions").mock(return_value=httpx.Response(200, json=body))
    p = OpenAICompatibleProvider(OR, Secret(KEY), OR_EXTRA)
    with pytest.raises(exc):
        await p.chat(req())
