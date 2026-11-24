from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.crypto import Cipher
from app.db import get_session
from app.services import credentials as creds
from app.services import providers as provider_svc
from app.web.deps import cipher_error, get_cipher
from app.web.templates import render, templates

router = APIRouter()

NOTICES = {
    "saved": "Key saved (encrypted). The input was cleared.",
    "deleted": "Key deleted. Past runs are not affected.",
}


def _provider_or_404(session: Session, provider_id: int):
    provider = provider_svc.get_provider(session, provider_id)
    if provider is None:
        raise HTTPException(404, "Provider not found.")
    return provider


@router.get("/settings", name="settings_index")
def settings_index():
    return RedirectResponse("/settings/keys", status_code=303)


@router.get("/settings/keys", name="settings_keys")
def settings_keys(
    request: Request,
    notice: str = "",
    session: Session = Depends(get_session),
    cipher: Cipher | None = Depends(get_cipher),
):
    statuses = creds.list_key_statuses(session, cipher)
    return render(
        request,
        "settings/keys.html",
        active_nav="settings",
        page_title="API keys",
        statuses=statuses,
        any_broken=any(s.broken for s in statuses),
        cipher_error=cipher_error(request),
        data_dir=request.app.state.settings.data_dir,
        notice=NOTICES.get(notice),
    )


@router.get("/settings/keys/{provider_id:int}", name="settings_key_form")
def settings_key_form(
    request: Request,
    provider_id: int,
    session: Session = Depends(get_session),
    cipher: Cipher | None = Depends(get_cipher),
):
    provider = _provider_or_404(session, provider_id)
    return render(
        request,
        "settings/key_form.html",
        active_nav="settings",
        page_title=f"API key: {provider.name}",
        status=creds.key_status(session, cipher, provider),
        extra_fields=creds.secret_extra_fields(provider),
        errors=[],
    )


@router.post("/settings/keys/{provider_id:int}", name="settings_key_save")
async def settings_key_save(
    request: Request,
    provider_id: int,
    session: Session = Depends(get_session),
    cipher: Cipher | None = Depends(get_cipher),
):
    provider = _provider_or_404(session, provider_id)
    form = await request.form()
    fields = creds.secret_extra_fields(provider)
    errors: list[str] = []
    if cipher is None:
        errors = [f"Keys can't be stored: {cipher_error(request)}"]
    else:
        try:
            creds.save_credential(
                session,
                cipher,
                provider,
                str(form.get("api_key") or ""),
                {f: str(form.get(f"extra_{f}") or "") for f in fields},
            )
        except creds.CredentialError as e:
            errors = [str(e)]
    if not errors:
        return RedirectResponse("/settings/keys?notice=saved", status_code=303)
    # Re-render WITHOUT the submitted key: the browser never gets it back.
    return render(
        request,
        "settings/key_form.html",
        active_nav="settings",
        page_title=f"API key: {provider.name}",
        status=creds.key_status(session, cipher, provider),
        extra_fields=fields,
        errors=errors,
        status_code=422,
    )


@router.post("/settings/keys/{provider_id:int}/delete", name="settings_key_delete")
def settings_key_delete(provider_id: int, session: Session = Depends(get_session)):
    creds.delete_credential(session, _provider_or_404(session, provider_id))
    return RedirectResponse("/settings/keys?notice=deleted", status_code=303)


@router.post("/settings/keys/{provider_id:int}/test", name="settings_key_test")
async def settings_key_test(
    request: Request,
    provider_id: int,
    session: Session = Depends(get_session),
    cipher: Cipher | None = Depends(get_cipher),
):
    provider = _provider_or_404(session, provider_id)
    first_model = next(
        (m.model.model_id for m in provider_svc.list_models(session, cipher) if m.provider.id == provider.id and m.model.model_id),
        None,
    )
    ok, message = await creds.test_provider(session, cipher, provider, first_model)
    return templates.TemplateResponse(
        request, "settings/_test_result.html", {"ok": ok, "message": message}
    )
