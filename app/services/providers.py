"""Providers and the model registry (presets from seed/providers.yaml, user additions)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlmodel import Session, col, select

from app.crypto import Cipher
from app.models import Country, LlmModel, Provider, ProviderKind, ProviderOrigin, RunItem, RunModel
from app.models.base import utcnow
from app.models.runs import Judgement, Response, Run
from app.providers.base import ChatMessage, ChatRequest, ChatResult, ProviderError
from app.schemas.providers import ModelData, ProviderData
from app.services import credentials

PRESETS_PATH = Path(__file__).resolve().parents[2] / "seed" / "providers.yaml"


class ProviderServiceError(ValueError):
    """User-facing validation error."""


# ── presets ──────────────────────────────────────────────────────────────────


@dataclass
class PresetResult:
    providers_added: int = 0
    models_added: int = 0


def load_presets(session: Session, path: Path = PRESETS_PATH) -> PresetResult:
    """Idempotent: providers are matched by name, models by (provider, display name).
    Existing rows are never changed, so user edits survive re-seeding."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result = PresetResult()
    countries = {c.code for c in session.exec(select(Country))}
    for p in doc.get("providers") or []:
        provider = session.exec(select(Provider).where(Provider.name == p["name"])).first()
        if provider is None:
            data = ProviderData(
                name=p["name"], kind=p["kind"], base_url=p.get("base_url") or "", extra=p.get("extra") or {}
            )
            provider = Provider(**data.model_dump(), origin=ProviderOrigin.PRESET)
            session.add(provider)
            session.flush()
            result.providers_added += 1
        existing = {
            m.display_name for m in session.exec(select(LlmModel).where(LlmModel.provider_id == provider.id))
        }
        for m in p.get("models") or []:
            if m["display_name"] in existing:
                continue
            country = m.get("country")
            session.add(
                LlmModel(
                    provider_id=provider.id,
                    display_name=m["display_name"],
                    model_id=m.get("model_id") or "",
                    country_code=country if country in countries else None,
                    notes=m.get("notes"),
                    # Mock models work out of the box; real ones wait for a key.
                    enabled=provider.kind == ProviderKind.MOCK,
                )
            )
            result.models_added += 1
    session.commit()
    return result


# ── providers ────────────────────────────────────────────────────────────────


def list_providers(session: Session) -> list[Provider]:
    return list(session.exec(select(Provider).order_by(Provider.name)))


def get_provider(session: Session, provider_id: int) -> Provider | None:
    return session.get(Provider, provider_id)


def _name_taken(session: Session, name: str, exclude_id: int | None = None) -> bool:
    stmt = select(Provider).where(Provider.name == name)
    if exclude_id is not None:
        stmt = stmt.where(Provider.id != exclude_id)
    return session.exec(stmt).first() is not None


def create_provider(session: Session, data: ProviderData) -> Provider:
    if _name_taken(session, data.name):
        raise ProviderServiceError(f"a provider named “{data.name}” already exists")
    if data.kind != ProviderKind.MOCK and not data.base_url:
        raise ProviderServiceError("enter the base URL")
    provider = Provider(**data.model_dump(), origin=ProviderOrigin.USER)
    session.add(provider)
    session.commit()
    return provider


def update_provider(session: Session, provider: Provider, data: ProviderData) -> Provider:
    """Name changes are allowed (the env-override variable name follows the name)."""
    if _name_taken(session, data.name, exclude_id=provider.id):
        raise ProviderServiceError(f"a provider named “{data.name}” already exists")
    if data.kind != provider.kind:
        raise ProviderServiceError("the provider kind can't be changed; add a new provider instead")
    provider.name = data.name
    provider.base_url = data.base_url
    provider.extra = data.extra
    provider.updated_at = utcnow()
    session.add(provider)
    session.commit()
    return provider


def delete_provider(session: Session, provider: Provider) -> None:
    has_models = session.exec(select(LlmModel.id).where(LlmModel.provider_id == provider.id)).first()
    if has_models is not None:
        raise ProviderServiceError("delete or move this provider's models first")
    session.delete(provider)  # the credential row goes with it (ON DELETE CASCADE)
    session.commit()


# ── models ───────────────────────────────────────────────────────────────────


@dataclass
class ModelRow:
    model: LlmModel
    provider: Provider
    key: credentials.KeyStatus

    @property
    def usable(self) -> bool:
        return self.key.usable and bool(self.model.model_id)


def list_models(session: Session, cipher: Cipher | None) -> list[ModelRow]:
    providers = {p.id: p for p in list_providers(session)}
    statuses = {pid: credentials.key_status(session, cipher, p) for pid, p in providers.items()}
    models = session.exec(select(LlmModel).order_by(LlmModel.display_name)).all()
    rows = [ModelRow(m, providers[m.provider_id], statuses[m.provider_id]) for m in models]
    # Usable first, then mock last, then by name.
    return sorted(
        rows,
        key=lambda r: (r.provider.kind == ProviderKind.MOCK, not r.usable, r.provider.name, r.model.display_name),
    )


def get_model(session: Session, model_id: int) -> LlmModel | None:
    return session.get(LlmModel, model_id)


def _check_model(session: Session, data: ModelData) -> None:
    if session.get(Provider, data.provider_id) is None:
        raise ProviderServiceError("choose a provider")
    if data.country_code and session.get(Country, data.country_code) is None:
        raise ProviderServiceError(f"unknown country {data.country_code}")


def create_model(session: Session, data: ModelData) -> LlmModel:
    _check_model(session, data)
    model = LlmModel(**data.model_dump())
    session.add(model)
    session.commit()
    return model


def update_model(session: Session, model: LlmModel, data: ModelData) -> LlmModel:
    """Past runs are unaffected: responses keep their own model-ID snapshot."""
    _check_model(session, data)
    for k, v in data.model_dump().items():
        setattr(model, k, v)
    model.updated_at = utcnow()
    session.add(model)
    session.commit()
    return model


def set_enabled(session: Session, model: LlmModel, enabled: bool) -> LlmModel:
    if enabled and not model.model_id:
        raise ProviderServiceError("set the API model ID before enabling the model")
    model.enabled = enabled
    model.updated_at = utcnow()
    session.add(model)
    session.commit()
    return model


def is_model_used(session: Session, model: LlmModel) -> bool:
    checks = [
        select(RunModel.run_id).where(RunModel.llm_model_id == model.id),
        select(Run.id).where(Run.judge_model_id == model.id),
        select(RunItem.id).where((RunItem.model_a_id == model.id) | (col(RunItem.model_b_id) == model.id)),
        select(Response.id).where(Response.llm_model_id == model.id),
        select(Judgement.id).where(Judgement.judge_model_id == model.id),
    ]
    return any(session.exec(stmt).first() is not None for stmt in checks)


def delete_model(session: Session, model: LlmModel) -> None:
    if is_model_used(session, model):
        raise ProviderServiceError("this model is used by a run; disable it instead of deleting it")
    session.delete(model)
    session.commit()


async def send_test_prompt(
    session: Session, cipher: Cipher | None, model: LlmModel, prompt: str, timeout: float = 120
) -> ChatResult:
    """One chat call with the model's own settings. Raises ProviderError/CredentialError."""
    prompt = (prompt or "").strip() or "Say hello in one short sentence."
    if not model.model_id:
        raise ProviderServiceError("set the API model ID first")
    provider = session.get(Provider, model.provider_id)
    adapter = credentials.adapter_for(session, cipher, provider, timeout)
    messages: list[dict[str, Any]] = []
    if model.system_prompt:
        messages.append({"role": "system", "content": model.system_prompt})
    messages.append({"role": "user", "content": prompt})
    req = ChatRequest(
        model_id=model.model_id,
        messages=[ChatMessage(**m) for m in messages],
        temperature=model.temperature,
        max_tokens=model.max_tokens,
    )
    return await adapter.chat(req)


__all__ = ["ProviderError", "ProviderServiceError"]
