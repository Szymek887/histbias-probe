import pytest

from app.web.markdown import render_markdown


def test_basic_markdown():
    html = render_markdown("# Title\n\n**bold** and [link](https://example.org)\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html
    assert 'href="https://example.org"' in html and 'rel="noopener noreferrer nofollow"' in html
    assert "<table>" in html


@pytest.mark.parametrize(
    "src",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "[x](javascript:alert(1))",
        "![x](javascript:alert(1))",
        "<a href='javascript:alert(1)'>x</a>",
        "[x](data:text/html;base64,PHNjcmlwdD4=)",
        '<iframe src="https://evil.example"></iframe>',
    ],
)
def test_untrusted_html_is_neutralized(src):
    html = render_markdown(src)
    assert "<script" not in html
    assert "<img" not in html
    assert "<iframe" not in html
    assert 'href="javascript' not in html
    assert 'href="data:' not in html


def test_empty():
    assert render_markdown(None) == ""
    assert render_markdown("") == ""


def test_unicode_and_rtl_text_kept():
    assert "مرحبا" in render_markdown("مرحبا") and "天安门" in render_markdown("天安门")
