"""Knowledge-graph constraints for teaching generation.

This module implements a generic KG-grounded learning pipeline:
1. build a LearningPlan from graph evidence,
2. generate with evidence-aware boundaries,
3. attach a lightweight consistency report.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


KG_CONSTRAINED_SYSTEM_PROMPT = """你是一个知识图谱增强的交互式学习助手。
LearningPlan 和 evidence 是优先依据，不是拒答开关。先用证据回答；证据只覆盖一部分时，给出有边界的回答，并明确哪些部分来自证据、哪些部分需要更多材料确认。
不要伪造课程私有事实、引用或节点关系；但用户询问常见公式、定义、数学推导或基础概念时，可以用通用知识回答，并标注为通用说明。
保持知识原文的语言和术语：英文原文、公式、变量名、专有名词和 evidence 片段必须保留英文；除非用户明确要求翻译，不要把英文定义整段翻译成中文。如果翻译可能造成误解，直接用英文回答。
如果 evidence 主要是英文，默认使用英文作答；需要中文辅助时也必须保留英文关键句和术语。
如果学习者正在练习或请求提示，优先给提示和思路，不要一次性泄露完整答案。
输出要符合学习者当前水平，回答问题时优先直接回答，再给依据或限制。"""


DEFAULT_CONSTRAINTS = [
    "优先使用 LearningPlan.allowed_concepts、LearningPlan.learning_intent_graph 和 evidence 中出现的知识。",
    "可以基于证据做简短解释和连接，但不要编造未被证据支持的具体事实、前置关系、因果关系或结论。",
    "如果证据不足，给出限定性回答并说明缺口；不要直接拒答，除非关键事实完全没有依据。",
    "保持知识原文语言：英文原文、术语、公式、变量名和 evidence 片段保留英文；不确定时用英文回答。",
    "如果 evidence 主要是英文，默认使用英文作答；需要中文辅助时也必须保留英文关键句和术语。",
    "根据 learning_level 控制难度，初学者回答优先解释前置知识和关键定义。",
    "练习、批改和提示场景优先给分步提示，除非题目明确要求公布标准答案。",
]


KG_CONSTRAINED_SYSTEM_PROMPT = """You are a teaching assistant using knowledge-graph context as helpful reference, not as a refusal gate.
Answer or generate the requested teaching content directly. Prefer retrieved evidence when it is relevant, but do not expose internal LearningPlan, graph constraints, self-check steps, or pipeline wording.
If the evidence is partial, answer the supported part and clearly mark uncertain parts. For common formulas, definitions, derivations, and basic course concepts, you may use general knowledge and label it as a general explanation when it is not directly in the evidence.
Keep source language stable: English source sentences, formulas, variable names, and technical terms should remain English unless the user explicitly asks for translation."""

DEFAULT_CONSTRAINTS = [
    "Use evidence as preferred context, not as a hard refusal condition.",
    "Do not invent course-specific facts, citations, or graph relations that are not present.",
    "If evidence is incomplete, answer the supported part and mark uncertainty.",
    "Keep English source text, formulas, variable names, and technical terms in English.",
    "Hide internal LearningPlan, pipeline, and consistency-check wording from users.",
]

FORMULA_REFERENCE_PATTERN = re.compile(r"\[\[(SEE_)?FORMULA:([^\]]+)\]\]", re.I)
EQUATION_LABEL_PATTERN = re.compile(r"\b(?:Equation|Eq\.)\s+([0-9]+(?:\.[0-9]+[a-z]?)?)\b(?!\s*[:(])", re.I)
PAREN_FORMULA_LABEL_PATTERN = re.compile(r"\(([A]?[0-9]+(?:\.[0-9]+[a-z]?)?)\)", re.I)
INLINE_MATH_PATTERN = re.compile(r"\${1,2}\s*(.+?)\s*\${1,2}", re.S)
CORE_CONSISTENCY_TYPES = {"chapter", "section", "concept", "formula", "theorem", "example"}
LECTURE_EXPECTED_ENTITY_LIMIT = 10
_FORMULA_INDEX: Optional[Dict[str, Dict[str, Any]]] = None


def _structured_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        nested_candidate = parent / "structured" / "structured" / "formula_library.json"
        if nested_candidate.exists():
            return nested_candidate.parent
        candidate = parent / "structured" / "formula_library.json"
        if candidate.exists():
            return parent / "structured"
    fallback = Path(__file__).resolve().parents[1] / "structured"
    nested = fallback / "structured"
    return nested if (nested / "formula_library.json").exists() else fallback


def _load_formula_index() -> Dict[str, Dict[str, Any]]:
    global _FORMULA_INDEX
    if _FORMULA_INDEX is not None:
        return _FORMULA_INDEX

    structured_dir = _structured_dir()
    formula_path = structured_dir / "formula_library.json"
    index: Dict[str, Dict[str, Any]] = {}
    try:
        payload = json.loads(formula_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _FORMULA_INDEX = index
        return index

    explicit_derives_to: Dict[str, List[str]] = {}
    for item in payload.get("formulas") or []:
        if not isinstance(item, dict):
            continue
        formula_id = str(item.get("id") or "").strip()
        latex = str(item.get("latex") or "").strip()
        if not formula_id or not latex:
            continue
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        symbols = item.get("symbols")
        if not isinstance(symbols, list):
            symbols = _infer_formula_symbols(
                formula_id=formula_id,
                latex=latex,
                context=str(item.get("context") or ""),
                source=source,
            )
        derives_from = _normalize_formula_id_list(item.get("derives_from") or item.get("derived_from"))
        derives_to = _normalize_formula_id_list(item.get("derives_to") or item.get("derived_to"))
        for upstream in derives_from:
            explicit_derives_to.setdefault(upstream.lower(), []).append(formula_id)
        index[formula_id.lower()] = {
            "id": formula_id,
            "label": str(item.get("label_format") or f"Equation {formula_id}").strip(),
            "latex": latex,
            "formula_type": str(item.get("formula_type") or "block"),
            "source": source,
            "context": str(item.get("context") or ""),
            "description": item.get("description"),
            "symbols": symbols,
            "derives_from": derives_from,
            "derives_to": derives_to,
        }

    for record in index.values():
        context_ids = _formula_ids_in_text(str(record.get("context") or ""))
        inferred_from_context = [
            formula_id
            for formula_id in context_ids
            if formula_id.lower() in index and formula_id.lower() != str(record.get("id") or "").lower()
        ]
        if inferred_from_context:
            record["derives_from"] = _unique_formula_ids(
                [*record.get("derives_from", []), *inferred_from_context],
                exclude={str(record.get("id") or "")},
            )

    inferred_derivations = _infer_formula_derivations(structured_dir)
    for formula_id, upstream_ids in inferred_derivations.items():
        record = index.get(formula_id.lower())
        if not record:
            continue
        record["derives_from"] = _unique_formula_ids(
            [*record.get("derives_from", []), *upstream_ids],
            exclude={formula_id},
        )

    for upstream_id, downstream_ids in explicit_derives_to.items():
        record = index.get(upstream_id.lower())
        if record:
            record["derives_to"] = _unique_formula_ids([*record.get("derives_to", []), *downstream_ids])
    for formula_id, record in index.items():
        for upstream_id in record.get("derives_from", []):
            upstream = index.get(str(upstream_id).lower())
            if upstream:
                upstream["derives_to"] = _unique_formula_ids([*upstream.get("derives_to", []), record["id"]])

    _FORMULA_INDEX = index
    return index


def expand_formula_references(value: Any, *, display: bool = True, expand_labels: bool = False) -> str:
    """Expand structured formula references to their original LaTeX."""
    text = str(value or "")
    has_structured_ref = bool(FORMULA_REFERENCE_PATTERN.search(text))
    if not has_structured_ref and not expand_labels:
        return text

    index = _load_formula_index()

    def replace(match: re.Match[str]) -> str:
        formula_id = match.group(2).strip()
        record = index.get(formula_id.lower())
        if not record:
            return f"Equation {formula_id}"
        label = record.get("label") or f"Equation {formula_id}"
        latex = record.get("latex") or ""
        if display and not match.group(1):
            return f"{label}:\n$$ {latex} $$"
        return f"{label} (${latex}$)"

    expanded = FORMULA_REFERENCE_PATTERN.sub(replace, text)
    expanded = re.sub(r"\b(Equation|Eq\.)\s+Equation\s+", r"\1 ", expanded)
    expanded = re.sub(r"\bEquations\s+Equation\s+", "Equations ", expanded)
    if expand_labels and not has_structured_ref:
        def replace_label(match: re.Match[str]) -> str:
            formula_id = match.group(1).strip()
            record = index.get(formula_id.lower())
            if not record:
                return match.group(0)
            label = record.get("label") or f"Equation {formula_id}"
            latex = record.get("latex") or ""
            if display:
                return f"{label}:\n$$ {latex} $$"
            return f"{label} (${latex}$)"

        expanded = EQUATION_LABEL_PATTERN.sub(replace_label, expanded)
    return expanded


def formula_context_for_text(text: Any, *, limit: int = 8) -> List[Dict[str, Any]]:
    """Return formula records referenced by structured tags or equation labels in text."""
    index = _load_formula_index()
    formula_ids = _formula_ids_in_text(str(text or ""))
    records: List[Dict[str, Any]] = []
    for formula_id in formula_ids:
        record = index.get(formula_id.lower())
        if not record:
            continue
        records.append(_public_formula_record(record))
        if len(records) >= limit:
            break
    return records


def format_formula_context(formulas: Sequence[Dict[str, Any]]) -> str:
    if not formulas:
        return "No directly referenced formulas."
    lines: List[str] = []
    for formula in formulas:
        label = formula.get("label") or f"Equation {formula.get('id', '')}".strip()
        lines.append(f"- {label}: $$ {formula.get('latex', '')} $$")
        source = formula.get("source") if isinstance(formula.get("source"), dict) else {}
        source_bits = [str(source.get(key) or "").strip() for key in ("chapter", "unit_id", "subsection")]
        source_text = " / ".join(bit for bit in source_bits if bit)
        if source_text:
            lines.append(f"  Scope: {source_text}")
        derives_from = formula.get("derives_from") or []
        derives_to = formula.get("derives_to") or []
        if derives_from:
            lines.append(f"  Derived from: {', '.join(str(item) for item in derives_from[:6])}")
        if derives_to:
            lines.append(f"  Leads to: {', '.join(str(item) for item in derives_to[:6])}")
        symbol_lines = []
        for symbol in formula.get("symbols") or []:
            name = symbol.get("symbol")
            meaning = symbol.get("meaning")
            if name and meaning:
                symbol_lines.append(f"{name} = {meaning}")
        if symbol_lines:
            lines.append(f"  Symbols in this scope: {'; '.join(symbol_lines[:8])}")
    return "\n".join(lines)


def graph_paths_for_evidence(
    graph_data: Optional[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    *,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    """Build short relation paths among selected evidence nodes."""
    if not graph_data or not evidence:
        return []
    nodes = graph_data.get("nodes") or []
    relations = graph_data.get("relations") or graph_data.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(relations, list):
        return []

    evidence_ids = [str(item.get("id") or "") for item in evidence if item.get("id")]
    evidence_id_set = set(evidence_ids)
    if not evidence_id_set:
        return []
    node_by_id = {str(node.get("id") or ""): node for node in nodes if isinstance(node, dict)}
    paths: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for relation in relations:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source_id") or relation.get("source") or relation.get("from") or "")
        target = str(relation.get("target_id") or relation.get("target") or relation.get("to") or "")
        if source not in evidence_id_set and target not in evidence_id_set:
            continue
        if source not in node_by_id or target not in node_by_id:
            continue
        relation_type = str(relation.get("relation_type") or relation.get("type") or "related")
        key = f"{source}:{relation_type}:{target}"
        if key in seen:
            continue
        seen.add(key)
        paths.append(
            {
                "source": source,
                "source_label": _node_label(node_by_id[source]) or source,
                "target": target,
                "target_label": _node_label(node_by_id[target]) or target,
                "type": relation_type,
                "description": str(
                    relation.get("description")
                    or (relation.get("metadata") or {}).get("description")
                    or ""
                ),
            }
        )
        if len(paths) >= limit:
            break
    return paths


def format_graph_paths(paths: Sequence[Dict[str, Any]]) -> str:
    if not paths:
        return "No direct graph relation paths among the selected evidence."
    lines = []
    for path in paths:
        description = str(path.get("description") or "").strip()
        suffix = f" — {description}" if description else ""
        lines.append(
            f"- {path.get('source_label') or path.get('source')} --{path.get('type') or 'related'}--> "
            f"{path.get('target_label') or path.get('target')}{suffix}"
        )
    return "\n".join(lines)


def _public_formula_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": record.get("id"),
        "label": record.get("label"),
        "latex": record.get("latex"),
        "source": record.get("source") or {},
        "context": _clip(str(record.get("context") or ""), 500),
        "symbols": record.get("symbols") or [],
        "derives_from": record.get("derives_from") or [],
        "derives_to": record.get("derives_to") or [],
    }


def _formula_ids_in_text(text: str) -> List[str]:
    ids: List[str] = []
    ids.extend(match.group(2).strip() for match in FORMULA_REFERENCE_PATTERN.finditer(text or ""))
    ids.extend(match.group(1).strip() for match in EQUATION_LABEL_PATTERN.finditer(text or ""))
    ids.extend(match.group(1).strip() for match in PAREN_FORMULA_LABEL_PATTERN.finditer(text or ""))
    return _unique_formula_ids(ids)


def _normalize_formula_id_list(value: Any) -> List[str]:
    if isinstance(value, str):
        candidates = re.split(r"[,;\s]+", value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        candidates = [str(item) for item in value]
    else:
        candidates = []
    return _unique_formula_ids(candidates)


def _unique_formula_ids(values: Iterable[Any], *, exclude: Optional[set[str]] = None) -> List[str]:
    exclude_lower = {item.lower() for item in (exclude or set())}
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        lower = text.lower()
        if lower in seen or lower in exclude_lower:
            continue
        seen.add(lower)
        result.append(text)
    return result


def _infer_formula_derivations(structured_dir: Path) -> Dict[str, List[str]]:
    derivations: Dict[str, List[str]] = {}
    for chapter_path in sorted(structured_dir.glob("chapter*.json")):
        try:
            payload = json.loads(chapter_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        blocks = payload.get("blocks")
        if not isinstance(blocks, list):
            continue

        recent_formula_ids: List[str] = []
        pending_reference_ids: List[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            content = str(block.get("content") or "")
            formula_ids = _formula_ids_in_text(content)
            if not formula_ids:
                continue
            inline_ids = [
                formula_id
                for formula_id in formula_ids
                if f"[[FORMULA:{formula_id}]]" not in content
            ]
            direct_formula_ids = [
                formula_id
                for formula_id in formula_ids
                if f"[[FORMULA:{formula_id}]]" in content
            ]
            pending_reference_ids = _unique_formula_ids([*pending_reference_ids, *inline_ids])
            for formula_id in direct_formula_ids:
                upstream = _unique_formula_ids([*recent_formula_ids[-4:], *pending_reference_ids], exclude={formula_id})
                if upstream:
                    derivations[formula_id] = _unique_formula_ids([*derivations.get(formula_id, []), *upstream])
                recent_formula_ids = _unique_formula_ids([*recent_formula_ids, formula_id])[-8:]
                pending_reference_ids = []
    return derivations


def _infer_formula_symbols(
    *,
    formula_id: str,
    latex: str,
    context: str,
    source: Dict[str, Any],
) -> List[Dict[str, str]]:
    symbols = _extract_latex_symbols(latex)
    if not symbols:
        return []
    definitions = _symbol_definitions_from_context(context)
    scope = {
        "chapter": str(source.get("chapter") or ""),
        "unit_id": str(source.get("unit_id") or ""),
        "formula_id": formula_id,
    }
    return [
        {
            "symbol": symbol,
            "meaning": definitions.get(symbol) or _default_symbol_meaning(symbol),
            **scope,
        }
        for symbol in symbols[:18]
    ]


def _extract_latex_symbols(latex: str) -> List[str]:
    text = re.sub(r"\\(?:begin|end)\s*\{[^}]+\}", " ", latex or "")
    command_symbols = re.findall(r"\\(?:Delta|delta|alpha|beta|gamma|sigma|mu|bar|overline)\s*(?:_\s*\{?([A-Za-z])\}?)?", text)
    symbols: List[str] = []
    for command, subscript in re.findall(r"\\(Delta|delta|alpha|beta|gamma|sigma|mu)\s*(?:_\s*\{?([A-Za-z])\}?)?", text):
        symbol = f"\\{command}"
        if subscript:
            symbol = f"{symbol}_{subscript}"
        symbols.append(symbol)
    for command, inner in re.findall(r"\\(bar|overline)\s*\{+\s*([A-Za-z])\s*\}+", text):
        symbols.append(f"\\{command}{{{inner}}}")
    for token in re.findall(r"(?<![A-Za-z\\])([A-Za-z])(?:\s*_\s*\{?([A-Za-z]+)\}?)?", text):
        base, subscript = token
        if base in {"l", "r"}:
            continue
        symbols.append(f"{base}_{subscript}" if subscript else base)
    symbols.extend(f"\\Delta {item}" for item in re.findall(r"\\Delta\s+([A-Za-z])", text))
    return _unique_formula_ids(symbols)


def _symbol_definitions_from_context(context: str) -> Dict[str, str]:
    text = re.sub(r"\s+", " ", context or "")
    definitions: Dict[str, str] = {}
    for symbol, meaning in re.findall(
        r"\$\s*([^$]{1,24}?)\s*\$\s+(?:denotes?|is|means?|represents?)\s+([^.;]{3,180})",
        text,
        flags=re.I,
    ):
        clean_symbol = _normalize_symbol_text(symbol)
        if clean_symbol:
            definitions[clean_symbol] = meaning.strip()
    for meaning, symbol in re.findall(
        r"([^.;]{3,120}?)\s+(?:is denoted by|is written as)\s+\$\s*([^$]{1,24}?)\s*\$",
        text,
        flags=re.I,
    ):
        clean_symbol = _normalize_symbol_text(symbol)
        if clean_symbol:
            definitions[clean_symbol] = meaning.strip()
    return definitions


def _normalize_symbol_text(symbol: str) -> str:
    return re.sub(r"\s+", " ", symbol or "").strip()


def _default_symbol_meaning(symbol: str) -> str:
    known = {
        "p": "allele frequency or probability in the local formula scope",
        "q": "category or allele frequency in the local formula scope",
        "W": "absolute fitness in the local formula scope",
        "w": "relative fitness in the local formula scope",
        "z": "trait value in the local formula scope",
        "R": "response or change measured by the local formula",
        "\\alpha": "average effect in the local formula scope",
        "\\sigma": "variance/covariance operator in the local formula scope",
        "\\Delta p": "change in allele frequency in the local formula scope",
    }
    return known.get(symbol, "meaning depends on the chapter/formula scope")


INTENT_KEYWORDS = [
    ("feedback", ("批改", "评价", "判断", "答案", "得分", "哪里错", "对不对")),
    ("hint", ("提示", "hint", "思路", "下一步")),
    ("practice", ("练习", "习题", "做题", "题目", "practice")),
    ("quiz", ("测验", "quiz", "选择题", "填空题", "简答题")),
    ("example", ("例子", "举例", "example")),
    ("next_step", ("下一步", "推荐", "学习路径", "复习")),
    ("explain", ("解释", "讲解", "什么是", "为什么", "如何理解")),
]


SUPPORTED_RELATION_TYPES = {
    "contains",
    "related",
    "precedes",
    "prerequisite_of",
    "defines",
    "derives",
    "explains",
    "depends_on",
    "example_of",
    "exercise_of",
    "misconception_of",
    "references_formula",
    "references_table",
    "references_figure",
    "references_example",
    "supports",
    "causes",
}


def infer_learner_intent(text: str, default: str = "explain") -> str:
    normalized = (text or "").lower()
    for intent, keywords in INTENT_KEYWORDS:
        if any(keyword.lower() in normalized for keyword in keywords):
            return intent
    return default


def evidence_from_rag(items: Sequence[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    return [
        _normalize_evidence_item(item, index=index, default_source="retrieval")
        for index, item in enumerate(items[:limit], start=1)
    ]


def evidence_from_graph(
    graph_data: Optional[Dict[str, Any]],
    *,
    query: str = "",
    chapter_data: Optional[Dict[str, Any]] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    if not graph_data:
        return []

    nodes = graph_data.get("nodes") or []
    if not isinstance(nodes, list):
        return []

    query_text = " ".join(
        part
        for part in [
            query,
            str((chapter_data or {}).get("title") or ""),
            str((chapter_data or {}).get("content") or "")[:1200],
        ]
        if part
    )
    tokens = _tokenize(query_text)

    scored_nodes = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        label = _node_label(node)
        content = _node_content(node)
        node_text = f"{label} {content} {json.dumps(node.get('metadata') or {}, ensure_ascii=False)}"
        score = _overlap_score(tokens, node_text)
        if chapter_data and label and label in str(chapter_data.get("title") or ""):
            score += 5
        scored_nodes.append((score, index, node))

    scored_nodes.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    if tokens and any(score > 0 for score, _, _ in scored_nodes):
        selected = [node for score, _, node in scored_nodes if score > 0][:limit]
    else:
        selected = [node for _, _, node in scored_nodes[:limit]]

    return [
        _normalize_evidence_item(node, index=index, default_source="graph")
        for index, node in enumerate(selected, start=1)
    ]


def relation_evidence_from_graph(
    graph_data: Optional[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    limit: int = 12,
) -> List[Dict[str, Any]]:
    if not graph_data or not evidence:
        return []

    relations = graph_data.get("relations") or graph_data.get("edges") or []
    if not isinstance(relations, list):
        return []

    evidence_ids = {str(item.get("id") or "") for item in evidence if item.get("id")}
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for relation in relations:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source_id") or relation.get("source") or relation.get("from") or "")
        target = str(relation.get("target_id") or relation.get("target") or relation.get("to") or "")
        relation_type = str(
            relation.get("relation_type") or relation.get("type") or relation.get("label") or "related"
        )
        if evidence_ids and source not in evidence_ids and target not in evidence_ids:
            continue
        if relation_type not in SUPPORTED_RELATION_TYPES and not relation_type:
            continue
        key = f"{source}:{relation_type}:{target}"
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "source": source,
                "target": target,
                "type": relation_type,
                "metadata": relation.get("metadata") or relation.get("properties") or {},
            }
        )
        if len(result) >= limit:
            break
    return result


def build_learning_plan(
    *,
    query: str,
    evidence: Sequence[Dict[str, Any]],
    relations: Optional[Sequence[Dict[str, Any]]] = None,
    learner_intent: Optional[str] = None,
    learning_level: str = "beginner",
    task: str = "qa",
    chapter_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_evidence = [
        _normalize_evidence_item(item, index=index, default_source="graph")
        for index, item in enumerate(evidence, start=1)
    ]
    allowed = _allowed_concepts(normalized_evidence)
    primary = allowed[0] if allowed else {}
    subject_name = (
        str((chapter_data or {}).get("title") or "").strip()
        or str(primary.get("name") or "").strip()
        or "未匹配到图谱主题"
    )
    subject_id = str((chapter_data or {}).get("id") or primary.get("id") or "").strip()
    intent = learner_intent or infer_learner_intent(query, default=_default_intent_for_task(task))
    learning_relations = list(relations or [])

    return {
        "subject": {
            "id": subject_id,
            "name": subject_name,
            "type": str(primary.get("type") or "topic"),
            "match_score": 1.0 if allowed else 0.0,
        },
        "learner_intent": intent,
        "learning_level": learning_level,
        "task": task,
        "allowed_concepts": allowed,
        "slots": _build_slots(normalized_evidence, learning_relations),
        "learning_intent_graph": {
            "nodes": [item["name"] for item in allowed],
            "edges": [
                {
                    "from": relation.get("source"),
                    "to": relation.get("target"),
                    "type": relation.get("type") or "related",
                }
                for relation in learning_relations
            ],
        },
        "evidence": normalized_evidence,
        "constraints": DEFAULT_CONSTRAINTS,
    }


def format_learning_plan(plan: Dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2)


def format_evidence(evidence: Sequence[Dict[str, Any]]) -> str:
    if not evidence:
        return "无可用图谱证据。"
    lines = []
    for item in evidence:
        source = item.get("source") or "graph"
        label = item.get("label") or item.get("id") or "context"
        node_type = item.get("type") or "context"
        content = _clip(str(item.get("content") or ""), 700)
        lines.append(f"[{item.get('index', '?')}] ({source}/{node_type}) {label}: {content}")
    return "\n".join(lines)


def build_constrained_generation_prompt(
    *,
    task_title: str,
    user_input: str,
    learning_plan: Dict[str, Any],
    requirements: Iterable[str],
    source_content: str = "",
) -> str:
    """Build a soft KG-guided prompt without exposing pipeline internals."""
    requirement_text = "\n".join(f"{index}. {item}" for index, item in enumerate(requirements, start=1))
    source_block = f"\nSource/chapter content:\n{source_content.strip()}\n" if source_content.strip() else ""
    evidence = learning_plan.get("evidence") or []
    allowed = learning_plan.get("allowed_concepts") or []
    core_concepts = _expected_entities_for_task(_entity_records_from_allowed(allowed), learning_plan, str(learning_plan.get("task") or "qa"))
    concept_names = ", ".join(str(item.get("name") or "") for item in core_concepts[:6] if item.get("name"))
    concept_line = f"\nCore terms to preserve when relevant: {concept_names}\n" if concept_names else ""
    return f"""Task: {task_title}
User input: {user_input}
{source_block}
Relevant evidence:
{format_evidence(evidence)}
{concept_line}
Requirements:
{requirement_text}

Use the evidence as helpful context, not as a hard gate. Do not mention LearningPlan, GC-DPG, graph constraints, phases, or self-checks. Keep English source wording, formulas, variables, and technical terms in English. Output only the final user-facing content."""


def build_lecture_gc_dpg_requirements(style: str, *, slide_level: bool = False) -> List[str]:
    """Return lecture-only KG planning rules inspired by graph-constrained generation."""
    scope = "this slide" if slide_level else "the chapter"
    return [
        f"Teaching style: {style}.",
        "Before writing, internally select only 3-6 highly relevant concepts and 1-3 relation paths from the evidence; use them as planning anchors only.",
        f"Write natural Markdown teaching prose for {scope}, not a concept inventory, extraction report, evidence report, or outline.",
        "Ground each core explanation in the source content or retrieved evidence; when extending beyond evidence, keep the extension limited and clearly tied to the lesson goal.",
        "When introducing a core concept, formula, theorem, or example from the evidence, keep its original term or formula label in the first mention, then explain it naturally.",
        "Mention technical terms only where they are needed to explain a definition, formula, relation, example, or misconception.",
        "Avoid clustered lists of names or concepts. Prefer short explanatory paragraphs, examples, teacher questions, and transitions.",
        "State each concept and conclusion once. Never repeat or restate a point for emphasis; every idea should be explained in one clear pass.",
        "Keep English source terms, formulas, variables, and key definitions in English when the source is English; Chinese explanation may support but must not change the meaning.",
        "Do not add provenance labels, entity sections, extraction sections, AI-origin labels, HTML spans, JSON, notes, or self-check text.",
    ]


def clean_generated_lecture_output(text: str) -> str:
    """Remove model-internal reasoning and legacy wrappers from lecture text."""
    cleaned = str(text or "")
    original = cleaned
    had_internal_wrapper = bool(re.search(r"(?is)<(?:think|reasoning)\b[^>]*>", cleaned))
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*?</think>", "", cleaned)
    cleaned = re.sub(r"(?is)<reasoning\b[^>]*>.*?</reasoning>", "", cleaned)

    final_marker = re.search(
        r"(?im)^\s*(?:#+\s*)?(?:最终(?:答案|文案|输出|讲稿|授课文案)|正式(?:文案|讲稿)|"
        r"Final(?:\s+(?:answer|output|lecture\s+script))?|Lecture\s+script|授课文案|正文)\s*[:：]\s*",
        cleaned,
    )
    if final_marker:
        prefix = cleaned[: final_marker.start()]
        if re.search(
            r"(?i)<think|reasoning|chain\s*of\s*thought|scratchpad|analysis|"
            r"思考过程|推理过程|分析过程|内部推理|我的思路|我需要|我会先",
            prefix,
        ):
            cleaned = cleaned[final_marker.end() :]

    cleaned = re.sub(
        r"(?ims)^\s*(?:#+\s*)?(?:思考过程|推理过程|分析过程|内部推理|我的思路|"
        r"Reasoning|Analysis|Chain\s*of\s*thought|Scratchpad)\s*[:：]?\s*\n.*?"
        r"(?=^\s*(?:#+\s*)?(?:最终(?:答案|文案|输出|讲稿|授课文案)|正式(?:文案|讲稿)|"
        r"Final(?:\s+(?:answer|output|lecture\s+script))?|Lecture\s+script|授课文案|正文)\s*[:：]?\s*$|\Z)",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?im)^\s*(?:#+\s*)?(?:最终(?:答案|文案|输出|讲稿|授课文案)|正式(?:文案|讲稿)|"
        r"Final(?:\s+(?:answer|output|lecture\s+script))?|Lecture\s+script|授课文案|正文)\s*[:：]\s*",
        "",
        cleaned,
        count=1,
    )
    cleaned = re.sub(r"</?span\b[^>]*>", "", cleaned, flags=re.I)
    label_pattern = (
        r"(?:图谱实体|知识图谱实体|提取实体|抽取实体|实体清单|关键实体|AI补充|AI\s*补充|"
        r"Graph\s*entities|Extracted\s*entities|Key\s*entities|AI\s*supplement|AI\s*additions)"
    )
    cleaned = re.sub(
        rf"(?im)^\s*(?:[-*]\s*)?\*\*\s*{label_pattern}\s*\*\*\s*[:：].*(?:\n|$)",
        "",
        cleaned,
    )
    cleaned = re.sub(
        rf"(?im)^\s*(?:[-*]\s*)?{label_pattern}\s*[:：].*(?:\n|$)",
        "",
        cleaned,
    )
    cleaned = re.sub(
        rf"(?ims)^\s*(?:#+\s*)?{label_pattern}\s*\n(?:[-*]\s*)?.*?(?=\n\s*#|\n\s*\*\*|\Z)",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    # Keep a pause between Latin words and trailing punctuation. Without the
    # space some TTS engines read constructs such as ``model-中文`` letter by
    # letter instead of as natural mixed-language speech.
    cleaned = re.sub(r"([A-Za-z])([\-–—/:;，。！？、])(?=\S)", r"\1\2 ", cleaned)
    # Also separate dangling connectors (e.g. ``macro-``) and connectors
    # immediately before Chinese text, while preserving Latin compounds.
    cleaned = re.sub(r"([A-Za-z])([\-–—])", r"\1 \2", cleaned)
    cleaned = cleaned.strip()
    if cleaned:
        if _looks_like_internal_reasoning_only(cleaned):
            return ""
        return cleaned
    if had_internal_wrapper or _looks_like_internal_reasoning_only(original):
        return ""
    return original.strip()


def _looks_like_internal_reasoning_only(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    final_marker = re.search(
        r"(?im)^\s*(?:#+\s*)?(?:最终(?:答案|文案|输出|讲稿|授课文案)|正式(?:文案|讲稿)|"
        r"Final(?:\s+(?:answer|output|lecture\s+script))?|Lecture\s+script|授课文案|正文)\s*[:：]",
        value,
    )
    if final_marker:
        return False
    if re.match(
        r"(?is)^\s*(?:#+\s*)?(?:思考过程|推理过程|分析过程|内部推理|我的思路|"
        r"Reasoning|Analysis|Chain\s*of\s*thought|Scratchpad)\s*[:：]?",
        value,
    ):
        return True
    if re.match(
        r"(?is)^\s*(?:okay|ok|alright|let'?s|first,?\s+i|i\s+need\s+to|we\s+need\s+to|"
        r"the\s+user\s+(?:wants|asks|requested)|need\s+to|need\s+craft|need\s+generate)\b",
        value,
    ) and re.search(
        r"(?is)\b(?:prompt|slide|lecture|script|generate|craft|output|reasoning|answer|final|"
        r"evidence|graph|entities|user|requirements?)\b|讲稿|逐页|文案|课件|图谱|实体",
        value[:1200],
    ):
        return True
    return bool(
        re.match(
            r"(?is)^\s*(?:我需要|我会先|先分析|我们需要先)\s*"
            r"(?:分析|规划|检查|根据|为|生成|确保|构思|整理)",
            value,
        )
    )


def check_generation_consistency(
    output: str,
    learning_plan: Dict[str, Any],
    *,
    task: str = "qa",
) -> Dict[str, Any]:
    evidence = learning_plan.get("evidence") or []
    allowed = learning_plan.get("allowed_concepts") or []
    output_text = output or ""
    output_lower = output_text.lower()

    support_entities = _entity_records_from_allowed(allowed)
    expected_entities = _expected_entities_for_task(support_entities, learning_plan, task)
    mentioned_entities = [
        entity for entity in expected_entities
        if _entity_mentioned(entity["name"], output_text)
    ]
    mentioned_ids = {entity["id"] for entity in mentioned_entities}
    missing_entities = [
        entity for entity in expected_entities
        if entity["id"] not in mentioned_ids
    ]
    extracted_entities = _extract_entity_candidates(output_text)
    support_names = [entity["name"] for entity in support_entities]
    unsupported_entities = [
        entity for entity in extracted_entities
        if not _candidate_supported(entity["name"], support_names)
    ][:30]

    matched = len(mentioned_entities)
    evidence_count = len(evidence)
    support_ratio = _knowledge_support_ratio(
        output_text=output_text,
        evidence_count=evidence_count,
        expected_count=len(expected_entities),
        matched_count=matched,
    )

    insufficiency_acknowledged = (
        "当前图谱依据不足" in output_text
        or "图谱依据不足" in output_text
        or "insufficient evidence" in output_lower
        or "need more evidence" in output_lower
        or "not enough evidence" in output_lower
    )
    hint_policy_violated = (
        (learning_plan.get("learner_intent") in {"hint", "practice", "feedback"} or task in {"hint", "practice", "feedback"})
        and ("正确答案" in output_text or "标准答案" in output_text)
        and "除非" not in output_text
    )
    warnings: List[str] = []
    if not evidence_count and output_text.strip() and not insufficiency_acknowledged:
        warnings.append("未检索到可用图谱证据；该回答应被视为未由图谱验证。")
    if hint_policy_violated:
        warnings.append("练习/提示场景可能直接泄露完整答案。")

    is_safe = bool(output_text.strip()) and not hint_policy_violated

    return {
        "knowledge_support_ratio": round(min(1.0, support_ratio), 3),
        "unsupported_concept_rate": 0.0 if evidence_count else (0.5 if output_text.strip() else 1.0),
        "entity_recall": round(matched / len(expected_entities), 3) if expected_entities else 0.0,
        "entity_hallucination_rate": round(
            len(unsupported_entities) / max(1, len(extracted_entities)),
            3,
        ) if extracted_entities else 0.0,
        "expected_entities": expected_entities,
        "mentioned_entities": mentioned_entities,
        "missing_entities": missing_entities,
        "extracted_entities": extracted_entities[:60],
        "unsupported_entities": unsupported_entities,
        "learning_goal_alignment": 1.0 if output_text.strip() else 0.0,
        "difficulty_match": "appropriate",
        "hint_policy_violated": hint_policy_violated,
        "is_safe_to_show": is_safe,
        "warnings": warnings,
    }


def build_kg_grounded_exercise(
    *,
    chapter_id: str,
    chapter_title: str,
    chapter_content: str = "",
    evidence: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    normalized = [
        _normalize_evidence_item(item, index=index, default_source="graph")
        for index, item in enumerate(evidence, start=1)
    ]
    if not normalized:
        return {
            "id": f"ex_{_safe_id(chapter_id)}_kg_gap",
            "question": f"当前图谱是否有足够证据为《{chapter_title or chapter_id}》生成可靠练习？",
            "options": [
                "A. 有，且可以自由扩展图谱外概念",
                "B. 有，但只能使用教师输入的口头常识",
                "C. 没有，但可以猜测标准答案",
                "D. 没有，需要先补充知识图谱证据",
            ],
            "correct_answer": "D",
            "explanation": "当前检索不到可用图谱证据。系统应先补充或导入相关知识图谱，再生成练习。",
            "learning_plan": build_learning_plan(
                query=chapter_title or chapter_id,
                evidence=[],
                task="practice",
                chapter_data={"id": chapter_id, "title": chapter_title, "content": chapter_content},
            ),
        }

    item = normalized[0]
    label = str(item.get("label") or chapter_title or "该知识点")
    content = _clip(str(item.get("content") or label), 140)
    explanation = f"依据图谱证据[{item.get('index', 1)}]\u201c{label}\u201d：{content}"
    plan = build_learning_plan(
        query=chapter_title or label,
        evidence=normalized,
        learner_intent="practice",
        task="practice",
        chapter_data={"id": chapter_id, "title": chapter_title, "content": chapter_content},
    )
    return {
        "id": f"ex_{_safe_id(chapter_id)}_kg_1",
        "question": f"根据当前知识图谱，关于\u201c{label}\u201d最可靠的说法是哪一项？",
        "options": [
            f"A. {content}",
            "B. 可以直接引入图谱中未出现的高级概念作为结论",
            "C. 即使没有证据，也可以补全不存在的概念关系",
            "D. 该知识点与本章没有任何关系",
        ],
        "correct_answer": "A",
        "explanation": explanation,
        "source_evidence": normalized[:3],
        "learning_plan": plan,
    }


def _normalize_evidence_item(item: Dict[str, Any], *, index: int, default_source: str) -> Dict[str, Any]:
    metadata = dict(item.get("metadata") or {})
    content = expand_formula_references(_node_content(item))
    label = expand_formula_references(_node_label(item), display=False)
    return {
        "index": item.get("index") or index,
        "id": str(item.get("id") or item.get("node_id") or metadata.get("id") or label or f"evidence_{index}"),
        "label": label or f"evidence_{index}",
        "type": str(item.get("type") or metadata.get("type") or "concept"),
        "content": _clip(content or label, 900),
        "source": str(metadata.get("source") or item.get("source") or default_source),
    }


def _allowed_concepts(evidence: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    concepts: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence:
        key = str(item.get("id") or item.get("label") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        concepts.append(
            {
                "id": key,
                "name": str(item.get("label") or key),
                "type": str(item.get("type") or "concept"),
                "source_index": item.get("index"),
            }
        )
    return concepts


def _build_slots(evidence: Sequence[Dict[str, Any]], relations: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    slots: List[Dict[str, Any]] = []
    concept_entities = [
        {"id": item.get("id"), "name": item.get("label")}
        for item in evidence
        if item.get("id") and item.get("label")
    ]
    if concept_entities:
        slots.append({"type": "definition", "coverage": "full", "entities": concept_entities[:8]})

    prereq_entities = [
        {"id": relation.get("source"), "name": relation.get("source")}
        for relation in relations
        if relation.get("type") in {"precedes", "prerequisite_of", "depends_on"}
    ]
    if prereq_entities:
        slots.append({"type": "prerequisite", "coverage": "partial", "entities": prereq_entities[:6]})

    exercise_entities = [
        {"id": item.get("id"), "name": item.get("label")}
        for item in evidence
        if str(item.get("type") or "").lower() in {"exercise", "quiz", "question"}
    ]
    if exercise_entities:
        slots.append({"type": "practice", "coverage": "full", "entities": exercise_entities[:6]})

    return slots


def _entity_records_from_allowed(allowed: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    entities: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, concept in enumerate(allowed, start=1):
        name = _expected_entity_name(concept)
        if not name:
            continue
        key = _entity_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        entities.append(
            {
                "id": str(concept.get("id") or key),
                "name": name,
                "type": str(concept.get("type") or "concept"),
                "source_index": concept.get("source_index") or index,
            }
        )
    return entities


def _knowledge_support_ratio(
    *,
    output_text: str,
    evidence_count: int,
    expected_count: int,
    matched_count: int,
) -> float:
    if not output_text.strip():
        return 0.0
    if evidence_count <= 0:
        return 0.0
    if expected_count <= 0:
        return 1.0
    return matched_count / max(1, expected_count)


def _expected_entities_for_task(
    entities: Sequence[Dict[str, Any]],
    learning_plan: Dict[str, Any],
    task: str,
) -> List[Dict[str, Any]]:
    if task != "lecture":
        return list(entities)

    selected: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add_entity(entity: Optional[Dict[str, Any]]) -> None:
        if not entity:
            return
        key = _entity_key(str(entity.get("name") or entity.get("id") or ""))
        if not key or key in seen:
            return
        seen.add(key)
        selected.append(dict(entity))

    def add_reference(reference: Any) -> None:
        if not isinstance(reference, dict):
            return
        ref_id = str(reference.get("id") or "").strip()
        ref_name = str(reference.get("name") or reference.get("label") or "").strip()
        ref_key = _entity_key(ref_name)
        for entity in entities:
            if ref_id and str(entity.get("id") or "") == ref_id:
                add_entity(entity)
                return
            if ref_key and _entity_key(str(entity.get("name") or "")) == ref_key:
                add_entity(entity)
                return

    add_reference(learning_plan.get("subject"))
    for slot in learning_plan.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        for entity in slot.get("entities") or []:
            add_reference(entity)
            if len(selected) >= LECTURE_EXPECTED_ENTITY_LIMIT:
                return selected[:LECTURE_EXPECTED_ENTITY_LIMIT]

    if selected:
        return selected[:LECTURE_EXPECTED_ENTITY_LIMIT]

    for entity in entities:
        if _is_core_consistency_entity(entity):
            add_entity(entity)
        if len(selected) >= LECTURE_EXPECTED_ENTITY_LIMIT:
            break

    return (selected or list(entities)[:LECTURE_EXPECTED_ENTITY_LIMIT])[:LECTURE_EXPECTED_ENTITY_LIMIT]


def _is_core_consistency_entity(entity: Dict[str, Any]) -> bool:
    name = str(entity.get("name") or "").strip()
    node_type = str(entity.get("type") or "").strip().lower()
    if not name or len(name) > 120:
        return False
    return node_type in CORE_CONSISTENCY_TYPES


def _expected_entity_name(concept: Dict[str, Any]) -> str:
    name = str(concept.get("name") or "").strip()
    if not name:
        return ""
    node_type = str(concept.get("type") or "").strip().lower()
    node_id = str(concept.get("id") or "").strip().lower()
    if _is_evidence_block_label(name, node_type, node_id):
        return ""
    return name


def _is_evidence_block_label(name: str, node_type: str, node_id: str) -> bool:
    normalized = _text_match_key(name)
    if node_id.startswith("block::"):
        return True
    if re.match(r"^chapter\d+\s+\d+\s+(?:proposition|derivation|example|exercise|note)\b", normalized):
        return True
    if len(name) > 140 and node_type in {"proposition", "derivation", "observation", "note"}:
        return True
    return False


def _entity_mentioned(name: str, text: str) -> bool:
    clean_name = str(name or "").strip()
    if not clean_name or not text:
        return False
    if re.search(r"[\u4e00-\u9fff]", clean_name):
        if clean_name in text:
            return True
    name_key = _text_match_key(clean_name)
    text_key = _text_match_key(text)
    if not name_key or not text_key:
        return False
    if _phrase_in_text(name_key, text_key):
        return True
    return _salient_token_match(name_key, text_key)


def _extract_entity_candidates(text: str) -> List[Dict[str, Any]]:
    clean_text = re.sub(r"<[^>]+>", " ", text or "")
    patterns = [
        r"\b(?:Equation|Eq\.?|Formula|Table|Figure)\s+[0-9]+(?:\.[0-9]+[a-z]?)?\b",
        r"\b[A-Z][A-Za-z]*(?:['\u2019]s)?(?:[- ][A-Za-z][A-Za-z]*(?:['\u2019]s)?){0,4}\s+(?:[Tt]heorem|[Ii]dentity|[Ee]quation|[Rr]ule|[Ll]aw|[Pp]rinciple|[Mm]odel)\b",
        r"\b[A-Z][A-Za-z0-9]*(?:['\u2019]s)?(?:[- ][A-Z][A-Za-z0-9]*(?:['\u2019]s)?){1,5}\b",
    ]
    stopwords = {
        "the", "and", "for", "with", "this", "that", "from", "into", "then", "when",
        "where", "which", "because", "there", "their", "will", "can", "may", "should",
        "chapter", "section", "example", "question", "answer", "markdown", "ai",
        "it", "its", "we", "you", "they", "are", "was", "were", "is", "be", "been",
    }
    seen: set[str] = set()
    entities: List[Dict[str, Any]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, clean_text):
            value = re.sub(r"\s+", " ", match.group(0)).strip(" -_.,:;()[]{}")
            if len(value) < 2:
                continue
            key = _entity_key(value)
            if not key or key in seen or key in stopwords:
                continue
            if _candidate_is_noise(value, stopwords):
                continue
            if not re.search(r"[\u4e00-\u9fff]", value) and value.lower() in stopwords:
                continue
            seen.add(key)
            entities.append({"name": value, "count": len(re.findall(re.escape(value), clean_text, re.I))})
            if len(entities) >= 120:
                return entities
    return entities


def _candidate_supported(candidate: str, expected_names: Sequence[str]) -> bool:
    candidate_key = _entity_key(candidate)
    if not candidate_key:
        return True
    for expected in expected_names:
        if _entity_mentioned(candidate, expected) or _entity_mentioned(expected, candidate):
            return True
    return False


def _entity_key(value: str) -> str:
    return _text_match_key(value)


def _text_match_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"[\u2018\u2019\u201b`]", "'", text)
    text = re.sub(r"\b([a-z]+)'s\b", r"\1", text)
    text = text.replace("'", "")
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _phrase_in_text(name_key: str, text_key: str) -> bool:
    return re.search(rf"(?<![0-9a-z]){re.escape(name_key)}(?![0-9a-z])", text_key) is not None


def _salient_tokens(match_key: str) -> List[str]:
    stopwords = {
        "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
        "chapter", "section", "proposition", "derivation", "example", "note",
    }
    return [
        token
        for token in match_key.split()
        if token not in stopwords and (len(token) >= 3 or re.search(r"\d", token))
    ]


def _salient_token_match(name_key: str, text_key: str) -> bool:
    name_tokens = _salient_tokens(name_key)
    if not name_tokens:
        return False
    generic_starts = {"equation", "formula", "table", "figure"}
    if name_tokens[0] in generic_starts:
        return False
    text_tokens = set(_salient_tokens(text_key))
    overlap = sum(1 for token in name_tokens if token in text_tokens)
    if len(name_tokens) == 1:
        return overlap == 1
    threshold = 2 if len(name_tokens) <= 3 else max(3, int(len(name_tokens) * 0.65 + 0.999))
    first_token = name_tokens[0]
    generic_starts = {"theorem", "identity"}
    if first_token not in generic_starts and first_token not in text_tokens:
        return False
    return overlap >= threshold


def _candidate_is_noise(value: str, stopwords: set[str]) -> bool:
    key = _text_match_key(value)
    tokens = key.split()
    if not tokens:
        return True
    if tokens[0] in stopwords:
        return True
    if not re.search(r"[\u4e00-\u9fff]", value):
        useful = [token for token in tokens if token not in stopwords and len(token) >= 3]
        if not useful:
            return True
    return False


def _node_label(item: Dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    return str(
        item.get("label")
        or metadata.get("label")
        or metadata.get("title")
        or item.get("title")
        or item.get("node_id")
        or item.get("id")
        or ""
    ).strip()


def _node_content(item: Dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    return str(
        item.get("content")
        or metadata.get("content")
        or metadata.get("description")
        or item.get("description")
        or _node_label(item)
        or ""
    ).strip()


def _clip(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _tokenize(text: str) -> set[str]:
    lowered = (text or "").lower()
    ascii_tokens = {token for token in re.findall(r"[a-zA-Z0-9_]{2,}", lowered) if len(token) >= 2}
    cjk_tokens = {token for token in re.findall(r"[\u4e00-\u9fff]{2,}", lowered)}
    grams: set[str] = set()
    for token in cjk_tokens:
        grams.update(token[index : index + 2] for index in range(max(1, len(token) - 1)))
    return ascii_tokens | grams


def _overlap_score(tokens: set[str], text: str) -> int:
    if not tokens:
        return 0
    target = (text or "").lower()
    return sum(1 for token in tokens if token in target)


def _default_intent_for_task(task: str) -> str:
    return {
        "lecture": "explain",
        "qa": "explain",
        "exercise": "practice",
        "practice": "practice",
        "feedback": "feedback",
    }.get(task, "explain")


def _safe_id(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", value or "chapter").strip("_")
    return clean or "chapter"
