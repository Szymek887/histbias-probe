"""Results, exports and duplicate run (M6)."""

import asyncio
import csv
import io
import re
import time

import httpx
import respx
from sqlmodel import Session, select

from app.crypto import Cipher
from app.db import get_engine
from app.engine import planner
from app.engine.executor import Executor
from app.models import Judgement, LlmModel, Provider, Response, Run, Topic, TopicType
from app.schemas.runs import RunSettings
from app.services import credentials, export
from app.services import providers as provider_svc
from app.services import results as svc
from app.services.topic_io import import_seed
from tests.conftest import csrf_token
from tests.test_run_engine import DEEPSEEK, FAKE_KEY, chat_ok


class J:
    """A minimal stand-in for a Judgement."""

    def __init__(self, score, censorship="none", refusal=False, depth=3, balance=3):
        self.overall_score, self.censorship, self.refusal = score, censorship, refusal
        self.depth_score, self.balance_score, self.ok = depth, balance, True


def row(rid, model, score=None, *, era="present", mode="internal", countries=("CN",), country=None,
        topic=1, refusal=False, turn=1, judge_refusal=False, censorship="none") -> svc.Row:  # fmt: skip
    return svc.Row(
        response_id=rid, item_id=rid, topic_id=topic, topic_title=f"T{topic}", era=era, countries=list(countries),
        mode=mode, language="en", repetition=1, turn=turn, model_pk=model, model_name=f"M{model}",
        model_country=country, text="x", refusal_heuristic=refusal, answer_error=None,
        judgement=J(score, censorship, judge_refusal) if score is not None else None,
    )


# ── aggregates ───────────────────────────────────────────────────────────────


def test_scorecard_aggregates():
    rows = [
        row(1, 1, 1, era="past", countries=("CN",), country="CN", refusal=True, judge_refusal=True, censorship="hard"),
        row(2, 1, 3, era="present", countries=("CN",), country="CN", censorship="soft"),
        row(3, 1, 5, era="present", countries=("US",), country="CN", mode="criss_cross"),
        row(4, 1, None, refusal=True),  # answered, not judged
        row(5, 2, 4, country=None, judge_refusal=True),  # judge says refusal, heuristic not
    ]
    by = {s.model_pk: s for s in svc.scorecard(rows)}
    m1 = by[1]
    assert (m1.responses, m1.judged, m1.hard, m1.soft) == (4, 3, 1, 1)
    assert m1.mean == 3 and round(m1.sd, 3) == 2.0
    assert m1.refusal_heuristic == 0.5 and round(m1.refusal_judge, 3) == round(1 / 3, 3)
    assert m1.by_era == {"past": 1, "20th_century": None, "present": 4}
    assert m1.by_mode == {"internal": 2, "criss_cross": 5}
    assert m1.home == 2 and m1.foreign == 5  # CN topics vs the US topic
    assert m1.disagreements == 0 and by[2].disagreements == 1
    assert by[2].home is None and by[2].sd is None


def test_scorecard_sorting_puts_missing_last():
    rows = [row(1, 1, 2), row(2, 2, 5), row(3, 3, None)]
    assert [s.model_pk for s in svc.scorecard(rows)] == [2, 1, 3]
    assert [s.model_pk for s in svc.scorecard(rows, "mean", desc=False)] == [1, 2, 3]
    assert [s.model_pk for s in svc.scorecard(rows, "name", desc=False)] == [1, 2, 3]
    assert [s.model_pk for s in svc.scorecard(rows, "bogus")] == [2, 1, 3]  # falls back to mean


def test_heatmap_cells_and_compare():
    rows = [row(1, 1, 2, topic=1), row(2, 1, 3, topic=1, turn=2), row(3, 2, 5, topic=1), row(4, 2, None, topic=2)]
    heat = svc.heatmap(rows)
    c = heat.cell(1, 1)
    assert c.mean == 2.5 and c.color == 3 and c.responses == 2 and c.first_response_id == 1
    assert heat.cell(2, 2).mean is None and heat.cell(2, 1) is None
    assert [t.topic_id for t in heat.topics] == [1, 2]
    me, others = svc.comparable(rows, 1)
    assert me.response_id == 1 and [r.response_id for r in others] == [3]  # same turn only
    filtered = svc.apply_filters(rows, svc.Filters(country="US"))
    assert filtered == []


# ── full seed bank (M6 "Done when") ──────────────────────────────────────────


def full_bank_run(settings) -> int:
    engine = get_engine(settings)
    with Session(engine) as s:
        import_seed(s)
        provider_svc.load_presets(s)
        cipher = Cipher.from_settings(settings)
        topics = [t.id for t in s.exec(select(Topic).order_by(Topic.id))]
        m = {x.display_name: x.id for x in s.exec(select(LlmModel))}
        run = planner.start_run(
            s, cipher,
            RunSettings(
                name="Full bank", topic_ids=topics, model_ids=[m["Mock Echo"], m["Mock Random"], m["Mock Refuser"]],
                languages=["en"], pairing="manual",
                pairs=[(m["Mock Echo"], m["Mock Random"]), (m["Mock Refuser"], m["Mock Random"])],
                judge_model_id=m["Mock Judge"], concurrency=32,
            ),
        )
        run_id = run.id
    settings.max_concurrency = 32
    settings.provider_concurrency = 32
    ex = Executor(engine, settings, cipher, retry_base_delay=0, idle_interval=0.01)
    asyncio.run(ex.run_until_idle(timeout=120))
    return run_id


def test_full_seed_bank_results_render_fast(client, migrated):
    run_id = full_bank_run(migrated)
    with Session(get_engine(migrated)) as s:
        n_responses = len(s.exec(select(Response)).all())
        assert len(s.exec(select(Topic)).all()) == 50
        assert n_responses == 35 * 3 + 15 * 2 * 4  # internal + criss-cross (2 pairs × 4)
        assert len(s.exec(select(Judgement)).all()) == n_responses
    client.get(f"/runs/{run_id}/results")  # warm up templates
    start = time.perf_counter()
    r = client.get(f"/runs/{run_id}/results")
    elapsed = time.perf_counter() - start
    assert r.status_code == 200 and elapsed < 1.0, f"results took {elapsed:.2f}s"
    html = r.text
    assert "Scorecard" in html and "Heatmap" in html
    assert html.count('<th scope="row"><a href="/topics/') == 50  # every topic has a heatmap row
    for name in ("Mock Echo", "Mock Random", "Mock Refuser"):
        assert name in html
    # Filters and sorting
    cn = client.get(f"/runs/{run_id}/results", params={"country": "CN", "mode": "internal"}, headers={"HX-Request": "true"})
    assert "<html" not in cn.text and cn.text.count('<th scope="row"><a href="/topics/') < 50
    assert client.get(f"/runs/{run_id}/results", params={"sort": "name", "dir": "asc"}).status_code == 200
    # Exports of the full bank
    start = time.perf_counter()
    csv_resp = client.get(f"/runs/{run_id}/export.csv")
    assert time.perf_counter() - start < 2
    rows = list(csv.DictReader(io.StringIO(csv_resp.content.decode("utf-8-sig"))))
    assert len(rows) == n_responses
    data = client.get(f"/runs/{run_id}/export.json").json()
    assert len(data["items"]) == 35 * 3 + 15 * 2
    assert sum(len(r["judgements"]) for i in data["items"] for r in i["responses"]) == n_responses


# ── exports ──────────────────────────────────────────────────────────────────


@respx.mock
def test_csv_utf8_and_safe(client, migrated):
    respx.post(DEEPSEEK).mock(return_value=httpx.Response(200, json=chat_ok("DeepSeek answer")))
    engine = get_engine(migrated)
    with Session(engine) as s:
        import_seed(s)
        provider_svc.load_presets(s)
        cipher = Cipher.from_settings(migrated)
        ds = s.exec(select(LlmModel).where(LlmModel.display_name == "DeepSeek")).one()
        ds.enabled = True
        s.add(ds)
        s.commit()
        credentials.save_credential(s, cipher, s.get(Provider, ds.provider_id), FAKE_KEY)
        mj = s.exec(select(LlmModel).where(LlmModel.display_name == "Mock Judge")).one()
        topic = s.exec(select(Topic).where(Topic.type == TopicType.INTERNAL)).first()
        run = planner.start_run(s, cipher, RunSettings(
            name="Ünïcode run 台湾", topic_ids=[topic.id], model_ids=[ds.id], languages=["en"], judge_model_id=mj.id,
        ))
        run_id = run.id
    asyncio.run(Executor(engine, migrated, cipher, retry_base_delay=0, idle_interval=0.01).run_until_idle())
    arabic = "الحرب العالمية الثانية"
    chinese = "天安门广场 1989年"
    with Session(engine) as s:
        resp = s.exec(select(Response)).one()
        resp.text = f"{arabic}\n{chinese}\nLine \"quoted\", with comma"
        s.add(resp)
        j = s.exec(select(Judgement)).one()
        j.rationale = "=HYPERLINK(\"http://evil\")"
        j.factual_issues = ["+1 claim", "@sum"]
        s.add(j)
        s.commit()

    r = client.get(f"/runs/{run_id}/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == f'attachment; filename="histbias-run-{run_id}-unicode-run.csv"'
    raw = r.content
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM: spreadsheets detect UTF-8
    text = raw.decode("utf-8-sig")
    assert "\r\n" in text
    (rec,) = list(csv.DictReader(io.StringIO(text)))
    assert list(rec) == export.CSV_COLUMNS
    assert rec["answer"] == f"{arabic}\n{chinese}\nLine \"quoted\", with comma"
    assert rec["run_name"] == "Ünïcode run 台湾" and rec["model"] == "DeepSeek" and rec["model_api_id"] == "deepseek-flash"
    assert rec["rationale"] == "'=HYPERLINK(\"http://evil\")"  # formula injection neutralised
    assert rec["factual_issues"] == "+1 claim | @sum".replace("+", "'+", 1)
    assert rec["collected_via"] == "API" and rec["judge"] == "Mock Judge" and rec["rubric_version"] == "v1"
    assert FAKE_KEY not in text

    j = client.get(f"/runs/{run_id}/export.json")
    assert j.headers["content-type"] == "application/json"
    assert arabic.encode() in j.content  # readable UTF-8, not \u escapes
    data = j.json()
    assert data["collected_via"] == "API" and data["limitations"]
    (item,) = data["items"]
    (resp_json,) = item["responses"]
    assert resp_json["text"].startswith(arabic) and resp_json["judgements"][0]["rationale"].startswith("=HYPERLINK")
    assert resp_json["raw"]["request"]["model"] == "deepseek-flash"
    assert FAKE_KEY not in j.text and "Authorization" not in j.text and "Bearer" not in j.text


def test_draft_has_no_results(client, migrated):
    with Session(get_engine(migrated)) as s:
        import_seed(s)
        provider_svc.load_presets(s)
        echo = s.exec(select(LlmModel).where(LlmModel.display_name == "Mock Echo")).one()
        topic = s.exec(select(Topic)).first()
        run = planner.save_draft(s, RunSettings(name="d", topic_ids=[topic.id], model_ids=[echo.id], languages=["en"]))
        run_id = run.id
    assert client.get(f"/runs/{run_id}/results").status_code == 409
    assert client.get(f"/runs/{run_id}/export.csv").status_code == 409
    assert client.get("/runs/999/results").status_code == 404


# ── duplicate run and compare ────────────────────────────────────────────────


def test_duplicate_run_and_compare(client, migrated):
    engine = get_engine(migrated)
    with Session(engine) as s:
        import_seed(s)
        provider_svc.load_presets(s)
        cipher = Cipher.from_settings(migrated)
        m = {x.display_name: x.id for x in s.exec(select(LlmModel))}
        topic = s.exec(select(Topic).where(Topic.type == TopicType.INTERNAL)).first()
        run = planner.start_run(s, cipher, RunSettings(
            name="Original", topic_ids=[topic.id], model_ids=[m["Mock Echo"], m["Mock Random"]],
            languages=["en"], repetitions=2, judge_model_id=m["Mock Judge"],
        ))
        run_id, topic_id = run.id, topic.id
    asyncio.run(Executor(engine, migrated, cipher, retry_base_delay=0, idle_interval=0.01).run_until_idle())

    page = client.get(f"/runs/{run_id}").text
    assert f'href="/runs/new?from_run={run_id}"' in page and 'href="/runs/{}/results"'.format(run_id) in page
    dup = client.get("/runs/new", params={"from_run": run_id}).text
    assert 'value="Original (copy)"' in dup and "Duplicating" in dup
    assert f'name="topic_ids" value="{topic_id}" data-group' in dup
    assert re.search(rf'<option value="{m["Mock Judge"]}"\s*selected', dup)
    assert 'name="repetitions" min="1" max="20" value="2"' in dup
    # Start the duplicate: a new run with the same settings.
    form = {
        "csrf_token": csrf_token(client), "name": "Original (copy)", "topic_ids": [str(topic_id)],
        "model_ids": [str(m["Mock Echo"]), str(m["Mock Random"])], "languages": ["en"], "internal_models": "all",
        "repetitions": "2", "temperature_mode": "model", "concurrency": "4", "judge_model_id": str(m["Mock Judge"]),
        "action": "start",
    }
    assert client.post("/runs/new", data=form, follow_redirects=False).status_code == 303
    with Session(engine) as s:
        runs = s.exec(select(Run).order_by(Run.id)).all()
        assert len(runs) == 2 and runs[0].config["settings"] | {"name": "x"} == runs[1].config["settings"] | {"name": "x"}
        echo_resp = s.exec(select(Response).where(Response.llm_model_id == m["Mock Echo"])).first()
    html = client.get(f"/responses/{echo_resp.id}").text
    assert "<strong>Compare</strong>: the same topic answered by 1 other model" in html
    assert "Mock Random" in html
