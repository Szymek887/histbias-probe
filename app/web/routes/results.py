from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response as HttpResponse
from sqlmodel import Session

from app.db import get_session
from app.models import RunStatus
from app.services import export
from app.services import results as svc
from app.services import runs as run_svc
from app.services import study as study_svc
from app.web.templates import is_htmx, render, templates

router = APIRouter()

SCORECARD_COLUMNS = [
    ("name", "Model"), ("mean", "Mean"), ("hard", "Hard"), ("soft", "Soft"),
    ("refusal_heuristic", "Refusal (heur.)"), ("refusal_judge", "Refusal (judge)"),
    ("disagreements", "Heur. ≠ judge"),
    ("past", "Past"), ("20th_century", "20th c."), ("present", "Present"),
    ("internal", "Internal"), ("criss_cross", "Criss-cross"),
    ("home", "Home"), ("foreign", "Foreign"), ("depth", "Depth"), ("balance", "Balance"),
]  # fmt: skip


def _run_or_404(session: Session, run_id: int):
    run = run_svc.get_run(session, run_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    if run.status == RunStatus.DRAFT:
        raise HTTPException(409, "A draft run has no results yet.")
    return run


@router.get("/runs/{run_id:int}/results", name="run_results")
def run_results(
    request: Request, run_id: int, country: str = "", era: str = "", mode: str = "",
    sort: str = "mean", dir: str = "desc", session: Session = Depends(get_session),
):
    run = _run_or_404(session, run_id)
    filters = svc.Filters(country=country, era=era, mode=mode)
    all_rows = svc.load_rows(session, run)
    rows = svc.apply_filters(all_rows, filters)
    desc = dir != "asc"
    scores = svc.scorecard(rows, sort=sort, desc=desc)
    context = {
        "run": run,
        "rows": rows,
        "filters": filters,
        "options": svc.filter_options(all_rows),
        "scores": scores,
        "heat": svc.heatmap(rows, [s.model_pk for s in sorted(scores, key=lambda s: s.name.lower())]),
        "columns": SCORECARD_COLUMNS,
        "sort": sort if sort in svc.SORT_KEYS else "mean",
        "desc": desc,
        "judged": sum(r.judged for r in rows),
        "has_judge": bool(run.judge_model_id),
        "study": study_svc.compare(scores, study_svc.model_ids_of(run.config or {})),
    }
    if is_htmx(request):
        return templates.TemplateResponse(request, "results/_body.html", context)
    return render(request, "results/page.html", active_nav="runs", page_title=f"Results: {run.name}", **context)


def _download(content: str, media_type: str, name: str) -> HttpResponse:
    return HttpResponse(
        content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/runs/{run_id:int}/export.csv", name="run_export_csv")
def run_export_csv(run_id: int, session: Session = Depends(get_session)):
    run = _run_or_404(session, run_id)
    return _download(export.to_csv(session, run), "text/csv; charset=utf-8", export.filename(run, "csv"))


@router.get("/runs/{run_id:int}/export.json", name="run_export_json")
def run_export_json(run_id: int, session: Session = Depends(get_session)):
    run = _run_or_404(session, run_id)
    return _download(export.to_json_text(session, run), "application/json", export.filename(run, "json"))
