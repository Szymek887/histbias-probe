from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlmodel import Session

from app.db import get_session
from app.schemas.topics import CountryData
from app.services import topics as svc
from app.web.forms import friendly_errors
from app.web.templates import render

router = APIRouter()


def _page(request: Request, session: Session, *, errors=None, values=None, notice=None):
    return render(
        request,
        "countries/list.html",
        active_nav="topics",
        page_title="Countries",
        countries=svc.list_countries(session),
        topic_counts=svc.country_topic_counts(session),
        errors=errors or [],
        values=values or {},
        notice=notice,
        status_code=422 if errors else 200,
    )


@router.get("/countries", name="countries_list")
def countries_list(request: Request, notice: str = "", session: Session = Depends(get_session)):
    return _page(request, session, notice={"added": "Country added.", "saved": "Country saved."}.get(notice))


@router.post("/countries", name="country_create")
async def country_create(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    values = {k: str(form.get(k) or "") for k in ("code", "name", "native_language")}
    try:
        svc.create_country(session, CountryData(**values))
    except ValidationError as e:
        return _page(request, session, errors=friendly_errors(e), values=values)
    except svc.TopicServiceError as e:
        return _page(request, session, errors=[str(e)], values=values)
    return RedirectResponse("/countries?notice=added", status_code=303)


@router.get("/countries/{code}/edit", name="country_edit")
def country_edit(request: Request, code: str, session: Session = Depends(get_session)):
    country = next((c for c in svc.list_countries(session) if c.code == code), None)
    if country is None:
        raise HTTPException(404, "Country not found.")
    return render(
        request,
        "countries/edit.html",
        active_nav="topics",
        page_title=f"Edit country {code}",
        country=country,
        values={"name": country.name, "native_language": country.native_language},
        errors=[],
    )


@router.post("/countries/{code}/edit", name="country_update")
async def country_update(request: Request, code: str, session: Session = Depends(get_session)):
    form = await request.form()
    values = {k: str(form.get(k) or "") for k in ("name", "native_language")}
    errors: list[str] = []
    try:
        svc.update_country(session, code, CountryData(code=code, **values))
    except ValidationError as e:
        errors = friendly_errors(e)
    except svc.TopicServiceError as e:
        errors = [str(e)]
    if not errors:
        return RedirectResponse("/countries?notice=saved", status_code=303)
    return render(
        request,
        "countries/edit.html",
        active_nav="topics",
        page_title=f"Edit country {code}",
        country={"code": code},
        values=values,
        errors=errors,
        status_code=422,
    )
