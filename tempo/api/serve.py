"""FastAPI serving layer.

Three GET endpoints over the ONLY table the API is allowed to touch:
``metrics_team_week``. There is deliberately no per-author
endpoint and no import of any raw fact table --- the dashboard cannot show an
individual because the API cannot serve one.

Test seam: the route handlers call the module-level coroutines ``fetch_repos``
and ``fetch_series``; unit tests monkeypatch those, so no Postgres is needed to
assert the wire shape, the n-floor masking, or the 422 on unknown metrics.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException, Query

if sys.platform == "win32":
    # psycopg's async pool cannot run on Windows' default ProactorEventLoop
    # (uvicorn's default there). Selector loop is fine — this API spawns no
    # subprocesses. Linux/Docker deploys are untouched.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from .. import db
from ..metrics import keys
from . import otlp

_ALL_METRICS = set(keys.ALL_METRICS)


# --- DB access (monkeypatched away in unit tests) ---------------------------


async def fetch_repos() -> list[str]:
    """Distinct repos present in metrics_team_week, ascending."""
    async with db.async_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT repo FROM metrics_team_week ORDER BY repo"
            )
            return [row[0] for row in await cur.fetchall()]


async def fetch_series(
    repo: str,
    metrics: list[str],
    from_: date | None,
    to_: date | None,
) -> list[dict]:
    """Return one series per requested metric that has data, weeks ascending.
    A NULL ``value`` (n-floor mask) is passed straight through as
    JSON null; the frontend renders it as "insufficient n"."""
    conds = ["repo = %s", "metric = ANY(%s)"]
    params: list[object] = [repo, list(metrics)]
    if from_ is not None:
        conds.append("week >= %s")
        params.append(from_)
    if to_ is not None:
        conds.append("week <= %s")
        params.append(to_)
    sql = (
        "SELECT metric, week, value, n FROM metrics_team_week "
        f"WHERE {' AND '.join(conds)} ORDER BY metric, week"
    )
    by_metric: dict[str, list[dict]] = {}
    async with db.async_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
    for metric, week, value, n in rows:
        by_metric.setdefault(metric, []).append(
            {
                "week": week.isoformat(),
                "value": None if value is None else float(value),
                "n": None if n is None else int(n),
            }
        )
    # Preserve the requested metric order; only metrics with data get a series.
    return [
        {"metric": m, "points": by_metric[m]} for m in metrics if m in by_metric
    ]


# --- app --------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.async_pool().open()
    try:
        yield
    finally:
        await db.async_pool().close()


app = FastAPI(
    title="Tempo",
    summary="Team-level engineering metrics: did AI actually make us faster?",
    lifespan=lifespan,
)
app.include_router(otlp.router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/api/repos")
async def get_repos() -> dict:
    return {"repos": await fetch_repos()}


@app.get("/api/metrics")
async def get_metrics(
    repo: str,
    metrics: str | None = Query(default=None),
    from_: date | None = Query(default=None, alias="from"),
    to_: date | None = Query(default=None, alias="to"),
) -> dict:
    """``/api/metrics?repo=<slug>&metrics=<csv, default all>&from=<date>&to=<date>``.

    Unknown metric names -> 422 listing ``keys.ALL_METRICS``. Weeks ascending;
    ``value: null`` marks an n-floor-masked slice."""
    if metrics is None:
        requested = list(keys.ALL_METRICS)
    else:
        requested = [m.strip() for m in metrics.split(",") if m.strip()]
    unknown = [m for m in requested if m not in _ALL_METRICS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unknown metric(s)",
                "unknown": unknown,
                "allowed": list(keys.ALL_METRICS),
            },
        )
    series = await fetch_series(repo, requested, from_, to_)
    return {"repo": repo, "series": series}
