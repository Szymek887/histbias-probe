# HistBias Probe

A local web app for testing how large language models handle **censorship and historical politics**. It sends a bank of historical and political questions to models you choose, then has a judge model score each answer for censorship and bias. Results come out as scorecards you can compare between models and over time.

This is a product built from the student research project *"Ocenzurowanie i polityka historyczna w wypowiedziach LLM"* (AGH, Q4 2026). In that project, 9 models from 6 countries were tested by hand and scored by two people. HistBias Probe automates the same method so anyone can repeat it on new models, new topics, or newer model versions.

> **Status:** MVP complete (M0–M7, version 0.9.0): topic bank, encrypted API keys, provider adapters, runs in internal and criss-cross mode, judge scoring (rubric v1), results with scorecard, heatmap and CSV/JSON export, Docker image, and a comparison with the study's human ranking. Still to do: the demo run against the real study models, which needs API keys ([docs/demo.md](docs/demo.md)).

## What it does

- **Question bank**: a database of topics with context and sources. Ships with the 50 topics from the study, and users can add, edit, import and export their own.
- **Two test modes**:
  - **Internal**: an open question ("Tell me about X") about events in one country.
  - **Criss-cross**: models from two countries answer the same question, then each comments on the other's answer.
- **Model selection**: choose which models to test and which model judges the answers.
- **API keys page**: store a token per provider. Tokens are stored encrypted, on your machine only.
- **Automatic judging**: a 1–5 score using the study's rubric, censorship type (none / soft / hard), and a short justification.
- **Results**: a scorecard per model, a model × topic heatmap, side-by-side answers, and CSV/JSON export.

## Quick start

```bash
uv sync
uv run histbias init        # create data/, the DB and secret.key; load seed topics and provider presets
uv run histbias serve       # http://127.0.0.1:8000  (--reload for development)
uv run pytest               # tests
```

Or with Docker (data in the `histbias-data` volume):

```bash
docker build -t histbias-probe .
docker run --rm -p 127.0.0.1:8000:8000 -v histbias-data:/data histbias-probe
```

Keep the `127.0.0.1:` prefix: there are no accounts, so anyone who can reach the port can use your API keys.

Then:
1. Try the **Mock** models first; they need no key.
2. Add API keys in **Settings → API keys**.
3. Enable models in **Models**.
4. Start a **New run**, then open its **Results** tab.

The [user guide](docs/user-guide.md) walks through every screen. Other commands: `histbias seed [--overwrite]`, `histbias export-topics [FILE]`, `histbias import-topics FILE [--overwrite]`, `histbias compare-study RUN_ID`.

## Screenshots

| | |
| :---: | :---: |
| ![Results: scorecard and heatmap](docs/img/results.png) | ![Criss-cross answer with the 2×2 grid and judge scores](docs/img/response.png) |
| **Results**: scorecard and topic × model heatmap | **Criss-cross answer**: A1/B1 answer, A2/B2 comment on each other |
| ![New run wizard](docs/img/wizard.png) | ![Topic bank](docs/img/topics.png) |
| **New run** wizard with the call estimate | **Topics**: the question bank |

Screenshots use the mock models, so the scores are random.

## Documentation

| Doc | Contents |
| :--- | :--- |
| [01 – Product](docs/01-product.md) | Goals, users, features, MVP scope, non-goals |
| [02 – Architecture](docs/02-architecture.md) | Stack, components, project layout, run engine |
| [03 – Data model](docs/03-data-model.md) | Database tables and relations |
| [04 – UI](docs/04-ui.md) | Screens, wireframes, user flows |
| [05 – Methodology](docs/05-methodology.md) | Test modes, prompts, scoring rubric, judge prompt |
| [06 – Providers](docs/06-providers.md) | Supported model APIs and adapters |
| [07 – Security](docs/07-security.md) | API token storage and local-only operation |
| [08 – Roadmap](docs/08-roadmap.md) | Milestones with acceptance criteria |
| [User guide](docs/user-guide.md) | How to use the app |
| [Demo](docs/demo.md) | Re-running the study and comparing with its human ranking |
| [Security checklist](docs/security-checklist.md) | Each security rule and the test that verifies it |
| [Decisions](docs/decisions.md) | Decision log and open questions |

## Repository contents

```
seed/topics.yaml               # 50 topics from the study (questions + context, no answers or scores)
seed/providers.yaml            # preset providers/models (no keys)
seed/study_ranking.yaml        # the study's human ranking (for the demo comparison)
app/                           # the application (FastAPI + HTMX)
tests/                         # pytest suite (mock provider + respx; no real API calls)
Dockerfile                     # single-container image, data in /data
scripts/extract_wiki_seed.py   # regenerates seed/topics.yaml from the study wiki
docs/                          # planning documentation
CLAUDE.md                      # guidance for AI-assisted implementation sessions
```
