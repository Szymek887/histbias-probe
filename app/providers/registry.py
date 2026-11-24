"""Provider kind -> adapter class."""

from typing import Any

from app.models import ProviderKind
from app.providers.anthropic import AnthropicProvider
from app.providers.base import NO_SECRET, ChatProvider, Secret
from app.providers.gigachat import GigaChatProvider
from app.providers.mock import MockProvider
from app.providers.openai_compat import OpenAICompatibleProvider

ADAPTERS: dict[str, type] = {
    ProviderKind.OPENAI_COMPATIBLE: OpenAICompatibleProvider,
    ProviderKind.ANTHROPIC: AnthropicProvider,
    ProviderKind.GIGACHAT: GigaChatProvider,
    ProviderKind.MOCK: MockProvider,
}

# Kinds that work without any key.
KEYLESS_KINDS = {ProviderKind.MOCK}


def make_adapter(
    kind: str, base_url: str, secret: Secret = NO_SECRET, extra: dict[str, Any] | None = None,
    timeout: float = 120,
) -> ChatProvider:
    try:
        cls = ADAPTERS[kind]
    except KeyError:
        raise ValueError(f"unknown provider kind {kind!r}") from None
    return cls(base_url, secret, extra or {}, timeout)
