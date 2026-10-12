"""Source builders for structured sync."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from KGTS.core.graph_service import normalize_relation_type

from KGTS.maintenance.book_outline import (
    APPENDICES_PART_ID,
    APPENDICES_PART_LABEL,
    BOOK_PARTS,
    BOOK_TITLE,
    CANONICAL_CHAPTER_TITLES,
    appendix_number,
    part_for_chapter,
)
from KGTS.maintenance.toc_fusion import (
    TocFusion,
    build_structured_units,
    fuse_toc_with_structured_units,
)
from KGTS.maintenance.sync_utils import (
    SourceSpec,
    PROJECT_ROOT,
    STRUCTURED_DIR,
    TOC_EXPORT_DIR,
    _chapter_label,
    _chapter_node_id,
    _clean_label,
    _expand_formula_references,
    _node_payload,
    _parse_all_references,
    _relation_payload,
    _section_node_id,
    _sha256_file,
    resolve_structured_data_dir,
)


TOC_ROOT_NODE_ID = "toc::root"
_TOC_CHAPTER_NODE_BY_STRUCTURED_CHAPTER: Dict[str, str] = {}
_TOC_NODE_BY_STRUCTURED_UNIT: Dict[str, str] = {}
_TOC_METADATA_BY_NODE_ID: Dict[str, Dict[str, Any]] = {}
_TOC_HAS_INDEXED_SOURCE = False
_TOC_FUSION: Optional[TocFusion] = None
_TOC_CHILDREN_BY_NODE_ID: Dict[str, List[str]] = {}
_STRUCTURED_SECTION_BY_SOURCE_UNIT: Dict[str, str] = {}
_STRUCTURED_SECTION_BY_CHAPTER_HEADING: Dict[str, str] = {}
_STRUCTURED_SECTION_TO_TOC_NODE: Dict[str, str] = {}
_RESOURCE_CONTAINER_BY_NODE_ID: Dict[str, str] = {}
_STRUCTURED_CHAPTERS_WITH_HEADINGS: set[str] = set()


def _reset_toc_index() -> None:
    global _TOC_HAS_INDEXED_SOURCE, _TOC_FUSION
    _TOC_CHAPTER_NODE_BY_STRUCTURED_CHAPTER.clear()
    _TOC_NODE_BY_STRUCTURED_UNIT.clear()
    _TOC_METADATA_BY_NODE_ID.clear()
    _TOC_CHILDREN_BY_NODE_ID.clear()
    _STRUCTURED_SECTION_TO_TOC_NODE.clear()
    _RESOURCE_CONTAINER_BY_NODE_ID.clear()
    _TOC_HAS_INDEXED_SOURCE = False
    _TOC_FUSION = None


def _reference_chapter(default_chapter: str, reference_id: str) -> str:
    reference_id = str(reference_id or "").strip()
    appendix_match = re.match(r"^A0*([0-9]+)\.", reference_id, flags=re.I)
    if appendix_match:
        return f"appendix{int(appendix_match.group(1))}"
    chapter_match = re.match(r"^0*([0-9]+)\.", reference_id)
    if chapter_match:
        return f"chapter{int(chapter_match.group(1))}"
    return default_chapter


def _toc_node_id(raw_id: str) -> str:
    return f"toc::{str(raw_id or '').strip()}"


def _normalize_source_name(value: str) -> str:
    name = unicodedata.normalize("NFC", str(value or "").strip())
    if not name:
        return name
    if any(marker in name for marker in ("Ŀ", "¼", "Ä", "Â")):
        try:
            repaired = name.encode("utf-8").decode("gbk")
        except UnicodeError:
            repaired = ""
        if repaired and re.search(r"[\u4e00-\u9fff]", repaired):
            return unicodedata.normalize("NFC", repaired)
    return name


def _source_name(path: Path) -> str:
    return _normalize_source_name(path.name)


def _project_relative_path(path: Path) -> str:
    try:
        return _normalize_source_name(str(path.resolve().relative_to(Path.cwd().resolve())))
    except ValueError:
        try:
            return _normalize_source_name(str(path.resolve().relative_to(resolve_structured_data_dir(STRUCTURED_DIR).parent.resolve())))
        except ValueError:
            return _source_name(path)


def _structured_chapter_sort_key(chapter: str) -> tuple[int, str]:
    match = re.fullmatch(r"chapter0*([0-9]+)", str(chapter or "").strip(), flags=re.I)
    if match:
        return (int(match.group(1)), chapter)
    match = re.fullmatch(r"appendix0*([0-9]+)", str(chapter or "").strip(), flags=re.I)
    if match:
        return (1000 + int(match.group(1)), chapter)
    return (10000, chapter)


def _structured_chapter_to_toc_node_id(chapter: str) -> Optional[str]:
    return _TOC_CHAPTER_NODE_BY_STRUCTURED_CHAPTER.get(str(chapter or "").strip().lower())


def _structured_unit_to_toc_node_id(unit_id: str) -> Optional[str]:
    return _TOC_NODE_BY_STRUCTURED_UNIT.get(str(unit_id or "").strip().lower())


def _toc_metadata_for_node_id(node_id: Optional[str]) -> Dict[str, Any]:
    return dict(_TOC_METADATA_BY_NODE_ID.get(str(node_id or "").strip()) or {})


def _toc_children_for_node_id(node_id: Optional[str]) -> List[str]:
    return list(_TOC_CHILDREN_BY_NODE_ID.get(str(node_id or "").strip()) or [])


def _structured_heading_key(chapter: str, heading: Any) -> str:
    normalized_chapter = str(chapter or "").strip().lower()
    normalized_heading = _normalize_toc_match_text(heading)
    return f"{normalized_chapter}\0{normalized_heading}" if normalized_chapter and normalized_heading else ""


def _index_structured_section_lookup(source_paths: List[Path]) -> None:
    _STRUCTURED_SECTION_BY_SOURCE_UNIT.clear()
    _STRUCTURED_SECTION_BY_CHAPTER_HEADING.clear()
    _STRUCTURED_CHAPTERS_WITH_HEADINGS.clear()
    for path in source_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        chapter = str(metadata.get("chapter") or path.stem.split("_")[0]).strip()
        heading_path = _heading_path_from_metadata(metadata)
        if not chapter or not heading_path:
            continue
        _STRUCTURED_CHAPTERS_WITH_HEADINGS.add(chapter.strip().lower())
        deepest_section_id = _section_node_id(chapter, heading_path)
        source_unit = str(payload.get("id") or path.stem).strip()
        for key in (source_unit, path.name, path.stem):
            normalized = str(key or "").strip().lower()
            if normalized:
                _STRUCTURED_SECTION_BY_SOURCE_UNIT[normalized] = deepest_section_id
        for depth, heading in enumerate(heading_path, start=1):
            key = _structured_heading_key(chapter, heading)
            if key:
                _STRUCTURED_SECTION_BY_CHAPTER_HEADING.setdefault(
                    key,
                    _section_node_id(chapter, heading_path[:depth]),
                )


def _resource_container_node_id(
    chapter: str,
    *,
    source_unit: Any = None,
    source_file: Any = None,
    subsection: Any = None,
) -> str:
    candidates = [
        str(source_unit or "").strip(),
        str(source_file or "").strip(),
    ]
    for value in list(candidates):
        if value:
            candidates.append(Path(value).name)
            candidates.append(Path(value).stem)
    for candidate in candidates:
        section_id = _STRUCTURED_SECTION_BY_SOURCE_UNIT.get(candidate.lower())
        if section_id:
            return section_id
    section_id = _STRUCTURED_SECTION_BY_CHAPTER_HEADING.get(_structured_heading_key(chapter, subsection))
    if section_id:
        return section_id
    return _chapter_container_node_id(chapter)


def _resource_toc_container_node_id(
    chapter: str,
    fallback_container_node_id: str,
    *,
    source_unit: Any = None,
    source_file: Any = None,
    subsection: Any = None,
) -> str:
    if not _has_toc_export_outline():
        return fallback_container_node_id
    candidates = [
        str(source_unit or "").strip(),
        str(source_file or "").strip(),
    ]
    for value in list(candidates):
        if value:
            candidates.append(Path(value).name)
            candidates.append(Path(value).stem)
    for candidate in candidates:
        structured_section_id = _STRUCTURED_SECTION_BY_SOURCE_UNIT.get(candidate.lower())
        toc_node_id = _STRUCTURED_SECTION_TO_TOC_NODE.get(structured_section_id or "")
        if toc_node_id:
            return toc_node_id
        direct_toc_id = _structured_unit_to_toc_node_id(candidate)
        if direct_toc_id and direct_toc_id != _structured_chapter_to_toc_node_id(chapter):
            return direct_toc_id
    structured_section_id = _STRUCTURED_SECTION_BY_CHAPTER_HEADING.get(_structured_heading_key(chapter, subsection))
    toc_node_id = _STRUCTURED_SECTION_TO_TOC_NODE.get(structured_section_id or "")
    if toc_node_id:
        return toc_node_id
    return fallback_container_node_id


def _has_toc_export_outline() -> bool:
    return _TOC_FUSION is not None and bool(_TOC_FUSION.toc_by_id)


def _chapter_has_structured_headings(chapter: str) -> bool:
    return str(chapter or "").strip().lower() in _STRUCTURED_CHAPTERS_WITH_HEADINGS


def _export_toc_part_node_id_for_chapter(chapter: str) -> Optional[str]:
    if not _TOC_FUSION:
        return None
    title = ""
    part = part_for_chapter(chapter)
    if part is not None:
        title = part.label
    elif _structured_appendix_number(chapter) is not None:
        title = APPENDICES_PART_LABEL
    if not title:
        return None
    for toc_id, item in _TOC_FUSION.toc_by_id.items():
        if str(item.get("entry_type") or "").strip().lower() != "part":
            continue
        if _normalize_toc_match_text(item.get("title")) == _normalize_toc_match_text(title):
            return _toc_node_id(toc_id)
    return None


def _chapter_container_node_id(chapter: str) -> str:
    return _chapter_node_id(chapter)


def _canonical_chapter_title(chapter: str, fallback: Optional[str] = None) -> str:
    title = CANONICAL_CHAPTER_TITLES.get(str(chapter or "").strip().lower())
    if title:
        return title
    return str(fallback or chapter)


def _toc_entry_node_type(entry_type: str) -> str:
    normalized = str(entry_type or "").strip().lower()
    if normalized == "part":
        return "part"
    if normalized == "appendix":
        return "appendix"
    return "section"


def _normalize_toc_match_text(value: Any) -> str:
    text = re.sub(r"\$[^$]*\$", " ", str(value or ""))
    text = text.lower().replace("introduction", " ")
    text = re.sub(r"\bchapter\s*[0-9]+\b", " ", text)
    text = re.sub(r"^[0-9]+[.]\s*", "", text)
    text = re.sub(r"\b[ivx]+[.]\s*", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _toc_title_score(query: str, title: str) -> float:
    left = _normalize_toc_match_text(query)
    right = _normalize_toc_match_text(title)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_terms = set(left.split())
    right_terms = set(right.split())
    if not left_terms or not right_terms:
        return 0.0
    query_coverage = len(left_terms & right_terms) / len(left_terms)
    target_coverage = len(left_terms & right_terms) / len(right_terms)
    harmonic = (
        (2 * query_coverage * target_coverage) / (query_coverage + target_coverage)
        if query_coverage + target_coverage
        else 0.0
    )
    sequence = SequenceMatcher(None, left, right).ratio()
    contains = 0.96 if (left in right or right in left) and min(len(left_terms), len(right_terms)) >= 3 else 0.0
    return max(harmonic, sequence, contains)


def _toc_title_token_score(query: str, title: str) -> float:
    left = _normalize_toc_match_text(query)
    right = _normalize_toc_match_text(title)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_terms = set(left.split())
    right_terms = set(right.split())
    if not left_terms or not right_terms:
        return 0.0
    query_coverage = len(left_terms & right_terms) / len(left_terms)
    target_coverage = len(left_terms & right_terms) / len(right_terms)
    harmonic = (
        (2 * query_coverage * target_coverage) / (query_coverage + target_coverage)
        if query_coverage + target_coverage
        else 0.0
    )
    contains = 0.96 if (left in right or right in left) and min(len(left_terms), len(right_terms)) >= 3 else 0.0
    return max(harmonic, contains)


def _is_generic_toc_query(value: Any) -> bool:
    normalized = _normalize_toc_match_text(value)
    terms = normalized.split()
    raw = str(value or "").lower()
    if not normalized:
        return True
    if "introduction" in raw and len(terms) <= 3:
        return True
    return False


def _structured_chapter_number(chapter: str) -> Optional[int]:
    match = re.fullmatch(r"chapter0*([0-9]+)", str(chapter or "").strip(), flags=re.I)
    return int(match.group(1)) if match else None


def _structured_appendix_number(chapter: str) -> Optional[int]:
    match = re.fullmatch(r"appendix0*([0-9]+)", str(chapter or "").strip(), flags=re.I)
    return int(match.group(1)) if match else None


def _is_intro_heading(value: Any) -> bool:
    normalized = _normalize_toc_match_text(value)
    return normalized in {"introduction", "intro"} or normalized.endswith(" introduction")


def _toc_raw_id(node_id: Optional[str]) -> str:
    value = str(node_id or "").strip()
    return value[len("toc::"):] if value.startswith("toc::") else value


def _heading_path_from_metadata(metadata: Dict[str, Any]) -> List[str]:
    raw_path = metadata.get("heading_path")
    if isinstance(raw_path, list):
        candidates = [str(item or "").strip() for item in raw_path]
    else:
        candidates = []
        for key in ("section_level_1", "section_level_2", "section_level_3", "section"):
            value = str(metadata.get(key) or "").strip()
            if value:
                candidates.append(value)
        subsections = metadata.get("subsections")
        if isinstance(subsections, list):
            candidates.extend(str(item or "").strip() for item in subsections)

    result: List[str] = []
    seen_adjacent = ""
    for item in candidates:
        normalized = re.sub(r"\s+", " ", item).strip()
        if not normalized or normalized == seen_adjacent:
            continue
        result.append(normalized)
        seen_adjacent = normalized
    return result


def _append_unique_node(nodes: List[Dict[str, Any]], seen_ids: set[str], node: Dict[str, Any]) -> None:
    node_id = str(node.get("id") or "")
    if not node_id or node_id in seen_ids:
        return
    seen_ids.add(node_id)
    nodes.append(node)


def _append_unique_relation(relations: List[Dict[str, Any]], seen_ids: set[str], relation: Dict[str, Any]) -> None:
    relation_id = str((relation.get("metadata") or {}).get("id") or relation.get("id") or "")
    if not relation_id:
        relation_id = "::".join(
            str(relation.get(key) or "")
            for key in ("source_id", "relation_type", "target_id")
        )
    if relation_id in seen_ids:
        return
    seen_ids.add(relation_id)
    relations.append(relation)


def _semantic_relation_for_pair(
    source: Dict[str, Any],
    target: Dict[str, Any],
    source_keywords: set[str],
    target_keywords: set[str],
) -> Optional[tuple[str, float, str]]:
    from KGTS.maintenance.sync_utils import _node_text, _overlap_score

    source_text = _node_text(source).lower()
    target_text = _node_text(target).lower()
    combined = f"{source_text}\n{target_text}"
    overlap = _overlap_score(source_keywords, target_keywords)
    target_type = str(target.get("type") or "").lower()
    source_type = str(source.get("type") or "").lower()

    if any(token in target_text for token in ("in contrast", "however", "whereas", "unlike", "rather than")):
        return "contrasts_with", max(overlap, 0.72), "contrast marker"
    if "example" in target_text or target_text.strip().startswith("example"):
        return "example_of", max(overlap, 0.7), "example marker"
    if any(token in combined for token in ("define", "denote", "called", "means that", "let ")):
        return "defines", max(overlap, 0.64), "definition marker"
    if source_type == "derivation" or target_type == "derivation" or any(token in combined for token in ("derive", "proof", "theorem", "identity", "equation")):
        return "derives", max(overlap, 0.62), "derivation marker"
    if any(token in combined for token in ("depends on", "requires", "based on", "recall", "assume")):
        return "depends_on", max(overlap, 0.6), "dependency marker"
    if any(token in combined for token in ("support", "evidence", "consistent with", "suggest")):
        return "supports", max(overlap, 0.58), "support marker"
    if any(token in combined for token in ("apply", "application", "used to", "use of")):
        return "applies_to", max(overlap, 0.58), "application marker"
    if any(token in combined for token in ("cause", "lead to", "effect of", "resulting in")):
        return "causes", max(overlap, 0.58), "causal marker"
    if overlap >= 0.42:
        return "explains", overlap, "high keyword overlap"
    if overlap >= 0.28:
        return "related", overlap, "moderate keyword overlap"
    return None


def _add_semantic_candidate_relations(spec: SourceSpec) -> None:
    from KGTS.maintenance.sync_utils import _keywords, _node_text

    block_nodes = [
        node for node in spec.nodes
        if str(node.get("id") or "").startswith("block::")
    ]
    if len(block_nodes) < 2:
        return

    existing = {
        (
            str(relation.get("source_id") or relation.get("source") or ""),
            str(relation.get("target_id") or relation.get("target") or ""),
            str(relation.get("relation_type") or relation.get("type") or ""),
        )
        for relation in spec.relations
    }
    keyword_map = {str(node.get("id")): _keywords(_node_text(node)) for node in block_nodes}

    for index, source in enumerate(block_nodes):
        source_id = str(source.get("id"))
        candidates: list[tuple[float, Dict[str, Any], str, str]] = []
        for target in block_nodes[index + 1:index + 7]:
            target_id = str(target.get("id"))
            inferred = _semantic_relation_for_pair(
                source,
                target,
                keyword_map.get(source_id, set()),
                keyword_map.get(target_id, set()),
            )
            if not inferred:
                continue
            relation_type, score, reason = inferred
            if (source_id, target_id, relation_type) in existing:
                continue
            candidates.append((score, target, relation_type, reason))

        for score, target, relation_type, reason in sorted(candidates, key=lambda item: item[0], reverse=True)[:2]:
            target_id = str(target.get("id"))
            metadata = source.get("metadata") or {}
            relation = _relation_payload(
                f"rel::{source_id}::{relation_type}::{target_id}::semantic",
                source_id,
                target_id,
                relation_type,
                description=f"semantic candidate: {reason}",
                similarity=round(score, 4),
                chapter=metadata.get("chapter"),
                source_file=metadata.get("source"),
            )
            relation["metadata"]["relation_source"] = "semantic_candidate"
            relation["metadata"]["relation_inference"] = reason
            spec.relations.append(relation)
            existing.add((source_id, target_id, relation_type))


def _resolve_relation_types(nodes: List[Dict[str, Any]], relations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    node_lookup = {str(node.get("id")): node for node in nodes if node.get("id")}
    resolved: List[Dict[str, Any]] = []

    for relation in relations:
        item = dict(relation)
        metadata = dict(item.get("metadata") or {})
        source_id = str(item.get("source_id") or item.get("source") or "")
        target_id = str(item.get("target_id") or item.get("target") or "")
        original_type = str(item.get("relation_type") or item.get("type") or "other")
        relation_type = normalize_relation_type(
            original_type,
            metadata,
            node_lookup.get(source_id),
            node_lookup.get(target_id),
        )
        if relation_type == "other":
            relation_type = "related"
        if relation_type != original_type:
            metadata.setdefault("original_relation_type", original_type)
            metadata.setdefault("relation_inference", "preset_or_other_resolution")
        item["relation_type"] = relation_type
        item["metadata"] = metadata
        resolved.append(item)

    return resolved


def _build_chunk_source(path: Path) -> SourceSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    metadata = payload.get("metadata") or {}
    chapter = str(metadata.get("chapter") or path.stem.split("_")[0])
    heading_path = _heading_path_from_metadata(metadata)
    chapter_title = str(heading_path[0] if heading_path else metadata.get("section") or metadata.get("source_title") or chapter)
    file_hash = _sha256_file(path)
    source_file = path.name

    nodes: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    seen_node_ids: set[str] = set()
    seen_relation_ids: set[str] = set()
    previous_block_id: Optional[str] = None
    source_unit = str(payload.get("id") or path.stem)
    toc_unit_id = _structured_unit_to_toc_node_id(source_unit)
    toc_chapter_node_id = _structured_chapter_to_toc_node_id(chapter)
    chapter_root_id = _chapter_node_id(chapter)
    parent_node_id = chapter_root_id
    toc_parent_metadata = _toc_metadata_for_node_id(toc_unit_id)

    for depth, heading in enumerate(heading_path, start=1):
        section_path = heading_path[:depth]
        section_id = _section_node_id(chapter, section_path)
        parent_for_section = parent_node_id if depth == 1 else _section_node_id(chapter, heading_path[: depth - 1])
        is_deepest_heading = depth == len(heading_path)
        _append_unique_node(
            nodes,
            seen_node_ids,
            _node_payload(
                node_id=section_id,
                content=heading,
                node_type="section",
                label=heading,
                chapter=chapter,
                source_file=source_file,
                extra_metadata={
                    "role": "heading",
                    "heading": heading,
                    "heading_path": section_path,
                    "heading_depth": depth,
                    "source_unit": source_unit,
                    "toc_parent_id": toc_unit_id,
                    **(toc_parent_metadata if is_deepest_heading else {}),
                    "section": metadata.get("section"),
                    "display_heading": metadata.get("display_heading"),
                },
            ),
        )
        _append_unique_relation(
            relations,
            seen_relation_ids,
            _relation_payload(
                f"rel::{parent_for_section}::contains::{section_id}",
                parent_for_section,
                section_id,
                "contains",
                description="heading tree contains subsection",
                chapter=chapter,
                source_file=source_file,
            ),
        )
        parent_node_id = section_id

    if toc_unit_id and toc_unit_id != toc_chapter_node_id:
        if heading_path:
            _append_unique_relation(
                relations,
                seen_relation_ids,
                _relation_payload(
                    f"rel::{toc_unit_id}::contains::{parent_node_id}",
                    toc_unit_id,
                    parent_node_id,
                    "contains",
                    description="exported TOC section contains structured heading",
                    chapter=chapter,
                    source_file=source_file,
                ),
            )
        else:
            parent_node_id = toc_unit_id

    for index, block in enumerate(payload.get("blocks") or [], start=1):
        block_type = str(block.get("type") or "concept")
        raw_block_content = str(block.get("content") or "")
        block_content = _expand_formula_references(raw_block_content)
        block_id = f"block::{path.stem}::{index}"
        label = f"{chapter} #{index} {block_type}: {_clean_label(block_content)}"
        _append_unique_node(
            nodes,
            seen_node_ids,
            _node_payload(
                node_id=block_id,
                content=block_content,
                node_type=block_type,
                label=label,
                chapter=chapter,
                source_file=source_file,
                extra_metadata={
                    "block_index": index,
                    "source_unit": source_unit,
                    "toc_parent_id": toc_unit_id,
                    **toc_parent_metadata,
                    "section": metadata.get("section"),
                    "subsections": metadata.get("subsections"),
                    "heading_path": heading_path,
                    "heading_depth": len(heading_path) + 1,
                    "source_title": metadata.get("source_title"),
                    "source_file_name": metadata.get("source_file"),
                },
            )
        )
        _append_unique_relation(
            relations,
            seen_relation_ids,
            _relation_payload(
                f"rel::{parent_node_id}::contains::{block_id}",
                parent_node_id,
                block_id,
                "contains",
                description="section contains structured block",
                chapter=chapter,
                source_file=source_file,
            )
        )
        if previous_block_id:
            _append_unique_relation(
                relations,
                seen_relation_ids,
                _relation_payload(
                    f"rel::{previous_block_id}::precedes::{block_id}",
                    previous_block_id,
                    block_id,
                    "precedes",
                    description="structured block sequence",
                    chapter=chapter,
                    source_file=source_file,
                )
            )

        references = _parse_all_references(raw_block_content)
        for formula_id in references["formula"]:
            formula_chapter = _reference_chapter(chapter, formula_id)
            formula_node_id = f"formula::{formula_chapter}::{formula_id}"
            _RESOURCE_CONTAINER_BY_NODE_ID.setdefault(formula_node_id, parent_node_id)
            _append_unique_relation(
                relations,
                seen_relation_ids,
                _relation_payload(
                    f"rel::{block_id}::references_formula::{formula_node_id}",
                    block_id,
                    formula_node_id,
                    "references_formula",
                    description=f"block references formula {formula_id}",
                    chapter=chapter,
                    source_file=source_file,
                )
            )
        for table_id in references["table"]:
            table_chapter = _reference_chapter(chapter, table_id)
            table_node_id = f"table::{table_chapter}::{table_id}"
            _RESOURCE_CONTAINER_BY_NODE_ID.setdefault(table_node_id, parent_node_id)
            _append_unique_relation(
                relations,
                seen_relation_ids,
                _relation_payload(
                    f"rel::{block_id}::references_table::{table_node_id}",
                    block_id,
                    table_node_id,
                    "references_table",
                    description=f"block references table {table_id}",
                    chapter=chapter,
                    source_file=source_file,
                )
            )
        for figure_id in references["figure"]:
            figure_chapter = _reference_chapter(chapter, figure_id)
            figure_node_id = f"figure::{figure_chapter}::{figure_id}"
            _RESOURCE_CONTAINER_BY_NODE_ID.setdefault(figure_node_id, parent_node_id)
            _append_unique_relation(
                relations,
                seen_relation_ids,
                _relation_payload(
                    f"rel::{block_id}::references_figure::{figure_node_id}",
                    block_id,
                    figure_node_id,
                    "references_figure",
                    description=f"block references figure {figure_id}",
                    chapter=chapter,
                    source_file=source_file,
                ),
            )
        for example_id in references["example"]:
            example_chapter = _reference_chapter(chapter, example_id)
            example_node_id = f"example::{example_chapter}::{example_id}"
            _RESOURCE_CONTAINER_BY_NODE_ID.setdefault(example_node_id, parent_node_id)
            _append_unique_relation(
                relations,
                seen_relation_ids,
                _relation_payload(
                    f"rel::{block_id}::references_example::{example_node_id}",
                    block_id,
                    example_node_id,
                    "references_example",
                    description=f"block references example {example_id}",
                    chapter=chapter,
                    source_file=source_file,
                ),
            )

        previous_block_id = block_id

    return SourceSpec(
        source_key=f"chunk::{path.name}",
        file_hash=file_hash,
        nodes=nodes,
        relations=relations,
        chapters={chapter: chapter_title},
    )


def _build_formula_source(path: Path) -> SourceSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    file_hash = _sha256_file(path)
    source_file = path.name
    nodes: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    chapters: Dict[str, str] = {}

    for item in payload.get("formulas") or []:
        source = item.get("source") or {}
        chapter = str(source.get("chapter") or "unknown")
        structured_container_node_id = _resource_container_node_id(
            chapter,
            source_unit=source.get("unit_id"),
            subsection=source.get("subsection"),
        )
        container_node_id = _resource_toc_container_node_id(
            chapter,
            structured_container_node_id,
            source_unit=source.get("unit_id"),
            subsection=source.get("subsection"),
        )
        chapter_title = str(source.get("subsection") or chapter)
        chapters.setdefault(chapter, chapter_title)
        formula_id = str(item.get("id"))
        node_id = f"formula::{chapter}::{formula_id}"
        container_node_id = _RESOURCE_CONTAINER_BY_NODE_ID.get(node_id, container_node_id)
        label = str(item.get("label_format") or f"Formula {formula_id}")
        content = str(item.get("latex") or "")
        nodes.append(
            _node_payload(
                node_id=node_id,
                content=content,
                node_type="formula",
                label=label,
                chapter=chapter,
                source_file=source_file,
                extra_metadata={
                    "formula_id": formula_id,
                    "formula_type": item.get("formula_type"),
                    "context": item.get("context"),
                    "description": item.get("description"),
                    "source_unit": source.get("unit_id"),
                    "subsection": source.get("subsection"),
                },
            )
        )
        relations.append(
            _relation_payload(
                f"rel::{container_node_id}::contains::{node_id}",
                container_node_id,
                node_id,
                "contains",
                description="chapter contains formula",
                chapter=chapter,
                source_file=source_file,
            )
        )

    return SourceSpec(
        source_key=f"library::{path.name}",
        file_hash=file_hash,
        nodes=nodes,
        relations=relations,
        chapters=chapters,
    )


def _build_table_source(path: Path) -> SourceSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    file_hash = _sha256_file(path)
    source_file = path.name
    nodes: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    chapters: Dict[str, str] = {}

    for item in payload.get("tables") or []:
        source = item.get("source") or {}
        chapter = str(source.get("chapter") or "unknown")
        structured_container_node_id = _resource_container_node_id(
            chapter,
            source_unit=source.get("unit_id"),
            subsection=source.get("subsection"),
        )
        container_node_id = _resource_toc_container_node_id(
            chapter,
            structured_container_node_id,
            source_unit=source.get("unit_id"),
            subsection=source.get("subsection"),
        )
        chapter_title = str(source.get("subsection") or chapter)
        chapters.setdefault(chapter, chapter_title)
        table_id = str(item.get("id"))
        node_id = f"table::{chapter}::{table_id}"
        container_node_id = _RESOURCE_CONTAINER_BY_NODE_ID.get(node_id, container_node_id)
        label = str(item.get("label_format") or item.get("title") or f"Table {table_id}")
        content = json.dumps(item.get("rows") or item.get("html") or [], ensure_ascii=False)
        from KGTS.maintenance.sync_utils import _truncate
        nodes.append(
            _node_payload(
                node_id=node_id,
                content=content,
                node_type="note",
                label=_truncate(label, 96),
                chapter=chapter,
                source_file=source_file,
                extra_metadata={
                    "table_id": table_id,
                    "table_type": item.get("table_type"),
                    "title": item.get("title"),
                    "description": item.get("description"),
                    "source_unit": source.get("unit_id"),
                    "subsection": source.get("subsection"),
                },
            )
        )
        relations.append(
            _relation_payload(
                f"rel::{container_node_id}::contains::{node_id}",
                container_node_id,
                node_id,
                "contains",
                description="chapter contains table",
                chapter=chapter,
                source_file=source_file,
            )
        )

    return SourceSpec(
        source_key=f"library::{path.name}",
        file_hash=file_hash,
        nodes=nodes,
        relations=relations,
        chapters=chapters,
    )


def _build_example_source(path: Path) -> SourceSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    file_hash = _sha256_file(path)
    source_file = path.name
    nodes: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    chapters: Dict[str, str] = {}

    for item in payload.get("examples") or []:
        chapter = str(item.get("chapter") or "unknown")
        structured_container_node_id = _resource_container_node_id(
            chapter,
            source_file=item.get("source_file"),
        )
        container_node_id = _resource_toc_container_node_id(
            chapter,
            structured_container_node_id,
            source_file=item.get("source_file"),
        )
        example_id = str(item.get("example_id") or item.get("id") or "").strip()
        if not example_id:
            continue
        chapters.setdefault(chapter, chapter)
        node_id = f"example::{chapter}::{example_id}"
        container_node_id = _RESOURCE_CONTAINER_BY_NODE_ID.get(node_id, container_node_id)
        label = str(item.get("label") or f"Example {example_id}")
        content = str(item.get("content_markdown") or item.get("content_plain") or item.get("title") or "")
        nodes.append(
            _node_payload(
                node_id=node_id,
                content=content,
                node_type="example",
                label=label,
                chapter=chapter,
                source_file=source_file,
                extra_metadata={
                    "example_id": example_id,
                    "title": item.get("title"),
                    "source_unit": item.get("source_file"),
                    "start_block_index": item.get("start_block_index"),
                    "end_block_index": item.get("end_block_index"),
                    "formula_refs": item.get("formula_refs"),
                    "table_refs": item.get("table_refs"),
                    "figure_refs": item.get("figure_refs"),
                },
            )
        )
        relations.append(
            _relation_payload(
                f"rel::{container_node_id}::contains::{node_id}",
                container_node_id,
                node_id,
                "contains",
                description="chapter contains example",
                chapter=chapter,
                source_file=source_file,
            )
        )
        for formula_id in item.get("formula_refs") or []:
            formula_chapter = _reference_chapter(chapter, str(formula_id))
            formula_node_id = f"formula::{formula_chapter}::{formula_id}"
            relations.append(
                _relation_payload(
                    f"rel::{node_id}::references_formula::{formula_node_id}",
                    node_id,
                    formula_node_id,
                    "references_formula",
                    description=f"example references formula {formula_id}",
                    chapter=chapter,
                    source_file=source_file,
                )
            )
        for table_id in item.get("table_refs") or []:
            table_chapter = _reference_chapter(chapter, str(table_id))
            table_node_id = f"table::{table_chapter}::{table_id}"
            relations.append(
                _relation_payload(
                    f"rel::{node_id}::references_table::{table_node_id}",
                    node_id,
                    table_node_id,
                    "references_table",
                    description=f"example references table {table_id}",
                    chapter=chapter,
                    source_file=source_file,
                )
            )
        for figure_id in item.get("figure_refs") or []:
            figure_chapter = _reference_chapter(chapter, str(figure_id))
            figure_node_id = f"figure::{figure_chapter}::{figure_id}"
            relations.append(
                _relation_payload(
                    f"rel::{node_id}::references_figure::{figure_node_id}",
                    node_id,
                    figure_node_id,
                    "references_figure",
                    description=f"example references figure {figure_id}",
                    chapter=chapter,
                    source_file=source_file,
                )
            )

    return SourceSpec(
        source_key=f"library::{path.name}",
        file_hash=file_hash,
        nodes=nodes,
        relations=relations,
        chapters=chapters,
    )


def _build_figure_source(path: Path) -> SourceSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    file_hash = _sha256_file(path)
    source_file = path.name
    nodes: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    chapters: Dict[str, str] = {}
    figures = payload.get("figures")
    if isinstance(figures, list):
        figures = {
            str(item.get("id") or index): item
            for index, item in enumerate(figures, start=1)
            if isinstance(item, dict)
        }
    if not isinstance(figures, dict):
        return SourceSpec(source_key=f"library::{path.name}", file_hash=file_hash, nodes=[], relations=[], chapters={})

    for figure_id, item in figures.items():
        if not isinstance(item, dict):
            continue
        figure_id = str(item.get("id") or figure_id).strip()
        chapter = str(item.get("chapter") or _reference_chapter("unknown", figure_id))
        container_node_id = _chapter_container_node_id(chapter)
        if not figure_id:
            continue
        chapters.setdefault(chapter, chapter)
        node_id = f"figure::{chapter}::{figure_id}"
        container_node_id = _RESOURCE_CONTAINER_BY_NODE_ID.get(node_id, container_node_id)
        label = str(item.get("label") or f"Figure {figure_id}")
        content = str(item.get("caption") or label)
        figure_metadata = {
            "figure_id": figure_id,
            "asset_path": item.get("asset_path"),
            "caption": item.get("caption"),
            "page": item.get("page"),
            "source_pdf": item.get("source_pdf"),
        }
        if item.get("confidence") is not None:
            figure_metadata["confidence"] = item.get("confidence")
        nodes.append(
            _node_payload(
                node_id=node_id,
                content=content,
                node_type="figure",
                label=label,
                chapter=chapter,
                source_file=source_file,
                extra_metadata=figure_metadata,
            )
        )
        relations.append(
            _relation_payload(
                f"rel::{container_node_id}::contains::{node_id}",
                container_node_id,
                node_id,
                "contains",
                description="chapter contains figure",
                chapter=chapter,
                source_file=source_file,
            )
        )

    return SourceSpec(
        source_key=f"library::{path.name}",
        file_hash=file_hash,
        nodes=nodes,
        relations=relations,
        chapters=chapters,
    )


def _build_toc_source(path: Path) -> SourceSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    file_hash = _sha256_file(path)
    source_file = _source_name(path)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    raw_nodes = payload.get("nodes") if isinstance(payload.get("nodes"), dict) else {}
    root_nodes = payload.get("root_nodes") if isinstance(payload.get("root_nodes"), list) else []
    title = str(metadata.get("source_title") or "Evolution and Selection of Quantitative Traits")
    nodes: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    seen_node_ids: set[str] = set()
    seen_relation_ids: set[str] = set()
    chapters: Dict[str, str] = {}

    _append_unique_node(
        nodes,
        seen_node_ids,
        _node_payload(
            node_id=TOC_ROOT_NODE_ID,
            content=title,
            node_type="part",
            label=title,
            chapter="toc",
            source_file=source_file,
            extra_metadata={
                "role": "toc_root",
                "source_title": title,
                "source_file_name": metadata.get("source_file"),
                "toc_export": _project_relative_path(path),
                "total_nodes": metadata.get("total_nodes"),
                "root_count": metadata.get("root_count"),
                "navigation_units": metadata.get("navigation_units"),
                "toc_path": [],
            },
        ),
    )

    normalized_nodes: Dict[str, Dict[str, Any]] = {}
    for raw_id, item in raw_nodes.items():
        if not isinstance(item, dict):
            continue
        toc_id = str(item.get("id") or raw_id or "").strip()
        title_text = str(item.get("title") or "").strip()
        if not toc_id or not title_text:
            continue
        normalized_nodes[toc_id] = item

    def path_for(node_id: str) -> List[str]:
        path_items: List[str] = []
        visited: set[str] = set()
        current_id: Optional[str] = node_id
        while current_id and current_id in normalized_nodes and current_id not in visited:
            visited.add(current_id)
            current = normalized_nodes[current_id]
            current_title = str(current.get("title") or "").strip()
            if current_title:
                path_items.append(current_title)
            parent_id = current.get("parent_id")
            current_id = str(parent_id).strip() if parent_id else None
        return list(reversed(path_items))

    def sort_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    for toc_id, item in sorted(
        normalized_nodes.items(),
        key=lambda pair: (sort_int(pair[1].get("page")), sort_int(pair[1].get("level")), pair[0]),
    ):
        entry_type = str(item.get("entry_type") or "section").strip().lower() or "section"
        node_type = _toc_entry_node_type(entry_type)
        title_text = str(item.get("title") or "").strip()
        page = item.get("page")
        content = f"{title_text}"
        if page is not None:
            content = f"{content}\nPage: {page}"
        level = item.get("level")
        if level is not None:
            content = f"{content}\nLevel: {level}"

        _append_unique_node(
            nodes,
            seen_node_ids,
            _node_payload(
                node_id=_toc_node_id(toc_id),
                content=content,
                node_type=node_type,
                label=title_text,
                chapter="toc",
                source_file=source_file,
                extra_metadata={
                    "role": "toc_entry",
                    "toc_node_id": toc_id,
                    "toc_entry_type": entry_type,
                    "toc_level": item.get("level"),
                    "toc_page": page,
                    "toc_parent_id": item.get("parent_id"),
                    "toc_unit_id": item.get("unit_id"),
                    "toc_path": path_for(toc_id),
                    "source_title": title,
                    "source_file_name": metadata.get("source_file"),
                    "child_count": len(item.get("children") or []),
                },
            ),
        )

    for toc_id, item in normalized_nodes.items():
        parent_id = item.get("parent_id")
        source_id = _toc_node_id(str(parent_id).strip()) if parent_id else TOC_ROOT_NODE_ID
        target_id = _toc_node_id(toc_id)
        _append_unique_relation(
            relations,
            seen_relation_ids,
            _relation_payload(
                f"rel::{source_id}::contains::{target_id}",
                source_id,
                target_id,
                "contains",
                description="table of contents contains entry",
                chapter="toc",
                source_file=source_file,
            ),
        )

        previous_child_id: Optional[str] = None
        for child_id in item.get("children") or []:
            child_id = str(child_id or "").strip()
            if not child_id or child_id not in normalized_nodes:
                continue
            if previous_child_id:
                _append_unique_relation(
                    relations,
                    seen_relation_ids,
                    _relation_payload(
                        f"rel::{_toc_node_id(previous_child_id)}::precedes::{_toc_node_id(child_id)}",
                        _toc_node_id(previous_child_id),
                        _toc_node_id(child_id),
                        "precedes",
                        description="table of contents sibling order",
                        chapter="toc",
                        source_file=source_file,
                    ),
                )
            previous_child_id = child_id

    previous_root_id: Optional[str] = None
    for root_id in root_nodes:
        root_id = str(root_id or "").strip()
        if not root_id or root_id not in normalized_nodes:
            continue
        if previous_root_id:
            _append_unique_relation(
                relations,
                seen_relation_ids,
                _relation_payload(
                    f"rel::{_toc_node_id(previous_root_id)}::precedes::{_toc_node_id(root_id)}",
                    _toc_node_id(previous_root_id),
                    _toc_node_id(root_id),
                    "precedes",
                    description="table of contents root order",
                    chapter="toc",
                    source_file=source_file,
                ),
            )
        previous_root_id = root_id

    return SourceSpec(
        source_key=f"toc::{source_file}",
        file_hash=file_hash,
        nodes=nodes,
        relations=relations,
        chapters=chapters,
    )


def _index_toc_chapter_mapping(path: Path, source_paths: List[Path]) -> None:
    global _TOC_HAS_INDEXED_SOURCE, _TOC_FUSION
    _reset_toc_index()
    _TOC_HAS_INDEXED_SOURCE = True
    structured_units = build_structured_units(
        source_paths,
        heading_path_from_metadata=_heading_path_from_metadata,
    )
    fusion = fuse_toc_with_structured_units(path, structured_units)
    _TOC_FUSION = fusion

    for chapter, toc_id in fusion.chapter_to_toc_id.items():
        if toc_id:
            _TOC_CHAPTER_NODE_BY_STRUCTURED_CHAPTER[chapter.lower()] = _toc_node_id(toc_id)
    for unit_id, toc_id in fusion.unit_to_toc_id.items():
        if toc_id:
            _TOC_NODE_BY_STRUCTURED_UNIT[unit_id.lower()] = _toc_node_id(toc_id)
    for toc_id, metadata in fusion.metadata_by_toc_id.items():
        _TOC_METADATA_BY_NODE_ID[_toc_node_id(toc_id)] = metadata
        item = fusion.toc_by_id.get(toc_id) or {}
        _TOC_CHILDREN_BY_NODE_ID[_toc_node_id(toc_id)] = [
            _toc_node_id(str(child_id or "").strip())
            for child_id in (item.get("children") or [])
            if str(child_id or "").strip()
        ]
    for unit in structured_units:
        unit_key = unit.unit_id.strip().lower()
        toc_id = fusion.unit_to_toc_id.get(unit_key)
        chapter_toc_id = fusion.chapter_to_toc_id.get(unit.chapter.strip().lower())
        if not toc_id or toc_id == chapter_toc_id:
            continue
        section_id = _section_node_id(unit.chapter, list(unit.headings))
        _STRUCTURED_SECTION_TO_TOC_NODE[section_id] = _toc_node_id(toc_id)
    return


def _toc_export_files() -> List[Path]:
    configured = str(TOC_EXPORT_DIR).strip()
    if configured == ".":
        configured = ""
    candidates: List[Path] = []
    if configured:
        export_path = Path(configured)
        if export_path.is_file():
            candidates.append(export_path)
        elif export_path.exists():
            candidates.extend(export_path.glob("*_toc_tree.json"))
    else:
        structured_root = Path(STRUCTURED_DIR)
        structured_data_dir = resolve_structured_data_dir(structured_root)
        package_root = structured_root if structured_data_dir.parent == structured_root else structured_data_dir
        fallback_dirs = [package_root / "目录树导出"]
        try:
            if structured_root.resolve() == (PROJECT_ROOT / "structured").resolve():
                fallback_dirs.append(PROJECT_ROOT / "目录树导出")
        except OSError:
            pass
        for export_path in fallback_dirs:
            if export_path.exists():
                candidates.extend(export_path.glob("*_toc_tree.json"))

    resolved: Dict[str, Path] = {}
    for candidate in candidates:
        if ".git" in candidate.parts or ".runtime" in candidate.parts or ".tmp" in candidate.parts:
            continue
        try:
            resolved[str(candidate.resolve())] = candidate
        except OSError:
            continue
    return sorted(resolved.values(), key=lambda item: str(item).lower())


def _without_toc_export_outline_edges(spec: SourceSpec) -> SourceSpec:
    """Keep detailed TOC entries, but let the canonical book outline own the tree root."""
    if _has_toc_export_outline():
        return spec
    toc_part_node_ids = {
        str(node.get("id") or "")
        for node in spec.nodes
        if str(node.get("id") or "").startswith("toc::")
        and (
            str(node.get("type") or "") == "part"
            or str((node.get("metadata") or {}).get("toc_entry_type") or "").lower() == "part"
        )
    }
    if not toc_part_node_ids:
        return spec

    relations = [
        relation
        for relation in spec.relations
        if not (
            str(relation.get("relation_type") or relation.get("type") or "") == "contains"
            and str(relation.get("source_id") or relation.get("source") or "") in {TOC_ROOT_NODE_ID, *toc_part_node_ids}
        )
    ]
    return SourceSpec(
        source_key=spec.source_key,
        file_hash=spec.file_hash,
        nodes=spec.nodes,
        relations=relations,
        chapters=spec.chapters,
    )


def _build_chapter_specs(chapters: Dict[str, str]) -> List[SourceSpec]:
    if not chapters:
        return []
    specs: List[SourceSpec] = []
    has_export_outline = _has_toc_export_outline()
    outline_nodes: List[Dict[str, Any]] = [
        _node_payload(
            node_id=TOC_ROOT_NODE_ID,
            content=BOOK_TITLE,
            node_type="part",
            label=BOOK_TITLE,
            chapter="toc",
            source_file="structured_sync",
            extra_metadata={
                "role": "book_root",
                "source_title": BOOK_TITLE,
                "outline_source": "canonical",
                "toc_path": [],
            },
        )
    ]
    outline_relations: List[Dict[str, Any]] = []

    chapters_by_part: Dict[str, list[str]] = {part.id: [] for part in BOOK_PARTS}
    appendices: list[str] = []
    extra_chapters: list[str] = []
    for chapter in sorted(chapters, key=_structured_chapter_sort_key):
        appendix_index = appendix_number(chapter)
        if appendix_index is not None:
            appendices.append(chapter)
            continue
        part = part_for_chapter(chapter)
        if part is None:
            extra_chapters.append(chapter)
            continue
        chapters_by_part.setdefault(part.id, []).append(chapter)

    previous_part_id: Optional[str] = None
    unmapped_chapters = {
        chapter for chapter in chapters
        if not _structured_chapter_to_toc_node_id(chapter)
    }
    for part in BOOK_PARTS:
        if not chapters_by_part.get(part.id):
            continue
        emit_canonical_part = (not has_export_outline) or _toc_raw_id(
            _export_toc_part_node_id_for_chapter(chapters_by_part[part.id][0])
        ) == ""
        if emit_canonical_part:
            outline_nodes.append(
                _node_payload(
                    node_id=part.id,
                    content=part.label,
                    node_type="part",
                    label=part.label,
                    chapter="toc",
                    source_file="structured_sync",
                    extra_metadata={
                        "role": "book_part",
                        "outline_source": "canonical",
                        "part_number": part.id.removeprefix("part::"),
                        "chapter_start": part.chapter_start,
                        "chapter_end": part.chapter_end,
                        "toc_path": [part.label],
                    },
                )
            )
            outline_relations.append(
                _relation_payload(
                    f"rel::{TOC_ROOT_NODE_ID}::contains::{part.id}",
                    TOC_ROOT_NODE_ID,
                    part.id,
                    "contains",
                    description="book root contains canonical part",
                    chapter="toc",
                    source_file="structured_sync",
                )
            )
        if previous_part_id and emit_canonical_part and not has_export_outline:
            outline_relations.append(
                _relation_payload(
                    f"rel::{previous_part_id}::precedes::{part.id}",
                    previous_part_id,
                    part.id,
                    "precedes",
                    description="canonical part order",
                    chapter="toc",
                    source_file="structured_sync",
                )
            )
        if emit_canonical_part:
            previous_part_id = part.id

    emit_appendices_part = bool(appendices) and not has_export_outline
    if emit_appendices_part:
        outline_nodes.append(
            _node_payload(
                node_id=APPENDICES_PART_ID,
                content=APPENDICES_PART_LABEL,
                node_type="part",
                label=APPENDICES_PART_LABEL,
                chapter="toc",
                source_file="structured_sync",
                extra_metadata={
                    "role": "book_part",
                    "outline_source": "canonical",
                    "part_number": "appendices",
                    "toc_path": [APPENDICES_PART_LABEL],
                },
            )
        )
        outline_relations.append(
            _relation_payload(
                f"rel::{TOC_ROOT_NODE_ID}::contains::{APPENDICES_PART_ID}",
                TOC_ROOT_NODE_ID,
                APPENDICES_PART_ID,
                "contains",
                description="book root contains appendices",
                chapter="toc",
                source_file="structured_sync",
            )
        )
        if previous_part_id:
            outline_relations.append(
                _relation_payload(
                    f"rel::{previous_part_id}::precedes::{APPENDICES_PART_ID}",
                    previous_part_id,
                    APPENDICES_PART_ID,
                    "precedes",
                    description="canonical part order",
                    chapter="toc",
                    source_file="structured_sync",
                )
            )
        previous_part_id = APPENDICES_PART_ID

    if extra_chapters:
        extra_part_id = "part::other"
        outline_nodes.append(
            _node_payload(
                node_id=extra_part_id,
                content="Other Structured Units",
                node_type="part",
                label="Other Structured Units",
                chapter="toc",
                source_file="structured_sync",
                extra_metadata={
                    "role": "book_part",
                    "outline_source": "canonical",
                    "part_number": "other",
                    "toc_path": ["Other Structured Units"],
                },
            )
        )
        outline_relations.append(
            _relation_payload(
                f"rel::{TOC_ROOT_NODE_ID}::contains::{extra_part_id}",
                TOC_ROOT_NODE_ID,
                extra_part_id,
                "contains",
                description="book root contains uncategorized structured units",
                chapter="toc",
                source_file="structured_sync",
            )
        )
        if previous_part_id:
            outline_relations.append(
                _relation_payload(
                    f"rel::{previous_part_id}::precedes::{extra_part_id}",
                    previous_part_id,
                    extra_part_id,
                    "precedes",
                    description="canonical part order",
                    chapter="toc",
                    source_file="structured_sync",
                )
            )
        chapters_by_part[extra_part_id] = extra_chapters

    file_hash = hashlib.sha256(
        json.dumps(
            {
                "book_title": BOOK_TITLE,
                "parts": [(part.id, part.label, part.chapter_start, part.chapter_end) for part in BOOK_PARTS],
                "present_parts": [part_id for part_id, values in sorted(chapters_by_part.items()) if values],
                "appendices": appendices,
                "extra_chapters": extra_chapters,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    specs.append(
        SourceSpec(
            source_key="book::outline",
            file_hash=file_hash,
            nodes=outline_nodes,
            relations=outline_relations,
            chapters={},
        )
    )

    previous_by_part: Dict[str, Optional[str]] = {}
    previous_by_toc_parent: Dict[str, Optional[str]] = {}
    for chapter, raw_title in sorted(chapters.items(), key=lambda item: _structured_chapter_sort_key(item[0])):
        title = _canonical_chapter_title(chapter, raw_title)
        node_id = _chapter_node_id(chapter)
        node_type = "appendix" if _structured_appendix_number(chapter) is not None else "chapter"
        payload = {
            "chapter": chapter,
            "title": title,
            "type": node_type,
        }
        toc_node_id = _structured_chapter_to_toc_node_id(chapter)
        toc_metadata = _toc_metadata_for_node_id(toc_node_id)
        metadata = {
            "role": "chapter_root",
            "title": title,
            "outline_source": "canonical",
        }
        parent_part_id: Optional[str]
        export_part_node_id = _export_toc_part_node_id_for_chapter(chapter) if has_export_outline else None
        if has_export_outline and toc_node_id:
            parent_part_id = toc_node_id
            metadata.update(
                {
                    "book_part_id": toc_metadata.get("toc_root_part_id"),
                    "book_part_label": toc_metadata.get("toc_root_part_title"),
                    "toc_path": [*(toc_metadata.get("toc_path") or []), _chapter_label(chapter, title)],
                }
            )
        elif has_export_outline and export_part_node_id:
            parent_part_id = export_part_node_id
            parent_metadata = _toc_metadata_for_node_id(export_part_node_id)
            metadata.update(
                {
                    "book_part_id": parent_metadata.get("toc_node_id"),
                    "book_part_label": parent_metadata.get("toc_title"),
                    "toc_path": [*(parent_metadata.get("toc_path") or []), _chapter_label(chapter, title)],
                    "toc_match_role": "chapter_missing_from_export",
                    "toc_fusion_source": "structured_toc_fusion",
                }
            )
        else:
            part = part_for_chapter(chapter)
            if part is not None:
                parent_part_id = part.id
                metadata.update(
                    {
                        "book_part_id": part.id,
                        "book_part_label": part.label,
                        "toc_path": [part.label, _chapter_label(chapter, title)],
                    }
                )
            elif _structured_appendix_number(chapter) is not None:
                parent_part_id = APPENDICES_PART_ID
                metadata.update(
                    {
                        "book_part_id": APPENDICES_PART_ID,
                        "book_part_label": APPENDICES_PART_LABEL,
                        "toc_path": [APPENDICES_PART_LABEL, _chapter_label(chapter, title)],
                    }
                )
            else:
                parent_part_id = "part::other" if extra_chapters else TOC_ROOT_NODE_ID
                metadata.update(
                    {
                        "book_part_id": parent_part_id,
                        "book_part_label": "Other Structured Units",
                        "toc_path": ["Other Structured Units", _chapter_label(chapter, title)],
                }
            )
        if toc_node_id:
            metadata.update(
                {
                    "toc_chapter_id": toc_node_id,
                    "toc_match_role": "chapter_auxiliary",
                    **toc_metadata,
                }
            )
        relations: List[Dict[str, Any]] = []
        relations.append(
            _relation_payload(
                f"rel::{parent_part_id}::contains::{node_id}",
                parent_part_id,
                node_id,
                "contains",
                description=(
                    "exported TOC chapter contains structured chapter"
                    if has_export_outline and parent_part_id == toc_node_id
                    else "canonical part contains structured chapter"
                ),
                chapter=chapter,
                source_file="structured_sync",
            )
        )
        if has_export_outline and toc_node_id and not _chapter_has_structured_headings(chapter):
            for child_toc_node_id in _toc_children_for_node_id(toc_node_id):
                child_metadata = _toc_metadata_for_node_id(child_toc_node_id)
                child_entry_type = str(child_metadata.get("toc_entry_type") or "").strip().lower()
                if child_entry_type in {"section", "subsection"}:
                    relations.append(
                        _relation_payload(
                            f"rel::{node_id}::contains::{child_toc_node_id}",
                            node_id,
                            child_toc_node_id,
                            "contains",
                            description="structured chapter contains exported TOC section",
                            chapter=chapter,
                            source_file="structured_sync",
                        )
                    )
        previous_map = previous_by_toc_parent if has_export_outline else previous_by_part
        previous_sibling_id = previous_map.get(parent_part_id)
        if previous_sibling_id:
            relations.append(
                _relation_payload(
                    f"rel::{previous_sibling_id}::precedes::{node_id}",
                    previous_sibling_id,
                    node_id,
                    "precedes",
                    description="canonical chapter order",
                    chapter=chapter,
                    source_file="structured_sync",
                )
            )
        previous_map[parent_part_id] = node_id
        if toc_node_id and toc_node_id != node_id and not has_export_outline:
            relations.append(
                _relation_payload(
                    f"rel::{node_id}::contains::{toc_node_id}",
                    node_id,
                    toc_node_id,
                    "contains",
                    description="canonical chapter contains matching TOC entry",
                    chapter=chapter,
                    source_file="structured_sync",
                )
            )
        file_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        specs.append(
            SourceSpec(
                source_key=f"chapter::{chapter}",
                file_hash=file_hash,
                nodes=[
                    _node_payload(
                        node_id=node_id,
                        content=title,
                        node_type=node_type,
                        label=_chapter_label(chapter, title),
                        chapter=chapter,
                        source_file="structured_sync",
                        extra_metadata=metadata,
                    )
                ],
                relations=relations,
                chapters={chapter: title},
            )
        )
    return specs


def _collect_specs(*, skip_semantic: bool = False) -> tuple[List[SourceSpec], Dict[str, str]]:
    _reset_toc_index()
    structured_data_dir = resolve_structured_data_dir(STRUCTURED_DIR)
    source_paths: List[Path] = []
    formula_path: Optional[Path] = None
    table_path: Optional[Path] = None
    example_path: Optional[Path] = None
    figure_path: Optional[Path] = None
    chapters: Dict[str, str] = {}

    for path in sorted(structured_data_dir.glob("*.json"), key=lambda item: item.name.lower()):
        if path.name == "formula_library.json":
            formula_path = path
        elif path.name == "table_library.json":
            table_path = path
        elif path.name == "figure_library.json":
            figure_path = path
        elif path.name == "example_library.json":
            example_path = path
        else:
            source_paths.append(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            metadata = payload.get("metadata") or {}
            chapter = str(metadata.get("chapter") or path.stem.split("_")[0])
            heading_path = _heading_path_from_metadata(metadata)
            chapter_title = str(heading_path[0] if heading_path else metadata.get("section") or metadata.get("source_title") or chapter)
            chapters.setdefault(chapter, chapter_title)
            continue

    if figure_path is None:
        fallback_figure_path = structured_data_dir.parent / "figure_library.json"
        if fallback_figure_path.exists():
            figure_path = fallback_figure_path

    _index_structured_section_lookup(source_paths)

    toc_paths = _toc_export_files()
    if toc_paths:
        _index_toc_chapter_mapping(toc_paths[0], source_paths)

    toc_specs: List[SourceSpec] = []
    for path in toc_paths:
        spec = _build_toc_source(path)
        spec = _without_toc_export_outline_edges(spec)
        toc_specs.append(spec)
        for chapter, title in spec.chapters.items():
            chapters.setdefault(chapter, title)

    chunk_specs: List[SourceSpec] = []
    for path in source_paths:
        chunk_specs.append(_build_chunk_source(path))

    library_specs: List[SourceSpec] = []
    for builder, path in (
        (_build_formula_source, formula_path),
        (_build_table_source, table_path),
        (_build_example_source, example_path),
        (_build_figure_source, figure_path),
    ):
        if path is None or not path.exists():
            continue
        spec = builder(path)
        library_specs.append(spec)
        for chapter, title in spec.chapters.items():
            chapters.setdefault(chapter, title)

    specs: List[SourceSpec] = [*toc_specs, *chunk_specs, *library_specs]

    chapter_specs = _build_chapter_specs(chapters)
    for spec in specs:
        if not skip_semantic:
            _add_semantic_candidate_relations(spec)
        spec.relations = _resolve_relation_types(spec.nodes, spec.relations)
    return chapter_specs + specs, chapters
