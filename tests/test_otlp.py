"""OTLP receiver tests. Pure-core ingest via injectable sink/state
dicts and hand-crafted fixtures; route-level auth/malformed/response-shape via
TestClient with the DB store monkeypatched away. No Postgres, no network."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

from tempo.api import otlp
from tempo.api.serve import app

FIXTURES = Path(__file__).parent / "fixtures" / "otlp"
UTC = timezone.utc

# All fixtures stamp their datapoints with this instant; the sink key is
# (UTC date, org_unit, repo). Derive the date the same way the receiver does.
NANO = "1751803200000000000"
DAY = datetime.fromtimestamp(int(NANO) / 1e9, tz=UTC).date()
KEY = (DAY, "core", "example/repo")


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- pure ingest core -------------------------------------------------------


def test_delta_ingest_maps_tokens_and_excludes_cache():
    sink: dict = {}
    state: dict = {}
    stats = otlp.ingest_payload(load("delta.json"), sink=sink, state=state)

    # input->tokens_in, output->tokens_out; cache* excluded from token counts;
    # cost read straight from cost.usage; session.count -> sessions.
    assert sink[KEY] == {
        "tokens_in": 100,
        "tokens_out": 40,
        "cost_usd": 1.25,
        "sessions": 2,
    }
    assert stats.points_consumed == 4  # input, output, cost, session
    assert stats.points_ignored == 2  # cacheRead, cacheCreation
    # DELTA records per-stream timeUnixNano dedup cursors (review finding #1)
    assert all(k.startswith(otlp._DELTA_NS) for k in state)
    assert stats.touched_streams == set(state)


def test_retried_delta_payload_does_not_double_count():
    """OTLP/HTTP delivers at-least-once; a resent batch must be a no-op
    (adversarial-review finding #1)."""
    sink: dict = {}
    state: dict = {}
    otlp.ingest_payload(load("delta.json"), sink=sink, state=state)
    first = {k: dict(v) for k, v in sink.items()}
    stats = otlp.ingest_payload(load("delta.json"), sink=sink, state=state)
    assert sink == first  # nothing added
    assert stats.points_consumed == 0
    assert stats.points_deduped == 4


def test_stream_key_is_salted_hmac_not_recomputable_hash():
    """A bare sha256 over low-entropy attrs is a presence oracle
    (adversarial-review finding #2) - the key must be HMAC(salt, ...)."""
    import hashlib

    key = otlp._stream_key("claude_code.token.usage", {"user.email": "a@b.c"}, "r", "o")
    bare = hashlib.sha256(
        "|".join(["claude_code.token.usage", "user.email=a@b.c", "repo=r", "org_unit=o"]).encode()
    ).hexdigest()
    assert key != bare


def test_malformed_and_hostile_datapoints_rejected_not_fatal():
    """One bad datapoint never sinks the batch (findings #3/#4)."""
    payload = {
        "resourceMetrics": [
            {
                "resource": {"attributes": ["garbage", {"no-key": True}]},
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": "claude_code.cost.usage",
                                "sum": {
                                    "aggregationTemporality": 1,
                                    "dataPoints": [
                                        "not-an-object",
                                        {"asDouble": -500000},
                                        {"asDouble": 1e9},
                                        {"asDouble": 3.5, "timeUnixNano": "1755691200000000000"},
                                    ],
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }
    sink: dict = {}
    state: dict = {}
    stats = otlp.ingest_payload(payload, sink=sink, state=state)
    assert stats.points_rejected == 3  # non-object, negative, out-of-range
    assert stats.points_consumed == 1  # the good datapoint still lands
    assert sum(acc["cost_usd"] for acc in sink.values()) == 3.5


def test_cumulative_deltification_and_counter_reset():
    sink: dict = {}
    state: dict = {}

    otlp.ingest_payload(load("cumulative_1.json"), sink=sink, state=state)
    assert sink[KEY]["tokens_in"] == 100  # first sighting = full cumulative value
    assert sink[KEY]["cost_usd"] == 2.0
    assert len(state) == 2

    stats = otlp.ingest_payload(load("cumulative_2.json"), sink=sink, state=state)
    # token input 100 -> 250 : rising counter, delta 150 ; total 250
    assert sink[KEY]["tokens_in"] == 250
    # cost 2.0 -> 1.0 : decrease = counter reset, add raw 1.0 ; total 3.0
    assert sink[KEY]["cost_usd"] == 3.0
    assert stats.resets == 1
    assert len(state) == 2  # same two streams, keyed by (metric, attrs)


def test_identity_attributes_dropped_and_absent_from_sink_and_state():
    sink: dict = {}
    state: dict = {}
    otlp.ingest_payload(load("identity.json"), sink=sink, state=state)

    assert sink[KEY]["tokens_in"] == 10  # the value is still counted
    # str() over both dicts flattens tuple keys and hashed stream keys alike;
    # any identity leak (into sink keys/values OR state) would surface here.
    blob = str(sink) + str(state)
    for secret in (
        "u-SECRET-9",
        "secret@example.com",
        "acct-SECRET-5",
        "sess-SECRET-7",
        "org-SECRET-3",
    ):
        assert secret not in blob  # identity never lands anywhere observable


def test_unattributed_repo_when_no_resource_tag():
    payload = {
        "resourceMetrics": [
            {
                "resource": {"attributes": []},
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": "claude_code.session.count",
                                "sum": {
                                    "aggregationTemporality": 1,
                                    "dataPoints": [
                                        {"timeUnixNano": NANO, "asInt": "1"}
                                    ],
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }
    sink: dict = {}
    otlp.ingest_payload(payload, sink=sink, state={})
    # repo falls back to 'unattributed', org_unit to settings default.
    keys_ = list(sink)
    assert len(keys_) == 1
    assert keys_[0][2] == "unattributed"


# --- route level (TestClient; store monkeypatched, no DB) -------------------


def test_route_returns_partial_success(monkeypatch):
    calls = []
    monkeypatch.setattr(
        otlp, "store_payload", lambda p: calls.append(p) or otlp.IngestStats()
    )
    client = TestClient(app)
    r = client.post("/v1/metrics", json=load("delta.json"))
    assert r.status_code == 200
    assert r.json() == {"partialSuccess": {}}
    assert len(calls) == 1


def test_route_malformed_json_400():
    client = TestClient(app)
    r = client.post("/v1/metrics", content=b"{not valid json")
    assert r.status_code == 400


def test_route_bearer_token_enforced_when_configured(monkeypatch):
    monkeypatch.setattr(
        otlp, "settings", lambda: SimpleNamespace(otlp_token="s3cret", org_unit="default")
    )
    monkeypatch.setattr(otlp, "store_payload", lambda p: otlp.IngestStats())
    client = TestClient(app)
    body = {"resourceMetrics": []}

    assert client.post("/v1/metrics", json=body).status_code == 401
    assert (
        client.post(
            "/v1/metrics", json=body, headers={"Authorization": "Bearer nope"}
        ).status_code
        == 401
    )
    ok = client.post(
        "/v1/metrics", json=body, headers={"Authorization": "Bearer s3cret"}
    )
    assert ok.status_code == 200
    assert ok.json() == {"partialSuccess": {}}


def test_route_open_when_no_token(monkeypatch):
    monkeypatch.setattr(
        otlp, "settings", lambda: SimpleNamespace(otlp_token=None, org_unit="default")
    )
    monkeypatch.setattr(otlp, "store_payload", lambda p: otlp.IngestStats())
    client = TestClient(app)
    r = client.post("/v1/metrics", json={"resourceMetrics": []})
    assert r.status_code == 200
