from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from KGTS.core import context_cache
from KGTS.core import graph_context


def _configure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KGTS_CONTEXT_CACHE_ENABLED", "1")
    monkeypatch.setenv("KGTS_CONTEXT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("KGTS_CONTEXT_CACHE_MAX_ENTRIES", "2")
    monkeypatch.setenv("KGTS_CONTEXT_CACHE_TTL_SECONDS", "3600")
    monkeypatch.setenv("KGTS_CONTEXT_CACHE_MAX_BYTES", "100000")
    context_cache.clear()


def test_context_cache_round_trip_and_bounded_entries(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    first = context_cache.make_key({"query": "matrix", "revision": "a"})
    second = context_cache.make_key({"query": "eigenvalue", "revision": "a"})
    third = context_cache.make_key({"query": "fitness", "revision": "a"})
    assert context_cache.set(first, {"context": "one"})
    assert context_cache.get(first) == {"context": "one"}
    context_cache.set(second, {"context": "two"})
    # Touch the first entry after the second is inserted so LRU behavior is
    # observable even when all writes happen within the same millisecond.
    assert context_cache.get(first) == {"context": "one"}
    context_cache.set(third, {"context": "three"})
    assert context_cache.get(first) == {"context": "one"}
    assert context_cache.get(second) is None
    assert context_cache.get(third) == {"context": "three"}


def test_corrupt_entry_is_removed_and_misses(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    key = context_cache.make_key({"query": "corrupt"})
    context_cache.set(key, {"ok": True})
    with context_cache._connect() as conn:
        conn.execute("UPDATE context_cache SET payload_json = ? WHERE cache_key = ?", ("{bad", key))
    assert context_cache.get(key) is None
    assert context_cache.get(key) is None


def test_graph_revision_changes_when_database_changes(monkeypatch, tmp_path):
    db = tmp_path / "knowledge_graph.db"
    monkeypatch.setenv("GRAPH_DB_PATH", str(db))
    db.write_bytes(b"before")
    before = context_cache.graph_revision()
    db.write_bytes(b"after with a different size")
    assert context_cache.graph_revision() != before


def test_graphrag_context_reuses_identical_assembly(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    payload = {"retrieval_stats": {"mode": "sparse_hybrid"}, "context": "cached"}
    with patch.object(graph_context, "_build_graphrag_context_uncached", return_value=dict(payload)) as build:
        first = graph_context.build_graphrag_context("matrix", limit=2)
        second = graph_context.build_graphrag_context(" matrix ", limit=2)
    assert build.call_count == 1
    assert first["context_cache_hit"] is False
    assert second["context_cache_hit"] is True


def test_global_and_explicit_empty_scope_use_different_keys(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    with patch.object(graph_context, "_build_graphrag_context_uncached", side_effect=[
        {"scope": "global"},
        {"scope": "empty"},
    ]) as build:
        global_result = graph_context.build_graphrag_context("matrix")
        empty_result = graph_context.build_graphrag_context("matrix", allowed_node_ids=[])
    assert build.call_count == 2
    assert global_result["scope"] == "global"
    assert empty_result["scope"] == "empty"
