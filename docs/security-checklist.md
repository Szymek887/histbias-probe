# Security checklist (M7)

Each rule from [07 – Security](07-security.md) and how it is verified. Run all checks with `uv run pytest`.
Last verified: 2026-11-25, app version 0.9.0, 335 tests passing.

## API tokens

| Rule | Status | Verified by |
| :--- | :---: | :--- |
| Tokens are encrypted with Fernet; the key comes from `HISTBIAS_SECRET_KEY` or `data/secret.key` (mode 0600) | ✅ | `test_crypto.py::test_key_file_created_0600_and_reused`, `::test_env_key_wins_and_no_file_written`, `::test_roundtrip_and_wrong_key` |
| Extra secret fields (Yandex folder ID, GigaChat client ID) are encrypted too | ✅ | `test_credentials.py::test_extra_secret_fields` |
| A lost key doesn't crash the app; the UI asks to re-enter keys | ✅ | `test_credentials.py::test_lost_key_marks_broken_without_crashing`, `test_keys_models_web.py::test_broken_key_prompts_reentry` |
| Only `••••` + the last 4 characters are shown; the key is never in any page | ✅ | `test_crypto.py::test_hint_and_mask`, `test_keys_models_web.py::test_add_test_replace_delete_key_never_leaks`, `test_security.py::test_stored_key_never_appears_in_pages` |
| `HISTBIAS_KEY_<PROVIDER>` overrides the stored key and is labelled "from environment" | ✅ | `test_credentials.py::test_environment_override`, `test_keys_models_web.py::test_environment_key_shown_as_env` |
| Provider settings (JSON) refuse secrets | ✅ | `test_keys_models_web.py::test_custom_provider_rejects_secret_in_settings` |

## Never leak tokens

| Rule | Status | Verified by |
| :--- | :---: | :--- |
| Logs redact `Authorization`, `x-api-key`, `api_key`, bearer tokens and key query parameters, including in tracebacks | ✅ | `test_log_redaction.py` (all tests) |
| A key echoed back by a provider error is scrubbed from the error message | ✅ | `test_adapters.py::test_echoed_key_is_scrubbed_from_errors`, `test_run_engine.py` (item errors), `test_judge.py` (judge errors) |
| Stored requests (`response.messages`, `raw`) hold bodies only, no headers | ✅ | `test_run_engine.py::test_mock_and_real_model_run_completes` (no key in any stored response) |
| Run exports (CSV/JSON) and topic exports contain no credential data | ✅ | `test_results.py::test_csv_utf8_and_safe`, `test_topic_io.py::test_export_has_no_credentials` |
| The error page shows no traceback or locals (non-debug mode) | ✅ | `test_security.py::test_server_error_page_shows_no_traceback` |
| No API docs or OpenAPI schema are served | ✅ | `test_security.py::test_no_api_docs_and_no_cors` |

## Network exposure

| Rule | Status | Verified by |
| :--- | :---: | :--- |
| Default bind is `127.0.0.1`; another bind logs a warning | ✅ | `test_cli.py::test_serve_defaults_to_localhost`, `test_security.py::test_bind_warnings` |
| Docker: the container listens on `0.0.0.0`, the docs publish it on `127.0.0.1` only | ✅ (by review) | `Dockerfile`, README "Docker" |
| CSRF: double-submit token on every state-changing request (form field or `X-CSRF-Token` header) | ✅ | `test_topics_web.py::test_post_without_csrf_token_is_rejected`, `::test_htmx_header_token_accepted`, `test_runs_web.py::test_actions_require_csrf`, `test_security.py::test_run_actions_check_origin_and_token` |
| A foreign `Origin` is rejected | ✅ | `test_topics_web.py::test_post_with_wrong_token_or_foreign_origin_is_rejected`, `test_security.py::test_run_actions_check_origin_and_token` |
| No CORS headers | ✅ | `test_security.py::test_no_api_docs_and_no_cors` |

## Content handling

| Rule | Status | Verified by |
| :--- | :---: | :--- |
| Model answers are rendered as Markdown with HTML disabled and sanitized (`markdown-it-py` + `nh3`) | ✅ | `test_markdown.py::test_untrusted_html_is_neutralized`, `test_runs_web.py::test_model_output_is_sanitized`, `test_keys_models_web.py::test_test_prompt_output_is_sanitized` |
| Judge output (rationale, issues, raw output) is escaped | ✅ | `test_runs_web.py::test_judge_in_wizard_and_verdict_page` |
| No template uses `|safe`, `Markup()` or turns autoescape off; answer text always goes through `|markdown` | ✅ | `test_security.py::test_no_template_marks_anything_safe`, `::test_model_answers_use_the_markdown_filter` |
| CSV export neutralizes spreadsheet formulas (`= + - @`, tab, CR) | ✅ | `test_results.py::test_csv_utf8_and_safe` |
| YAML imports use `yaml.safe_load`, a Pydantic schema and a 5 MB limit | ✅ | `test_topic_io.py::test_unreadable_files`, `test_topic_schema.py` |
| The judge gets answers as quoted data, never the tested model's name | ✅ | `test_judge.py::test_prompt_v1_contents`, `test_security.py::test_judge_prompt_never_names_the_evaluated_model` |

## TLS

| Rule | Status | Verified by |
| :--- | :---: | :--- |
| TLS verification is never turned off; GigaChat uses a configured CA bundle | ✅ | `test_adapters.py::test_gigachat_missing_ca_bundle_gives_hint` |
| A certificate failure explains the Russian Trusted Root CA requirement | ✅ | `test_adapters.py::test_gigachat_tls_failure_gives_ca_hint`, `test_security.py::test_gigachat_form_explains_the_ca` |

## Repository hygiene

| Rule | Status | Verified by |
| :--- | :---: | :--- |
| `.gitignore` and `.dockerignore` exclude `data/`, `.env`, `*.db`, `secret.key` | ✅ | `test_security.py::test_gitignore_and_dockerignore_keep_secrets_out` |
| `seed/providers.yaml` holds no secrets | ✅ | reviewed; keys are refused in provider settings |
| Pre-commit secret scan (`detect-secrets`) | ⏸ optional | Not set up: the folder isn't a git repository yet. Add it when the repo is created. |

## Known limits (accepted)

- There are no user accounts: anyone who can reach the port can use the stored keys. That is why the bind stays local.
- The mock provider and `Mock Judge` are always available, so a fresh install can be tried without keys.
