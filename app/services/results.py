"""Run results: scorecard, heatmap, side-by-side comparison (docs/04-ui.md §7, 05-methodology
"Aggregates on the results page").

Nothing is stored: one SQL query loads a row per response (with the run judge's verdict),
and the aggregates are computed from those rows. Topic and model names come from the run's
frozen config where available (snapshot rule).
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import and_
from sqlmodel import Session, col, select

from app.models import Judgement, Response, Run, RunItem, Topic, TopicCountry
from app.services.topics import ERA_ORDER

ERAS = ["past", "20th_century", "present"]
MODES = ["internal", "criss_cross"]


@dataclass
class Filters:
    country: str = ""
    era: str = ""
    mode: str = ""

    def active(self) -> bool:
        return bool(self.country or self.era or self.mode)

    def query(self) -> dict[str, str]:
        return {k: v for k, v in (("country", self.country), ("era", self.era), ("mode", self.mode)) if v}


@dataclass
class Row:
    """One response with the run judge's verdict (if any)."""

    response_id: int
    item_id: int
    topic_id: int
    topic_title: str
    era: str
    countries: list[str]
    mode: str
    language: str
    repetition: int
    turn: int
    model_pk: int
    model_name: str
    model_country: str | None
    text: str
    refusal_heuristic: bool
    answer_error: str | None
    judgement: Judgement | None

    @property
    def judged(self) -> bool:
        return self.judgement is not None and self.judgement.ok

    @property
    def score(self) -> int | None:
        return self.judgement.overall_score if self.judged else None

    @property
    def home(self) -> bool | None:
        """Is this the model's own country's topic? None if the model has no country."""
        return None if not self.model_country else self.model_country in self.countries


def load_rows(session: Session, run: Run) -> list[Row]:
    cfg = run.config or {}
    models = cfg.get("models") or {}
    snap_topics = cfg.get("topics") or {}
    rubric = (cfg.get("judge") or {}).get("rubric_version")
    join_on = and_(
        Judgement.response_id == Response.id,
        Judgement.judge_model_id == (run.judge_model_id or -1),
        Judgement.rubric_version == (rubric or ""),
    )
    result = session.exec(
        select(Response, RunItem, Judgement)
        .join(RunItem, RunItem.id == Response.run_item_id)
        .join(Judgement, join_on, isouter=True)
        .where(RunItem.run_id == run.id)
        .order_by(Response.id)
    ).all()
    topic_ids = {item.topic_id for _, item, _ in result}
    topics = {t.id: t for t in session.exec(select(Topic).where(col(Topic.id).in_(topic_ids)))}
    live_countries: dict[int, list[str]] = defaultdict(list)
    for tc in session.exec(
        select(TopicCountry).where(col(TopicCountry.topic_id).in_(topic_ids)).order_by(TopicCountry.position)
    ):
        live_countries[tc.topic_id].append(tc.country_code)

    rows = []
    for resp, item, judgement in result:
        snap = snap_topics.get(str(item.topic_id)) or {}
        topic = topics.get(item.topic_id)
        model = models.get(str(resp.llm_model_id)) or {}
        row = Row(
            response_id=resp.id,
            item_id=item.id,
            topic_id=item.topic_id,
            topic_title=snap.get("title") or (topic.title if topic else f"topic {item.topic_id}"),
            era=snap.get("era") or (topic.era if topic else ""),
            countries=snap.get("countries") or live_countries.get(item.topic_id, []),
            mode=item.mode,
            language=item.language,
            repetition=item.repetition,
            turn=resp.turn,
            model_pk=resp.llm_model_id,
            model_name=resp.display_name_snapshot or model.get("display_name", ""),
            model_country=model.get("country_code"),
            text=resp.text,
            refusal_heuristic=resp.refusal_detected,
            answer_error=resp.error,
            judgement=judgement,
        )
        rows.append(row)
    return rows


def apply_filters(rows: list[Row], f: Filters) -> list[Row]:
    return [
        r for r in rows
        if (not f.country or f.country in r.countries) and (not f.era or r.era == f.era)
        and (not f.mode or r.mode == f.mode)
    ]  # fmt: skip


# ── scorecard ────────────────────────────────────────────────────────────────


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _rate(flags: list[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None


@dataclass
class ModelScore:
    model_pk: int
    name: str
    country: str | None
    responses: int = 0
    judged: int = 0
    mean: float | None = None
    sd: float | None = None
    hard: int = 0
    soft: int = 0
    refusal_heuristic: float | None = None  # share of responses
    refusal_judge: float | None = None  # share of judged responses
    disagreements: int = 0  # judge refusal != heuristic
    by_era: dict[str, float | None] = field(default_factory=dict)
    by_mode: dict[str, float | None] = field(default_factory=dict)
    depth: float | None = None
    balance: float | None = None
    home: float | None = None
    foreign: float | None = None

    def sort_value(self, key: str) -> float | str | None:
        if key == "name":
            return self.name.lower()
        if key in ERAS:
            return self.by_era.get(key)
        if key in MODES:
            return self.by_mode.get(key)
        return getattr(self, key, None)


SORT_KEYS = {
    "name", "mean", "hard", "soft", "refusal_heuristic", "refusal_judge", "disagreements",
    "depth", "balance", "home", "foreign", *ERAS, *MODES,
}  # fmt: skip


def scorecard(rows: list[Row], sort: str = "mean", desc: bool = True) -> list[ModelScore]:
    by_model: dict[int, list[Row]] = defaultdict(list)
    for r in rows:
        by_model[r.model_pk].append(r)
    out = []
    for pk, rs in by_model.items():
        judged = [r for r in rs if r.judged]
        scores = [r.score for r in judged]
        s = ModelScore(model_pk=pk, name=rs[0].model_name, country=rs[0].model_country)
        s.responses = len(rs)
        s.judged = len(judged)
        s.mean = _mean(scores)
        s.sd = statistics.stdev(scores) if len(scores) > 1 else None
        s.hard = sum(1 for r in judged if r.judgement.censorship == "hard")
        s.soft = sum(1 for r in judged if r.judgement.censorship == "soft")
        s.refusal_heuristic = _rate([r.refusal_heuristic for r in rs])
        s.refusal_judge = _rate([bool(r.judgement.refusal) for r in judged])
        s.disagreements = sum(1 for r in judged if bool(r.judgement.refusal) != r.refusal_heuristic)
        s.by_era = {e: _mean([r.score for r in judged if r.era == e]) for e in ERAS}
        s.by_mode = {m: _mean([r.score for r in judged if r.mode == m]) for m in MODES}
        s.depth = _mean([r.judgement.depth_score for r in judged])
        s.balance = _mean([r.judgement.balance_score for r in judged])
        s.home = _mean([r.score for r in judged if r.home is True])
        s.foreign = _mean([r.score for r in judged if r.home is False])
        out.append(s)
    key = sort if sort in SORT_KEYS else "mean"
    present = [s for s in out if s.sort_value(key) is not None]
    missing = [s for s in out if s.sort_value(key) is None]
    present.sort(key=lambda s: (s.sort_value(key), s.name.lower()), reverse=desc)
    return present + sorted(missing, key=lambda s: s.name.lower())  # no data: always last


# ── heatmap ──────────────────────────────────────────────────────────────────


@dataclass
class Cell:
    scores: list[int] = field(default_factory=list)
    responses: int = 0
    refusals: int = 0  # heuristic
    first_response_id: int | None = None

    @property
    def mean(self) -> float | None:
        return _mean(self.scores)

    @property
    def color(self) -> int | None:
        """1-5 class for the cell colour (rounded half up)."""
        m = self.mean
        return None if m is None else min(5, max(1, int(m + 0.5)))


@dataclass
class HeatTopic:
    topic_id: int
    title: str
    era: str
    countries: list[str]
    mode: str


@dataclass
class Heatmap:
    models: list[tuple[int, str]]
    topics: list[HeatTopic]
    cells: dict[tuple[int, int], Cell]

    def cell(self, topic_id: int, model_pk: int) -> Cell | None:
        return self.cells.get((topic_id, model_pk))


def heatmap(rows: list[Row], model_order: list[int] | None = None) -> Heatmap:
    """Topics × models. A cell is the mean overall score of all that model's judged answers
    on the topic (both turns, all languages and repetitions)."""
    cells: dict[tuple[int, int], Cell] = {}
    topics: dict[int, HeatTopic] = {}
    names: dict[int, str] = {}
    for r in sorted(rows, key=lambda r: (r.turn, r.repetition, r.response_id)):
        names.setdefault(r.model_pk, r.model_name)
        topics.setdefault(r.topic_id, HeatTopic(r.topic_id, r.topic_title, r.era, r.countries, r.mode))
        c = cells.setdefault((r.topic_id, r.model_pk), Cell())
        c.responses += 1
        c.refusals += r.refusal_heuristic
        if c.first_response_id is None:
            c.first_response_id = r.response_id
        if r.judged:
            c.scores.append(r.score)
    order = [pk for pk in (model_order or []) if pk in names] + sorted(
        (pk for pk in names if pk not in (model_order or [])), key=lambda pk: names[pk].lower()
    )
    ordered_topics = sorted(
        topics.values(),
        key=lambda t: (t.mode != "internal", t.countries, ERA_ORDER.get(t.era, 9), t.title.lower()),
    )
    return Heatmap([(pk, names[pk]) for pk in order], ordered_topics, cells)


# ── comparison ───────────────────────────────────────────────────────────────


def comparable(rows: list[Row], response_id: int) -> tuple[Row | None, list[Row]]:
    """The same topic answered by the other models in this run: same language, turn and
    repetition (docs/04-ui.md §8 "Compare")."""
    me = next((r for r in rows if r.response_id == response_id), None)
    if me is None:
        return None, []
    others = [
        r for r in rows
        if r.topic_id == me.topic_id and r.language == me.language and r.turn == me.turn
        and r.repetition == me.repetition and r.response_id != me.response_id
    ]  # fmt: skip
    # One answer per model (a model in several pairs answers turn 1 once per pair).
    seen: set[int] = set()
    unique = []
    for r in sorted(others, key=lambda r: (r.model_name.lower(), r.response_id)):
        if r.model_pk not in seen and r.model_pk != me.model_pk:
            seen.add(r.model_pk)
            unique.append(r)
    return me, unique


def filter_options(rows: list[Row]) -> dict[str, list[str]]:
    return {
        "countries": sorted({c for r in rows for c in r.countries}),
        "eras": [e for e in ERAS if any(r.era == e for r in rows)],
        "modes": [m for m in MODES if any(r.mode == m for r in rows)],
    }
