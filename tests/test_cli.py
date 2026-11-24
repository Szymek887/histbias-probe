from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


def test_init_creates_db_and_is_rerunnable(settings):
    assert not settings.data_dir.exists()
    r = runner.invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    assert settings.db_path.exists()
    r = runner.invoke(app, ["init"])
    assert r.exit_code == 0, r.output


def test_serve_requires_init(settings, monkeypatch):
    called = []
    monkeypatch.setattr("app.cli.uvicorn.run", lambda *a, **k: called.append(k))
    r = runner.invoke(app, ["serve"])
    assert r.exit_code == 1
    assert "histbias init" in r.output
    assert called == []


def test_serve_defaults_to_localhost(settings, monkeypatch):
    called = []
    monkeypatch.setattr("app.cli.uvicorn.run", lambda *a, **k: called.append(k))
    assert runner.invoke(app, ["init"]).exit_code == 0
    r = runner.invoke(app, ["serve"])
    assert r.exit_code == 0, r.output
    assert called[0]["host"] == "127.0.0.1"
    assert called[0]["port"] == 8000
    assert called[0]["factory"] is True


def test_init_loads_seed_once(settings):
    r = runner.invoke(app, ["init"])
    assert "50 added" in r.output
    r = runner.invoke(app, ["init"])
    assert "0 added" in r.output and "50 unchanged" in r.output


def test_init_no_seed(settings):
    r = runner.invoke(app, ["init", "--no-seed"])
    assert r.exit_code == 0 and "Topics:" not in r.output


def test_export_and_import_topics_commands(settings, tmp_path, monkeypatch):
    assert runner.invoke(app, ["init"]).exit_code == 0
    out = tmp_path / "topics.yaml"
    r = runner.invoke(app, ["export-topics", str(out)])
    assert r.exit_code == 0, r.output
    assert out.read_text(encoding="utf-8").count("- slug:") == 50

    r = runner.invoke(app, ["import-topics", str(out)])
    assert r.exit_code == 0 and "50 unchanged" in r.output

    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\ntopics:\n  - slug: x\n", encoding="utf-8")
    r = runner.invoke(app, ["import-topics", str(bad)])
    assert r.exit_code == 1
    r = runner.invoke(app, ["seed", "--overwrite"])
    assert r.exit_code == 0 and "50 unchanged" in r.output
