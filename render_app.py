from __future__ import annotations

import json
import os
import shutil
import sys
import types
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles


ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend" / "dist"
EDUCATION_DIR = BACKEND_DIR / "education"
MAINTENANCE_DIR = BACKEND_DIR / "maintenance"
VECTOR_DIR = BACKEND_DIR / "vector_index_system"
GRAPH_ADMIN_HTML = VECTOR_DIR / "knowledge_graph" / "backend_admin.html"
SEED_DIR = ROOT_DIR / "data" / "seed"
SEED_CHAPTERS_FILE = SEED_DIR / "chapters.json"
SEED_VECTOR_INDEX_DIR = SEED_DIR / "vector_index"


# Bootstrap: register KGTS as a namespace package so that
# ``from KGTS.xxx`` imports resolve when running standalone.
_KGTS_NAME = "KGTS"
if _KGTS_NAME not in sys.modules:
    _kgts_pkg = types.ModuleType(_KGTS_NAME)
    _kgts_pkg.__path__ = [str(ROOT_DIR)]
    sys.modules[_KGTS_NAME] = _kgts_pkg

for path in (ROOT_DIR, BACKEND_DIR, EDUCATION_DIR, MAINTENANCE_DIR, VECTOR_DIR):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)


from app_config import load_root_env, get_bind_host  # noqa: E402


load_root_env()

from KGTS.core.graph_service import GraphService, PRESET_RELATION_TYPES, _default_db_path  # noqa: E402
from backend.maintenance.structured_sync import scan_structured_sources  # noqa: E402

# Import after sys.path setup. These modules keep the existing teacher/student API
# behavior, while this file only adapts how they are served on Render.
from backend.education import api_server as education_api  # noqa: E402
from backend.maintenance import api_server as maintenance_api  # noqa: E402
from education.beamer_full_router import router as beamer_generator_router  # noqa: E402
from core.bridge import delete_generated_lecture_nodes, _is_generated_shell_chapter_node  # noqa: E402
from education.tts_router import router as tts_router  # noqa: E402
from core.path_policy import project_local_only  # noqa: E402
from core import context_cache  # noqa: E402
from core.seed import ensure_seed_graph  # noqa: E402
from core.tts_service import get_tts_status, run_tts_startup_cleanup  # noqa: E402


app = FastAPI(
    title="Knowledge-Graph-Teaching-System",
    description="Single FastAPI web service for Render deployment.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.include_router(tts_router)


def _append_api_routes(source_app: FastAPI, *, skip_paths: Iterable[str] = ()) -> None:
    skipped = set(skip_paths)
    for route in source_app.router.routes:
        route_path = getattr(route, "path", "")
        if route_path.startswith("/api/") and route_path not in skipped:
            app.router.routes.append(route)


def _graph_db_path() -> str | None:
    explicit = os.getenv("GRAPH_DB_PATH") or os.getenv("KNOWLEDGE_GRAPH_DB_PATH")
    if explicit:
        return explicit
    data_dir = os.getenv("APP_DATA_DIR")
    if data_dir:
        return str(Path(data_dir) / "knowledge_graph.db")
    return str(_default_db_path())


def _graph() -> GraphService:
    return GraphService(db_path=_graph_db_path())


def _env_flag(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _is_azure_app_service() -> bool:
    return bool(
        os.getenv("WEBSITE_SITE_NAME")
        or os.getenv("WEBSITE_INSTANCE_ID")
        or os.getenv("APPSETTING_WEBSITE_SITE_NAME")
    )


def _run_startup_maintenance() -> bool:
    """Keep cloud cold starts fast enough for platform health probes."""
    default = not _is_azure_app_service()
    return _env_flag("APP_RUN_STARTUP_MAINTENANCE", default)


def _runtime_chapters_file() -> Path:
    return Path(os.getenv("APP_RUNTIME_DIR", str(ROOT_DIR / ".runtime"))) / "chapters.json"


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _exercise_count(chapter: dict[str, Any], key: str) -> int:
    value = chapter.get(key)
    return len(value) if isinstance(value, list) else 0


def _chapter_seed_score(chapter: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        _exercise_count(chapter, "approved_exercise_bank"),
        _exercise_count(chapter, "exercise_bank"),
        len(str(chapter.get("lecture_content") or "")),
        len(str(chapter.get("content") or "")),
    )


def _is_empty_seed_chapter(chapter: dict[str, Any]) -> bool:
    if not isinstance(chapter, dict):
        return False
    if chapter.get("graph_data") or chapter.get("ppt_slides") or chapter.get("slide_lectures"):
        return False
    if _exercise_count(chapter, "approved_exercise_bank") or _exercise_count(chapter, "exercise_bank"):
        return False
    if str(chapter.get("lecture_content") or "").strip():
        return False
    if str(chapter.get("content") or "").strip():
        return False
    return True


def _ensure_seed_chapters() -> None:
    if not _env_flag("APP_BOOTSTRAP_SEED_DATA", True) or not SEED_CHAPTERS_FILE.exists():
        return

    seed_payload = _read_json_object(SEED_CHAPTERS_FILE)
    seed_chapters = seed_payload.get("chapters")
    if not isinstance(seed_chapters, dict) or not seed_chapters:
        return

    target_path = _runtime_chapters_file()
    target_payload = _read_json_object(target_path)
    target_chapters = target_payload.get("chapters")
    if not isinstance(target_chapters, dict):
        target_chapters = {}

    changed = False
    empty_seed_ids = {
        chapter_id
        for chapter_id, seed_chapter in seed_chapters.items()
        if isinstance(seed_chapter, dict) and _is_empty_seed_chapter(seed_chapter)
    }
    for chapter_id in empty_seed_ids:
        if chapter_id in target_chapters and _is_empty_seed_chapter(target_chapters.get(chapter_id) or {}):
            target_chapters.pop(chapter_id, None)
            changed = True

    for chapter_id, seed_chapter in seed_chapters.items():
        if not isinstance(seed_chapter, dict):
            continue
        if chapter_id in empty_seed_ids:
            continue
        current_chapter = target_chapters.get(chapter_id)
        if not isinstance(current_chapter, dict):
            target_chapters[chapter_id] = seed_chapter
            changed = True

    if not changed:
        return

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps({"chapters": target_chapters}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[render] seed chapters installed: {len(seed_chapters) - len(empty_seed_ids)} chapter(s)")


def _vector_index_dir() -> Path:
    configured = os.getenv("KGTS_VECTOR_INDEX_DIR")
    if configured:
        return Path(configured)
    return Path(os.getenv("APP_RUNTIME_DIR", str(ROOT_DIR / ".runtime"))) / "vector_index"


def _ensure_seed_vector_index() -> None:
    if os.getenv("KGTS_RETRIEVAL_MODE", "hybrid").strip().lower() != "hybrid":
        return
    if not _env_flag("APP_BOOTSTRAP_SEED_DATA", True):
        return
    if not (SEED_VECTOR_INDEX_DIR / "metadata.json").exists():
        return

    target_dir = _vector_index_dir()
    target_metadata = target_dir / "metadata.json"
    if target_metadata.exists():
        return

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(SEED_VECTOR_INDEX_DIR, target_dir)
    print(f"[render] seed vector index installed: {target_dir}")


def _ensure_seed_runtime() -> None:
    _ensure_seed_chapters()
    ensure_seed_graph()
    _ensure_seed_vector_index()
    if not _run_startup_maintenance():
        print("[render] startup maintenance skipped")
        return
    try:
        result = delete_generated_lecture_nodes()
        if result.get("deleted_count"):
            print(f"[render] removed generated lecture graph nodes: {result.get('deleted_count')}")
    except Exception as exc:
        print(f"[render] lecture graph cleanup skipped: {exc}")
    try:
        graph = _graph().read_graph()
        removed_shells = 0
        for node in graph.get("nodes", []):
            if not _is_generated_shell_chapter_node(node):
                continue
            node_id = str(node.get("id") or "")
            if not node_id:
                continue
            _graph().delete_node(node_id)
            removed_shells += 1
        if removed_shells:
            print(f"[render] removed shell chapter graph nodes: {removed_shells}")
    except Exception as exc:
        print(f"[render] shell chapter cleanup skipped: {exc}")


def _ensure_structured_graph() -> None:
    if not _run_startup_maintenance():
        return
    if not _env_flag("RENDER_AUTO_SYNC_STRUCTURED", True):
        return

    graph = _graph()
    stats = graph.read_graph().get("stats", {})
    node_count = int(stats.get("node_count") or 0)
    min_nodes = max(int(os.getenv("RENDER_AUTO_SYNC_MIN_NODES", "400")), 400)
    if node_count >= min_nodes:
        return

    result = scan_structured_sources(force=True)
    summary = result.get("package_summary") or {}
    print(
        "[render] structured graph sync completed: "
        f"nodes={summary.get('node_count', 'unknown')}, "
        f"edges={summary.get('edge_count', 'unknown')}"
    )


def _ensure_vector_index() -> None:
    if not _env_flag("KGTS_VECTOR_STARTUP_ENSURE", False):
        return
    graph = _graph()
    if graph.retrieval_mode != "hybrid":
        return
    try:
        stats = graph._vector_stats()
        if not int(stats.get("index_size") or 0) or stats.get("stale"):
            stats = graph.rebuild_vector_index()
        print(
            "[render] GraphRAG vector index ready: "
            f"mode={stats.get('mode')}, model={stats.get('model')}, size={stats.get('index_size')}"
        )
    except Exception as exc:
        print(f"[render] GraphRAG vector index startup check skipped: {exc}")


@app.on_event("startup")
async def startup_sync_structured_graph() -> None:
    _ensure_seed_runtime()
    _ensure_structured_graph()
    _ensure_vector_index()
    try:
        run_tts_startup_cleanup()
    except Exception as exc:
        print(f"[render] TTS cache cleanup skipped: {exc}")


def _graph_nodes(limit: int = 5000) -> list[dict[str, Any]]:
    nodes = _graph().read_graph().get("nodes", [])[:limit]
    results: list[dict[str, Any]] = []
    for node in nodes:
        metadata = node.get("metadata") or {}
        results.append(
            {
                "id": node.get("id"),
                "label": metadata.get("label") or node.get("label") or node.get("id"),
                "type": node.get("type"),
                "content": node.get("content"),
                "source": metadata.get("source"),
                "confidence": metadata.get("confidence", 1.0),
                "created_at": node.get("created_at"),
                "updated_at": node.get("updated_at"),
                "reviewed": bool(metadata.get("reviewed")),
                "metadata": metadata,
            }
        )
    return results


def _graph_relationships(limit: int = 10000) -> list[dict[str, Any]]:
    relations = _graph().read_graph().get("relations", [])[:limit]
    results: list[dict[str, Any]] = []
    for relation in relations:
        metadata = relation.get("metadata") or {}
        source_id = relation.get("source_id") or relation.get("source_node")
        target_id = relation.get("target_id") or relation.get("target_node")
        relation_type = relation.get("relation_type") or relation.get("type") or "related"
        results.append(
            {
                "id": relation.get("id"),
                "source_node": source_id,
                "target_node": target_id,
                "source_id": source_id,
                "target_id": target_id,
                "source": source_id,
                "target": target_id,
                "type": relation_type,
                "relation_type": relation_type,
                "strength": relation.get("similarity", 1.0),
                "description": metadata.get("description", ""),
                "source_file": metadata.get("source"),
                "created_at": relation.get("created_at"),
                "updated_at": relation.get("updated_at"),
                "reviewed": bool(metadata.get("reviewed")),
                "metadata": metadata,
            }
        )
    return results


@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    return {
        "status": "healthy",
        "service": "render-single-fastapi",
        "graph_db_path": _graph_db_path() or "default",
        "retrieval_mode": os.getenv("KGTS_RETRIEVAL_MODE", "hybrid"),
        "context_cache": context_cache.status(),
    }


@app.get("/api/local-assets/status")
async def local_assets_status() -> dict[str, Any]:
    graph = _graph()
    vector_stats = graph._vector_stats()
    tts_status = get_tts_status()
    outside_paths: list[dict[str, Any]] = []
    for source, payload in (("vector", vector_stats), ("tts", tts_status)):
        for item in payload.get("outside_project_paths") or []:
            if isinstance(item, dict):
                outside_paths.append({"source": source, **item})
    return {
        "success": True,
        "path_policy": "project_local" if project_local_only() else "external_paths_allowed",
        "project_local_only": project_local_only(),
        "outside_project_paths": outside_paths,
        "asset_status": {
            "vector": vector_stats,
            "tts": tts_status,
        },
    }


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


def _frontend_file_response(path: Path, media_type: str | None = None) -> FileResponse:
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
async def frontend_home() -> FileResponse:
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend home page is missing.")
    return _frontend_file_response(index_path, "text/html; charset=utf-8")


@app.get("/env-config.js", include_in_schema=False)
async def frontend_runtime_config() -> Response:
    overrides = {
        "education": os.getenv("EDUCATION_API_BASE_URL", ""),
        "maintenance": os.getenv("MAINTENANCE_API_BASE_URL", ""),
        "admin": os.getenv("BACKEND_ADMIN_BASE_URL", ""),
    }
    body = f"""
(function () {{
  var origin = window.location.origin;
  window.__APP_CONFIG__ = Object.freeze({{
    educationApiBaseUrl: {json.dumps(overrides["education"])} || origin,
    maintenanceApiBaseUrl: {json.dumps(overrides["maintenance"])} || origin,
    backendAdminBaseUrl: {json.dumps(overrides["admin"])} || origin
  }});
}})();
""".strip()
    return Response(
        content=body,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
@app.get("/backend/vector_index_system/knowledge_graph/backend_admin.html", include_in_schema=False)
async def graph_admin_page() -> FileResponse:
    if not GRAPH_ADMIN_HTML.exists():
        raise HTTPException(status_code=404, detail="Graph admin page is missing.")
    return _frontend_file_response(GRAPH_ADMIN_HTML, "text/html; charset=utf-8")


@app.get("/backend/vector_index_system/knowledge_graph/", include_in_schema=False)
async def legacy_graph_admin_dir() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=307)


@app.get("/viz", include_in_schema=False)
async def legacy_graph_visualization_page() -> FileResponse:
    graph_viz_html = VECTOR_DIR / "knowledge_graph" / "graph_viz.html"
    if not graph_viz_html.exists():
        raise HTTPException(status_code=404, detail="Graph visualization page is missing.")
    return _frontend_file_response(graph_viz_html, "text/html; charset=utf-8")


@app.get("/api/graph")
async def graph_data() -> dict[str, Any]:
    try:
        result = delete_generated_lecture_nodes()
        if result.get("deleted_count"):
            print(f"[render] removed generated lecture graph nodes: {result.get('deleted_count')}")
    except Exception as exc:
        print(f"[render] lecture graph cleanup skipped: {exc}")
    from core.bridge import build_frontend_graph

    return {"success": True, "data": build_frontend_graph(_graph().read_graph())}


@app.get("/api/graph/visualization")
async def graph_visualization_data(
    node_limit: int = 1500,
    relationship_limit: int = 5000,
) -> dict[str, Any]:
    from KGTS.maintenance.graph_ops import get_visualization_graph

    return {
        "success": True,
        "data": await get_visualization_graph(
            node_limit=node_limit,
            relationship_limit=relationship_limit,
        ),
    }


@app.get("/api/relation-audit")
async def relation_audit() -> dict[str, Any]:
    graph = _graph().read_graph()
    nodes = graph.get("nodes", [])
    relations = graph.get("relations", [])
    structural_types = {"contains", "precedes", "references_formula", "references_table"}
    type_counts = Counter(relation.get("relation_type") or "related" for relation in relations)
    degree: Counter[str] = Counter()
    semantic_candidate_count = 0

    for relation in relations:
        source_id = relation.get("source_id")
        target_id = relation.get("target_id")
        if source_id:
            degree[source_id] += 1
        if target_id:
            degree[target_id] += 1
        metadata = relation.get("metadata") or {}
        if metadata.get("relation_source") == "semantic_candidate":
            semantic_candidate_count += 1

    isolated = [
        {
            "id": node.get("id"),
            "label": (node.get("metadata") or {}).get("label") or node.get("label") or node.get("id"),
            "type": node.get("type"),
        }
        for node in nodes
        if degree[node.get("id")] == 0
    ]
    structural_count = sum(count for rel_type, count in type_counts.items() if rel_type in structural_types)
    semantic_count = len(relations) - structural_count
    node_count = len(nodes)
    relation_count = len(relations)
    present_types = set(type_counts)

    return {
        "success": True,
        "audit": {
            "node_count": node_count,
            "relation_count": relation_count,
            "type_counts": dict(sorted(type_counts.items())),
            "structural_count": structural_count,
            "semantic_count": semantic_count,
            "semantic_candidate_count": semantic_candidate_count,
            "avg_degree": round((relation_count * 2) / node_count, 2) if node_count else 0,
            "isolated_count": len(isolated),
            "isolated_nodes": isolated[:40],
            "missing_preset_types": sorted(PRESET_RELATION_TYPES - present_types),
            "coverage_note": "Structural/reference relations are deterministic. Semantic relations are generated candidates and are not guaranteed exhaustive.",
        },
    }


@app.get("/api/node")
async def get_graph_node(node_id: str = Query("", alias="node_id"), id: str = Query("", alias="id")) -> dict[str, Any]:
    target_id = node_id or id
    if not target_id:
        return {"success": False, "error": "node_id is required"}
    node = _graph().get_node(target_id)
    return {"success": bool(node), "node": node or {}}


@app.get("/api/relations")
async def get_graph_relations(node_id: str = "") -> dict[str, Any]:
    relations = _graph().get_relations(node_id=node_id or None)
    return {"success": True, "relations": relations, "count": len(relations)}


@app.get("/api/nodes")
async def get_legacy_nodes() -> dict[str, Any]:
    nodes = _graph_nodes()
    return {"success": True, "nodes": nodes, "count": len(nodes)}


@app.get("/api/relationships")
async def get_legacy_relationships() -> dict[str, Any]:
    relationships = _graph_relationships()
    return {"success": True, "relationships": relationships, "count": len(relationships)}


@app.get("/api/stats")
async def get_legacy_stats() -> dict[str, Any]:
    stats = _graph().get_graph_statistics()
    return {
        "success": True,
        "data": {
            "total_nodes": stats.get("nodes", {}).get("total", 0),
            "total_relationships": stats.get("relations", {}).get("total", 0),
            "details": stats,
        },
    }


@app.get("/api/search/{search_term}")
async def search_legacy_nodes(search_term: str) -> dict[str, Any]:
    nodes = _graph().search_nodes(search_term, limit=5000)
    return {"success": True, "nodes": nodes, "count": len(nodes), "search_term": search_term}


@app.get("/api/filter/{filter_type}")
async def filter_legacy_nodes(filter_type: str) -> dict[str, Any]:
    nodes = _graph().search_nodes("", node_type=filter_type, limit=5000)
    return {"success": True, "nodes": nodes, "count": len(nodes), "filter_type": filter_type}


@app.post("/api/update-node")
async def update_graph_node(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("node_id"):
        return {"success": False, "error": "node_id is required"}
    return _graph().update_node(
        payload["node_id"],
        payload.get("content"),
        payload.get("metadata"),
    )


@app.post("/api/update-relation")
async def update_graph_relation(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("relation_id"):
        return {"success": False, "error": "relation_id is required"}
    return _graph().update_relation(
        payload["relation_id"],
        payload.get("source_id"),
        payload.get("target_id"),
        payload.get("relation_type"),
        payload.get("metadata"),
        payload.get("similarity"),
    )


_append_api_routes(education_api.app, skip_paths={"/api/health"})
_append_api_routes(maintenance_api.app, skip_paths={"/api/health"})
app.include_router(beamer_generator_router)

if FRONTEND_DIR.exists():
    beamer_static_dir = ROOT_DIR / "education" / "beamer_generator_full" / "static"
    beamer_upload_dir = ROOT_DIR / "education" / "beamer_generator_full" / "uploads"
    if beamer_static_dir.exists():
        app.mount(
            "/beamer-generator/static",
            StaticFiles(directory=beamer_static_dir),
            name="beamer-generator-static",
        )
    if beamer_upload_dir.exists():
        app.mount(
            "/beamer-generator/uploads",
            StaticFiles(directory=beamer_upload_dir),
            name="beamer-generator-uploads",
        )

    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend_spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        target_path = (FRONTEND_DIR / full_path).resolve()
        try:
            target_path.relative_to(FRONTEND_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not Found")
        if target_path.is_file():
            return _frontend_file_response(target_path)

        index_path = FRONTEND_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="Frontend home page is missing.")
        return _frontend_file_response(index_path, "text/html; charset=utf-8")
else:
    @app.get("/", include_in_schema=False)
    async def missing_frontend() -> JSONResponse:
        return JSONResponse({"success": False, "error": "frontend directory is missing"}, status_code=404)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=get_bind_host(),
        port=int(os.getenv("PORT", os.getenv("APP_PORT", "8000"))),
        reload=False,
    )
