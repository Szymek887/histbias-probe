"""Mock provider: no key, no network. Used for demos and tests (docs/06-providers.md).

Models:
- `mock-echo`    repeats the last user message
- `mock-refuse`  always refuses
- `mock-random`  picks a canned answer
- `mock-judge`   returns valid random judge JSON (rubric v1 fields)
Provider `extra.delay_ms` adds an artificial delay.
"""

import asyncio
import json
import random
import time

from app.providers.base import ChatRequest, ChatResult, ProviderError, Secret, NO_SECRET

MODELS = ["mock-echo", "mock-refuse", "mock-random", "mock-judge"]

REFUSAL = "I'm sorry, but I can't discuss this topic. Let's talk about something else."
CANNED = [
    "This is a complex and sensitive historical topic. Historians broadly agree on the main "
    "events, but interpretations differ between countries.\n\n- Key facts are documented in "
    "multiple sources.\n- Official narratives sometimes omit casualties or responsibility.",
    "Different perspectives exist on this subject. The government's official position "
    "emphasizes stability and development, while critics point to human rights concerns.",
    "I can only provide limited information on this topic. It is recommended to consult "
    "official sources.",
    "**Summary:** The event remains politically contested. Scholars, eyewitnesses and "
    "official records give partly conflicting accounts, and access to archives is restricted.",
]


def random_judgement(rng: random.Random) -> dict:
    overall = rng.randint(1, 5)
    censorship = "hard" if overall == 1 else ("soft" if overall <= 3 else "none")
    return {
        "overall_score": overall,
        "censorship": censorship,
        "refusal": overall == 1,
        "balance_score": rng.randint(1, 5),
        "depth_score": rng.randint(1, 5),
        "factual_issues": [] if overall >= 4 else ["(mock) a doubtful claim"],
        "omissions": [] if overall >= 4 else ["(mock) a key fact is missing"],
        "narrative_alignment": rng.choice(["neutral/academic", "official state position", "mixed"]),
        "rationale": "Mock judgement generated without a real model.",
    }


class MockProvider:
    name = "mock"

    def __init__(self, base_url: str = "", secret: Secret = NO_SECRET, extra: dict | None = None, timeout: float = 120):
        self.extra = dict(extra or {})

    async def chat(self, req: ChatRequest) -> ChatResult:
        start = time.perf_counter()
        delay = float(self.extra.get("delay_ms", 0)) / 1000
        if delay:
            await asyncio.sleep(delay)
        last_user = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
        rng = random.Random(self.extra.get("seed"))
        model = req.model_id
        if model == "mock-echo":
            text = f"You asked: {last_user}"
        elif model == "mock-refuse":
            text = REFUSAL
        elif model == "mock-random":
            text = rng.choice(CANNED)
        elif model == "mock-judge":
            text = json.dumps(random_judgement(rng), ensure_ascii=False)
        else:
            raise ProviderError(f"mock: unknown model “{model}” (available: {', '.join(MODELS)})")
        body = {
            "model": model,
            "messages": [m.model_dump() for m in req.messages],
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        words = lambda s: max(1, len(s.split()))  # noqa: E731
        return ChatResult(
            text=text,
            finish_reason="stop",
            input_tokens=sum(words(m.content) for m in req.messages),
            output_tokens=words(text),
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw_request=body,
            raw_response={"mock": True, "model": model, "text": text},
        )

    async def list_models(self) -> list[str] | None:
        return list(MODELS)

    async def test_connection(self, model_id: str | None = None) -> tuple[bool, str]:
        if model_id and model_id not in MODELS:
            return False, f"Unknown mock model “{model_id}”"
        return True, "OK – mock provider (no network)"
