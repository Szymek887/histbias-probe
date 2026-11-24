# 02 – Architecture

## Stack

| Layer | Choice | Why |
| :--- | :--- | :--- |
| Language | Python 3.12+ | Good LLM SDK support, one language for the whole app |
| Package manager | `uv` | Fast, lockfile, `uv run` |
| Web framework | FastAPI | Async (many parallel LLM calls), simple routing |
| Templates | Jinja2 + **HTMX** | Server-rendered UI with no JS build step. HTMX handles forms, partial reloads, and run-progress polling. |
| CSS | Pico.css (vendored file, classless) | Clean look without writing CSS |
| Charts | Chart.js (vendored) or plain HTML table heatmap | Scorecard and heatmap |
| ORM | SQLModel (SQLAlchemy 2) | Typed models shared with Pydantic validation |
| DB | SQLite (`data/histbias.db`) | Local, single file, zero setup |
| Migrations | Alembic | The schema will evolve |
| HTTP client | `httpx` (async) | Used by all provider adapters |
| Crypto | `cryptography` (Fernet) | API token encryption at rest |
| CLI | `typer` | `histbias init / serve / seed / export-topics / import-topics / compare-study` |
| Tests | `pytest`, `pytest-asyncio`, `respx` (HTTP mocking) | |

We decided **not** to use LiteLLM or LangChain. All target providers are reached through 3 small adapters: OpenAI-compatible, Anthropic, and GigaChat. Owning that code keeps dependencies small and lets us record the raw requests and responses exactly, which research needs. See [decisions](decisions.md).

## Component overview

```
┌───────────────────────── Browser (HTMX) ─────────────────────────┐
│ Topics │ Models │ API keys │ New run │ Run progress │ Results    │
└──────────────────────────────┬───────────────────────────────────┘
                               │ HTML / partials
┌──────────────────────────────▼───────────────────────────────────┐
│ FastAPI app (127.0.0.1 only)                                     │
│  web/        routes + Jinja templates                            │
│  services/   topics · models · credentials · runs · results      │
│  engine/     planner → executor (asyncio) → judge → scorer       │
│  providers/  base · openai_compat · anthropic · gigachat · mock  │
│  crypto      Fernet encrypt/decrypt of tokens                    │
└───────┬─────────────────────────────┬────────────────────────────┘
        │ SQLModel                     │ httpx (async)
┌───────▼────────┐          ┌──────────▼─────────────────────────────┐
│ SQLite         │          │ LLM APIs: Anthropic, DeepSeek, Mistral,│
│ data/*.db      │          │ xAI, Yandex, GigaChat, Qianfan, HF,    │
│ data/secret.key│          │ Ollama / vLLM (local) ...              │
└────────────────┘          └────────────────────────────────────────┘
```

## Proposed project layout

```
histbias-probe/
├── pyproject.toml
├── alembic.ini
├── migrations/
├── app/
│   ├── main.py              # FastAPI app factory, startup (DB, worker)
│   ├── cli.py               # typer: init, serve, seed, export-topics, import-topics, compare-study
│   ├── config.py            # settings (pydantic-settings, env prefix HISTBIAS_)
│   ├── db.py                # engine, session
│   ├── crypto.py            # key loading/creation, encrypt/decrypt, mask
│   ├── models/              # SQLModel tables (see 03-data-model.md)
│   ├── schemas/             # Pydantic I/O schemas (judge output, YAML import)
│   ├── providers/
│   │   ├── base.py          # ChatProvider protocol, ChatRequest/ChatResult
│   │   ├── openai_compat.py
│   │   ├── anthropic.py
│   │   ├── gigachat.py      # OAuth token exchange + OpenAI-like chat
│   │   ├── mock.py
│   │   └── registry.py      # kind -> adapter class
│   ├── engine/
│   │   ├── planner.py       # run config -> run_items
│   │   ├── executor.py      # async worker, concurrency, retries
│   │   ├── crisscross.py    # 2-turn conversation logic
│   │   ├── refusal.py       # heuristic refusal detection
│   │   └── judge.py         # judge prompt building, JSON parsing, validation
│   ├── services/            # business logic used by routes and CLI
│   └── web/
│       ├── routes/          # topics.py, models.py, settings.py, runs.py, results.py
│       ├── templates/
│       └── static/          # pico.min.css, htmx.min.js, chart.min.js, app.css
├── seed/
│   ├── topics.yaml
│   └── providers.yaml
├── scripts/extract_wiki_seed.py
├── tests/
└── data/                    # gitignored: histbias.db, secret.key
```

## Provider adapter interface

```python
class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class ChatRequest(BaseModel):
    model_id: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    response_format_json: bool = False      # used by the judge

class ChatResult(BaseModel):
    text: str
    finish_reason: str | None               # "stop", "length", "content_filter", ...
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    raw_request: dict                       # stored for traceability (no secrets!)
    raw_response: dict

class ChatProvider(Protocol):
    def __init__(self, base_url: str, secret: Secret, extra: dict): ...
    async def chat(self, req: ChatRequest) -> ChatResult: ...
    async def test_connection(self) -> tuple[bool, str]: ...
    async def list_models(self) -> list[str] | None: ...   # optional
```

Error classes are `ProviderAuthError`, `ProviderRateLimit` (retryable), `ProviderTransient` (retryable, for 5xx and timeouts), `ProviderRefused` (the API blocked the content, which is a **result**, not an error), and `ProviderError`.

## Run engine

1. **Planner** expands a run config into `run_item` rows:
   - **Internal topic:** one item per tested model × selected language × repetition.
   - **Criss-cross topic:** one item per model pair × language × repetition. A pair is (model from country A, model from country B) among the selected models, or pairs chosen by hand. See [05 – Methodology](05-methodology.md).
2. **Executor** is one asyncio background worker started with the app.
   - It takes `pending` items, with a global concurrency limit (default 4) and a per-provider limit (default 2).
   - Retries use exponential backoff: 3 attempts for rate limits and transient errors.
   - State lives in the DB, so after a restart, items left in `running` go back to `pending` and the run continues.
   - **Pause** and **Cancel** set flags that the worker checks before each call.
3. **Criss-cross item:**
   - Turn 1: both models answer in parallel.
   - Turn 2: each model gets the follow-up with the partner's turn-1 answer.
   - That makes 4 responses per item.
4. **Refusal heuristic:** runs on every response (empty text, `content_filter` finish reason, known refusal phrases) and sets `refusal_detected`. The judge still runs, and the heuristic is stored next to the judge's verdict for comparison.
5. **Judge:** runs once all responses of an item exist.
   - Each response is judged separately. For turn 2, the judge also sees the partner's answer that was being critiqued.
   - Judge output must match a JSON schema. Invalid output is retried once with a "fix your JSON" message, then marked `judge_failed`.
6. **Scorer:** the results pages compute aggregates from one SQL query per page (rows aggregated in Python, see D57), so no aggregates are stored. Aggregates are average overall score, censorship counts, and splits by era, type, and topic country.

The UI polls `GET /runs/{id}/progress` (an HTMX partial) every 2 s while the run is active.

## Configuration

Settings come from environment variables or `.env`, with the prefix `HISTBIAS_`:

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `HISTBIAS_DATA_DIR` | `./data` | DB and secret key location |
| `HISTBIAS_HOST` | `127.0.0.1` | Bind address. Keep it local (see [07 – Security](07-security.md)). |
| `HISTBIAS_PORT` | `8000` | |
| `HISTBIAS_SECRET_KEY` | (unset) | Fernet key. If unset, it is generated into `data/secret.key`. |
| `HISTBIAS_MAX_CONCURRENCY` | `4` | Global parallel API calls |
| `HISTBIAS_REQUEST_TIMEOUT` | `120` | Seconds per API call |
| `HISTBIAS_PROVIDER_CONCURRENCY` | `2` | Parallel calls per provider. A provider can override it with `max_concurrency` in its advanced settings. |
| `HISTBIAS_RETRY_BASE_DELAY` | `2` | Seconds before the first retry of a 429/5xx/timeout; doubles each attempt, at least `Retry-After` |
| `HISTBIAS_RUN_WORKER` | `true` | Start the background run worker with the web app (tests turn it off) |
| `HISTBIAS_IN_CONTAINER` | `false` | Set by the Docker image. Only changes the startup warning for a non-local bind. |
| `HISTBIAS_DEBUG` | `false` | Starlette debug page with tracebacks on errors. Development only. |

## Packaging

- `uv run histbias serve` for development and normal use.
- `Dockerfile` (M7): `python:3.12-slim` + uv, runs as a non-root user, data in the `/data` volume. The container listens on `0.0.0.0` (it must, to be reachable through Docker's port mapping) with `HISTBIAS_IN_CONTAINER=true`; the port must be published on the host's loopback only (`-p 127.0.0.1:8000:8000`). It starts with `histbias serve --init`, which creates and seeds the DB on the first start and only migrates on later starts.
