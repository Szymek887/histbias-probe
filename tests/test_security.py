"""M7 security checklist (docs/07-security.md, docs/security-checklist.md).

Items already covered elsewhere are listed in docs/security-checklist.md; this file adds the
cross-cutting checks.
"""

import logging
import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select
from typer.testing import CliRunner

from app.cli import app as cli
from app.config import Settings
from app.crypto import Cipher
from app.db import get_engine
from app.main import create_app
from app.models import LlmModel, Topic, TopicType
from app.services import providers as provider_svc
from app.services.topic_io import import_seed
from tests.conftest import csrf_token
from tests.test_run_engine import FAKE_KEY, enable_deepseek

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "web" / "templates"


def test_no_template_marks_anything_safe():
    """Model output goes through the sanitizing `markdown` filter only; nothing is `|safe`."""
    offenders = [
        f"{p.relative_to(TEMPLATES)}:{n}"
        for p in TEMPLATES.rglob("*.html")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\|\s*safe\b|Markup\(|autoescape\s+false", line)
    ]
    assert offenders == []


def test_model_answers_use_the_markdown_filter():
    """In the run/result templates, answer text (`<x>.text`) is always rendered with `|markdown`."""
    for folder in ("runs", "results"):
        for p in (TEMPLATES / folder).rglob("*.html"):
            for expr in re.findall(r"\{\{(.*?)\}\}", p.read_text(encoding="utf-8")):
                if re.search(r"\w\.text\b", expr):
                    assert "|markdown" in expr.replace(" ", ""), f"{p.name}: {expr.strip()}"


def test_server_error_page_shows_no_traceback(migrated, caplog):
    app = create_app(migrated)

    @app.get("/boom")
    def boom():
        raise RuntimeError(f"failed with Authorization: Bearer {FAKE_KEY}")

    with TestClient(app, raise_server_exceptions=False) as c, caplog.at_level(logging.ERROR):
        r = c.get("/boom")
    assert r.status_code == 500 and "Error 500" in r.text
    assert "Traceback" not in r.text and "RuntimeError" not in r.text and FAKE_KEY not in r.text
    # Logged with the traceback, through the redacting formatter.
    from app.log import RedactingFormatter

    logged = "\n".join(RedactingFormatter().format(rec) for rec in caplog.records)
    assert "RuntimeError" in logged and FAKE_KEY not in logged


def test_no_api_docs_and_no_cors(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
    r = client.get("/", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    r = client.options("/runs/new", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


def test_run_actions_check_origin_and_token(client, migrated):
    with Session(get_engine(migrated)) as s:
        import_seed(s)
        provider_svc.load_presets(s)
        topic = s.exec(select(Topic).where(Topic.type == TopicType.INTERNAL)).first().id
        echo = s.exec(select(LlmModel).where(LlmModel.display_name == "Mock Echo")).one().id
    data = {"csrf_token": csrf_token(client), "name": "x", "topic_ids": [str(topic)], "model_ids": [str(echo)],
            "languages": ["en"], "internal_models": "all", "repetitions": "1", "temperature_mode": "model",
            "concurrency": "1", "action": "start"}  # fmt: skip
    assert client.post("/runs/new", data=data, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/runs/new", data={**data, "csrf_token": "x" * 40}).status_code == 403
    assert client.post("/runs/new", data=data, follow_redirects=False).status_code == 303
    assert client.post("/runs/1/delete", headers={"Origin": "http://localhost:9999"}).status_code == 403


def test_stored_key_never_appears_in_pages(client, migrated):
    with Session(get_engine(migrated)) as s:
        import_seed(s)
        provider_svc.load_presets(s)
        ds = enable_deepseek(s, Cipher.from_settings(migrated))
        ds_id, provider_id = ds.id, ds.provider_id
    for path in ("/settings/keys", "/models", f"/providers/{provider_id}/edit", f"/models/{ds_id}/edit", "/runs/new"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert FAKE_KEY not in page.text and FAKE_KEY[-8:] not in page.text
    assert "cdef" in client.get("/settings/keys").text  # only the last 4 characters


def test_start_asks_before_paid_calls(client, migrated):
    with Session(get_engine(migrated)) as s:
        import_seed(s)
        provider_svc.load_presets(s)
        ds = enable_deepseek(s, Cipher.from_settings(migrated)).id
        topics = [t.id for t in s.exec(select(Topic).where(Topic.type == TopicType.INTERNAL).limit(25))]
        echo = s.exec(select(LlmModel).where(LlmModel.display_name == "Mock Echo")).one().id
    base = {"csrf_token": csrf_token(client), "name": "x", "topic_ids": [str(t) for t in topics], "languages": ["en"],
            "internal_models": "all", "repetitions": "1", "temperature_mode": "model", "concurrency": "1"}  # fmt: skip
    paid = client.post("/runs/new/summary", data={**base, "model_ids": [str(ds)]}).text
    assert "25 calls to paid APIs" in paid and "return confirm(" in paid
    free = client.post("/runs/new/summary", data={**base, "model_ids": [str(echo)]}).text
    assert "return confirm(" not in free


def test_bind_warnings():
    assert Settings(host="127.0.0.1").bind_warning() is None
    assert "anyone who can reach" in Settings(host="0.0.0.0").bind_warning()
    assert "127.0.0.1:8000:8000" in Settings(host="0.0.0.0", in_container=True).bind_warning()


def test_serve_init_creates_a_missing_database(settings, monkeypatch):
    called = []
    monkeypatch.setattr("app.cli.uvicorn.run", lambda *a, **k: called.append(k))
    assert not settings.db_path.exists()
    r = CliRunner().invoke(cli, ["serve", "--init"])
    assert r.exit_code == 0, r.output
    assert settings.db_path.exists() and "50 added" in r.output and called
    r = CliRunner().invoke(cli, ["serve", "--init"])  # second start: no re-seeding
    assert r.exit_code == 0 and "added" not in r.output


def test_gitignore_and_dockerignore_keep_secrets_out():
    root = Path(__file__).resolve().parents[1]
    for name in (".gitignore", ".dockerignore"):
        lines = {line.strip() for line in (root / name).read_text().splitlines()}
        missing = {"data/", ".env", "*.db", "secret.key"} - lines
        assert not missing, f"{name} lacks {missing}"


def test_gigachat_form_explains_the_ca(client, migrated):
    from app.models import Provider

    with Session(get_engine(migrated)) as s:
        provider_svc.load_presets(s)
        gc = s.exec(select(Provider).where(Provider.kind == "gigachat")).one().id
    assert "Russian Trusted Root CA" in client.get(f"/providers/{gc}/edit").text


def test_judge_prompt_never_names_the_evaluated_model(migrated):
    """D10: the judge sees the answer as quoted data, without the tested model's name."""
    import asyncio

    from app.engine import planner
    from app.engine.executor import Executor
    from app.models import Judgement
    from app.schemas.runs import RunSettings

    engine = get_engine(migrated)
    cipher = Cipher.from_settings(migrated)
    with Session(engine) as s:
        import_seed(s)
        provider_svc.load_presets(s)
        m = {x.display_name: x.id for x in s.exec(select(LlmModel))}
        topics = [t.id for t in s.exec(select(Topic).where(Topic.type == TopicType.INTERNAL).limit(2))]
        planner.start_run(s, cipher, RunSettings(
            name="blind", topic_ids=topics, model_ids=[m["Mock Echo"], m["Mock Refuser"]], languages=["en"],
            judge_model_id=m["Mock Judge"],
        ))  # fmt: skip
    asyncio.run(Executor(engine, migrated, cipher, retry_base_delay=0, idle_interval=0.01).run_until_idle())
    with Session(engine) as s:
        judgements = s.exec(select(Judgement)).all()
    assert len(judgements) == 4
    for j in judgements:
        for name in ("Mock Echo", "Mock Refuser", "mock-echo", "mock-refuse"):
            assert name not in j.judge_prompt
