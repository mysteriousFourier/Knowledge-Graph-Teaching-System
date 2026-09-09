"""Bridge layer between the frontend APIs and vector_index_system."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
VECTOR_SYSTEM_DIR = BACKEND_DIR / "vector_index_system"
MCP_SERVER_DIR = BACKEND_DIR / "mcp-server"
RUNTIME_DIR = Path(os.getenv("APP_RUNTIME_DIR", str(PROJECT_ROOT / ".runtime")))
LEGACY_CHAPTERS_FILE = BACKEND_DIR / "data" / "chapters.json"
LEGACY_PROGRESS_FILE = BACKEND_DIR / "data" / "chapter_progress.json"
CHAPTERS_FILE = RUNTIME_DIR / "chapters.json"
PROGRESS_FILE = RUNTIME_DIR / "chapter_progress.json"
TTS_COURSE_AUDIO_DIR = RUNTIME_DIR / "tts" / "audio" / "course"

from KGTS.core.cli_dispatch import dispatch_tool
from KGTS.core.memory_runtime import MemoryService
from KGTS.mcp_server.graphml_importer import convert_to_mcp_format, parse_graphml_file

try:
    from KGTS.education.kg_constraints import expand_formula_references
except Exception:
    def expand_formula_references(value: Any, *, display: bool = True) -> str:
        return str(value or "")


def _now() -> str:
    return datetime.now().isoformat()


def _safe_tts_course_id(value: str | None) -> str:
    text = (value or "").strip()[:120]
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")


def _clear_tts_course_audio(chapter_id: str | None) -> None:
    safe = _safe_tts_course_id(chapter_id)
    if not safe:
        return
    target = (TTS_COURSE_AUDIO_DIR / safe).resolve()
    base = TTS_COURSE_AUDIO_DIR.resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return
    shutil.rmtree(target, ignore_errors=True)


def _timestamp_value(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return 0.0

    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _chapter_sort_value(chapter: Dict[str, Any]) -> float:
    return _timestamp_value(chapter.get("updated_at") or chapter.get("created_at"))


def _strip_chapter_prefix(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^(?:chapter[_:]+)+", "", text, flags=re.I).strip()
    return text


def _chapter_slug(value: Any) -> str:
    text = _strip_chapter_prefix(value)
    match = re.search(r"\bchapter\s*0*([0-9]+)\b", text, flags=re.I)
    if match:
        return f"chapter{int(match.group(1))}"
    match = re.fullmatch(r"chapter0*([0-9]+)", text, flags=re.I)
    if match:
        return f"chapter{int(match.group(1))}"
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text, flags=re.UNICODE).strip("_").lower()
    return slug[:80]


def canonical_chapter_id(chapter_id: Optional[str] = None, title: Optional[str] = None) -> str:
    for candidate in (chapter_id, title):
        slug = _chapter_slug(candidate)
        if slug:
            return f"chapter::{slug}"
    return ""


def _normalized_chapter_stub(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("：", ":")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^章节\s*:\s*", "", text)
    text = re.sub(r"^章节\s+", "", text)
    return text


def _is_generic_chapter_stub(title: Any, content: Any) -> bool:
    title_stub = _normalized_chapter_stub(title)
    if not re.fullmatch(r"chapter\s*0*\d+", title_stub):
        return False
    content_stub = _normalized_chapter_stub(content)
    return not content_stub or content_stub == title_stub


def _looks_like_temporary_chapter_id(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if re.fullmatch(r"chapter::\d{8,}", text):
        return True
    if re.fullmatch(r"(?:chapter|ppt|temp|draft)[_-]\d{8,}", text):
        return True
    return bool(re.fullmatch(r"\d{8,}", _strip_chapter_prefix(text)))


def _resolve_chapter_storage_id(chapter_id: Optional[str], title: Optional[str]) -> str:
    title_based_id = canonical_chapter_id(title=title)
    raw_id = str(chapter_id or "").strip()
    id_based = canonical_chapter_id(chapter_id=chapter_id)
    if title_based_id and (_looks_like_temporary_chapter_id(raw_id) or not id_based):
        return title_based_id
    return canonical_chapter_id(chapter_id, title) or raw_id or title_based_id


def _chapter_identity(chapter: Dict[str, Any]) -> str:
    return canonical_chapter_id(
        str(chapter.get("id") or ""),
        str(chapter.get("title") or ""),
    ).lower()


def _text_len(value: Any) -> int:
    return len(value) if isinstance(value, str) else 0


def _chapter_detail_score(chapter: Dict[str, Any]) -> tuple[int, int, int, float]:
    content_len = _text_len(chapter.get("content"))
    lecture_len = _text_len(chapter.get("lecture_content"))
    has_graph = 1 if chapter.get("graph_data") else 0
    preferred_id = 1 if str(chapter.get("id") or "").startswith("chapter::") else 0
    return (lecture_len * 2 + content_len, has_graph, preferred_id, _chapter_sort_value(chapter))


def _is_placeholder_chapter(chapter: Dict[str, Any]) -> bool:
    title = str(chapter.get("title") or "").strip()
    content = str(chapter.get("content") or "").strip()
    if chapter.get("lecture_content") or chapter.get("graph_data"):
        return False
    if chapter.get("exercise_bank") or chapter.get("approved_exercise_bank") or chapter.get("ppt_slides"):
        return False
    return _is_generic_chapter_stub(title, content)


def _is_toc_export_chapter(chapter: Dict[str, Any]) -> bool:
    if not isinstance(chapter, dict):
        return False
    candidates = [
        chapter.get("id"),
        chapter.get("chapter_id"),
        chapter.get("source_type"),
        chapter.get("source"),
    ]
    metadata = chapter.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend([metadata.get("id"), metadata.get("source"), metadata.get("source_file")])
    return any(
        re.search(r"(?:^|::)toc[_:]", str(candidate or ""), flags=re.I)
        or str(candidate or "").strip().lower().endswith("_toc_tree.json")
        for candidate in candidates
    )


def _is_empty_shell_chapter(chapter: Dict[str, Any]) -> bool:
    if not isinstance(chapter, dict):
        return False
    title = str(chapter.get("title") or "").strip()
    if not title:
        return False
    if chapter.get("lecture_content") or chapter.get("graph_data"):
        return False
    if chapter.get("ppt_slides") or chapter.get("slide_lectures"):
        return False
    content = str(chapter.get("content") or "").strip()
    if not content:
        return bool(re.search(r"\bchapter\s*\d+\b", title, flags=re.I))
    if _normalized_chapter_stub(title) == _normalized_chapter_stub(content):
        return bool(re.search(r"\bchapter\s*\d+\b", title, flags=re.I))
    return _is_generic_chapter_stub(title, content)


def _is_generated_shell_chapter_node(node: Dict[str, Any]) -> bool:
    if not isinstance(node, dict):
        return False
    if str(node.get("type") or "") != "chapter":
        return False
    metadata = node.get("metadata") or {}
    source = str(metadata.get("source") or node.get("source") or "")
    title = str(metadata.get("label") or node.get("label") or "")
    content = str(node.get("content") or metadata.get("description") or "")
    node_id = str(node.get("id") or metadata.get("id") or "")
    if source != "frontend_test":
        return False
    return _is_generic_chapter_stub(title, content) or _looks_like_temporary_chapter_id(node_id)


def _is_graph_chapter_list_shell(node: Dict[str, Any]) -> bool:
    if not isinstance(node, dict):
        return False
    if str(node.get("type") or "") != "chapter":
        return False
    metadata = node.get("metadata") or {}
    source = str(metadata.get("source") or node.get("source") or "")
    if _is_generated_shell_chapter_node(node):
        return True
    if source == "structured_sync" and str(metadata.get("role") or "") == "chapter_root":
        return True
    title = str(metadata.get("label") or node.get("label") or "")
    content = str(node.get("content") or metadata.get("description") or "")
    node_id = str(node.get("id") or metadata.get("id") or "")
    return _is_empty_shell_chapter({"id": node_id, "title": title, "content": content})


def _dedupe_chapters(chapters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_by_identity: Dict[str, Dict[str, Any]] = {}
    for chapter in chapters:
        identity = _chapter_identity(chapter)
        current = best_by_identity.get(identity)
        if current is None or _chapter_detail_score(chapter) > _chapter_detail_score(current):
            best_by_identity[identity] = chapter
    return list(best_by_identity.values())


def call_backend_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
    return dispatch_tool(name, arguments or {})


def _node_label(node: Dict[str, Any]) -> str:
    metadata = node.get("metadata") or {}
    return (
        node.get("label")
        or metadata.get("label")
        or node.get("content")
        or node.get("id")
        or "untitled"
    )


def _node_size(node_type: str) -> str:
    if node_type == "chapter":
        return "large"
    if node_type in {"note", "observation"}:
        return "small"
    return "medium"


def _normalize_node(node: Dict[str, Any]) -> Dict[str, Any]:
    label = expand_formula_references(_node_label(node), display=False)
    metadata = dict(node.get("metadata") or {})
    content = expand_formula_references(node.get("content") or metadata.get("description") or label)
    if metadata.get("description"):
        metadata["description"] = expand_formula_references(metadata.get("description"))
    if metadata.get("label"):
        metadata["label"] = expand_formula_references(metadata.get("label"), display=False)
    return {
        **node,
        "id": node.get("id"),
        "label": label,
        "content": content,
        "type": node.get("type") or metadata.get("type") or "concept",
        "size": node.get("size") or _node_size(node.get("type") or "concept"),
        "metadata": metadata,
    }


def _as_relation_endpoint(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("id", "node_id", "nodeId", "value", "label"):
            if value.get(key):
                return str(value.get(key)).strip()
        return ""
    if value is None:
        return ""
    return str(value).strip()


def _first_relation_value(*values: Any) -> str:
    for value in values:
        endpoint = _as_relation_endpoint(value)
        if endpoint:
            return endpoint
    return ""


def _normalize_relation(relation: Dict[str, Any]) -> Dict[str, Any]:
    raw_metadata = relation.get("metadata") or relation.get("properties") or {}
    if isinstance(raw_metadata, str):
        try:
            raw_metadata = json.loads(raw_metadata)
        except json.JSONDecodeError:
            raw_metadata = {}
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    source_id = _first_relation_value(
        relation.get("source_id"),
        relation.get("source"),
        relation.get("source_node"),
        relation.get("sourceId"),
        relation.get("sourceNode"),
        relation.get("from"),
        metadata.get("source_id"),
        metadata.get("source"),
        metadata.get("source_node"),
        metadata.get("sourceId"),
        metadata.get("sourceNode"),
        metadata.get("from"),
    )
    target_id = _first_relation_value(
        relation.get("target_id"),
        relation.get("target"),
        relation.get("target_node"),
        relation.get("targetId"),
        relation.get("targetNode"),
        relation.get("to"),
        metadata.get("target_id"),
        metadata.get("target"),
        metadata.get("target_node"),
        metadata.get("targetId"),
        metadata.get("targetNode"),
        metadata.get("to"),
    )
    relation_type = _first_relation_value(
        relation.get("relation_type"),
        relation.get("type"),
        relation.get("label"),
        metadata.get("relation_type"),
        metadata.get("type"),
        metadata.get("label"),
    ) or "related"
    description = expand_formula_references(relation.get("description") or metadata.get("description") or "")
    if metadata.get("description"):
        metadata["description"] = expand_formula_references(metadata.get("description"))
    return {
        **relation,
        "source_id": source_id,
        "target_id": target_id,
        "source": source_id,
        "target": target_id,
        "relation_type": relation_type,
        "type": relation_type,
        "description": description,
        "metadata": metadata,
    }


def normalize_frontend_node(node: Dict[str, Any]) -> Dict[str, Any]:
    return _normalize_node(node)


def normalize_frontend_relation(relation: Dict[str, Any]) -> Dict[str, Any]:
    return _normalize_relation(relation)


def build_frontend_graph(raw_graph: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    graph = raw_graph or call_backend_tool("read_graph")
    if not isinstance(graph, dict):
        graph = {}
    nodes = [_normalize_node(node) for node in graph.get("nodes", [])]
    lecture_node_ids = {str(node.get("id")) for node in nodes if _is_generated_lecture_node(node)}
    nodes = [node for node in nodes if str(node.get("id")) not in lecture_node_ids]
    raw_relations = graph.get("relations") or graph.get("edges") or []
    relations = [
        normalized
        for relation in raw_relations
        if isinstance(relation, dict)
        for normalized in [_normalize_relation(relation)]
        if str(normalized.get("source_id") or "") not in lecture_node_ids
        and str(normalized.get("target_id") or "") not in lecture_node_ids
    ]
    return {
        **graph,
        "nodes": nodes,
        "relations": relations,
        "edges": relations,
    }


def get_graph_schema() -> Dict[str, Any]:
    schema = call_backend_tool("get_graph_schema")
    if isinstance(schema, dict):
        return schema
    graph = build_frontend_graph()
    return {
        "stats": graph.get("stats", {}),
        "vector_stats": graph.get("vector_stats", {}),
        "node_types": sorted({node.get("type") for node in graph.get("nodes", []) if node.get("type")}),
        "relation_types": sorted(
            {edge.get("relation_type") or edge.get("type") for edge in graph.get("relations", []) if edge.get("relation_type") or edge.get("type")}
        ),
    }


def search_nodes(keyword: str, node_type: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    results = call_backend_tool(
        "search_nodes",
        {"keyword": keyword, "node_type": node_type, "limit": limit},
    )
    if isinstance(results, list):
        return [_normalize_node(result) for result in results]
    if isinstance(results, dict) and isinstance(results.get("results"), list):
        return [_normalize_node(result) for result in results["results"]]
    return []


def _is_generated_lecture_node(node: Dict[str, Any]) -> bool:
    metadata = node.get("metadata") or {}
    label = str(metadata.get("label") or node.get("label") or "")
    source = str(metadata.get("source") or node.get("source") or "")
    node_id = str(node.get("id") or metadata.get("id") or "")
    node_type = str(node.get("type") or metadata.get("type") or "")
    haystack = f"{node_id}\n{label}\n{node.get('content') or ''}\n{json.dumps(metadata, ensure_ascii=False)}"
    return (
        "授课文案" in haystack
        or "__lecture" in node_id
        or "ai_lecture" in haystack
        or "lecture_note" in haystack
        or "lecture script" in haystack.lower()
        or "generated lecture" in haystack.lower()
        or "teaching script" in haystack.lower()
        or "lesson script" in haystack.lower()
        or "lecture_content" in haystack
        or "lecture_learning_plan" in haystack
        or (
        "授课文案" in label
        or "鎺堣" in label
        or node_id.endswith("__lecture")
        or source == "ai_lecture"
        or (source == "frontend_test" and node_type == "observation" and "__lecture" in node_id))
    )


def delete_generated_lecture_nodes() -> Dict[str, Any]:
    graph = call_backend_tool("read_graph")
    removed: List[str] = []
    for node in graph.get("nodes", []) if isinstance(graph, dict) else []:
        if not isinstance(node, dict) or not _is_generated_lecture_node(node):
            continue
        node_id = str(node.get("id") or (node.get("metadata") or {}).get("id") or "")
        if not node_id:
            continue
        try:
            result = call_backend_tool("delete_memory", {"node_id": node_id})
            if not isinstance(result, dict) or result.get("success", True):
                removed.append(node_id)
        except Exception:
            continue
    return {"success": True, "deleted_ids": removed, "deleted_count": len(removed)}


def semantic_search(
    query: str,
    node_type: Optional[str] = None,
    top_k: int = 10,
    allowed_node_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    results = call_backend_tool(
        "semantic_search",
        {
            "query": query,
            "node_type": node_type,
            "top_k": top_k,
            "allowed_node_ids": allowed_node_ids,
        },
    )
    if isinstance(results, list):
        return results
    if isinstance(results, dict) and isinstance(results.get("results"), list):
        return results["results"]
    return []


def search_memory(query: str, k: int = 5) -> Dict[str, Any]:
    return MemoryService().search_memory(query, k=k)


def build_rag_context(
    question: str,
    limit: int = 6,
    *,
    seed_node_ids: Optional[List[str]] = None,
    allowed_node_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from KGTS.core.graph_context import build_graphrag_context

    graphrag = build_graphrag_context(
        question,
        seed_node_ids=seed_node_ids,
        allowed_node_ids=allowed_node_ids,
        limit=limit,
    )
    keyword_hits = graphrag.get("keyword_hits") or []
    semantic_hits = graphrag.get("vector_hits") or []

    try:
        memory_payload = search_memory(question, k=limit)
        memory_hits = memory_payload.get("results", []) if isinstance(memory_payload, dict) else []
    except Exception:
        memory_hits = []

    llm_context: List[Dict[str, Any]] = list(graphrag.get("llm_context") or [])
    context_lines: List[str] = list(graphrag.get("context_lines") or [])
    seen: set[str] = set()
    for item in llm_context:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        seen.add(f"{metadata.get('source')}:{metadata.get('id')}:{str(item.get('content') or '')[:80]}")

    def add_context(source_id: str, label: str, node_type: str, content: str, source: str) -> None:
        clean_label = (label or source_id or "untitled").strip()
        clean_content = (content or clean_label).strip()
        if _is_generated_lecture_node(
            {
                "id": source_id,
                "type": node_type,
                "content": clean_content,
                "metadata": {"label": clean_label, "source": source},
            }
        ):
            return
        key = f"{source}:{source_id or clean_label}:{clean_content[:80]}"
        if not clean_content or key in seen:
            return
        seen.add(key)
        clipped = clean_content[:700]
        llm_context.append(
            {
                "content": clipped,
                "metadata": {
                    "id": source_id,
                    "label": clean_label,
                    "type": node_type or "context",
                    "source": source,
                },
            }
        )
        context_lines.append(f"- [{source}] {clean_label} ({node_type or 'context'}): {clipped[:220]}")

    for hit in memory_hits:
        metadata = hit.get("metadata") or {}
        add_context(
            str(metadata.get("id") or hit.get("id") or ""),
            str(metadata.get("label") or metadata.get("provider") or "memory"),
            str(metadata.get("type") or "memory"),
            str(hit.get("content") or ""),
            "memory",
        )

    return {
        "context": "\n".join(context_lines[:24]),
        "llm_context": llm_context[:24],
        "keyword_hits": keyword_hits,
        "semantic_hits": semantic_hits,
        "vector_hits": semantic_hits,
        "memory_hits": memory_hits,
        "retrieval_mode": graphrag.get("retrieval_mode"),
        "retrieval_stats": graphrag.get("retrieval_stats") or {},
        "graphrag_context": graphrag,
        "graph_paths": graphrag.get("graph_paths") or [],
        "formula_context": graphrag.get("formula_context") or [],
    }


def _build_local_answer_legacy(question: str, limit: int = 5) -> Dict[str, Any]:
    rag_context = build_rag_context(question, limit=limit)
    keyword_hits = rag_context["keyword_hits"]
    semantic_hits = rag_context["semantic_hits"]
    memory_hits = rag_context["memory_hits"]

    lines: List[str] = []
    seen: set[str] = set()

    for hit in keyword_hits:
        label = _node_label(hit)
        text = (hit.get("content") or label).strip()
        if label in seen:
            continue
        seen.add(label)
        lines.append(f"- {label}: {text[:140]}")

    for hit in semantic_hits:
        metadata = hit.get("metadata") or {}
        label = metadata.get("label") or hit.get("node_id") or "semantic_hit"
        text = (metadata.get("content") or label).strip()
        if label in seen:
            continue
        seen.add(label)
        lines.append(f"- {label}: {text[:140]}")

    for hit in memory_hits:
        label = hit.get("metadata", {}).get("provider") or "memory"
        text = str(hit.get("content") or "").strip()
        key = f"{label}:{text[:40]}"
        if not text or key in seen:
            continue
        seen.add(key)
        lines.append(f"- [{label}] {text[:140]}")

    if lines:
        answer = "Based on retrieved graph/memory evidence, keeping source wording in its original language:\n" + "\n".join(lines[:limit])
    else:
        answer = "I could not find directly relevant evidence in the knowledge graph or memory store. Please add the source passage or ask with a more specific term."

    return {
        "answer": answer,
        "keyword_hits": keyword_hits,
        "semantic_hits": semantic_hits,
        "memory_hits": memory_hits,
    }


def build_local_answer(question: str, limit: int = 5) -> Dict[str, Any]:
    rag_context = build_rag_context(question, limit=limit)
    lines: List[str] = []
    seen: set[str] = set()

    for item in rag_context["llm_context"]:
        metadata = item.get("metadata") or {}
        label = metadata.get("label") or metadata.get("id") or "context"
        source = metadata.get("source") or "graph"
        text = str(item.get("content") or "").strip()
        key = f"{source}:{label}:{text[:40]}"
        if not text or key in seen:
            continue
        seen.add(key)
        lines.append(f"- [{source}] {label}: {text[:180]}")

    if lines:
        answer = "Based on retrieved graph/memory evidence, keeping source wording in its original language:\n" + "\n".join(lines[:limit])
    else:
        answer = "I could not find directly relevant evidence in the knowledge graph or memory store. Please add the source passage or ask with a more specific term."

    return {
        "answer": answer,
        "context": rag_context["context"],
        "llm_context": rag_context["llm_context"],
        "keyword_hits": rag_context["keyword_hits"],
        "semantic_hits": rag_context["semantic_hits"],
        "memory_hits": rag_context["memory_hits"],
        "vector_hits": rag_context.get("vector_hits") or rag_context["semantic_hits"],
        "retrieval_mode": rag_context.get("retrieval_mode"),
        "retrieval_stats": rag_context.get("retrieval_stats") or {},
        "graphrag_context": rag_context.get("graphrag_context") or {},
        "graph_paths": rag_context.get("graph_paths") or [],
        "formula_context": rag_context.get("formula_context") or [],
    }

def import_graph_payload(graph_data: Dict[str, Any]) -> Dict[str, Any]:
    raw_nodes = graph_data.get("nodes", [])
    raw_relations = graph_data.get("relations") or graph_data.get("edges") or []

    nodes: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []

    for node in raw_nodes:
        metadata = dict(node.get("metadata") or node.get("properties") or {})
        if node.get("label") and "label" not in metadata:
            metadata["label"] = node["label"]
        if node.get("id"):
            metadata["id"] = str(node["id"])
        if node.get("source") and "source" not in metadata:
            metadata["source"] = node["source"]
        nodes.append(
            {
                "id": node.get("id"),
                "content": node.get("content") or node.get("description") or node.get("definition") or node.get("label") or "",
                "type": node.get("type") or "concept",
                "metadata": metadata,
            }
        )

    for relation in raw_relations:
        metadata = dict(relation.get("metadata") or relation.get("properties") or {})
        relations.append(
            {
                "source_id": relation.get("source_id") or relation.get("source") or relation.get("from"),
                "target_id": relation.get("target_id") or relation.get("target") or relation.get("to"),
                "relation_type": relation.get("relation_type") or relation.get("type") or relation.get("label") or "related",
                "metadata": metadata,
                "similarity": relation.get("similarity") or relation.get("strength"),
            }
        )

    return call_backend_tool("batch_import_graph", {"nodes": nodes, "relations": relations})


def import_graphml_payload(
    *,
    file_path: Optional[str] = None,
    file_content: Optional[str] = None,
) -> Dict[str, Any]:
    temp_path: Optional[Path] = None
    try:
        source_path = file_path
        if file_content is not None:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".graphml", delete=False, encoding="utf-8") as handle:
                handle.write(file_content)
                temp_path = Path(handle.name)
            source_path = str(temp_path)

        if not source_path:
            raise ValueError("file_path or file_content is required")

        nodes, edges = parse_graphml_file(source_path)
        converted = convert_to_mcp_format(nodes, edges)
        result = call_backend_tool(
            "batch_import_graph",
            {"nodes": converted.get("nodes", []), "relations": converted.get("edges", [])},
        )
        result["graphml_stats"] = {
            "nodes_parsed": len(nodes),
            "edges_parsed": len(edges),
        }
        result["source_file"] = file_path or "inline_content"
        return result
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def import_graph_db_payload(file_bytes: bytes) -> Dict[str, Any]:
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".db", delete=False) as handle:
            handle.write(file_bytes)
            temp_path = Path(handle.name)

        graph_data = _read_graph_db(temp_path)
        result = import_graph_payload(graph_data)
        result["sqlite_stats"] = {
            "nodes_parsed": len(graph_data.get("nodes") or []),
            "relations_parsed": len(graph_data.get("relations") or []),
        }
        return result
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink(missing_ok=True)
            except PermissionError:
                # Windows can keep SQLite files locked briefly after reads. The
                # import result is more important than best-effort temp cleanup.
                pass


def _read_graph_db(db_path: Path) -> Dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        tables = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

        node_table = "nodes" if "nodes" in tables else None
        relation_table = "relationships" if "relationships" in tables else ("relations" if "relations" in tables else None)
        if not node_table:
            raise ValueError("SQLite 图谱库缺少 nodes 表")

        nodes = [_sqlite_node_to_payload(dict(row)) for row in conn.execute(f'SELECT * FROM "{node_table}"')]
        relations: List[Dict[str, Any]] = []
        if relation_table:
            relations = [
                _sqlite_relation_to_payload(dict(row))
                for row in conn.execute(f'SELECT * FROM "{relation_table}"')
            ]

        return {"nodes": nodes, "relations": relations}
    finally:
        conn.close()


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sqlite_node_to_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _json_object(row.get("metadata_json") or row.get("metadata"))
    label = row.get("label") or metadata.get("label") or row.get("content") or row.get("id")
    if label and "label" not in metadata:
        metadata["label"] = label
    if row.get("source") and "source" not in metadata:
        metadata["source"] = row.get("source")
    return {
        "id": row.get("id"),
        "label": label,
        "content": row.get("content") or metadata.get("content") or label or "",
        "type": row.get("type") or metadata.get("type") or "concept",
        "source": row.get("source") or metadata.get("source"),
        "metadata": metadata,
    }


def _sqlite_relation_to_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _json_object(row.get("metadata_json") or row.get("metadata"))
    description = row.get("description") or metadata.get("description") or ""
    if description and "description" not in metadata:
        metadata["description"] = description
    if row.get("source") and "source" not in metadata:
        metadata["source"] = row.get("source")
    return {
        "id": row.get("id"),
        "source_id": row.get("source_node") or row.get("source_id") or row.get("source"),
        "target_id": row.get("target_node") or row.get("target_id") or row.get("target"),
        "relation_type": row.get("type") or row.get("relation_type") or metadata.get("type") or "related",
        "description": description,
        "source": row.get("source") or metadata.get("source"),
        "metadata": metadata,
        "similarity": row.get("strength") or row.get("similarity"),
    }


class ChapterStore:
    def __init__(self, chapters_file: Path, progress_file: Path):
        self.chapters_file = chapters_file
        self.progress_file = progress_file
        self.chapters_file.parent.mkdir(parents=True, exist_ok=True)
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        self._chapters_cache_signature: tuple[int, int] | None = None
        self._chapters_cache: Dict[str, Dict[str, Any]] | None = None
        self._deleted_chapter_ids_cache: set[str] | None = None

    def _chapters_file_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.chapters_file.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _load_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_chapters(self) -> Dict[str, Dict[str, Any]]:
        signature = self._chapters_file_signature()
        if signature == self._chapters_cache_signature and self._chapters_cache is not None:
            return self._chapters_cache
        raw = self._load_json(self.chapters_file)
        chapters = raw.get("chapters")
        deleted = set(raw.get("deleted_chapters") or [])
        merged: Dict[str, Dict[str, Any]] = dict(chapters) if isinstance(chapters, dict) else {}
        changed = False
        for chapter_id, chapter in list(merged.items()):
            if not isinstance(chapter, dict):
                merged.pop(chapter_id, None)
                changed = True
                continue
            if _is_placeholder_chapter({**chapter, "id": chapter.get("id") or chapter_id}):
                merged.pop(chapter_id, None)
                changed = True
                continue
            if _is_toc_export_chapter({**chapter, "id": chapter.get("id") or chapter_id}):
                merged.pop(chapter_id, None)
                changed = True
                continue
            if _is_empty_shell_chapter({**chapter, "id": chapter.get("id") or chapter_id}):
                merged.pop(chapter_id, None)
                changed = True
                continue
            identity = _chapter_identity({**chapter, "id": chapter.get("id") or chapter_id})
            aliases = {
                str(chapter_id),
                str(chapter.get("id") or ""),
                canonical_chapter_id(str(chapter_id)) or "",
                canonical_chapter_id(str(chapter.get("id") or ""), str(chapter.get("title") or "")) or "",
                identity,
            }
            if any(alias and alias in deleted for alias in aliases):
                merged.pop(chapter_id, None)
                changed = True
        if changed:
            try:
                self._save_chapters(merged)
            except OSError:
                pass
        self._chapters_cache_signature = self._chapters_file_signature()
        self._chapters_cache = merged
        self._deleted_chapter_ids_cache = deleted
        return merged

    def _save_chapters(self, chapters: Dict[str, Dict[str, Any]]) -> None:
        current = self._load_json(self.chapters_file)
        deleted = current.get("deleted_chapters")
        self._save_json(
            self.chapters_file,
            {
                "chapters": chapters,
                "deleted_chapters": deleted if isinstance(deleted, list) else [],
            },
        )
        self._chapters_cache_signature = self._chapters_file_signature()
        self._chapters_cache = chapters
        self._deleted_chapter_ids_cache = {str(item) for item in deleted} if isinstance(deleted, list) else set()

    def _load_deleted_chapter_ids(self) -> set[str]:
        signature = self._chapters_file_signature()
        if signature == self._chapters_cache_signature and self._deleted_chapter_ids_cache is not None:
            return self._deleted_chapter_ids_cache
        raw = self._load_json(self.chapters_file)
        deleted = raw.get("deleted_chapters")
        result = {str(item) for item in deleted} if isinstance(deleted, list) else set()
        self._chapters_file_signature()
        self._chapters_cache_signature = self._chapters_file_signature()
        self._deleted_chapter_ids_cache = result
        return result

    def _save_deleted_chapter_ids(self, deleted: set[str]) -> None:
        raw = self._load_json(self.chapters_file)
        chapters = raw.get("chapters")
        self._save_json(
            self.chapters_file,
            {
                "chapters": chapters if isinstance(chapters, dict) else {},
                "deleted_chapters": sorted(item for item in deleted if item),
            },
        )
        self._chapters_cache_signature = self._chapters_file_signature()
        self._chapters_cache = chapters if isinstance(chapters, dict) else {}
        self._deleted_chapter_ids_cache = set(deleted)

    def _clear_deleted_chapter_ids(self, *ids: str) -> None:
        deleted = self._load_deleted_chapter_ids()
        changed = False
        for item in ids:
            if item in deleted:
                deleted.remove(item)
                changed = True
        if changed:
            self._save_deleted_chapter_ids(deleted)

    def _mark_chapter_deleted(self, chapter_id: str, chapter: Optional[Dict[str, Any]]) -> set[str]:
        deleted = self._load_deleted_chapter_ids()
        title = str((chapter or {}).get("title") or "")
        raw_id = str((chapter or {}).get("id") or chapter_id or "")
        markers = {
            str(chapter_id or ""),
            raw_id,
            canonical_chapter_id(chapter_id, title) or "",
            canonical_chapter_id(raw_id, title) or "",
            _chapter_identity({"id": raw_id or chapter_id or "", "title": title}),
        }
        deleted.update(marker for marker in markers if marker)
        self._save_deleted_chapter_ids(deleted)
        return markers

    def _load_progress(self) -> Dict[str, Any]:
        progress = self._load_json(LEGACY_PROGRESS_FILE)
        progress.update(self._load_json(self.progress_file))
        return progress

    def _save_progress(self, progress: Dict[str, Any]) -> None:
        self._save_json(self.progress_file, progress)

    def _chapter_alias_keys(
        self,
        chapters: Dict[str, Dict[str, Any]],
        chapter_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> List[str]:
        target_id = canonical_chapter_id(chapter_id, title)
        target_identity = _chapter_identity({"id": target_id or chapter_id or "", "title": title or ""})
        aliases: List[str] = []
        for key, chapter in chapters.items():
            record = dict(chapter or {})
            record.setdefault("id", key)
            if key == chapter_id or key == target_id:
                aliases.append(key)
                continue
            if target_identity and _chapter_identity(record) == target_identity:
                aliases.append(key)
                continue
            if title and str(record.get("title") or "").strip() == str(title).strip():
                aliases.append(key)
        return aliases

    def _best_chapter_record(self, chapters: Dict[str, Dict[str, Any]], aliases: List[str]) -> Dict[str, Any]:
        records = [chapters[key] for key in aliases if isinstance(chapters.get(key), dict)]
        if not records:
            return {}
        return dict(max(records, key=_chapter_detail_score))

    def _store_chapter_record(
        self,
        chapters: Dict[str, Dict[str, Any]],
        chapter_id: str,
        record: Dict[str, Any],
        aliases: Optional[List[str]] = None,
    ) -> None:
        for alias in aliases or []:
            if alias != chapter_id:
                chapters.pop(alias, None)
        record["id"] = chapter_id
        chapters[chapter_id] = record

    def _ensure_backend_chapter_node(self, chapter: Dict[str, Any]) -> None:
        call_backend_tool(
            "add_memory",
            {
                "content": chapter.get("content") or chapter["title"],
                "type": "chapter",
                "metadata": {
                    "id": chapter["id"],
                    "label": chapter["title"],
                    "source": "frontend_test",
                    "chapter_id": chapter["id"],
                },
            },
        )

    def _ensure_backend_lecture_node(self, chapter: Dict[str, Any]) -> None:
        # Generated lecture scripts stay in chapter.lecture_content only.
        return

    def save_chapter(
        self,
        *,
        title: str,
        content: Optional[str] = None,
        graph_data: Optional[Dict[str, Any]] = None,
        chapter_id: Optional[str] = None,
        course_id: Optional[str] = None,
        source_type: Optional[str] = None,
        source_node_ids: Optional[List[str]] = None,
        source_scope: Optional[Dict[str, Any]] = None,
        ppt_slides: Optional[List[Dict[str, Any]]] = None,
        slide_lectures: Optional[List[Dict[str, Any]]] = None,
        tex_content: Optional[str] = None,
        editable_model: Optional[Dict[str, Any]] = None,
        asset_map: Optional[Dict[str, Any]] = None,
        rendered_pages: Optional[List[Dict[str, Any]]] = None,
        render_source: Optional[str] = None,
        render_error: Optional[str] = None,
        ppt_artifact: Optional[Dict[str, Any]] = None,
        ppt_source_node_ids: Optional[List[str]] = None,
        lecture_source_node_ids: Optional[List[str]] = None,
        lecture_target_duration_minutes: Optional[float] = None,
        lecture_speech_rate_cpm: Optional[int] = None,
        lecture_pacing: Optional[Dict[str, Any]] = None,
        sync_backend: bool = True,
    ) -> Dict[str, Any]:
        chapters = self._load_chapters()
        title_based_id = canonical_chapter_id(title=title)
        resolved_id = _resolve_chapter_storage_id(chapter_id, title) or f"chapter_{uuid.uuid4().hex[:8]}"
        self._clear_deleted_chapter_ids(chapter_id or "", resolved_id, title_based_id or "")
        aliases = list(
            dict.fromkeys(
                self._chapter_alias_keys(chapters, chapter_id or resolved_id, title)
                + self._chapter_alias_keys(chapters, resolved_id, title)
            )
        )
        record = self._best_chapter_record(chapters, aliases)
        previous_lecture_content = record.get("lecture_content")
        previous_slide_lectures = record.get("slide_lectures")
        record.update(
            {
                "id": resolved_id,
                "course_id": course_id if course_id is not None else record.get("course_id", ""),
                "title": title,
                "content": content if content is not None else record.get("content", ""),
                "graph_data": graph_data if graph_data is not None else record.get("graph_data"),
                "lecture_content": record.get("lecture_content"),
                "lecture_learning_plan": record.get("lecture_learning_plan"),
                "lecture_consistency_report": record.get("lecture_consistency_report"),
                "exercises": record.get("exercises"),
                "exercise_bank": record.get("exercise_bank", []),
                "approved_exercise_bank": record.get("approved_exercise_bank", []),
                "exercise_feedback": record.get("exercise_feedback", {}),
                "source_type": source_type if source_type is not None else record.get("source_type"),
                "source_node_ids": source_node_ids if source_node_ids is not None else record.get("source_node_ids"),
                "source_scope": source_scope if source_scope is not None else record.get("source_scope"),
                "ppt_slides": ppt_slides if ppt_slides is not None else record.get("ppt_slides"),
                "slide_lectures": slide_lectures if slide_lectures is not None else record.get("slide_lectures"),
                "tex_content": tex_content if tex_content is not None else record.get("tex_content"),
                "editable_model": editable_model if editable_model is not None else record.get("editable_model"),
                "asset_map": asset_map if asset_map is not None else record.get("asset_map"),
                "rendered_pages": rendered_pages if rendered_pages is not None else record.get("rendered_pages"),
                "render_source": render_source if render_source is not None else record.get("render_source"),
                "render_error": render_error if render_error is not None else record.get("render_error"),
                "ppt_artifact": ppt_artifact if ppt_artifact is not None else record.get("ppt_artifact"),
                "ppt_source_node_ids": ppt_source_node_ids if ppt_source_node_ids is not None else record.get("ppt_source_node_ids"),
                "lecture_source_node_ids": lecture_source_node_ids if lecture_source_node_ids is not None else record.get("lecture_source_node_ids"),
                "lecture_target_duration_minutes": lecture_target_duration_minutes if lecture_target_duration_minutes is not None else record.get("lecture_target_duration_minutes"),
                "lecture_speech_rate_cpm": lecture_speech_rate_cpm if lecture_speech_rate_cpm is not None else record.get("lecture_speech_rate_cpm"),
                "lecture_pacing": lecture_pacing if lecture_pacing is not None else record.get("lecture_pacing"),
                "created_at": record.get("created_at") or _now(),
                "updated_at": _now(),
            }
        )
        if content is not None and content != previous_lecture_content:
            _clear_tts_course_audio(resolved_id)
        if slide_lectures is not None and slide_lectures != previous_slide_lectures:
            _clear_tts_course_audio(resolved_id)
        self._store_chapter_record(chapters, resolved_id, record, aliases)
        self._save_chapters(chapters)
        if sync_backend:
            self._ensure_backend_chapter_node(record)
        if sync_backend and graph_data:
            import_graph_payload(graph_data)
        return record

    def save_lecture(
        self,
        *,
        chapter_id: str,
        course_id: Optional[str] = None,
        lecture_content: str,
        graph_data: Optional[Dict[str, Any]] = None,
        source_type: Optional[str] = None,
        source_node_ids: Optional[List[str]] = None,
        source_scope: Optional[Dict[str, Any]] = None,
        ppt_slides: Optional[List[Dict[str, Any]]] = None,
        slide_lectures: Optional[List[Dict[str, Any]]] = None,
        tex_content: Optional[str] = None,
        editable_model: Optional[Dict[str, Any]] = None,
        asset_map: Optional[Dict[str, Any]] = None,
        rendered_pages: Optional[List[Dict[str, Any]]] = None,
        render_source: Optional[str] = None,
        render_error: Optional[str] = None,
        ppt_artifact: Optional[Dict[str, Any]] = None,
        ppt_source_node_ids: Optional[List[str]] = None,
        lecture_source_node_ids: Optional[List[str]] = None,
        lecture_target_duration_minutes: Optional[float] = None,
        lecture_speech_rate_cpm: Optional[int] = None,
        lecture_pacing: Optional[Dict[str, Any]] = None,
        learning_plan: Optional[Dict[str, Any]] = None,
        consistency_report: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved_id = canonical_chapter_id(chapter_id) or chapter_id
        chapter = self.get_chapter(resolved_id) or self.get_chapter(chapter_id) or {
            "id": resolved_id,
            "title": _strip_chapter_prefix(chapter_id) or resolved_id,
            "content": "",
            "created_at": _now(),
        }
        resolved_id = canonical_chapter_id(resolved_id, chapter.get("title")) or resolved_id
        previous_lecture_content = chapter.get("lecture_content")
        chapter["lecture_content"] = lecture_content
        if course_id is not None:
            chapter["course_id"] = course_id
        chapter["updated_at"] = _now()
        if lecture_content != previous_lecture_content:
            _clear_tts_course_audio(resolved_id)
        if graph_data is not None:
            chapter["graph_data"] = graph_data
        if source_type is not None:
            chapter["source_type"] = source_type
        if source_node_ids is not None:
            chapter["source_node_ids"] = source_node_ids
        if source_scope is not None:
            chapter["source_scope"] = source_scope
        if ppt_slides is not None:
            chapter["ppt_slides"] = ppt_slides
        if slide_lectures is not None:
            if slide_lectures != chapter.get("slide_lectures"):
                _clear_tts_course_audio(resolved_id)
            chapter["slide_lectures"] = slide_lectures
        if tex_content is not None:
            chapter["tex_content"] = tex_content
        if editable_model is not None:
            chapter["editable_model"] = editable_model
        if asset_map is not None:
            chapter["asset_map"] = asset_map
        if rendered_pages is not None:
            chapter["rendered_pages"] = rendered_pages
        if render_source is not None:
            chapter["render_source"] = render_source
        if render_error is not None:
            chapter["render_error"] = render_error
        if ppt_artifact is not None:
            chapter["ppt_artifact"] = ppt_artifact
        if ppt_source_node_ids is not None:
            chapter["ppt_source_node_ids"] = ppt_source_node_ids
        if lecture_source_node_ids is not None:
            chapter["lecture_source_node_ids"] = lecture_source_node_ids
        if lecture_target_duration_minutes is not None:
            chapter["lecture_target_duration_minutes"] = lecture_target_duration_minutes
        if lecture_speech_rate_cpm is not None:
            chapter["lecture_speech_rate_cpm"] = lecture_speech_rate_cpm
        if lecture_pacing is not None:
            chapter["lecture_pacing"] = lecture_pacing
        if learning_plan is not None:
            chapter["lecture_learning_plan"] = learning_plan
        if consistency_report is not None:
            chapter["lecture_consistency_report"] = consistency_report
        saved = self.save_chapter(
            title=chapter["title"],
            content=chapter.get("content", ""),
            graph_data=chapter.get("graph_data"),
            chapter_id=resolved_id,
            course_id=chapter.get("course_id"),
            source_type=chapter.get("source_type"),
            source_node_ids=chapter.get("source_node_ids"),
            source_scope=chapter.get("source_scope"),
            ppt_slides=chapter.get("ppt_slides"),
            slide_lectures=chapter.get("slide_lectures"),
            tex_content=chapter.get("tex_content"),
            editable_model=chapter.get("editable_model"),
            asset_map=chapter.get("asset_map"),
            rendered_pages=chapter.get("rendered_pages"),
            render_source=chapter.get("render_source"),
            render_error=chapter.get("render_error"),
            ppt_artifact=chapter.get("ppt_artifact"),
            ppt_source_node_ids=chapter.get("ppt_source_node_ids"),
            lecture_source_node_ids=chapter.get("lecture_source_node_ids"),
            lecture_target_duration_minutes=chapter.get("lecture_target_duration_minutes"),
            lecture_speech_rate_cpm=chapter.get("lecture_speech_rate_cpm"),
            lecture_pacing=chapter.get("lecture_pacing"),
            sync_backend=False,
        )
        saved["lecture_content"] = lecture_content
        saved["lecture_learning_plan"] = chapter.get("lecture_learning_plan")
        saved["lecture_consistency_report"] = chapter.get("lecture_consistency_report")
        saved["exercises"] = chapter.get("exercises")
        saved["exercise_bank"] = chapter.get("exercise_bank", [])
        saved["approved_exercise_bank"] = chapter.get("approved_exercise_bank", saved.get("approved_exercise_bank", []))
        saved["exercise_feedback"] = chapter.get("exercise_feedback", saved.get("exercise_feedback", {}))
        saved["source_type"] = chapter.get("source_type", saved.get("source_type"))
        saved["source_node_ids"] = chapter.get("source_node_ids", saved.get("source_node_ids"))
        saved["source_scope"] = chapter.get("source_scope", saved.get("source_scope"))
        saved["ppt_slides"] = chapter.get("ppt_slides", saved.get("ppt_slides"))
        saved["slide_lectures"] = chapter.get("slide_lectures", saved.get("slide_lectures"))
        saved["tex_content"] = chapter.get("tex_content", saved.get("tex_content"))
        saved["editable_model"] = chapter.get("editable_model", saved.get("editable_model"))
        saved["asset_map"] = chapter.get("asset_map", saved.get("asset_map"))
        saved["rendered_pages"] = chapter.get("rendered_pages", saved.get("rendered_pages"))
        saved["render_source"] = chapter.get("render_source", saved.get("render_source"))
        saved["render_error"] = chapter.get("render_error", saved.get("render_error"))
        saved["ppt_artifact"] = chapter.get("ppt_artifact", saved.get("ppt_artifact"))
        saved["ppt_source_node_ids"] = chapter.get("ppt_source_node_ids", saved.get("ppt_source_node_ids"))
        saved["lecture_source_node_ids"] = chapter.get("lecture_source_node_ids", saved.get("lecture_source_node_ids"))
        saved["lecture_target_duration_minutes"] = chapter.get("lecture_target_duration_minutes", saved.get("lecture_target_duration_minutes"))
        saved["lecture_speech_rate_cpm"] = chapter.get("lecture_speech_rate_cpm", saved.get("lecture_speech_rate_cpm"))
        saved["lecture_pacing"] = chapter.get("lecture_pacing", saved.get("lecture_pacing"))
        chapters = self._load_chapters()
        aliases = self._chapter_alias_keys(chapters, chapter_id, saved.get("title"))
        self._store_chapter_record(chapters, saved["id"], saved, aliases)
        self._save_chapters(chapters)
        return saved

    def delete_chapter(self, chapter_id: str) -> Dict[str, Any]:
        chapters = self._load_chapters()
        chapter = self.get_chapter(chapter_id)
        title = chapter.get("title") if isinstance(chapter, dict) else None
        aliases = self._chapter_alias_keys(chapters, chapter_id, title)
        if not aliases and chapter_id in chapters:
            aliases = [chapter_id]
        removed: List[str] = []
        for alias in aliases:
            if alias in chapters:
                chapters.pop(alias, None)
                removed.append(alias)
        markers = self._mark_chapter_deleted(chapter_id, chapter)
        self._save_chapters(chapters)
        for alias in set(removed).union(markers).union({chapter_id}):
            _clear_tts_course_audio(alias)

        for alias in set(removed).union(markers):
            try:
                call_backend_tool("delete_memory", {"node_id": alias})
            except Exception:
                pass

        progress = self._load_progress()
        for student_payload in progress.values():
            if not isinstance(student_payload, dict):
                continue
            for key in ("chapter_progress", "learned_chapters"):
                records = student_payload.get(key)
                if isinstance(records, dict):
                    for alias in set(removed).union(markers):
                        records.pop(alias, None)

        self._save_progress(progress)
        return {"success": bool(removed or markers), "deleted_ids": sorted(set(removed).union(markers)), "chapter_id": chapter_id}

    def save_exercise_bank(
        self,
        *,
        chapter_id: str,
        exercises: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        resolved_id = canonical_chapter_id(chapter_id) or chapter_id
        chapter = self.get_chapter(resolved_id) or self.get_chapter(chapter_id) or {
            "id": resolved_id,
            "title": _strip_chapter_prefix(chapter_id) or resolved_id,
            "content": "",
            "created_at": _now(),
        }
        resolved_id = canonical_chapter_id(resolved_id, chapter.get("title")) or resolved_id
        clean_bank = [item for item in exercises if isinstance(item, dict)]
        chapter["exercise_bank"] = clean_bank
        chapter["exercises"] = clean_bank[0] if clean_bank else None
        chapter["approved_exercise_bank"] = chapter.get("approved_exercise_bank", [])
        chapter["exercise_feedback"] = chapter.get("exercise_feedback", {})
        chapter["updated_at"] = _now()

        saved = self.save_chapter(
            title=chapter["title"],
            content=chapter.get("content", ""),
            graph_data=chapter.get("graph_data"),
            chapter_id=resolved_id,
            sync_backend=False,
        )
        saved["exercise_bank"] = clean_bank
        saved["exercises"] = chapter["exercises"]
        saved["approved_exercise_bank"] = chapter.get("approved_exercise_bank", saved.get("approved_exercise_bank", []))
        saved["exercise_feedback"] = chapter.get("exercise_feedback", saved.get("exercise_feedback", {}))
        chapters = self._load_chapters()
        aliases = self._chapter_alias_keys(chapters, chapter_id, saved.get("title"))
        self._store_chapter_record(chapters, saved["id"], saved, aliases)
        self._save_chapters(chapters)
        return saved

    def save_approved_exercise(
        self,
        *,
        chapter_id: str,
        exercise: Dict[str, Any],
        feedback_key: str,
        approved: bool,
    ) -> Dict[str, Any]:
        chapters = self._load_chapters()
        resolved_id = canonical_chapter_id(chapter_id) or chapter_id
        chapter = chapters.get(resolved_id) or self.get_chapter(resolved_id) or self.get_chapter(chapter_id) or {
            "id": resolved_id,
            "title": _strip_chapter_prefix(chapter_id) or resolved_id,
            "content": "",
            "created_at": _now(),
        }
        resolved_id = canonical_chapter_id(resolved_id, chapter.get("title")) or resolved_id
        approved_bank = chapter.get("approved_exercise_bank")
        if not isinstance(approved_bank, list):
            approved_bank = []

        exercise_id = str((exercise or {}).get("id") or "")
        exercise_question = str((exercise or {}).get("question") or "").strip()

        def item_matches(item: Dict[str, Any]) -> bool:
            if not isinstance(item, dict):
                return True
            keys = {
                str(item.get("approval_key") or ""),
                str(item.get("feedback_key") or ""),
                str(item.get("id") or ""),
            }
            if feedback_key and feedback_key in keys:
                return True
            if exercise_id and exercise_id in keys:
                return True
            if exercise_question and str(item.get("question") or "").strip() == exercise_question:
                return True
            return False

        approved_bank = [
            item for item in approved_bank
            if not item_matches(item)
        ]
        if approved and isinstance(exercise, dict):
            approved_item = dict(exercise)
            approved_item["approval_key"] = feedback_key
            approved_item["approved_at"] = _now()
            approved_bank.append(approved_item)

        chapter["approved_exercise_bank"] = approved_bank
        chapter["updated_at"] = _now()
        chapter["id"] = resolved_id
        aliases = self._chapter_alias_keys(chapters, chapter_id, chapter.get("title"))
        self._store_chapter_record(chapters, resolved_id, chapter, aliases)
        self._save_chapters(chapters)
        return chapter

    def save_exercise_feedback(
        self,
        *,
        chapter_id: str,
        feedback_key: str,
        rating: str,
        exercise_id: Optional[str] = None,
        question: Optional[str] = None,
        note: Optional[str] = None,
        scope: str = "exercise",
        option_key: Optional[str] = None,
        option_text: Optional[str] = None,
        parent_feedback_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        chapters = self._load_chapters()
        resolved_id = canonical_chapter_id(chapter_id) or chapter_id
        chapter = chapters.get(resolved_id) or self.get_chapter(resolved_id) or self.get_chapter(chapter_id) or {
            "id": resolved_id,
            "title": _strip_chapter_prefix(chapter_id) or resolved_id,
            "content": "",
            "created_at": _now(),
        }
        resolved_id = canonical_chapter_id(resolved_id, chapter.get("title")) or resolved_id
        feedback = chapter.get("exercise_feedback")
        if not isinstance(feedback, dict):
            feedback = {}

        normalized_rating = str(rating or "").strip().lower()
        if normalized_rating in {"clear", "none", "neutral", ""}:
            feedback.pop(feedback_key, None)
        else:
            feedback[feedback_key] = {
                "rating": normalized_rating,
                "scope": scope or "exercise",
                "exercise_id": exercise_id or "",
                "question": question or "",
                "option_key": option_key or "",
                "option_text": option_text or "",
                "parent_feedback_key": parent_feedback_key or "",
                "note": note or "",
                "updated_at": _now(),
            }

        chapter["exercise_feedback"] = feedback
        chapter["id"] = resolved_id
        chapter["updated_at"] = _now()
        aliases = self._chapter_alias_keys(chapters, chapter_id, chapter.get("title"))
        self._store_chapter_record(chapters, resolved_id, chapter, aliases)
        self._save_chapters(chapters)
        return chapter

    def list_chapters(self, course_id: Optional[str] = None) -> List[Dict[str, Any]]:
        chapters = self._load_chapters()
        deleted = self._load_deleted_chapter_ids()
        course_filter = str(course_id).strip() if course_id is not None else ""
        merged_chapters: Dict[str, Dict[str, Any]] = {}
        changed = False
        for key, chapter in chapters.items():
            if not isinstance(chapter, dict):
                continue
            if _is_placeholder_chapter({**chapter, "id": chapter.get("id") or key}):
                continue
            if _is_empty_shell_chapter({**chapter, "id": chapter.get("id") or key}):
                changed = True
                continue
            record = dict(chapter)
            resolved_id = canonical_chapter_id(record.get("id") or key, record.get("title")) or str(record.get("id") or key)
            record["id"] = resolved_id
            current = merged_chapters.get(resolved_id)
            if current is None or _chapter_detail_score(record) > _chapter_detail_score(current):
                merged_chapters[resolved_id] = record
        if course_filter:
            records = [
                record
                for record in merged_chapters.values()
                if str(record.get("course_id") or "").strip() == course_filter
            ]
            records = _dedupe_chapters(records)
            records.sort(key=_chapter_sort_value, reverse=True)
            return records
        try:
            graph = build_frontend_graph()
        except Exception:
            graph = {"nodes": []}
        for node in graph.get("nodes", []):
            if node.get("type") != "chapter":
                continue
            if _is_graph_chapter_list_shell(node):
                continue
            chapter_id = canonical_chapter_id(str(node.get("id")), _node_label(node)) or str(node.get("id"))
            if chapter_id in deleted or str(node.get("id") or "") in deleted:
                continue
            if chapter_id in merged_chapters:
                continue
            merged_chapters[chapter_id] = {
                "id": chapter_id,
                "title": _node_label(node),
                "content": node.get("content") or "",
                "graph_data": None,
                "lecture_content": None,
                "exercises": None,
                "exercise_bank": [],
                "approved_exercise_bank": [],
                "exercise_feedback": {},
                "created_at": node.get("created_at") or _now(),
                "updated_at": node.get("updated_at") or _now(),
            }
            changed = True
        if changed or set(merged_chapters.keys()) != set(chapters.keys()):
            try:
                self._save_chapters(merged_chapters)
            except OSError:
                pass
        records = _dedupe_chapters(list(merged_chapters.values()))
        records.sort(key=_chapter_sort_value, reverse=True)
        return records

    def get_chapter(self, chapter_id: str) -> Optional[Dict[str, Any]]:
        chapters = self._load_chapters()
        deleted = self._load_deleted_chapter_ids()
        resolved_id = canonical_chapter_id(chapter_id) or chapter_id
        if chapter_id in deleted or resolved_id in deleted:
            return None
        chapter = chapters.get(resolved_id) or chapters.get(chapter_id)
        if chapter:
            identity = _chapter_identity({**chapter, "id": chapter.get("id") or resolved_id})
            aliases = [
                candidate
                for key, candidate in chapters.items()
                if _chapter_identity({**candidate, "id": candidate.get("id") or key}) == identity
            ]
            if aliases:
                record = dict(max(aliases, key=_chapter_detail_score))
                record["id"] = canonical_chapter_id(record.get("id") or resolved_id, record.get("title")) or resolved_id
                return record
            record = dict(chapter)
            record["id"] = resolved_id
            return record

        node = call_backend_tool("get_node", {"node_id": resolved_id})
        if not (isinstance(node, dict) and node.get("id")) and resolved_id != chapter_id:
            node = call_backend_tool("get_node", {"node_id": chapter_id})
        if isinstance(node, dict) and node.get("id"):
            resolved_id = canonical_chapter_id(str(node.get("id") or resolved_id), _node_label(node)) or resolved_id
            if resolved_id in deleted or str(node.get("id") or "") in deleted:
                return None
            return {
                "id": resolved_id,
                "title": _node_label(node),
                "content": node.get("content") or "",
                "graph_data": None,
                "lecture_content": None,
                "exercises": None,
                "exercise_bank": [],
                "approved_exercise_bank": [],
                "exercise_feedback": {},
                "created_at": node.get("created_at") or _now(),
                "updated_at": node.get("updated_at") or _now(),
            }
        return None

    def _normalize_student_progress(self, student_progress: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(student_progress, dict):
            student_progress = {}
        chapter_progress = student_progress.setdefault("chapter_progress", {})
        if not isinstance(chapter_progress, dict):
            chapter_progress = {}
            student_progress["chapter_progress"] = chapter_progress
        learned = student_progress.setdefault("learned_chapters", {})
        if isinstance(learned, dict):
            for raw_chapter_id, learned_at in learned.items():
                chapter_id = canonical_chapter_id(str(raw_chapter_id)) or str(raw_chapter_id)
                record = chapter_progress.setdefault(chapter_id, {})
                record.setdefault("status", "learned")
                record.setdefault("learned_at", learned_at)
                record.setdefault("review_status", "learned")
                record.setdefault("correct_count", 0)
                record.setdefault("wrong_count", 0)
        else:
            student_progress["learned_chapters"] = {}
        return student_progress

    def _chapter_progress_record(self, student_progress: Dict[str, Any], chapter_id: str) -> Dict[str, Any]:
        student_progress = self._normalize_student_progress(student_progress)
        records = student_progress.setdefault("chapter_progress", {})
        record = records.setdefault(chapter_id, {})
        record.setdefault("status", "unlearned")
        record.setdefault("review_status", record.get("status") or "unlearned")
        record.setdefault("correct_count", 0)
        record.setdefault("wrong_count", 0)
        return record

    def mark_chapter_status(self, chapter_id: str, student_id: str = "student_001", status: str = "learned") -> Dict[str, Any]:
        chapter_id = canonical_chapter_id(chapter_id) or chapter_id
        status = (status or "learned").strip().lower()
        if status not in {"learned", "reviewing", "forgotten", "reset"}:
            status = "learned"
        progress = self._load_progress()
        student_progress = self._normalize_student_progress(progress.setdefault(student_id, {}))
        learned = student_progress.setdefault("learned_chapters", {})

        if status == "reset":
            student_progress.setdefault("chapter_progress", {}).pop(chapter_id, None)
            if isinstance(learned, dict):
                learned.pop(chapter_id, None)
            student_progress["updated_at"] = _now()
            progress[student_id] = student_progress
            self._save_progress(progress)
            return {"student_id": student_id, "chapter_id": chapter_id, "updated_at": student_progress["updated_at"], "progress": None}

        record = self._chapter_progress_record(student_progress, chapter_id)
        now = _now()
        record["status"] = status
        record["review_status"] = status
        record["updated_at"] = now
        if status == "learned":
            record["learned_at"] = now
            if isinstance(learned, dict):
                learned[chapter_id] = now
        elif status in {"reviewing", "forgotten"}:
            record["review_requested_at"] = now
            if status == "forgotten":
                record["forgotten_at"] = now

        student_progress["updated_at"] = now
        progress[student_id] = student_progress
        self._save_progress(progress)
        return {"student_id": student_id, "chapter_id": chapter_id, "updated_at": now, "learned_at": record.get("learned_at"), "progress": record}

    def mark_learned(self, chapter_id: str, student_id: str = "student_001") -> Dict[str, Any]:
        return self.mark_chapter_status(chapter_id, student_id, "learned")

    def record_practice_result(self, chapter_id: str, *, is_correct: bool, student_id: str = "student_001") -> Dict[str, Any]:
        chapter_id = canonical_chapter_id(chapter_id) or chapter_id
        progress = self._load_progress()
        student_progress = self._normalize_student_progress(progress.setdefault(student_id, {}))
        record = self._chapter_progress_record(student_progress, chapter_id)
        if is_correct:
            record["correct_count"] = int(record.get("correct_count") or 0) + 1
            if record.get("status") in {"forgotten", "reviewing", "unlearned"}:
                record["status"] = "reviewing"
                record["review_status"] = "reviewing"
        else:
            record["wrong_count"] = int(record.get("wrong_count") or 0) + 1
            record["status"] = "forgotten"
            record["review_status"] = "forgotten"
            record["forgotten_at"] = _now()
        record["last_practiced_at"] = _now()
        record["updated_at"] = record["last_practiced_at"]
        student_progress["updated_at"] = record["updated_at"]
        progress[student_id] = student_progress
        self._save_progress(progress)
        return {"student_id": student_id, "chapter_id": chapter_id, "progress": record}

    def reset_progress(self, student_id: str = "student_001", chapter_id: Optional[str] = None) -> Dict[str, Any]:
        progress = self._load_progress()
        student_progress = self._normalize_student_progress(progress.setdefault(student_id, {}))
        if chapter_id:
            resolved_id = canonical_chapter_id(chapter_id) or chapter_id
            student_progress.setdefault("chapter_progress", {}).pop(resolved_id, None)
            learned = student_progress.setdefault("learned_chapters", {})
            if isinstance(learned, dict):
                learned.pop(resolved_id, None)
            target = resolved_id
        else:
            student_progress["chapter_progress"] = {}
            student_progress["learned_chapters"] = {}
            target = None
        student_progress["updated_at"] = _now()
        progress[student_id] = student_progress
        self._save_progress(progress)
        return {"student_id": student_id, "chapter_id": target, "progress": self.progress(student_id)["progress"]}

    def progress(self, student_id: str = "student_001") -> Dict[str, Any]:
        chapters = self.list_chapters()
        all_progress = self._load_progress()
        student_progress = self._normalize_student_progress(all_progress.get(student_id, {}))
        learned = student_progress.get("learned_chapters", {})
        chapter_progress = student_progress.get("chapter_progress", {})
        learned_ids = set(learned.keys()) if isinstance(learned, dict) else set()
        total = len(chapters)
        learned_count = len([chapter for chapter in chapters if chapter["id"] in learned_ids])
        progress_percentage = round((learned_count / total) * 100, 2) if total else 0.0
        forgotten_count = len([item for item in chapter_progress.values() if isinstance(item, dict) and item.get("status") == "forgotten"])
        reviewing_count = len([item for item in chapter_progress.values() if isinstance(item, dict) and item.get("status") == "reviewing"])

        return {
            "progress": {
                "total_chapters": total,
                "learned_chapters": learned_count,
                "reviewing_chapters": reviewing_count,
                "forgotten_chapters": forgotten_count,
                "progress_percentage": progress_percentage,
                "chapters": chapter_progress,
            },
        }

    def review(self, student_id: str = "student_001", chapter_id: Optional[str] = None) -> Dict[str, Any]:
        chapters = self.list_chapters()
        progress_payload = self.progress(student_id)
        progress = progress_payload["progress"]
        chapter_progress = progress.get("chapters", {})

        scored: List[tuple[float, Dict[str, Any], Dict[str, Any]]] = []
        for chapter in chapters:
            record = chapter_progress.get(chapter["id"], {}) if isinstance(chapter_progress, dict) else {}
            score = self._review_score(chapter, record)
            scored.append((score, chapter, record))
        scored.sort(key=lambda item: item[0], reverse=True)

        queue = [
            {
                "chapter_id": chapter["id"],
                "title": chapter.get("title") or chapter["id"],
                "status": record.get("status") or "unlearned",
                "correct_count": int(record.get("correct_count") or 0),
                "wrong_count": int(record.get("wrong_count") or 0),
                "learned_at": record.get("learned_at"),
                "last_practiced_at": record.get("last_practiced_at"),
                "reason": self._review_reason(chapter, record),
                "priority": round(score, 2),
            }
            for score, chapter, record in scored[:8]
            if score > 0
        ]

        recommendations = [
            {
                "type": item["status"] if item["status"] != "unlearned" else "推荐学习",
                "content": f"{item['reason']}：《{item['title']}》",
                "chapter_id": item["chapter_id"],
            }
            for item in queue[:4]
        ]

        selected = self.get_chapter(chapter_id) if chapter_id else None
        path = []
        if selected:
            path = [
                item["title"]
                for item in queue
                if item["chapter_id"] != selected["id"]
            ][:3]
            path = [selected.get("title") or selected["id"], *path]

        return {
            "progress": progress,
            "recommendations": recommendations,
            "queue": queue,
            "chapter": selected,
            "path": path,
            "nodes": path,
        }

    def _review_score(self, chapter: Dict[str, Any], record: Dict[str, Any]) -> float:
        if not isinstance(record, dict) or not record:
            return 40.0
        status = record.get("status")
        score = 0.0
        if status == "forgotten":
            score += 120.0
        elif status == "reviewing":
            score += 90.0
        elif status == "learned":
            score += 52.0
        wrong_count = int(record.get("wrong_count") or 0)
        correct_count = int(record.get("correct_count") or 0)
        score += wrong_count * 18
        score -= min(correct_count * 4, 24)
        last_practiced = _timestamp_value(record.get("last_practiced_at") or record.get("learned_at"))
        if last_practiced:
            age_days = max(0.0, (datetime.now().timestamp() - last_practiced) / 86400)
            score += min(age_days * 2.5, 45)
        if chapter.get("exercise_bank"):
            score += 6
        return score

    def _review_reason(self, chapter: Dict[str, Any], record: Dict[str, Any]) -> str:
        if not isinstance(record, dict) or not record:
            return "尚未学习，适合加入学习起点"
        if record.get("status") == "forgotten":
            return "你标记为已忘记或最近答错，优先重新学习"
        if int(record.get("wrong_count") or 0) > int(record.get("correct_count") or 0):
            return "错题次数偏多，建议先复盘"
        if record.get("status") == "reviewing":
            return "已放回复习队列，建议巩固"
        return "已学完但需要间隔复习"


chapter_store = ChapterStore(CHAPTERS_FILE, PROGRESS_FILE)
