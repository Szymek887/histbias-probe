# 07 – Security

The app is a **local, single-user tool**, but it holds paid API tokens, so their handling must be careful.

## API token storage

- Tokens are entered in **Settings → API keys** through a `type="password"` field, sent only by POST to the local server.
- They are encrypted with **Fernet** (`cryptography`; AES-128-CBC + HMAC) and stored in `credential.secret_enc`. Extra secret fields, such as a Yandex folder ID or a GigaChat client ID, go in `extra_enc`.
- **Encryption key:**
  - `HISTBIAS_SECRET_KEY` from the environment, if set.
  - Otherwise it is generated on first start into `data/secret.key` with file mode `0600`.
  - Losing the key means stored tokens can't be decrypted. The UI then offers "re-enter keys" and doesn't crash.
- **Display:** only `••••` plus the last 4 characters (`credential.hint`). The decrypted token is never rendered into HTML, never returned by any endpoint, and never put in an export.
- **Environment override:** if a variable like `HISTBIAS_KEY_ANTHROPIC` exists, it is used instead of the stored key, and the UI shows "from environment". This is useful for CI and for people who don't want keys in the DB.
- **Deleting a key** removes the row. Older runs keep working, because responses don't reference credentials.

## Never leak tokens

- **Logging:** a filter redacts `Authorization`, `x-api-key`, and `api_key` values. Logs never include headers.
- **Stored requests:** `response.messages` and `raw` hold only bodies, never headers. Adapters must build `raw_request` from the body only, and a test verifies that.
- **Exports:** run exports (CSV/JSON) and topic exports contain no credential data. A test checks this too.
- **Tracebacks:** the error page in non-debug mode shows no locals.

## Network exposure

- The default bind is `127.0.0.1`. Binding to `0.0.0.0` requires `HISTBIAS_HOST` set explicitly, and the app then logs a warning. Since there are no accounts, anyone who can reach the port can use your keys.
- **CSRF:** forms include a CSRF token (double-submit cookie). The `Origin` header is checked on state-changing requests, which protects against malicious web pages posting to `localhost`.
- No CORS is enabled.

## Content handling

- Model answers are untrusted. They are rendered as Markdown with HTML **escaped and sanitized** (`markdown-it-py` with HTML disabled + `bleach`/`nh3`). Never use `|safe` on model output.
- Imported YAML is parsed with `yaml.safe_load` and validated with a Pydantic schema, with a maximum file size of 5 MB.
- Model answers are never used as code or instructions by the app. The judge gets them as quoted data inside its prompt.

## Repository hygiene

- `.gitignore`: `data/`, `.env`, `*.db`, `secret.key`.
- `seed/providers.yaml` holds no secrets. A pre-commit check (`detect-secrets` or a simple regex) is optional for M7.
