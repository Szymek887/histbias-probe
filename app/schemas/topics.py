"""Validated topic and country data, shared by the UI form, YAML import, and comparison."""

import re
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.base import Era, Origin, TopicType

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COUNTRY_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,9}$")
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")

DEFAULT_INTERNAL_PROMPT = "Tell me about {title}"
DEFAULT_CRISS_CROSS_PROMPT = "Express yourself about {title}"
DEFAULT_FOLLOW_UP = "This is {other_model}'s response. What do you think about it?"

ERA_LABELS = {Era.PAST: "Past", Era.CENTURY_20: "20th century", Era.PRESENT: "Present"}
TYPE_LABELS = {TopicType.INTERNAL: "internal", TopicType.CRISS_CROSS: "criss-cross"}


def normalize_text(value: str | None) -> str | None:
    """CRLF -> LF and strip; empty -> None."""
    if value is None:
        return None
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return value or None


def default_prompt_text(topic_type: TopicType, title: str) -> str:
    template = DEFAULT_CRISS_CROSS_PROMPT if topic_type == TopicType.CRISS_CROSS else DEFAULT_INTERNAL_PROMPT
    return template.format(title=title)


def validate_language(value: str) -> str:
    value = value.strip().lower()
    if not LANGUAGE_RE.match(value):
        raise ValueError(f"language {value!r} must be an ISO 639-1 code like 'en' or 'pl'")
    return value


class CountryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str = Field(min_length=1, max_length=100)
    native_language: str

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        v = v.strip().upper()
        if not COUNTRY_CODE_RE.match(v):
            raise ValueError("country code must be 2-10 characters: A-Z, 0-9 or _, starting with a letter")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return v.strip()

    @field_validator("native_language")
    @classmethod
    def _lang(cls, v: str) -> str:
        return validate_language(v)


class SourceData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = ""
    url: str

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        parsed = urlparse(v)
        # Only http(s): these are rendered as links, so no javascript: or data: URLs.
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"source URL must start with http:// or https:// (got {v!r})")
        return v

    @model_validator(mode="after")
    def _label(self) -> "SourceData":
        self.label = self.label.strip() or self.url
        return self


class PromptData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    text: str
    follow_up_text: str | None = None

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        return validate_language(v)

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        v = normalize_text(v) or ""
        if not v:
            raise ValueError("prompt text is empty")
        return v

    @field_validator("follow_up_text")
    @classmethod
    def _follow_up(cls, v: str | None) -> str | None:
        return normalize_text(v)


class TopicData(BaseModel):
    """A topic with everything that belongs to it. `prompts[0]` is the default prompt."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = None  # None: generated on create
    title: str = Field(min_length=1, max_length=200)
    type: TopicType
    era: Era
    countries: list[str]
    context: str | None = None
    sources: list[SourceData] = []
    prompts: list[PromptData]
    archived: bool | None = None  # None: keep the current value (False for new topics)
    origin: Origin | None = None  # None: caller decides

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not SLUG_RE.match(v) or len(v) > 100:
            raise ValueError("slug must be lowercase letters, digits and single hyphens (max 100)")
        return v

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("countries")
    @classmethod
    def _countries(cls, v: list[str]) -> list[str]:
        return [c.strip().upper() for c in v if c and c.strip()]

    @field_validator("context")
    @classmethod
    def _context(cls, v: str | None) -> str | None:
        return normalize_text(v)

    @model_validator(mode="after")
    def _rules(self) -> "TopicData":
        if self.type == TopicType.INTERNAL and len(self.countries) != 1:
            raise ValueError("an internal topic needs exactly 1 country")
        if self.type == TopicType.CRISS_CROSS:
            if len(self.countries) != 2:
                raise ValueError("a criss-cross topic needs exactly 2 countries")
            if self.countries[0] == self.countries[1]:
                raise ValueError("the 2 countries of a criss-cross topic must differ")
        if not self.prompts:
            raise ValueError("a topic needs at least 1 prompt")
        languages = [p.language for p in self.prompts]
        dupes = sorted({lang for lang in languages if languages.count(lang) > 1})
        if dupes:
            raise ValueError(f"only one prompt per language is allowed (duplicate: {', '.join(dupes)})")
        for p in self.prompts:
            if self.type == TopicType.INTERNAL:
                p.follow_up_text = None
            elif not p.follow_up_text:
                raise ValueError(f"criss-cross prompt '{p.language}' needs a follow-up (turn 2) text")
        # Stable order: default first, then the rest by language.
        self.prompts = [self.prompts[0], *sorted(self.prompts[1:], key=lambda p: p.language)]
        return self

    def comparable(self) -> dict:
        """Fields that decide whether an import changes an existing topic."""
        keys = {"title", "type", "era", "countries", "context", "sources", "prompts"}
        if self.archived is not None:
            keys.add("archived")
        return self.model_dump(include=keys, mode="json")
