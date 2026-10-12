"""Independent graph registry and cross-graph retrieval."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from KGTS.core.graph_service import GraphService


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = ROOT_DIR / "data" / "seed" / "graph_registry.json"


def registry_path() -> Path:
    return Path(os.getenv("KGTS_GRAPH_REGISTRY", str(DEFAULT_REGISTRY_PATH)))


def load_registry() -> Dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return {"schema": "kgts.graph_registry.v1", "graphs": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": "kgts.graph_registry.v1", "graphs": []}
    return payload if isinstance(payload, dict) else {"schema": "kgts.graph_registry.v1", "graphs": []}


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT_DIR / path


def graph_entries(*, enabled_only: bool = True) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for raw in load_registry().get("graphs") or []:
        if not isinstance(raw, dict):
            continue
        if enabled_only and raw.get("enabled", True) is False:
            continue
        item = dict(raw)
        item["graph_id"] = str(item.get("graph_id") or item.get("id") or "").strip()
        item["db_path"] = str(_resolve_path(str(item.get("db_path") or "")))
        if item["graph_id"] and item["db_path"]:
            entries.append(item)
    return entries


def cross_graph_search(
    query: str,
    *,
    top_k: int = 10,
    node_type: Optional[str] = None,
    allowed_graph_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Search every registered graph independently, then merge ranked hits."""
    limit = max(1, int(top_k or 10))
    allowed = {str(value).strip() for value in (allowed_graph_ids or []) if str(value).strip()}
    hits: List[Dict[str, Any]] = []
    for entry in graph_entries():
        graph_id = entry["graph_id"]
        if allowed and graph_id not in allowed:
            continue
        db_path = Path(entry["db_path"])
        if not db_path.exists():
            continue
        try:
            # Each independent DB owns its own sparse index. Never point a
            # registered book at the default graph's FAISS index.
            from unittest.mock import patch
            with patch.dict(os.environ, {"KGTS_RETRIEVAL_MODE": "sparse_hybrid"}, clear=False):
                graph_hits = GraphService(db_path=db_path).semantic_search(query, node_type=node_type, top_k=limit)
        except Exception as exc:
            continue
        for hit in graph_hits:
            raw_node_id = str(hit.get("node_id") or "")
            metadata = dict(hit.get("metadata") or {})
            metadata.update(
                {
                    "graph_id": graph_id,
                    "book": entry.get("book") or graph_id,
                    "graph_db_path": str(db_path),
                    "source_pack": entry.get("source_pack"),
                    "original_node_id": raw_node_id,
                }
            )
            item = dict(hit)
            item["node_id"] = f"{graph_id}::{raw_node_id}"
            item["metadata"] = metadata
            item["graph_id"] = graph_id
            item["book"] = metadata["book"]
            hits.append(item)
    hits.sort(key=lambda item: float(item.get("similarity") or item.get("hybrid_score") or 0.0), reverse=True)
    return hits[:limit]


def registry_statistics() -> Dict[str, Any]:
    result: List[Dict[str, Any]] = []
    for entry in graph_entries():
        db_path = Path(entry["db_path"])
        item = {"graph_id": entry["graph_id"], "book": entry.get("book"), "db_path": str(db_path), "exists": db_path.exists()}
        if db_path.exists():
            try:
                item["stats"] = GraphService(db_path=db_path).get_graph_statistics()
            except Exception as exc:
                item["error"] = str(exc)
        result.append(item)
    return {"schema": load_registry().get("schema", "kgts.graph_registry.v1"), "graphs": result}
