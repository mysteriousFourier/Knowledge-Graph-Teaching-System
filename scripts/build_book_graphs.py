#!/usr/bin/env python3
"""Build independent SQLite knowledge graphs from packaged book deliveries."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_library(target: Dict[str, Any], payload: Dict[str, Any], key: str) -> None:
    values = payload.get(key)
    if isinstance(values, list):
        target.setdefault(key, []).extend(item for item in values if isinstance(item, dict))
    elif isinstance(values, dict):
        target.setdefault(key, {}).update(values)


def _prepare_input(pack_dir: Path, input_dir: Path) -> Path:
    if input_dir.exists():
        shutil.rmtree(input_dir)
    structured = input_dir / "structured"
    structured.mkdir(parents=True, exist_ok=True)
    counters = {"structured": 0, "formulas": 0, "tables": 0, "examples": 0, "figures": 0}
    formulas: Dict[str, Any] = {"book": pack_dir.name, "formulas": []}
    tables: Dict[str, Any] = {"book": pack_dir.name, "tables": []}
    examples: Dict[str, Any] = {"book": pack_dir.name, "examples": []}
    figures: Dict[str, Any] = {"book": pack_dir.name, "figures": []}
    source_index = 0
    for chapter_dir in sorted((p for p in pack_dir.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        for source in sorted((chapter_dir / "structured").glob("*.json")):
            payload = _json(source)
            destination = structured / f"{source_index:05d}_{source.name}"
            destination.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            source_index += 1
            counters["structured"] += 1
        for library_name, target, key in (
            ("formula_library.json", formulas, "formulas"),
            ("table_library.json", tables, "tables"),
            ("example_library.json", examples, "examples"),
            ("figure_library.json", figures, "figures"),
        ):
            library = chapter_dir / "libraries" / library_name
            if not library.exists():
                continue
            payload = _json(library)
            if isinstance(payload, dict):
                _merge_library(target, payload, key)
    for key, target in (("formulas", formulas), ("tables", tables), ("examples", examples)):
        target["count"] = len(target[key])
        if target[key]:
            (structured / f"{key[:-1]}_library.json").write_text(json.dumps(target, ensure_ascii=False), encoding="utf-8")
            counters[key] = len(target[key])
    if figures["figures"]:
        (structured / "figure_library.json").write_text(json.dumps(figures, ensure_ascii=False), encoding="utf-8")
        counters["figures"] = len(figures["figures"])
    (input_dir / "input_summary.json").write_text(json.dumps(counters, ensure_ascii=False, indent=2), encoding="utf-8")
    return structured


def _build_one(*, graph_id: str, book: str, pack_dir: Path, output_root: Path, skip_semantic: bool) -> Dict[str, Any]:
    from KGTS.core.graph_service import GraphService
    from KGTS.maintenance import sync_builders, sync_core, sync_utils

    input_dir = output_root / "inputs" / graph_id
    structured_dir = _prepare_input(pack_dir, input_dir)
    db_path = output_root / "graphs" / graph_id / "knowledge_graph.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    source_patches = [
        patch.object(sync_builders, "STRUCTURED_DIR", structured_dir),
        patch.object(sync_utils, "STRUCTURED_DIR", structured_dir),
        patch.object(sync_builders, "TOC_EXPORT_DIR", Path("")),
        patch.object(sync_utils, "TOC_EXPORT_DIR", Path("")),
        patch.object(sync_builders, "BOOK_TITLE", book),
        patch.object(sync_core, "MANIFEST_PATH", input_dir / "structured_sync_manifest.json"),
        patch.object(sync_core, "TEACHER_PACKAGE_PATH", input_dir / "teacher_memory_package.json"),
        patch.object(sync_core, "_ensure_data_dir", lambda: input_dir.mkdir(parents=True, exist_ok=True)),
    ]
    with source_patches[0], source_patches[1], source_patches[2], source_patches[3], source_patches[4], source_patches[5], source_patches[6], source_patches[7]:
        specs, chapters = sync_builders._collect_specs(skip_semantic=skip_semantic)
    nodes = [node for spec in specs for node in spec.nodes]
    relations = [relation for spec in specs for relation in spec.relations]
    graph = GraphService(db_path=db_path)
    imported = graph.batch_import_graph(nodes, relations)
    stats = graph.get_graph_statistics()
    return {
        "graph_id": graph_id,
        "book": book,
        "source_pack": str(pack_dir.relative_to(ROOT)).replace("\\", "/"),
        "db_path": str(db_path.relative_to(ROOT)).replace("\\", "/"),
        "input_dir": str(input_dir.relative_to(ROOT)).replace("\\", "/"),
        "chapters": chapters,
        "nodes": len(nodes),
        "relations": len(relations),
        "imported": imported,
        "stats": stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--popgen", type=Path, default=ROOT / "Pack" / "PopGen_chapters_2_3_4_6")
    parser.add_argument("--genetics", type=Path, default=ROOT / "GeneticsPack")
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "seed")
    parser.add_argument("--include-semantic", action="store_true")
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    results = [
        _build_one(graph_id="popgen", book="Principles of Population Genetics", pack_dir=args.popgen.resolve(), output_root=output_root, skip_semantic=not args.include_semantic),
        _build_one(graph_id="genetics", book="Genetics and Analysis of Quantitative Traits", pack_dir=args.genetics.resolve(), output_root=output_root, skip_semantic=not args.include_semantic),
    ]
    registry = {
        "schema": "kgts.graph_registry.v1",
        "retrieval": {"mode": "cross_graph", "independent_graphs": True},
        "graphs": [{
                "graph_id": "legacy_seed",
                "book": "KGTS existing seed graph",
                "source_pack": "data/seed/knowledge_graph.db",
                "db_path": "data/seed/knowledge_graph.db",
                "enabled": True,
            }] + [
            {
                "graph_id": item["graph_id"],
                "book": item["book"],
                "source_pack": item["source_pack"],
                "db_path": item["db_path"],
                "enabled": True,
            }
            for item in results
        ],
    }
    registry_path = output_root / "graph_registry.json"
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"success": True, "registry": str(registry_path.relative_to(ROOT)).replace("\\", "/"), "graphs": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
