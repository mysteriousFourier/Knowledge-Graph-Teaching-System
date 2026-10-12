from __future__ import annotations

import json
from pathlib import Path

from KGTS.core.graph_registry import cross_graph_search
from KGTS.core.graph_service import GraphService


def test_cross_graph_search_keeps_independent_graph_identity(tmp_path, monkeypatch):
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    GraphService(first).add_node("Hardy Weinberg allele frequencies", "concept", {"id": "same", "label": "HWE"})
    GraphService(second).add_node("Hardy Weinberg genotype frequencies", "concept", {"id": "same", "label": "HWE"})
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema": "kgts.graph_registry.v1", "graphs": [
        {"graph_id": "first", "book": "First", "db_path": str(first), "enabled": True},
        {"graph_id": "second", "book": "Second", "db_path": str(second), "enabled": True},
    ]}), encoding="utf-8")
    monkeypatch.setenv("KGTS_GRAPH_REGISTRY", str(registry))
    hits = cross_graph_search("Hardy Weinberg", top_k=10)
    assert {hit["graph_id"] for hit in hits} == {"first", "second"}
    assert {hit["node_id"] for hit in hits} == {"first::same", "second::same"}
