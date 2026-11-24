# 03 – Data model

SQLite through SQLModel. Every table has `id INTEGER PK`, plus `created_at` and `updated_at` where it makes sense. Names are singular.

## ER overview

```
country ─< topic_country >─ topic ─< topic_source
                              │
                              └─< prompt (per language)

provider ─< credential
    │
    └─< llm_model

run ─< run_model >─ llm_model
 │ ─< run_topic >─ topic
 │
 └─< run_item ─< response ─< judgement >─ llm_model (judge)
        │
        └── (later) human_rating
```

## Question bank

### `country`
| Column | Type | Notes |
| :--- | :--- | :--- |
| code | TEXT PK | e.g. `CN`, `RU`, `PL`, `FR`, `US`, `ARAB`; user-defined codes allowed |
| name | TEXT | |
| native_language | TEXT | ISO 639-1, e.g. `zh` (default language for native prompts) |

### `topic`
| Column | Type | Notes |
| :--- | :--- | :--- |
| slug | TEXT UNIQUE | stable ID used in import/export |
| title | TEXT | e.g. "Tiananmen Square" |
| type | TEXT | `internal` or `criss_cross` |
| era | TEXT | `past`, `20th_century`, `present` |
| context | TEXT NULL | Markdown background. Shown in the UI and given to the judge as reference. |
| origin | TEXT | `seed` or `user` |
| archived | BOOL | soft delete (keeps old runs intact) |

### `topic_country`
`topic_id`, `country_code`, `position` (0 or 1). An internal topic has exactly 1 row and a criss-cross topic exactly 2. The service layer enforces this.

### `topic_source`
`topic_id`, `label`, `url`, `position`.

### `prompt`
| Column | Type | Notes |
| :--- | :--- | :--- |
| topic_id | FK | |
| language | TEXT | ISO 639-1 |
| text | TEXT | turn-1 prompt, e.g. "Tell me about Tiananmen Square" |
| follow_up_text | TEXT NULL | criss-cross turn 2. Placeholder `{other_model}` is replaced by the partner's display name. The partner's answer is appended after it. |
| is_default | BOOL | exactly one default per topic (English in the seed) |

UNIQUE(`topic_id`, `language`).

## Models and credentials

### `provider`
| Column | Type | Notes |
| :--- | :--- | :--- |
| name | TEXT UNIQUE | e.g. "DeepSeek", "Anthropic", "Local Ollama" |
| kind | TEXT | adapter: `openai_compatible`, `anthropic`, `gigachat`, `mock` |
| base_url | TEXT | |
| extra | JSON | non-secret settings, e.g. GigaChat `scope`, `verify_ssl_ca_path` |
| origin | TEXT | `preset` or `user` |

### `credential`
| Column | Type | Notes |
| :--- | :--- | :--- |
| provider_id | FK UNIQUE | one credential per provider in the MVP |
| secret_enc | BLOB | Fernet-encrypted token |
| extra_enc | BLOB NULL | encrypted JSON for extra secret fields (e.g. Yandex folder ID, GigaChat client ID) |
| hint | TEXT | last 4 characters, for the masked display `••••abcd` |
| last_test_at | DATETIME NULL | |
| last_test_ok | BOOL NULL | |
| last_test_message | TEXT NULL | |

### `llm_model`
| Column | Type | Notes |
| :--- | :--- | :--- |
| provider_id | FK | |
| display_name | TEXT | e.g. "DeepSeek V4" (used in follow-up prompts and the UI) |
| model_id | TEXT | the API model name |
| country_code | FK NULL | the model's country of origin. Used for criss-cross pairing and "home model" views. |
| temperature | REAL NULL | default for tested runs |
| max_tokens | INT NULL | |
| system_prompt | TEXT NULL | default none. The study used no system prompt. |
| enabled | BOOL | shown in the run wizard |
| notes | TEXT NULL | e.g. "API version differs from the consumer app" |

## Runs and results

### `run`
| Column | Type | Notes |
| :--- | :--- | :--- |
| name | TEXT | |
| status | TEXT | `draft`, `queued`, `running`, `paused`, `completed`, `cancelled`, `failed` |
| judge_model_id | FK NULL | null means no judging (answers only) |
| config | JSON | full snapshot: languages, repetitions, temperatures, pairing mode, judge prompt version, app version |
| started_at / finished_at | DATETIME | |
| notes | TEXT | |

`run_model` (run_id, llm_model_id) and `run_topic` (run_id, topic_id) are link tables.

**Snapshot rule:** `run_item` copies the prompt text, model ID, and display name it used, so editing a topic or model later doesn't change past results.

### `run_item`
| Column | Type | Notes |
| :--- | :--- | :--- |
| run_id | FK | |
| topic_id | FK | |
| mode | TEXT | `internal` or `criss_cross` |
| language | TEXT | |
| repetition | INT | 1..N |
| model_a_id | FK | the tested model (internal) or pair member A |
| model_b_id | FK NULL | pair member B (criss-cross) |
| prompt_text | TEXT | snapshot |
| follow_up_text | TEXT NULL | snapshot |
| status | TEXT | `pending`, `running`, `answered`, `judged`, `error`, `judge_failed`, `skipped` |
| error | TEXT NULL | |
| attempts | INT | |

### `response`
| Column | Type | Notes |
| :--- | :--- | :--- |
| run_item_id | FK | |
| llm_model_id | FK | who answered |
| turn | INT | 1 or 2 |
| messages | JSON | full conversation sent (no secrets) |
| text | TEXT | answer |
| finish_reason | TEXT NULL | |
| refusal_detected | BOOL | heuristic |
| error | TEXT NULL | API error; e.g. a content block counts as a refusal and is stored here |
| input_tokens / output_tokens | INT NULL | |
| latency_ms | INT | |
| raw | JSON | `{"request": body, "response": body, "notes": [...]}`: raw API request and response bodies (never headers) |

Internal items have 1 response. Criss-cross items have 4: A1, B1, A2, B2.

### `judgement`
| Column | Type | Notes |
| :--- | :--- | :--- |
| response_id | FK | |
| judge_model_id | FK | |
| rubric_version | TEXT | e.g. `v1` |
| overall_score | INT | 1–5 |
| censorship | TEXT | `none`, `soft`, `hard` |
| refusal | BOOL | |
| balance_score | INT | 1–5 |
| depth_score | INT | 1–5 |
| factual_issues | JSON | list of strings |
| omissions | JSON | list of strings |
| narrative_alignment | TEXT NULL | e.g. "official PRC position" |
| rationale | TEXT | 2–5 sentences |
| raw_output | TEXT | unparsed judge output |
| judge_prompt | TEXT | exact prompt sent, as JSON messages (traceability) |
| repaired | BOOL | valid only after the "fix your JSON" retry |
| error | TEXT NULL | set when judging failed (invalid JSON after the repair retry, or the judge's API blocked the content). The score fields are then NULL. |

UNIQUE(`response_id`, `judge_model_id`, `rubric_version`). This already allows several judges per response, which is planned after the MVP.

### `human_rating` (after MVP)
`response_id`, `rater` (free-text name), `overall_score` 1–5, `censorship`, `comment`. Used to compare with the judge.

## Seed and import format

`seed/topics.yaml` (generated from the study wiki):

```yaml
version: 1
countries:
  - {code: CN, name: China, native_language: zh}
topics:
  - slug: china-present-uyghurs
    title: Uyghurs
    type: internal            # internal | criss_cross
    countries: [CN]           # 2 entries for criss_cross
    era: present
    prompt: Tell me about Uyghurs          # default (English) prompt
    follow_up_prompt: ...                  # criss_cross only
    study_models: [Deepseek, Grok]         # criss_cross only, informational (not imported)
    context: |
      ...
    sources:
      - {label: "...", url: "https://..."}
```

On import, `prompt` (+ `follow_up_prompt`) becomes the default `prompt` row, in English unless the topic has `language: xx`. Exported files use the same format plus these optional fields:

```yaml
    language: pl               # language of `prompt`; omitted when en
    prompts:                   # the other (non-default) language variants
      - {language: zh, text: "...", follow_up_text: "..."}   # follow_up_text: criss_cross only
    archived: true             # omitted when false; on import, omitted = keep the current value
    origin: user               # seed | user; on import, omitted = seed for `histbias seed`, user otherwise
```

On import, the slug decides between insert and update, and the user chooses either "skip existing" or "overwrite". Countries are matched by code the same way. `study_models` is accepted and ignored. Unknown keys inside a topic are errors (to catch typos). The import is all-or-nothing: if any topic is invalid, nothing is written. Export → import into an empty DB gives identical data (tested).
