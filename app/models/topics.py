"""Question bank tables: country, topic, topic_country, topic_source, prompt."""

from datetime import datetime

from sqlalchemy import Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import Origin, created_at_field, updated_at_field


class Country(SQLModel, table=True):
    __tablename__ = "country"

    code: str = Field(primary_key=True)  # e.g. CN, RU, PL, ARAB; user-defined allowed
    name: str
    native_language: str  # ISO 639-1


class Topic(SQLModel, table=True):
    __tablename__ = "topic"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    title: str
    type: str  # TopicType
    era: str  # Era
    context: str | None = Field(default=None, sa_type=Text)
    origin: str = Field(default=Origin.USER)
    archived: bool = Field(default=False)
    created_at: datetime = created_at_field()
    updated_at: datetime = updated_at_field()


class TopicCountry(SQLModel, table=True):
    """1 row for an internal topic, 2 for criss-cross (enforced in the service layer)."""

    __tablename__ = "topic_country"
    __table_args__ = (
        UniqueConstraint("topic_id", "country_code"),
        UniqueConstraint("topic_id", "position"),
    )

    id: int | None = Field(default=None, primary_key=True)
    topic_id: int = Field(foreign_key="topic.id", ondelete="CASCADE", index=True)
    country_code: str = Field(foreign_key="country.code")
    position: int = 0  # 0 or 1


class TopicSource(SQLModel, table=True):
    __tablename__ = "topic_source"

    id: int | None = Field(default=None, primary_key=True)
    topic_id: int = Field(foreign_key="topic.id", ondelete="CASCADE", index=True)
    label: str
    url: str
    position: int = 0


class Prompt(SQLModel, table=True):
    __tablename__ = "prompt"
    __table_args__ = (UniqueConstraint("topic_id", "language"),)

    id: int | None = Field(default=None, primary_key=True)
    topic_id: int = Field(foreign_key="topic.id", ondelete="CASCADE", index=True)
    language: str  # ISO 639-1
    text: str = Field(sa_type=Text)
    # Criss-cross turn 2. `{other_model}` is replaced by the partner's display name.
    follow_up_text: str | None = Field(default=None, sa_type=Text)
    is_default: bool = Field(default=False)
    created_at: datetime = created_at_field()
    updated_at: datetime = updated_at_field()
