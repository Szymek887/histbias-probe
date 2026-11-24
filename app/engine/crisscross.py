"""Criss-cross conversation logic (docs/05-methodology.md, "Criss-cross mode").

Turn 1: A and B answer the prompt independently (in parallel) → A1, B1.
Turn 2: each conversation continues with the partner's turn-1 answer:
    A: [user: prompt, assistant: A1, user: follow-up(B's name) + "\\n\\n---\\n" + B1] → A2
    B: the same with A1 → B2

The partner's answer is inserted exactly as received (the stored `response.text`).
"""

from dataclasses import dataclass

from app.providers.base import ChatMessage

PLACEHOLDER = "{other_model}"
SEPARATOR = "\n\n---\n"


def follow_up_message(follow_up_text: str, partner_name: str, partner_answer: str) -> str:
    return follow_up_text.replace(PLACEHOLDER, partner_name) + SEPARATOR + partner_answer


def turn1_messages(prompt: str, system_prompt: str | None = None) -> list[ChatMessage]:
    messages = [ChatMessage(role="system", content=system_prompt)] if system_prompt else []
    messages.append(ChatMessage(role="user", content=prompt))
    return messages


def turn2_messages(
    prompt: str,
    own_answer: str,
    follow_up_text: str,
    partner_name: str,
    partner_answer: str,
    system_prompt: str | None = None,
) -> list[ChatMessage]:
    return [
        *turn1_messages(prompt, system_prompt),
        ChatMessage(role="assistant", content=own_answer),
        ChatMessage(role="user", content=follow_up_message(follow_up_text, partner_name, partner_answer)),
    ]


@dataclass(frozen=True)
class TurnOne:
    """What a side said in turn 1 (from the stored response)."""

    text: str
    blocked: bool  # the API blocked it (stored error, no text)


def turn2_skip_reason(own_name: str, own: TurnOne, partner_name: str, partner: TurnOne) -> str | None:
    """Why a side gets no turn 2, or None. A blocked or empty turn-1 answer can't be
    continued (own) or commented on (partner); the block itself is the result."""
    if own.blocked or not own.text.strip():
        return f"{own_name} has no turn-1 answer to continue ({'blocked' if own.blocked else 'empty'})"
    if partner.blocked or not partner.text.strip():
        return f"{partner_name}'s turn-1 answer was {'blocked' if partner.blocked else 'empty'}, so {own_name} had nothing to comment on"
    return None
