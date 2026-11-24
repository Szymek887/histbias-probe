import re

import pytest
from sqlmodel import select

from app.models import Country, Prompt, Run, RunTopic, Topic, TopicCountry, TopicSource
from tests.conftest import csrf_token


@pytest.fixture
def seeded_client(client, seeded):
    return client


def post(client, url, data=None, **kw):
    data = dict(data or {})
    data.setdefault("csrf_token", csrf_token(client))
    return client.post(url, data=data, follow_redirects=False, **kw)


def topic_rows(html: str) -> int:
    return html.count('name="ids"')


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_shows_50_seed_topics(seeded_client):
    r = seeded_client.get("/topics")
    assert r.status_code == 200
    assert topic_rows(r.text) == 50
    assert "Tiananmen Square" in r.text


def test_list_filters_and_search(seeded_client):
    assert topic_rows(seeded_client.get("/topics?type=criss_cross").text) == 15
    # The study wiki has 5 internal Polish topics (only 1 in "past").
    assert topic_rows(seeded_client.get("/topics?country=PL&type=internal").text) == 5
    assert topic_rows(seeded_client.get("/topics?country=CN&era=present&type=internal").text) == 2
    assert topic_rows(seeded_client.get("/topics?q=tiananmen").text) == 1
    assert topic_rows(seeded_client.get("/topics?q=nothing-matches-this").text) == 0


def test_list_htmx_returns_partial(seeded_client):
    r = seeded_client.get("/topics?q=taiwan", headers={"HX-Request": "true"})
    assert "<html" not in r.text
    assert "Taiwan" in r.text


def test_runs_page_hint_links(seeded_client):
    assert seeded_client.get("/countries").status_code == 200


# ── create / edit ────────────────────────────────────────────────────────────


def test_create_criss_cross_topic_with_polish_prompt(seeded_client, session):
    r = post(
        seeded_client,
        "/topics/new",
        {
            "title": "Katyn massacre",
            "type": "criss_cross",
            "era": "20th_century",
            "country_1": "PL",
            "country_2": "RU",
            "context": "In **1940** the NKVD…",
            "source_label": ["IPN", ""],
            "source_url": ["https://ipn.gov.pl/", ""],
            "prompt_language": ["en", "pl"],
            "prompt_text": ["", "Wypowiedz się o zbrodni katyńskiej"],
            "prompt_follow_up": ["", "To jest odpowiedź {other_model}. Co o niej sądzisz?"],
        },
    )
    assert r.status_code == 303, r.text
    topic_id = int(re.search(r"/topics/(\d+)", r.headers["location"]).group(1))
    topic = session.get(Topic, topic_id)
    assert topic.slug == "poland-vs-russia-katyn-massacre"
    assert (topic.type, topic.origin, topic.archived) == ("criss_cross", "user", False)
    countries = session.exec(
        select(TopicCountry).where(TopicCountry.topic_id == topic_id).order_by(TopicCountry.position)
    ).all()
    assert [c.country_code for c in countries] == ["PL", "RU"]
    prompts = {p.language: p for p in session.exec(select(Prompt).where(Prompt.topic_id == topic_id))}
    assert prompts["en"].text == "Express yourself about Katyn massacre"
    assert prompts["en"].is_default and not prompts["pl"].is_default
    assert prompts["en"].follow_up_text == "This is {other_model}'s response. What do you think about it?"
    assert prompts["pl"].text == "Wypowiedz się o zbrodni katyńskiej"
    assert len(session.exec(select(TopicSource).where(TopicSource.topic_id == topic_id)).all()) == 1

    detail = seeded_client.get(r.headers["location"])
    assert "Topic created." in detail.text
    assert "<strong>1940</strong>" in detail.text
    assert "Wypowiedz się" in detail.text
    assert topic_rows(seeded_client.get("/topics").text) == 51


def test_create_internal_default_prompt(seeded_client, session):
    r = post(
        seeded_client,
        "/topics/new",
        {"title": "Partitions", "type": "internal", "era": "past", "country_1": "PL",
         "prompt_language": ["en"], "prompt_text": [""], "prompt_follow_up": [""]},
    )
    assert r.status_code == 303
    t = session.exec(select(Topic).where(Topic.title == "Partitions")).one()
    assert t.slug == "poland-past-partitions"
    p = session.exec(select(Prompt).where(Prompt.topic_id == t.id)).one()
    assert (p.text, p.follow_up_text) == ("Tell me about Partitions", None)


def test_create_with_errors_rerenders_form(seeded_client, session):
    r = post(
        seeded_client,
        "/topics/new",
        {"title": "Half done", "type": "criss_cross", "era": "present", "country_1": "PL",
         "source_label": ["x"], "source_url": ["javascript:alert(1)"],
         "prompt_language": ["en"], "prompt_text": ["Hello"], "prompt_follow_up": [""]},
    )
    assert r.status_code == 422
    assert "must start with http:// or https://" in r.text
    assert 'value="Half done"' in r.text  # input kept
    assert session.exec(select(Topic).where(Topic.title == "Half done")).first() is None

    r = post(
        seeded_client,
        "/topics/new",
        {"title": "Half done", "type": "criss_cross", "era": "present", "country_1": "PL",
         "prompt_language": ["en"], "prompt_text": ["Hello"], "prompt_follow_up": [""]},
    )
    assert r.status_code == 422
    assert "needs exactly 2 countries" in r.text


def test_edit_topic_keeps_slug(seeded_client, session):
    t = session.exec(select(Topic).where(Topic.slug == "china-present-uyghurs")).one()
    form = seeded_client.get(f"/topics/{t.id}/edit")
    assert form.status_code == 200 and "Tell me about Uyghurs" in form.text
    r = post(
        seeded_client,
        f"/topics/{t.id}/edit",
        {"title": "Uyghurs in Xinjiang", "type": "internal", "era": "present", "country_1": "CN",
         "prompt_language": ["en", "zh"], "prompt_text": ["Tell me about Uyghurs", "告诉我关于维吾尔族的情况"],
         "prompt_follow_up": ["", ""]},
    )
    assert r.status_code == 303
    session.refresh(t)
    assert (t.slug, t.title, t.origin) == ("china-present-uyghurs", "Uyghurs in Xinjiang", "seed")
    langs = {p.language for p in session.exec(select(Prompt).where(Prompt.topic_id == t.id))}
    assert langs == {"en", "zh"}


def test_form_row_partials(client):
    assert 'name="source_url"' in client.get("/topics/_rows/source").text
    assert 'name="prompt_language"' in client.get("/topics/_rows/prompt").text


def test_context_preview_is_sanitized(client):
    r = post(client, "/topics/_preview", {"context": "**hi** <script>alert(1)</script> [x](javascript:alert(1))"})
    assert "<strong>hi</strong>" in r.text
    assert "<script>" not in r.text
    assert 'href="javascript' not in r.text


def test_context_is_escaped_on_detail(seeded_client, session):
    t = session.exec(select(Topic)).first()
    t.context = "<img src=x onerror=alert(1)>"
    session.add(t)
    session.commit()
    html = seeded_client.get(f"/topics/{t.id}").text
    assert "<img" not in html


# ── archive / delete ─────────────────────────────────────────────────────────


def test_archive_restore(seeded_client, session):
    t = session.exec(select(Topic)).first()
    assert post(seeded_client, f"/topics/{t.id}/archive").status_code == 303
    assert topic_rows(seeded_client.get("/topics").text) == 49
    assert topic_rows(seeded_client.get("/topics?archived=true").text) == 1
    assert post(seeded_client, f"/topics/{t.id}/restore").status_code == 303
    assert topic_rows(seeded_client.get("/topics").text) == 50


def test_delete_unused_topic(seeded_client, session):
    topic_id = session.exec(select(Topic)).first().id
    r = post(seeded_client, f"/topics/{topic_id}/delete")
    assert r.headers["location"] == "/topics?notice=deleted"
    session.expire_all()
    assert session.get(Topic, topic_id) is None
    assert session.exec(select(Prompt).where(Prompt.topic_id == topic_id)).all() == []


def test_delete_topic_used_by_run_archives_it(seeded_client, session):
    t = session.exec(select(Topic)).first()
    run = Run(name="r")
    session.add(run)
    session.flush()
    session.add(RunTopic(run_id=run.id, topic_id=t.id))
    session.commit()
    r = post(seeded_client, f"/topics/{t.id}/delete")
    assert "archived_instead" in r.headers["location"]
    session.refresh(t)
    assert t.archived
    detail = seeded_client.get(f"/topics/{t.id}").text
    assert "/runs/%d" % run.id in detail


# ── countries ────────────────────────────────────────────────────────────────


def test_add_and_edit_country(client, session):
    r = post(client, "/countries", {"code": "ua", "name": "Ukraine", "native_language": "uk"})
    assert r.status_code == 303
    assert session.get(Country, "UA").native_language == "uk"
    r = post(client, "/countries", {"code": "UA", "name": "Again", "native_language": "uk"})
    assert r.status_code == 422 and "already exists" in r.text
    r = post(client, "/countries", {"code": "U", "name": "", "native_language": "ukrainian"})
    assert r.status_code == 422
    r = post(client, "/countries/UA/edit", {"name": "Ukraina", "native_language": "uk"})
    assert r.status_code == 303
    session.expire_all()
    assert session.get(Country, "UA").name == "Ukraina"


def test_inline_add_country_in_topic_form(seeded_client, session):
    r = post(
        seeded_client,
        "/topics/_country",
        {"type": "criss_cross", "country_1": "PL", "new_country_code": "UA",
         "new_country_name": "Ukraine", "new_country_language": "uk"},
    )
    assert r.status_code == 200
    assert '<option value="UA" selected>' in r.text  # filled into the empty second select
    assert session.get(Country, "UA") is not None


# ── import / export ──────────────────────────────────────────────────────────


def test_export_download(seeded_client):
    r = seeded_client.get("/topics/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.count("- slug:") == 50


def test_import_flow(seeded_client, session):
    yaml_text = """version: 1
countries: [{code: UA, name: Ukraine, native_language: uk}]
topics:
  - slug: ukraine-20th-century-holodomor
    title: Holodomor
    type: internal
    countries: [UA]
    era: 20th_century
    prompt: Tell me about Holodomor
  - slug: china-present-uyghurs
    title: Uyghurs (changed)
    type: internal
    countries: [CN]
    era: present
    prompt: Tell me about Uyghurs
"""
    r = seeded_client.post(
        "/topics/import",
        data={"csrf_token": csrf_token(seeded_client)},
        files={"file": ("t.yaml", yaml_text.encode(), "application/x-yaml")},
    )
    assert r.status_code == 200, r.text
    assert "<strong>1</strong> new" in r.text and "<strong>1</strong> changed" in r.text
    token = re.search(r'name="token" value="([0-9a-f]{32})"', r.text).group(1)
    assert session.exec(select(Topic).where(Topic.slug == "ukraine-20th-century-holodomor")).first() is None

    r = post(seeded_client, "/topics/import/confirm", {"token": token, "mode": "skip"})
    assert r.status_code == 200
    assert "Topics added: 1" in r.text and "Topics skipped (already exist): 1" in r.text
    session.expire_all()
    assert session.exec(select(Topic).where(Topic.slug == "china-present-uyghurs")).one().title == "Uyghurs"
    # The token is single-use.
    assert post(seeded_client, "/topics/import/confirm", {"token": token, "mode": "skip"}).status_code == 410


def test_import_errors_shown(client):
    r = client.post(
        "/topics/import",
        data={"csrf_token": csrf_token(client)},
        files={"file": ("t.yaml", b"topics:\n  - slug: x\n    title: X\n", "application/x-yaml")},
    )
    assert r.status_code == 422
    assert "Nothing was imported" in r.text
    assert 'name="token"' not in r.text


def test_import_token_is_validated(client):
    assert post(client, "/topics/import/confirm", {"token": "../../etc/passwd", "mode": "skip"}).status_code == 400


# ── CSRF ─────────────────────────────────────────────────────────────────────


def test_post_without_csrf_token_is_rejected(seeded_client, session):
    csrf_token(seeded_client)
    r = seeded_client.post("/countries", data={"code": "UA", "name": "Ukraine", "native_language": "uk"})
    assert r.status_code == 403
    assert session.get(Country, "UA") is None


def test_post_with_wrong_token_or_foreign_origin_is_rejected(client):
    token = csrf_token(client)
    data = {"code": "UA", "name": "Ukraine", "native_language": "uk"}
    assert client.post("/countries", data=data | {"csrf_token": "x" * 40}).status_code == 403
    r = client.post("/countries", data=data | {"csrf_token": token}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_htmx_header_token_accepted(client):
    token = csrf_token(client)
    r = client.post("/topics/_preview", data={"context": "x"}, headers={"X-CSRF-Token": token})
    assert r.status_code == 200


def test_pages_embed_csrf_token(client):
    token = csrf_token(client)
    html = client.get("/topics/new").text
    assert f'value="{token}"' in html
    assert f'"X-CSRF-Token": "{token}"' in html
