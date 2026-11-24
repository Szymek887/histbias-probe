"""Command line: `histbias init | serve | seed | export-topics | import-topics | compare-study`."""

import logging
from pathlib import Path

import typer
import uvicorn
from sqlmodel import Session

from app.config import get_settings
from app.crypto import load_key
from app.db import db_exists, get_engine, migrate
from app.log import setup_logging
from app.services import providers as provider_svc
from app.services import topic_io

app = typer.Typer(
    help="HistBias Probe: test LLMs for censorship and historical bias.", no_args_is_help=True
)
log = logging.getLogger("histbias")


def _print_result(r: topic_io.ImportResult) -> None:
    typer.echo(
        f"Topics: {r.topics_added} added, {r.topics_updated} updated, "
        f"{r.topics_skipped} skipped, {r.topics_unchanged} unchanged. "
        f"Countries: {r.countries_added} added, {r.countries_updated} updated."
    )


def _print_presets(r: provider_svc.PresetResult) -> None:
    typer.echo(f"Providers: {r.providers_added} added. Models: {r.models_added} added.")


def _require_db():
    settings = get_settings()
    if not db_exists(settings):
        typer.echo(f"No database at {settings.db_path}. Run `histbias init` first.", err=True)
        raise typer.Exit(1)
    return settings


@app.command()
def init(
    seed: bool = typer.Option(
        True, help="Load seed/topics.yaml and seed/providers.yaml (existing rows are kept)."
    ),
) -> None:
    """Create the data directory, database and encryption key, and load the seed data.
    Safe to re-run."""
    settings = get_settings()
    setup_logging(settings.log_level)
    migrate(settings)
    load_key(settings)  # creates data/secret.key (0600) unless HISTBIAS_SECRET_KEY is set
    typer.echo(f"Database ready: {settings.db_path}")
    if seed:
        with Session(get_engine(settings)) as session:
            _print_result(topic_io.import_seed(session))
            _print_presets(provider_svc.load_presets(session))


@app.command("seed")
def seed_cmd(
    overwrite: bool = typer.Option(
        False, help="Overwrite seed topics you have edited with the seed file's version."
    ),
) -> None:
    """(Re)load seed/topics.yaml and seed/providers.yaml. New rows are added; existing topics
    are matched by slug, providers by name, models by display name."""
    settings = _require_db()
    setup_logging(settings.log_level)
    mode = topic_io.ImportMode.OVERWRITE if overwrite else topic_io.ImportMode.SKIP
    with Session(get_engine(settings)) as session:
        _print_result(topic_io.import_seed(session, mode=mode))
        _print_presets(provider_svc.load_presets(session))


@app.command("export-topics")
def export_topics_cmd(
    out: Path | None = typer.Argument(None, help="Output file (default: stdout)."),
) -> None:
    """Export all topics (including archived) as YAML."""
    settings = _require_db()
    with Session(get_engine(settings)) as session:
        content = topic_io.export_topics(session)
    if out is None:
        typer.echo(content, nl=False)
    else:
        out.write_text(content, encoding="utf-8")
        typer.echo(f"Wrote {out}", err=True)


@app.command("import-topics")
def import_topics_cmd(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    overwrite: bool = typer.Option(False, help="Overwrite existing topics (matched by slug)."),
) -> None:
    """Import topics from a YAML file."""
    settings = _require_db()
    setup_logging(settings.log_level)
    try:
        parsed = topic_io.parse_topic_file(file.read_bytes())
    except topic_io.ImportFormatError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    mode = topic_io.ImportMode.OVERWRITE if overwrite else topic_io.ImportMode.SKIP
    with Session(get_engine(settings)) as session:
        preview = topic_io.preview_import(session, parsed)
        if not preview.ok:
            for err in preview.errors:
                typer.echo(f"Error: {err}", err=True)
            raise typer.Exit(1)
        _print_result(topic_io.apply_import(session, parsed, mode))


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Bind address (default: HISTBIAS_HOST or 127.0.0.1)."),
    port: int | None = typer.Option(None, help="Port (default: HISTBIAS_PORT or 8000)."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (development)."),
    init_missing: bool = typer.Option(
        False, "--init", help="If there is no database yet, run `histbias init` first (used by the Docker image)."
    ),
) -> None:
    """Start the web app."""
    if init_missing and not db_exists(get_settings()):
        init(seed=True)
    settings = _require_db()
    setup_logging(settings.log_level)
    migrate(settings)  # apply new migrations to an existing database (no-op when up to date)
    if host is not None:
        settings.host = host
    if port is not None:
        settings.port = port
    if warning := settings.bind_warning():
        log.warning(warning)
    log.info("Serving on http://%s:%s", settings.host, settings.port)
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=reload,
        log_config=None,  # use our redacting root handler
    )


@app.command("compare-study")
def compare_study_cmd(run_id: int = typer.Argument(..., help="Run ID (see the Runs page).")) -> None:
    """Compare a judged run's mean scores with the AGH study's human ranking
    (seed/study_ranking.yaml)."""
    from app.models import Run
    from app.services import results, study

    settings = _require_db()
    with Session(get_engine(settings)) as session:
        run = session.get(Run, run_id)
        if run is None:
            typer.echo(f"No run {run_id}.", err=True)
            raise typer.Exit(1)
        scores = results.scorecard(results.load_rows(session, run))
    cmp = study.compare(scores, study.model_ids_of(run.config or {}))
    if not cmp.pairs:
        typer.echo("No judged model in this run matches a study model (see seed/study_ranking.yaml).", err=True)
        raise typer.Exit(1)
    typer.echo(f"Run {run.id} ({run.name}) vs. the study's human ranking\n")
    typer.echo(f"{'Study model':<20} {'Place':>5} {'Study':>6} {'Run model':<22} {'Run':>6} {'Diff':>6} {'Rank s→r':>9}")
    for p in cmp.pairs:
        typer.echo(
            f"{p.study.name:<20} {p.study.place:>5} {p.study.mean:>6.2f} {p.score.name[:22]:<22} "
            f"{p.score.mean:>6.2f} {p.delta:>+6.2f} {p.study_rank:>4.1f}→{p.app_rank:<4.1f}"
        )
    rho = "n/a (needs 3+ models)" if cmp.spearman is None else f"{cmp.spearman:.2f}"
    typer.echo(f"\nMatched models: {cmp.n}. Spearman's rho: {rho}. Mean |diff|: {cmp.mean_abs_delta:.2f}.")
    if cmp.missing:
        typer.echo("Not in this run: " + ", ".join(m.name for m in cmp.missing))
    if cmp.unmatched:
        typer.echo("No study counterpart: " + ", ".join(cmp.unmatched))
