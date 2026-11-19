import os

# CI machines have no .env; every ingest path requires a salt (fail-closed).
os.environ.setdefault("TEMPO_AUTHOR_SALT", "test-salt-not-secret")
