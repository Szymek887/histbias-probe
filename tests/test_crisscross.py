"""Criss-cross mode (M4): pairing, the two-turn conversation, partial retries, resume."""

import asyncio
import json

import httpx
import pytest
import respx
from sqlmodel import select

from app.engine import crisscross, planner
from app.models import Provider, Response, RunItem, RunItemStatus, RunStatus, Topic
from app.providers.base import ChatMessage
from app.services import credentials
from tests.test_run_engine import (
    DEEPSEEK,
    FAKE_KEY,
    chat_ok,
    executor,
    items_of,
    model,
    responses_of,
    run_settings,
    set_mock_delay,
)

XAI = "https://api.x.ai/v1/chat/completions"
# Markdown, non-Latin text and odd whitespace: turn 2 must carry it byte for byte.
B1_TEXT = "  **Taiwan** is a self-governing island.\n\n| a | b |\n|---|---|\n台湾 — «quote»  \n"
A1_TEXT = "Taiwan is an inalienable part of China.\n"


def taiwan(session) -> Topic:
    return session.exec(select(Topic).where(Topic.slug == "china-vs-usa-taiwan")).one()


def enable(session, cipher, name: str):
    m = model(session, name)
    m.enabled = True
    session.add(m)
    session.commit()
    provider = session.get(Provider, m.provider_id)
    if credentials.get_credential(session, provider) is None:
        credentials.save_credential(session, cipher, provider, FAKE_KEY)
    return m


def by_label(item: RunItem, responses: list[Response]) -> dict[str, Response]:
    side = lambda r: "A" if r.llm_model_id == item.model_a_id else "B"  # noqa: E731
    return {f"{side(r)}{r.turn}": r for r in responses}


def responder(text1: str, text2: str):
    """A respx side effect: turn 1 (one user message) → text1, turn 2 → text2."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        turn2 = sum(1 for m in body["messages"] if m["role"] == "user") == 2
        return httpx.Response(200, json=chat_ok(text2 if turn2 else text1))

    return handler


def test_follow_up_message_and_messages():
    msg = crisscross.follow_up_message("This is {other_model}'s response. What do you think about it?", "Grok", B1_TEXT)
    assert msg == "This is Grok's response. What do you think about it?\n\n---\n" + B1_TEXT
    msgs = crisscross.turn2_messages("Express yourself about Taiwan", A1_TEXT, "This is {other_model}'s response.", "Grok", B1_TEXT, "sys")
    assert [m.role for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[2] == ChatMessage(role="assistant", content=A1_TEXT)
    assert msgs[3].content.endswith("\n---\n" + B1_TEXT)


def test_turn2_skip_reasons():
    ok, blocked, empty = crisscross.TurnOne("x", False), crisscross.TurnOne("", True), crisscross.TurnOne(" ", False)
    assert crisscross.turn2_skip_reason("A", ok, "B", ok) is None
    assert "blocked" in crisscross.turn2_skip_reason("A", blocked, "B", ok)
    assert "nothing to comment on" in crisscross.turn2_skip_reason("A", ok, "B", empty)


def test_auto_pairing_preview(bank, cipher):
    ds, pro, grok = enable(bank, cipher, "DeepSeek"), enable(bank, cipher, "DeepSeek V4 Pro"), enable(bank, cipher, "Grok")
    echo = model(bank, "Mock Echo")
    plan = planner.build_plan(bank, cipher, run_settings([taiwan(bank)], [ds, pro, grok, echo]))
    ((topic, pairs),) = plan.pairings
    assert [(a.display_name, b.display_name) for a, b in pairs] == [("DeepSeek", "Grok"), ("DeepSeek V4 Pro", "Grok")]
    assert plan.calls == 8 and plan.criss_cross_items == 2
    # A topic whose second country has no selected model is skipped with the reason.
    plan = planner.build_plan(bank, cipher, run_settings([taiwan(bank)], [ds]))
    assert plan.skipped_topics[0][1] == "no selected model from US" and plan.problems


def test_manual_pairs(bank, cipher):
    echo, rnd, refuser = model(bank, "Mock Echo"), model(bank, "Mock Random"), model(bank, "Mock Refuser")
    s = run_settings([taiwan(bank)], [echo, rnd], pairing="manual", pairs=[(echo.id, rnd.id), (echo.id, refuser.id)])
    plan = planner.build_plan(bank, cipher, s)
    assert [(a.id, b.id) for a, b in plan.pairings[0][1]] == [(echo.id, rnd.id)]
    assert any("Mock Refuser is not selected" in n for n in plan.notes)
    with pytest.raises(ValueError):
        run_settings([taiwan(bank)], [echo], pairing="manual", pairs=[(echo.id, echo.id)])
    plan = planner.build_plan(bank, cipher, run_settings([taiwan(bank)], [echo], pairing="manual"))
    assert plan.skipped_topics[0][1] == "no pairs chosen"


@respx.mock
async def test_taiwan_deepseek_x_grok(bank, cipher, migrated):
    """M4 "Done when": A1, B1, A2, B2, and turn 2 carries the partner's answer exactly."""
    ds_route = respx.post(DEEPSEEK).mock(side_effect=responder(A1_TEXT, "A2: I disagree with Grok."))
    xai_route = respx.post(XAI).mock(side_effect=responder(B1_TEXT, "B2: DeepSeek repeats the official line."))
    ds, grok = enable(bank, cipher, "DeepSeek"), enable(bank, cipher, "Grok")
    run = planner.start_run(bank, cipher, run_settings([taiwan(bank)], [ds, grok]))
    await executor(migrated, cipher).run_until_idle()

    (item,) = items_of(bank, run)
    assert item.mode == "criss_cross" and item.status == RunItemStatus.ANSWERED and item.error is None
    assert (item.model_a_id, item.model_b_id) == (ds.id, grok.id)
    r = by_label(item, responses_of(bank, run))
    assert sorted(r) == ["A1", "A2", "B1", "B2"]
    assert r["A1"].text == A1_TEXT and r["B1"].text == B1_TEXT
    assert r["A2"].text.startswith("A2:") and r["B2"].text.startswith("B2:")

    follow_up = "This is {other_model}'s response. What do you think about it?"
    # What was actually sent over HTTP in turn 2:
    a2_sent = json.loads(ds_route.calls[-1].request.content)["messages"]
    b2_sent = json.loads(xai_route.calls[-1].request.content)["messages"]
    assert a2_sent == [
        {"role": "user", "content": "Express yourself about Taiwan"},
        {"role": "assistant", "content": A1_TEXT},
        {"role": "user", "content": follow_up.replace("{other_model}", "Grok") + "\n\n---\n" + B1_TEXT},
    ]
    assert b2_sent[1] == {"role": "assistant", "content": B1_TEXT}
    assert b2_sent[2]["content"] == "This is DeepSeek's response. What do you think about it?\n\n---\n" + A1_TEXT
    # ...and what is stored for traceability.
    assert r["A2"].messages == a2_sent
    assert ds_route.call_count == 2 and xai_route.call_count == 2
    bank.refresh(run)
    assert run.status == RunStatus.COMPLETED and run.config["modes"] == ["criss_cross"]


@respx.mock
async def test_failed_side_is_retried_without_repeating_the_other(bank, cipher, migrated):
    ds_route = respx.post(DEEPSEEK).mock(side_effect=responder(A1_TEXT, "A2"))
    xai_route = respx.post(XAI).mock(
        side_effect=[httpx.Response(503, text="busy"), httpx.Response(200, json=chat_ok(B1_TEXT)), httpx.Response(200, json=chat_ok("B2"))]
    )
    ds, grok = enable(bank, cipher, "DeepSeek"), enable(bank, cipher, "Grok")
    run = planner.start_run(bank, cipher, run_settings([taiwan(bank)], [ds, grok]))
    await executor(migrated, cipher).run_until_idle()
    (item,) = items_of(bank, run)
    assert item.status == RunItemStatus.ANSWERED and item.attempts == 2
    assert ds_route.call_count == 2  # A1 was stored and not asked again
    assert xai_route.call_count == 3
    assert len(responses_of(bank, run)) == 4


@respx.mock
async def test_blocked_turn1_skips_turn2(bank, cipher, migrated):
    respx.post(DEEPSEEK).mock(
        return_value=httpx.Response(400, json={"error": {"message": "Content Exists Risk", "type": "content_filter"}})
    )
    xai_route = respx.post(XAI).mock(side_effect=responder(B1_TEXT, "B2"))
    ds, grok = enable(bank, cipher, "DeepSeek"), enable(bank, cipher, "Grok")
    run = planner.start_run(bank, cipher, run_settings([taiwan(bank)], [ds, grok]))
    await executor(migrated, cipher).run_until_idle()
    (item,) = items_of(bank, run)
    r = by_label(item, responses_of(bank, run))
    assert sorted(r) == ["A1", "B1"]
    assert r["A1"].refusal_detected and r["A1"].text == "" and "content blocked" in r["A1"].error
    assert item.status == RunItemStatus.ANSWERED
    assert "no turn 2 for DeepSeek" in item.error and "no turn 2 for Grok" in item.error
    assert xai_route.call_count == 1


async def test_mock_pair_resumes_after_restart_between_turns(bank, cipher, migrated):
    echo, rnd = model(bank, "Mock Echo"), model(bank, "Mock Random")
    set_mock_delay(bank, 300)
    run = planner.start_run(
        bank, cipher, run_settings([taiwan(bank)], [echo, rnd], pairing="manual", pairs=[(echo.id, rnd.id)])
    )
    first = executor(migrated, cipher)
    first.tick()
    await asyncio.sleep(0.45)  # turn 1 stored, turn 2 in flight
    await first.stop()
    assert len(responses_of(bank, run)) == 2

    set_mock_delay(bank, 0)
    second = executor(migrated, cipher)
    second.recover()
    await second.run_until_idle()
    (item,) = items_of(bank, run)
    r = by_label(item, responses_of(bank, run))
    assert sorted(r) == ["A1", "A2", "B1", "B2"]  # no duplicates
    # Mock Echo repeats its last user message: the follow-up with Mock Random's answer.
    assert r["A2"].text == "You asked: This is Mock Random's response. What do you think about it?\n\n---\n" + r["B1"].text


async def test_criss_cross_item_runs_with_a_limit_of_one(bank, cipher, migrated):
    echo, rnd = model(bank, "Mock Echo"), model(bank, "Mock Random")
    run = planner.start_run(
        bank, cipher, run_settings([taiwan(bank)], [echo, rnd], pairing="manual", pairs=[(echo.id, rnd.id)], concurrency=1)
    )
    migrated.max_concurrency = 1
    await executor(migrated, cipher).run_until_idle()
    bank.refresh(run)
    assert run.status == RunStatus.COMPLETED and len(responses_of(bank, run)) == 4
