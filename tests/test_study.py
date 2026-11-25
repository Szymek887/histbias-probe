"""M7 demo: comparing a run with the AGH study's human ranking (seed/study_ranking.yaml)."""

import pytest
from typer.testing import CliRunner

from app.cli import app as cli
from app.services import study
from app.services.results import ModelScore
from tests.test_results import full_bank_run


def score(pk, name, mean, judged=10):
    return ModelScore(model_pk=pk, name=name, country=None, judged=judged, mean=mean)


def test_seed_ranking_matches_the_wiki_table():
    r = study.load_ranking()
    assert [m.place for m in r.models] == list(range(1, 10))
    assert [(m.name, m.mean) for m in r.models[:2]] == [("Le Chat (Mistral)", 4.8), ("Claude", 4.7)]
    assert r.models[-1].name == "Baidu (ERNIE)" and r.models[-1].mean == 2.5
    assert next(m for m in r.models if m.name == "Falcon").note  # criss-cross only


def test_every_study_model_has_a_preset():
    """Each study model is matched by a preset in seed/providers.yaml (display name or API ID)."""
    import yaml

    presets = yaml.safe_load(open(study.RANKING_PATH.parent / "providers.yaml", encoding="utf-8"))
    models = [m for p in presets["providers"] for m in p["models"]]
    ranking = study.load_ranking()
    matched = {study.match_model(m["display_name"], m["model_id"], ranking).name
               for m in models if study.match_model(m["display_name"], m["model_id"], ranking)}
    assert matched == {m.name for m in ranking.models}


def test_matching_by_name_or_api_id():
    r = study.load_ranking()
    assert study.match_model("My Mistral", "mistral-large-latest", r).name == "Le Chat (Mistral)"
    assert study.match_model("alice ai (yandex)", None, r).name == "Alice (Yandex)"
    assert study.match_model("Alice", "gpt://b1gabc/aliceai-llm", r).name == "Alice (Yandex)"  # last path segment
    assert study.match_model("Mock Echo", "mock-echo", r) is None


def test_ranks_and_spearman():
    assert study._ranks([5, 3, 3, 1]) == [1, 2.5, 2.5, 4]
    assert study.spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1)
    assert study.spearman([1, 2, 3], [30, 20, 10]) == pytest.approx(-1)
    assert study.spearman([1, 2], [1, 2]) is None  # too few
    assert study.spearman([1, 1, 1], [1, 2, 3]) is None  # no variance


def test_compare_pairs_unmatched_and_missing():
    scores = [
        score(1, "Mistral Large", 4.5), score(2, "Claude Sonnet 5.5", 4.6), score(3, "DeepSeek", 2.0),
        score(4, "Mock Echo", 3.0), score(5, "Grok", None, judged=0),
    ]
    c = study.compare(scores, {1: "mistral-large-latest", 2: "claude-sonnet-5-5", 3: "deepseek-flash", 4: "mock-echo"})
    assert [p.study.name for p in c.pairs] == ["Le Chat (Mistral)", "Claude", "DeepSeek"]
    assert [p.app_rank for p in c.pairs] == [2, 1, 3] and [p.study_rank for p in c.pairs] == [1, 2, 3]
    assert c.spearman == pytest.approx(0.5)
    assert c.mean_abs_delta == pytest.approx((0.3 + 0.1 + 0.7) / 3)
    assert c.unmatched == ["Mock Echo"]  # Grok has no score, so it's neither matched nor unmatched
    assert "Grok" in [m.name for m in c.missing]


def test_two_run_models_for_one_study_model_keep_the_better_covered():
    c = study.compare([score(1, "DeepSeek", 2.0, judged=5), score(2, "deepseek-flash", 3.0, judged=50)], {})
    assert len(c.pairs) == 1 and c.pairs[0].score.model_pk == 2 and c.unmatched == ["DeepSeek"]


@pytest.fixture
def mock_ranking(monkeypatch):
    """The study ranking with the mock models standing in for three study models."""
    ranking = study.StudyRanking("test", "1-5", [
        study.StudyModel(1, "Le Chat (Mistral)", "FR", 4.8, None, ("mock echo",)),
        study.StudyModel(2, "Claude", "US", 4.7, None, ("mock random",)),
        study.StudyModel(9, "Baidu (ERNIE)", "CN", 2.5, None, ("mock refuser",)),
        study.StudyModel(8, "GigaChat", "RU", 2.6, None, ("gigachat",)),
    ])  # fmt: skip
    monkeypatch.setattr(study, "load_ranking", lambda: ranking)
    return ranking


def test_results_page_and_cli_show_the_comparison(client, migrated, mock_ranking):
    run_id = full_bank_run(migrated)
    html = client.get(f"/runs/{run_id}/results").text
    assert "Compared with the study" in html and "Spearman" in html
    assert "Not in this run: GigaChat" in html
    for name in ("Le Chat (Mistral)", "Claude", "Baidu (ERNIE)"):
        assert name in html
    # The refuser scores lowest, as ERNIE does in the study.
    r = CliRunner().invoke(cli, ["compare-study", str(run_id)])
    assert r.exit_code == 0, r.output
    assert "Matched models: 3" in r.output and "Spearman's rho" in r.output
    assert "Baidu (ERNIE)" in r.output and "Not in this run: GigaChat" in r.output


def test_no_section_without_matches(client, migrated):
    run_id = full_bank_run(migrated)  # mock models only, real ranking
    assert "Compared with the study" not in client.get(f"/runs/{run_id}/results").text
    r = CliRunner().invoke(cli, ["compare-study", str(run_id)])
    assert r.exit_code == 1 and "No judged model" in r.output
    assert CliRunner().invoke(cli, ["compare-study", "999"]).exit_code == 1
