"""Settings and tracked-repo config. FROZEN.

Env (.env at repo root, loaded here without overriding real env):
  TEMPO_DATABASE_URL  postgres DSN     (default: local docker-compose instance)
  TEMPO_AUTHOR_SALT   HMAC salt        (required for ingest; never in the DB)
  GITHUB_TOKEN        fine-grained read-only PAT (or `gh auth token` output)
  TEMPO_OTLP_TOKEN    optional bearer token the OTLP receiver requires when set
  TEMPO_ORG_UNIT      org_unit value for AI telemetry rows (default: "default")
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    author_salt: str
    github_token: str
    otlp_token: str | None
    org_unit: str


@lru_cache(maxsize=1)
def settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    return Settings(
        database_url=os.environ.get(
            "TEMPO_DATABASE_URL", "postgresql://tempo:tempo@localhost:5433/tempo"
        ),
        author_salt=os.environ.get("TEMPO_AUTHOR_SALT", ""),
        github_token=os.environ.get("GITHUB_TOKEN", ""),
        otlp_token=os.environ.get("TEMPO_OTLP_TOKEN") or None,
        org_unit=os.environ.get("TEMPO_ORG_UNIT", "default"),
    )


@dataclass(frozen=True, slots=True)
class TrackedRepo:
    slug: str          # e.g. "pallets/flask" — also the `repo` value in every table
    git_url: str
    clone_path: Path   # bare clone location for the rework blame job


def load_repos(path: Path | None = None) -> list[TrackedRepo]:
    """Parse config/repos.yaml. Raises FileNotFoundError/KeyError loudly —
    a missing or malformed repo config should stop a job, not be guessed at."""
    cfg = yaml.safe_load((path or ROOT / "config" / "repos.yaml").read_text(encoding="utf-8"))
    return [
        TrackedRepo(
            slug=entry["slug"],
            git_url=entry["git_url"],
            clone_path=(ROOT / entry["clone_path"]).resolve(),
        )
        for entry in cfg["repos"]
    ]
