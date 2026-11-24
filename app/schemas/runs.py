"""Run wizard input. Stored (as `model_dump()`) in `run.config["settings"]`."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LANGUAGE_CHOICES = {"en": "English", "native": "Native language of the topic's country (if a prompt exists)"}
PAIRING_CHOICES = {
    "auto": "Automatic by country: every model from the topic's first country × every model from its second",
    "manual": "Choose pairs manually (used for every selected criss-cross topic)",
}
INTERNAL_MODEL_CHOICES = {
    "all": "All selected models",
    "home": "Only models from the topic's country",
}


class RunSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    topic_ids: list[int] = Field(min_length=1)
    model_ids: list[int] = Field(min_length=1)
    languages: list[Literal["en", "native"]] = Field(min_length=1)
    internal_models: Literal["all", "home"] = "all"
    pairing: Literal["auto", "manual"] = "auto"
    pairs: list[tuple[int, int]] = []  # manual pairing: (model A, model B)
    judge_model_id: int | None = None  # None = no judge, answers only
    repetitions: int = Field(default=1, ge=1, le=20)
    # None = each model's own setting (or the provider default); open question 2.
    temperature: float | None = Field(default=None, ge=0, le=2)
    concurrency: int = Field(default=4, ge=1, le=32)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("topic_ids", "model_ids", "languages")
    @classmethod
    def _dedupe(cls, v: list) -> list:
        return list(dict.fromkeys(v))

    @field_validator("pairs")
    @classmethod
    def _pairs(cls, v: list[tuple[int, int]]) -> list[tuple[int, int]]:
        for a, b in v:
            if a == b:
                raise ValueError("a model can't be paired with itself")
        return list(dict.fromkeys(v))
