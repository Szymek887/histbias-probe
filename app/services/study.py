"""Compare a run's scorecard with the AGH study's human ranking (M7 demo, docs/08-roadmap.md).

The ranking is in `seed/study_ranking.yaml`. A run model matches a study model when its
display name or API model ID (or the ID's last path segment) is in the entry's `match` list.
"""

import statistics
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.services.results import ModelScore

RANKING_PATH = Path(__file__).resolve().parents[2] / "seed" / "study_ranking.yaml"


@dataclass(frozen=True)
class StudyModel:
    place: int
    name: str
    country: str | None
    mean: float
    note: str | None
    match: tuple[str, ...]


@dataclass
class StudyRanking:
    source: str
    scale: str
    models: list[StudyModel]


@lru_cache
def load_ranking(path: Path = RANKING_PATH) -> StudyRanking:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    models = [
        StudyModel(
            place=int(m["place"]), name=str(m["name"]), country=m.get("country"), mean=float(m["mean"]),
            note=m.get("note"), match=tuple(str(x).casefold() for x in m.get("match") or [m["name"]]),
        )
        for m in data["models"]
    ]  # fmt: skip
    return StudyRanking(source=data.get("source", ""), scale=data.get("scale", ""), models=models)


def _keys(display_name: str, model_id: str | None) -> set[str]:
    keys = {display_name.casefold()}
    if model_id:
        keys.add(model_id.casefold())
        keys.add(model_id.rsplit("/", 1)[-1].casefold())
    return keys


def match_model(display_name: str, model_id: str | None, ranking: StudyRanking) -> StudyModel | None:
    keys = _keys(display_name, model_id)
    return next((m for m in ranking.models if keys & set(m.match)), None)


@dataclass
class Pair:
    study: StudyModel
    score: ModelScore
    app_rank: float = 0  # among the matched models, 1 = best (ties share the average rank)
    study_rank: float = 0

    @property
    def delta(self) -> float:
        return self.score.mean - self.study.mean


@dataclass
class Comparison:
    pairs: list[Pair] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)  # run models with scores but no study match
    missing: list[StudyModel] = field(default_factory=list)  # study models not in the run
    spearman: float | None = None
    mean_abs_delta: float | None = None

    @property
    def n(self) -> int:
        return len(self.pairs)


def _ranks(values: list[float]) -> list[float]:
    """1 = highest value; ties get the average of their positions."""
    order = sorted(range(len(values)), key=lambda i: -values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    """Spearman's rank correlation (Pearson on average ranks); None if undefined."""
    if len(a) < 3 or len(a) != len(b):
        return None
    ra, rb = _ranks(a), _ranks(b)
    if len(set(ra)) == 1 or len(set(rb)) == 1:
        return None
    return statistics.correlation(ra, rb)


def compare(scores: list[ModelScore], model_ids: dict[int, str | None], ranking: StudyRanking | None = None) -> Comparison:
    """`model_ids` maps run model pk -> API model ID (from the run snapshot)."""
    ranking = ranking or load_ranking()
    best: dict[int, Pair] = {}  # study place -> pair; a study model gets the run model with most judged answers
    out = Comparison()
    for s in scores:
        if s.mean is None:
            continue
        m = match_model(s.name, model_ids.get(s.model_pk), ranking)
        if m is None:
            out.unmatched.append(s.name)
        elif m.place not in best or s.judged > best[m.place].score.judged:
            if m.place in best:
                out.unmatched.append(best[m.place].score.name)
            best[m.place] = Pair(study=m, score=s)
        else:
            out.unmatched.append(s.name)
    out.pairs = sorted(best.values(), key=lambda p: p.study.place)
    out.missing = [m for m in ranking.models if m.place not in best]
    if out.pairs:
        app = [p.score.mean for p in out.pairs]
        study = [p.study.mean for p in out.pairs]
        for p, ra, rs in zip(out.pairs, _ranks(app), _ranks(study), strict=True):
            p.app_rank, p.study_rank = ra, rs
        out.spearman = spearman(app, study)
        out.mean_abs_delta = statistics.fmean(abs(p.delta) for p in out.pairs)
    return out


def model_ids_of(run_config: dict) -> dict[int, str | None]:
    return {int(pk): m.get("model_id") for pk, m in (run_config.get("models") or {}).items()}
