"""Serving-layer tests. Route handlers call module-level
``fetch_repos``/``fetch_series``; we monkeypatch those, so no Postgres is
touched. TestClient is used without its context manager on purpose --- that
skips the DB-opening lifespan, which unit tests must not require."""

from __future__ import annotations

from datetime import date

from starlette.testclient import TestClient

from tempo.api import serve
from tempo.api.serve import app
from tempo.metrics import keys


def test_healthz():
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_repos(monkeypatch):
    async def fake_repos():
        return ["example/repo", "psf/requests"]

    monkeypatch.setattr(serve, "fetch_repos", fake_repos)
    r = TestClient(app).get("/api/repos")
    assert r.status_code == 200
    assert r.json() == {"repos": ["example/repo", "psf/requests"]}


def test_metrics_wire_shape_weeks_ascending_and_null_mask(monkeypatch):
    captured = {}

    async def fake_series(repo, metrics, from_, to_):
        captured["args"] = (repo, metrics, from_, to_)
        return [
            {
                "metric": "pr_lead_time_p50_hours",
                "points": [
                    {"week": "2026-06-29", "value": 12.4, "n": 5},
                    {"week": "2026-07-06", "value": None, "n": 2},
                ],
            }
        ]

    monkeypatch.setattr(serve, "fetch_series", fake_series)
    r = TestClient(app).get(
        "/api/metrics",
        params={
            "repo": "example/repo",
            "metrics": "pr_lead_time_p50_hours",
            "from": "2026-06-01",
            "to": "2026-07-31",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["repo"] == "example/repo"
    assert len(body["series"]) == 1
    points = body["series"][0]["points"]
    weeks = [p["week"] for p in points]
    assert weeks == sorted(weeks)  # ascending
    assert points[1]["value"] is None  # n-floor mask -> JSON null
    assert points[1]["n"] == 2
    # handler parsed the dates and passed the single requested metric through
    assert captured["args"] == (
        "example/repo",
        ["pr_lead_time_p50_hours"],
        date(2026, 6, 1),
        date(2026, 7, 31),
    )


def test_metrics_default_is_all_metrics(monkeypatch):
    captured = {}

    async def fake_series(repo, metrics, from_, to_):
        captured["metrics"] = metrics
        return []

    monkeypatch.setattr(serve, "fetch_series", fake_series)
    r = TestClient(app).get("/api/metrics", params={"repo": "x/y"})
    assert r.status_code == 200
    assert captured["metrics"] == list(keys.ALL_METRICS)


def test_metrics_unknown_metric_422_lists_all(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("fetch_series must not run for an unknown metric")

    monkeypatch.setattr(serve, "fetch_series", boom)
    r = TestClient(app).get(
        "/api/metrics",
        params={"repo": "x/y", "metrics": "bogus_metric,pr_lead_time_p50_hours"},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["unknown"] == ["bogus_metric"]
    assert detail["allowed"] == list(keys.ALL_METRICS)


def test_metrics_requires_repo():
    r = TestClient(app).get("/api/metrics")
    assert r.status_code == 422
