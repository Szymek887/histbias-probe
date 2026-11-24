"""Run planner: wizard settings → `run_item` rows (docs/02-architecture.md, "Run engine").

Internal topic: one item per tested model × selected language × repetition.
Criss-cross topic: one item per model pair × language × repetition (4 calls each).

Snapshot rule: the prompt text is copied into each item, and the models' settings are
frozen in `run.config["models"]`, which the executor uses instead of the live rows.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, col, delete, select

from app import __version__
from app.crypto import Cipher
from app.models import (
    Country,
    LlmModel,
    Prompt,
    Provider,
    Run,
    RunItem,
    RunModel,
    RunStatus,
    RunTopic,
    Topic,
    TopicCountry,
    TopicType,
)
from app.models.base import utcnow
from app.engine import judge as judging
from app.schemas.runs import RunSettings
from app.services import credentials

MODE_INTERNAL = TopicType.INTERNAL.value
MODE_CRISS_CROSS = TopicType.CRISS_CROSS.value


class PlanError(ValueError):
    """User-facing: the run can't be started as configured."""


@dataclass
class PlannedItem:
    topic: Topic
    language: str
    prompt_text: str
    model: LlmModel
    repetition: int
    model_b: LlmModel | None = None  # criss-cross partner
    follow_up_text: str | None = None

    @property
    def mode(self) -> str:
        return MODE_CRISS_CROSS if self.model_b is not None else MODE_INTERNAL

    @property
    def calls(self) -> int:
        return 4 if self.model_b is not None else 1  # A1, B1, A2, B2


@dataclass
class Plan:
    settings: RunSettings
    items: list[PlannedItem] = field(default_factory=list)
    models: list[LlmModel] = field(default_factory=list)  # usable, selected
    providers: dict[int, Provider] = field(default_factory=dict)
    excluded_models: list[tuple[LlmModel, str]] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)  # planned
    topic_countries: dict[int, list[str]] = field(default_factory=dict)
    skipped_topics: list[tuple[Topic, str]] = field(default_factory=list)
    judge: LlmModel | None = None
    judge_problem: str | None = None  # the chosen judge can't be used
    judge_warnings: list[str] = field(default_factory=list)
    # Criss-cross preview: topic -> its pairs (A, B).
    pairings: list[tuple[Topic, list[tuple[LlmModel, LlmModel]]]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return sum(i.calls for i in self.items)

    @property
    def judge_calls(self) -> int:
        """One per response (turn 2 can be skipped, and repairs add calls, so this is an estimate)."""
        return self.calls if self.judge is not None else 0

    def _paid(self, m: LlmModel | None) -> bool:
        p = self.providers.get(m.provider_id) if m is not None else None
        return m is not None and (p is None or p.kind != "mock")

    @property
    def paid_calls(self) -> int:
        """Model and judge calls that go to real (billed) providers, i.e. not the mock one."""
        n = sum(
            (2 * self._paid(i.model) + 2 * self._paid(i.model_b)) if i.model_b is not None else self._paid(i.model)
            for i in self.items
        )
        return n + (self.judge_calls if self._paid(self.judge) else 0)

    @property
    def internal_items(self) -> int:
        return sum(1 for i in self.items if i.model_b is None)

    @property
    def criss_cross_items(self) -> int:
        return sum(1 for i in self.items if i.model_b is not None)

    @property
    def problems(self) -> list[str]:
        """Why the run can't start (empty = it can)."""
        out = []
        if not self.models:
            out.append("choose at least one model that has an API key")
        if not self.topics and not self.skipped_topics:
            out.append("choose at least one topic")
        if self.models and not self.items:
            out.append("no model calls: check the model, pairing and language choices")
        if self.judge_problem:
            out.append(self.judge_problem)
        return out


def _load_models(session: Session, cipher: Cipher | None, plan: Plan) -> None:
    ids = plan.settings.model_ids
    models = {m.id: m for m in session.exec(select(LlmModel).where(col(LlmModel.id).in_(ids)))}
    statuses: dict[int, credentials.KeyStatus] = {}
    for mid in ids:
        m = models.get(mid)
        if m is None:
            continue
        provider = session.get(Provider, m.provider_id)
        plan.providers[provider.id] = provider
        if provider.id not in statuses:
            statuses[provider.id] = credentials.key_status(session, cipher, provider)
        problem = _key_problem(statuses[provider.id], m)
        if problem:
            plan.excluded_models.append((m, problem))
        else:
            plan.models.append(m)


def _key_problem(status: credentials.KeyStatus, model: LlmModel) -> str | None:
    if not model.enabled:
        return "disabled"
    if not model.model_id:
        return "no API model ID"
    if status.broken:
        return "stored key can't be decrypted"
    if not status.usable:
        return "no API key"
    if status.missing_extras:
        return f"key is missing {', '.join(status.missing_extras)}"
    return None


def _load_judge(session: Session, cipher: Cipher | None, plan: Plan) -> None:
    judge_id = plan.settings.judge_model_id
    if judge_id is None:
        return
    judge = session.get(LlmModel, judge_id)
    if judge is None:
        plan.judge_problem = "the chosen judge model no longer exists"
        return
    provider = session.get(Provider, judge.provider_id)
    plan.providers[provider.id] = provider
    problem = _key_problem(credentials.key_status(session, cipher, provider), judge)
    if problem:
        plan.judge_problem = f"the judge {judge.display_name} can't be used ({problem})"
        return
    plan.judge = judge
    tested = {m.id: m for m in plan.models}
    if judge.id in tested:
        plan.judge_warnings.append(
            f"{judge.display_name} is also a tested model – its own answers may be scored leniently."
        )
    same_provider = [m.display_name for m in plan.models if m.provider_id == judge.provider_id and m.id != judge.id]
    if same_provider:
        plan.judge_warnings.append(
            f"The judge comes from the same provider as {', '.join(same_provider)} – it may favour them."
        )
    if judge.country_code:
        own = [t.title for t in plan.topics if judge.country_code in plan.topic_countries.get(t.id, [])]
        if own:
            plan.judge_warnings.append(
                f"The judge is from {judge.country_code}, a country in {len(own)} selected topic(s); "
                "it may favour its own country's view."
            )


def _choose_prompts(
    prompts: list[Prompt], languages: list[str], natives: list[str]
) -> tuple[list[Prompt], list[str]]:
    """The prompts to ask for one topic, and notes about missing languages.
    `natives`: the native languages of the topic's countries (2 for criss-cross)."""
    by_lang = {p.language: p for p in prompts}
    default = next((p for p in prompts if p.is_default), prompts[0] if prompts else None)
    chosen: list[Prompt] = []
    missing: list[str] = []
    if "en" in languages:
        p = by_lang.get("en") or default
        if p is not None:
            chosen.append(p)
            if p.language != "en":
                missing.append("en")
    if "native" in languages:
        found = [by_lang[n] for n in dict.fromkeys(natives) if n in by_lang]
        if not found:
            missing.append("native")
        chosen.extend(p for p in found if p not in chosen)
    return chosen, missing


def _pairs_for(plan: Plan, countries: list[str], manual: list[tuple[LlmModel, LlmModel]]) -> tuple[list[tuple[LlmModel, LlmModel]], str | None]:
    """The (A, B) pairs for one criss-cross topic, or a skip reason."""
    if plan.settings.pairing == "manual":
        return (manual, None) if manual else ([], "no pairs chosen")
    x, y = (countries + [None, None])[:2]
    side_a = [m for m in plan.models if m.country_code and m.country_code == x]
    side_b = [m for m in plan.models if m.country_code and m.country_code == y]
    if not side_a or not side_b:
        lacking = " and ".join(c for c, side in ((x, side_a), (y, side_b)) if not side and c)
        return [], f"no selected model from {lacking}"
    return [(a, b) for a in side_a for b in side_b], None


def _manual_pairs(session: Session, plan: Plan) -> list[tuple[LlmModel, LlmModel]]:
    usable = {m.id: m for m in plan.models}
    ids = {i for pair in plan.settings.pairs for i in pair}
    names = {m.id: m.display_name for m in session.exec(select(LlmModel).where(col(LlmModel.id).in_(ids)))}
    out = []
    for a, b in plan.settings.pairs:
        if a in usable and b in usable:
            out.append((usable[a], usable[b]))
        else:
            missing = [names.get(i, f"model {i}") for i in (a, b) if i not in usable]
            plan.notes.append(
                f"Pair left out: {', '.join(missing)} is not selected or has no working key."
            )
    return out


def build_plan(session: Session, cipher: Cipher | None, settings: RunSettings) -> Plan:
    plan = Plan(settings=settings)
    _load_models(session, cipher, plan)
    manual = _manual_pairs(session, plan) if settings.pairing == "manual" else []

    ids = settings.topic_ids
    topics = {t.id: t for t in session.exec(select(Topic).where(col(Topic.id).in_(ids)))}
    for tc in session.exec(
        select(TopicCountry).where(col(TopicCountry.topic_id).in_(ids)).order_by(TopicCountry.position)
    ):
        plan.topic_countries.setdefault(tc.topic_id, []).append(tc.country_code)
    prompts: dict[int, list[Prompt]] = {}
    for p in session.exec(
        select(Prompt).where(col(Prompt.topic_id).in_(ids)).order_by(col(Prompt.is_default).desc(), Prompt.language)
    ):
        prompts.setdefault(p.topic_id, []).append(p)
    native_lang = {c.code: c.native_language for c in session.exec(select(Country))}

    no_native, no_english = [], []
    for tid in ids:
        topic = topics.get(tid)
        if topic is None:
            continue
        if topic.archived:
            plan.skipped_topics.append((topic, "archived"))
            continue
        countries = plan.topic_countries.get(tid) or []
        natives = [native_lang[c] for c in countries if c in native_lang]
        chosen, missing = _choose_prompts(prompts.get(tid, []), settings.languages, natives)
        is_cc = topic.type == TopicType.CRISS_CROSS
        if is_cc:
            chosen = [p for p in chosen if p.follow_up_text]
        if not chosen:
            plan.skipped_topics.append((topic, "no prompt in the selected languages"))
            continue
        if "native" in missing:
            no_native.append(topic.title)
        if "en" in missing:
            no_english.append(topic.title)

        if is_cc:
            pairs, reason = _pairs_for(plan, countries, manual)
            if reason:
                plan.skipped_topics.append((topic, reason))
                continue
            plan.pairings.append((topic, pairs))
            plan.topics.append(topic)
            for rep in range(1, settings.repetitions + 1):
                for prompt in chosen:
                    for a, b in pairs:
                        plan.items.append(
                            PlannedItem(topic, prompt.language, prompt.text, a, rep, model_b=b,
                                        follow_up_text=prompt.follow_up_text)
                        )
            continue

        country = countries[0] if countries else None
        models = plan.models
        if settings.internal_models == "home":
            models = [m for m in models if m.country_code and m.country_code == country]
            if not models:
                plan.skipped_topics.append((topic, f"no selected model from {country}"))
                continue
        plan.topics.append(topic)
        for rep in range(1, settings.repetitions + 1):
            for prompt in chosen:
                for model in models:
                    plan.items.append(PlannedItem(topic, prompt.language, prompt.text, model, rep))

    _load_judge(session, cipher, plan)
    if no_native:
        plan.notes.append(
            f"{len(no_native)} topic(s) have no native-language prompt, so they are asked in English only: "
            + ", ".join(no_native[:8]) + ("…" if len(no_native) > 8 else "")
        )
    if no_english:
        plan.notes.append(
            f"{len(no_english)} topic(s) have no English prompt; their default prompt is used: "
            + ", ".join(no_english[:8]) + ("…" if len(no_english) > 8 else "")
        )
    return plan


def _judge_snapshot(plan: Plan) -> dict[str, Any] | None:
    j = plan.judge
    if j is None:
        return None
    p = plan.providers[j.provider_id]
    return {
        "llm_model_id": j.id,
        "display_name": j.display_name,
        "model_id": j.model_id,
        "provider_id": p.id,
        "provider_name": p.name,
        "provider_kind": p.kind,
        "temperature": judging.JUDGE_TEMPERATURE,
        "max_tokens": j.max_tokens,
        "rubric_version": judging.RUBRIC_VERSION,
        "warnings": plan.judge_warnings,
    }


def snapshot(plan: Plan) -> dict[str, Any]:
    """The frozen run config. The executor reads model settings from here."""
    s = plan.settings
    models = {}
    for m in plan.models:
        p = plan.providers[m.provider_id]
        models[str(m.id)] = {
            "display_name": m.display_name,
            "model_id": m.model_id,
            "provider_id": p.id,
            "provider_name": p.name,
            "provider_kind": p.kind,
            "base_url": p.base_url,
            "country_code": m.country_code,
            "temperature": s.temperature if s.temperature is not None else m.temperature,
            "max_tokens": m.max_tokens,
            "system_prompt": m.system_prompt,
        }
    return {
        "settings": s.model_dump(mode="json"),
        "modes": sorted({i.mode for i in plan.items}),
        "app_version": __version__,
        "planned_at": utcnow().isoformat(),
        "judge": _judge_snapshot(plan),
        "rubric_version": judging.RUBRIC_VERSION if plan.judge else None,
        "models": models,
        "topics": {
            str(t.id): {
                "slug": t.slug,
                "title": t.title,
                "era": t.era,
                "type": t.type,
                "countries": plan.topic_countries.get(t.id, []),
            }
            for t in plan.topics
        },
        "skipped_topics": [{"topic_id": t.id, "title": t.title, "reason": r} for t, r in plan.skipped_topics],
        "excluded_models": [{"llm_model_id": m.id, "display_name": m.display_name, "reason": r} for m, r in plan.excluded_models],
        "notes": plan.notes,
        "pairings": [
            {"topic_id": t.id, "title": t.title, "pairs": [[a.id, b.id] for a, b in pairs]} for t, pairs in plan.pairings
        ],
        "total_items": len(plan.items),
        "total_calls": plan.calls,
    }


def _write_links(session: Session, run: Run, topic_ids: list[int], model_ids: list[int]) -> None:
    session.exec(delete(RunTopic).where(RunTopic.run_id == run.id))
    session.exec(delete(RunModel).where(RunModel.run_id == run.id))
    existing_topics = set(session.exec(select(Topic.id).where(col(Topic.id).in_(topic_ids))))
    existing_models = set(session.exec(select(LlmModel.id).where(col(LlmModel.id).in_(model_ids))))
    for tid in topic_ids:
        if tid in existing_topics:
            session.add(RunTopic(run_id=run.id, topic_id=tid))
    for mid in model_ids:
        if mid in existing_models:
            session.add(RunModel(run_id=run.id, llm_model_id=mid))


def save_draft(session: Session, settings: RunSettings, run: Run | None = None) -> Run:
    if run is not None and run.status != RunStatus.DRAFT:
        raise PlanError("only draft runs can be edited")
    run = run or Run(name=settings.name)
    run.name = settings.name
    run.status = RunStatus.DRAFT
    run.config = {"settings": settings.model_dump(mode="json")}
    run.updated_at = utcnow()
    session.add(run)
    session.flush()
    _write_links(session, run, settings.topic_ids, settings.model_ids)
    session.commit()
    return run


def start_run(session: Session, cipher: Cipher | None, settings: RunSettings, run: Run | None = None) -> Run:
    """Plan the items and queue the run (a new one, or the given draft)."""
    if run is not None and run.status != RunStatus.DRAFT:
        raise PlanError("this run has already been started")
    plan = build_plan(session, cipher, settings)
    if plan.problems:
        raise PlanError("; ".join(plan.problems))
    run = run or Run(name=settings.name)
    run.name = settings.name
    run.config = snapshot(plan)
    run.judge_model_id = plan.judge.id if plan.judge else None
    run.status = RunStatus.QUEUED
    run.updated_at = utcnow()
    session.add(run)
    session.flush()
    _write_links(session, run, settings.topic_ids, [m.id for m in plan.models])
    for it in plan.items:
        session.add(
            RunItem(
                run_id=run.id,
                topic_id=it.topic.id,
                mode=it.mode,
                language=it.language,
                repetition=it.repetition,
                model_a_id=it.model.id,
                model_b_id=it.model_b.id if it.model_b else None,
                prompt_text=it.prompt_text,
                follow_up_text=it.follow_up_text,
            )
        )
    session.commit()
    return run


def settings_of(run: Run) -> RunSettings:
    return RunSettings.model_validate(run.config["settings"])
