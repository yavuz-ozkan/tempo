"""Unit tests — GitHub collector over recorded fixtures, zero network.

Every test drives `tempo.collectors.github` through an injected `run_query`
returning hand-trimmed JSON fixtures (logins already anonymized to
alice/bob/carol/dave). No GITHUB_TOKEN, no Postgres. The DB write path is
exercised against an upsert-simulating fake connection.

Coverage: fresh insert, idempotent re-upsert, draft
ready_at, both revert patterns, multi-page reviews/commits, ghost author,
incremental cutoff, backfill cutoff, pending-review skip, no-network proof,
raw-login-never-in-rows privacy proof, real hmac_author wiring.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tempo import db
from tempo.collectors import github
from tempo.config import settings

UTC = timezone.utc
FIX = Path(__file__).parent / "fixtures" / "github"

# Content-test clock: the main fixture's PRs are updatedAt 2026-07-07, well
# inside the 48h incremental window relative to this fixed "now".
NOW = datetime(2026, 7, 8, tzinfo=UTC)


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def fake_hash(login: str) -> str:
    """Deterministic, salt-free stand-in for db.hmac_author (readable in asserts)."""
    return "H:" + login


def opaque_hash(login: str) -> str:
    """A hasher whose output does NOT contain the login — for the privacy proof."""
    return "gh_" + hashlib.sha256(login.encode()).hexdigest()


def make_run_query(list_pages, *, review_pages=None, commit_pages=None, calls=None):
    """Fake seam. `list_pages` keyed by PRList cursor; review/commit pages keyed
    by (pr_number, cursor). Requesting an unregistered page fails loudly, which
    is how the cutoff test proves it stopped paginating."""
    review_pages = review_pages or {}
    commit_pages = commit_pages or {}

    def rq(query: str, variables: dict) -> dict:
        if calls is not None:
            calls.append((query, variables))
        if "query PRList" in query:
            cursor = variables.get("cursor")
            assert cursor in list_pages, f"unexpected list cursor {cursor!r}"
            return list_pages[cursor]
        if "query ReviewsPage" in query:
            return review_pages[(variables["number"], variables.get("cursor"))]
        if "query CommitsPage" in query:
            return commit_pages[(variables["number"], variables.get("cursor"))]
        raise AssertionError(f"unexpected query: {query[:40]!r}")

    return rq


def collect_main(*, calls=None):
    """Collect the primary multi-PR fixture (with PR 106's follow-up pages)."""
    rq = make_run_query(
        {None: load("pr_list_main.json")},
        review_pages={(106, "RC1"): load("reviews_page2_pr106.json")},
        commit_pages={(106, "CC1"): load("commits_page2_pr106.json")},
        calls=calls,
    )
    return github.collect_rows(
        "acme/widgets", run_query=rq, hash_author=fake_hash, now=NOW
    )


def by_number(rowsets):
    return {rs.pr["number"]: rs for rs in rowsets}


# --------------------------------------------------------------------------- #
# Fakes for the SQL write path (simulate ON CONFLICT upsert into dicts)        #
# --------------------------------------------------------------------------- #

class FakeCursor:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, seq):
        self.store["sql"].append(sql)
        if "INTO pull_requests" in sql:
            table, key = "pull_requests", lambda r: r["id"]
        elif "INTO reviews" in sql:
            table, key = "reviews", lambda r: r["id"]
        elif "INTO pr_commits" in sql:
            table, key = "pr_commits", lambda r: (r["pr_id"], r["sha"])
        else:  # pragma: no cover - guards against an unexpected statement
            raise AssertionError(sql)
        bucket = self.store.setdefault(table, {})
        for row in seq:
            bucket[key(row)] = dict(row)  # upsert = overwrite on primary key


class FakeConn:
    def __init__(self):
        self.store = {"sql": []}
        self.commits = 0

    def cursor(self):
        return FakeCursor(self.store)

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    yield
    settings.cache_clear()


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #

def test_fresh_insert_builds_expected_pr_row():
    rowsets, _ = collect_main()
    rs = by_number(rowsets)[101]

    assert rs.pr["id"] == 101001
    assert rs.pr["repo"] == "acme/widgets"
    assert rs.pr["number"] == 101
    assert rs.pr["author_hash"] == "H:alice"
    assert rs.pr["first_commit_at"] == datetime(2026, 7, 1, 8, 30, tzinfo=UTC)
    # not a draft -> ready_at is the PR createdAt
    assert rs.pr["ready_at"] == datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    assert rs.pr["merged_at"] == datetime(2026, 7, 3, 15, 30, tzinfo=UTC)
    assert (rs.pr["additions"], rs.pr["deletions"], rs.pr["changed_files"]) == (40, 5, 3)
    assert rs.pr["is_revert"] is False
    # titles/bodies never survive row-building
    assert "title" not in rs.pr and "body" not in rs.pr

    # one submitted review; the PENDING one (submittedAt: null) is skipped
    assert len(rs.reviews) == 1
    review = rs.reviews[0]
    assert review["id"] == 5001
    assert review["pr_id"] == 101001
    assert review["reviewer_hash"] == "H:bob"
    assert review["submitted_at"] == datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    assert review["state"] == "APPROVED"

    assert rs.commit_shas == ["aaa1110000000000000000000000000000000000"]


def test_pending_review_is_skipped():
    rowsets, _ = collect_main()
    rs = by_number(rowsets)[101]
    # carol's PENDING review (databaseId 5002, submittedAt null) must not appear
    assert all(r["id"] != 5002 for r in rs.reviews)
    assert all(r["reviewer_hash"] != "H:carol" for r in rs.reviews)


def test_draft_pr_ready_at_from_ready_for_review_event():
    rowsets, _ = collect_main()
    rs = by_number(rowsets)[102]
    # draft: ready_at is the READY_FOR_REVIEW event (10:00), not createdAt (08:00)
    assert rs.pr["ready_at"] == datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    assert rs.pr["ready_at"] != datetime(2026, 7, 1, 8, 0, tzinfo=UTC)


def test_revert_detection_title_pattern():
    rowsets, _ = collect_main()
    # PR 103 title is 'Revert "..."' but its commit message has no marker
    assert by_number(rowsets)[103].pr["is_revert"] is True


def test_revert_detection_commit_message_pattern():
    rowsets, _ = collect_main()
    prs = by_number(rowsets)
    # PR 104 title does NOT match ^Revert; detection is via "This reverts commit"
    assert prs[104].pr["is_revert"] is True
    # control: an ordinary PR is not flagged
    assert prs[101].pr["is_revert"] is False


def test_ghost_author_hashes_literal_ghost():
    rowsets, _ = collect_main()
    rs = by_number(rowsets)[105]  # author: null
    assert rs.pr["author_hash"] == "H:ghost"


def test_multipage_reviews_and_commits_are_merged():
    calls = []
    rowsets, _ = collect_main(calls=calls)
    rs = by_number(rowsets)[106]

    # reviews across both pages, in order (bob, carol from page 1; dave from page 2)
    assert [r["reviewer_hash"] for r in rs.reviews] == ["H:bob", "H:carol", "H:dave"]
    # commit SHAs across both pages
    assert rs.commit_shas == [
        "fff6010000000000000000000000000000000000",
        "fff6020000000000000000000000000000000000",
        "fff6030000000000000000000000000000000000",
    ]
    # earliest authoredDate lives on page 2 -> proves pages are merged first
    assert rs.pr["first_commit_at"] == datetime(2026, 6, 19, 8, 0, tzinfo=UTC)

    # follow-up sub-connection queries were actually issued
    assert any("ReviewsPage" in q for q, _ in calls)
    assert any("CommitsPage" in q for q, _ in calls)


def test_stats_counts_and_cost_hint():
    rowsets, stats = collect_main()
    assert stats.prs_upserted == 6
    assert stats.reviews_upserted == 4  # PR101:1 + PR106:3
    assert stats.commits_upserted == 8  # five single-commit PRs + PR106:3
    assert stats.pages == 1
    # rateLimit.cost summed: main(1) + PR106 reviews page(1) + PR106 commits page(1)
    assert stats.cost_points_hint == 3


def test_incremental_cutoff_stops_on_old_page():
    calls = []
    rq = make_run_query(
        {None: load("pr_list_cutoff_page1.json"), "P1": load("pr_list_cutoff_page2.json")},
        calls=calls,
    )
    now = datetime(2026, 7, 6, tzinfo=UTC)  # cutoff = 2026-07-04T00:00Z
    rowsets, stats = github.collect_rows(
        "acme/widgets", run_query=rq, hash_author=fake_hash, now=now
    )
    assert sorted(rs.pr["number"] for rs in rowsets) == [201, 202]
    assert stats.pages == 2
    # page beyond "P1" (cursor "P2") is never requested — we stopped at the old PR
    assert not any(v.get("cursor") == "P2" for q, v in calls if "PRList" in q)


def test_backfill_cutoff_uses_months():
    rq = make_run_query(
        {None: load("pr_list_cutoff_page1.json"), "P1": load("pr_list_cutoff_page2.json")}
    )
    now = datetime(2026, 7, 6, tzinfo=UTC)  # 1-month cutoff = 2026-06-06T00:00Z
    rowsets, _ = github.collect_rows(
        "acme/widgets", backfill_months=1, run_query=rq, hash_author=fake_hash, now=now
    )
    # 204 (updatedAt 2026-06-01) falls before the 1-month window; 203 is inside
    assert sorted(rs.pr["number"] for rs in rowsets) == [201, 202, 203]


def test_no_network_when_run_query_injected(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def boom(*args, **kwargs):
        raise AssertionError("network path was touched")

    monkeypatch.setattr(github, "_default_run_query", boom)
    monkeypatch.setattr(github.httpx, "post", boom)

    rowsets, stats = collect_main()
    assert stats.prs_upserted == 6  # succeeded with zero network


def test_no_raw_login_survives_row_building():
    rq = make_run_query(
        {None: load("pr_list_main.json")},
        review_pages={(106, "RC1"): load("reviews_page2_pr106.json")},
        commit_pages={(106, "CC1"): load("commits_page2_pr106.json")},
    )
    rowsets, _ = github.collect_rows(
        "acme/widgets", run_query=rq, hash_author=opaque_hash, now=NOW
    )
    blob = json.dumps(
        [
            {"pr": {k: str(v) for k, v in rs.pr.items()},
             "reviews": rs.reviews, "shas": rs.commit_shas}
            for rs in rowsets
        ],
        default=str,
    )
    for login in ("alice", "bob", "carol", "dave"):
        assert login not in blob, f"raw login {login!r} leaked into row data"


def test_idempotent_reupsert_same_ids():
    rowsets, _ = collect_main()
    conn = FakeConn()

    github.write_rows(rowsets, conn=conn)
    first = {t: len(conn.store.get(t, {})) for t in ("pull_requests", "reviews", "pr_commits")}

    github.write_rows(rowsets, conn=conn)  # re-run identical data
    second = {t: len(conn.store.get(t, {})) for t in ("pull_requests", "reviews", "pr_commits")}

    assert first == second == {"pull_requests": 6, "reviews": 4, "pr_commits": 8}
    # genuine upsert semantics in the SQL
    assert any("ON CONFLICT (id) DO UPDATE" in s for s in conn.store["sql"])
    assert any("ON CONFLICT (pr_id, sha) DO NOTHING" in s for s in conn.store["sql"])


def test_sync_repo_fresh_insert_uses_real_hmac_and_commits(monkeypatch):
    monkeypatch.setenv("TEMPO_AUTHOR_SALT", "unit-test-salt")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    settings.cache_clear()

    conn = FakeConn()

    @contextmanager
    def _connection():
        yield conn

    class FakePool:
        def connection(self):
            return _connection()

    monkeypatch.setattr(github.db, "pool", lambda: FakePool())

    rq = make_run_query(
        {None: load("pr_list_main.json")},
        review_pages={(106, "RC1"): load("reviews_page2_pr106.json")},
        commit_pages={(106, "CC1"): load("commits_page2_pr106.json")},
    )
    # large backfill window so the cutoff is decoupled from the real wall clock
    stats = github.sync_repo("acme/widgets", backfill_months=120, run_query=rq)

    assert stats.prs_upserted == 6
    assert conn.commits == 1
    assert len(conn.store["pull_requests"]) == 6

    # default hash path is db.hmac_author: a 64-char hex, never the raw login
    author_hash = conn.store["pull_requests"][101001]["author_hash"]
    assert author_hash == db.hmac_author("alice")
    assert len(author_hash) == 64 and "alice" not in author_hash


def test_prefers_full_database_id_over_int_database_id():
    # fullDatabaseId is the BigInt-safe id; ensure it is used when present
    rowsets, _ = collect_main()
    assert by_number(rowsets)[101].pr["id"] == 101001


def test_invalid_slug_rejected():
    with pytest.raises(ValueError):
        github.collect_rows("not-a-slug", run_query=lambda q, v: {}, hash_author=fake_hash, now=NOW)


def test_merge_commit_oid_lands_in_pr_commits():
    """Squash-merge repos: blame attributes survival to the squash commit,
    which is mergeCommit.oid — it must be recorded (integration find)."""
    from tempo.collectors.github import build_pr_rowset

    node = {
        "fullDatabaseId": "9001",
        "number": 42,
        "title": "feat: thing",
        "body": "",
        "createdAt": "2026-06-01T00:00:00Z",
        "mergedAt": "2026-06-02T00:00:00Z",
        "isDraft": False,
        "additions": 1,
        "deletions": 0,
        "changedFiles": 1,
        "mergeCommit": {"oid": "e" * 40},
        "author": {"login": "alice"},
        "reviews": {"nodes": []},
        "commits": {"nodes": [{"commit": {"oid": "a" * 40, "authoredDate": "2026-06-01T00:00:00Z", "message": "x"}}]},
        "timelineItems": {"nodes": []},
    }
    rowset = build_pr_rowset(node, repo="r/r", hash_author=lambda login: "h:" + login)
    assert "e" * 40 in rowset.commit_shas
    assert "a" * 40 in rowset.commit_shas
