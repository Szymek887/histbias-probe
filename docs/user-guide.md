# User guide

HistBias Probe asks language models historical and political questions and scores their answers for censorship and bias with a judge model. Everything runs on your computer; only the model calls go out to the providers.

## 1. Install and start

With [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run histbias init     # once: creates data/, the database and the encryption key, loads the 50 seed topics and provider presets
uv run histbias serve    # http://127.0.0.1:8000
```

Or with Docker:

```bash
docker build -t histbias-probe .
docker run --rm -p 127.0.0.1:8000:8000 -v histbias-data:/data histbias-probe
```

Keep the `127.0.0.1:` part. There are no user accounts, so anyone who can reach the port can use your API keys.

**Back up `data/`** (or the Docker volume). It holds the database and `secret.key`. Without the key, stored API keys can't be decrypted and must be entered again; runs and results are not affected.

## 2. Try it without keys

The **Mock** models need no key: *Mock Echo* repeats the question, *Mock Random* gives varied answers, *Mock Refuser* always refuses, and *Mock Judge* gives random but valid verdicts. Use them to learn the screens; their scores mean nothing.

## 3. Add API keys

**Settings → API keys**: paste a key per provider and press **Save**. Keys are encrypted on disk and shown only as `••••` plus the last 4 characters. **Test** checks the key with a short request.

- A key in an environment variable (e.g. `HISTBIAS_KEY_ANTHROPIC`) is used instead of the stored one and is labelled "from environment".
- **Yandex** also needs the folder ID; **GigaChat** needs its authorization key and the Russian Trusted Root CA as a PEM file. Put the file's path in the provider's advanced settings (`ca_bundle_path`). The provider page explains where to get it.
- **Bielik** runs locally with Ollama and needs no key.

## 4. Choose models

**Models** lists the presets (the study's models and a few more). Enable the ones you want; open a model and use **Send test prompt** to check it answers. Model IDs change often; edit them here when a provider renames a model. Set each model's **country**: it drives criss-cross pairing and the home/foreign scores.

## 5. Topics

**Topics** holds the question bank: 35 internal topics (one country, three eras) and 15 criss-cross topics (two countries) from the study. Each topic has a context paragraph and sources; the judge uses them as reference.

- Add or edit topics in the UI, or **Import** a YAML file (a preview shows what will change). **Export** writes topics back to YAML.
- Archiving hides a topic without touching past runs. Runs keep a frozen copy of everything they used.

## 6. Start a run

**New run** (or select topics on the Topics page and press **Start run with selected**):

1. **Topics**: internal, criss-cross, or both.
2. **Models** to test.
3. **Languages**: English, and/or the topic country's language where the topic has a prompt in it.
4. **Internal topics**: ask all models, or only the topic country's models.
5. **Criss-cross pairing**: automatic (every model from country A × every model from country B) or manual pairs.
6. **Judge**: a model that scores each answer, or none (answers only). The wizard warns when the judge is also tested, or comes from a tested provider or a topic's country.
7. **Repetitions**, temperature and concurrency.

The summary shows the number of model and judge calls before you start. When a run makes 20 or more calls to paid APIs, **Start run** asks you to confirm. **Save as draft** keeps the settings without calling anything.

The run is processed in the background. You can close the browser; if the server stops, the run resumes where it left off after `histbias serve`. **Pause**, **Resume** and **Cancel** are on the run page, and **Retry failed** re-queues items that hit errors.

## 7. Read the results

The run page has three tabs:

- **Items**: every question with each model's answer, the refusal heuristic and the judge's score. Click an answer to see the full conversation, the judge's verdict and rationale, the exact prompts and the raw API bodies. For criss-cross topics the answer page shows the 2×2 grid: A1 and B1 answer the question, then A2 and B2 comment on each other's answer. **Compare** shows the other models' answers to the same question side by side.
- **Results**:
  - The **scorecard** has one row per model: mean score (1 = refused, 5 = objective), hard and soft censorship counts, refusal rate (heuristic and judge), means by era and mode, and **home vs foreign**: the model's score on its own country's topics against other countries' topics, which is the study's key signal.
  - The **heatmap** shows topics × models, from 1 (red) to 5 (green). Filter by country, era and mode.
  - When the run includes the study's models, a section **compares the run with the study's human ranking** (see [demo.md](demo.md)).
- **Config**: the frozen settings, the prompts used, and **Duplicate run** to repeat it later, for example after a model update.

**Export CSV** (one row per judgement; opens in Excel with Arabic and Chinese intact) and **Export JSON** (the full run, including raw bodies and judge prompts) always contain the whole run.

## 8. Things to keep in mind

- Answers come from the **APIs**, not the consumer apps, which may add their own moderation layer.
- The **judge is a model too**: it can favour its own answers or its own country's view. Use a judge from outside the tested set, and read the rationales.
- Answers vary between calls. Use **repetitions** for comparisons you want to publish.
- The refusal heuristic only looks at the start of an answer; a disagreement with the judge is worth reading.

## Command line

| Command | What it does |
| :--- | :--- |
| `histbias init` | Create the data folder, database and key; load seed topics and presets. Safe to re-run. |
| `histbias serve [--host] [--port] [--reload] [--init]` | Start the web app; applies new database migrations. `--init` runs `init` first if there is no database. |
| `histbias seed [--overwrite]` | Reload the seed topics and presets. |
| `histbias export-topics [FILE]` / `import-topics FILE [--overwrite]` | Topic bank as YAML. |
| `histbias compare-study RUN_ID` | Print a run's comparison with the study's human ranking. |

Settings are environment variables with the prefix `HISTBIAS_` (or a `.env` file); see [02 – Architecture](02-architecture.md#configuration).
