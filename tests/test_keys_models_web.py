import logging
import re
import sqlite3

import httpx
import pytest
import respx
from sqlmodel import select

from app.models import Credential, LlmModel, Provider
from app.services import providers as provider_svc
from tests.conftest import csrf_token

KEY = "sk-live-SECRET-0123456789abcdef"


@pytest.fixture
def app_client(client, seeded):
    provider_svc.load_presets(seeded)
    return client


def post(client, url, data=None):
    data = dict(data or {})
    data.setdefault("csrf_token", csrf_token(client))
    return client.post(url, data=data, follow_redirects=False)


def pid(session, name):
    return session.exec(select(Provider).where(Provider.name == name)).one().id


ALL_PAGES = ["/", "/models", "/settings/keys", "/topics", "/runs/new"]


def test_keys_page_lists_presets(app_client):
    html = app_client.get("/settings/keys").text
    for name in ("Anthropic", "DeepSeek", "GigaChat", "Mock", "Local Ollama"):
        assert name in html
    assert "Back up" in html and "secret.key" in html


def test_add_test_replace_delete_key_never_leaks(app_client, session, migrated, caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="httpx")
    caplog.set_level(logging.DEBUG, logger="httpcore")
    deepseek = pid(session, "DeepSeek")

    form = app_client.get(f"/settings/keys/{deepseek}")
    assert 'type="password"' in form.text

    r = post(app_client, f"/settings/keys/{deepseek}", {"api_key": KEY})
    assert r.status_code == 303
    keys_html = app_client.get("/settings/keys").text
    assert "••••cdef" in keys_html

    with respx.mock:
        route = respx.get("https://api.deepseek.com/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "deepseek-chat"}]})
        )
        r = post(app_client, f"/settings/keys/{deepseek}/test")
        assert route.calls.last.request.headers["authorization"] == f"Bearer {KEY}"
    assert "status-completed" in r.text and "1 models available" in r.text

    # The real-key flow for a model: enable DeepSeek and send a test prompt.
    m = session.exec(select(LlmModel).where(LlmModel.display_name == "DeepSeek")).one()
    with respx.mock:
        respx.post("https://api.deepseek.com/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "choices": [{"message": {"content": "Hello **there**"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            })
        )
        r = post(app_client, f"/models/{m.id}/test", {"test_prompt": "Hi"})
    assert "<strong>there</strong>" in r.text

    # Re-showing the form after an error must not echo the key.
    r = post(app_client, f"/settings/keys/{deepseek}", {"api_key": KEY + "\nsecond line"})
    assert r.status_code == 422 and KEY not in r.text

    pages = [app_client.get(p).text for p in ALL_PAGES + [f"/settings/keys/{deepseek}", f"/providers/{deepseek}/edit", f"/models/{m.id}/edit"]]
    for html in pages:
        assert KEY not in html
        assert KEY[-8:] not in html  # only the last 4 characters are ever shown

    export = app_client.get("/topics/export").text
    assert KEY not in export

    session.close()
    con = sqlite3.connect(migrated.db_path)
    for table, in con.execute("select name from sqlite_master where type='table'"):
        for row in con.execute(f"select * from {table}"):
            assert KEY not in repr(row), table
    con.close()
    assert KEY.encode() not in migrated.db_path.read_bytes()

    assert KEY not in caplog.text
    assert KEY[-8:] not in caplog.text

    r = post(app_client, f"/settings/keys/{deepseek}/delete")
    assert r.status_code == 303
    assert "••••cdef" not in app_client.get("/settings/keys").text


def test_extra_secret_field_form(app_client, session):
    yandex = pid(session, "Yandex AI Studio")
    form = app_client.get(f"/settings/keys/{yandex}").text
    assert 'name="extra_folder_id"' in form
    r = post(app_client, f"/settings/keys/{yandex}", {"api_key": KEY})
    assert r.status_code == 422 and "folder_id" in r.text
    r = post(app_client, f"/settings/keys/{yandex}", {"api_key": KEY, "extra_folder_id": "b1gFOLDER"})
    assert r.status_code == 303
    assert "b1gFOLDER" not in app_client.get("/settings/keys").text


def test_environment_key_shown_as_env(app_client, session, monkeypatch):
    monkeypatch.setenv("HISTBIAS_KEY_ANTHROPIC", KEY)
    html = app_client.get("/settings/keys").text
    assert "from environment" in html and "HISTBIAS_KEY_ANTHROPIC" in html
    assert KEY not in html


def test_broken_key_prompts_reentry(app_client, session):
    deepseek = session.get(Provider, pid(session, "DeepSeek"))
    session.add(Credential(provider_id=deepseek.id, secret_enc=b"gAAAAgarbage", hint="abcd"))
    session.commit()
    html = app_client.get("/settings/keys").text
    assert "can't decrypt" in html and "Re-enter" in html


def test_runs_page_hint(app_client, session):
    assert "or try the Mock model" in app_client.get("/").text
    post(app_client, f"/settings/keys/{pid(session, 'DeepSeek')}", {"api_key": KEY})
    assert "or try the Mock model" not in app_client.get("/").text


# ── models ───────────────────────────────────────────────────────────────────


def test_models_page(app_client):
    html = app_client.get("/models").text
    assert "Mock Echo" in html and "mock-echo" in html and "Claude Sonnet 5.5" in html
    assert "(set model ID)" in html  # presets with an empty model ID


def test_add_edit_toggle_model(app_client, session):
    mock = pid(session, "Mock")
    r = post(app_client, "/models/new", {"provider_id": str(mock), "display_name": "Mock 2", "model_id": "mock-random",
                                         "country_code": "PL", "temperature": "0.7", "enabled": "on"})
    assert r.status_code == 303
    m = session.exec(select(LlmModel).where(LlmModel.display_name == "Mock 2")).one()
    assert (m.model_id, m.country_code, m.temperature, m.enabled) == ("mock-random", "PL", 0.7, True)

    r = post(app_client, f"/models/{m.id}/toggle", {})
    assert r.status_code == 200
    session.refresh(m)
    assert not m.enabled
    r = post(app_client, f"/models/{m.id}/toggle", {"enabled": "on"})
    session.refresh(m)
    assert m.enabled

    r = post(app_client, f"/models/{m.id}/edit", {"provider_id": str(mock), "display_name": "Mock 2", "model_id": "",
                                                  "enabled": "on"})
    assert r.status_code == 422 and "model ID" in r.text

    r = post(app_client, f"/models/{m.id}/test", {"test_prompt": "hello"})
    assert r.status_code == 200 and "<article" in r.text

    model_id = m.id
    assert post(app_client, f"/models/{model_id}/delete").status_code == 303
    session.expire_all()
    assert session.get(LlmModel, model_id) is None


def test_toggle_needs_model_id(app_client, session):
    m = session.exec(select(LlmModel).where(LlmModel.model_id == "")).first()
    r = post(app_client, f"/models/{m.id}/toggle", {"enabled": "on"})
    assert "model ID" in r.text
    session.refresh(m)
    assert not m.enabled


def test_test_prompt_without_key(app_client, session):
    m = session.exec(select(LlmModel).where(LlmModel.display_name == "DeepSeek")).one()
    r = post(app_client, f"/models/{m.id}/test", {"test_prompt": "hi"})
    assert "no API key for DeepSeek" in r.text


def test_test_prompt_output_is_sanitized(app_client, session):
    m = session.exec(select(LlmModel).where(LlmModel.model_id == "mock-echo")).one()
    r = post(app_client, f"/models/{m.id}/test", {"test_prompt": "<script>alert(1)</script>"})
    assert "<script>" not in r.text and "&lt;script&gt;" in r.text


# ── custom providers ─────────────────────────────────────────────────────────


def test_add_custom_openai_provider(app_client, session):
    r = post(app_client, "/providers/new", {"name": "My vLLM", "kind": "openai_compatible", "base_url": "http://gpu:8000/v1",
                                            "flags_present": "1", "flag_no_auth": "on"})
    assert r.status_code == 303
    p = session.exec(select(Provider).where(Provider.name == "My vLLM")).one()
    assert p.origin == "user" and p.extra == {"no_auth": True}
    assert re.search(rf"/models/new\?provider_id={p.id}", r.headers["location"])


def test_custom_provider_rejects_secret_in_settings(app_client):
    r = post(app_client, "/providers/new", {"name": "X", "kind": "openai_compatible", "base_url": "https://x.example/v1",
                                            "extra": '{"api_key": "sk-123"}'})
    assert r.status_code == 422 and "looks like a secret" in r.text


def test_edit_preset_provider_base_url(app_client, session):
    yandex = pid(session, "Yandex AI Studio")
    form = app_client.get(f"/providers/{yandex}/edit").text
    assert "secret_extra_fields" in form
    p = session.get(Provider, yandex)
    import json

    r = post(app_client, f"/providers/{yandex}/edit", {
        "name": p.name, "base_url": "https://llm.example/v1", "extra": json.dumps(p.extra), "flags_present": "1",
    })
    assert r.status_code == 303
    session.refresh(p)
    assert p.base_url == "https://llm.example/v1"
    assert p.extra["secret_extra_fields"] == ["folder_id"]


def test_mock_provider_edit_keeps_no_auth(app_client, session):
    mock = session.get(Provider, pid(session, "Mock"))
    import json

    r = post(app_client, f"/providers/{mock.id}/edit", {"name": "Mock", "base_url": "", "extra": json.dumps(mock.extra)})
    assert r.status_code == 303
    session.refresh(mock)
    assert mock.extra.get("no_auth") is True
