import pytest
import yaml
from sqlmodel import Session, select

from app.config import Settings
from app.db import get_engine, migrate
from app.models import Country, Prompt, Topic, TopicCountry, TopicSource
from app.schemas.topics import TopicData
from app.services import topic_io
from app.services import topics as svc
from app.services.topic_io import ImportMode, Status


def test_seed_import_loads_50_topics_idempotently(session):
    r = topic_io.import_seed(session)
    assert (r.topics_added, r.countries_added) == (50, 6)
    assert len(session.exec(select(Topic)).all()) == 50
    assert {t.origin for t in session.exec(select(Topic))} == {"seed"}
    r = topic_io.import_seed(session)
    assert (r.topics_added, r.topics_unchanged) == (0, 50)
    assert len(session.exec(select(Topic)).all()) == 50


def test_seed_import_keeps_user_edits_unless_overwrite(seeded):
    topic = seeded.exec(select(Topic).where(Topic.slug == "china-present-uyghurs")).one()
    data = svc.topic_data(seeded, topic).model_copy(update={"title": "Edited"})
    svc.update_topic(seeded, topic, TopicData.model_validate(data.model_dump()))
    r = topic_io.import_seed(seeded)
    assert r.topics_skipped == 1
    assert seeded.get(Topic, topic.id).title == "Edited"
    r = topic_io.import_seed(seeded, mode=ImportMode.OVERWRITE)
    assert r.topics_updated == 1
    seeded.refresh(topic)
    assert topic.title == "Uyghurs"


def test_seed_criss_cross_topics(seeded):
    cc = svc.list_topics(seeded, svc.TopicFilters(type="criss_cross"))
    assert len(cc) == 15
    assert all(len(r.countries) == 2 for r in cc)
    taiwan = [r for r in cc if "Taiwan" in r.topic.title]
    assert taiwan, "the Taiwan criss-cross topic is needed for M4"


def _dump_db(session: Session) -> dict:
    """Everything in the question bank, without ids and timestamps."""
    topics = {t.id: t.slug for t in session.exec(select(Topic))}
    return {
        "countries": sorted((c.code, c.name, c.native_language) for c in session.exec(select(Country))),
        "topics": sorted(
            (t.slug, t.title, t.type, t.era, t.context, t.origin, t.archived)
            for t in session.exec(select(Topic))
        ),
        "topic_country": sorted(
            (topics[x.topic_id], x.country_code, x.position) for x in session.exec(select(TopicCountry))
        ),
        "sources": sorted(
            (topics[x.topic_id], x.label, x.url, x.position) for x in session.exec(select(TopicSource))
        ),
        "prompts": sorted(
            (topics[x.topic_id], x.language, x.text, x.follow_up_text or "", x.is_default)
            for x in session.exec(select(Prompt))
        ),
    }


def test_export_import_round_trip(seeded, tmp_path):
    seeded.add(Country(code="UA", name="Ukraine", native_language="uk"))
    seeded.commit()
    svc.create_topic(
        seeded,
        TopicData.model_validate(
            {
                "title": "Wołyń massacre",
                "type": "criss_cross",
                "era": "20th_century",
                "countries": ["PL", "UA"],
                "context": "Multi-line\n\n**markdown** with “quotes” and ąę",
                "sources": [{"label": "IPN", "url": "https://ipn.gov.pl/"}],
                "prompts": [
                    {"language": "en", "text": "Express yourself about the Volhynia massacre",
                     "follow_up_text": "This is {other_model}'s response. What do you think about it?"},
                    {"language": "pl", "text": "Wypowiedz się o rzezi wołyńskiej",
                     "follow_up_text": "To jest odpowiedź {other_model}. Co o niej sądzisz?"},
                    {"language": "uk", "text": "Висловіться про Волинську трагедію",
                     "follow_up_text": "Це відповідь {other_model}. Що ви про неї думаєте?"},
                ],
            }
        ),
    )
    archived = seeded.exec(select(Topic).where(Topic.slug == "china-present-uyghurs")).one()
    svc.set_archived(seeded, archived, True)

    exported = topic_io.export_topics(seeded)

    fresh = Settings(data_dir=tmp_path / "fresh")
    migrate(fresh)
    with Session(get_engine(fresh)) as s2:
        parsed = topic_io.parse_topic_file(exported.encode())
        assert parsed.errors == []
        r = topic_io.apply_import(s2, parsed, ImportMode.SKIP)
        assert r.topics_added == 51
        assert _dump_db(s2) == _dump_db(seeded)
        assert topic_io.export_topics(s2) == exported
        # Re-importing the same file into either DB changes nothing.
        preview = topic_io.preview_import(s2, parsed)
        assert {t.status for t in preview.topics} == {Status.UNCHANGED}


def test_export_selected_includes_only_used_countries(seeded):
    t = seeded.exec(select(Topic).where(Topic.slug == "china-present-uyghurs")).one()
    doc = yaml.safe_load(topic_io.export_topics(seeded, [t.id]))
    assert [x["slug"] for x in doc["topics"]] == ["china-present-uyghurs"]
    assert [c["code"] for c in doc["countries"]] == ["CN"]


def test_export_has_no_credentials(seeded):
    from app.models import Credential, Provider

    p = Provider(name="Anthropic", kind="anthropic", base_url="https://api.anthropic.com")
    seeded.add(p)
    seeded.flush()
    seeded.add(Credential(provider_id=p.id, secret_enc=b"gAAAA-encrypted", hint="7f2a"))
    seeded.commit()
    out = topic_io.export_topics(seeded)
    assert "gAAAA" not in out and "7f2a" not in out and "Anthropic" not in out


YAML_OK = """
version: 1
countries:
  - {code: UA, name: Ukraine, native_language: uk}
topics:
  - slug: ukraine-present-holodomor
    title: Holodomor
    type: internal
    countries: [UA]
    era: 20th_century
    prompt: Tell me about Holodomor
    prompts:
      - {language: uk, text: Розкажи мені про Голодомор}
"""


def test_preview_statuses(seeded):
    parsed = topic_io.parse_topic_file(YAML_OK)
    p = topic_io.preview_import(seeded, parsed)
    assert p.ok
    assert [(c.data.code, c.status) for c in p.countries] == [("UA", Status.NEW)]
    assert [t.status for t in p.topics] == [Status.NEW]
    topic_io.apply_import(seeded, parsed, ImportMode.SKIP)

    changed = YAML_OK.replace("title: Holodomor", "title: Holodomor (1932–33)")
    p = topic_io.preview_import(seeded, topic_io.parse_topic_file(changed))
    assert p.topics[0].status == Status.CHANGED
    assert p.topics[0].changed_fields == ["title"]


@pytest.mark.parametrize(
    "content, msg",
    [
        ("not: [valid", "not valid YAML"),
        ("- just a list", "not a topic file"),
        ("version: 2\ntopics: []", "unsupported format version"),
        (b"\xff\xfe\x00bad", "UTF-8"),
        (b"x" * (5 * 1024 * 1024 + 1), "5 MB"),
    ],
)
def test_unreadable_files(content, msg):
    with pytest.raises(topic_io.ImportFormatError, match=msg):
        topic_io.parse_topic_file(content)


def test_per_topic_errors_are_all_reported(seeded):
    bad = YAML_OK + """
  - slug: ukraine-present-holodomor
    title: Duplicate
    type: internal
    countries: [UA]
    era: present
    prompt: x
  - slug: bad-url
    title: Bad
    type: internal
    countries: [UA]
    era: present
    prompt: x
    sources: [{label: x, url: "javascript:alert(1)"}]
  - slug: unknown-country
    title: Unknown
    type: internal
    countries: [ZZ]
    era: present
    prompt: x
  - slug: typo-field
    title: Typo
    typ: internal
    countries: [UA]
    era: present
    prompt: x
"""
    parsed = topic_io.parse_topic_file(bad)
    preview = topic_io.preview_import(seeded, parsed)
    text = "\n".join(preview.errors)
    assert "used more than once" in text
    assert "bad-url" in text and "http" in text
    assert "unknown country ZZ" in text
    assert "typo-field" in text
    with pytest.raises(topic_io.ImportFormatError):
        topic_io.apply_import(seeded, parsed, ImportMode.SKIP)
    assert seeded.exec(select(Country).where(Country.code == "UA")).first() is None


def test_overwrite_updates_country(seeded):
    parsed = topic_io.parse_topic_file(
        "version: 1\ncountries: [{code: CN, name: PRC, native_language: zh}]\ntopics: []"
    )
    assert topic_io.preview_import(seeded, parsed).countries[0].status == Status.CHANGED
    topic_io.apply_import(seeded, parsed, ImportMode.SKIP)
    assert seeded.get(Country, "CN").name == "China"
    topic_io.apply_import(seeded, parsed, ImportMode.OVERWRITE)
    assert seeded.get(Country, "CN").name == "PRC"


def test_seed_file_survives_export(seeded):
    """Export of the freshly loaded seed carries the same content as seed/topics.yaml."""
    seed = yaml.safe_load(topic_io.SEED_PATH.read_text(encoding="utf-8"))
    exported = yaml.safe_load(topic_io.export_topics(seeded))
    assert exported["countries"] == sorted(seed["countries"], key=lambda c: c["code"])
    by_slug = {t["slug"]: t for t in exported["topics"]}
    for t in seed["topics"]:
        e = by_slug[t["slug"]]
        expected = {k: v for k, v in t.items() if k != "study_models"}
        if "context" in expected:
            expected["context"] = expected["context"].strip()
        assert {k: v for k, v in e.items() if k != "origin"} == expected
        assert e["origin"] == "seed"
