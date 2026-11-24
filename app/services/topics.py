"""Topic bank: countries and topics (create, edit, list, archive)."""

import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.models import (
    Country,
    Era,
    Origin,
    Prompt,
    Run,
    RunItem,
    RunTopic,
    Topic,
    TopicCountry,
    TopicSource,
    TopicType,
)
from app.models.base import utcnow
from app.schemas.topics import CountryData, PromptData, SourceData, TopicData

ERA_ORDER = {Era.PAST: 0, Era.CENTURY_20: 1, Era.PRESENT: 2}


class TopicServiceError(ValueError):
    """A user-facing validation error from the service layer."""


# ── countries ────────────────────────────────────────────────────────────────


def list_countries(session: Session) -> list[Country]:
    return list(session.exec(select(Country).order_by(Country.name)))


def create_country(session: Session, data: CountryData) -> Country:
    if session.get(Country, data.code):
        raise TopicServiceError(f"country {data.code} already exists")
    country = Country(**data.model_dump())
    session.add(country)
    session.commit()
    return country


def update_country(session: Session, code: str, data: CountryData) -> Country:
    country = session.get(Country, code)
    if country is None:
        raise TopicServiceError(f"country {code} does not exist")
    if data.code != code:
        raise TopicServiceError("a country code can't be changed")
    country.name = data.name
    country.native_language = data.native_language
    session.add(country)
    session.commit()
    return country


def country_topic_counts(session: Session) -> dict[str, int]:
    rows = session.exec(
        select(TopicCountry.country_code, func.count()).group_by(TopicCountry.country_code)
    )
    return dict(rows.all())


# ── listing ──────────────────────────────────────────────────────────────────


@dataclass
class TopicFilters:
    q: str = ""
    country: str = ""
    era: str = ""
    type: str = ""
    archived: bool = False  # True: show archived topics only


@dataclass
class TopicRow:
    topic: Topic
    countries: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)


def list_topics(session: Session, filters: TopicFilters | None = None) -> list[TopicRow]:
    f = filters or TopicFilters()
    stmt = select(Topic).where(Topic.archived == f.archived)
    if f.q.strip():
        like = f"%{f.q.strip().lower()}%"
        stmt = stmt.where(
            or_(func.lower(Topic.title).like(like), func.lower(Topic.slug).like(like))
        )
    if f.era:
        stmt = stmt.where(Topic.era == f.era)
    if f.type:
        stmt = stmt.where(Topic.type == f.type)
    if f.country:
        stmt = stmt.where(
            col(Topic.id).in_(
                select(TopicCountry.topic_id).where(TopicCountry.country_code == f.country)
            )
        )
    topics = list(session.exec(stmt))
    rows = {t.id: TopicRow(t) for t in topics}
    if rows:
        ids = list(rows)
        for tc in session.exec(
            select(TopicCountry)
            .where(col(TopicCountry.topic_id).in_(ids))
            .order_by(TopicCountry.position)
        ):
            rows[tc.topic_id].countries.append(tc.country_code)
        for p in session.exec(
            select(Prompt)
            .where(col(Prompt.topic_id).in_(ids))
            .order_by(col(Prompt.is_default).desc(), Prompt.language)
        ):
            rows[p.topic_id].languages.append(p.language)
    return sorted(
        rows.values(),
        key=lambda r: (
            r.topic.type != TopicType.INTERNAL,
            r.countries,
            ERA_ORDER.get(r.topic.era, 9),
            r.topic.title.lower(),
        ),
    )


# ── single topic ─────────────────────────────────────────────────────────────


def get_topic(session: Session, topic_id: int) -> Topic | None:
    return session.get(Topic, topic_id)


def get_topic_by_slug(session: Session, slug: str) -> Topic | None:
    return session.exec(select(Topic).where(Topic.slug == slug)).first()


def topic_data(session: Session, topic: Topic) -> TopicData:
    """Read a stored topic back into its canonical TopicData form."""
    countries = session.exec(
        select(TopicCountry).where(TopicCountry.topic_id == topic.id).order_by(TopicCountry.position)
    )
    sources = session.exec(
        select(TopicSource).where(TopicSource.topic_id == topic.id).order_by(TopicSource.position)
    )
    prompts = session.exec(
        select(Prompt)
        .where(Prompt.topic_id == topic.id)
        .order_by(col(Prompt.is_default).desc(), Prompt.language)
    )
    return TopicData.model_construct(
        slug=topic.slug,
        title=topic.title,
        type=TopicType(topic.type),
        era=Era(topic.era),
        countries=[c.country_code for c in countries],
        context=topic.context,
        sources=[SourceData.model_construct(label=s.label, url=s.url) for s in sources],
        prompts=[
            PromptData.model_construct(
                language=p.language, text=p.text, follow_up_text=p.follow_up_text
            )
            for p in prompts
        ],
        archived=topic.archived,
        origin=Origin(topic.origin),
    )


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:80].strip("-")


def suggest_slug(session: Session, data: TopicData) -> str:
    """Seed-style slug, e.g. `china-present-uyghurs` or `china-vs-france-trade-war`."""
    names = []
    for code in data.countries:
        country = session.get(Country, code)
        names.append(slugify(country.name if country else code) or code.lower())
    if data.type == TopicType.CRISS_CROSS:
        prefix = "-vs-".join(names)
    else:
        prefix = f"{names[0]}-{data.era.value.replace('_', '-')}"
    base = slugify(f"{prefix} {data.title}") or "topic"
    slug, n = base, 2
    while get_topic_by_slug(session, slug):
        slug, n = f"{base}-{n}", n + 1
    return slug


def _check_countries(session: Session, codes: list[str]) -> None:
    missing = [c for c in codes if session.get(Country, c) is None]
    if missing:
        raise TopicServiceError(f"unknown country: {', '.join(missing)}")


def _write_children(session: Session, topic: Topic, data: TopicData) -> None:
    """Replace countries, sources and prompts. Deletes are flushed first so the
    unique constraints don't fire on re-inserted rows."""
    for model in (TopicCountry, TopicSource, Prompt):
        for row in session.exec(select(model).where(model.topic_id == topic.id)):
            session.delete(row)
    session.flush()
    for i, code in enumerate(data.countries):
        session.add(TopicCountry(topic_id=topic.id, country_code=code, position=i))
    for i, s in enumerate(data.sources):
        session.add(TopicSource(topic_id=topic.id, label=s.label, url=s.url, position=i))
    for i, p in enumerate(data.prompts):
        session.add(
            Prompt(
                topic_id=topic.id,
                language=p.language,
                text=p.text,
                follow_up_text=p.follow_up_text,
                is_default=(i == 0),
            )
        )


def create_topic(
    session: Session, data: TopicData, origin: Origin = Origin.USER, commit: bool = True
) -> Topic:
    _check_countries(session, data.countries)
    slug = data.slug or suggest_slug(session, data)
    if get_topic_by_slug(session, slug):
        raise TopicServiceError(f"a topic with slug {slug!r} already exists")
    topic = Topic(
        slug=slug,
        title=data.title,
        type=data.type,
        era=data.era,
        context=data.context,
        origin=data.origin or origin,
        archived=bool(data.archived),
    )
    session.add(topic)
    session.flush()
    _write_children(session, topic, data)
    if commit:
        session.commit()
    return topic


def update_topic(session: Session, topic: Topic, data: TopicData, commit: bool = True) -> Topic:
    """Overwrite a topic's content. The slug and origin stay as they are.

    Past runs are unaffected: run items keep their own prompt snapshot."""
    _check_countries(session, data.countries)
    topic.title = data.title
    topic.type = data.type
    topic.era = data.era
    topic.context = data.context
    if data.archived is not None:
        topic.archived = data.archived
    topic.updated_at = utcnow()
    session.add(topic)
    _write_children(session, topic, data)
    if commit:
        session.commit()
    return topic


def set_archived(session: Session, topic: Topic, archived: bool) -> Topic:
    topic.archived = archived
    topic.updated_at = utcnow()
    session.add(topic)
    session.commit()
    return topic


def is_used_by_runs(session: Session, topic: Topic) -> bool:
    in_run = session.exec(select(RunTopic).where(RunTopic.topic_id == topic.id)).first()
    in_item = session.exec(select(RunItem.id).where(RunItem.topic_id == topic.id)).first()
    return in_run is not None or in_item is not None


def delete_topic(session: Session, topic: Topic) -> str:
    """Hard-delete a topic, or archive it if any run references it. Returns what happened."""
    if is_used_by_runs(session, topic):
        set_archived(session, topic, True)
        return "archived"
    session.delete(topic)
    session.commit()
    return "deleted"


def topic_runs(session: Session, topic: Topic) -> list[Run]:
    stmt = (
        select(Run)
        .join(RunTopic, col(RunTopic.run_id) == Run.id)
        .where(RunTopic.topic_id == topic.id)
        .order_by(col(Run.created_at).desc())
    )
    return list(session.exec(stmt))
