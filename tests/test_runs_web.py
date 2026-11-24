import asyncio
import re

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.crypto import Cipher
from app.db import get_engine
from app.engine.executor import Executor
from app.main import create_app
from app.models import LlmModel, Response, Run, RunItem, RunStatus, Topic, TopicType
from app.services import providers as provider_svc
from app.services.topic_io import import_seed
from tests.conftest import csrf_token


def setup_bank(settings) -> tuple[list[int], dict[str, int]]:
    with Session(get_engine(settings)) as s:
        import_seed(s)
        provider_svc.load_presets(s)
        topics = s.exec(select(Topic.id).where(Topic.type == TopicType.INTERNAL).order_by(Topic.id).limit(6)).all()
        models = {m.display_name: m.id for m in s.exec(select(LlmModel))}
    return list(topics), models


def form(client: TestClient, topics, models, **extra) -> dict:
    return {
        "csrf_token": csrf_token(client),
        "name": "Web run",
        "topic_ids": [str(t) for t in topics],
        "model_ids": [str(m) for m in models],
        "languages": ["en"],
        "internal_models": "all",
        "repetitions": "1",
        "temperature_mode": "model",
        "concurrency": "4",
        **extra,
    }


def drive(settings) -> None:
    ex = Executor(get_engine(settings), settings, Cipher.from_settings(settings), retry_base_delay=0, idle_interval=0.01)
    asyncio.run(ex.run_until_idle())


def test_wizard_preselects_topics_and_lists_models(client, migrated):
    topics, models = setup_bank(migrated)
    page = client.get("/runs/new", params={"ids": topics[:2]})
    assert page.status_code == 200
    html = page.text
    for t in topics[:2]:
        assert re.search(rf'name="topic_ids" value="{t}"[^>]*checked', html)
    assert not re.search(rf'name="topic_ids" value="{topics[2]}"[^>]*checked', html)
    assert "Mock Echo" in html and "Summary: 8 model calls" in html  # 2 topics × 4 mock models
    assert 'data-group="topics-cc"' in html  # criss-cross topics are selectable


def test_summary_partial(client, migrated):
    topics, models = setup_bank(migrated)
    data = form(client, topics, [models["Mock Echo"], models["Mock Random"]], repetitions="2")
    r = client.post("/runs/new/summary", data=data, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "<html" not in r.text
    assert "24 model calls" in r.text
    data["topic_ids"] = []
    r = client.post("/runs/new/summary", data=data)
    assert "Choose at least one topic." in r.text and "disabled" in r.text


def test_start_run_and_watch_progress(client, migrated):
    topics, models = setup_bank(migrated)
    r = client.post("/runs/new", data=form(client, topics, [models["Mock Echo"], models["Mock Refuser"]], action="start"),
                    follow_redirects=False)
    assert r.status_code == 303
    url = r.headers["location"].split("?")[0]
    run_id = int(url.rsplit("/", 1)[1])
    page = client.get(url)
    assert "queued" in page.text and 'hx-trigger="every 2s"' in page.text and "Pause" in page.text

    drive(migrated)
    progress = client.get(f"{url}/progress")
    assert "completed" in progress.text and "12/12 answered" in progress.text
    assert "every 2s" not in progress.text  # polling stops
    assert "refusal?" in progress.text

    with Session(get_engine(migrated)) as s:
        resp = s.exec(select(Response).join(RunItem).where(RunItem.run_id == run_id)).first()
    detail = client.get(f"/responses/{resp.id}")
    assert detail.status_code == 200 and "Raw request and response bodies" in detail.text
    assert client.get("/").text.count(f'href="/runs/{run_id}"') == 1


def test_model_output_is_sanitized(client, migrated):
    topics, models = setup_bank(migrated)
    client.post("/runs/new", data=form(client, topics[:1], [models["Mock Echo"]], action="start"))
    drive(migrated)
    with Session(get_engine(migrated)) as s:
        resp = s.exec(select(Response)).one()
        resp.text = "<script>alert(1)</script> **bold** 台湾 <img src=x onerror=alert(1)>"
        resp.raw = {"response": {"text": "<script>x</script> 台湾"}}
        s.add(resp)
        s.commit()
        rid = resp.id
    html = client.get(f"/responses/{rid}").text
    assert "<script>alert" not in html and "<img" not in html and "<strong>bold</strong>" in html
    assert "台湾" in html and "\\u53f0" not in html  # raw JSON keeps readable UTF-8
    assert "<script>x" not in html


def test_draft_edit_start_delete(client, migrated):
    topics, models = setup_bank(migrated)
    r = client.post("/runs/new", data=form(client, topics, [models["Mock Echo"]], action="draft"), follow_redirects=False)
    url = r.headers["location"].split("?")[0]
    page = client.get(url)
    assert "Draft" in page.text and "6 model calls" in page.text
    edit = client.get(f"{url}/edit")
    run_id = url.rsplit("/", 1)[1]
    assert f'name="draft_id" value="{run_id}"' in edit.text
    # Save the draft again with 2 topics.
    client.post("/runs/new", data=form(client, topics[:2], [models["Mock Echo"]], action="draft", draft_id=run_id))
    with Session(get_engine(migrated)) as s:
        assert len(s.exec(select(Run)).all()) == 1
        assert s.get(Run, int(run_id)).config["settings"]["topic_ids"] == topics[:2]
    r = client.post(f"{url}/start", data={"csrf_token": csrf_token(client)}, follow_redirects=False)
    assert r.headers["location"].endswith("notice=started")
    assert client.get(f"{url}/edit").status_code == 409
    # Can't delete while queued; can after cancel.
    r = client.post(f"{url}/delete", data={"csrf_token": csrf_token(client)})
    assert "pause or cancel" in r.text
    client.post(f"{url}/cancel", data={"csrf_token": csrf_token(client)})
    r = client.post(f"{url}/delete", data={"csrf_token": csrf_token(client)}, follow_redirects=False)
    assert r.headers["location"] == "/?notice=deleted"
    with Session(get_engine(migrated)) as s:
        assert s.exec(select(Run)).all() == []


def test_start_rejected_without_usable_models(client, migrated):
    topics, models = setup_bank(migrated)
    r = client.post("/runs/new", data=form(client, topics, [models["DeepSeek"]], action="start"))
    assert r.status_code == 422
    assert "Choose at least one model that has an API key" in r.text


def test_actions_require_csrf(client, migrated):
    topics, models = setup_bank(migrated)
    client.post("/runs/new", data=form(client, topics, [models["Mock Echo"]], action="start"))
    r = client.post("/runs/1/pause")
    assert r.status_code == 403
    with Session(get_engine(migrated)) as s:
        assert s.get(Run, 1).status == RunStatus.QUEUED


def test_background_worker_runs_in_the_app(migrated):
    """With the worker on (the default outside tests), a started run completes by itself."""
    migrated.run_worker = True
    topics, models = setup_bank(migrated)
    with TestClient(create_app(migrated)) as c:
        c.post("/runs/new", data=form(c, topics, [models["Mock Echo"]], action="start"))
        for _ in range(200):
            if "completed" in c.get("/runs/1/progress").text:
                break
            asyncio.run(asyncio.sleep(0.02))
        assert "6/6 answered" in c.get("/runs/1/progress").text


def test_criss_cross_wizard_and_grid(client, migrated):
    _, models = setup_bank(migrated)
    with Session(get_engine(migrated)) as s:
        taiwan = s.exec(select(Topic).where(Topic.slug == "china-vs-usa-taiwan")).one().id
    echo, rnd = models["Mock Echo"], models["Mock Random"]
    row = client.get("/runs/new/pair-row")
    assert row.status_code == 200 and 'name="pair_a"' in row.text and "Mock Echo" in row.text

    data = form(client, [taiwan], [echo, rnd], pairing="manual", pair_a=[str(echo), ""], pair_b=[str(rnd), ""])
    summary = client.post("/runs/new/summary", data=data)
    assert "4 model calls" in summary.text and "Taiwan → Mock Echo × Mock Random" in summary.text
    auto = client.post("/runs/new/summary", data={**data, "pairing": "auto"})
    assert "no selected model from CN and US" in auto.text

    r = client.post("/runs/new", data={**data, "action": "start"}, follow_redirects=False)
    url = r.headers["location"].split("?")[0]
    drive(migrated)
    live = client.get(f"{url}/progress").text
    assert "Mock Echo × Mock Random" in live and "4/4" in live
    with Session(get_engine(migrated)) as s:
        a2 = s.exec(select(Response).where(Response.turn == 2, Response.llm_model_id == echo)).one()
    page = client.get(f"/responses/{a2.id}").text
    for label in ("A1", "B1", "A2", "B2"):
        assert f"<strong>{label}</strong>" in page
    assert "Selected answer: A2 (Mock Echo)" in page and "Conversation sent" in page
    assert "Manual" in client.get(url).text or "manual" in client.get(url).text


def test_judge_in_wizard_and_verdict_page(client, migrated):
    from app.models import Judgement

    topics, models = setup_bank(migrated)
    echo, mj = models["Mock Echo"], models["Mock Judge"]
    page = client.get("/runs/new").text
    assert 'name="judge_model_id"' in page and "No judge – collect answers only" in page
    data = form(client, topics[:2], [echo, mj], judge_model_id=str(mj))
    summary = client.post("/runs/new/summary", data=data).text
    assert "about 4 judge calls" in summary and "also a tested model" in summary

    r = client.post("/runs/new", data=form(client, topics[:2], [echo], judge_model_id=str(mj), action="start"),
                    follow_redirects=False)
    url = r.headers["location"].split("?")[0]
    drive(migrated)
    live = client.get(f"{url}/progress").text
    assert "2 judged" in live and 'class="score score-' in live and "<th>Score</th>" in live
    assert "Mock Judge" in client.get("/").text and "rubric v1" in client.get(url).text

    with Session(get_engine(migrated)) as s:
        j = s.exec(select(Judgement)).first()
        j.rationale = "<script>alert(1)</script> biased"
        j.factual_issues = ["<img src=x onerror=alert(1)>"]
        s.add(j)
        s.commit()
        rid = j.response_id
    html = client.get(f"/responses/{rid}").text
    assert "Judge verdict" in html and "Judge prompt" in html and "Raw judge output" in html
    assert "<script>alert" not in html and "<img src=x" not in html and "&lt;script&gt;" in html
    assert "## Model answer to evaluate" in html  # the stored prompt is shown


def test_judge_without_key_blocks_start(client, migrated):
    topics, models = setup_bank(migrated)
    r = client.post("/runs/new", data=form(client, topics[:1], [models["Mock Echo"]], judge_model_id=str(models["DeepSeek"]), action="start"))
    assert r.status_code == 422 and "be used (disabled)" in r.text
