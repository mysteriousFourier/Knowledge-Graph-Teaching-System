"""GraphRAG context helpers for graph-backed lesson preparation."""

from __future__ import annotations

from collections import deque
import os
from typing import Any, Dict, Iterable, List, Optional, Set

from KGTS.core.bridge import build_frontend_graph, get_graph_schema, search_nodes, semantic_search
from KGTS.core import context_cache
from KGTS.education.kg_constraints import formula_context_for_text, graph_paths_for_evidence


STRUCTURAL_TYPES = {"contains"}
REFERENCE_TYPES = {"references_formula", "references_table", "references_figure", "references_example"}
EXPANSION_TYPES = REFERENCE_TYPES.union(
    {
        "defines",
        "explains",
        "derives",
        "depends_on",
        "supports",
        "example_of",
        "applies_to",
        "precedes",
        "related",
    }
)
CONTENT_TYPES = {
    "concept",
    "discussion",
    "proposition",
    "derivation",
    "example",
    "formula",
    "theorem",
    "table",
    "figure",
    "note",
}


def build_node_context(node_id: str, *, max_nodes: int = 260) -> Dict[str, Any]:
    return build_node_contexts([node_id], max_nodes=max_nodes)


def build_graphrag_context(
    query: str,
    *,
    seed_node_ids: Optional[Iterable[str]] = None,
    allowed_node_ids: Optional[Iterable[str]] = None,
    limit: int = 6,
    max_nodes: int = 260,
    expansion_limit: int = 24,
) -> Dict[str, Any]:
    """Build or reuse a deterministic GraphRAG context payload."""
    seeds = _normalize_node_ids(seed_node_ids or [])
    allowed = _normalize_node_ids(allowed_node_ids or [])
    identity = {
        "kind": "graphrag",
        "graph_revision": context_cache.graph_revision(),
        "query": " ".join(str(query or "").split()).casefold(),
        "seed_node_ids": seeds,
        # None means global scope; [] is an explicit empty scope and must not
        # share a cache entry with the global query.
        "allowed_node_ids": None if allowed_node_ids is None else sorted(allowed),
        "limit": int(limit or 6),
        "max_nodes": int(max_nodes or 260),
        "expansion_limit": int(expansion_limit or 24),
        "retrieval_mode": os.getenv("KGTS_RETRIEVAL_MODE", "hybrid").strip().lower(),
        "context_chars": os.getenv("KGTS_RAG_CONTEXT_CHARS", "12000"),
    }
    cache_key = context_cache.make_key(identity)
    cached = context_cache.get(cache_key)
    if cached is not None:
        cached["context_cache_hit"] = True
        stats = cached.get("retrieval_stats")
        if isinstance(stats, dict):
            cached["retrieval_stats"] = {**stats, "context_cache_hit": True}
        return cached
    result = _build_graphrag_context_uncached(
        query,
        seed_node_ids=seeds,
        allowed_node_ids=allowed if allowed_node_ids is not None else None,
        limit=limit,
        max_nodes=max_nodes,
        expansion_limit=expansion_limit,
    )
    result["context_cache_hit"] = False
    stats = result.get("retrieval_stats")
    if isinstance(stats, dict):
        result["retrieval_stats"] = {**stats, "context_cache_hit": False}
    context_cache.set(cache_key, result)
    return result


def _build_graphrag_context_uncached(
    query: str,
    *,
    seed_node_ids: Optional[Iterable[str]] = None,
    allowed_node_ids: Optional[Iterable[str]] = None,
    limit: int = 6,
    max_nodes: int = 260,
    expansion_limit: int = 24,
) -> Dict[str, Any]:
    """Build a unified GraphRAG payload from vector hits plus graph expansion.

    When seed nodes are provided, the selected contains-subtree remains the
    evidence boundary. Vector search is scoped to that subtree plus direct
    reference nodes.
    """
    expansion_limit = max(1, min(int(expansion_limit), 64))
    lightweight = os.getenv("KGTS_RETRIEVAL_MODE", "hybrid").strip().lower() == "sparse_hybrid"
    graph = {"nodes": [], "relations": []} if lightweight else build_frontend_graph()
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    relations = graph.get("relations") or graph.get("edges") or []
    node_by_id = {str(node.get("id") or ""): node for node in nodes if isinstance(node, dict)}

    seeds = _normalize_node_ids(seed_node_ids or [])
    allowed = set(_normalize_node_ids(allowed_node_ids or []))
    selected_context: Optional[Dict[str, Any]] = None
    if seeds:
        selected_context = build_node_contexts(seeds, max_nodes=max_nodes)
        if not selected_context.get("success"):
            raise ValueError(str(selected_context.get("error") or "Graph node not found"))
        selected_ids = [str(node.get("id") or "") for node in selected_context.get("nodes") or []]
        selected_set = {node_id for node_id in selected_ids if node_id}
        allowed = allowed & selected_set if allowed_node_ids is not None else selected_set
        if lightweight:
            graph = selected_context["graph_data"]
    allowed_set: Optional[Set[str]] = allowed if seeds or allowed_node_ids is not None else None

    scoped_graph = _scope_graph(graph, allowed_set) if allowed_set is not None else graph
    top_k = max(1, min(int(limit or 6), expansion_limit))

    try:
        vector_hits = semantic_search(query, top_k=top_k, allowed_node_ids=sorted(allowed_set) if allowed_set is not None else None)
    except Exception as exc:
        vector_hits = []
        vector_error = str(exc)
    else:
        vector_error = None

    try:
        keyword_hits = [] if lightweight else search_nodes(query, limit=top_k)
        if allowed_set is not None:
            keyword_hits = [hit for hit in keyword_hits if str(hit.get("id") or "") in allowed_set]
    except Exception:
        keyword_hits = []

    fallback_seed_ids = seeds if not vector_hits and not keyword_hits else []
    if lightweight:
        from KGTS.core.graph_service import GraphService
        service = GraphService()
        if seeds:
            graph = service.read_neighborhood(
                sorted(allowed_set or []), max_nodes=min(max_nodes + 32, 512), hops=0,
                allowed_node_ids=allowed_set,
            )
        else:
            hit_ids = [str(hit.get("node_id") or "") for hit in vector_hits]
            graph = service.read_neighborhood(
                hit_ids, max_nodes=expansion_limit, hops=2,
                relation_types=EXPANSION_TYPES, allowed_node_ids=allowed_set,
            )
        graph = build_frontend_graph(graph)
        graph["vector_stats"] = service._vector_stats()
        nodes = graph.get("nodes", [])
        relations = graph.get("relations", [])
        node_by_id = {str(node.get("id") or ""): node for node in nodes}
        scoped_graph = _scope_graph(graph, allowed_set)
    expanded_node_ids = _expand_hit_nodes(
        vector_hits=vector_hits,
        keyword_hits=keyword_hits,
        seed_ids=fallback_seed_ids,
        relations=relations,
        node_by_id=node_by_id,
        allowed_node_ids=allowed_set,
        limit=expansion_limit,
    )
    expanded_nodes = [node_by_id[node_id] for node_id in expanded_node_ids if node_id in node_by_id]
    context_relations = _relations_for_nodes(relations, {str(node.get("id") or "") for node in expanded_nodes})
    graph_context = {"nodes": expanded_nodes, "relations": context_relations, "edges": context_relations}
    evidence = _nodes_to_evidence(expanded_nodes)
    graph_paths = graph_paths_for_evidence(graph_context, evidence, limit=12)
    formula_context = formula_context_for_text(_formula_text(query, expanded_nodes), limit=12)
    llm_context, context_lines = _build_llm_context(expanded_nodes, vector_hits, keyword_hits, context_relations)
    retrieval_stats = _retrieval_stats(scoped_graph, vector_error)
    if lightweight:
        retrieval_stats = {**retrieval_stats, "graph_hops": 2,
                           "context_node_limit": expansion_limit,
                           "context_truncated": bool(graph.get("stats", {}).get("truncated"))}

    return {
        "query": query,
        "seed_node_ids": seeds,
        "allowed_node_ids": sorted(allowed_set) if allowed_set else [],
        "scope_mode": "subtree" if seeds else "global",
        "vector_hits": vector_hits,
        "keyword_hits": keyword_hits,
        "expanded_nodes": expanded_nodes,
        "relations": context_relations,
        "graph_paths": graph_paths,
        "formula_context": formula_context,
        "evidence": evidence,
        "llm_context": llm_context,
        "context_lines": context_lines,
        "context": "\n".join(context_lines),
        "retrieval_stats": retrieval_stats,
        "retrieval_mode": retrieval_stats.get("mode"),
        "selected_context": selected_context,
        "source_scope": (selected_context or {}).get("scope"),
        "graph_data": graph_context,
        "scoped_graph_data": scoped_graph,
    }


def build_node_contexts(node_ids: Iterable[str], *, max_nodes: int = 260) -> Dict[str, Any]:
    """Build or reuse a selected graph subtree context."""
    normalized_ids = _normalize_node_ids(node_ids)
    identity = {
        "kind": "node_contexts",
        "graph_revision": context_cache.graph_revision(),
        "node_ids": normalized_ids,
        "max_nodes": int(max_nodes or 260),
        "retrieval_mode": os.getenv("KGTS_RETRIEVAL_MODE", "hybrid").strip().lower(),
    }
    cache_key = context_cache.make_key(identity)
    cached = context_cache.get(cache_key)
    if cached is not None:
        cached["context_cache_hit"] = True
        return cached
    result = _build_node_contexts_uncached(normalized_ids, max_nodes=max_nodes)
    result["context_cache_hit"] = False
    context_cache.set(cache_key, result)
    return result


def _build_node_contexts_uncached(node_ids: Iterable[str], *, max_nodes: int = 260) -> Dict[str, Any]:
    node_ids = list(node_ids)
    if os.getenv("KGTS_RETRIEVAL_MODE", "hybrid").strip().lower() == "sparse_hybrid":
        from KGTS.core.graph_service import GraphService
        service = GraphService()
        subtree = service.read_neighborhood(node_ids, max_nodes=max_nodes, hops=32,
                                            relation_types=STRUCTURAL_TYPES, outgoing_only=True)
        graph = service.read_neighborhood(
            [node["id"] for node in subtree["nodes"]], max_nodes=min(max_nodes + 32, 512),
            hops=1, relation_types=REFERENCE_TYPES, outgoing_only=True,
        )
        graph["stats"]["truncated"] |= subtree["stats"]["truncated"]
        graph = build_frontend_graph(graph)
    else:
        graph = build_frontend_graph()
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    relations = graph.get("relations") or graph.get("edges") or []
    node_by_id = {str(node.get("id") or ""): node for node in nodes if isinstance(node, dict)}
    root_ids = _normalize_node_ids(node_ids)
    if not root_ids:
        return {"success": False, "error": "No graph node selected", "node_ids": []}

    missing_ids = [node_id for node_id in root_ids if node_id not in node_by_id]
    if missing_ids:
        missing_text = ", ".join(missing_ids)
        return {"success": False, "error": f"Node {missing_text} not found", "node_ids": root_ids}

    children_by_parent: Dict[str, List[str]] = {}
    reference_by_source: Dict[str, List[Dict[str, Any]]] = {}
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source_id") or relation.get("source") or relation.get("from") or "")
        target = str(relation.get("target_id") or relation.get("target") or relation.get("to") or "")
        relation_type = str(relation.get("relation_type") or relation.get("type") or "")
        if not source or not target:
            continue
        if relation_type in STRUCTURAL_TYPES:
            children_by_parent.setdefault(source, []).append(target)
        elif relation_type in REFERENCE_TYPES:
            reference_by_source.setdefault(source, []).append(relation)

    root_ids = sorted(root_ids, key=lambda item: _node_sort_key(node_by_id[item]))
    selected_ids: List[str] = []
    selected_id_set = set(selected_ids)
    for root_id in root_ids:
        remaining = max_nodes - len(selected_id_set)
        if remaining <= 0:
            break
        subtree_ids = _walk_contains(root_id, children_by_parent, node_by_id, max_nodes=remaining)
        for selected_id in subtree_ids:
            if selected_id in selected_id_set:
                continue
            selected_id_set.add(selected_id)
            selected_ids.append(selected_id)

    referenced_ids: List[str] = []
    context_relations: List[Dict[str, Any]] = []
    context_relation_types = STRUCTURAL_TYPES.union(REFERENCE_TYPES)
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source_id") or relation.get("source") or relation.get("from") or "")
        target = str(relation.get("target_id") or relation.get("target") or relation.get("to") or "")
        relation_type = str(relation.get("relation_type") or relation.get("type") or "")
        if source in selected_id_set and relation_type in context_relation_types:
            if relation_type in STRUCTURAL_TYPES and target not in selected_id_set:
                continue
            context_relations.append(relation)
            if relation_type in REFERENCE_TYPES and target in node_by_id and target not in selected_id_set and target not in referenced_ids:
                referenced_ids.append(target)

    ordered_ids = selected_ids + referenced_ids
    ordered_nodes = [node_by_id[item] for item in ordered_ids if item in node_by_id]
    root_trees = [
        _build_tree(root_id, children_by_parent, node_by_id, selected_id_set)
        for root_id in root_ids
        if root_id in selected_id_set
    ]
    if len(root_trees) == 1:
        tree = root_trees[0]
        title = _context_title(node_by_id[root_ids[0]])
    else:
        tree = {
            "id": "__selected_roots__",
            "label": f"Selected {len(root_trees)} graph scopes",
            "type": "selection",
            "children": root_trees,
        }
        title_parts = [_context_title(node_by_id[root_id]) for root_id in root_ids]
        title = " / ".join(title_parts[:4])
        if len(title_parts) > 4:
            title = f"{title} / ..."
    content = _format_context_content(ordered_nodes, tree)

    evidence = [
        {
            "id": str(node.get("id") or ""),
            "label": _node_label(node),
            "type": str(node.get("type") or "concept"),
            "content": str(node.get("content") or ""),
            "source": "selected_graph_subtree",
        }
        for node in ordered_nodes
        if _is_evidence_node(node)
    ][:80]

    return {
        "success": True,
        "node_id": root_ids[0],
        "node_ids": root_ids,
        "root": node_by_id[root_ids[0]],
        "roots": [node_by_id[root_id] for root_id in root_ids],
        "tree": tree,
        "nodes": ordered_nodes,
        "relations": context_relations,
        "chapter_title": title,
        "chapter_content": content,
        "scope": {
            "mode": "subtree",
            "root_count": len(root_ids),
            "selected_count": len(selected_ids),
            "referenced_count": len(referenced_ids),
            "max_nodes": max_nodes,
            "truncated": len(selected_ids) >= max_nodes or bool(graph.get("stats", {}).get("truncated")),
        },
        "evidence": evidence,
        "allowed_node_ids": ordered_ids,
        "graph_data": {
            "nodes": ordered_nodes,
            "relations": context_relations,
            "edges": context_relations,
        },
    }


def _scope_graph(graph: Dict[str, Any], allowed_node_ids: Optional[Set[str]]) -> Dict[str, Any]:
    if allowed_node_ids is None:
        return graph
    nodes = [
        node
        for node in graph.get("nodes", [])
        if isinstance(node, dict) and str(node.get("id") or "") in allowed_node_ids
    ]
    relations = []
    for relation in graph.get("relations") or graph.get("edges") or []:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source_id") or relation.get("source") or relation.get("from") or "")
        target = str(relation.get("target_id") or relation.get("target") or relation.get("to") or "")
        if source in allowed_node_ids and target in allowed_node_ids:
            relations.append(relation)
    return {
        **graph,
        "nodes": nodes,
        "relations": relations,
        "edges": relations,
        "stats": {"node_count": len(nodes), "relation_count": len(relations)},
    }


def _expand_hit_nodes(
    *,
    vector_hits: List[Dict[str, Any]],
    keyword_hits: List[Dict[str, Any]],
    seed_ids: List[str],
    relations: List[Dict[str, Any]],
    node_by_id: Dict[str, Dict[str, Any]],
    allowed_node_ids: Optional[Set[str]],
    limit: int,
) -> List[str]:
    ordered: List[str] = []
    seen: Set[str] = set()

    def add(node_id: str) -> None:
        node_id = str(node_id or "").strip()
        if not node_id or node_id in seen or node_id not in node_by_id:
            return
        if allowed_node_ids is not None and node_id not in allowed_node_ids:
            return
        seen.add(node_id)
        ordered.append(node_id)

    for hit in vector_hits:
        add(str(hit.get("node_id") or (hit.get("metadata") or {}).get("id") or ""))
    for hit in keyword_hits:
        add(str(hit.get("id") or (hit.get("metadata") or {}).get("id") or ""))
    for node_id in seed_ids:
        add(node_id)

    queue = deque((node_id, 0) for node_id in ordered)
    while queue:
        node_id, depth = queue.popleft()
        if depth >= 2:
            continue
        for relation in _neighbor_relations(relations, node_id):
            relation_type = str(relation.get("relation_type") or relation.get("type") or "")
            if relation_type not in EXPANSION_TYPES:
                continue
            before = len(ordered)
            add(str(relation.get("target_id") or relation.get("target") or relation.get("to") or ""))
            add(str(relation.get("source_id") or relation.get("source") or relation.get("from") or ""))
            queue.extend((added, depth + 1) for added in ordered[before:])
            if len(ordered) >= limit:
                return ordered[:limit]
    return ordered[:limit]


def _neighbor_relations(relations: List[Dict[str, Any]], node_id: str) -> List[Dict[str, Any]]:
    results = []
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source_id") or relation.get("source") or relation.get("from") or "")
        target = str(relation.get("target_id") or relation.get("target") or relation.get("to") or "")
        if source == node_id or target == node_id:
            results.append(relation)
    return results


def _relations_for_nodes(relations: List[Dict[str, Any]], node_ids: Set[str]) -> List[Dict[str, Any]]:
    if not node_ids:
        return []
    results = []
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source_id") or relation.get("source") or relation.get("from") or "")
        target = str(relation.get("target_id") or relation.get("target") or relation.get("to") or "")
        if source in node_ids and target in node_ids:
            results.append(relation)
    return results


def _nodes_to_evidence(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    evidence = []
    for index, node in enumerate(nodes, start=1):
        evidence.append(
            {
                "index": index,
                "id": str(node.get("id") or ""),
                "label": _node_label(node),
                "type": str(node.get("type") or "concept"),
                "content": str(node.get("content") or ""),
                "source": (node.get("metadata") or {}).get("source") or "graphrag",
                "source_file": (node.get("metadata") or {}).get("source_file"),
            }
        )
    return evidence


def _build_llm_context(
    nodes: List[Dict[str, Any]],
    vector_hits: List[Dict[str, Any]],
    keyword_hits: List[Dict[str, Any]],
    relations: Optional[List[Dict[str, Any]]] = None,
) -> tuple[List[Dict[str, Any]], List[str]]:
    vector_ids = {
        str(hit.get("node_id") or (hit.get("metadata") or {}).get("id") or "")
        for hit in vector_hits
    }
    keyword_ids = {
        str(hit.get("id") or (hit.get("metadata") or {}).get("id") or "")
        for hit in keyword_hits
    }
    llm_context = []
    context_lines = []
    remaining = max(1000, min(int(os.getenv("KGTS_RAG_CONTEXT_CHARS", "12000")), 24000))
    if relations:
        labels = {str(node.get("id")): _node_label(node) for node in nodes}
        paths = []
        for relation in relations[:12]:
            source = str(relation.get("source_id") or relation.get("source") or "")
            target = str(relation.get("target_id") or relation.get("target") or "")
            kind = relation.get("relation_type") or relation.get("type") or "related"
            paths.append(f"[{source}] {labels.get(source, source)} --{kind}--> [{target}] {labels.get(target, target)}")
        text = "\n".join(paths)[:min(2000, remaining // 4)]
        remaining -= len(text)
        llm_context.append({"content": text, "metadata": {"source": "graph_relations", "type": "relations"}})
        context_lines.append(text)
    for node in nodes:
        node_id = str(node.get("id") or "")
        source = next((hit.get("retrieval_source", "vector") for hit in vector_hits
                       if str(hit.get("node_id") or "") == node_id), "vector")
        if node_id in keyword_ids and node_id not in vector_ids:
            source = "keyword"
        elif node_id not in vector_ids:
            source = "graph_expansion"
        label = _node_label(node)
        node_type = str(node.get("type") or "concept")
        content = str(node.get("content") or label).strip()
        if not content:
            continue
        clipped = content[:min(900, remaining)]
        if not clipped:
            break
        remaining -= len(clipped)
        llm_context.append(
            {
                "content": clipped,
                "metadata": {
                    "id": node_id,
                    "label": label,
                    "type": node_type,
                    "source": source,
                    "source_file": (node.get("metadata") or {}).get("source_file"),
                    "document_source": (node.get("metadata") or {}).get("source"),
                },
            }
        )
        context_lines.append(f"- [{source}] {label} ({node_type}): {clipped[:240]}")
    return llm_context, context_lines


def _formula_text(query: str, nodes: List[Dict[str, Any]]) -> str:
    parts = [query]
    for node in nodes:
        if str(node.get("type") or "") == "formula":
            parts.append(_node_label(node))
        parts.append(str(node.get("content") or "")[:500])
    return "\n".join(parts)


def _retrieval_stats(graph: Dict[str, Any], vector_error: Optional[str]) -> Dict[str, Any]:
    stats = graph.get("vector_stats") if isinstance(graph.get("vector_stats"), dict) else {}
    try:
        schema = get_graph_schema()
        latest = schema.get("vector_stats") if isinstance(schema.get("vector_stats"), dict) else {}
        if latest:
            stats = {**stats, **latest}
    except Exception:
        pass
    if vector_error and not stats.get("last_error"):
        stats = {**stats, "last_error": vector_error}
    if "mode" not in stats:
        stats = {**stats, "mode": "unknown"}
    return stats


def _normalize_node_ids(node_ids: Iterable[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for node_id in node_ids:
        text = str(node_id or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _walk_contains(
    root_id: str,
    children_by_parent: Dict[str, List[str]],
    node_by_id: Dict[str, Dict[str, Any]],
    *,
    max_nodes: int,
) -> List[str]:
    ordered: List[str] = []
    visited = {root_id}
    queue = deque([root_id])
    while queue and len(ordered) < max_nodes:
        current = queue.popleft()
        if current in node_by_id:
            ordered.append(current)
        children = sorted(
            (child for child in children_by_parent.get(current, []) if child in node_by_id),
            key=lambda child_id: _node_sort_key(node_by_id[child_id]),
        )
        for child in children:
            if child in visited:
                continue
            visited.add(child)
            queue.append(child)
    return ordered


def _build_tree(
    node_id: str,
    children_by_parent: Dict[str, List[str]],
    node_by_id: Dict[str, Dict[str, Any]],
    selected_ids: set[str],
    ancestors: frozenset[str] = frozenset(),
) -> Dict[str, Any]:
    node = node_by_id[node_id]
    children = [
        _build_tree(child_id, children_by_parent, node_by_id, selected_ids, ancestors | {node_id})
        for child_id in sorted(children_by_parent.get(node_id, []), key=lambda item: _node_sort_key(node_by_id.get(item, {})))
        if child_id in selected_ids and child_id in node_by_id and child_id not in ancestors | {node_id}
    ]
    return {
        "id": node_id,
        "label": _node_label(node),
        "type": str(node.get("type") or "concept"),
        "children": children,
    }


def _format_context_content(nodes: List[Dict[str, Any]], tree: Dict[str, Any]) -> str:
    lines = [
        f"Selected graph scope: {_tree_line(tree)}",
        "",
        "Teaching tree:",
        *_format_tree_lines(tree),
        "",
        "Source nodes:",
    ]
    for node in nodes:
        node_type = str(node.get("type") or "concept")
        label = _node_label(node)
        content = str(node.get("content") or "").strip()
        if not content and node_type in {"chapter", "section"}:
            content = label
        if node_type in {"chapter", "section"} and content == label:
            lines.append(f"\n## {label}")
            continue
        lines.append(f"\n### [{node_type}] {label}")
        if content:
            lines.append(content[:2400])
    return "\n".join(lines).strip()


def _format_tree_lines(tree: Dict[str, Any], depth: int = 0) -> List[str]:
    indent = "  " * depth
    lines = [f"{indent}- [{tree.get('type')}] {tree.get('label')}"]
    for child in tree.get("children") or []:
        lines.extend(_format_tree_lines(child, depth + 1))
    return lines


def _tree_line(tree: Dict[str, Any]) -> str:
    return f"{tree.get('label')} ({tree.get('type')})"


def _node_label(node: Dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return str(node.get("label") or metadata.get("label") or node.get("id") or "untitled")


def _context_title(node: Dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    heading_path = metadata.get("heading_path")
    if isinstance(heading_path, list) and heading_path:
        return " / ".join(str(item) for item in heading_path[-2:] if item)
    return _node_label(node)


def _node_sort_key(node: Dict[str, Any]) -> tuple[int, str, str]:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    toc_page = metadata.get("toc_page")
    if isinstance(toc_page, int) or (isinstance(toc_page, str) and toc_page.isdigit()):
        toc_level = metadata.get("toc_level")
        level = int(toc_level) if isinstance(toc_level, int) or (isinstance(toc_level, str) and toc_level.isdigit()) else 0
        return (0, "toc", f"{int(toc_page):06d}:{level:02d}:{metadata.get('toc_node_id') or node.get('id') or ''}")
    block_index = metadata.get("block_index")
    if isinstance(block_index, int):
        order = block_index
    elif isinstance(block_index, str) and block_index.isdigit():
        order = int(block_index)
    else:
        order = 0
    node_type = str(node.get("type") or "")
    type_order = {"chapter": 0, "section": 1, "discussion": 2, "proposition": 2, "derivation": 2, "example": 3, "formula": 4, "note": 5, "figure": 6}.get(node_type, 9)
    return (type_order, str((metadata.get("source_unit") or metadata.get("source") or "")), f"{order:06d}:{_node_label(node)}")


def _is_evidence_node(node: Dict[str, Any]) -> bool:
    node_type = str(node.get("type") or "")
    if node_type in {"chapter", "section"}:
        return False
    return node_type in CONTENT_TYPES or bool(str(node.get("content") or "").strip())
