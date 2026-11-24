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
| D18 | **The first prompt row is the default prompt** in the topic form (no radio button) (M1) | Works with HTMX-added rows without client-side index bookkeeping. Stored as `prompt.is_default`; the default always sorts first. |
| D19 | **Empty default prompt is filled from the template** ("Tell me about {title}" / "Express yourself about {title}"); an empty **English** criss-cross follow-up gets the study's text. Other languages must give their own follow-up (M1) | Implements the "pre-filled" prompt from 04-ui without JS, and never puts English turn-2 text into a non-English conversation. |
| D20 | **New topic slugs follow the seed style**: `{country}-{era}-{title}` for internal, `{country}-vs-{country}-{title}` for criss-cross, using country names; `-2`, `-3`… on collision. Slugs can't be edited (M1) | Slugs are the identity for import/export; editing them would break round-trips and re-imports. |
| D21 | **Delete = hard delete only if no run references the topic; otherwise it is archived** (M1) | 04-ui UX rule. Archived topics are hidden from the list (filter "Show archived") and from the run wizard. |
| D22 | **Import preview keeps the upload in `data/imports/<random>.yaml`** until confirmed (pruned after 24 h) (M1) | Starlette limits non-file form fields to 1 MB, so the 5 MB file can't round-trip through a hidden field. The token is validated as 32 hex characters. |
| D23 | **CSRF: double-submit cookie (`histbias_csrf`, HttpOnly, SameSite=Strict) + `Origin` check**, enforced by one app-wide dependency. Forms send `csrf_token`; HTMX sends `X-CSRF-Token` from `hx-headers` on `<body>`. A missing `Origin` is allowed (non-browser clients), but the token is always required (M1) | 07-security. Added in M1 because it is the first milestone with forms. |
| D24 | **Topic context is rendered through the same sanitizing Markdown renderer as model output** (`app/web/markdown.py`: markdown-it with HTML off + nh3, http/https/mailto links only) (M1) | Imported YAML is untrusted too. Source URLs are restricted to http(s) at validation time because they are rendered as links. |
| D25 | **Anthropic adapter uses raw httpx, not the `anthropic` SDK** (M2) | Same reason as D6: the exact request/response bodies are stored, and tests use `respx`. 06-providers allowed either. |
| D26 | **No temperature to Anthropic by default; no server-side refusal fallbacks** (M2) | Current Claude models accept only default sampling and API version 2026-10-21 rejects `temperature`. A fallback model answering would invalidate a censorship measurement. |
| D27 | **GigaChat adapter implemented in M2** (the roadmap listed only mock/openai/anthropic) (M2) | It is a preset and a study model; it reuses the OpenAI-compatible adapter plus an OAuth step (~80 lines). |
| D28 | **Environment override names**: `HISTBIAS_KEY_<PROVIDER NAME, non-alnum → _>`, extra secret fields as `…__<FIELD>` (e.g. `HISTBIAS_KEY_YANDEX_AI_STUDIO__FOLDER_ID`) (M2) | Deterministic from the provider name; shown on the provider form. |
| D29 | **Secrets never leave memory in clear**: `Secret` hides its value in repr; error messages and stored error bodies are scrubbed of the key and key-like values; provider `extra` rejects secret-looking setting names; base URLs may not contain credentials or a query string (M2) | 07-security "never leak tokens", including keys pasted into the wrong field or echoed back by an API. |
| D30 | **Key hint only for keys ≥ 12 characters** (M2) | Showing the last 4 characters of a short secret reveals too much of it. |
| D31 | **Content blocks are results**: 403 with "unsafe" (Qianfan) → `ProviderRefused`, not an auth error; `REFUSAL_FINISH_REASONS = {content_filter, refusal, blacklist}` (M2) | Found while verifying presets; needed by the M3 refusal heuristic. |
| D32 | **The encryption key is created by `histbias init` and on app start**; if it is invalid the app still starts and the API keys page shows the problem (M2) | 07-security: "doesn't crash". |

## Open questions (decide during implementation)

1. ~~Exact model IDs and endpoints~~ Verified 2026-11-24 (see 06-providers). Still open: Falcon Arabic has no hosted API, so it must run locally; the Yandex `/models` folder header is documented inconsistently; Qianfan has no documented `/models`. Re-check model IDs before the M7 demo.
2. ~~**Default tested temperature:** each provider's default, or a fixed value like 0.7 for comparability? Proposal: provider default, recorded in the run config.~~ Decided (D41).
3. ~~**Should internal topics default to all selected models or only home-country models?** Proposal: all models (foreign models act as a control), with the option in the wizard.~~ Decided (D41).
4. **Seed balance:** the wiki has 7 internal topics for ARAB (3 in "past") but only 5 for PL and RU (1 in "past" each), against the 6-per-country / 2-per-era structure in 05-methodology. Fix in the wiki and re-run `scripts/extract_wiki_seed.py` if those pages exist.
5. **Native-language prompts:** the seed has English only, because the study's native wording wasn't recorded. Who writes the zh/ru/pl/fr/ar translations, and are they needed for the M7 demo?
6. **Grammar of generated seed prompts:** "Tell me about {title}" sometimes reads awkwardly (e.g. "Tell me about Ottoman Empire"). Proofread `seed/topics.yaml` once before release.
7. **Seed criss-cross topics have no context yet** (the wiki pages are still missing it). Add it when the wiki gets it, then re-run `scripts/extract_wiki_seed.py`.
8. **Licensing** of the repository and of the seed context texts (they quote and link third-party sources).
