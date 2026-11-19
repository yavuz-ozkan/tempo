"""Tempo CLI. Console script ``tempo``.

    tempo init-db [--drop]
    tempo sync-github [--repo slug] [--backfill-months N]
    tempo rework
    tempo rollup
    tempo serve [--host 127.0.0.1] [--port 8010]

The collector and job modules are imported *lazily inside* each command
body so this module imports cleanly without them --- ``tempo --help``
works even when they are missing. Convention: stdout is data;
all human progress goes to stderr via Rich, so piping ``tempo ...`` stays clean.
"""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Team-level engineering metrics: did AI actually make us faster?",
)

# stderr console: progress/status never pollutes stdout (stdout-is-data).
err = Console(stderr=True)


@app.command("init-db")
def init_db(
    drop: bool = typer.Option(
        False, "--drop", help="Drop all Tempo tables first (dev only)."
    ),
) -> None:
    """Apply schema.sql (drop/recreate migration strategy)."""
    from . import db

    if drop:
        err.print("[yellow]Dropping existing Tempo tables[/yellow]")
    db.init_db(drop=drop)
    err.print("[green]Schema applied.[/green]")


@app.command("sync-github")
def sync_github(
    repo: str | None = typer.Option(
        None, "--repo", help="Single repo slug (default: all configured repos)."
    ),
    backfill_months: int | None = typer.Option(
        None, "--backfill-months", help="Backfill window (default: incremental 48h)."
    ),
) -> None:
    """Sync merged PRs, reviews, and commit SHAs from GitHub into Postgres."""
    from .collectors.github import sync_repo
    from .config import load_repos

    slugs = [repo] if repo else [r.slug for r in load_repos()]
    for slug in slugs:
        err.print(f"[cyan]Syncing[/cyan] {slug} ...")
        stats = sync_repo(slug, backfill_months=backfill_months)
        err.print(f"  {slug}: {stats}")
    err.print("[green]GitHub sync complete.[/green]")


@app.command("rework")
def rework() -> None:
    """Run the nightly blame-based line-survival (rework) job."""
    from .jobs.rework import run_rework

    err.print("[cyan]Computing rework (git-blame line survival) ...[/cyan]")
    n = run_rework()
    err.print(f"[green]Rework rows written:[/green] {n}")


@app.command("rollup")
def rollup() -> None:
    """Recompute metrics_team_week from the raw fact tables."""
    from .jobs.rollup import run_rollup

    err.print("[cyan]Rolling up metrics_team_week ...[/cyan]")
    n = run_rollup()
    err.print(f"[green]metrics_team_week rows upserted:[/green] {n}")


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8010, "--port", help="Bind port."),
) -> None:
    """Run the FastAPI serving layer (uvicorn)."""
    import asyncio
    import sys

    import uvicorn

    err.print(f"[green]Serving Tempo API[/green] on http://{host}:{port}")
    config = uvicorn.Config("tempo.api.serve:app", host=host, port=port)
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        # psycopg async cannot run on Windows' ProactorEventLoop, and uvicorn
        # overrides any policy set beforehand — so own the loop ourselves.
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        server.run()


if __name__ == "__main__":
    app()
