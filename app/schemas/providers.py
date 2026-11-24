"""Validated input for providers and models."""

import json
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import ProviderKind

# Keys in non-secret `extra` that look like someone pasted a secret there.
_SECRETISH = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization|client[_-]?secret)")
# ...except these known non-secret settings.
_ALLOWED_SECRETISH = {"secret_extra_fields", "auth_header", "auth_prefix", "max_tokens_field"}


def validate_base_url(v: str, kind: str | None = None) -> str:
    v = (v or "").strip().rstrip("/")
    if not v:
        return v
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("base URL must start with http:// or https://")
    if parsed.username or parsed.password:
        raise ValueError("don't put credentials in the base URL; use Settings → API keys")
    if parsed.query:
        raise ValueError("the base URL must not contain a query string")
    return v


def check_extra(extra: dict[str, Any]) -> dict[str, Any]:
    for key in extra:
        if key not in _ALLOWED_SECRETISH and _SECRETISH.search(key):
            raise ValueError(
                f"setting “{key}” looks like a secret; secrets belong in Settings → API keys, "
                "where they are encrypted"
            )
    return extra


def parse_extra_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"advanced settings are not valid JSON: {e.msg} (line {e.lineno})") from e
    if not isinstance(value, dict):
        raise ValueError("advanced settings must be a JSON object, e.g. {\"supports_json_mode\": true}")
    return value


class ProviderData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    kind: ProviderKind
    base_url: str = ""
    extra: dict[str, Any] = {}

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("extra")
    @classmethod
    def _extra(cls, v: dict[str, Any]) -> dict[str, Any]:
        return check_extra(v)

    @model_validator(mode="after")
    def _url(self) -> "ProviderData":
        self.base_url = validate_base_url(self.base_url)
        return self


class ModelData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: int
    display_name: str = Field(min_length=1, max_length=100)
    model_id: str = Field(default="", max_length=300)
    country_code: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0, le=200_000)
    system_prompt: str | None = None
    enabled: bool = False
    notes: str | None = None

    @field_validator("display_name", "model_id")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("country_code", "system_prompt", "notes")
    @classmethod
    def _empty_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.replace("\r\n", "\n").strip()
        return v or None

    @model_validator(mode="after")
    def _enabled_needs_id(self) -> "ModelData":
        if self.enabled and not self.model_id:
            raise ValueError("set the API model ID before enabling the model")
        if self.country_code:
            self.country_code = self.country_code.upper()
        return self
