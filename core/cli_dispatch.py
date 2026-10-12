#!/usr/bin/env python3
"""Unified CLI dispatch for Knowledge-Gragph-Teaching-System memory and graph operations."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from KGTS.core.graph_service import GraphService
from KGTS.core.memory_runtime import MemoryService, resolve_memory_config, save_memory_config


PROJECT_ROOT = Path(__file__).resolve().parent


def _service_graph(db_path: Optional[str] = None) -> GraphService:
    from KGTS.config import load_root_env

    load_root_env()
    return GraphService(db_path=db_path)


def _service_memory() -> MemoryService:
    return MemoryService()


def _rag_answer(query: str, graph: GraphService, memory: MemoryService, limit: int = 5) -> Dict[str, Any]:
    semantic_hits = graph.semantic_search(query, top_k=limit)
    keyword_hits = graph.search_nodes(query, limit=limit)
    memory_hits = memory.search_memory(query, k=limit)

    context_lines = []
    seen = set()
    for hit in semantic_hits:
        label = hit["metadata"].get("label") or hit["node_id"]
        if label in seen:
            continue
        seen.add(label)
        context_lines.append(f"- {label}")
    for hit in keyword_hits:
        label = hit["metadata"].get("label") or hit["id"]
        if label in seen:
            continue
        seen.add(label)
        context_lines.append(f"- {label}")

    if context_lines:
        answer = "Relevant graph context:\n" + "\n".join(context_lines[:limit])
    else:
        answer = "No relevant graph context was found."

    return {
        "status": "ready",
        "query": query,
        "answer": answer,
        "graph_hits": semantic_hits,
        "keyword_hits": keyword_hits,
        "memory_hits": memory_hits,
    }


def dispatch_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
    args = arguments or {}
    if name == "rebuild_staging_graph":
        from KGTS.maintenance.structured_sync import rebuild_staging_graph

        return rebuild_staging_graph(
            toc_export_dir=args.get("toc_export_dir"),
            skip_semantic=bool(args.get("skip_semantic", False)),
            rebuild_vector=bool(args.get("rebuild_vector", True)),
        )

    if name == "list_graphs":
        from KGTS.core.graph_registry import registry_statistics
        return registry_statistics()

    graph = _service_graph(args.get("db_path"))
    memory = _service_memory()

    if name == "read_graph":
        return graph.read_graph()
    if name == "list_nodes":
        return graph.list_nodes(
            limit=int(args.get("limit", 5000)),
            include_content=bool(args.get("include_content", False)),
            node_types=args.get("node_types"),
        )
    if name == "list_relationships":
        return graph.list_relationships_by_type(
            relation_type=args.get("relation_type"),
            limit=int(args.get("limit", 10000)),
            include_metadata=bool(args.get("include_metadata", False)),
        )
    if name == "get_node":
        return graph.get_node(args["node_id"]) or {}
    if name == "search_nodes":
        return graph.search_nodes(args.get("keyword", ""), args.get("node_type"), int(args.get("limit", 20)))
    if name == "semantic_search":
        if not args.get("db_path") and not args.get("allowed_node_ids"):
            from KGTS.core.graph_registry import cross_graph_search, graph_entries
            explicit_single_graph = bool(os.getenv("GRAPH_DB_PATH") or os.getenv("KNOWLEDGE_GRAPH_DB_PATH") or os.getenv("APP_DATA_DIR"))
            if not explicit_single_graph and len(graph_entries()) > 1 and str(os.getenv("KGTS_CROSS_GRAPH_RETRIEVAL", "1")).lower() not in {"0", "false", "no", "off"}:
                return cross_graph_search(
                    args.get("query", ""),
                    node_type=args.get("node_type"),
                    top_k=int(args.get("top_k", 10)),
                    allowed_graph_ids=args.get("allowed_graph_ids"),
                )
        return graph.semantic_search(
            args.get("query", ""),
            args.get("node_type"),
            int(args.get("top_k", 10)),
            args.get("allowed_node_ids"),
        )
    if name == "rebuild_vector_index":
        return graph.rebuild_vector_index()
    if name == "reset_vector_index":
        return graph.reset_vector_index()
    if name == "add_memory":
        created = graph.add_node(args.get("content", ""), args.get("type", "concept"), args.get("metadata"))
        memory.add_memory(
            {
                "content": created.get("content", ""),
                "metadata": created.get("metadata", {}),
                "type": created.get("type", "concept"),
            }
        )
        return created
    if name == "update_memory":
        return graph.update_node(args["node_id"], args.get("content"), args.get("metadata"))
    if name == "delete_memory":
        return graph.delete_node(args["node_id"])
    if name == "add_relation":
        return graph.add_relation(
            args["source_id"],
            args["target_id"],
            args["relation_type"],
            args.get("metadata"),
            args.get("similarity"),
        )
    if name == "get_relations":
        return graph.get_relations(args.get("node_id"), args.get("relation_type"))
    if name == "get_relation":
        return graph.get_relation(args["relation_id"]) or {}
    if name == "get_neighbors":
        return graph.get_neighbors(args["node_id"], args.get("direction", "both"))
    if name == "get_graph_schema":
        graph_data = graph.read_graph()
        return {
            "stats": graph_data.get("stats", {}),
            "vector_stats": graph_data.get("vector_stats", {}),
            "node_types": list(graph_data.get("stats", {}).get("node_types", {}).keys()),
            "relation_types": sorted({relation["relation_type"] for relation in graph_data.get("relations", [])}),
        }
    if name == "batch_import_graph":
        return graph.batch_import_graph(args.get("nodes", []), args.get("relations", []))
    if name == "update_relation":
        return graph.update_relation(
            args["relation_id"],
            args.get("source_id"),
            args.get("target_id"),
            args.get("relation_type"),
            args.get("metadata"),
            args.get("similarity"),
        )
    if name == "delete_relation":
        return graph.delete_relation(args["relation_id"])
    if name == "delete_by_sources":
        return graph.delete_by_sources(args.get("sources", []))
    if name == "get_graph_statistics":
        return graph.get_graph_statistics()
    if name == "get_subgraph_by_type":
        return graph.get_subgraph_by_type(args["node_type"])
    if name == "get_k_hop_neighbors":
        return graph.get_k_hop_neighbors(args["node_id"], int(args.get("k", 2)))
    if name == "get_prerequisites":
        return graph.get_prerequisites(args["node_id"], int(args.get("max_depth", 3)))
    if name == "get_follow_up":
        return graph.get_follow_up(args["node_id"], int(args.get("max_depth", 3)))
    if name == "get_note":
        return graph.get_note(args.get("node_id"))
    if name == "trace_call_path":
        return graph.get_follow_up(args["start_node_id"], int(args.get("max_depth", 5)))
    if name == "discover_weak_relations":
        node = graph.get_node(args["node_id"])
        query = (node or {}).get("content") or args["node_id"]
        return graph.semantic_search(query, top_k=10)

    return {"status": "error", "error": f"Unknown tool: {name}"}


def _print_json(payload: Any) -> int:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
    if isinstance(payload, dict):
        return 0 if payload.get("status") != "error" and not payload.get("error") else 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("Usage: python -m KGTS.core.cli_dispatch <tool_name> [json_arguments]")
        return 0 if args else 1

    tool_name = args[0]
    raw_arguments = args[1] if len(args) > 1 else "{}"
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return _print_json({"status": "error", "error": f"Invalid JSON arguments: {exc}"})
    if not isinstance(parsed, dict):
        return _print_json({"status": "error", "error": "json_arguments must be an object"})
    return _print_json(dispatch_tool(tool_name, parsed))


if __name__ == "__main__":
    raise SystemExit(main())
