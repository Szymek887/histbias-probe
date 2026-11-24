"""Run exports: CSV (one row per judgement) and JSON (full run). docs/04-ui.md §7.

Neither contains credential data: the run config holds no secrets, and stored request/
response bodies never include headers (docs/07-security.md, tested).
"""

import csv
import io
import json
from typing import Any

from sqlmodel import Session, col, select

from app import __version__
from app.engine import crisscross
from app.models import Judgement, Response, Run, RunItem, Topic, TopicCountry
from app.models.base import utcnow

COLLECTED_VIA = "API"
LIMITATIONS = [
    "Answers were collected through the providers' APIs, not their consumer chat apps; apps may add "
    "their own moderation layer, so results can differ.",
    "Scores come from an LLM judge, which may favour its own answers or its own country's view.",
    "The reference context given to the judge is written by human researchers and may be incomplete.",
    "Answers vary between calls; use repetitions for serious comparisons.",
    "The judge's quality may be lower for answers in some languages (e.g. Arabic or Chinese).",
]

CSV_COLUMNS = [
    "run_id", "run_name", "item_id", "response_id", "topic_slug", "topic_title", "era", "topic_countries",
    "mode", "language", "repetition", "turn", "model", "model_api_id", "model_country", "partner_model",
    "prompt", "follow_up", "answer", "finish_reason", "refusal_heuristic", "answer_error",
    "input_tokens", "output_tokens", "latency_ms",
    "judge", "rubric_version", "overall_score", "censorship", "judge_refusal", "balance_score", "depth_score",
    "narrative_alignment", "factual_issues", "omissions", "rationale", "judge_repaired", "judge_error",
    "collected_via",
]  # fmt: skip

# Cells starting with these are run as formulas by spreadsheet apps (CSV injection).
# Model answers are untrusted, so such cells get a leading apostrophe.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def _load(session: Session, run: Run):
    items = list(session.exec(select(RunItem).where(RunItem.run_id == run.id).order_by(RunItem.id)))
    item_ids = [i.id for i in items]
    responses = list(
        session.exec(select(Response).where(col(Response.run_item_id).in_(item_ids)).order_by(Response.id))
    )
    judgements: dict[int, list[Judgement]] = {}
    for j in session.exec(
        select(Judgement).where(col(Judgement.response_id).in_([r.id for r in responses])).order_by(Judgement.id)
    ):
        judgements.setdefault(j.response_id, []).append(j)
    topic_ids = {i.topic_id for i in items}
    topics = {t.id: t for t in session.exec(select(Topic).where(col(Topic.id).in_(topic_ids)))}
    countries: dict[int, list[str]] = {}
    for tc in session.exec(
        select(TopicCountry).where(col(TopicCountry.topic_id).in_(topic_ids)).order_by(TopicCountry.position)
    ):
        countries.setdefault(tc.topic_id, []).append(tc.country_code)
    return items, responses, judgements, topics, countries


def _topic_info(run: Run, topic_id: int, topics: dict, countries: dict) -> dict[str, Any]:
    snap = (run.config.get("topics") or {}).get(str(topic_id)) or {}
    t = topics.get(topic_id)
    return {
        "id": topic_id,
        "slug": snap.get("slug") or (t.slug if t else None),
        "title": snap.get("title") or (t.title if t else None),
        "era": snap.get("era") or (t.era if t else None),
        "countries": snap.get("countries") or countries.get(topic_id, []),
    }


def _model_name(run: Run, pk: int | None) -> str:
    if pk is None:
        return ""
    return ((run.config.get("models") or {}).get(str(pk)) or {}).get("display_name", f"model {pk}")


def _judge_name(run: Run, judge_model_id: int) -> str:
    j = run.config.get("judge") or {}
    if j.get("llm_model_id") == judge_model_id:
        return j.get("display_name", "")
    return _model_name(run, judge_model_id)


def to_csv(session: Session, run: Run) -> str:
    """UTF-8 with a BOM, so spreadsheet apps detect the encoding (Arabic, Chinese, Cyrillic)."""
    items, responses, judgements, topics, countries = _load(session, run)
    by_item = {i.id: i for i in items}
    models = run.config.get("models") or {}
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for r in responses:
        item = by_item[r.run_item_id]
        topic = _topic_info(run, item.topic_id, topics, countries)
        partner_pk = None
        if item.model_b_id is not None:
            partner_pk = item.model_b_id if r.llm_model_id == item.model_a_id else item.model_a_id
        partner = _model_name(run, partner_pk)
        follow_up = ""
        if r.turn == 2 and item.follow_up_text:
            follow_up = item.follow_up_text.replace(crisscross.PLACEHOLDER, partner)
        base = [
            run.id, run.name, item.id, r.id, topic["slug"], topic["title"], topic["era"], " ".join(topic["countries"]),
            item.mode, item.language, item.repetition, r.turn, r.display_name_snapshot, r.model_id_snapshot,
            (models.get(str(r.llm_model_id)) or {}).get("country_code") or "", partner,
            item.prompt_text, follow_up, r.text, r.finish_reason or "", r.refusal_detected, r.error or "",
            r.input_tokens, r.output_tokens, r.latency_ms,
        ]
        js = judgements.get(r.id) or [None]
        for j in js:
            if j is None:
                judge_cols = [""] * 13
            else:
                judge_cols = [
                    _judge_name(run, j.judge_model_id), j.rubric_version, j.overall_score, j.censorship or "",
                    "" if j.refusal is None else j.refusal, j.balance_score, j.depth_score,
                    j.narrative_alignment or "", " | ".join(j.factual_issues), " | ".join(j.omissions),
                    j.rationale or "", j.repaired, j.error or "",
                ]
            writer.writerow([_cell(v) for v in (*base, *judge_cols, COLLECTED_VIA)])
    return "﻿" + buf.getvalue()


def _dt(value) -> str | None:
    return value.isoformat() if value else None


def to_json(session: Session, run: Run) -> dict[str, Any]:
    items, responses, judgements, topics, countries = _load(session, run)
    by_item: dict[int, list[Response]] = {}
    for r in responses:
        by_item.setdefault(r.run_item_id, []).append(r)
    return {
        "format": "histbias-run-export",
        "format_version": 1,
        "exported_at": utcnow().isoformat(),
        "app_version": __version__,
        "collected_via": COLLECTED_VIA,
        "limitations": LIMITATIONS,
        "run": {
            "id": run.id,
            "name": run.name,
            "status": run.status,
            "judge_model_id": run.judge_model_id,
            "created_at": _dt(run.created_at),
            "started_at": _dt(run.started_at),
            "finished_at": _dt(run.finished_at),
            "notes": run.notes,
            "config": run.config,
        },
        "items": [
            {
                "id": i.id,
                "topic": _topic_info(run, i.topic_id, topics, countries),
                "mode": i.mode,
                "language": i.language,
                "repetition": i.repetition,
                "model_a": {"llm_model_id": i.model_a_id, "display_name": _model_name(run, i.model_a_id)},
                "model_b": (
                    {"llm_model_id": i.model_b_id, "display_name": _model_name(run, i.model_b_id)}
                    if i.model_b_id is not None else None
                ),
                "prompt_text": i.prompt_text,
                "follow_up_text": i.follow_up_text,
                "status": i.status,
                "error": i.error,
                "attempts": i.attempts,
                "responses": [
                    {
                        "id": r.id,
                        "llm_model_id": r.llm_model_id,
                        "display_name": r.display_name_snapshot,
                        "model_api_id": r.model_id_snapshot,
                        "turn": r.turn,
                        "messages": r.messages,
                        "text": r.text,
                        "finish_reason": r.finish_reason,
                        "refusal_detected": r.refusal_detected,
                        "error": r.error,
                        "input_tokens": r.input_tokens,
                        "output_tokens": r.output_tokens,
                        "latency_ms": r.latency_ms,
                        "raw": r.raw,
                        "created_at": _dt(r.created_at),
                        "judgements": [
                            {
                                "judge_model_id": j.judge_model_id,
                                "judge": _judge_name(run, j.judge_model_id),
                                "rubric_version": j.rubric_version,
                                "overall_score": j.overall_score,
                                "censorship": j.censorship,
                                "refusal": j.refusal,
                                "balance_score": j.balance_score,
                                "depth_score": j.depth_score,
                                "factual_issues": j.factual_issues,
                                "omissions": j.omissions,
                                "narrative_alignment": j.narrative_alignment,
                                "rationale": j.rationale,
                                "repaired": j.repaired,
                                "error": j.error,
                                "raw_output": j.raw_output,
                                "judge_prompt": j.judge_prompt,
                                "created_at": _dt(j.created_at),
                            }
                            for j in judgements.get(r.id, [])
                        ],
                    }
                    for r in by_item.get(i.id, [])
                ],
            }
            for i in items
        ],
    }


def to_json_text(session: Session, run: Run) -> str:
    return json.dumps(to_json(session, run), ensure_ascii=False, indent=2)


def filename(run: Run, ext: str) -> str:
    from app.services.topics import slugify

    return f"histbias-run-{run.id}-{slugify(run.name)[:40] or 'run'}.{ext}"
