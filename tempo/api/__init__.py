"""Tempo serving layer: FastAPI app + OTLP/HTTP metrics receiver.

`serve.app` is the ASGI application (GET dashboard endpoints + mounted OTLP
router). `otlp` owns the `POST /v1/metrics` receiver and its pure ingest core.
The serving layer touches `metrics_team_week` and nothing else;
the OTLP receiver writes `ai_usage_daily`/`ai_stream_state` and drops all
identity attributes at the door.
"""
