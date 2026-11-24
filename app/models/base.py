"""Shared helpers for SQLModel tables."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

# Stable constraint names, so Alembic batch migrations on SQLite can find them.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Timezone-aware UTC now. sqlmodel stores datetimes as UTC and rejects naive values."""
    return datetime.now(UTC)


def created_at_field() -> Any:
    return Field(default_factory=utcnow, nullable=False)


def updated_at_field() -> Any:
    return Field(
        default_factory=utcnow,
        nullable=False,
        sa_column_kwargs={"onupdate": utcnow},
    )


def json_field(default_factory: Any = dict, nullable: bool = False) -> Any:
    return Field(default_factory=default_factory, sa_column=Column(JSON, nullable=nullable))


# Value sets. Columns are stored as plain TEXT holding these values; the service
# layer validates them.


class TopicType(StrEnum):
    INTERNAL = "internal"
    CRISS_CROSS = "criss_cross"


class Era(StrEnum):
    PAST = "past"
    CENTURY_20 = "20th_century"
    PRESENT = "present"


class Origin(StrEnum):
    SEED = "seed"
    USER = "user"


class ProviderOrigin(StrEnum):
    PRESET = "preset"
    USER = "user"


class ProviderKind(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    GIGACHAT = "gigachat"
    MOCK = "mock"


class RunStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RunItemStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    ANSWERED = "answered"
    JUDGED = "judged"
    ERROR = "error"
    JUDGE_FAILED = "judge_failed"
    SKIPPED = "skipped"


class Censorship(StrEnum):
    NONE = "none"
    SOFT = "soft"
    HARD = "hard"
