"""Logging setup with secret redaction (see docs/07-security.md).

Redaction happens in the formatter, so it covers the final message text, including
formatted arguments and exception tracebacks. Code must still never log headers or
tokens on purpose; this is a safety net.
"""

import logging
import re

REDACTED = "[REDACTED]"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization: Bearer xxx / Authorization: Api-Key xxx / 'Authorization': 'xxx'
    (
        re.compile(
            r"""(?ix)
            (["']?authorization["']?\s*[:=]\s*["']?)
            (?:(bearer|basic|api-key)\s+)?
            [^\s"',}]+
            """
        ),
        lambda m: f"{m.group(1)}{m.group(2) + ' ' if m.group(2) else ''}{REDACTED}",
    ),
    # x-api-key / api_key / api-key / apikey with : or = separators, quoted or not
    (
        re.compile(r"""(?i)(["']?(?:x-api-key|api[_-]?key)["']?\s*[:=]\s*["']?)[^\s"',}&]+"""),
        rf"\1{REDACTED}",
    ),
    # Credentials in URL query strings: ?key=..., &access_token=...
    (
        re.compile(r"(?i)([?&](?:key|token|access_token|api_?key)=)[^&\s\"']+"),
        rf"\1{REDACTED}",
    ),
    # A bare "Bearer <token>" anywhere
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), rf"\1{REDACTED}"),
]


def redact(text: str) -> str:
    """Replace secret-looking values in `text` with [REDACTED]."""
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger with one redacting stream handler.

    uvicorn is started with `log_config=None`, so its loggers propagate here too.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_histbias", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(LOG_FORMAT))
    handler._histbias = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level.upper())
    # httpx logs full request URLs at INFO; keep it at WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
