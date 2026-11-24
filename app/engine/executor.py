"""Run executor: one asyncio background worker (docs/02-architecture.md, "Run engine").

- All state lives in the DB. On start, items left `running` by a killed process go back
  to `pending`, so runs continue after a restart.
- Items: internal = 1 call; criss-cross = turn 1 (A1 ∥ B1), then turn 2 (A2 ∥ B2), see
  `app/engine/crisscross.py`. Each answer is stored as soon as it arrives, so a retry or a
  restart repeats only the calls that are missing.
- Limits (counted in parallel calls): a global one (`HISTBIAS_MAX_CONCURRENCY`), one per run (`settings.concurrency`),
  and one per provider (`HISTBIAS_PROVIDER_CONCURRENCY`, or provider extra `max_concurrency`).
- Rate limits and transient errors are retried with exponential backoff (honouring
  `Retry-After`), up to `max_attempts` calls per item. While an item waits it is `pending`
  and holds no slot.
- Pause and cancel are run status changes; the worker checks them before every call.
  Calls already in flight finish and are stored.
- Claims and run completion are conditional UPDATEs, so they can't race the web requests
  that pause, cancel or retry.
"""

import asyncio
import logging
import random
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, delete, exists, func, update
from sqlmodel import Session, col, select

from app.config import Settings
from app.crypto import Cipher, DecryptionError
from app.engine import crisscross, refusal
from app.engine import judge as judging
from app.models import (
    Judgement,
    LlmModel,
    Provider,
    Response,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    Topic,
    TopicCountry,
)
from app.models.base import utcnow
from app.providers.base import ChatMessage, ChatRequest, ProviderError, ProviderRefused, ProviderRateLimit
from app.schemas.judge import JudgeVerdict
from app.services import credentials

log = logging.getLogger(__name__)

ACTIVE_RUN = (RunStatus.QUEUED, RunStatus.RUNNING)
OPEN_ITEM = (RunItemStatus.PENDING, RunItemStatus.RUNNING)
ANSWERED = (RunItemStatus.ANSWERED, RunItemStatus.JUDGED, RunItemStatus.JUDGE_FAILED)
ANSWER, JUDGE = "answer", "judge"  # the two phases of an item
MAX_RETRY_DELAY = 120.0
CLAIM_BATCH = 500


class Executor:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        cipher: Cipher | None,
        *,
        max_attempts: int = 3,
        retry_base_delay: float | None = None,
        idle_interval: float = 1.0,
    ):
        self.engine = engine
        self.settings = settings
        self.cipher = cipher
        self.max_attempts = max_attempts
        self.retry_base_delay = settings.retry_base_delay if retry_base_delay is None else retry_base_delay
        self.idle_interval = idle_interval
        self.global_limit = max(1, settings.max_concurrency)
        self._tasks: dict[int, asyncio.Task] = {}  # item id -> task
        self._task_run: dict[int, int] = {}  # item id -> run id
        self._task_providers: dict[int, list[int]] = {}  # item id -> provider id per parallel call
        self._not_before: dict[int, float] = {}  # item id -> monotonic time (backoff)
        self._judge_attempts: Counter = Counter()  # item id -> judge-phase attempts (in memory)
        self._wake: asyncio.Event | None = None
        self._loop_task: asyncio.Task | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def recover(self) -> int:
        """Items a killed process left `running` go back to `pending`."""
        with Session(self.engine) as session:
            result = session.exec(
                update(RunItem)
                .where(col(RunItem.status) == RunItemStatus.RUNNING)
                .values(status=RunItemStatus.PENDING, updated_at=utcnow())
            )
            session.commit()
        if result.rowcount:
            log.info("Resuming: %d interrupted item(s) put back in the queue", result.rowcount)
        return result.rowcount

    async def start(self) -> None:
        self.recover()
        self._wake = asyncio.Event()
        self._loop_task = asyncio.create_task(self._loop(), name="histbias-executor")

    async def stop(self) -> None:
        """Stop without waiting for calls in flight; their items are recovered on the next start."""
        tasks = [t for t in (self._loop_task, *self._tasks.values()) if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loop_task = None

    def wake(self) -> None:
        """Called by routes after starting/resuming/retrying a run. Thread-safe."""
        if self._wake is None or self._loop_task is None:
            return
        loop = self._loop_task.get_loop()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._wake.set()
        else:
            loop.call_soon_threadsafe(self._wake.set)

    async def _loop(self) -> None:
        assert self._wake is not None
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("Run worker tick failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), self._next_wait())
            except TimeoutError:
                pass

    def _next_wait(self) -> float:
        if not self._not_before:
            return self.idle_interval
        soonest = min(self._not_before.values()) - time.monotonic()
        return max(0.05, min(self.idle_interval, soonest))

    # ── scheduling ───────────────────────────────────────────────────────────

    def tick(self) -> int:
        """Activate queued runs, finish completed ones, and start as many items as the
        limits allow. Returns the number of items started."""
        with Session(self.engine) as session:
            for run in session.exec(select(Run).where(Run.status == RunStatus.QUEUED)).all():
                session.exec(
                    update(Run)
                    .where(Run.id == run.id, col(Run.status) == RunStatus.QUEUED)
                    .values(status=RunStatus.RUNNING, started_at=run.started_at or utcnow(), updated_at=utcnow())
                )
            session.commit()
            for run_id in session.exec(select(Run.id).where(Run.status == RunStatus.RUNNING)).all():
                self._maybe_finish(session, run_id)
            runs = list(session.exec(select(Run).where(Run.status == RunStatus.RUNNING)))
            return self._claim(session, runs)

    def _maybe_finish(self, session: Session, run_id: int) -> None:
        if any(r == run_id for r in self._task_run.values()):
            return
        run = session.get(Run, run_id)
        # With a judge, answered items still wait for their judgements.
        open_statuses = OPEN_ITEM + ((RunItemStatus.ANSWERED,) if run.judge_model_id else ())
        open_items = exists().where(RunItem.run_id == run_id, col(RunItem.status).in_(open_statuses))
        answered = session.exec(
            select(func.count()).select_from(RunItem).where(RunItem.run_id == run_id, col(RunItem.status).in_(ANSWERED))
        ).one()
        errors = session.exec(
            select(func.count()).select_from(RunItem).where(RunItem.run_id == run_id, RunItem.status == RunItemStatus.ERROR)
        ).one()
        # Every item failed: the run failed (e.g. a wrong key). "Retry failed" re-queues it.
        status = RunStatus.FAILED if errors and not answered else RunStatus.COMPLETED
        result = session.exec(
            update(Run)
            .where(Run.id == run_id, col(Run.status) == RunStatus.RUNNING, ~open_items)
            .values(status=status, finished_at=utcnow(), updated_at=utcnow())
        )
        session.commit()
        if result.rowcount:
            log.info("Run %d %s", run_id, status)

    def _provider_limit(self, provider: Provider) -> int:
        try:
            return max(1, int((provider.extra or {}).get("max_concurrency", self.settings.provider_concurrency)))
        except (TypeError, ValueError):
            return max(1, self.settings.provider_concurrency)

    def _claim(self, session: Session, runs: list[Run]) -> int:
        """Limits count calls: an internal item makes 1 call at a time, a criss-cross item 2
        (both sides of a turn in parallel). An item that needs more slots than a limit has
        still runs when nothing else holds that limit, so a limit of 1 never deadlocks."""
        in_use = sum(len(p) for p in self._task_providers.values())
        if in_use >= self.global_limit or not runs:
            return 0
        run_limit = {r.id: int(r.config.get("settings", {}).get("concurrency", self.global_limit)) for r in runs}
        per_run: Counter = Counter()
        per_provider: Counter = Counter()
        for item_id, pids in self._task_providers.items():
            per_run[self._task_run[item_id]] += len(pids)
            per_provider.update(pids)
        model_provider = dict(session.exec(select(LlmModel.id, LlmModel.provider_id)).all())
        providers = {p.id: p for p in session.exec(select(Provider))}
        now = time.monotonic()
        started = 0
        judged_runs = [r.id for r in runs if r.judge_model_id]
        judge_provider = {r.id: model_provider.get(r.judge_model_id) for r in runs if r.judge_model_id}
        candidates = session.exec(
            select(RunItem)
            .where(
                col(RunItem.run_id).in_(run_limit),
                (col(RunItem.status) == RunItemStatus.PENDING)
                | ((col(RunItem.status) == RunItemStatus.ANSWERED) & col(RunItem.run_id).in_(judged_runs)),
            )
            .order_by(RunItem.run_id, RunItem.id)
            .limit(CLAIM_BATCH)
        ).all()

        def fits(used: int, need: int, limit: int) -> bool:
            return used == 0 or used + need <= limit

        for item in candidates:
            if in_use >= self.global_limit:
                break
            if item.id in self._tasks or self._not_before.get(item.id, 0) > now:
                continue
            phase = JUDGE if item.status == RunItemStatus.ANSWERED else ANSWER
            if phase == ANSWER:
                pids = [model_provider.get(item.model_a_id)]
                if item.model_b_id is not None:
                    pids.append(model_provider.get(item.model_b_id))
            else:
                # Judge calls for a criss-cross item (4 responses) run 2 at a time.
                pids = [judge_provider[item.run_id]] * (2 if item.model_b_id is not None else 1)
            need = len(pids)
            if not fits(in_use, need, self.global_limit) or not fits(per_run[item.run_id], need, run_limit[item.run_id]):
                continue
            if not all(
                providers.get(pid) is None or fits(per_provider[pid], n, self._provider_limit(providers[pid]))
                for pid, n in Counter(pids).items()
            ):
                continue
            values: dict[str, Any] = {"status": RunItemStatus.RUNNING, "updated_at": utcnow()}
            if phase == ANSWER:  # `attempts` counts answer attempts; judge retries are counted in memory
                values["attempts"] = RunItem.attempts + 1
            claimed = session.exec(
                update(RunItem).where(RunItem.id == item.id, col(RunItem.status) == item.status).values(**values)
            )
            session.commit()
            if not claimed.rowcount:
                continue
            self._not_before.pop(item.id, None)
            in_use += need
            per_run[item.run_id] += need
            per_provider.update(pids)
            self._task_run[item.id] = item.run_id
            self._task_providers[item.id] = pids
            self._tasks[item.id] = asyncio.create_task(self._execute(item.id, phase), name=f"run-item-{item.id}-{phase}")
            started += 1
        return started

    # ── one item ─────────────────────────────────────────────────────────────

    async def _execute(self, item_id: int, phase: str = ANSWER) -> None:
        try:
            if phase == JUDGE:
                await self._judge_item(item_id)
            else:
                await self._call(item_id)
        except asyncio.CancelledError:
            raise  # shutdown: the item stays `running` and is recovered on the next start
        except Exception as e:  # never let one item kill the worker
            log.exception("Run item %d crashed (%s)", item_id, phase)
            status = RunItemStatus.JUDGE_FAILED if phase == JUDGE else RunItemStatus.ERROR
            self._finish(item_id, status, f"internal error ({type(e).__name__}); see the server log")
        finally:
            self._tasks.pop(item_id, None)
            self._task_run.pop(item_id, None)
            self._task_providers.pop(item_id, None)
            if self._wake is not None:
                self._wake.set()

    def _prepare(self, item_id: int) -> "_ItemContext":
        with Session(self.engine) as session:
            item = session.get(RunItem, item_id)
            run = session.get(Run, item.run_id)
            snaps = run.config.get("models") or {}

            def side(model_pk: int) -> _Side:
                snap = snaps.get(str(model_pk))
                if snap is None:
                    raise ValueError("the run config has no snapshot for this model")
                model = session.get(LlmModel, model_pk)
                provider = session.get(Provider, model.provider_id)
                adapter = credentials.adapter_for(session, self.cipher, provider, self.settings.request_timeout)
                return _Side(model_pk, snap, adapter)

            a = side(item.model_a_id)
            b = side(item.model_b_id) if item.model_b_id is not None else None
            done = {
                (r.llm_model_id, r.turn): crisscross.TurnOne(r.text, blocked=bool(r.error) and not r.text)
                for r in session.exec(select(Response).where(Response.run_item_id == item_id))
            }
            session.expunge(item)
            return _ItemContext(item, a, b, done)

    async def _call(self, item_id: int) -> None:
        try:
            ctx = self._prepare(item_id)
        except (credentials.CredentialError, DecryptionError, ValueError) as e:
            self._finish(item_id, RunItemStatus.ERROR, str(e))
            return
        if not await self._run_calls(ctx, ctx.turn1_calls()):
            return
        notes: list[str] = []
        if ctx.b is not None:
            # Pause/cancel are checked before turn 2 too.
            with Session(self.engine) as session:
                status = session.get(Run, ctx.item.run_id).status
            if status == RunStatus.PAUSED:
                self._requeue(item_id, "paused after turn 1", 0, refund_attempt=True)
                return
            if status == RunStatus.CANCELLED:
                self._finish(item_id, RunItemStatus.SKIPPED, "run cancelled after turn 1")
                return
            calls, notes = ctx.turn2_calls()
            if not await self._run_calls(ctx, calls):
                return
        self._finish(item_id, RunItemStatus.ANSWERED, "; ".join(notes) or None)

    async def _run_calls(self, ctx: "_ItemContext", calls: list["_Call"]) -> bool:
        """Make the calls in parallel and store every answer as soon as the batch returns,
        so a retry or restart only repeats the calls that failed. False = item not done."""
        if not calls:
            return True
        results = await asyncio.gather(*(c.side.adapter.chat(c.req) for c in calls), return_exceptions=True)
        failures: list[tuple[_Call, ProviderError]] = []
        crash: BaseException | None = None
        for call, res in zip(calls, results, strict=True):
            if isinstance(res, ProviderRefused):
                self._store(ctx, call, text="", finish_reason=None, error=str(res), raw={"response": res.raw}, blocked=True)
            elif isinstance(res, ProviderError):
                failures.append((call, res))
            elif isinstance(res, BaseException):
                crash = crash or res
            else:
                self._store(
                    ctx, call,
                    text=res.text, finish_reason=res.finish_reason, error=None,
                    raw={"request": res.raw_request, "response": res.raw_response, "notes": res.notes},
                    input_tokens=res.input_tokens, output_tokens=res.output_tokens, latency_ms=res.latency_ms,
                )
        if crash is not None:
            raise crash
        if not failures:
            return True
        item = ctx.item
        cc = ctx.b is not None
        # Criss-cross items make up to 2 rounds of calls, so they get the attempt budget twice.
        budget = self.max_attempts * (2 if cc else 1)
        message = "; ".join(
            f"{c.side.snap['display_name']} turn {c.turn}: {e}" if cc else str(e) for c, e in failures
        )
        if all(e.retryable for _, e in failures) and item.attempts < budget:
            delay = max(self._retry_delay(item.attempts, e) for _, e in failures)
            self._requeue(item.id, f"{message} – retrying in {delay:.0f}s (attempt {item.attempts + 1}/{budget})", delay)
        else:
            retryable = all(e.retryable for _, e in failures)
            suffix = f" (gave up after {item.attempts} attempts)" if retryable else ""
            self._finish(item.id, RunItemStatus.ERROR, f"{message}{suffix}")
        return False

    def _retry_delay(self, attempt: int, e: ProviderError) -> float:
        delay = self.retry_base_delay * (2 ** (attempt - 1))
        if isinstance(e, ProviderRateLimit) and e.retry_after:
            delay = max(delay, e.retry_after)
        delay *= random.uniform(1.0, 1.25)  # jitter
        return min(delay, MAX_RETRY_DELAY)

    def _requeue(
        self, item_id: int, message: str, delay: float, refund_attempt: bool = False,
        status: RunItemStatus = RunItemStatus.PENDING,
    ) -> None:
        if delay:
            self._not_before[item_id] = time.monotonic() + delay
        values: dict[str, Any] = {"status": status, "error": message[:2000], "updated_at": utcnow()}
        if refund_attempt:
            values["attempts"] = RunItem.attempts - 1
        with Session(self.engine) as session:
            session.exec(
                update(RunItem)
                .where(RunItem.id == item_id, col(RunItem.status) == RunItemStatus.RUNNING)
                .values(**values)
            )
            session.commit()

    def _finish(self, item_id: int, status: RunItemStatus, message: str | None) -> None:
        with Session(self.engine) as session:
            session.exec(
                update(RunItem)
                .where(RunItem.id == item_id, col(RunItem.status) == RunItemStatus.RUNNING)
                .values(status=status, error=message[:2000] if message else None, updated_at=utcnow())
            )
            session.commit()

    def _store(
        self, ctx: "_ItemContext", call: "_Call", *, text: str, finish_reason: str | None,
        error: str | None, raw: dict[str, Any], blocked: bool = False, input_tokens: int | None = None,
        output_tokens: int | None = None, latency_ms: int = 0,
    ) -> None:
        """One response row per call. The item is only marked answered once every call is
        stored; a restart in between re-runs the item, which skips the stored calls."""
        snap = call.side.snap
        with Session(self.engine) as session:
            session.add(
                Response(
                    run_item_id=ctx.item.id,
                    llm_model_id=call.side.model_pk,
                    model_id_snapshot=snap["model_id"],
                    display_name_snapshot=snap["display_name"],
                    turn=call.turn,
                    messages=[m.model_dump() for m in call.req.messages],
                    text=text,
                    finish_reason=finish_reason,
                    refusal_detected=refusal.is_refusal(text, finish_reason, blocked),
                    error=error,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    raw=raw,
                )
            )
            session.commit()
        ctx.done[(call.side.model_pk, call.turn)] = crisscross.TurnOne(text, blocked)

    # ── judge phase ──────────────────────────────────────────────────────────

    async def _judge_item(self, item_id: int) -> None:
        """Judge every response of an answered item that has no valid judgement yet
        (rubric v1). Ends `judged`, or `judge_failed` if any judgement failed."""
        try:
            ctx = self._prepare_judge(item_id)
        except (credentials.CredentialError, DecryptionError, ValueError) as e:
            self._finish(item_id, RunItemStatus.JUDGE_FAILED, f"judge: {e}")
            return
        limit = asyncio.Semaphore(2)

        async def one(job: _JudgeJob) -> None:
            async with limit:
                await self._judge_one(ctx, job)

        results = await asyncio.gather(*(one(j) for j in ctx.jobs), return_exceptions=True)
        api_errors = [r for r in results if isinstance(r, ProviderError)]
        crash = next((r for r in results if isinstance(r, BaseException) and not isinstance(r, ProviderError)), None)
        if crash is not None:
            raise crash
        if api_errors:
            self._judge_attempts[item_id] += 1
            attempt = self._judge_attempts[item_id]
            message = "judge: " + "; ".join(dict.fromkeys(str(e) for e in api_errors))
            if all(e.retryable for e in api_errors) and attempt < self.max_attempts:
                delay = max(self._retry_delay(attempt, e) for e in api_errors)
                self._requeue(
                    item_id, f"{message} – retrying in {delay:.0f}s (attempt {attempt + 1}/{self.max_attempts})",
                    delay, status=RunItemStatus.ANSWERED,
                )
            else:
                self._judge_attempts.pop(item_id, None)
                suffix = f" (gave up after {attempt} attempts)" if all(e.retryable for e in api_errors) else ""
                self._finish(item_id, RunItemStatus.JUDGE_FAILED, message + suffix)
            return
        self._judge_attempts.pop(item_id, None)
        with Session(self.engine) as session:
            failed = [
                j for j in session.exec(
                    select(Judgement)
                    .join(Response, Response.id == Judgement.response_id)
                    .where(
                        Response.run_item_id == item_id,
                        Judgement.judge_model_id == ctx.judge.model_pk,
                        Judgement.rubric_version == judging.RUBRIC_VERSION,
                    )
                )
                if not j.ok
            ]
        if failed:
            self._finish(
                item_id, RunItemStatus.JUDGE_FAILED,
                f"{len(failed)} judgement(s) failed: " + "; ".join(dict.fromkeys(j.error or "" for j in failed)),
            )
        else:
            self._finish(item_id, RunItemStatus.JUDGED, None)

    def _prepare_judge(self, item_id: int) -> "_JudgeContext":
        with Session(self.engine) as session:
            item = session.get(RunItem, item_id)
            run = session.get(Run, item.run_id)
            snap = run.config.get("judge")
            if not snap or run.judge_model_id is None:
                raise ValueError("this run has no judge")
            judge_model = session.get(LlmModel, run.judge_model_id)
            provider = session.get(Provider, judge_model.provider_id)
            adapter = credentials.adapter_for(session, self.cipher, provider, self.settings.request_timeout)
            topic = session.get(Topic, item.topic_id)
            countries = list(
                session.exec(
                    select(TopicCountry.country_code)
                    .where(TopicCountry.topic_id == item.topic_id)
                    .order_by(TopicCountry.position)
                )
            )
            responses = list(session.exec(select(Response).where(Response.run_item_id == item_id)))
            ok = {
                j.response_id
                for j in session.exec(
                    select(Judgement).where(
                        col(Judgement.response_id).in_([r.id for r in responses]),
                        Judgement.judge_model_id == run.judge_model_id,
                        Judgement.rubric_version == judging.RUBRIC_VERSION,
                    )
                )
                if j.ok
            }
            names = {int(k): v.get("display_name", "") for k, v in (run.config.get("models") or {}).items()}
            turn1 = {r.llm_model_id: r for r in responses if r.turn == 1}
            jobs = []
            for r in sorted(responses, key=lambda r: (r.turn, r.llm_model_id != item.model_a_id)):
                if r.id in ok:
                    continue
                partner_name = partner_answer = follow_up = None
                if r.turn == 2:
                    partner_pk = item.model_b_id if r.llm_model_id == item.model_a_id else item.model_a_id
                    partner_name = names.get(partner_pk, "the other model")
                    partner = turn1.get(partner_pk)
                    partner_answer = partner.text if partner else ""
                    follow_up = (item.follow_up_text or "").replace(crisscross.PLACEHOLDER, partner_name)
                jobs.append(
                    _JudgeJob(
                        response_id=r.id,
                        input=judging.JudgeInput(
                            title=topic.title,
                            era=topic.era,
                            countries=countries,
                            context=topic.context,
                            prompt_text=item.prompt_text,
                            answer_text=_answer_for_judge(r),
                            partner_name=partner_name,
                            partner_answer=partner_answer,
                            follow_up=follow_up,
                        ),
                    )
                )
            return _JudgeContext(item_id, _Side(run.judge_model_id, snap, adapter), jobs)

    async def _judge_one(self, ctx: "_JudgeContext", job: "_JudgeJob") -> None:
        """One judgement with one repair retry. Provider errors propagate (handled per item);
        a judge-side content block is stored as a failed judgement."""
        messages = judging.build_messages(job.input)
        prompt = judging.prompt_record(messages)
        try:
            first = await ctx.judge.adapter.chat(self._judge_request(ctx, messages))
        except ProviderRefused as e:
            self._store_judgement(ctx, job, prompt, "", None, error=f"the judge's API blocked the content: {e}")
            return
        verdict, problems = judging.parse_output(first.text)
        if verdict is not None:
            self._store_judgement(ctx, job, prompt, first.text, verdict)
            return
        try:
            second = await ctx.judge.adapter.chat(
                self._judge_request(ctx, judging.repair_messages(messages, first.text, problems))
            )
        except ProviderRefused as e:
            raw = first.text + "\n\n--- repair request: " + "; ".join(problems)
            self._store_judgement(ctx, job, prompt, raw, None, error=f"the judge's API blocked the repair request: {e}")
            return
        verdict, problems2 = judging.parse_output(second.text)
        raw = (
            f"{first.text}\n\n--- repair request: {'; '.join(problems)}\n--- reply after repair request:\n{second.text}"
        )
        if verdict is not None:
            self._store_judgement(ctx, job, prompt, raw, verdict, repaired=True)
        else:
            self._store_judgement(
                ctx, job, prompt, raw, None, error="invalid judge output after the repair retry: " + "; ".join(problems2)
            )

    @staticmethod
    def _judge_request(ctx: "_JudgeContext", messages: list[ChatMessage]) -> ChatRequest:
        snap = ctx.judge.snap
        return ChatRequest(
            model_id=snap["model_id"],
            messages=messages,
            temperature=snap.get("temperature", judging.JUDGE_TEMPERATURE),
            max_tokens=snap.get("max_tokens"),
            response_format_json=True,
        )

    def _store_judgement(
        self, ctx: "_JudgeContext", job: "_JudgeJob", prompt: str, raw: str, verdict: JudgeVerdict | None,
        *, repaired: bool = False, error: str | None = None,
    ) -> None:
        """Replaces an earlier failed judgement of the same response (UNIQUE per judge and rubric)."""
        fields = verdict.model_dump() if verdict is not None else {}
        with Session(self.engine) as session:
            session.exec(
                delete(Judgement).where(
                    Judgement.response_id == job.response_id,
                    Judgement.judge_model_id == ctx.judge.model_pk,
                    Judgement.rubric_version == judging.RUBRIC_VERSION,
                )
            )
            session.add(
                Judgement(
                    response_id=job.response_id,
                    judge_model_id=ctx.judge.model_pk,
                    rubric_version=judging.RUBRIC_VERSION,
                    raw_output=raw,
                    judge_prompt=prompt,
                    repaired=repaired,
                    error=error[:2000] if error else None,
                    **fields,
                )
            )
            session.commit()


    # ── tests and CLI ────────────────────────────────────────────────────────

    async def run_until_idle(self, timeout: float = 30) -> None:
        """Drive the worker in the foreground until no active run has open items."""
        deadline = time.monotonic() + timeout
        while True:
            self.tick()
            if not self._tasks and not self._has_open_work():
                return
            if time.monotonic() > deadline:
                raise TimeoutError("runs did not finish in time")
            if self._tasks:
                await asyncio.wait(list(self._tasks.values()), timeout=self._next_wait(), return_when=asyncio.FIRST_COMPLETED)
            else:
                await asyncio.sleep(self._next_wait())

    def _has_open_work(self) -> bool:
        with Session(self.engine) as session:
            return session.exec(
                select(func.count())
                .select_from(RunItem)
                .join(Run, Run.id == RunItem.run_id)
                .where(col(Run.status).in_(ACTIVE_RUN), col(RunItem.status).in_(OPEN_ITEM))
            ).one() > 0 or session.exec(
                select(func.count()).select_from(Run).where(col(Run.status).in_(ACTIVE_RUN))
            ).one() > 0


# ── per-item state ───────────────────────────────────────────────────────────


@dataclass
class _Side:
    model_pk: int  # llm_model.id
    snap: dict[str, Any]  # frozen model settings from run.config["models"]
    adapter: Any

    def request(self, messages: list[ChatMessage]) -> ChatRequest:
        return ChatRequest(
            model_id=self.snap["model_id"],
            messages=messages,
            temperature=self.snap.get("temperature"),
            max_tokens=self.snap.get("max_tokens"),
        )


@dataclass
class _Call:
    side: _Side
    turn: int
    req: ChatRequest


@dataclass
class _ItemContext:
    item: RunItem
    a: _Side
    b: _Side | None
    done: dict[tuple[int, int], crisscross.TurnOne]  # (llm_model.id, turn) -> stored answer

    def _sides(self) -> list[_Side]:
        return [self.a] if self.b is None else [self.a, self.b]

    def turn1_calls(self) -> list[_Call]:
        return [
            _Call(s, 1, s.request(crisscross.turn1_messages(self.item.prompt_text, s.snap.get("system_prompt"))))
            for s in self._sides()
            if (s.model_pk, 1) not in self.done
        ]

    def turn2_calls(self) -> tuple[list[_Call], list[str]]:
        assert self.b is not None
        if not self.item.follow_up_text:
            raise ValueError("this criss-cross item has no follow-up prompt")
        calls, notes = [], []
        for own, partner in ((self.a, self.b), (self.b, self.a)):
            own_name, partner_name = own.snap["display_name"], partner.snap["display_name"]
            own1, partner1 = self.done[(own.model_pk, 1)], self.done[(partner.model_pk, 1)]
            reason = crisscross.turn2_skip_reason(own_name, own1, partner_name, partner1)
            if reason:
                notes.append(f"no turn 2 for {own_name}: {reason}")
            elif (own.model_pk, 2) not in self.done:
                messages = crisscross.turn2_messages(
                    self.item.prompt_text, own1.text, self.item.follow_up_text,
                    partner_name, partner1.text, own.snap.get("system_prompt"),
                )
                calls.append(_Call(own, 2, own.request(messages)))
        return calls, notes


def _answer_for_judge(r: Response) -> str:
    """The answer text, or a neutral note for a blocked/empty answer (no model or provider
    name: the judge must not learn who answered, D10)."""
    if r.text.strip():
        return r.text
    if r.error:
        return "(No answer: the provider's API blocked the request or the answer.)"
    return "(The model returned an empty answer.)"


@dataclass
class _JudgeJob:
    response_id: int
    input: judging.JudgeInput


@dataclass
class _JudgeContext:
    item_id: int
    judge: _Side
    jobs: list[_JudgeJob]
