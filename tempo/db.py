"""Database access + pseudonymization. FROZEN.

Sync pool for collectors/jobs, async pool for the API. Raw SQL via psycopg 3 —
the schema is a hand-written star (schema.sql); no ORM.
"""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache
from pathlib import Path

from psycopg_pool import AsyncConnectionPool, ConnectionPool

from .config import ROOT, settings


@lru_cache(maxsize=1)
def pool() -> ConnectionPool:
    return ConnectionPool(settings().database_url, min_size=1, max_size=4, open=True)


@lru_cache(maxsize=1)
def async_pool() -> AsyncConnectionPool:
    # Opened lazily by the API's lifespan handler (await .open()).
    return AsyncConnectionPool(settings().database_url, min_size=1, max_size=8, open=False)


def hmac_author(login: str) -> str:
    """Pseudonymize a GitHub login at ingest. Defense-in-depth, not
    anonymization — the hash exists so rework/self-review logic can say "same
    author", never who. Raises if the salt is unset: silently hashing with an
    empty salt would fake the protection."""
    salt = settings().author_salt
    if not salt:
        raise RuntimeError("TEMPO_AUTHOR_SALT is not set — refusing to ingest identity")
    return hmac.new(salt.encode(), login.lower().encode(), hashlib.sha256).hexdigest()


def init_db(drop: bool = False) -> None:
    """Apply schema.sql. drop=True nukes all Tempo tables first (dev only)."""
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    with pool().connection() as conn:
        if drop:
            conn.execute(
                "DROP TABLE IF EXISTS metrics_team_week, rework, pr_commits, "
                "ai_stream_state, ai_usage_daily, deploy_events, issues, "
                "reviews, pull_requests CASCADE"
            )
        conn.execute(schema)
