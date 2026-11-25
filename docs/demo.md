# Demo: re-running the study (M7)

Goal (from [08 – Roadmap](08-roadmap.md)): run the study's models on the seed bank and compare the results with the study's human ranking.

## The study's ranking

From the study wiki ("Ranking modeli"), stored in [`seed/study_ranking.yaml`](../seed/study_ranking.yaml):

| Place | Model | Country | Human mean (1–5) |
| :---: | :--- | :---: | :---: |
| 1 | Le Chat (Mistral) | FR | 4.8 |
| 2 | Claude | US | 4.7 |
| 3 | Grok | US | 3.7 |
| 4 | Bielik | PL | 3.5 |
| 5 | Alice (Yandex) | RU | 3.2 |
| 6 | Falcon | ARAB | 3.0 (criss-cross only) |
| 7 | DeepSeek | CN | 2.7 |
| 8 | GigaChat | RU | 2.6 |
| 9 | Baidu (ERNIE) | CN | 2.5 |

## Procedure

1. **Keys.** In **Settings → API keys**, add keys for Mistral, Anthropic, xAI, Yandex (plus folder ID), DeepSeek, GigaChat (plus the CA bundle, see [06 – Providers](06-providers.md)) and Baidu Qianfan. Bielik runs locally with Ollama: `ollama pull SpeakLeash/bielik-11b-v3.1-instruct:Q4_K_M`. Falcon Arabic has no hosted API; run it locally (vLLM or Ollama) or leave it out.
2. **Models.** In **Models**, enable the presets and check each one with **Send test prompt** on its page.
3. **Judge.** Choose a judge that is *not* one of the tested models, ideally from a country with no topic in the bank. The wizard warns when the judge is tested too, or comes from a tested provider or a topic country. Write down which judge you used; it affects the scores (bias toward its own answers and its own country's view).
4. **Run.** **New run** → all topics (internal + criss-cross) → the study models → English → internal models: *all* → criss-cross pairing: *automatic by country* → the judge → repetitions 1 (3 for a more stable comparison) → **Start run**. The wizard shows the number of calls before you start; the full bank with 9 models is roughly 450 model calls and 450 judge calls per repetition.
5. **Compare.** Open the run's **Results** tab. The section "Compared with the study's ranking" lists each matched model's study place and mean next to the run's mean, the difference, and Spearman's rank correlation. The same table is printed by:

   ```bash
   uv run histbias compare-study <RUN_ID>
   ```

6. **Record** the result below (judge, date, ρ, notable differences) and export the run as JSON for the archive.

## What to expect, and why results will differ

- **Apps vs APIs.** The study used the consumer chat apps (Le Chat, Wenxin, the DeepSeek app, Alice). Apps add their own moderation layers, so API answers are often *less* censored. Expect higher API scores for DeepSeek and ERNIE in particular.
- **Model versions.** The presets point at the current API models (e.g. `mistral-large-latest`, `deepseek-flash`), not the exact Q4 2026 app versions.
- **Raters.** The study used two human raters; the app uses one LLM judge with rubric v1. Compare *rank order* (Spearman's ρ) more than absolute means.
- **Falcon** was rated on criss-cross questions only in the study. Filter the results by mode = criss-cross for a fair comparison of that row.
- **Arabic internal topics** were not rated in the study (answers were too weak); the run includes them.

## Results

### Dry run with mock models (2026-11-25)

The pipeline was checked end to end with the mock provider (no keys): the full seed bank, 3 mock models, 2 manual criss-cross pairs and Mock Judge gave 225 answers, all judged; the results page renders in under 50 ms. The comparison itself is tested with mock models standing in for study models (`tests/test_study.py`). Mock scores are random, so this dry run says nothing about real models.

### Real models

*Not run yet: it needs the API keys above.* Fill in after the first real run:

| Date | Judge | Models matched | Spearman ρ | Mean \|diff\| | Notes |
| :--- | :--- | :---: | :---: | :---: | :--- |
| | | | | | |
