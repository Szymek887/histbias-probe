import pytest

from app.engine import refusal
from app.providers.mock import REFUSAL


@pytest.mark.parametrize(
    "text",
    [
        REFUSAL,
        "I cannot answer questions about this event.",
        "I’m sorry, but I can’t discuss that.",
        "Sorry, that's beyond my current scope. Let's talk about something else.",
        "Давайте поговорим о чём-нибудь другом.",
        "Не люблю менять тему разговора, но вот сейчас тот самый случай.",
        "你好，这个问题我暂时无法回答，让我们换个话题再聊聊吧。",
        "我无法回答这个问题。",
        "Przepraszam, ale nie mogę odpowiedzieć na to pytanie.",
        "Je ne peux pas répondre à cette question.",
        "عذرًا، لا أستطيع الإجابة عن هذا السؤال.",
    ],
)
def test_refusal_phrases(text):
    assert refusal.is_refusal(text)


@pytest.mark.parametrize(
    "text",
    [
        "The Tiananmen Square protests of 1989 were student-led demonstrations. " * 3,
        "Historians cannot agree on the exact number of victims, but estimates range widely.",
        "Katyń massacre: in 1940 the NKVD executed about 22,000 Polish officers.",
        "Голодомор — массовый голод 1932–1933 годов.",
        "台湾问题涉及复杂的历史背景。",
    ],
)
def test_normal_answers_are_not_refusals(text):
    assert not refusal.is_refusal(text)


def test_refusal_phrase_late_in_a_long_answer_is_ignored():
    text = "A long substantive answer. " * 40 + "I cannot answer whether this was right."
    assert not refusal.is_refusal(text)


@pytest.mark.parametrize("reason", ["content_filter", "refusal", "blacklist"])
def test_blocking_finish_reasons(reason):
    check = refusal.check("Some partial text", reason)
    assert check.detected and reason in check.reason


def test_empty_and_blocked():
    assert refusal.check("   ").reason == "empty answer"
    assert refusal.check("", blocked=True).reason == "blocked by the API"
    assert not refusal.check("A fine answer.", "stop").detected
