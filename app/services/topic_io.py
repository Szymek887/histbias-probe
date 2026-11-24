"""YAML import and export of topics (format: docs/03-data-model.md, "Seed and import format")."""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlmodel import Session, col, select

from app.models import Country, Origin, Topic
from app.schemas.topics import (
    SLUG_RE,
    CountryData,
    SourceData,
    TopicData,
)
from app.services import topics as topic_service

MAX_IMPORT_BYTES = 5 * 1024 * 1024
FORMAT_VERSION = 1
SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "topics.yaml"


class ImportFormatError(ValueError):
    """The file is not valid YAML or not a topic file at all."""


class ImportMode(StrEnum):
    SKIP = "skip"  # leave existing topics/countries untouched
    OVERWRITE = "overwrite"


class Status(StrEnum):
    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


# ── file schema ──────────────────────────────────────────────────────────────


class _PromptYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str
    text: str
    follow_up_text: str | None = None


class TopicYaml(BaseModel):
    """One topic as written in YAML. `prompt` is the default prompt (language `language`,
    English if omitted); `prompts` holds the other language variants."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    type: str
    countries: list[str]
    era: str
    language: str = "en"
    prompt: str
    follow_up_prompt: str | None = None
    study_models: list[str] | None = None  # informational, not imported
    context: str | None = None
    sources: list[SourceData] = []
    prompts: list[_PromptYaml] = []
    archived: bool | None = None
    origin: Origin | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError("slug must be lowercase letters, digits and single hyphens")
        return v

    def to_data(self) -> TopicData:
        prompts = [
            {"language": self.language, "text": self.prompt, "follow_up_text": self.follow_up_prompt},
            *(p.model_dump() for p in self.prompts),
        ]
        return TopicData.model_validate(
            {
                "slug": self.slug,
                "title": self.title,
                "type": self.type,
                "era": self.era,
                "countries": self.countries,
                "context": self.context,
                "sources": [s.model_dump() for s in self.sources],
                "prompts": prompts,
                "archived": self.archived,
                "origin": self.origin,
            }
        )


def _format_errors(prefix: str, exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"])
        msg = err["msg"].removeprefix("Value error, ")
        out.append(f"{prefix}{' ' + loc if loc else ''}: {msg}")
    return out


@dataclass
class ParsedFile:
    countries: list[CountryData] = field(default_factory=list)
    topics: list[TopicData] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_topic_file(content: bytes | str) -> ParsedFile:
    """Parse and validate a topic YAML file. Raises ImportFormatError for unreadable files;
    per-topic problems are collected in `errors` so the user sees all of them at once."""
    if isinstance(content, bytes):
        if len(content) > MAX_IMPORT_BYTES:
            raise ImportFormatError("the file is larger than 5 MB")
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise ImportFormatError("the file is not UTF-8 text") from e
    try:
        doc = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ImportFormatError(f"not valid YAML: {e}") from e
    if not isinstance(doc, dict) or "topics" not in doc:
        raise ImportFormatError("not a topic file: expected a mapping with a 'topics' list")
    if doc.get("version", FORMAT_VERSION) != FORMAT_VERSION:
        raise ImportFormatError(f"unsupported format version {doc.get('version')!r}")
    raw_countries = doc.get("countries") or []
    raw_topics = doc.get("topics") or []
    if not isinstance(raw_countries, list) or not isinstance(raw_topics, list):
        raise ImportFormatError("'countries' and 'topics' must be lists")

    parsed = ParsedFile()
    for i, raw in enumerate(raw_countries):
        try:
            parsed.countries.append(CountryData.model_validate(raw))
        except ValidationError as e:
            parsed.errors += _format_errors(f"countries[{i}]", e)
    codes = [c.code for c in parsed.countries]
    for dup in sorted({c for c in codes if codes.count(c) > 1}):
        parsed.errors.append(f"country {dup} is listed more than once")

    seen: set[str] = set()
    for i, raw in enumerate(raw_topics):
        label = f"topics[{i}]"
        if isinstance(raw, dict) and isinstance(raw.get("slug"), str):
            label += f" ({raw['slug']})"
        try:
            data = TopicYaml.model_validate(raw).to_data()
        except ValidationError as e:
            parsed.errors += _format_errors(label, e)
            continue
        if data.slug in seen:
            parsed.errors.append(f"{label}: slug is used more than once in the file")
            continue
        seen.add(data.slug)
        parsed.topics.append(data)
    return parsed


# ── preview and apply ────────────────────────────────────────────────────────


@dataclass
class CountryPreview:
    data: CountryData
    status: Status


@dataclass
class TopicPreview:
    data: TopicData
    status: Status
    changed_fields: list[str] = field(default_factory=list)


@dataclass
class ImportPreview:
    countries: list[CountryPreview] = field(default_factory=list)
    topics: list[TopicPreview] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def count(self, kind: str, status: Status) -> int:
        return sum(1 for x in getattr(self, kind) if x.status == status)

    @property
    def ok(self) -> bool:
        return not self.errors


def diff_fields(existing: TopicData, incoming: TopicData) -> list[str]:
    new = incoming.comparable()
    old = existing.model_dump(include=set(new), mode="json")
    return [k for k in new if new[k] != old[k]]


def preview_import(session: Session, parsed: ParsedFile) -> ImportPreview:
    preview = ImportPreview(errors=list(parsed.errors))
    known = {c.code for c in topic_service.list_countries(session)}
    for c in parsed.countries:
        existing = session.get(Country, c.code)
        if existing is None:
            status = Status.NEW
        elif (existing.name, existing.native_language) != (c.name, c.native_language):
            status = Status.CHANGED
        else:
            status = Status.UNCHANGED
        preview.countries.append(CountryPreview(c, status))
        known.add(c.code)

    for t in parsed.topics:
        missing = [c for c in t.countries if c not in known]
        if missing:
            preview.errors.append(
                f"topic {t.slug}: unknown country {', '.join(missing)} (add it to 'countries')"
            )
            continue
        existing = topic_service.get_topic_by_slug(session, t.slug)
        if existing is None:
            preview.topics.append(TopicPreview(t, Status.NEW))
            continue
        changed = diff_fields(topic_service.topic_data(session, existing), t)
        preview.topics.append(
            TopicPreview(t, Status.CHANGED if changed else Status.UNCHANGED, changed)
        )
    return preview


@dataclass
class ImportResult:
    countries_added: int = 0
    countries_updated: int = 0
    topics_added: int = 0
    topics_updated: int = 0
    topics_skipped: int = 0
    topics_unchanged: int = 0


def apply_import(
    session: Session,
    parsed: ParsedFile,
    mode: ImportMode,
    default_origin: Origin = Origin.USER,
) -> ImportResult:
    """Apply a parsed file in one transaction. Refuses files with errors."""
    preview = preview_import(session, parsed)
    if not preview.ok:
        raise ImportFormatError("the file has errors; fix them before importing")
    result = ImportResult()
    try:
        for cp in preview.countries:
            if cp.status == Status.NEW:
                session.add(Country(**cp.data.model_dump()))
                result.countries_added += 1
            elif cp.status == Status.CHANGED and mode == ImportMode.OVERWRITE:
                country = session.get(Country, cp.data.code)
                country.name = cp.data.name
                country.native_language = cp.data.native_language
                session.add(country)
                result.countries_updated += 1
        session.flush()
        for tp in preview.topics:
            if tp.status == Status.NEW:
                topic_service.create_topic(session, tp.data, origin=default_origin, commit=False)
                result.topics_added += 1
            elif tp.status == Status.UNCHANGED:
                result.topics_unchanged += 1
            elif mode == ImportMode.OVERWRITE:
                topic = topic_service.get_topic_by_slug(session, tp.data.slug)
                topic_service.update_topic(session, topic, tp.data, commit=False)
                result.topics_updated += 1
            else:
                result.topics_skipped += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return result


def import_seed(
    session: Session, path: Path = SEED_PATH, mode: ImportMode = ImportMode.SKIP
) -> ImportResult:
    """Load seed/topics.yaml. Idempotent: matched by slug, existing topics are kept
    unless mode is OVERWRITE."""
    parsed = parse_topic_file(path.read_bytes())
    return apply_import(session, parsed, mode, default_origin=Origin.SEED)


# ── export ───────────────────────────────────────────────────────────────────


class _Literal(str):
    pass


class _Dumper(yaml.SafeDumper):
    pass


def _repr_str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if isinstance(value, _Literal) or "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style=style)


_Dumper.add_representer(_Literal, _repr_str)
_Dumper.add_representer(str, _repr_str)


def topic_to_yaml_dict(data: TopicData) -> dict[str, Any]:
    default, *others = data.prompts
    out: dict[str, Any] = {
        "slug": data.slug,
        "title": data.title,
        "type": data.type.value,
        "countries": list(data.countries),
        "era": data.era.value,
    }
    if default.language != "en":
        out["language"] = default.language
    out["prompt"] = default.text
    if default.follow_up_text:
        out["follow_up_prompt"] = default.follow_up_text
    if data.context:
        out["context"] = _Literal(data.context)
    if data.sources:
        out["sources"] = [{"label": s.label, "url": s.url} for s in data.sources]
    if others:
        out["prompts"] = [
            {"language": p.language, "text": p.text}
            | ({"follow_up_text": p.follow_up_text} if p.follow_up_text else {})
            for p in others
        ]
    if data.archived:
        out["archived"] = True
    if data.origin:
        out["origin"] = data.origin.value
    return out


def export_topics(session: Session, topic_ids: list[int] | None = None) -> str:
    """All topics (including archived) or the given ones, with the countries they use."""
    stmt = select(Topic).order_by(Topic.slug)
    if topic_ids is not None:
        stmt = stmt.where(col(Topic.id).in_(topic_ids))
    topics = [topic_service.topic_data(session, t) for t in session.exec(stmt)]
    used = {c for t in topics for c in t.countries}
    countries = [
        {"code": c.code, "name": c.name, "native_language": c.native_language}
        for c in session.exec(select(Country).order_by(Country.code))
        if topic_ids is None or c.code in used
    ]
    doc = {
        "version": FORMAT_VERSION,
        "countries": countries,
        "topics": [topic_to_yaml_dict(t) for t in topics],
    }
    return yaml.dump(doc, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000)
