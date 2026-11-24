import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlmodel import Session

from app.db import get_session
from app.models import ProviderKind
from app.schemas.providers import ProviderData, parse_extra_json
from app.services import credentials as creds
from app.services import providers as svc
from app.web.forms import friendly_errors
from app.web.templates import render

router = APIRouter()

# Kinds a user can add. GigaChat/Anthropic presets already exist; any other API with an
# OpenAI-compatible endpoint works through openai_compatible.
USER_KINDS = [ProviderKind.OPENAI_COMPATIBLE, ProviderKind.ANTHROPIC, ProviderKind.GIGACHAT]


def _form(request: Request, *, provider=None, values: dict, errors: list[str], status_code: int = 200):
    return render(
        request,
        "providers/form.html",
        active_nav="models",
        page_title=f"Edit provider: {provider.name}" if provider else "Add provider",
        provider=provider,
        values=values,
        kinds=USER_KINDS,
        errors=errors,
        env_var=creds.env_var_name(values.get("name") or "NAME"),
        status_code=status_code,
    )


def _values_from(provider) -> dict:
    return {
        "name": provider.name,
        "kind": provider.kind,
        "base_url": provider.base_url,
        "extra": json.dumps(provider.extra or {}, indent=2, ensure_ascii=False) if provider.extra else "",
    }


async def _parse(request: Request, kind_default: str | None = None) -> tuple[dict, ProviderData | None, list[str]]:
    form = await request.form()
    values = {
        "name": str(form.get("name") or ""),
        "kind": kind_default or str(form.get("kind") or ProviderKind.OPENAI_COMPATIBLE),
        "base_url": str(form.get("base_url") or ""),
        "extra": str(form.get("extra") or ""),
    }
    try:
        extra = parse_extra_json(values["extra"])
        # Simple checkboxes for the common OpenAI-compatible options.
        if values["kind"] == ProviderKind.OPENAI_COMPATIBLE and form.get("flags_present"):
            for flag in ("supports_json_mode", "no_auth"):
                if f"flag_{flag}" in form:
                    extra[flag] = True
                else:
                    extra.pop(flag, None)
        data = ProviderData(name=values["name"], kind=values["kind"], base_url=values["base_url"], extra=extra)
    except ValidationError as e:
        return values, None, friendly_errors(e)
    except ValueError as e:
        return values, None, [str(e)]
    return values, data, []


@router.get("/providers/new", name="provider_new")
def provider_new(request: Request):
    return _form(request, values={"kind": ProviderKind.OPENAI_COMPATIBLE}, errors=[])


@router.post("/providers/new", name="provider_create")
async def provider_create(request: Request, session: Session = Depends(get_session)):
    values, data, errors = await _parse(request)
    if data is not None:
        if data.kind not in USER_KINDS:
            errors = ["choose a provider type"]
        else:
            try:
                provider = svc.create_provider(session, data)
            except svc.ProviderServiceError as e:
                errors = [str(e)]
            else:
                return RedirectResponse(f"/models/new?provider_id={provider.id}&notice=provider_added", status_code=303)
    return _form(request, values=values, errors=errors, status_code=422)


@router.get("/providers/{provider_id:int}/edit", name="provider_edit")
def provider_edit(request: Request, provider_id: int, session: Session = Depends(get_session)):
    provider = svc.get_provider(session, provider_id)
    if provider is None:
        raise HTTPException(404, "Provider not found.")
    return _form(request, provider=provider, values=_values_from(provider), errors=[])


@router.post("/providers/{provider_id:int}/edit", name="provider_update")
async def provider_update(request: Request, provider_id: int, session: Session = Depends(get_session)):
    provider = svc.get_provider(session, provider_id)
    if provider is None:
        raise HTTPException(404, "Provider not found.")
    values, data, errors = await _parse(request, kind_default=provider.kind)
    if data is not None:
        try:
            svc.update_provider(session, provider, data)
        except svc.ProviderServiceError as e:
            errors = [str(e)]
        else:
            return RedirectResponse("/models?notice=provider_saved", status_code=303)
    return _form(request, provider=provider, values=values, errors=errors, status_code=422)


@router.post("/providers/{provider_id:int}/delete", name="provider_delete")
def provider_delete(request: Request, provider_id: int, session: Session = Depends(get_session)):
    provider = svc.get_provider(session, provider_id)
    if provider is None:
        raise HTTPException(404, "Provider not found.")
    try:
        svc.delete_provider(session, provider)
    except svc.ProviderServiceError as e:
        return _form(request, provider=provider, values=_values_from(provider), errors=[str(e)], status_code=422)
    return RedirectResponse("/models?notice=provider_deleted", status_code=303)
