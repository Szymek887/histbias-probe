"""CSRF protection (docs/07-security.md): double-submit cookie + Origin check.

- Middleware gives every browser a random token cookie and exposes it as
  `request.state.csrf_token` for templates.
- `verify_csrf` (an app-wide dependency) rejects state-changing requests unless
  the token is sent back in the `X-CSRF-Token` header (HTMX, set on <body>) or the
  `csrf_token` form field, and the `Origin` header, when present, is this server.
"""

import secrets
from urllib.parse import urlparse

from fastapi import HTTPException, Request

CSRF_COOKIE = "histbias_csrf"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _valid_format(token: str | None) -> bool:
    return bool(token) and 20 <= len(token) <= 100 and token.replace("-", "").replace("_", "").isalnum()


async def csrf_cookie_middleware(request: Request, call_next):
    token = request.cookies.get(CSRF_COOKIE)
    is_new = not _valid_format(token)
    if is_new:
        token = secrets.token_urlsafe(32)
    request.state.csrf_token = token
    response = await call_next(request)
    if is_new:
        response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="strict", path="/")
    return response


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True  # non-browser clients; the token check still applies
    parsed = urlparse(origin)
    return parsed.scheme in ("http", "https") and parsed.netloc == request.headers.get("host")


async def verify_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    if not _origin_ok(request):
        raise HTTPException(403, "Cross-origin request blocked.")
    cookie = request.cookies.get(CSRF_COOKIE)
    sent = request.headers.get(CSRF_HEADER)
    if not sent:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            value = form.get(CSRF_FIELD)
            sent = value if isinstance(value, str) else None
    if not cookie or not sent or not secrets.compare_digest(cookie, sent):
        raise HTTPException(403, "Missing or invalid CSRF token. Reload the page and try again.")
