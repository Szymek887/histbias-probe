"""Planner + executor with the mock provider and respx (never real APIs)."""

import asyncio
import json

import httpx
import pytest
import respx
from sqlmodel import Session, select

from app.crypto import Cipher
from app.db import get_engine
from app.engine import planner
from app.engine.executor import Executor
from app.models import LlmModel, Provider, Response, Run, RunItem, RunItemStatus, RunStatus, Topic, TopicType
from app.schemas.runs import RunSettings
from app.services import credentials
from app.services import runs as run_svc

DEEPSEEK = "https://api.deepseek.com/chat/completions"
FAKE_KEY = "sk-test-0123456789abcdef"


def chat_ok(text: str = "DeepSeek says hello.") -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7},
    }


def model(session: Session, name: str) -> LlmModel:
    return session.exec(select(LlmModel).where(LlmModel.display_name == name)).one()


def internal_topics(session: Session, n: int = 6) -> list[Topic]:
    return list(session.exec(select(Topic).where(Topic.type == TopicType.INTERNAL).order_by(Topic.id).limit(n)))


def run_settings(topics, models, **kw) -> RunSettings:
    return RunSettings(
        name=kw.pop("name", "Test run"),
        topic_ids=[t.id for t in topics],
        model_ids=[m.id for m in models],
        languages=kw.pop("languages", ["en"]),
        **kw,
    )


def enable_deepseek(session: Session, cipher: Cipher) -> LlmModel:
    ds = model(session, "DeepSeek")
    ds.enabled = True
    session.add(ds)
    session.commit()
    credentials.save_credential(session, cipher, session.get(Provider, ds.provider_id), FAKE_KEY)
    return ds


def executor(settings, cipher, **kw) -> Executor:
    kw.setdefault("retry_base_delay", 0)
    kw.setdefault("idle_interval", 0.01)
    return Executor(get_engine(settings), settings, cipher, **kw)


def items_of(session: Session, run: Run) -> list[RunItem]:
    session.expire_all()
    return list(session.exec(select(RunItem).where(RunItem.run_id == run.id).order_by(RunItem.id)))


def responses_of(session: Session, run: Run) -> list[Response]:
    session.expire_all()
    return list(
        session.exec(select(Response).join(RunItem, RunItem.id == Response.run_item_id).where(RunItem.run_id == run.id))
    )


def set_mock_delay(session: Session, ms: int) -> None:
    mock = session.exec(select(Provider).where(Provider.kind == "mock")).one()
    mock.extra = {**mock.extra, "delay_ms": ms}
    session.add(mock)
    session.commit()


# ── planner ──────────────────────────────────────────────────────────────────


def test_plan_counts_and_notes(bank, cipher):
    topics = internal_topics(bank)
    models = [model(bank, "Mock Echo"), model(bank, "Mock Random")]
    plan = planner.build_plan(bank, cipher, run_settings(topics, models))
    assert plan.calls == 12 and not plan.problems
    plan = planner.build_plan(bank, cipher, run_settings(topics, models, repetitions=3, languages=["en", "native"]))
    assert plan.calls == 36  # the seed has English prompts only
    assert any("no native-language prompt" in n for n in plan.notes)


def test_plan_excludes_models_without_key(bank, cipher):
    ds = model(bank, "DeepSeek")
    plan = planner.build_plan(bank, cipher, run_settings(internal_topics(bank, 1), [ds, model(bank, "Mock Echo")]))
    assert [(m.display_name, why) for m, why in plan.excluded_models] == [("DeepSeek", "disabled")]
    ds.enabled = True
    bank.add(ds)
    bank.commit()
    plan = planner.build_plan(bank, cipher, run_settings(internal_topics(bank, 1), [ds]))
    assert plan.excluded_models[0][1] == "no API key"
    assert "choose at least one model that has an API key" in plan.problems


def test_plan_skips_unpaired_criss_cross_and_home_mode(bank, cipher):
    cc = bank.exec(select(Topic).where(Topic.type == TopicType.CRISS_CROSS)).first()
    topics = [*internal_topics(bank, 2), cc]
    plan = planner.build_plan(bank, cipher, run_settings(topics, [model(bank, "Mock Echo")]))
    assert plan.calls == 2
    # Mock models have no country, so automatic pairing finds no pair.
    assert [(t.id, why) for t, why in plan.skipped_topics] == [(cc.id, "no selected model from CN and ARAB")]
    # Mock models have no country, so "home models only" leaves nothing to ask.
    plan = planner.build_plan(bank, cipher, run_settings(topics[:2], [model(bank, "Mock Echo")], internal_models="home"))
    assert plan.calls == 0 and plan.problems


def test_draft_then_start_freezes_snapshot(bank, cipher):
    topics = internal_topics(bank, 2)
    echo = model(bank, "Mock Echo")
    s = run_settings(topics, [echo], temperature=0.3)
    run = planner.save_draft(bank, s)
    assert run.status == RunStatus.DRAFT and items_of(bank, run) == []
    with pytest.raises(planner.PlanError):
        planner.save_draft(bank, s, planner.start_run(bank, cipher, s, run))
    bank.refresh(run)
    assert run.status == RunStatus.QUEUED
    snap = run.config["models"][str(echo.id)]
    assert snap == snap | {"model_id": "mock-echo", "display_name": "Mock Echo", "temperature": 0.3}
    items = items_of(bank, run)
    assert len(items) == 2 and items[0].prompt_text.startswith("Tell me about")
    assert run.config["settings"] == s.model_dump()


# ── executor ─────────────────────────────────────────────────────────────────


@respx.mock
async def test_mock_and_real_model_run_completes(bank, cipher, migrated):
    """M3 "Done when": 6 topics × 2 mock models + 1 real model (DeepSeek via respx)."""
    route = respx.post(DEEPSEEK).mock(return_value=httpx.Response(200, json=chat_ok()))
    ds = enable_deepseek(bank, cipher)
    echo, refuser = model(bank, "Mock Echo"), model(bank, "Mock Refuser")
    run = planner.start_run(bank, cipher, run_settings(internal_topics(bank), [echo, refuser, ds]))

    # Editing the model after the start must not change what the run calls.
    ds.model_id = "changed-later"
    bank.add(ds)
    bank.commit()

    await executor(migrated, cipher).run_until_idle()
    bank.refresh(run)
    assert run.status == RunStatus.COMPLETED and run.started_at and run.finished_at
    items = items_of(bank, run)
    assert len(items) == 18 and {i.status for i in items} == {RunItemStatus.ANSWERED}
    responses = responses_of(bank, run)
    assert len(responses) == 18
    by_model = {}
    for r in responses:
        by_model.setdefault(r.display_name_snapshot, []).append(r)
    assert all(r.refusal_detected for r in by_model["Mock Refuser"])
    assert not any(r.refusal_detected for r in by_model["Mock Echo"])
    assert by_model["Mock Echo"][0].text.startswith("You asked: Tell me about")
    assert {r.model_id_snapshot for r in by_model["DeepSeek"]} == {"deepseek-flash"}
    assert route.call_count == 6
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "deepseek-flash" and sent["messages"][0]["role"] == "user"
    # Stored bodies only: the key is nowhere in the stored responses.
    assert FAKE_KEY not in json.dumps([r.model_dump(mode="json") for r in responses])
    assert by_model["DeepSeek"][0].raw["request"]["model"] == "deepseek-flash"


@respx.mock
async def test_rate_limit_is_retried(bank, cipher, migrated):
    route = respx.post(DEEPSEEK).mock(
        side_effect=[
            httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "0"}),
            httpx.Response(429, json={"error": {"message": "slow down"}}),
            httpx.Response(200, json=chat_ok()),
        ]
    )
    ds = enable_deepseek(bank, cipher)
    run = planner.start_run(bank, cipher, run_settings(internal_topics(bank, 1), [ds]))
    await executor(migrated, cipher).run_until_idle()
    (item,) = items_of(bank, run)
    assert item.status == RunItemStatus.ANSWERED and item.attempts == 3 and item.error is None
    assert route.call_count == 3
    bank.refresh(run)
    assert run.status == RunStatus.COMPLETED


@respx.mock
async def test_transient_errors_give_up_then_retry_failed(bank, cipher, migrated):
    respx.post(DEEPSEEK).mock(return_value=httpx.Response(503, text="overloaded"))
    ds = enable_deepseek(bank, cipher)
    run = planner.start_run(bank, cipher, run_settings(internal_topics(bank, 1), [ds]))
    ex = executor(migrated, cipher)
    await ex.run_until_idle()
    (item,) = items_of(bank, run)
    assert item.status == RunItemStatus.ERROR and item.attempts == 3
    assert "gave up after 3 attempts" in item.error
    bank.refresh(run)
    assert run.status == RunStatus.FAILED

    respx.post(DEEPSEEK).mock(return_value=httpx.Response(200, json=chat_ok()))
    assert run_svc.retry_failed(bank, run) == 1
    await ex.run_until_idle()
    (item,) = items_of(bank, run)
    bank.refresh(run)
    assert item.status == RunItemStatus.ANSWERED and run.status == RunStatus.COMPLETED


@respx.mock
async def test_auth_error_is_not_retried(bank, cipher, migrated):
    route = respx.post(DEEPSEEK).mock(return_value=httpx.Response(401, json={"error": {"message": "bad key"}}))
    ds = enable_deepseek(bank, cipher)
    run = planner.start_run(bank, cipher, run_settings(internal_topics(bank, 1), [ds]))
    await executor(migrated, cipher).run_until_idle()
    (item,) = items_of(bank, run)
    assert item.status == RunItemStatus.ERROR and item.attempts == 1 and "401" in item.error
    assert FAKE_KEY not in item.error
    assert route.call_count == 1


@respx.mock
async def test_content_block_is_stored_as_refusal(bank, cipher, migrated):
    respx.post(DEEPSEEK).mock(
        return_value=httpx.Response(400, json={"error": {"message": "Content Exists Risk", "type": "content_filter"}})
    )
    ds = enable_deepseek(bank, cipher)
    run = planner.start_run(bank, cipher, run_settings(internal_topics(bank, 1), [ds]))
    await executor(migrated, cipher).run_until_idle()
    (item,) = items_of(bank, run)
    (resp,) = responses_of(bank, run)
    assert item.status == RunItemStatus.ANSWERED
    assert resp.refusal_detected and resp.text == "" and "content blocked" in resp.error


async def test_missing_key_at_run_time_is_an_item_error(bank, cipher, migrated):
    ds = enable_deepseek(bank, cipher)
    run = planner.start_run(bank, cipher, run_settings(internal_topics(bank, 1), [ds]))
    credentials.delete_credential(bank, bank.get(Provider, ds.provider_id))
    await executor(migrated, cipher).run_until_idle()
    (item,) = items_of(bank, run)
    assert item.status == RunItemStatus.ERROR and "no API key" in item.error


async def test_resume_after_restart(bank, cipher, migrated):
    """Kill the worker with calls in flight; a new worker finishes the run with no duplicates."""
    set_mock_delay(bank, 300)
    topics = internal_topics(bank)
    run = planner.start_run(bank, cipher, run_settings(topics, [model(bank, "Mock Echo"), model(bank, "Mock Random")]))
    first = executor(migrated, cipher)
    first.tick()  # claims 2 items (provider limit) and starts the calls
    await asyncio.sleep(0.05)
    await first.stop()  # like killing the server mid-call
    statuses = [i.status for i in items_of(bank, run)]
    assert statuses.count(RunItemStatus.RUNNING) == 2

    set_mock_delay(bank, 0)
    second = executor(migrated, cipher)
    assert second.recover() == 2
    await second.run_until_idle()
    bank.refresh(run)
    assert run.status == RunStatus.COMPLETED
    assert {i.status for i in items_of(bank, run)} == {RunItemStatus.ANSWERED}
    assert len(responses_of(bank, run)) == 12  # exactly one response per item


async def test_concurrency_limits(bank, cipher, migrated):
    set_mock_delay(bank, 200)
    models = [model(bank, "Mock Echo"), model(bank, "Mock Random")]
    run = planner.start_run(bank, cipher, run_settings(internal_topics(bank), models, concurrency=4))
    ex = executor(migrated, cipher)
    assert ex.tick() == 2  # one provider, default per-provider limit 2
    await ex.stop()

    mock = bank.exec(select(Provider).where(Provider.kind == "mock")).one()
    mock.extra = {**mock.extra, "max_concurrency": 10}
    bank.add(mock)
    bank.commit()
    ex = executor(migrated, cipher)
    ex.recover()
    assert ex.tick() == 4  # now the run's own limit
    await ex.stop()

    migrated.max_concurrency = 3
    ex = executor(migrated, cipher)
    ex.recover()
    assert ex.tick() == 3  # global limit
    await ex.stop()
    assert run.id


async def test_pause_resume_cancel(bank, cipher, migrated):
    models = [model(bank, "Mock Echo")]
    run = planner.start_run(bank, cipher, run_settings(internal_topics(bank), models))
    ex = executor(migrated, cipher)
    run_svc.pause(bank, run)
    assert ex.tick() == 0
    assert run.status == RunStatus.PAUSED
    with pytest.raises(run_svc.RunActionError):
        run_svc.pause(bank, run)
    run_svc.resume(bank, run)
    await ex.run_until_idle()
    bank.refresh(run)
    assert run.status == RunStatus.COMPLETED

    run2 = planner.start_run(bank, cipher, run_settings(internal_topics(bank), models))
    run_svc.cancel(bank, run2)
    await ex.run_until_idle()
    bank.refresh(run2)
    assert run2.status == RunStatus.CANCELLED and run2.finished_at
    assert {i.status for i in items_of(bank, run2)} == {RunItemStatus.SKIPPED}
    with pytest.raises(run_svc.RunActionError):
        run_svc.resume(bank, run2)


async def test_delete_run_cascades(bank, cipher, migrated):
    run = planner.start_run(bank, cipher, run_settings(internal_topics(bank, 2), [model(bank, "Mock Echo")]))
    with pytest.raises(run_svc.RunActionError):
        run_svc.delete_run(bank, run)  # still queued
    await executor(migrated, cipher).run_until_idle()
    bank.refresh(run)
    run_svc.delete_run(bank, run)
    bank.expire_all()
    assert bank.exec(select(RunItem)).all() == [] and bank.exec(select(Response)).all() == []
