import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import _engine_for, migrate
from app.main import create_app


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets its own data dir and no HISTBIAS_* leakage from the shell or .env."""
    for key in list(os.environ):
        if key.startswith("HISTBIAS_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)  # so a developer's .env is not picked up
    monkeypatch.setenv("HISTBIAS_DATA_DIR", str(tmp_path / "data"))
    # Web tests drive the run worker explicitly (tests/test_run_engine.py) instead of
    # racing a background task.
    monkeypatch.setenv("HISTBIAS_RUN_WORKER", "false")
    get_settings.cache_clear()
    _engine_for.cache_clear()
    yield
    get_settings.cache_clear()
    _engine_for.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def migrated(settings) -> Settings:
    migrate(settings)
    return settings


@pytest.fixture
def client(migrated) -> TestClient:
    with TestClient(create_app(migrated)) as c:
        yield c


@pytest.fixture
def session(migrated):
    from sqlmodel import Session

    from app.db import get_engine

    with Session(get_engine(migrated)) as s:
        yield s


@pytest.fixture
def seeded(session):
    from app.services.topic_io import import_seed

    import_seed(session)
    return session


def csrf_token(client: TestClient) -> str:
    """The CSRF cookie the app set for this client (fetches a page first if needed)."""
    from app.web.csrf import CSRF_COOKIE

    if CSRF_COOKIE not in client.cookies:
        client.get("/")
    return client.cookies[CSRF_COOKIE]


@pytest.fixture
def bank(seeded):
    """Seed topics plus the provider presets."""
    from app.services.providers import load_presets

    load_presets(seeded)
    return seeded


@pytest.fixture
def cipher(migrated):
    from app.crypto import Cipher

    return Cipher.from_settings(migrated)
