"""Run and result tables. Rows here are snapshots: never rewrite them when topics or
models change (docs/03-data-model.md, "Snapshot rule")."""

from datetime import datetime
from typing import Any

from sqlalchemy import Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import (
    RunItemStatus,
    RunStatus,
    created_at_field,
    json_field,
    updated_at_field,
)


class Run(SQLModel, table=True):
    __tablename__ = "run"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    status: str = Field(default=RunStatus.DRAFT, index=True)
    judge_model_id: int | None = Field(default=None, foreign_key="llm_model.id")  # null = no judge
    config: dict[str, Any] = json_field()  # full frozen snapshot
    started_at: datetime | None = None
    finished_at: datetime | None = None
    notes: str | None = Field(default=None, sa_type=Text)
    created_at: datetime = created_at_field()
    updated_at: datetime = updated_at_field()


class RunModel(SQLModel, table=True):
    __tablename__ = "run_model"

    run_id: int = Field(foreign_key="run.id", ondelete="CASCADE", primary_key=True)
    llm_model_id: int = Field(foreign_key="llm_model.id", primary_key=True)


class RunTopic(SQLModel, table=True):
    __tablename__ = "run_topic"

    run_id: int = Field(foreign_key="run.id", ondelete="CASCADE", primary_key=True)
    topic_id: int = Field(foreign_key="topic.id", primary_key=True)


class RunItem(SQLModel, table=True):
    __tablename__ = "run_item"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", ondelete="CASCADE", index=True)
    topic_id: int = Field(foreign_key="topic.id")
    mode: str  # TopicType
    language: str
    repetition: int = 1
    model_a_id: int = Field(foreign_key="llm_model.id")
    model_b_id: int | None = Field(default=None, foreign_key="llm_model.id")
    prompt_text: str = Field(sa_type=Text)  # snapshot
    follow_up_text: str | None = Field(default=None, sa_type=Text)  # snapshot
    status: str = Field(default=RunItemStatus.PENDING, index=True)
    error: str | None = Field(default=None, sa_type=Text)
    attempts: int = 0
    created_at: datetime = created_at_field()
    updated_at: datetime = updated_at_field()


class Response(SQLModel, table=True):
    __tablename__ = "response"

    id: int | None = Field(default=None, primary_key=True)
    run_item_id: int = Field(foreign_key="run_item.id", ondelete="CASCADE", index=True)
    llm_model_id: int = Field(foreign_key="llm_model.id")
    # Snapshot of who answered, so model edits don't change past results.
    model_id_snapshot: str = ""
    display_name_snapshot: str = ""
    turn: int = 1  # 1 or 2
    messages: list[dict[str, Any]] = json_field(default_factory=list)  # bodies only, no headers
    text: str = Field(default="", sa_type=Text)
    finish_reason: str | None = None
    refusal_detected: bool = False
    error: str | None = Field(default=None, sa_type=Text)
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0
    raw: dict[str, Any] = json_field()  # raw API response body
    created_at: datetime = created_at_field()


class Judgement(SQLModel, table=True):
    """One judge's verdict on one response. A failed judgement (invalid JSON after the
    repair retry, or the judge's API blocked the content) keeps its raw output and `error`,
    with the score fields left empty."""

    __tablename__ = "judgement"
    __table_args__ = (UniqueConstraint("response_id", "judge_model_id", "rubric_version"),)

    id: int | None = Field(default=None, primary_key=True)
    response_id: int = Field(foreign_key="response.id", ondelete="CASCADE", index=True)
    judge_model_id: int = Field(foreign_key="llm_model.id")
    rubric_version: str
    overall_score: int | None = None  # 1-5
    censorship: str | None = None  # Censorship
    refusal: bool | None = None
    balance_score: int | None = None  # 1-5
    depth_score: int | None = None  # 1-5
    factual_issues: list[str] = json_field(default_factory=list)
    omissions: list[str] = json_field(default_factory=list)
    narrative_alignment: str | None = None
    rationale: str | None = Field(default=None, sa_type=Text)
    raw_output: str = Field(sa_type=Text)
    judge_prompt: str = Field(sa_type=Text)
    repaired: bool = False  # valid only after the "fix your JSON" retry
    error: str | None = Field(default=None, sa_type=Text)  # set = judge_failed
    created_at: datetime = created_at_field()

    @property
    def ok(self) -> bool:
        return self.error is None and self.overall_score is not None
