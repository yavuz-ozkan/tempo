"""GitHub GraphQL collector.

Nightly incremental sync (or a one-time backfill) of MERGED pull requests into
`pull_requests` / `reviews` / `pr_commits`. v1 metrics are all merged-PR
metrics, so only MERGED PRs are queried — newest-updated first, paginated.

Design for testability:
  * `build_pr_rowset` and `collect_rows` are PURE with respect to Postgres —
    they turn a GraphQL payload into plain row dicts, driven by an injected
    `run_query(query, variables) -> dict` seam. Unit tests feed recorded JSON
    fixtures through them with zero network and no `GITHUB_TOKEN`.
  * `write_rows` is the thin SQL layer; it takes an injectable connection so it
    can be exercised against a fake in tests. Live Postgres is not used in unit tests.

Privacy invariants:
  * Raw logins exist only transiently: `hash_author` (default `db.hmac_author`)
    is applied inside row-building, so every dict that leaves `collect_rows`
    carries an HMAC hash and never a raw login.
  * A deleted author (`author: null`) hashes the literal string ``"ghost"``.
  * PR titles/bodies and commit messages are read ONLY to compute `is_revert`,
    then discarded — never returned by row-building, never inserted.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from .. import db
from ..config import settings

UTC = timezone.utc

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

# Page sizes: reviews paginate past 50, commits past 100.
PR_PAGE_SIZE = 50
REVIEWS_PAGE_SIZE = 50
COMMITS_PAGE_SIZE = 100

# Incremental overlap window: PRs updated in the last 48h.
INCREMENTAL_OVERLAP = timedelta(hours=48)

# Revert detection: PR title `^Revert\b`, or git's own revert-template
# marker "This reverts commit" in the PR body or any commit message.
REVERT_TITLE_RE = re.compile(r"^Revert\b")
REVERT_COMMIT_MARKER = "This reverts commit"

RunQuery = Callable[[str, dict], dict]


# --------------------------------------------------------------------------- #
# GraphQL queries (field names verified live against the schema, 2026-07-07).  #
# --------------------------------------------------------------------------- #

PR_LIST_QUERY = """
query PRList($owner: String!, $name: String!, $cursor: String) {
  rateLimit { cost }
  repository(owner: $owner, name: $name) {
    pullRequests(states: [MERGED], first: 50, after: $cursor,
                 orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        databaseId
        fullDatabaseId
        number
        title
        body
        isDraft
        createdAt
        mergedAt
        updatedAt
        additions
        deletions
        changedFiles
        mergeCommit { oid }
        author { login }
        reviews(first: 50) {
          pageInfo { hasNextPage endCursor }
          nodes { databaseId fullDatabaseId author { login } submittedAt state }
        }
        commits(first: 100) {
          pageInfo { hasNextPage endCursor }
          nodes { commit { oid authoredDate message } }
        }
        timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT], first: 10) {
          nodes { __typename ... on ReadyForReviewEvent { createdAt } }
        }
      }
    }
  }
}
"""

REVIEWS_PAGE_QUERY = """
query ReviewsPage($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  rateLimit { cost }
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviews(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { databaseId fullDatabaseId author { login } submittedAt state }
      }
    }
  }
}
"""

COMMITS_PAGE_QUERY = """
query CommitsPage($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  rateLimit { cost }
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      commits(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { commit { oid authoredDate message } }
      }
    }
  }
}
"""


# --------------------------------------------------------------------------- #
# Public result shapes                                                         #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class SyncStats:
    """Outcome of a `sync_repo` run. `cost_points_hint` is the summed GraphQL
    `rateLimit.cost` across all queries (falls back to the page count if the
    API/fixtures did not report a cost)."""

    prs_upserted: int = 0
    reviews_upserted: int = 0
    commits_upserted: int = 0
    pages: int = 0
    cost_points_hint: int = 0


@dataclass(frozen=True, slots=True)
class PRRowset:
    """One PR flattened into ready-to-insert row dicts (schema column names).
    `pr` and `reviews` contain HMAC hashes only — never a raw login."""

    pr: dict
    reviews: list[dict] = field(default_factory=list)
    commit_shas: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Pure helpers (no network, no DB)                                             #
# --------------------------------------------------------------------------- #

def _parse_ts(value: str | None) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp to a tz-aware datetime, or None."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _min_ts(values: Iterable[str | None]) -> datetime | None:
    parsed = [t for t in (_parse_ts(v) for v in values) if t is not None]
    return min(parsed) if parsed else None


def _pick_id(node: dict) -> int:
    """Prefer `fullDatabaseId` (String, BigInt-safe) over the deprecated
    Int-typed `databaseId` — GitHub ids now exceed the 32-bit Int range."""
    full = node.get("fullDatabaseId")
    if full is not None:
        return int(full)
    return int(node["databaseId"])


def _hash_login(author_obj: dict | None, hash_author: Callable[[str], str]) -> str:
    login = author_obj.get("login") if author_obj else None
    return hash_author(login if login else "ghost")


def _ready_at(node: dict) -> datetime | None:
    """`ready_at` = the earliest READY_FOR_REVIEW event's createdAt if the PR
    was ever a draft, else the PR's createdAt (schema.sql)."""
    events = (node.get("timelineItems") or {}).get("nodes") or []
    ready = _min_ts(
        e.get("createdAt")
        for e in events
        if e.get("__typename") == "ReadyForReviewEvent"
    )
    return ready if ready is not None else _parse_ts(node.get("createdAt"))


def _is_revert(title: str, body: str, commit_messages: Iterable[str]) -> bool:
    if REVERT_TITLE_RE.match(title or ""):
        return True
    if REVERT_COMMIT_MARKER in (body or ""):
        return True
    return any(REVERT_COMMIT_MARKER in (m or "") for m in commit_messages)


def build_pr_rowset(
    node: dict, *, repo: str, hash_author: Callable[[str], str]
) -> PRRowset:
    """Pure: a fully-assembled PR node (reviews/commits already de-paginated)
    -> row dicts. Titles/bodies/commit messages are consumed for `is_revert`
    and then dropped; nothing identifying survives beyond HMAC hashes."""
    pr_id = _pick_id(node)
    commit_nodes = ((node.get("commits") or {}).get("nodes")) or []
    commit_shas = [c["commit"]["oid"] for c in commit_nodes]
    # Squash-merge repos: the PR's branch commits never enter the canonical
    # history — blame attributes surviving lines to the squash commit, which is
    # mergeCommit. Include it so the rework job can match survival.
    # (Merge-commit workflows: it's the merge SHA, which blame never emits and
    # the rework job skips — harmless. Rebase-merge repos remain a known
    # undercount.)
    merge_oid = (node.get("mergeCommit") or {}).get("oid")
    if merge_oid and merge_oid not in commit_shas:
        commit_shas.append(merge_oid)
    first_commit_at = _min_ts(c["commit"].get("authoredDate") for c in commit_nodes)
    commit_messages = [c["commit"].get("message", "") for c in commit_nodes]

    pr = {
        "id": pr_id,
        "repo": repo,
        "number": int(node["number"]),
        "author_hash": _hash_login(node.get("author"), hash_author),
        "first_commit_at": first_commit_at,
        "ready_at": _ready_at(node),
        "merged_at": _parse_ts(node.get("mergedAt")),
        "additions": node.get("additions"),
        "deletions": node.get("deletions"),
        "changed_files": node.get("changedFiles"),
        "is_revert": _is_revert(
            node.get("title") or "", node.get("body") or "", commit_messages
        ),
    }

    reviews: list[dict] = []
    for r in ((node.get("reviews") or {}).get("nodes")) or []:
        submitted_at = _parse_ts(r.get("submittedAt"))
        if submitted_at is None:
            # PENDING / unsubmitted reviews have no submittedAt; the schema
            # requires submitted_at NOT NULL, so they are not review events.
            continue
        reviews.append(
            {
                "id": _pick_id(r),
                "pr_id": pr_id,
                "reviewer_hash": _hash_login(r.get("author"), hash_author),
                "submitted_at": submitted_at,
                "state": r["state"],
            }
        )

    return PRRowset(pr=pr, reviews=reviews, commit_shas=commit_shas)


def _months_before(dt: datetime, months: int) -> datetime:
    """`dt` minus `months` calendar months (day clamped to month length).
    Avoids a dateutil dependency."""
    total = dt.month - 1 - months
    year = dt.year + total // 12
    month = total % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _data(resp: dict) -> dict:
    data = resp.get("data")
    if data is None:
        raise RuntimeError(f"GitHub GraphQL query failed: {resp.get('errors')!r}")
    return data


def _cost(data: dict) -> int:
    return int((data.get("rateLimit") or {}).get("cost") or 0)


# --------------------------------------------------------------------------- #
# Fetch orchestration (uses run_query seam; still no DB)                       #
# --------------------------------------------------------------------------- #

def _complete_connection(
    node: dict, connection_name: str, query: str, owner: str, name: str,
    run_query: RunQuery,
) -> int:
    """De-paginate a PR sub-connection (reviews/commits) in place. Returns the
    added rateLimit cost. Follow-up pages are appended onto the node's nodes."""
    conn = node[connection_name]
    page_info = conn.get("pageInfo") or {}
    cursor = page_info.get("endCursor")
    added_cost = 0
    while page_info.get("hasNextPage"):
        resp = run_query(
            query,
            {"owner": owner, "name": name, "number": node["number"], "cursor": cursor},
        )
        data = _data(resp)
        added_cost += _cost(data)
        page = data["repository"]["pullRequest"][connection_name]
        conn["nodes"].extend(page.get("nodes") or [])
        page_info = page.get("pageInfo") or {}
        cursor = page_info.get("endCursor")
    return added_cost


def collect_rows(
    slug: str,
    *,
    backfill_months: int | None = None,
    run_query: RunQuery,
    hash_author: Callable[[str], str] | None = None,
    now: datetime | None = None,
) -> tuple[list[PRRowset], SyncStats]:
    """Fetch MERGED PRs via `run_query` and build row dicts. Pure of Postgres.

    Incremental (backfill_months is None): stop once a page's PRs are all older
    than `now - 48h` (updatedAt). Backfill: stop at `now - backfill_months`.
    Because PRs come newest-updated-first, we stop at the first PR older than
    the cutoff (all subsequent ones are older too).
    """
    hash_author = hash_author or db.hmac_author
    now = now or datetime.now(UTC)
    cutoff = (
        now - INCREMENTAL_OVERLAP
        if backfill_months is None
        else _months_before(now, backfill_months)
    )

    owner, _, name = slug.partition("/")
    if not owner or not name:
        raise ValueError(f"repo slug must be 'owner/name', got {slug!r}")

    rowsets: list[PRRowset] = []
    cursor: str | None = None
    pages = 0
    total_cost = 0

    while True:
        resp = run_query(PR_LIST_QUERY, {"owner": owner, "name": name, "cursor": cursor})
        data = _data(resp)
        pages += 1
        total_cost += _cost(data)
        pr_conn = data["repository"]["pullRequests"]

        stop = False
        for node in pr_conn.get("nodes") or []:
            updated_at = _parse_ts(node.get("updatedAt"))
            if updated_at is not None and updated_at < cutoff:
                stop = True
                break
            total_cost += _complete_connection(
                node, "reviews", REVIEWS_PAGE_QUERY, owner, name, run_query
            )
            total_cost += _complete_connection(
                node, "commits", COMMITS_PAGE_QUERY, owner, name, run_query
            )
            rowsets.append(build_pr_rowset(node, repo=slug, hash_author=hash_author))

        if stop:
            break
        page_info = pr_conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    stats = SyncStats(
        prs_upserted=len(rowsets),
        reviews_upserted=sum(len(rs.reviews) for rs in rowsets),
        commits_upserted=sum(len(rs.commit_shas) for rs in rowsets),
        pages=pages,
        cost_points_hint=total_cost or pages,
    )
    return rowsets, stats


# --------------------------------------------------------------------------- #
# SQL layer (thin; injectable connection)                                      #
# --------------------------------------------------------------------------- #

_PR_UPSERT = """
INSERT INTO pull_requests
  (id, repo, number, author_hash, first_commit_at, ready_at, merged_at,
   additions, deletions, changed_files, is_revert, synced_at)
VALUES
  (%(id)s, %(repo)s, %(number)s, %(author_hash)s, %(first_commit_at)s,
   %(ready_at)s, %(merged_at)s, %(additions)s, %(deletions)s,
   %(changed_files)s, %(is_revert)s, now())
ON CONFLICT (id) DO UPDATE SET
  repo = EXCLUDED.repo,
  number = EXCLUDED.number,
  author_hash = EXCLUDED.author_hash,
  first_commit_at = EXCLUDED.first_commit_at,
  ready_at = EXCLUDED.ready_at,
  merged_at = EXCLUDED.merged_at,
  additions = EXCLUDED.additions,
  deletions = EXCLUDED.deletions,
  changed_files = EXCLUDED.changed_files,
  is_revert = EXCLUDED.is_revert,
  synced_at = now()
"""

_REVIEW_UPSERT = """
INSERT INTO reviews (id, pr_id, reviewer_hash, submitted_at, state)
VALUES (%(id)s, %(pr_id)s, %(reviewer_hash)s, %(submitted_at)s, %(state)s)
ON CONFLICT (id) DO UPDATE SET
  pr_id = EXCLUDED.pr_id,
  reviewer_hash = EXCLUDED.reviewer_hash,
  submitted_at = EXCLUDED.submitted_at,
  state = EXCLUDED.state
"""

_COMMIT_UPSERT = """
INSERT INTO pr_commits (pr_id, sha)
VALUES (%(pr_id)s, %(sha)s)
ON CONFLICT (pr_id, sha) DO NOTHING
"""


def write_rows(rowsets: list[PRRowset], *, conn) -> None:
    """Idempotent upsert of collected rows through `conn` (psycopg or a fake).
    PRs first so review/commit foreign keys resolve within the same txn."""
    pr_rows = [rs.pr for rs in rowsets]
    review_rows = [r for rs in rowsets for r in rs.reviews]
    commit_rows = [
        {"pr_id": rs.pr["id"], "sha": sha}
        for rs in rowsets
        for sha in rs.commit_shas
    ]
    with conn.cursor() as cur:
        if pr_rows:
            cur.executemany(_PR_UPSERT, pr_rows)
        if review_rows:
            cur.executemany(_REVIEW_UPSERT, review_rows)
        if commit_rows:
            cur.executemany(_COMMIT_UPSERT, commit_rows)


def _default_run_query(query: str, variables: dict) -> dict:
    """Default seam: a plain httpx POST to the GitHub GraphQL API."""
    token = settings().github_token
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set — cannot query GitHub GraphQL")
    resp = httpx.post(
        GITHUB_GRAPHQL_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        json={"query": query, "variables": variables},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def sync_repo(
    slug: str,
    *,
    backfill_months: int | None = None,
    run_query: RunQuery | None = None,
) -> SyncStats:
    """Sync one repo's MERGED PRs into Postgres (this module's public API).

    `run_query(query, variables) -> dict` defaults to a live httpx POST; tests
    inject a fake returning fixture payloads. Row-building never touches the DB,
    so the interesting logic is fully unit-testable offline.
    """
    rq = run_query or _default_run_query
    rowsets, stats = collect_rows(slug, backfill_months=backfill_months, run_query=rq)
    if rowsets:
        with db.pool().connection() as conn:
            write_rows(rowsets, conn=conn)
            conn.commit()
    return stats
