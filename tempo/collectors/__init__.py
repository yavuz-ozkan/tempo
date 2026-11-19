"""Collectors package: the GitHub GraphQL collector (github.py).

Re-exported here so callers can `from tempo.collectors import sync_repo`.
"""

from __future__ import annotations

from .github import SyncStats, sync_repo

__all__ = ["SyncStats", "sync_repo"]
