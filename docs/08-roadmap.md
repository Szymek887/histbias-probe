# 08 – Roadmap

Each milestone produces a working app and ends with its acceptance criteria met and tests passing. Milestones are ordered so a **demo with the Mock provider is possible from M4**.

## M0 – Skeleton
- `pyproject.toml` (uv), FastAPI app, `histbias serve`, base layout template (Pico + HTMX), and nav bar.
- SQLModel tables from [03 – Data model](03-data-model.md), the first Alembic migration, and `histbias init`.
- Config (`HISTBIAS_*`), `data/` creation, logging with secret redaction.
- pytest setup and CI-ready `uv run pytest`.

**Done when:** `uv run histbias init && uv run histbias serve` shows empty pages for every nav item, and the tests pass.

## M1 – Topic bank
- Import `seed/topics.yaml` during `init` (idempotent, by slug).
- Topics list with search and filters (country, era, type), a create/edit form with sources and multi-language prompts, topic detail, and archive.
- Country management (add a country with its native language).
- YAML import (preview: new / changed / unchanged; skip or overwrite) and export.

**Done when:**
- 50 seed topics show up.
- A user can add a criss-cross topic with 2 countries and a Polish prompt.
- Export followed by import into a fresh DB gives identical data (round-trip test).

## M2 – Providers, keys, and models
- Crypto module (key generation, encrypt/decrypt, mask), the API keys page (add, replace, delete, test), and the environment override.
- Adapters: `mock`, `openai_compatible`, and `anthropic`, with `respx` tests.
- Model registry: load `seed/providers.yaml` presets; add or edit a model; add a custom OpenAI-compatible provider; "Send test prompt".
- **Verify the provider presets against current docs** and fix the URLs and model IDs in `seed/providers.yaml`.

**Done when:**
- A real key for at least one provider tests OK.
- The test prompt returns an answer.
- The key never appears in HTML, logs, or the DB in plain text (tests).

## M3 – Run engine (internal mode)
- Run wizard steps 1, 2, and 4 (topics, models, settings) with the call-count summary. Judge selection is added in M5.
- Planner → `run_item`s. Asyncio worker with global and per-provider concurrency, retries, pause, cancel, and resume after restart.
- Refusal heuristic.
- Run detail page with HTMX progress and "Retry failed".

**Done when:**
- A run of 6 topics × 2 mock models plus 1 real model completes.
- Killing the server mid-run and restarting it continues the run.
- 429 errors are retried.

## M4 – Criss-cross mode
- Automatic pairing by country, and manual pairing, with the preview in the wizard.
- Two-turn conversation logic. Response detail shows the 2×2 grid.

**Done when:**
- The Taiwan topic with DeepSeek × Grok (or mock models) produces A1, B1, A2, and B2.
- Turn 2 contains the partner's answer exactly as received.

## M5 – Judge
- Wizard step 3: judge select, a self-judge warning, and a "no judge" option.
- Judge prompt v1 ([05 – Methodology](05-methodology.md)), JSON validation, repair retry, `judge_failed`.
- Judge verdict on response detail, with the collapsible prompt and raw output.

**Done when:**
- All responses in a run are judged.
- Invalid judge JSON is repaired or marked as failed.
- A unit test covers each rubric field's validation.

## M6 – Results and export
- Scorecard (mean overall, by era, by mode, hard/soft counts, refusal rate, home vs. foreign), a model × topic heatmap, filters, and side-by-side comparison.
- CSV and JSON export.
- "Duplicate run".

**Done when:** a run on the full seed bank with mock models renders the scorecard and heatmap in under 1 s, and the CSV opens correctly in a spreadsheet with UTF-8, including Arabic and Chinese.

## M7 – Polish and release
- Dockerfile with the `data/` volume.
- A README quick start with screenshots, and a short user guide.
- Empty states, error messages (e.g. the GigaChat CA hint), and confirmation dialogs.
- Security checklist from [07 – Security](07-security.md) verified: CSRF, sanitizing, redaction.
- **Demo:** re-run the study's models on the seed bank and compare with the study's human ranking.

## After MVP (backlog, by priority)
1. **Second judge and agreement stats:** exact match, ±1, Cohen's κ, and a disagreement list.
2. **Human ratings** in the UI, with judge-vs-human agreement. This would let the study's 2-person scores be imported later as a validation set.
3. **Run comparison:** the same model over time, with score deltas per topic.
4. **Auto-translation** of prompts into native languages, using a chosen model and marked as machine-translated.
5. **Cost tracking:** tokens × price table per model.
6. **Markdown/HTML report export**, e.g. for the GitLab wiki.
7. Repetitions statistics view (variance per item).

## Rough effort

| Milestone | Estimate (1 developer + AI assistant) |
| :--- | :--- |
| M0–M1 | 2–3 days |
| M2 | 2 days |
| M3–M4 | 3–4 days |
| M5 | 1–2 days |
| M6 | 2 days |
| M7 | 1–2 days |
