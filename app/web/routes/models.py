from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlmodel import Session

from app.crypto import Cipher, DecryptionError
from app.db import get_session
from app.providers.base import ProviderError
from app.schemas.providers import ModelData
from app.services import credentials as creds
from app.services import providers as svc
from app.services import topics as topic_svc
from app.web.deps import get_cipher
from app.web.forms import friendly_errors
from app.web.templates import render, templates

router = APIRouter()

NOTICES = {
    "created": "Model added.",
    "saved": "Model saved.",
    "deleted": "Model deleted.",
    "provider_added": "Provider added. Now add a model for it, and its API key in Settings → API keys.",
    "provider_saved": "Provider saved.",
    "provider_deleted": "Provider deleted.",
}

FIELDS = ("provider_id", "display_name", "model_id", "country_code", "temperature", "max_tokens", "system_prompt", "notes")


def _model_or_404(session: Session, model_id: int):
    model = svc.get_model(session, model_id)
    if model is None:
        raise HTTPException(404, "Model not found.")
    return model


def _form(request: Request, session: Session, *, model=None, values: dict, errors: list[str], status_code=200, notice=None):
    return render(
        request,
        "models/form.html",
        active_nav="models",
        page_title=f"Edit model: {model.display_name}" if model else "Add model",
        model=model,
        values=values,
        errors=errors,
        providers=svc.list_providers(session),
        countries=topic_svc.list_countries(session),
        notice=notice,
        status_code=status_code,
    )


def _values_from(model) -> dict:
    return {f: "" if getattr(model, f) is None else str(getattr(model, f)) for f in FIELDS} | {
        "enabled": model.enabled
    }


async def _parse(request: Request) -> tuple[dict, ModelData | None, list[str]]:
    form = await request.form()
    values = {f: str(form.get(f) or "") for f in FIELDS} | {"enabled": "enabled" in form}
    raw = {k: (v if v != "" else None) for k, v in values.items()}
    raw["display_name"] = values["display_name"]
    raw["model_id"] = values["model_id"]
    try:
        return values, ModelData.model_validate(raw), []
    except ValidationError as e:
        return values, None, friendly_errors(e)


@router.get("/models", name="models_list")
def models_list(
    request: Request, notice: str = "", session: Session = Depends(get_session),
    cipher: Cipher | None = Depends(get_cipher),
):
    return render(
        request,
        "models/list.html",
        active_nav="models",
        page_title="Models",
        rows=svc.list_models(session, cipher),
        providers=svc.list_providers(session),
        notice=NOTICES.get(notice),
    )


@router.get("/models/new", name="model_new")
def model_new(request: Request, provider_id: str = "", notice: str = "", session: Session = Depends(get_session)):
    return _form(request, session, values={"provider_id": provider_id, "enabled": False}, errors=[], notice=NOTICES.get(notice))


@router.post("/models/new", name="model_create")
async def model_create(request: Request, session: Session = Depends(get_session)):
    values, data, errors = await _parse(request)
    if data is not None:
        try:
            model = svc.create_model(session, data)
        except svc.ProviderServiceError as e:
            errors = [str(e)]
        else:
            return RedirectResponse(f"/models/{model.id}/edit?notice=created", status_code=303)
    return _form(request, session, values=values, errors=errors, status_code=422)


@router.get("/models/{model_id:int}/edit", name="model_edit")
def model_edit(request: Request, model_id: int, notice: str = "", session: Session = Depends(get_session)):
    model = _model_or_404(session, model_id)
    return _form(request, session, model=model, values=_values_from(model), errors=[], notice=NOTICES.get(notice))


@router.post("/models/{model_id:int}/edit", name="model_update")
async def model_update(request: Request, model_id: int, session: Session = Depends(get_session)):
    model = _model_or_404(session, model_id)
    values, data, errors = await _parse(request)
    if data is not None:
        try:
            svc.update_model(session, model, data)
        except svc.ProviderServiceError as e:
            errors = [str(e)]
        else:
            return RedirectResponse("/models?notice=saved", status_code=303)
    return _form(request, session, model=model, values=values, errors=errors, status_code=422)


@router.post("/models/{model_id:int}/toggle", name="model_toggle")
async def model_toggle(
    request: Request, model_id: int, session: Session = Depends(get_session),
    cipher: Cipher | None = Depends(get_cipher),
):
    model = _model_or_404(session, model_id)
    form = await request.form()
    error = None
    try:
        svc.set_enabled(session, model, "enabled" in form)
    except svc.ProviderServiceError as e:
        error = str(e)
    row = next(r for r in svc.list_models(session, cipher) if r.model.id == model.id)
    return templates.TemplateResponse(request, "models/_toggle.html", {"r": row, "error": error})


@router.post("/models/{model_id:int}/delete", name="model_delete")
def model_delete(request: Request, model_id: int, session: Session = Depends(get_session)):
    model = _model_or_404(session, model_id)
    try:
        svc.delete_model(session, model)
    except svc.ProviderServiceError as e:
        return _form(request, session, model=model, values=_values_from(model), errors=[str(e)], status_code=422)
    return RedirectResponse("/models?notice=deleted", status_code=303)


@router.post("/models/{model_id:int}/test", name="model_test")
async def model_test(
    request: Request, model_id: int, session: Session = Depends(get_session),
    cipher: Cipher | None = Depends(get_cipher),
):
    model = _model_or_404(session, model_id)
    form = await request.form()
    result, error = None, None
    try:
        result = await svc.send_test_prompt(
            session, cipher, model, str(form.get("test_prompt") or ""),
            timeout=request.app.state.settings.request_timeout,
        )
    except (ProviderError, creds.CredentialError, DecryptionError, svc.ProviderServiceError) as e:
        error = str(e)
    return templates.TemplateResponse(request, "models/_test_result.html", {"result": result, "error": error})
