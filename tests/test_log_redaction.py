import logging

import pytest

from app.log import REDACTED, RedactingFormatter, redact

SECRET = "sk-test-0123456789abcdefSECRET"


@pytest.mark.parametrize(
    "text",
    [
        f"Authorization: Bearer {SECRET}",
        f"{{'Authorization': 'Bearer {SECRET}'}}",
        f'{{"authorization": "{SECRET}"}}',
        f"Authorization: Api-Key {SECRET}",
        f"x-api-key: {SECRET}",
        f"{{'x-api-key': '{SECRET}'}}",
        f"api_key={SECRET}",
        f'{{"api_key": "{SECRET}"}}',
        f"apiKey={SECRET}",
        f"GET https://example.com/v1/models?key={SECRET}&x=1",
        f"token is Bearer {SECRET} ok",
    ],
)
def test_redact_hides_secret(text):
    out = redact(text)
    assert SECRET not in out
    assert REDACTED in out


def test_redact_keeps_normal_text():
    text = "Run 3: 12/40 items answered, model=deepseek-chat"
    assert redact(text) == text


def test_formatter_redacts_args_and_tracebacks():
    fmt = RedactingFormatter("%(message)s")
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "headers=%s", ({"x-api-key": SECRET},), None)
    assert SECRET not in fmt.format(record)

    try:
        raise RuntimeError(f"failed with Authorization: Bearer {SECRET}")
    except RuntimeError:
        import sys

        exc = sys.exc_info()
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", (), exc)
    out = fmt.format(record)
    assert "RuntimeError" in out
    assert SECRET not in out


def test_setup_logging_end_to_end(capsys):
    from app.log import setup_logging

    setup_logging("INFO")
    setup_logging("INFO")  # idempotent: still one handler
    logging.getLogger("histbias.test").info("calling with api_key=%s", SECRET)
    err = capsys.readouterr().err
    assert SECRET not in err
    assert err.count("calling with api_key=") == 1
