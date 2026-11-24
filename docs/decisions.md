# Decisions

## Decided (2026-11-24)

| # | Decision | Reason |
| :--- | :--- | :--- |
| D1 | **Python + FastAPI + SQLite + Jinja/HTMX** | One language. Async suits many parallel LLM calls. No JS build step, and a single-file DB. |
| D2 | **Local, single-user app; no login** | It's a research tool. Avoids auth and multi-tenant isolation. Tokens stay on the user's machine. |
| D3 | **API tokens encrypted at rest (Fernet), with an environment override** | A plain-text DB file is too easy to leak by sharing `data/`. |
| D4 | **Seed = questions + context only** (no answers, no human scores) | Chosen by the team. Human scores can be imported later as a validation set (backlog). |
| D5 | **UI and docs in English** | Wider audience. Matches the wiki topic pages. |
| D6 | **Own thin provider adapters, no LiteLLM/LangChain** | 3 adapters cover all target APIs. This gives exact raw request/response recording and fewer dependencies. Revisit if many more native APIs are needed. |
| D7 | **Keep the study's 1–5 overall scale**, plus censorship type, balance, and depth | Comparable with the study's ranking. The extra fields capture the study's findings (soft vs. hard censorship; shallow ≠ neutral). |
| D8 | **One response = one judgement** (per judge); turn 2 is judged with the partner's answer as context | Simple, traceable, and allows multiple judges later. |
| D9 | **Run snapshots** (prompt text, model ID, and config copied into the run) | Editing topics or models must not change past results. |
| D10 | **The judge doesn't see the evaluated model's name** | Reduces brand bias. |
| D11 | Seed country code **`ARAB`** for "Arab states" (Saudi Arabia topics, Falcon from the UAE) | Matches the study's grouping. Users can add finer codes. |
| D12 | **Enum-like columns are plain TEXT**; the value sets live as `StrEnum`s in `app/models/base.py` and are validated in services (M0) | SQLAlchemy `Enum` stores member *names*, not values, and makes adding a value a migration. Stored values match the docs exactly (`criss_cross`, `20th_century`). |
| D13 | **Model snapshot lives on `response`** (`model_id_snapshot`, `display_name_snapshot`) rather than on `run_item` (M0) | A criss-cross item has two models; one answering model per response is the natural place for the snapshot rule. |
| D14 | **Datetimes are timezone-aware UTC** (sqlmodel `UTCDateTime`); naive datetimes are rejected on write (M0) | Avoids mixed naive/aware comparisons in the run engine. |
| D15 | **Log redaction is done in the formatter** (`app/log.py`), not a `logging.Filter`; uvicorn runs with `log_config=None` so its logs go through it (M0) | The formatter sees the final text including formatted args and tracebacks, which a filter would miss. |
| D16 | **Pico.css classless build** (2.0.6) and htmx 2.0.4 vendored in `app/web/static/` (versions in `VENDORED.md`) (M0) | Matches "classless" in 02-architecture; no CDN, so the app works offline. |
| D17 | **Migrations at repo root** (`migrations/`), located from the package path; `histbias init` runs `alembic upgrade head` programmatically (M0) | Matches the planned layout. Requires the project to be installed in editable mode (the `uv sync` default). |

## Open questions (decide during implementation)

1. ~~Exact model IDs and endpoints~~ Verified 2026-11-24 (see 06-providers). Still open: Falcon Arabic has no hosted API, so it must run locally; the Yandex `/models` folder header is documented inconsistently; Qianfan has no documented `/models`. Re-check model IDs before the M7 demo.
2. ~~**Default tested temperature:** each provider's default, or a fixed value like 0.7 for comparability? Proposal: provider default, recorded in the run config.~~ Decided (D41).
3. ~~**Should internal topics default to all selected models or only home-country models?** Proposal: all models (foreign models act as a control), with the option in the wizard.~~ Decided (D41).
4. **Seed balance:** the wiki has 7 internal topics for ARAB (3 in "past") but only 5 for PL and RU (1 in "past" each), against the 6-per-country / 2-per-era structure in 05-methodology. Fix in the wiki and re-run `scripts/extract_wiki_seed.py` if those pages exist.
5. **Native-language prompts:** the seed has English only, because the study's native wording wasn't recorded. Who writes the zh/ru/pl/fr/ar translations, and are they needed for the M7 demo?
6. **Grammar of generated seed prompts:** "Tell me about {title}" sometimes reads awkwardly (e.g. "Tell me about Ottoman Empire"). Proofread `seed/topics.yaml` once before release.
7. **Seed criss-cross topics have no context yet** (the wiki pages are still missing it). Add it when the wiki gets it, then re-run `scripts/extract_wiki_seed.py`.
8. **Licensing** of the repository and of the seed context texts (they quote and link third-party sources).
