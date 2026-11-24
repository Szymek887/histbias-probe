"""Jinja2 environment and page rendering helpers."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.schemas.topics import ERA_LABELS, TYPE_LABELS
from app.web.csrf import CSRF_FIELD
from app.web.markdown import render_markdown

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Autoescaping is on for .html templates. Never mark model output `|safe`;
# it goes through the sanitizing Markdown renderer (docs/07-security.md).
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    href: str


NAV_ITEMS = [
    NavItem("runs", "Runs", "/"),
    NavItem("new_run", "New run", "/runs/new"),
    NavItem("topics", "Topics", "/topics"),
    NavItem("models", "Models", "/models"),
    NavItem("settings", "Settings", "/settings/keys"),
]

templates.env.globals.update(
    nav_items=NAV_ITEMS,
    app_version=__version__,
    csrf_field=CSRF_FIELD,
    era_labels={k.value: v for k, v in ERA_LABELS.items()},
    type_labels={k.value: v for k, v in TYPE_LABELS.items()},
)
templates.env.filters["markdown"] = render_markdown


def format_dt(value, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Local time for display (stored values are UTC)."""
    if value is None:
        return ""
    return value.astimezone().strftime(fmt)


templates.env.filters["dt"] = format_dt


def format_num(value, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def format_pct(value) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


templates.env.filters["num"] = format_num
# Starting a run with at least this many calls to paid (non-mock) providers asks for confirmation.
templates.env.globals["confirm_paid_calls"] = 20
templates.env.filters["pct"] = format_pct


def is_htmx(request: Request) -> bool:
    """An HTMX request that wants a partial (not a boosted full-page navigation)."""
    return request.headers.get("hx-request") == "true" and request.headers.get("hx-boosted") != "true"


def render(
    request: Request,
    template: str,
    *,
    active_nav: str = "",
    page_title: str = "",
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        template,
        {
            "active_nav": active_nav,
            "page_title": page_title,
            "csrf_token": getattr(request.state, "csrf_token", ""),
            **context,
        },
        status_code=status_code,
    )
