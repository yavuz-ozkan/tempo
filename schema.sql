-- Tempo schema (FROZEN — the base tables plus indexes and
-- one additive table). Migration strategy: drop/recreate via `tempo init-db --drop`.
--
-- Privacy invariants enforced HERE, not in prose:
--   * author identity only ever exists as HMAC hashes (pseudonymized at ingest)
--   * no titles, bodies, diff text, comments — the columns don't exist
--   * metrics_team_week is THE ONLY table the API serves

CREATE TABLE IF NOT EXISTS pull_requests (
  id              BIGINT PRIMARY KEY,          -- GitHub PR database id
  repo            TEXT NOT NULL,
  number          INT NOT NULL,
  author_hash     TEXT NOT NULL,               -- HMAC; raw login never stored
  first_commit_at TIMESTAMPTZ,                 -- earliest commit authored-date on the branch
  ready_at        TIMESTAMPTZ,                 -- created_at, or ready_for_review event if drafted
  merged_at       TIMESTAMPTZ,
  additions INT, deletions INT, changed_files INT,
  is_revert       BOOLEAN NOT NULL DEFAULT false,
  synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (repo, number)
);
CREATE INDEX IF NOT EXISTS idx_pr_repo_merged ON pull_requests (repo, merged_at);

CREATE TABLE IF NOT EXISTS reviews (
  id BIGINT PRIMARY KEY,
  pr_id BIGINT REFERENCES pull_requests(id),
  reviewer_hash TEXT NOT NULL,
  submitted_at TIMESTAMPTZ NOT NULL,
  state TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_pr ON reviews (pr_id);

CREATE TABLE IF NOT EXISTS issues (                -- Jira (planned — table exists, stays empty for now)
  key TEXT PRIMARY KEY, issue_type TEXT, project TEXT,
  work_started_at TIMESTAMPTZ, done_at TIMESTAMPTZ,
  status_raw TEXT, synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS deploy_events (         -- optional v1; empty is fine, DORA needs it eventually
  id BIGSERIAL PRIMARY KEY, repo TEXT, deployed_at TIMESTAMPTZ, source TEXT
);

CREATE TABLE IF NOT EXISTS ai_usage_daily (        -- OTLP rollup; deliberately coarse
  day DATE NOT NULL, org_unit TEXT NOT NULL, repo TEXT NOT NULL,  -- repo 'unattributed' when untagged
  tokens_in BIGINT NOT NULL DEFAULT 0, tokens_out BIGINT NOT NULL DEFAULT 0,
  cost_usd NUMERIC(10,2) NOT NULL DEFAULT 0, sessions INT NOT NULL DEFAULT 0,
  PRIMARY KEY (day, org_unit, repo)
);

CREATE TABLE IF NOT EXISTS ai_stream_state (       -- additive: last-value cache so
  stream_key TEXT PRIMARY KEY,                     -- CUMULATIVE-temporality OTLP counters can be deltified
  last_value NUMERIC NOT NULL,
  last_time_unix_nano BIGINT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pr_commits (            -- PR's commit SHAs; rework blame matches against these
  pr_id BIGINT REFERENCES pull_requests(id),
  sha TEXT NOT NULL,
  PRIMARY KEY (pr_id, sha)
);

CREATE TABLE IF NOT EXISTS rework (                -- nightly blame job output
  pr_id BIGINT PRIMARY KEY REFERENCES pull_requests(id),
  lines_added INT NOT NULL,
  lines_reworked_21d INT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL,
  sampled BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS metrics_team_week (     -- THE ONLY TABLE THE API SERVES
  week DATE NOT NULL, repo TEXT NOT NULL, metric TEXT NOT NULL,
  value NUMERIC,                                   -- NULL = masked by the n>=3 floor
  n INT,                                           -- distinct contributor count backing the slice
  PRIMARY KEY (week, repo, metric)
);
