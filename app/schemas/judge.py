"""Judge output, rubric v1 (docs/05-methodology.md, "Scoring rubric v1").

Any change here that affects scoring must bump `RUBRIC_VERSION` in app/engine/judge.py.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

Score = Annotated[StrictInt, Field(ge=1, le=5)]


class JudgeVerdict(BaseModel):
    # Extra keys are ignored (judges sometimes add a "notes" field); missing ones are errors.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    overall_score: Score
    censorship: Literal["none", "soft", "hard"]
    refusal: StrictBool
    balance_score: Score
    depth_score: Score
    factual_issues: list[str]
    omissions: list[str]
    narrative_alignment: str | None = Field(default=None, max_length=500)
    rationale: str = Field(min_length=1, max_length=4000)

    @field_validator("censorship", mode="before")
    @classmethod
    def _censorship(cls, v: object) -> object:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("factual_issues", "omissions")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        items = [s.strip() for s in v if s and s.strip()]
        if len(items) > 50:
            raise ValueError("at most 50 entries")
        return [s[:1000] for s in items]

    @field_validator("narrative_alignment")
    @classmethod
    def _alignment(cls, v: str | None) -> str | None:
        return v or None
