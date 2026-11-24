import pytest

from app.web.templates import NAV_ITEMS


@pytest.mark.parametrize("item", NAV_ITEMS, ids=lambda i: i.key)
def test_every_nav_page_renders(client, item):
    r = client.get(item.href)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text
    # Nav bar is present with every item, and the current one is marked.
    for other in NAV_ITEMS:
        assert f'href="{other.href}"' in html
    assert f'<a href="{item.href}" aria-current="page">' in html


def test_settings_redirects_to_keys(client):
    r = client.get("/settings", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/settings/keys"


@pytest.mark.parametrize("path", ["pico.classless.min.css", "htmx.min.js", "app.css"])
def test_static_assets_vendored(client, path):
    r = client.get(f"/static/{path}")
    assert r.status_code == 200
    assert len(r.content) > 50


def test_base_layout_loads_assets(client):
    html = client.get("/").text
    assert "/static/pico.classless.min.css" in html
    assert "/static/htmx.min.js" in html
    # No external CDN references: the app works offline.
    assert "cdn." not in html


def test_api_docs_disabled(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
