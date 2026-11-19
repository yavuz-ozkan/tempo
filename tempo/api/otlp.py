"""OTLP/HTTP JSON metrics receiver for Claude Code telemetry.

Claude Code exports OpenTelemetry metrics; this endpoint accepts the OTLP/HTTP
**JSON** encoding at ``POST /v1/metrics`` and rolls the three metrics Tempo
consumes into ``ai_usage_daily``. Everything else in the payload is ignored.

Client contract (verified 2026-07-07 against code.claude.com/docs/en/monitoring-usage):
  CLAUDE_CODE_ENABLE_TELEMETRY=1
  OTEL_METRICS_EXPORTER=otlp
  OTEL_EXPORTER_OTLP_PROTOCOL=http/json
  OTEL_EXPORTER_OTLP_ENDPOINT=http://<tailnet-host>:8010
  OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta   # this is also the client default
  OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <token>   # optional, matches TEMPO_OTLP_TOKEN
  OTEL_RESOURCE_ATTRIBUTES=repo=<slug>,team.id=<org_unit>   # repo/org attribution

Frozen token mapping: on ``claude_code.token.usage`` the
datapoint ``type`` attribute selects the sink field --- ``input`` -> tokens_in,
``output`` -> tokens_out; ``cacheRead``/``cacheCreation`` are excluded from the
token *counts* but are naturally included in cost (cost arrives pre-computed in
``claude_code.cost.usage``, USD, which already prices cache tokens).

Privacy invariant: identity attributes --- user.id, user.email,
user.account_uuid, user.account_id, session.id, organization.id --- are dropped
at the receiver and never logged. We achieve this by *never reading* them into
the sink: only token/cost/session values and the ``repo``/``team.id`` resource
attributes are consumed. Nothing in this module logs a payload. Per-datapoint
attributes are folded into a keyed one-way ``stream_key`` — **HMAC-SHA256 with
TEMPO_AUTHOR_SALT** (adversarial-review finding: a bare hash over low-entropy
attrs is a recomputable presence oracle; the keyed hash is not). Stream keys
exist so per-session cumulative counters deltify independently and so retried
DELTA exports dedup; ingest refuses to run when the salt is unset.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ..config import settings

UTC = timezone.utc

# The only three metrics Tempo consumes.
TOKEN_USAGE = "claude_code.token.usage"
COST_USAGE = "claude_code.cost.usage"
SESSION_COUNT = "claude_code.session.count"
_CONSUMED = frozenset({TOKEN_USAGE, COST_USAGE, SESSION_COUNT})

# OTLP AggregationTemporality enum (protobuf JSON encodes as int OR string name).
_CUMULATIVE = {2, "AGGREGATION_TEMPORALITY_CUMULATIVE"}


@dataclass
class IngestStats:
    """What one payload did. Returned to the route (and asserted in tests);
    carries no identity --- only counts and the opaque stream keys touched."""

    points_consumed: int = 0
    points_ignored: int = 0
    points_rejected: int = 0   # malformed / out-of-range / negative datapoints
    points_deduped: int = 0    # retried DELTA exports (at-least-once delivery)
    resets: int = 0
    touched_streams: set[str] = field(default_factory=set)


# --- OTLP JSON value/attribute decoding -------------------------------------


def _any_value(v: dict) -> object:
    """Decode an OTLP AnyValue. int64/uint64 are string-encoded in OTLP JSON."""
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:
        return int(v["intValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "boolValue" in v:
        return bool(v["boolValue"])
    return None


def _attrs_dict(attributes: list) -> dict[str, object]:
    out: dict[str, object] = {}
    for a in attributes or []:
        if isinstance(a, dict) and "key" in a:  # skip malformed entries, don't 500
            out[a["key"]] = _any_value(a.get("value", {}) or {})
    return out


def _dp_value(dp: dict) -> float:
    if "asInt" in dp:
        return int(dp["asInt"])
    if "asDouble" in dp:
        return float(dp["asDouble"])
    return 0


def _dp_day(dp: dict) -> date:
    nano = dp.get("timeUnixNano") or dp.get("startTimeUnixNano")
    if nano is None:
        return datetime.now(UTC).date()
    return datetime.fromtimestamp(int(nano) / 1e9, tz=UTC).date()


def _is_cumulative(sum_obj: dict) -> bool:
    return sum_obj.get("aggregationTemporality") in _CUMULATIVE


def _stream_key(name: str, dp_attrs: dict, repo: str, org_unit: str) -> str:
    """HMAC-SHA256 (keyed with TEMPO_AUTHOR_SALT) over metric name + sorted
    datapoint attrs + resource repo/org_unit. Including every datapoint
    attribute keeps distinct streams (per model/type/session) from colliding
    during cumulative->delta conversion. The keyed hash cannot be recomputed
    without the salt, so low-entropy identity attrs are not a presence oracle
    (adversarial-review finding #2)."""
    salt = settings().author_salt
    if not salt:
        raise RuntimeError("TEMPO_AUTHOR_SALT is not set — refusing to ingest telemetry")
    parts = [name]
    parts += [f"{k}={dp_attrs[k]}" for k in sorted(dp_attrs)]
    parts += [f"repo={repo}", f"org_unit={org_unit}"]
    return hmac.new(salt.encode(), "|".join(parts).encode(), hashlib.sha256).hexdigest()


# Delta streams store their dedup cursor (last timeUnixNano) in the same state
# mapping under a distinct namespace, so state stays a flat str->float dict.
_DELTA_NS = "delta-ts:"

# ai_usage_daily columns are NUMERIC(10,2)/BIGINT; a datapoint that could push a
# daily accumulator past this is garbage, not telemetry (finding #3).
_MAX_ABS_VALUE = 1e8


def _sink_field(name: str, dp_attrs: dict) -> str | None:
    """Which ai_usage_daily column this datapoint feeds, or None to ignore it."""
    if name == TOKEN_USAGE:
        t = dp_attrs.get("type")
        if t == "input":
            return "tokens_in"
        if t == "output":
            return "tokens_out"
        return None  # cacheRead / cacheCreation: excluded from token counts
    if name == COST_USAGE:
        return "cost_usd"
    if name == SESSION_COUNT:
        return "sessions"
    return None


def _empty_acc() -> dict[str, float]:
    return {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "sessions": 0}


# --- pure ingest core (injectable sink/state dicts, no DB, no network) -------


def ingest_payload(payload: dict, *, sink: dict, state: dict) -> IngestStats:
    """Fold one OTLP/HTTP JSON payload into ``sink`` (keyed by
    ``(day, org_unit, repo)`` -> per-column deltas) using ``state``
    (stream_key -> last cumulative value) to deltify CUMULATIVE streams.

    DELTA datapoints add their value in, deduped per stream by their
    ``timeUnixNano`` cursor — OTLP/HTTP exporters deliver at-least-once, so a
    retried batch must not double-count (adversarial-review finding #1); a
    datapoint at or before the stream's stored cursor is skipped. CUMULATIVE
    datapoints add the difference from the last seen value for that stream; a
    *negative* difference is a counter reset (new process / new start time) and
    the raw value is added instead. The first sighting of a stream adds its
    full value (cumulative counters start at zero). Persisting ``state`` across
    restarts is what makes both paths safe.

    Malformed, negative, or absurdly large datapoints are rejected individually
    (``points_rejected``) — one bad datapoint never sinks the batch (finding #3/#4).
    """
    stats = IngestStats()
    default_org = settings().org_unit
    for rm in payload.get("resourceMetrics", []) or []:
        if not isinstance(rm, dict):
            stats.points_rejected += 1
            continue
        res_attrs = _attrs_dict((rm.get("resource") or {}).get("attributes", []))
        repo = res_attrs.get("repo") or "unattributed"
        org_unit = res_attrs.get("team.id") or default_org
        for sm in rm.get("scopeMetrics", []) or []:
            if not isinstance(sm, dict):
                stats.points_rejected += 1
                continue
            for metric in sm.get("metrics", []) or []:
                if not isinstance(metric, dict):
                    stats.points_rejected += 1
                    continue
                name = metric.get("name")
                if name not in _CONSUMED:
                    continue
                sum_obj = metric.get("sum")
                if not isinstance(sum_obj, dict):
                    continue  # counters only; any gauge/histogram form is ignored
                cumulative = _is_cumulative(sum_obj)
                datapoints = sum_obj.get("dataPoints") or []
                if not isinstance(datapoints, list):
                    stats.points_rejected += 1
                    continue
                for dp in datapoints:
                    try:
                        if not isinstance(dp, dict):
                            raise TypeError("datapoint is not an object")
                        dp_attrs = _attrs_dict(dp.get("attributes", []))
                        col = _sink_field(name, dp_attrs)
                        if col is None:
                            stats.points_ignored += 1
                            continue
                        raw = _dp_value(dp)
                        if raw < 0 or abs(raw) >= _MAX_ABS_VALUE:
                            # monotonic counters are never negative; huge values
                            # would overflow NUMERIC(10,2) and poison weekly sums
                            stats.points_rejected += 1
                            continue
                        if cumulative:
                            skey = _stream_key(name, dp_attrs, repo, org_unit)
                            prev = state.get(skey)
                            if prev is None:
                                delta = raw
                            elif raw >= prev:
                                delta = raw - prev
                            else:
                                delta = raw  # counter reset
                                stats.resets += 1
                            state[skey] = raw
                            stats.touched_streams.add(skey)
                        else:
                            delta = raw
                            ts = int(dp.get("timeUnixNano") or 0)
                            if ts:
                                dkey = _DELTA_NS + _stream_key(name, dp_attrs, repo, org_unit)
                                if ts <= state.get(dkey, 0):
                                    stats.points_deduped += 1
                                    continue
                                state[dkey] = float(ts)
                                stats.touched_streams.add(dkey)
                        acc = sink.setdefault((_dp_day(dp), org_unit, repo), _empty_acc())
                        acc[col] += delta
                        stats.points_consumed += 1
                    except RuntimeError:
                        raise  # unset salt is a config error, not a bad datapoint
                    except Exception:
                        stats.points_rejected += 1
    return stats


# --- DB wrapper (the route's default store; monkeypatched away in unit tests) -


def store_payload(payload: dict) -> IngestStats:
    """Load stream state from Postgres, run the pure core, and upsert-add the
    resulting daily deltas plus the advanced stream cursors. Sync (psycopg
    ConnectionPool); the route runs it in a threadpool so it never blocks the
    event loop. Idempotent for retried exports on BOTH temporalities: cumulative
    via the last-value cursor, delta via the per-stream timeUnixNano cursor."""
    from .. import db

    sink: dict = {}
    state: dict = {}
    with db.pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT stream_key, last_value FROM ai_stream_state")
            for key, val in cur.fetchall():
                state[key] = float(val)
        stats = ingest_payload(payload, sink=sink, state=state)
        with conn.cursor() as cur:
            for (day, org_unit, repo), acc in sink.items():
                cur.execute(
                    """
                    INSERT INTO ai_usage_daily
                        (day, org_unit, repo, tokens_in, tokens_out, cost_usd, sessions)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (day, org_unit, repo) DO UPDATE SET
                        tokens_in  = ai_usage_daily.tokens_in  + EXCLUDED.tokens_in,
                        tokens_out = ai_usage_daily.tokens_out + EXCLUDED.tokens_out,
                        cost_usd   = ai_usage_daily.cost_usd   + EXCLUDED.cost_usd,
                        sessions   = ai_usage_daily.sessions   + EXCLUDED.sessions
                    """,
                    (
                        day,
                        org_unit,
                        repo,
                        int(acc["tokens_in"]),
                        int(acc["tokens_out"]),
                        acc["cost_usd"],
                        int(acc["sessions"]),
                    ),
                )
            now_ns = int(time.time() * 1e9)
            for key in stats.touched_streams:
                cur.execute(
                    """
                    INSERT INTO ai_stream_state (stream_key, last_value, last_time_unix_nano)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (stream_key) DO UPDATE SET
                        last_value = EXCLUDED.last_value,
                        last_time_unix_nano = EXCLUDED.last_time_unix_nano,
                        updated_at = now()
                    """,
                    (key, state[key], now_ns),
                )
    return stats


# --- route -------------------------------------------------------------------

router = APIRouter()


def _check_auth(request: Request) -> None:
    """Optional shared bearer token: required only when
    TEMPO_OTLP_TOKEN is configured, otherwise the endpoint is open (it lives on
    the tailnet/VPN, never public)."""
    token = settings().otlp_token
    if not token:
        return
    if request.headers.get("authorization", "") != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


@router.post("/v1/metrics")
async def receive_metrics(request: Request) -> dict:
    _check_auth(request)
    try:
        payload = json.loads(await request.body())
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="malformed OTLP JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    try:
        stats = await run_in_threadpool(store_payload, payload)
    except RuntimeError as exc:  # unset salt — server misconfigured, not client
        raise HTTPException(status_code=503, detail=str(exc))
    # OTLP/HTTP success response shape (empty partialSuccess = fully accepted;
    # rejected datapoints reported per the OTLP partial-success contract).
    if stats.points_rejected:
        return {
            "partialSuccess": {
                "rejectedDataPoints": stats.points_rejected,
                "errorMessage": "malformed, negative, or out-of-range datapoints were skipped",
            }
        }
    return {"partialSuccess": {}}
