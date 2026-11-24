"""Provider, credential and model registry tables."""

from datetime import datetime
from typing import Any

from sqlalchemy import LargeBinary, Text
from sqlmodel import Field, SQLModel

from app.models.base import ProviderOrigin, created_at_field, json_field, updated_at_field


class Provider(SQLModel, table=True):
    __tablename__ = "provider"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    kind: str  # ProviderKind
    base_url: str = ""
    extra: dict[str, Any] = json_field()  # non-secret settings only
    origin: str = Field(default=ProviderOrigin.USER)
    created_at: datetime = created_at_field()
    updated_at: datetime = updated_at_field()


class Credential(SQLModel, table=True):
    """One encrypted token per provider. Never store or render the plain token."""

    __tablename__ = "credential"

    id: int | None = Field(default=None, primary_key=True)
    provider_id: int = Field(foreign_key="provider.id", ondelete="CASCADE", unique=True)
    secret_enc: bytes = Field(sa_type=LargeBinary)  # Fernet token
    extra_enc: bytes | None = Field(default=None, sa_type=LargeBinary)  # encrypted JSON
    hint: str = ""  # last 4 characters, for `••••abcd`
    last_test_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_message: str | None = None
    created_at: datetime = created_at_field()
    updated_at: datetime = updated_at_field()


class LlmModel(SQLModel, table=True):
    __tablename__ = "llm_model"

    id: int | None = Field(default=None, primary_key=True)
    provider_id: int = Field(foreign_key="provider.id", index=True)
    display_name: str
    model_id: str  # API model name; "" means not configured yet
    country_code: str | None = Field(default=None, foreign_key="country.code")
    temperature: float | None = None
    max_tokens: int | None = None
    system_prompt: str | None = Field(default=None, sa_type=Text)
    enabled: bool = Field(default=False)
    notes: str | None = Field(default=None, sa_type=Text)
    created_at: datetime = created_at_field()
    updated_at: datetime = updated_at_field()
