from pathlib import Path

from app.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("HISTBIAS_DATA_DIR")
    s = Settings()
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.data_dir == Path("./data")
    assert s.secret_key is None
    assert s.max_concurrency == 4
    assert s.request_timeout == 120
    assert s.is_local_bind


def test_env_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("HISTBIAS_PORT", "9001")
    monkeypatch.setenv("HISTBIAS_HOST", "0.0.0.0")
    monkeypatch.setenv("HISTBIAS_SECRET_KEY", "not-a-real-key")
    s = Settings()
    assert s.port == 9001
    assert not s.is_local_bind
    # SecretStr never shows the value in repr/str.
    assert "not-a-real-key" not in repr(s)


def test_dotenv_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("HISTBIAS_MAX_CONCURRENCY=7\n")
    assert Settings().max_concurrency == 7


def test_ensure_data_dir(tmp_path):
    s = Settings(data_dir=tmp_path / "a" / "b")
    assert not s.data_dir.exists()
    s.ensure_data_dir()
    assert s.data_dir.is_dir()
    assert s.database_url.endswith("a/b/histbias.db")
