import re
import secrets
import time
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError
from sqlmodel import Session

from app.db import get_session
from app.models import Era, TopicType
from app.schemas.topics import CountryData
from app.services import topic_io
from app.services import topics as svc
from app.web.forms import FIELD_NAMES, PromptRow, SourceRow, TopicForm, friendly_errors
from app.web.markdown import render_markdown
from app.web.templates import is_htmx, render, templates

router = APIRouter()

NOTICES = {
    "created": "Topic created.",
    "saved": "Topic saved.",
    "archived": "Topic archived. It no longer shows in the list or the run wizard; past runs keep it.",
    "archived_instead": "This topic is used by a run, so it was archived instead of deleted.",
    "restored": "Topic restored.",
    "deleted": "Topic deleted.",
}


def _form_page(
    request: Request,
    session: Session,
    form: TopicForm,
    *,
    topic_id: int | None = None,
    errors: list[str] | None = None,
    country_error: str | None = None,
):
    return render(
        request,
        "topics/form.html",
        active_nav="topics",
        page_title=f"Edit topic: {form.title}" if topic_id else "New topic",
        form=form,
        topic_id=topic_id,
        errors=errors or [],
        country_error=country_error,
        countries=svc.list_countries(session),
        eras=list(Era),
        types=list(TopicType),
        status_code=422 if errors else 200,
    )


def _get_topic_or_404(session: Session, topic_id: int):
    topic = svc.get_topic(session, topic_id)
    if topic is None:
        raise HTTPException(404, "Topic not found.")
    return topic


# ── list ─────────────────────────────────────────────────────────────────────


@router.get("/topics", name="topics_list")
def topics_list(
    request: Request,
    q: str = "",
    country: str = "",
    era: str = "",
    type: str = "",
    archived: bool = False,
    notice: str = "",
    session: Session = Depends(get_session),
):
    filters = svc.TopicFilters(q=q, country=country, era=era, type=type, archived=archived)
    rows = svc.list_topics(session, filters)
    ctx = dict(rows=rows, filters=filters)
    if is_htmx(request):
        return templates.TemplateResponse(request, "topics/_table.html", ctx)
    return render(
        request,
        "topics/list.html",
        active_nav="topics",
        page_title="Topics",
        countries=svc.list_countries(session),
        eras=list(Era),
        types=list(TopicType),
        notice=NOTICES.get(notice),
        **ctx,
    )


# ── form helpers (HTMX) ──────────────────────────────────────────────────────


@router.get("/topics/_rows/source", name="topic_source_row")
def topic_source_row(request: Request):
    return templates.TemplateResponse(
        request, "topics/_source_row.html", {"source": SourceRow(), "removable": True}
    )


@router.get("/topics/_rows/prompt", name="topic_prompt_row")
def topic_prompt_row(request: Request):
    return templates.TemplateResponse(
        request,
        "topics/_prompt_row.html",
        {"prompt": PromptRow(language=""), "is_default": False, "removable": True},
    )


@router.post("/topics/_preview", name="topic_context_preview")
async def topic_context_preview(request: Request):
    form = await request.form()
    context = form.get("context")
    html = render_markdown(context if isinstance(context, str) else "")
    return Response(html or '<p class="empty-state">Nothing to preview.</p>', media_type="text/html")


@router.post("/topics/_country", name="topic_add_country")
async def topic_add_country(request: Request, session: Session = Depends(get_session)):
    """Inline "+ Add country" in the topic form. Returns the re-rendered country fields."""
    form = await request.form()
    topic_form = TopicForm.from_formdata(form)
    error = None
    try:
        data = CountryData(
            code=str(form.get("new_country_code") or ""),
            name=str(form.get("new_country_name") or ""),
            native_language=str(form.get("new_country_language") or ""),
        )
        svc.create_country(session, data)
        if not topic_form.country_1:
            topic_form.country_1 = data.code
        elif topic_form.type == TopicType.CRISS_CROSS and not topic_form.country_2:
            topic_form.country_2 = data.code
    except svc.TopicServiceError as e:
        error = str(e)
    except ValidationError as e:
        error = "; ".join(friendly_errors(e))
    return templates.TemplateResponse(
        request,
        "topics/_country_fields.html",
        {
            "form": topic_form,
            "countries": svc.list_countries(session),
            "country_error": error,
            "new_country": {} if error is None else dict(form),
        },
    )


# ── create / edit ────────────────────────────────────────────────────────────


@router.get("/topics/new", name="topic_new")
def topic_new(request: Request, session: Session = Depends(get_session)):
    return _form_page(request, session, TopicForm())


@router.post("/topics/new", name="topic_create")
async def topic_create(request: Request, session: Session = Depends(get_session)):
    form = TopicForm.from_formdata(await request.form())
    data, errors = form.to_data()
    if data is not None:
        try:
            topic = svc.create_topic(session, data)
        except svc.TopicServiceError as e:
            errors = [str(e)]
        else:
            return RedirectResponse(f"/topics/{topic.id}?notice=created", status_code=303)
    return _form_page(request, session, form, errors=errors)


@router.get("/topics/{topic_id:int}", name="topic_detail")
def topic_detail(
    request: Request, topic_id: int, notice: str = "", session: Session = Depends(get_session)
):
    topic = _get_topic_or_404(session, topic_id)
    return render(
        request,
        "topics/detail.html",
        active_nav="topics",
        page_title=topic.title,
        topic=topic,
        data=svc.topic_data(session, topic),
        countries={c.code: c for c in svc.list_countries(session)},
        runs=svc.topic_runs(session, topic),
        used_by_runs=svc.is_used_by_runs(session, topic),
        notice=NOTICES.get(notice),
    )


@router.get("/topics/{topic_id:int}/edit", name="topic_edit")
def topic_edit(request: Request, topic_id: int, session: Session = Depends(get_session)):
    topic = _get_topic_or_404(session, topic_id)
    form = TopicForm.from_data(svc.topic_data(session, topic))
    return _form_page(request, session, form, topic_id=topic.id)


@router.post("/topics/{topic_id:int}/edit", name="topic_update")
async def topic_update(request: Request, topic_id: int, session: Session = Depends(get_session)):
    topic = _get_topic_or_404(session, topic_id)
    form = TopicForm.from_formdata(await request.form())
    form.slug = topic.slug
    data, errors = form.to_data()
    if data is not None:
        try:
            svc.update_topic(session, topic, data)
        except svc.TopicServiceError as e:
            errors = [str(e)]
        else:
            return RedirectResponse(f"/topics/{topic.id}?notice=saved", status_code=303)
    return _form_page(request, session, form, topic_id=topic.id, errors=errors)


@router.post("/topics/{topic_id:int}/archive", name="topic_archive")
def topic_archive(topic_id: int, session: Session = Depends(get_session)):
    topic = _get_topic_or_404(session, topic_id)
    svc.set_archived(session, topic, True)
    return RedirectResponse(f"/topics/{topic.id}?notice=archived", status_code=303)


@router.post("/topics/{topic_id:int}/restore", name="topic_restore")
def topic_restore(topic_id: int, session: Session = Depends(get_session)):
    topic = _get_topic_or_404(session, topic_id)
    svc.set_archived(session, topic, False)
    return RedirectResponse(f"/topics/{topic.id}?notice=restored", status_code=303)


@router.post("/topics/{topic_id:int}/delete", name="topic_delete")
def topic_delete(topic_id: int, session: Session = Depends(get_session)):
    topic = _get_topic_or_404(session, topic_id)
    if svc.delete_topic(session, topic) == "archived":
        return RedirectResponse(f"/topics/{topic_id}?notice=archived_instead", status_code=303)
    return RedirectResponse("/topics?notice=deleted", status_code=303)


# ── export / import ──────────────────────────────────────────────────────────


@router.get("/topics/export", name="topics_export")
def topics_export(request: Request, session: Session = Depends(get_session)):
    raw_ids = request.query_params.getlist("ids")
    ids = [int(i) for i in raw_ids if i.isdigit()] if raw_ids else None
    content = topic_io.export_topics(session, ids)
    name = f"histbias-topics-{date.today():%Y%m%d}.yaml"
    return Response(
        content,
        media_type="application/x-yaml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_IMPORT_TTL_S = 24 * 3600


def _import_dir(request: Request) -> Path:
    d = request.app.state.settings.data_dir / "imports"
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in d.glob("*.yaml"):  # prune abandoned uploads
        if now - old.stat().st_mtime > _IMPORT_TTL_S:
            old.unlink(missing_ok=True)
    return d


def _import_file(request: Request, token: str) -> Path:
    if not _TOKEN_RE.match(token):
        raise HTTPException(400, "Invalid import token.")
    path = _import_dir(request) / f"{token}.yaml"
    if not path.exists():
        raise HTTPException(410, "This import has expired. Upload the file again.")
    return path


@router.get("/topics/import", name="topics_import")
def topics_import(request: Request):
    return render(request, "topics/import.html", active_nav="topics", page_title="Import topics")


@router.post("/topics/import", name="topics_import_upload")
async def topics_import_upload(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    upload = form.get("file")
    error = None
    if upload is None or isinstance(upload, str) or not upload.filename:
        error = "Choose a YAML file to import."
    else:
        content = await upload.read(topic_io.MAX_IMPORT_BYTES + 1)
        try:
            parsed = topic_io.parse_topic_file(content)
        except topic_io.ImportFormatError as e:
            error = str(e)
    if error:
        return render(
            request,
            "topics/import.html",
            active_nav="topics",
            page_title="Import topics",
            error=error,
            status_code=422,
        )
    preview = topic_io.preview_import(session, parsed)
    token = None
    if preview.ok:
        token = secrets.token_hex(16)
        (_import_dir(request) / f"{token}.yaml").write_bytes(content)
    return render(
        request,
        "topics/import_preview.html",
        active_nav="topics",
        page_title="Import topics: preview",
        preview=preview,
        token=token,
        filename=upload.filename,
        Status=topic_io.Status,
        field_names=FIELD_NAMES,
        status_code=200 if preview.ok else 422,
    )


@router.post("/topics/import/confirm", name="topics_import_confirm")
async def topics_import_confirm(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    path = _import_file(request, str(form.get("token") or ""))
    try:
        mode = topic_io.ImportMode(str(form.get("mode") or "skip"))
    except ValueError:
        raise HTTPException(400, "Unknown import mode.") from None
    parsed = topic_io.parse_topic_file(path.read_bytes())
    try:
        result = topic_io.apply_import(session, parsed, mode)
    except (topic_io.ImportFormatError, svc.TopicServiceError) as e:
        return render(
            request,
            "topics/import.html",
            active_nav="topics",
            page_title="Import topics",
            error=str(e),
            status_code=422,
        )
    path.unlink(missing_ok=True)
    return render(
        request,
        "topics/import_result.html",
        active_nav="topics",
        page_title="Import finished",
        result=result,
        mode=mode,
    )
