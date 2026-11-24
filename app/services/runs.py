"""Runs: listing, progress, and the pause / resume / cancel / retry / delete actions.
Planning lives in `app/engine/planner.py`, execution in `app/engine/executor.py`."""

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import update
from sqlmodel import Session, col, func, select

from app.models import Judgement, Response, Run, RunItem, RunItemStatus, RunStatus, Topic
from app.models.base import utcnow

ACTIVE = (RunStatus.QUEUED, RunStatus.RUNNING)
DONE_ITEM = (RunItemStatus.ANSWERED, RunItemStatus.JUDGED, RunItemStatus.JUDGE_FAILED)
FINAL_ITEM = (RunItemStatus.JUDGED, RunItemStatus.JUDGE_FAILED, RunItemStatus.ERROR, RunItemStatus.SKIPPED)


class RunActionError(ValueError):
    """User-facing: the action isn't possible in the run's current state."""


@dataclass
class Progress:
    counts: Counter = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def answered(self) -> int:
        return sum(self.counts[s] for s in DONE_ITEM)

    @property
    def errors(self) -> int:
        return self.counts[RunItemStatus.ERROR]

    @property
    def judged(self) -> int:
        return self.counts[RunItemStatus.JUDGED]

    @property
    def judge_failed(self) -> int:
        return self.counts[RunItemStatus.JUDGE_FAILED]

    @property
    def failed(self) -> int:
        """Items "Retry failed" would re-queue."""
        return self.errors + self.judge_failed

    @property
    def skipped(self) -> int:
        return self.counts[RunItemStatus.SKIPPED]

    @property
    def running(self) -> int:
        return self.counts[RunItemStatus.RUNNING]

    @property
    def pending(self) -> int:
        return self.counts[RunItemStatus.PENDING]

    has_judge: bool = False

    @property
    def finished(self) -> int:
        """Items with nothing left to do (answered counts only when there is no judge)."""
        done = sum(self.counts[s] for s in FINAL_ITEM)
        return done if self.has_judge else done + self.counts[RunItemStatus.ANSWERED]

    @property
    def percent(self) -> int:
        return int(100 * self.finished / self.total) if self.total else 0


def progress(session: Session, run_id: int) -> Progress:
    rows = session.exec(
        select(RunItem.status, func.count()).where(RunItem.run_id == run_id).group_by(RunItem.status)
    ).all()
    run = session.get(Run, run_id)
    return Progress(Counter(dict(rows)), has_judge=bool(run and run.judge_model_id))


@dataclass
class RunRow:
    run: Run
    progress: Progress

    @property
    def model_count(self) -> int:
        cfg = self.run.config
        return len(cfg.get("models") or {}) or len(cfg.get("settings", {}).get("model_ids", []))

    @property
    def topic_count(self) -> int:
        cfg = self.run.config
        return len(cfg.get("topics") or {}) or len(cfg.get("settings", {}).get("topic_ids", []))


def list_runs(session: Session) -> list[RunRow]:
    runs = session.exec(select(Run).order_by(col(Run.created_at).desc(), col(Run.id).desc())).all()
    counts: dict[int, Counter] = {r.id: Counter() for r in runs}
    for run_id, status, n in session.exec(
        select(RunItem.run_id, RunItem.status, func.count()).group_by(RunItem.run_id, RunItem.status)
    ):
        if run_id in counts:
            counts[run_id][status] = n
    return [RunRow(r, Progress(counts[r.id], has_judge=bool(r.judge_model_id))) for r in runs]


def get_run(session: Session, run_id: int) -> Run | None:
    return session.get(Run, run_id)


@dataclass
class ItemRow:
    item: RunItem
    topic_title: str
    model_name: str  # "A" or "A × B"
    responses: list[Response]  # ordered A1, B1, A2, B2
    judgements: dict[int, Judgement] = field(default_factory=dict)  # response id -> the run judge's verdict

    @property
    def verdicts(self) -> list[Judgement | None]:
        return [self.judgements.get(r.id) for r in self.responses]

    @property
    def expected(self) -> int:
        return 4 if self.item.model_b_id is not None else 1

    @property
    def first(self) -> Response | None:
        return self.responses[0] if self.responses else None

    @property
    def refusals(self) -> int:
        return sum(1 for r in self.responses if r.refusal_detected)


def _order(item: RunItem, responses: list[Response]) -> list[Response]:
    return sorted(responses, key=lambda r: (r.turn, r.llm_model_id != item.model_a_id))


def list_items(session: Session, run: Run) -> list[ItemRow]:
    models = run.config.get("models") or {}

    def name(model_pk: int | None) -> str:
        return (models.get(str(model_pk)) or {}).get("display_name", f"model {model_pk}")

    titles = {t.id: t.title for t in session.exec(
        select(Topic).where(col(Topic.id).in_(select(RunItem.topic_id).where(RunItem.run_id == run.id)))
    )}
    responses: dict[int, list[Response]] = {}
    for r in session.exec(
        select(Response).join(RunItem, RunItem.id == Response.run_item_id).where(RunItem.run_id == run.id)
    ):
        responses.setdefault(r.run_item_id, []).append(r)
    judgements = judgements_for(session, run, [r.id for rs in responses.values() for r in rs])
    items = session.exec(select(RunItem).where(RunItem.run_id == run.id).order_by(RunItem.id)).all()
    return [
        ItemRow(
            item=i,
            topic_title=titles.get(i.topic_id, f"topic {i.topic_id}"),
            model_name=name(i.model_a_id) + (f" × {name(i.model_b_id)}" if i.model_b_id is not None else ""),
            responses=_order(i, responses.get(i.id, [])),
            judgements=judgements,
        )
        for i in items
    ]


def judgements_for(session: Session, run: Run, response_ids: list[int]) -> dict[int, Judgement]:
    """The run judge's current-rubric judgement per response."""
    if not run.judge_model_id or not response_ids:
        return {}
    rubric = (run.config.get("judge") or {}).get("rubric_version")
    stmt = select(Judgement).where(
        col(Judgement.response_id).in_(response_ids), Judgement.judge_model_id == run.judge_model_id
    )
    if rubric:
        stmt = stmt.where(Judgement.rubric_version == rubric)
    return {j.response_id: j for j in session.exec(stmt)}


def item_responses(session: Session, item: RunItem) -> list[Response]:
    """All responses of an item, ordered A1, B1, A2, B2."""
    return _order(item, list(session.exec(select(Response).where(Response.run_item_id == item.id))))


# ── actions ──────────────────────────────────────────────────────────────────


def _set_status(session: Session, run: Run, status: RunStatus, allowed: tuple[str, ...], what: str) -> Run:
    result = session.exec(
        update(Run)
        .where(Run.id == run.id, col(Run.status).in_(allowed))
        .values(status=status, updated_at=utcnow())
    )
    session.commit()
    session.refresh(run)
    if not result.rowcount:
        raise RunActionError(f"can't {what} a run that is {run.status}")
    return run


def pause(session: Session, run: Run) -> Run:
    """Stops new calls; calls in flight finish and are stored."""
    return _set_status(session, run, RunStatus.PAUSED, ACTIVE, "pause")


def resume(session: Session, run: Run) -> Run:
    return _set_status(session, run, RunStatus.QUEUED, (RunStatus.PAUSED,), "resume")


def cancel(session: Session, run: Run) -> Run:
    """Pending items become `skipped`. Calls in flight finish and are stored."""
    _set_status(session, run, RunStatus.CANCELLED, (*ACTIVE, RunStatus.PAUSED), "cancel")
    session.exec(
        update(RunItem)
        .where(RunItem.run_id == run.id, col(RunItem.status) == RunItemStatus.PENDING)
        .values(status=RunItemStatus.SKIPPED, updated_at=utcnow())
    )
    run.finished_at = utcnow()
    session.add(run)
    session.commit()
    return run


def retry_failed(session: Session, run: Run) -> int:
    """Failed items go back to the queue with a fresh attempt budget; items whose judging
    failed go back to the judge (only the failed judgements are redone). The run is
    re-queued. Returns the number of items re-queued."""
    if run.status == RunStatus.DRAFT:
        raise RunActionError("a draft run has no items yet")
    result = session.exec(
        update(RunItem)
        .where(RunItem.run_id == run.id, col(RunItem.status) == RunItemStatus.ERROR)
        .values(status=RunItemStatus.PENDING, error=None, attempts=0, updated_at=utcnow())
    )
    rejudge = session.exec(
        update(RunItem)
        .where(RunItem.run_id == run.id, col(RunItem.status) == RunItemStatus.JUDGE_FAILED)
        .values(status=RunItemStatus.ANSWERED, error=None, updated_at=utcnow())
    )
    count = result.rowcount + rejudge.rowcount
    if count and run.status != RunStatus.PAUSED:
        run.status = RunStatus.QUEUED
        run.finished_at = None
        run.updated_at = utcnow()
        session.add(run)
    session.commit()
    return count


def delete_run(session: Session, run: Run) -> None:
    if run.status in ACTIVE:
        raise RunActionError("pause or cancel the run before deleting it")
    if progress(session, run.id).running:
        raise RunActionError("some calls are still in flight; try again in a moment")
    session.delete(run)  # items, responses and judgements go with it (ON DELETE CASCADE)
    session.commit()


def get_response(session: Session, response_id: int) -> Response | None:
    return session.get(Response, response_id)
