import pytest
from pydantic import ValidationError
from sqlmodel import select

from app.models import LlmModel, Provider, Run, RunModel
from app.schemas.providers import ModelData, ProviderData, parse_extra_json
from app.services import providers as svc


def test_presets_load_idempotently(seeded):
    r = svc.load_presets(seeded)
    assert r.providers_added >= 9 and r.models_added >= 10
    again = svc.load_presets(seeded)
    assert (again.providers_added, again.models_added) == (0, 0)
    mock = seeded.exec(select(Provider).where(Provider.kind == "mock")).one()
    mock_models = seeded.exec(select(LlmModel).where(LlmModel.provider_id == mock.id)).all()
    assert mock_models and all(m.enabled for m in mock_models)
    real = seeded.exec(select(LlmModel).where(LlmModel.provider_id != mock.id)).all()
    assert not any(m.enabled for m in real)
    claude = seeded.exec(select(LlmModel).where(LlmModel.display_name.like("Claude%"))).first()
    assert claude.country_code == "US"


def test_presets_without_countries(session):
    svc.load_presets(session)  # no topic seed: countries unknown -> left empty
    assert all(m.country_code is None for m in session.exec(select(LlmModel)))


def test_presets_keep_user_edits(seeded):
    svc.load_presets(seeded)
    m = seeded.exec(select(LlmModel).where(LlmModel.display_name == "DeepSeek")).one()
    m.model_id = "my-custom"
    seeded.add(m)
    seeded.commit()
    svc.load_presets(seeded)
    seeded.refresh(m)
    assert m.model_id == "my-custom"


@pytest.mark.parametrize(
    "extra, msg",
    [({"api_key": "x"}, "looks like a secret"), ({"Authorization": "Bearer x"}, "looks like a secret")],
)
def test_provider_extra_rejects_secrets(extra, msg):
    with pytest.raises(ValidationError, match=msg):
        ProviderData(name="X", kind="openai_compatible", base_url="https://x.example", extra=extra)


def test_provider_extra_allows_known_settings():
    d = ProviderData(name="Y", kind="openai_compatible", base_url="https://y.example/v1/",
                     extra={"auth_prefix": "Api-Key", "secret_extra_fields": ["folder_id"]})
    assert d.base_url == "https://y.example/v1"


@pytest.mark.parametrize("url", ["ftp://x", "https://user:pass@x.example", "https://x.example/v1?key=abc", "x.example"])
def test_provider_base_url_rules(url):
    with pytest.raises(ValidationError):
        ProviderData(name="X", kind="openai_compatible", base_url=url)


def test_parse_extra_json():
    assert parse_extra_json("") == {}
    with pytest.raises(ValueError, match="valid JSON"):
        parse_extra_json("{bad")
    with pytest.raises(ValueError, match="object"):
        parse_extra_json("[1]")


def test_model_rules(session):
    with pytest.raises(ValidationError, match="model ID"):
        ModelData(provider_id=1, display_name="X", model_id="", enabled=True)
    with pytest.raises(ValidationError):
        ModelData(provider_id=1, display_name="X", temperature=3)


def test_custom_provider_and_model(seeded):
    p = svc.create_provider(seeded, ProviderData(name="vLLM box", kind="openai_compatible", base_url="http://gpu:8000/v1"))
    with pytest.raises(svc.ProviderServiceError, match="already exists"):
        svc.create_provider(seeded, ProviderData(name="vLLM box", kind="openai_compatible", base_url="http://gpu:8000/v1"))
    m = svc.create_model(seeded, ModelData(provider_id=p.id, display_name="Bielik", model_id="bielik", country_code="pl"))
    assert m.country_code == "PL" and not m.enabled
    svc.set_enabled(seeded, m, True)
    with pytest.raises(svc.ProviderServiceError, match="models first"):
        svc.delete_provider(seeded, p)


def test_model_used_by_run_cannot_be_deleted(seeded):
    svc.load_presets(seeded)
    m = seeded.exec(select(LlmModel).where(LlmModel.model_id == "mock-echo")).one()
    run = Run(name="r")
    seeded.add(run)
    seeded.flush()
    seeded.add(RunModel(run_id=run.id, llm_model_id=m.id))
    seeded.commit()
    with pytest.raises(svc.ProviderServiceError, match="used by a run"):
        svc.delete_model(seeded, m)


async def test_send_test_prompt_with_mock(seeded):
    svc.load_presets(seeded)
    m = seeded.exec(select(LlmModel).where(LlmModel.model_id == "mock-echo")).one()
    r = await svc.send_test_prompt(seeded, None, m, "Ping?")
    assert r.text == "You asked: Ping?"


def test_every_preset_is_valid():
    import yaml

    doc = yaml.safe_load(svc.PRESETS_PATH.read_text(encoding="utf-8"))
    for p in doc["providers"]:
        ProviderData(name=p["name"], kind=p["kind"], base_url=p.get("base_url") or "", extra=p.get("extra") or {})
        for m in p["models"]:
            assert set(m) <= {"display_name", "model_id", "country", "notes"}, m
    names = {m["model_id"] for p in doc["providers"] for m in p["models"]}
    assert "deepseek-chat" not in names  # discontinued upstream
