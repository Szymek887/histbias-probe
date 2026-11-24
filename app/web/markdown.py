"""The only way to render untrusted Markdown (model answers, topic context) as HTML.

Raw HTML in the source is escaped by markdown-it (html=False), and the output is
sanitized again with nh3 as a second layer. Never mark model output `|safe` directly.
"""

import nh3
from markdown_it import MarkdownIt
from markupsafe import Markup

_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")

_ALLOWED_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
    "ul", "ol", "li", "strong", "em", "code", "pre", "a",
    "table", "thead", "tbody", "tr", "th", "td",
}  # fmt: skip
_ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "th": {"style"}, "td": {"style"}}


def render_markdown(text: str | None) -> Markup:
    if not text:
        return Markup("")
    html = _md.render(text)
    clean = nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
        filter_style_properties={"text-align"},
    )
    return Markup(clean)  # noqa: S704 - sanitized above
