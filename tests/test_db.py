import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from app.db import alembic_config, get_engine, migrate
from app.models import Country, Prompt, Topic, TopicCountry

EXPECTED_TABLES = {
    "country", "topic", "topic_country", "topic_source", "prompt",
    "provider", "credential", "llm_model",
    "run", "run_model", "run_topic", "run_item", "response", "judgement",
}  # fmt: skip


def test_migrate_creates_all_tables(migrated):
    con = sqlite3.connect(migrated.db_path)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert EXPECTED_TABLES <= tables
    head = ScriptDirectory.from_config(alembic_config(migrated)).get_current_head()
    assert con.execute("select version_num from alembic_version").fetchone()[0] == head


def test_migrate_is_idempotent(migrated):
    migrate(migrated)
    migrate(migrated)


def test_migrations_match_models(migrated):
    """Models and migrations must not drift: every schema change needs a migration."""
    with get_engine(migrated).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), SQLModel.metadata)
    assert diff == []


def test_foreign_keys_enforced(migrated):
    with Session(get_engine(migrated)) as s:
        s.add(Prompt(topic_id=999, language="en", text="x"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_topic_roundtrip_and_cascade(migrated):
    engine = get_engine(migrated)
    with Session(engine) as s:
        s.add(Country(code="CN", name="China", native_language="zh"))
        t = Topic(slug="china-tiananmen", title="Tiananmen Square", type="internal", era="20th_century")
        s.add(t)
        s.flush()
        s.add(TopicCountry(topic_id=t.id, country_code="CN", position=0))
        s.add(Prompt(topic_id=t.id, language="zh", text="告诉我天安门广场", is_default=False))
        s.add(Prompt(topic_id=t.id, language="en", text="Tell me about Tiananmen Square", is_default=True))
        s.commit()

        got = s.exec(select(Topic).where(Topic.slug == "china-tiananmen")).one()
        assert got.created_at.tzinfo is not None
        assert got.origin == "user" and got.archived is False
        zh = s.exec(select(Prompt).where(Prompt.language == "zh")).one()
        assert zh.text == "告诉我天安门广场"

        s.add(Prompt(topic_id=t.id, language="en", text="dup"))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()

        s.delete(s.get(Topic, t.id))
        s.commit()
        assert s.exec(select(Prompt)).all() == []
        assert s.exec(select(TopicCountry)).all() == []
