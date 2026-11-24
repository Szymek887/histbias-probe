import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlmodel import Session

from app.crypto import Cipher
from app.db import get_session
from app.engine import planner, refusal
from app.models import ProviderKind, RunItem, RunStatus, TopicType
from app.schemas.runs import INTERNAL_MODEL_CHOICES, LANGUAGE_CHOICES, PAIRING_CHOICES, RunSettings
from app.services import credentials
from app.services import providers as provider_svc
from app.services import results as results_svc
from app.services import runs as svc
from app.services import topics as topic_svc
from app.web.deps import get_cipher
from app.web.forms import friendly_errors
from app.web.templates import render, templates

router = APIRouter()

NOTICES = {
    "draft": "Draft saved. Start it when you're ready.",
    "started": "Run started.",
    "paused": "Run paused. Calls already in flight will finish.",
    "resumed": "Run resumed.",
    "cancelled": "Run cancelled. Calls already in flight will finish and be kept.",
    "retried": "Failed items are back in the queue.",
    "nothing_to_retry": "No failed items to retry.",
    "deleted": "Run deleted.",
}
LIVE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.PAUSED)


def _wake(request: Request) -> None:
    executor = getattr(request.app.state, "executor", None)
    if executor is not None:
        executor.wake()


def _run_or_404(session: Session, run_id: int):
    run = svc.get_run(session, run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    return run


# ── list ─────────────────────────────────────────────────────────────────────


@router.get("/", name="runs_list")
def runs_list(
    request: Request, notice: str = "", session: Session = Depends(get_session),
    cipher: Cipher | None = Depends(get_cipher),
):
    has_real_key = any(
        s.usable and s.provider.kind != ProviderKind.MOCK and s.source != "not_needed"
        for s in credentials.list_key_statuses(session, cipher)
    )
    return render(
        request,
        "runs/list.html",
        active_nav="runs",
        page_title="Runs",
        has_real_key=has_real_key,
        rows=svc.list_runs(session),
        notice=NOTICES.get(notice),
    )


# ── wizard ───────────────────────────────────────────────────────────────────


def _default_values(session: Session, cipher: Cipher | None, topic_ids: list[int], concurrency: int) -> dict:
    usable = [r.model.id for r in provider_svc.list_models(session, cipher) if r.model.enabled and r.usable]
    return {
        "name": f"Run {datetime.now():%Y-%m-%d %H:%M}",
        "topic_ids": set(topic_ids),
        "model_ids": set(usable),
        "languages": {"en"},
        "internal_models": "all",
        "pairing": "auto",
        "pairs": [],
        "judge_model_id": "",
        "repetitions": "1",
        "temperature_mode": "model",
        "temperature": "",
        "concurrency": str(concurrency),
        "draft_id": "",
    }


def _values_from_settings(s: RunSettings, draft_id: int | None = None) -> dict:
    return {
        "name": s.name,
        "topic_ids": set(s.topic_ids),
        "model_ids": set(s.model_ids),
        "languages": set(s.languages),
        "internal_models": s.internal_models,
        "pairing": s.pairing,
        "pairs": [tuple(p) for p in s.pairs],
        "judge_model_id": str(s.judge_model_id or ""),
        "repetitions": str(s.repetitions),
        "temperature_mode": "model" if s.temperature is None else "fixed",
        "temperature": "" if s.temperature is None else str(s.temperature),
        "concurrency": str(s.concurrency),
        "draft_id": str(draft_id or ""),
    }


def _ints(values: list) -> list[int]:
    return [int(v) for v in values if str(v).isdigit()]


def _pairs(form) -> list[tuple[int, int]]:
    """Manual pair rows (parallel `pair_a` / `pair_b` lists); incomplete rows are ignored."""
    return [
        (int(a), int(b))
        for a, b in zip(form.getlist("pair_a"), form.getlist("pair_b"), strict=False)
        if str(a).isdigit() and str(b).isdigit()
    ]


def _settings(values: dict, topic_ids: list[int], model_ids: list[int]) -> RunSettings:
    return RunSettings.model_validate(
        {
            "name": values["name"],
            "topic_ids": topic_ids,
            "model_ids": model_ids,
            "languages": sorted(values["languages"]),
            "internal_models": values["internal_models"],
            "pairing": values["pairing"],
            "pairs": values["pairs"],
            "judge_model_id": int(values["judge_model_id"]) if values["judge_model_id"].isdigit() else None,
            "repetitions": values["repetitions"],
            "temperature": values["temperature"] if values["temperature_mode"] == "fixed" else None,
            "concurrency": values["concurrency"],
        }
    )


async def _parse(request: Request) -> tuple[dict, RunSettings | None, list[str]]:
    form = await request.form()
    values = {
        "name": str(form.get("name") or ""),
        "topic_ids": set(_ints(form.getlist("topic_ids"))),
        "model_ids": set(_ints(form.getlist("model_ids"))),
        "languages": {str(v) for v in form.getlist("languages")},
        "internal_models": str(form.get("internal_models") or "all"),
        "pairing": str(form.get("pairing") or "auto"),
        "pairs": _pairs(form),
        "judge_model_id": str(form.get("judge_model_id") or ""),
        "repetitions": str(form.get("repetitions") or "1"),
        "temperature_mode": str(form.get("temperature_mode") or "model"),
        "temperature": str(form.get("temperature") or ""),
        "concurrency": str(form.get("concurrency") or "4"),
        "draft_id": str(form.get("draft_id") or ""),
    }
    errors = []
    if not values["topic_ids"]:
        errors.append("Choose at least one topic.")
    if not values["model_ids"]:
        errors.append("Choose at least one model.")
    if not values["languages"]:
        errors.append("Choose at least one language.")
    if values["temperature_mode"] == "fixed" and not values["temperature"]:
        errors.append("Enter the temperature, or choose “model default”.")
    if errors:
        return values, None, errors
    try:
        # Keep the order of the form (topics are listed by country and era).
        settings = _settings(values, _ints(form.getlist("topic_ids")), _ints(form.getlist("model_ids")))
        return values, settings, []
    except ValidationError as e:
        return values, None, friendly_errors(e)


def _wizard(
    request: Request, session: Session, cipher: Cipher | None, values: dict, *,
    errors: list[str] | None = None, status_code: int = 200, draft=None, duplicate_of=None,
):
    plan = None
    if values["topic_ids"] and values["model_ids"] and values["languages"]:
        try:
            settings = _settings(
                {**values, "name": values["name"] or "x", "temperature_mode": "model"},
                sorted(values["topic_ids"]), sorted(values["model_ids"]),
            )
            plan = planner.build_plan(session, cipher, settings)
        except ValidationError:
            plan = None
    model_rows = [r for r in provider_svc.list_models(session, cipher) if r.model.enabled]
    topic_rows = topic_svc.list_topics(session)
    return render(
        request,
        "runs/new.html",
        active_nav="new_run",
        page_title="Edit draft run" if draft else "New run",
        values=values,
        errors=errors or [],
        draft=draft,
        duplicate_of=duplicate_of,
        internal_topics=[r for r in topic_rows if r.topic.type == TopicType.INTERNAL],
        cc_topics=[r for r in topic_rows if r.topic.type == TopicType.CRISS_CROSS],
        model_rows=model_rows,
        pair_models=[r.model for r in model_rows if r.usable],
        judge_rows=model_rows,
        pairing_choices=PAIRING_CHOICES,
        language_choices=LANGUAGE_CHOICES,
        internal_model_choices=INTERNAL_MODEL_CHOICES,
        plan=plan,
        status_code=status_code,
    )


@router.get("/runs/new", name="run_new")
def run_new(request: Request, session: Session = Depends(get_session), cipher: Cipher | None = Depends(get_cipher)):
    ids = _ints(request.query_params.getlist("ids"))
    concurrency = request.app.state.settings.max_concurrency
    source_id = request.query_params.get("from_run", "")
    if source_id.isdigit():
        # "Duplicate run": the same settings as a new run, re-planned from the current
        # topics, models and keys when it starts.
        source = _run_or_404(session, int(source_id))
        values = _values_from_settings(planner.settings_of(source))
        values["name"] = f"{source.name} (copy)"[:200]
        return _wizard(request, session, cipher, values, duplicate_of=source)
    return _wizard(request, session, cipher, _default_values(session, cipher, ids, concurrency))


@router.get("/runs/new/pair-row", name="run_pair_row")
def run_pair_row(request: Request, session: Session = Depends(get_session), cipher: Cipher | None = Depends(get_cipher)):
    models = [r.model for r in provider_svc.list_models(session, cipher) if r.model.enabled and r.usable]
    return templates.TemplateResponse(request, "runs/_pair_row.html", {"pair_models": models, "pair": (None, None)})


@router.post("/runs/new/summary", name="run_summary")
async def run_summary(
    request: Request, session: Session = Depends(get_session), cipher: Cipher | None = Depends(get_cipher)
):
    values, settings, errors = await _parse(request)
    plan = planner.build_plan(session, cipher, settings) if settings else None
    return templates.TemplateResponse(
        request, "runs/_summary.html", {"plan": plan, "summary_errors": errors, "values": values}
    )


@router.post("/runs/new", name="run_create")
async def run_create(
    request: Request, session: Session = Depends(get_session), cipher: Cipher | None = Depends(get_cipher)
):
    values, settings, errors = await _parse(request)
    form = await request.form()
    action = str(form.get("action") or "start")
    draft = None
    if values["draft_id"].isdigit():
        draft = _run_or_404(session, int(values["draft_id"]))
    if settings is not None:
        try:
            if action == "draft":
                run = planner.save_draft(session, settings, draft)
                return RedirectResponse(f"/runs/{run.id}?notice=draft", status_code=303)
            run = planner.start_run(session, cipher, settings, draft)
        except planner.PlanError as e:
            errors = [str(e)[:1].upper() + str(e)[1:] + "."]
        else:
            _wake(request)
            return RedirectResponse(f"/runs/{run.id}?notice=started", status_code=303)
    return _wizard(request, session, cipher, values, errors=errors, status_code=422, draft=draft)


@router.get("/runs/{run_id:int}/edit", name="run_edit")
def run_edit(
    request: Request, run_id: int, session: Session = Depends(get_session),
    cipher: Cipher | None = Depends(get_cipher),
):
    run = _run_or_404(session, run_id)
    if run.status != RunStatus.DRAFT:
        raise HTTPException(409, "Only draft runs can be edited.")
    values = _values_from_settings(planner.settings_of(run), run.id)
    return _wizard(request, session, cipher, values, draft=run)


# ── detail and actions ───────────────────────────────────────────────────────


def _live_context(session: Session, run) -> dict:
    return {
        "run": run,
        "progress": svc.progress(session, run.id),
        "items": svc.list_items(session, run),
        "live": run.status in LIVE_STATUSES,
        "polling": run.status in (RunStatus.QUEUED, RunStatus.RUNNING),
    }


@router.get("/runs/{run_id:int}", name="run_detail")
def run_detail(
    request: Request, run_id: int, notice: str = "", error: str = "",
    session: Session = Depends(get_session), cipher: Cipher | None = Depends(get_cipher),
):
    run = _run_or_404(session, run_id)
    plan = None
    if run.status == RunStatus.DRAFT:
        plan = planner.build_plan(session, cipher, planner.settings_of(run))
    return render(
        request,
        "runs/detail.html",
        active_nav="runs",
        page_title=run.name,
        notice=NOTICES.get(notice),
        error=error[:300],
        plan=plan,
        **_live_context(session, run),
    )


@router.get("/runs/{run_id:int}/progress", name="run_progress")
def run_progress(request: Request, run_id: int, session: Session = Depends(get_session)):
    run = _run_or_404(session, run_id)
    # Once the run stops, the returned element has no `every 2s` trigger, so polling ends.
    return templates.TemplateResponse(request, "runs/_live.html", _live_context(session, run))


def _after(run_id: int, notice: str | None = None, error: str | None = None) -> RedirectResponse:
    from urllib.parse import urlencode

    query = urlencode({k: v for k, v in (("notice", notice), ("error", error)) if v})
    return RedirectResponse(f"/runs/{run_id}" + (f"?{query}" if query else ""), status_code=303)


@router.post("/runs/{run_id:int}/start", name="run_start")
def run_start(
    request: Request, run_id: int, session: Session = Depends(get_session),
    cipher: Cipher | None = Depends(get_cipher),
):
    run = _run_or_404(session, run_id)
    try:
        planner.start_run(session, cipher, planner.settings_of(run), run)
    except planner.PlanError as e:
        return _after(run_id, error=str(e))
    _wake(request)
    return _after(run_id, "started")


@router.post("/runs/{run_id:int}/{action}", name="run_action")
def run_action(request: Request, run_id: int, action: str, session: Session = Depends(get_session)):
    run = _run_or_404(session, run_id)
    try:
        if action == "pause":
            svc.pause(session, run)
            return _after(run_id, "paused")
        if action == "resume":
            svc.resume(session, run)
            _wake(request)
            return _after(run_id, "resumed")
        if action == "cancel":
            svc.cancel(session, run)
            return _after(run_id, "cancelled")
        if action == "retry":
            n = svc.retry_failed(session, run)
            _wake(request)
            return _after(run_id, "retried" if n else "nothing_to_retry")
        if action == "delete":
            svc.delete_run(session, run)
            return RedirectResponse("/?notice=deleted", status_code=303)
    except svc.RunActionError as e:
        return _after(run_id, error=str(e))
    raise HTTPException(404, "Unknown action.")


# ── response ─────────────────────────────────────────────────────────────────


@router.get("/responses/{response_id:int}", name="response_detail")
def response_detail(request: Request, response_id: int, session: Session = Depends(get_session)):
    response = svc.get_response(session, response_id)
    if response is None:
        raise HTTPException(404, "Response not found.")
    item = session.get(RunItem, response.run_item_id)
    run = svc.get_run(session, item.run_id)
    topic = topic_svc.get_topic(session, item.topic_id)
    check = refusal.check(response.text, response.finish_reason, blocked=bool(response.error) and not response.text)
    item_response_ids = [r.id for r in svc.item_responses(session, item)]
    cell_verdicts = svc.judgements_for(session, run, item_response_ids)
    verdict = cell_verdicts.get(response.id)
    judge_messages = []
    if verdict is not None:
        try:
            judge_messages = json.loads(verdict.judge_prompt)
        except (json.JSONDecodeError, TypeError):
            judge_messages = [{"role": "prompt", "content": verdict.judge_prompt}]
    grid = None
    if item.model_b_id is not None:
        # Criss-cross: the 2×2 grid A1 B1 / A2 B2.
        names = {k: v.get("display_name", "") for k, v in (run.config.get("models") or {}).items()}
        by_key = {(r.llm_model_id, r.turn): r for r in svc.item_responses(session, item)}
        grid = [
            [
                {
                    "label": f"{side}{turn}",
                    "name": names.get(str(pk), f"model {pk}"),
                    "turn": turn,
                    "response": by_key.get((pk, turn)),
                }
                for side, pk in (("A", item.model_a_id), ("B", item.model_b_id))
            ]
            for turn in (1, 2)
        ]
    return render(
        request,
        "runs/response.html",
        active_nav="runs",
        page_title=f"{response.display_name_snapshot} on {topic.title if topic else 'topic'}",
        response=response,
        item=item,
        run=run,
        topic=topic,
        refusal_check=check,
        grid=grid,
        compare=results_svc.comparable(results_svc.load_rows(session, run), response.id)[1],
        verdict=verdict,
        cell_verdicts=cell_verdicts,
        judge_messages=judge_messages,
        # Autoescaped as text (not |tojson, which would turn non-Latin answers into \u escapes).
        raw_json=json.dumps(response.raw, ensure_ascii=False, indent=2),
    )
