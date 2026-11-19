"""Nightly batch jobs: the rework blame pass (jobs/rework.py) and the
metrics_team_week rollup (jobs/rollup.py). Both are idempotent, re-runnable,
and driven by the CLI. Metric arithmetic lives in metrics/formulas.py,
never here."""
