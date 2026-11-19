# Tempo API image: uv-based, runs the FastAPI serving
# layer. The same image also carries the CLI, so the nightly jobs can run as
# `docker compose run --rm api tempo sync-github|rework|rollup` from host cron.
FROM python:3.12-slim

# uv (pinned via the published image) for reproducible, lockfile-driven installs.
COPY --from=ghcr.io/astral-sh/uv:0.8.13 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer first (cached until the lockfile changes). --no-install-project
# so app source changes don't bust the dependency cache.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

# Application source + the files the CLI reads at runtime.
COPY tempo ./tempo
COPY schema.sql ./schema.sql
COPY config ./config
RUN uv sync --frozen --no-dev

EXPOSE 8010

# stdout-is-data convention holds here too; uvicorn logs go to stderr.
CMD ["uv", "run", "--no-sync", "tempo", "serve", "--host", "0.0.0.0", "--port", "8010"]
