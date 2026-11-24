# 01 – Product

## Problem

LLMs from different countries answer historical and political questions differently. Some refuse outright (hard censorship). Some blur the answer or present an official narrative as fact (soft censorship). Some answer fully but with a clear ideological bias. Our study showed this, but it was manual: someone typed every prompt into a chat app, copied the answers into a wiki, and two people scored them. That is slow, and it can't be repeated when a new model version appears.

## Goal

A tool that anyone can **run locally** to:

1. maintain a bank of sensitive historical and political topics,
2. send them to a chosen set of models in a repeatable way,
3. score the answers automatically with a chosen judge model, using a transparent rubric,
4. compare models: who censors, on what, and how.

## Users

| User | Need |
| :--- | :--- |
| Students and researchers | Repeat or extend the study, test new models, add their own countries and topics |
| Journalists and analysts | Check quickly how a specific model talks about a specific event |
| Teams choosing an LLM | See whether a candidate model censors topics relevant to their use case |
| Our team (course) | Present the study as a working product, and re-test models in the future |

## Key features

### MVP

| # | Feature | Description |
| :--- | :--- | :--- |
| F1 | Topic bank | Topics stored in a DB with title, type (internal / criss-cross), country or countries, era (past / 20th century / present), context, sources, and prompts per language. CRUD in the UI. |
| F2 | Seed data | First start imports `seed/topics.yaml`: the 50 study topics, with questions and context only. |
| F3 | Import / export | Topics can be exported and imported as YAML, so people can share topic sets. |
| F4 | API keys | Settings page with one credential per provider. Stored encrypted and masked in the UI. "Test connection" button. |
| F5 | Model registry | Models are defined as provider + API model ID + display name + country. Presets ship for the study's models. Users can add any OpenAI-compatible model. |
| F6 | Run wizard | Pick topics → pick tested models → pick judge model → settings → start. Shows the number of API calls before starting. |
| F7 | Internal mode | One open prompt per (topic, model, language). |
| F8 | Criss-cross mode | Two-turn exchange between paired models (see [05 – Methodology](05-methodology.md)). |
| F9 | Judge | Scores each answer with the rubric and stores the score, censorship type, and rationale. Warns if the judge is also a tested model. |
| F10 | Results | Run progress, a scorecard per model (average, by era and by type), a model × topic heatmap, and response detail with side-by-side answers. |
| F11 | Export | Run results as CSV and JSON. |
| F12 | Mock provider | A fake model that needs no API key, for demos and tests. |

### After MVP (see roadmap)

- Second judge model, with judge-agreement statistics.
- Manual human rating in the UI, compared with the judge's score.
- Comparing two runs, e.g. the same model in different months.
- Automatic prompt translation into the model's native language.
- Cost and token tracking per run.
- Markdown or HTML report export, e.g. for a wiki.

## Non-goals

- **No multi-user hosting, accounts, or login.** The app runs locally for one user (see [decisions](decisions.md)).
- **No scraping of consumer chat apps** (web versions of DeepSeek, Alice, Baidu, etc.). The tool uses official APIs only. Results may therefore differ from the study, which used chat apps. That is a documented limitation.
- No fine-tuning and no hosting of models. Local models are reached through Ollama or vLLM endpoints.
- The tool doesn't claim to give the "truth". Scores are judge-model opinions under a published rubric.

## Success criteria

- A new user can install the app, add one API key, and finish a run on 5 topics × 2 models in under 15 minutes.
- Adding a new topic with a native-language prompt takes under 2 minutes in the UI.
- Every score can be traced to the exact prompt, raw answer, judge prompt, and judge output.
- A run on the full seed bank with the study's models is reproducible from an exported run config.
