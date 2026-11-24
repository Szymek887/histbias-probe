"""FastAPI app factory."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.config import Settings, get_settings
from app.crypto import Cipher, CryptoKeyError
from app.db import get_engine
from app.engine.executor import Executor
from app.log import setup_logging
from app.web.csrf import csrf_cookie_middleware, verify_csrf
from app.web.routes import countries, models, providers, results, runs, settings, topics
from app.web.templates import STATIC_DIR, render

log = logging.getLogger(__name__)


def create_app(app_settings: Settings | None = None) -> FastAPI:
    app_settings = app_settings or get_settings()
    setup_logging(app_settings.log_level)  # idempotent; needed in `--reload` worker processes
    app_settings.ensure_data_dir()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # One background worker per process runs the queued runs (docs/02-architecture.md).
        app.state.executor = None
        if app_settings.run_worker:
            app.state.executor = Executor(get_engine(app_settings), app_settings, app.state.cipher)
            await app.state.executor.start()
        try:
            yield
        finally:
            if app.state.executor is not None:
                await app.state.executor.stop()

    app = FastAPI(
        title="HistBias Probe",
        lifespan=lifespan,
        version=__version__,
        debug=app_settings.debug,
        # No public API docs: this is a local HTML app.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(verify_csrf)],
    )
    app.state.settings = app_settings
    # Creates data/secret.key (0600) on first start if HISTBIAS_SECRET_KEY isn't set.
    app.state.cipher, app.state.cipher_error = None, None
    try:
        app.state.cipher = Cipher.from_settings(app_settings)
    except CryptoKeyError as e:
        app.state.cipher_error = str(e)
        log.error("Encryption key problem: %s", e)
    if warning := app_settings.bind_warning():
        log.warning(warning)
    app.middleware("http")(csrf_cookie_middleware)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for module in (runs, results, topics, countries, models, providers, settings):
        app.include_router(module.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_page(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
        response = render(
            request,
            "error.html",
            page_title=f"Error {exc.status_code}",
            status_code=exc.status_code,
            status=exc.status_code,
            detail=exc.detail,
        )
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def server_error_page(request: Request, exc: Exception) -> HTMLResponse:
        # Logged through the redacting formatter; the page shows no traceback or locals
        # (docs/07-security.md). With HISTBIAS_DEBUG=true Starlette shows its debug page instead.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        detail = "Something went wrong. The error was written to the server log."
        try:
            return render(request, "error.html", page_title="Error 500", status_code=500, status=500, detail=detail)
        except Exception:  # the error page itself must not fail
            return HTMLResponse(f"<h1>Error 500</h1><p>{detail}</p>", status_code=500)

    return app
