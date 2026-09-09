from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import posixpath
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from KGTS.education.courseware_transport import pack_courseware

from KGTS.models.education import (
    CourseCreateRequest,
    CourseUpdateRequest,
    CoursewareExportPptxRequest,
    CoursewareProjectSaveRequest,
    GenerateLectureRequest,
    GeneratePptTexRequest,
    GenerateSlideLecturesRequest,
    PlanSlideSpeechRequest,
    PreviewTexRequest,
    AskQuestionRequest,
    LearningPlanRequest,
    NaturalSupplementRequest,
    SaveChapterRequest,
    SaveLectureRequest,
)
from KGTS.models.graph import AddNodeRequest, UpdateNodeRequest
from KGTS.core.mcp_client import call_mcp_tool
from KGTS.core.bridge import (
    RUNTIME_DIR,
    build_frontend_graph,
    build_rag_context,
    chapter_store,
    delete_generated_lecture_nodes,
    get_graph_schema,
    import_graph_payload,
    import_graph_db_payload,
    import_graphml_payload,
    search_nodes as backend_search_nodes,
)
from KGTS.core.tts_text import apply_speech_cues_for_tts, normalize_speech_cues
from KGTS.education.tts_router import clear_course_tts_cache
from KGTS.core.graph_context import build_graphrag_context, build_node_contexts
from KGTS.education.claude_api import DeepSeekAPIClient, get_deepseek_model, _strip_json_fence
from KGTS.education.kg_constraints import (
    KG_CONSTRAINED_SYSTEM_PROMPT,
    build_lecture_gc_dpg_requirements,
    build_learning_plan,
    build_constrained_generation_prompt,
    clean_generated_lecture_output,
    evidence_from_rag,
    evidence_from_graph,
    format_evidence,
    format_formula_context,
    format_graph_paths,
    expand_formula_references,
    formula_context_for_text,
    graph_paths_for_evidence,
)
from KGTS.config import load_root_env
from KGTS.education.exercise_helpers import _normalize_exercise_bank
from KGTS.education.qa_helpers import (
    _build_plan_from_graph,
    _build_question_fallback_response,
    _safe_consistency_report,
    answer_with_retrieval,
)
from KGTS.education.ppt_tex_generator import (
    ARTIFACT_DIR,
    build_pptx_artifact,
    build_tex_from_slides,
    normalize_generated_slides,
)
from KGTS.education.ppt_parser import SUPPORTED_COURSEWARE_EXTENSIONS, SUPPORTED_COURSEWARE_FORMATS_TEXT
from KGTS.education.courseware_editor import (
    assets_from_upload,
    build_editable_model,
    build_editable_model_from_slide_details,
    build_pptx_artifact_from_editable_model,
    list_courseware_projects,
    load_courseware_project,
    save_courseware_project,
    delete_courseware_project,
    serialize_editable_model_to_tex,
)
from KGTS.education.courseware_style import build_style_reference_guidance, build_style_reference_profile
from KGTS.education.teacher_profile import merge_teacher_guidance
from KGTS.education.course_store import course_store
from KGTS.education.beamer_full_router import _compile_latex_file_to_pdf_bytes, _compile_latex_to_pdf_bytes, _render_pdf_bytes_to_pages

load_root_env()

router = APIRouter(prefix="/api", tags=["education"])
logger = logging.getLogger(__name__)
RENDERED_PAGE_DIR = RUNTIME_DIR / "courseware" / "rendered-pages"
COURSEWARE_RENDER_QUEUE_DIR = RUNTIME_DIR / "courseware" / "render-queue"
COURSEWARE_RENDER_JOBS: Dict[str, Dict[str, Any]] = {}
COURSEWARE_RENDER_JOBS_LOCK = threading.Lock()
COURSEWARE_RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="courseware-render")


DEFAULT_SLIDE_LECTURE_DURATION_MINUTES = 10.0
DEFAULT_SLIDE_LECTURE_SPEECH_RATE_CPM = 250
DEFAULT_SLIDE_LECTURE_MAX_OUTPUT_TOKENS = 12000
SLIDE_LECTURE_JOBS: Dict[str, Dict[str, Any]] = {}
SLIDE_LECTURE_JOB_TTL_SECONDS = 60 * 60 * 4


def _courseware_image_warning(parse_result: Dict[str, Any]) -> str:
    missing_refs = [str(ref).strip() for ref in (parse_result.get("missing_image_refs") or []) if str(ref).strip()]
    if not missing_refs:
        return ""
    shown = "、".join(missing_refs[:6])
    suffix = f" 等 {len(missing_refs)} 个" if len(missing_refs) > 6 else ""
    tex_source = str(parse_result.get("tex_source_file") or "").strip()
    if tex_source:
        return f"检测到未匹配的图片引用：{shown}{suffix}。请确认 ZIP 中包含与 {tex_source} 相对路径一致的图片文件。"
    return f"检测到未匹配的图片引用：{shown}{suffix}。单独上传 .tex 不包含 fig 等相对路径图片；请上传包含 .tex 和图片目录的 ZIP，或用“图片包”补充资源。"


def _resolve_teacher_guidance(
    teacher_guidance: str = "",
    *,
    course_id: Optional[str] = None,
    title: str = "",
    profile_id: Optional[str] = None,
) -> tuple[str, Optional[Dict[str, Any]]]:
    course = course_store.get(str(course_id)) if course_id else None
    profile_title = "\n".join(part for part in (str((course or {}).get("title") or ""), title) if part)
    return merge_teacher_guidance(
        teacher_guidance,
        course_id=str(course_id or ""),
        title=profile_title,
        profile_id=str(profile_id or ""),
    )


@router.post("/education/courses")
async def create_course(request: CourseCreateRequest):
    try:
        course = course_store.create(title=request.title, description=request.description, course_id=request.course_id)
        return {"success": True, "course": course}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Course creation failed: {exc}") from exc


@router.get("/education/courses")
async def list_courses():
    try:
        courses = course_store.list()
        for course in courses:
            chapters = chapter_store.list_chapters(course.get("id"))
            course["chapter_count"] = len(chapters)
            course["courseware_count"] = sum(1 for chapter in chapters if str(chapter.get("source_type") or "").lower() == "courseware") or len(list_courseware_projects(course.get("id")))
        return {"success": True, "courses": courses}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Course list failed: {exc}") from exc


@router.get("/education/courses/{course_id}")
async def get_course(course_id: str):
    course = course_store.get(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    chapters = chapter_store.list_chapters(course_id)
    course["chapter_count"] = len(chapters)
    course["courseware_count"] = sum(1 for chapter in chapters if str(chapter.get("source_type") or "").lower() == "courseware") or len(list_courseware_projects(course_id))
    return {"success": True, "course": course}


@router.patch("/education/courses/{course_id}")
async def update_course(course_id: str, request: CourseUpdateRequest):
    try:
        course = course_store.update(course_id, title=request.title, description=request.description)
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        return {"success": True, "course": course}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Course update failed: {exc}") from exc


@router.delete("/education/courses/{course_id}")
async def delete_course(course_id: str):
    if not course_store.delete(course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    return {"success": True, "course_id": course_id}

@router.get("/")
async def root():
    return {
        "message": "知识图谱教育系统API",
        "version": "1.0.0",
        "endpoints": {
            "teacher": {
                "login": "/api/teacher/login",
                "generate_lecture": "/api/education/generate-lecture",
                "ask_question": "/api/education/ask-question",
                "natural_supplement": "/api/education/natural-supplement",
                "learning_plan": "/api/education/learning-plan",
                "get_graph": "/api/education/graph",
                "add_node": "/api/education/add-node",
                "update_node": "/api/education/update-node",
                "search_nodes": "/api/education/search-nodes",
            },
            "student": {
                "login": "/api/student/login",
                "chapter": "/api/student/chapter",
                "mark_chapter": "/api/student/mark-chapter",
                "exercises": "/api/student/exercises",
                "check_answer": "/api/student/check-answer",
                "question": "/api/student/question",
                "review": "/api/student/review",
            },
        },
    }


@router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@router.get("/config-status")
async def config_status():
    load_root_env()
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    return {
        "success": True,
        "deepseek_api_key_configured": bool(api_key),
        "deepseek_api_key_fingerprint": _deepseek_key_fingerprint(api_key),
        "deepseek_api_key_length": len(api_key.strip()),
        "flash_model": get_deepseek_model("flash"),
        "pro_model": get_deepseek_model("pro"),
        "deepseek_api_base": os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
    }


def _deepseek_key_fingerprint(api_key: str) -> str:
    value = str(api_key or "").strip()
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


@router.post("/save-config")
async def save_config(payload: dict[str, Any]):
    editable_keys = {
        "deepseek_api_key": "DEEPSEEK_API_KEY",
        "deepseek_api_base": "DEEPSEEK_API_BASE",
        "deepseek_flash_model": "DEEPSEEK_FLASH_MODEL",
        "deepseek_pro_model": "DEEPSEEK_PRO_MODEL",
    }
    env_path = Path(__file__).resolve().parents[1] / ".env"
    current: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()

    for payload_key, env_key in editable_keys.items():
        value = str(payload.get(payload_key) or "").strip()
        if not value and env_key == "DEEPSEEK_API_KEY":
            continue
        if value:
            current[env_key] = value
            os.environ[env_key] = value

    for env_key in editable_keys.values():
        if current.get(env_key):
            os.environ[env_key] = current[env_key]

    ordered_keys = sorted(current)
    env_path.write_text(
        "\n".join(f"{key}={current[key]}" for key in ordered_keys) + "\n",
        encoding="utf-8",
    )
    load_root_env(override=True)
    return await config_status()


@router.post("/test-deepseek-config")
async def test_deepseek_config():
    load_root_env()
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    model = get_deepseek_model("flash")
    base_url = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
    if not api_key:
        return {
            "success": False,
            "error": "deepseek_api_key_missing",
            "message": "未配置 DeepSeek API Key，请先在设置中保存 API Key。",
            "deepseek_api_key_configured": False,
            "deepseek_api_key_fingerprint": "",
            "deepseek_api_key_length": 0,
            "flash_model": model,
            "deepseek_api_base": base_url,
        }

    client = DeepSeekAPIClient(model=model)
    try:
        response = await client._call_deepseek(
            "请只回复 OK。",
            max_tokens=64,
            system_prompt="You are a connectivity test endpoint. Reply only OK.",
            read_timeout_seconds=20.0,
        )
    except Exception as exc:
        return {
            "success": False,
            "error": "deepseek_config_test_failed",
            "message": str(exc).strip() or exc.__class__.__name__,
            "deepseek_api_key_configured": True,
            "deepseek_api_key_fingerprint": _deepseek_key_fingerprint(api_key),
            "deepseek_api_key_length": len(api_key.strip()),
            "flash_model": model,
            "deepseek_api_base": base_url,
        }

    return {
        "success": True,
        "message": "DeepSeek 连接测试通过。",
        "response_preview": str(response or "").strip()[:40],
        "deepseek_api_key_configured": True,
        "deepseek_api_key_fingerprint": _deepseek_key_fingerprint(api_key),
        "deepseek_api_key_length": len(api_key.strip()),
        "flash_model": model,
        "deepseek_api_base": base_url,
    }


@router.post("/education/generate-lecture")
async def generate_lecture(request: GenerateLectureRequest):
    graph_data = None
    try:
        raw_chapter_title = (request.chapter_title or "").strip()
        chapter_content = expand_formula_references(request.chapter_content or "")
        selected_context = None
        graphrag_context = None
        source_node_ids = _normalize_source_node_ids(request.source_node_id, request.source_node_ids)
        source_node_id = source_node_ids[0] if source_node_ids else None
        if source_node_ids:
            selected_context = build_node_contexts(source_node_ids)
            if not selected_context.get("success"):
                raise HTTPException(status_code=404, detail=selected_context.get("error") or "Graph node not found")
            graph_content = str(selected_context.get("chapter_content") or "").strip()
            supplemental_content = chapter_content.strip()
            chapter_content = graph_content
            if supplemental_content and supplemental_content not in graph_content:
                chapter_content = f"{graph_content}\n\nTeacher supplemental notes:\n{supplemental_content}"
            if not raw_chapter_title:
                raw_chapter_title = str(selected_context.get("chapter_title") or "").strip()
            graph_data = selected_context.get("graph_data")
        try:
            if graph_data is None:
                graph_data = await call_mcp_tool("read_graph")
            if isinstance(graph_data, dict):
                graph_data = build_frontend_graph(graph_data)
        except Exception:
            try:
                if graph_data is None:
                    graph_data = build_frontend_graph()
            except Exception:
                graph_data = None

        claude_client = DeepSeekAPIClient(
            api_key=request.api_key,
            model=request.model or get_deepseek_model("pro"),
        )
        generated_title = raw_chapter_title or await _generate_chapter_title(
            claude_client,
            graph_data if isinstance(graph_data, dict) else {"nodes": [], "relations": []},
            chapter_content,
        )
        chapter_title = generated_title or "未命名章节"
        graphrag_query = f"{chapter_title}\n{chapter_content[:1200]}"
        try:
            graphrag_context = build_graphrag_context(
                graphrag_query,
                seed_node_ids=source_node_ids or None,
                limit=8,
            )
            if source_node_ids:
                selected_context = graphrag_context.get("selected_context") or selected_context
            graph_data = graphrag_context.get("graph_data") or graph_data
            if graphrag_context.get("context"):
                chapter_content = str(graphrag_context.get("context") or chapter_content)
        except Exception:
            graphrag_context = None
        existing_chapter = chapter_store.get_chapter(request.chapter_id) or {}
        effective_course_id = request.course_id or existing_chapter.get("course_id")
        effective_teacher_guidance, teacher_profile = _resolve_teacher_guidance(
            request.teacher_guidance,
            course_id=effective_course_id,
            title=chapter_title,
            profile_id=request.teacher_profile_id,
        )
        chapter_data = {
            "id": request.chapter_id,
            "title": chapter_title,
            "content": chapter_content,
            "course_id": effective_course_id,
            "teacher_guidance": effective_teacher_guidance,
            "source_node_ids": source_node_ids,
            "graphrag_context": graphrag_context,
            "graph_context": _format_graphrag_generation_context(graphrag_context) if graphrag_context else "",
        }
        if graphrag_context and graphrag_context.get("llm_context"):
            learning_plan = build_learning_plan(
                query=chapter_title,
                evidence=evidence_from_rag(graphrag_context.get("llm_context") or [], limit=8),
                relations=[
                    {
                        "source": item.get("source"),
                        "target": item.get("target"),
                        "type": item.get("type") or "related",
                        "metadata": {"description": item.get("description", "")},
                    }
                    for item in (graphrag_context.get("graph_paths") or [])
                ],
                learner_intent="explain",
                learning_level="beginner",
                task="lecture",
                chapter_data=chapter_data,
            )
        elif selected_context and selected_context.get("evidence"):
            learning_plan = build_learning_plan(
                query=chapter_title,
                evidence=selected_context.get("evidence") or [],
                learner_intent="explain",
                learning_level="beginner",
                task="lecture",
                chapter_data=chapter_data,
            )
        else:
            learning_plan = _build_plan_from_graph(
                query=chapter_title,
                graph_data=graph_data if isinstance(graph_data, dict) else None,
                task="lecture",
                chapter_data=chapter_data,
            )
        if not learning_plan.get("evidence"):
            rag = build_rag_context(f"{chapter_title}\n{chapter_content[:800]}", limit=6)
            learning_plan = build_learning_plan(
                query=chapter_title,
                evidence=evidence_from_rag(rag.get("llm_context") or [], limit=6),
                learner_intent="explain",
                learning_level="beginner",
                task="lecture",
                chapter_data=chapter_data,
            )

        lecture_content = await claude_client.generate_lecture(
            graph_data if isinstance(graph_data, dict) else {"nodes": [], "relations": []},
            chapter_data,
            request.style,
        )
        lecture_content = clean_generated_lecture_output(lecture_content)
        consistency_report = _safe_consistency_report(lecture_content, learning_plan, task="lecture")
        chapter_store.save_chapter(
            title=chapter_title,
            content=chapter_content,
            chapter_id=request.chapter_id,
            course_id=effective_course_id,
            source_type="graph_subtree" if selected_context else None,
            source_node_ids=source_node_ids or None,
            source_scope=(selected_context or {}).get("scope"),
            sync_backend=False,
        )
        saved_chapter = chapter_store.save_lecture(
            chapter_id=request.chapter_id,
            course_id=effective_course_id,
            lecture_content=lecture_content,
            learning_plan=learning_plan,
            consistency_report=consistency_report,
            source_type="graph_subtree" if selected_context else None,
            source_node_ids=source_node_ids or None,
            source_scope=(selected_context or {}).get("scope"),
        )

        return {
            "success": True,
            "content": lecture_content,
            "lecture_content": lecture_content,
            "chapter_id": saved_chapter.get("id") or request.chapter_id,
            "chapter_title": chapter_title,
            "style": request.style,
            "model": claude_client.model,
            "learning_plan": learning_plan,
            "consistency_report": consistency_report,
            "source_node_id": source_node_id,
            "source_node_ids": source_node_ids,
            "source_scope": (selected_context or {}).get("scope"),
            "retrieval_mode": (graphrag_context or {}).get("retrieval_mode"),
            "retrieval_stats": (graphrag_context or {}).get("retrieval_stats"),
            "graphrag_context": graphrag_context,
            "vector_hits": (graphrag_context or {}).get("vector_hits"),
            "graph_paths": (graphrag_context or {}).get("graph_paths"),
            "formula_context": (graphrag_context or {}).get("formula_context"),
            "teacher_profile_id": (teacher_profile or {}).get("profile_id") if teacher_profile else None,
            "generated_at": datetime.now().isoformat(),
        }
    except ValueError as e:
        if "API" in str(e).upper():
            return {
                "success": False,
                "error": "DeepSeek API is not configured",
                "message": "Please configure a DeepSeek API key in settings or DEEPSEEK_API_KEY.",
                "fallback": "Local generation is available.",
            }
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generate lecture failed: {str(e)}")


async def _generate_chapter_title(client: DeepSeekAPIClient, graph_data: Dict[str, Any], chapter_content: str) -> str:
    evidence = evidence_from_graph(graph_data, query=chapter_content[:800] or "课程章节", limit=8)
    evidence_text = format_evidence(evidence)
    prompt = f"""请基于以下课程材料生成一个简洁、准确的章节标题。

要求：
1. 只输出标题本身，不要解释。
2. 优先保留原文中的章节名、英文术语和编号。
3. 标题长度不超过 40 个汉字或 80 个英文字符。
4. 不要使用“未命名章节”“授课文案”“生成文案”等泛化标题。

章节正文：
{chapter_content[:1200] or "（未提供章节正文，请根据图谱证据判断）"}

图谱证据：
{evidence_text}
"""
    title = await client._call_deepseek(
        prompt,
        max_tokens=120,
        system_prompt="You generate concise course chapter titles. Return only the title.",
    )
    return str(title or "").strip().strip("\"'“”")


async def _auto_select_source_node_ids(query: str, api_key: Optional[str] = None) -> List[str]:
    """Use the vision-capable flash model to pick relevant graph nodes from content."""
    candidates = backend_search_nodes(str(query or "")[:1600], limit=16)
    if not candidates:
        return []
    compact = [
        {"id": str(item.get("id") or item.get("node_id") or ""), "label": item.get("label"), "content": str(item.get("content") or "")[:260]}
        for item in candidates
    ]
    compact = [item for item in compact if item["id"]]
    try:
        client = DeepSeekAPIClient(api_key=api_key, model="deepseek-v4-flash-vision-exp")
        raw = await client._call_deepseek(
            "根据课程标题、正文和候选知识图谱节点，选择最相关的节点。只输出 JSON 数组，元素为候选节点 id，最多 6 个。\n"
            f"课程内容：\n{str(query or '')[:1800]}\n候选节点：\n{json.dumps(compact, ensure_ascii=False)}",
            max_tokens=300,
            system_prompt="You select relevant knowledge graph node IDs. Return JSON array only.",
            read_timeout_seconds=30.0,
        )
        selected = json.loads(_strip_json_fence(raw))
        if isinstance(selected, list):
            allowed = {item["id"] for item in compact}
            return [str(item) for item in selected if str(item) in allowed][:6]
    except Exception:
        pass
    return [item["id"] for item in compact[:3]]


@router.post("/education/generate-ppt-tex")
async def generate_ppt_tex(request: GeneratePptTexRequest):
    source_node_ids = _normalize_source_node_ids(request.source_node_id, request.source_node_ids)
    auto_selected = False
    if not source_node_ids and not request.allow_no_node:
        source_node_ids = await _auto_select_source_node_ids(
            f"{request.chapter_title}\n{request.content}", request.api_key
        )
        auto_selected = bool(source_node_ids)
    if not source_node_ids and not request.allow_no_node and not request.content.strip() and not request.chapter_title.strip():
        raise HTTPException(status_code=400, detail="请选择图谱章节树节点，或提供标题/内容后自动选择节点")
    try:
        selected_context = build_node_contexts(source_node_ids) if source_node_ids else {"success": True, "chapter_content": request.content}
        if source_node_ids and not selected_context.get("success"):
            raise HTTPException(status_code=404, detail=selected_context.get("error") or "Graph node not found")
        chapter_title = str(request.chapter_title or selected_context.get("chapter_title") or "").strip() or "自动生成课件"
        base_content = str(request.content or selected_context.get("chapter_content") or "").strip()
        graphrag_context = build_graphrag_context(
            f"{chapter_title}\n{base_content[:1200]}",
            seed_node_ids=source_node_ids or None,
            limit=10,
        )
        selected_context = graphrag_context.get("selected_context") or selected_context
        graph_data = graphrag_context.get("graph_data") or selected_context.get("graph_data")
        context_content = _format_graphrag_generation_context(graphrag_context) or base_content
        source_evidence = evidence_from_rag(graphrag_context.get("llm_context") or [], limit=10)
        learning_plan = _build_ppt_learning_plan(
            chapter_title=chapter_title,
            chapter_content=context_content,
            graph_data=graph_data if isinstance(graph_data, dict) else None,
            selected_evidence=source_evidence,
        )
        graph_paths = graphrag_context.get("graph_paths") or graph_paths_for_evidence(
            graph_data if isinstance(graph_data, dict) else None,
            learning_plan.get("evidence") or [],
            limit=12,
        )
        formula_context = graphrag_context.get("formula_context") or formula_context_for_text(context_content, limit=12)
        raw_slides: Any = None
        warning = ""
        effective_teacher_guidance, teacher_profile = _resolve_teacher_guidance(
            request.teacher_guidance,
            course_id=request.course_id,
            title=chapter_title,
            profile_id=request.teacher_profile_id,
        )
        style_reference_guidance = build_style_reference_guidance(request.style_reference)
        try:
            client = DeepSeekAPIClient(
                api_key=request.api_key,
                model=request.model or get_deepseek_model("pro"),
            )
            prompt = _build_ppt_tex_prompt(
                chapter_title=chapter_title,
                context_content=context_content,
                learning_plan=learning_plan,
                graph_paths=graph_paths,
                formula_context=formula_context,
                style=request.style,
                teacher_guidance=effective_teacher_guidance,
                style_reference_guidance=style_reference_guidance,
                max_slides=request.max_slides,
            )
            raw_slides = await client._call_deepseek(
                prompt,
                max_tokens=3600,
                system_prompt=KG_CONSTRAINED_SYSTEM_PROMPT,
                read_timeout_seconds=90.0,
            )
            model_name = client.model
        except ValueError as exc:
            if "API" not in str(exc).upper():
                raise
            warning = "DeepSeek API未配置，已使用图谱内容生成基础 PPT/TeX。"
            model_name = get_deepseek_model("pro")
        except Exception as exc:
            warning = f"PPT/TeX大模型生成失败，已使用图谱内容生成基础 PPT/TeX：{exc}"
            model_name = get_deepseek_model("pro")

        slides = normalize_generated_slides(
            raw_slides,
            fallback_title=chapter_title,
            fallback_content=context_content,
            max_slides=max(1, min(int(request.max_slides or 12), 30)),
        )
        slides = _attach_slide_source_node_ids(slides, learning_plan.get("evidence") or source_evidence)
        tex_content = build_tex_from_slides(chapter_title, slides, style_reference=request.style_reference)
        editable_model = build_editable_model_from_slide_details(
            slides,
            title=chapter_title,
            source_tex=tex_content,
            tex_source_file="generated.tex",
        )
        artifact = build_pptx_artifact(chapter_title, slides, source_node_ids=source_node_ids, style_reference=request.style_reference)
        artifact["tex_content_hash"] = hashlib.md5(tex_content.encode("utf-8")).hexdigest()
        artifact.pop("tex_content", None)
        rendered_pages, render_error = _render_courseware_pdf_pages(tex_content, editable_model.get("assets") or {})
        return {
            "success": True,
            "chapter_title": chapter_title,
            "slide_count": len(slides),
            "slides": slides,
            "full_text": "\n\n---\n\n".join(str(slide.get("raw_text") or "") for slide in slides),
            "tex_content": tex_content,
            "editable_model": editable_model,
            "asset_map": editable_model.get("assets") or {},
            "layout": editable_model.get("layout") or {},
            "source_tex": tex_content,
            "rendered_pages": rendered_pages,
            **({"render_source": "latex"} if rendered_pages else {}),
            **({"render_error": render_error} if render_error else {}),
            "ppt_artifact": artifact,
            "learning_plan": learning_plan,
            "retrieval_mode": graphrag_context.get("retrieval_mode"),
            "retrieval_stats": graphrag_context.get("retrieval_stats"),
            "graphrag_context": graphrag_context,
            "vector_hits": graphrag_context.get("vector_hits"),
            "graph_paths": graph_paths,
            "formula_context": formula_context,
            "source_node_id": source_node_ids[0] if source_node_ids else None,
            "source_node_ids": source_node_ids,
            "auto_selected_nodes": auto_selected,
            "source_scope": selected_context.get("scope") if source_node_ids else None,
            "style": request.style,
            "style_reference": request.style_reference,
            "teacher_profile_id": (teacher_profile or {}).get("profile_id") if teacher_profile else None,
            "model": model_name,
            "generated_at": datetime.now().isoformat(),
            **({"warning": warning} if warning else {}),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PPT/TeX 生成失败: {str(e)}")


@router.post("/education/generate-slide-lectures")
async def generate_slide_lectures(request: GenerateSlideLecturesRequest):
    return await _generate_slide_lectures_sync(request)


@router.post("/education/generate-slide-lectures/jobs")
async def create_slide_lecture_job(request: GenerateSlideLecturesRequest):
    _prune_slide_lecture_jobs()
    job_id = f"slide_lecture_{uuid.uuid4().hex[:12]}"
    job = {
        "job_id": job_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "result": None,
        "error": "",
        "stage": "queued",
        "message": "逐页讲解任务已排队",
        "started_at": None,
    }
    SLIDE_LECTURE_JOBS[job_id] = job

    def update_stage(stage: str, message: str) -> None:
        job["stage"] = stage
        job["message"] = message
        job["updated_at"] = datetime.now().isoformat()
        logger.info("slide lecture job %s stage=%s message=%s", job_id, stage, message)

    async def run_job() -> None:
        job["status"] = "running"
        job["started_at"] = datetime.now().isoformat()
        job["updated_at"] = datetime.now().isoformat()
        update_stage("starting", "正在准备逐页讲解任务")
        try:
            result = await asyncio.to_thread(lambda: asyncio.run(_generate_slide_lectures_sync(request, progress=update_stage)))
            job["result"] = _compact_slide_lecture_response(result) if isinstance(result, dict) else result
            job["status"] = "completed"
            update_stage("completed", "逐页讲解生成完成")
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc).strip() or exc.__class__.__name__
            update_stage("failed", job["error"])
        finally:
            job["updated_at"] = datetime.now().isoformat()

    asyncio.create_task(run_job())
    return {
        "success": True,
        "job_id": job_id,
        "status": job["status"],
        "created_at": job["created_at"],
    }


@router.get("/education/generate-slide-lectures/jobs/{job_id}")
async def get_slide_lecture_job(job_id: str):
    _prune_slide_lecture_jobs()
    job = SLIDE_LECTURE_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="逐页讲解任务不存在或已过期")
    result = job.get("result")
    if isinstance(result, dict):
        result = _compact_slide_lecture_response(result)
    return {
        "success": True,
        "job_id": job_id,
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "result": result,
        "error": job.get("error") or "",
        "stage": job.get("stage") or "",
        "message": job.get("message") or "",
        "elapsed_seconds": _slide_lecture_job_elapsed_seconds(job),
    }


@router.post("/education/plan-slide-speech")
async def plan_slide_speech(request: PlanSlideSpeechRequest):
    lecture = str(request.lecture or "").strip()
    if not lecture:
        raise HTTPException(status_code=400, detail="缺少当前页讲稿，无法生成语音规划")
    speech_cues = await _plan_slide_speech_cues_with_model(request)
    spoken_text = apply_speech_cues_for_tts(lecture, speech_cues)
    return {
        "success": True,
        "speech_cues": speech_cues,
        "speech_cue_count": len(speech_cues),
        "estimated_chars": _count_speech_chars(spoken_text),
    }


def _prune_slide_lecture_jobs() -> None:
    now = datetime.now().timestamp()
    expired: List[str] = []
    for job_id, job in SLIDE_LECTURE_JOBS.items():
        try:
            updated = datetime.fromisoformat(str(job.get("updated_at") or job.get("created_at") or "")).timestamp()
        except ValueError:
            updated = now
        if now - updated > SLIDE_LECTURE_JOB_TTL_SECONDS:
            expired.append(job_id)
    for job_id in expired:
        SLIDE_LECTURE_JOBS.pop(job_id, None)


def _slide_lecture_job_elapsed_seconds(job: Dict[str, Any]) -> int:
    started_at = str(job.get("started_at") or job.get("created_at") or "")
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return 0
    return max(0, int((datetime.now() - started).total_seconds()))


async def _generate_slide_lectures_sync(
    request: GenerateSlideLecturesRequest,
    progress: Optional[Callable[[str, str], None]] = None,
):
    def mark(stage: str, message: str) -> None:
        if progress:
            progress(stage, message)

    source_node_ids = _normalize_source_node_ids(request.source_node_id, request.source_node_ids)
    if not source_node_ids:
        source_node_ids = _normalize_source_node_ids(None, request.ppt_source_node_ids)
    if not source_node_ids and not request.allow_no_node:
        base_query = "\n\n".join(str(slide.get("raw_text") or slide.get("content") or slide.get("title") or "") for slide in request.slides[:8])
        source_node_ids = await _auto_select_source_node_ids(f"{request.chapter_title}\n{base_query}", request.api_key)
    if not source_node_ids and not request.allow_no_node:
        raise HTTPException(status_code=400, detail="请选择图谱章节树节点后再生成逐页讲解")
    if not request.slides:
        raise HTTPException(status_code=400, detail="缺少已生成的 PPT/TeX 页面内容")
    try:
        selected_context = build_node_contexts(source_node_ids) if source_node_ids else {"success": True, "chapter_content": ""}
        if not selected_context.get("success"):
            raise HTTPException(status_code=404, detail=selected_context.get("error") or "Graph node not found")
        chapter_title = request.chapter_title or selected_context.get("chapter_title") or "图谱生成课件"
        mark("graphrag", "正在构建 GraphRAG 章节上下文")
        target_slide_indices = _normalize_target_slide_indices(request.target_slide_indices, request.slides)
        base_query_slides = [
            slide
            for slide in request.slides
            if not target_slide_indices or int(slide.get("index")) in set(target_slide_indices)
        ]
        base_query = "\n\n".join(str(slide.get("raw_text") or slide.get("content") or slide.get("title") or "") for slide in base_query_slides)[:1400]
        route_warnings: List[str] = []
        try:
            graphrag_context = build_graphrag_context(
                f"{chapter_title}\n{base_query}",
                seed_node_ids=source_node_ids,
                limit=6,
            )
        except Exception as e:
            graphrag_context = {"selected_context": selected_context, "graph_data": selected_context.get("graph_data")}
            route_warnings.append(f"图谱检索上下文构建失败，已仅使用所选课程树和当前页内容生成：{e}")
        selected_context = graphrag_context.get("selected_context") or selected_context
        graph_data = graphrag_context.get("graph_data") or selected_context.get("graph_data")
        graph_context_content = _format_graphrag_generation_context(graphrag_context)
        effective_teacher_guidance, teacher_profile = _resolve_teacher_guidance(
            request.teacher_guidance,
            course_id=request.course_id,
            title=chapter_title,
            profile_id=request.teacher_profile_id,
        )
        source_evidence = _compact_evidence_for_prompt(
            evidence_from_rag(graphrag_context.get("llm_context") or [], limit=4),
            limit=4,
            content_chars=260,
        )
        client = DeepSeekAPIClient(
            api_key=request.api_key,
            model=request.model or get_deepseek_model("pro"),
        )
        style_reference_guidance = build_style_reference_guidance(request.style_reference)
        mark("pacing", "正在计算逐页讲解字数分配")
        pacing = await _build_slide_lecture_pacing_with_model(
            client,
            request.slides,
            chapter_title=chapter_title,
            style=request.style,
            target_duration_minutes=request.target_duration_minutes,
            speech_rate_cpm=request.speech_rate_cpm,
            teacher_guidance=effective_teacher_guidance,
            style_reference_guidance=style_reference_guidance,
        )
        mark("generating", f"正在生成逐页讲解（{len(base_query_slides) or len(request.slides)} 页）")
        slide_lectures = await _generate_per_slide_lectures(
            client,
            request.slides,
            request.style,
            chapter_title,
            graph_data if isinstance(graph_data, dict) else None,
            selected_evidence=source_evidence,
            selected_graph_context=graph_context_content,
            source_node_ids=source_node_ids,
            teacher_guidance=effective_teacher_guidance,
            slide_feedback=request.slide_feedback or {},
            style_reference_guidance=style_reference_guidance,
            target_slide_indices=target_slide_indices,
            pacing_by_index=pacing["slides"],
            speech_rate_cpm=pacing["speech_rate_cpm"],
            progress=progress,
        )
        if _nonempty_slide_lecture_count(slide_lectures) == 0 and any(_compact_slide_for_lecture(slide).strip() for slide in request.slides):
            message = _slide_lecture_error_summary(slide_lectures) or "AI 未返回任何逐页讲解内容，请检查 DeepSeek 配置、模型返回或稍后重试。"
            return _compact_slide_lecture_response({
                "success": False,
                "error": "slide_lecture_generation_empty",
                "message": message,
                "chapter_title": request.chapter_title or selected_context.get("chapter_title") or "图谱生成课件",
                "slide_count": len(request.slides),
                "slides": request.slides,
                "full_text": "\n\n---\n\n".join(str(slide.get("raw_text") or "") for slide in request.slides),
                "tex_content": request.tex_content,
                "lecture_content": "",
                "slide_lectures": slide_lectures,
                "source_node_id": source_node_ids[0] if source_node_ids else None,
                "source_node_ids": source_node_ids,
                "source_scope": selected_context.get("scope"),
                "ppt_source_node_ids": request.ppt_source_node_ids or [],
                "lecture_source_node_ids": source_node_ids,
                "drift_report": _build_source_drift_report(request.ppt_source_node_ids or [], source_node_ids),
            })
        if target_slide_indices:
            slide_lectures = _merge_existing_slide_lectures(
                request.existing_slide_lectures,
                slide_lectures,
                request.slides,
            )
        merged_lecture = _merge_slide_lectures(slide_lectures)
        mark("summarizing", "正在整理讲解结果和教学计划")
        try:
            learning_plan = _build_ppt_learning_plan(
                chapter_title=chapter_title,
                chapter_content=_truncate_for_prompt("\n\n".join(str(slide.get("raw_text") or slide.get("content") or "") for slide in request.slides), 1800),
                graph_data=graph_data if isinstance(graph_data, dict) else None,
                selected_evidence=source_evidence,
            )
        except Exception as e:
            learning_plan = build_learning_plan(
                query=chapter_title,
                evidence=source_evidence,
                learner_intent="explain",
                learning_level="beginner",
                task="lecture",
                chapter_data={"title": chapter_title, "content": _truncate_for_prompt(merged_lecture, 1200)},
            )
            route_warnings.append(f"整体讲解计划构建失败，已使用逐页结果生成汇总：{e}")
        drift_report = _build_source_drift_report(request.ppt_source_node_ids or [], source_node_ids)
        warning = drift_report.get("warning") or "；".join(route_warnings) or None
        return _compact_slide_lecture_response({
            "success": True,
            "chapter_title": request.chapter_title or selected_context.get("chapter_title") or "图谱生成课件",
            "slide_count": len(request.slides),
            "slides": request.slides,
            "full_text": "\n\n---\n\n".join(str(slide.get("raw_text") or "") for slide in request.slides),
            "tex_content": request.tex_content,
            "lecture_content": merged_lecture,
            "slide_lectures": slide_lectures,
            "lecture_pacing": _summarize_slide_lecture_pacing(slide_lectures, pacing),
            "learning_plan": learning_plan,
            "consistency_report": _safe_consistency_report(merged_lecture, learning_plan, task="lecture"),
            "retrieval_mode": graphrag_context.get("retrieval_mode"),
            "retrieval_stats": graphrag_context.get("retrieval_stats"),
            "graphrag_context": graphrag_context,
            "vector_hits": graphrag_context.get("vector_hits"),
            "graph_paths": graphrag_context.get("graph_paths"),
            "formula_context": graphrag_context.get("formula_context"),
            "source_node_id": source_node_ids[0] if source_node_ids else None,
            "source_node_ids": source_node_ids,
            "source_scope": selected_context.get("scope"),
            "ppt_source_node_ids": request.ppt_source_node_ids or [],
            "lecture_source_node_ids": source_node_ids,
            "drift_report": drift_report,
            "warning": warning,
            "style": request.style,
            "style_reference": request.style_reference,
            "teacher_profile_id": (teacher_profile or {}).get("profile_id") if teacher_profile else None,
            "regenerated_slide_indices": target_slide_indices or [int(slide.get("index")) for slide in request.slides if isinstance(slide.get("index"), int)],
            "model": client.model,
            "generated_at": datetime.now().isoformat(),
        })
    except HTTPException:
        raise
    except ValueError as e:
        if "API" in str(e).upper():
            return {
                "success": False,
                "error": "DeepSeek API is not configured",
                "message": "Please configure a DeepSeek API key in settings or DEEPSEEK_API_KEY.",
            }
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"逐页讲解生成失败: {str(e)}")


@router.post("/education/courseware/style-reference")
async def upload_courseware_style_reference(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")
    lower_name = file.filename.lower()
    if not lower_name.endswith((".zip", ".tex")):
        raise HTTPException(status_code=400, detail="参考风格文件仅支持 .zip 或 .tex")
    try:
        file_bytes = await file.read()
        profile = build_style_reference_profile(file_bytes, file.filename)
        if not profile.get("success"):
            raise HTTPException(status_code=400, detail=profile.get("error") or "参考风格解析失败")
        return profile
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"参考风格解析失败: {str(e)}")


def _normalize_source_node_ids(source_node_id: Optional[str], source_node_ids: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for node_id in [source_node_id, *(source_node_ids or [])]:
        text = str(node_id or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _scope_graph_to_ids(graph_data: Optional[Dict[str, Any]], allowed_node_ids: List[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(graph_data, dict) or not allowed_node_ids:
        return graph_data
    allowed = {str(node_id or "").strip() for node_id in allowed_node_ids if str(node_id or "").strip()}
    if not allowed:
        return graph_data
    nodes = [
        node
        for node in graph_data.get("nodes", [])
        if isinstance(node, dict) and str(node.get("id") or "") in allowed
    ]
    relations = []
    for relation in graph_data.get("relations") or graph_data.get("edges") or []:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source_id") or relation.get("source") or relation.get("from") or "")
        target = str(relation.get("target_id") or relation.get("target") or relation.get("to") or "")
        if source in allowed and target in allowed:
            relations.append(relation)
    return {
        **graph_data,
        "nodes": nodes,
        "relations": relations,
        "edges": relations,
        "stats": {"node_count": len(nodes), "relation_count": len(relations)},
    }


def _build_ppt_tex_prompt(
    *,
    chapter_title: str,
    context_content: str,
    learning_plan: Dict[str, Any],
    graph_paths: List[Dict[str, Any]],
    formula_context: List[Dict[str, Any]],
    style: str,
    teacher_guidance: str,
    style_reference_guidance: str = "",
    max_slides: int,
) -> str:
    guidance = f"\nTeacher guidance:\n{teacher_guidance[:1600]}\n" if teacher_guidance else ""
    reference_guidance = f"\nReference courseware style guidance:\n{style_reference_guidance[:2000]}\n" if style_reference_guidance else ""
    return f"""Generate a classroom PPT/TeX slide plan from the selected graph subtree.

Return only valid JSON in this exact shape:
{{
  "slides": [
    {{
      "title": "short slide title",
      "bullets": ["3-5 concise bullet points"],
      "notes": "short teacher note for this slide"
    }}
  ]
}}

Rules:
1. Generate {max(1, min(int(max_slides or 12), 30))} slides or fewer.
2. Use the selected graph subtree as the evidence boundary.
3. Keep formulas in LaTeX when they appear in evidence.
4. Do not invent concepts outside the selected graph context.
5. Make slide titles specific enough to map back to the source nodes.
6. The teaching style is: {style}.
7. If reference style guidance is provided, transfer only visual/pacing conventions; never copy reference course content, people, dates, logos, or figures.
{guidance}
{reference_guidance}
Chapter title:
{chapter_title}

Selected graph subtree context:
{context_content[:7000]}

Matched graph evidence:
{format_evidence(learning_plan.get("evidence") or [])}

Graph relation paths:
{format_graph_paths(graph_paths)}

Formula derivation and scoped symbol context:
{format_formula_context(formula_context)}
"""


def _format_graphrag_generation_context(graphrag_context: Optional[Dict[str, Any]]) -> str:
    if not graphrag_context:
        return ""
    parts = []
    context = str(graphrag_context.get("context") or "").strip()
    if context:
        parts.append("GraphRAG vector hits and graph expansion:\n" + context)
    graph_paths = graphrag_context.get("graph_paths") or []
    if graph_paths:
        parts.append("Graph relation paths:\n" + format_graph_paths(graph_paths))
    formulas = graphrag_context.get("formula_context") or []
    if formulas:
        parts.append("Formula derivation and scoped symbol context:\n" + format_formula_context(formulas))
    return "\n\n".join(parts)


def _merge_slide_lectures(slide_lectures: List[Dict[str, Any]]) -> str:
    return "\n\n---\n\n".join(
        f"## 第 {item.get('index')} 页：{item.get('title') or ''}\n\n{str(item.get('lecture') or '').strip() or '_本页未生成文案_'}"
        for item in slide_lectures
    )


def _nonempty_slide_lecture_count(slide_lectures: List[Dict[str, Any]]) -> int:
    return sum(1 for item in slide_lectures if str((item or {}).get("lecture") or "").strip())


def _slide_lecture_error_summary(slide_lectures: List[Dict[str, Any]]) -> str:
    errors: List[str] = []
    for item in slide_lectures:
        if not isinstance(item, dict):
            continue
        error = str(item.get("error") or "").strip()
        if not error:
            continue
        errors.append(f"第 {item.get('index', '?')} 页：{error}")
        if len(errors) >= 3:
            break
    return "；".join(errors)


def _truncate_for_prompt(value: Any, max_chars: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", str(value or "").strip())
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 12].rstrip() + "\n...[truncated]"


def _compact_slide_for_lecture(slide: Dict[str, Any], max_chars: int = 1200) -> str:
    layout = slide.get("layout") if isinstance(slide.get("layout"), dict) else {}
    if str(layout.get("mode") or "") == "title":
        return ""
    parts: List[str] = []
    title = str(slide.get("title") or "").strip()
    if title:
        parts.append(f"标题: {title}")
    for key in ("content", "notes", "raw_text"):
        value = str(slide.get(key) or "").strip()
        if not value:
            continue
        prefix = "[备注] " if key == "notes" else ""
        if value not in parts:
            parts.append(prefix + value)
    for table_data in (slide.get("tables") or [])[:2]:
        if not isinstance(table_data, dict):
            continue
        rows = table_data.get("rows") or []
        table_lines = [
            " | ".join(str(cell) for cell in row)
            for row in rows[:6]
            if isinstance(row, list)
        ]
        if table_lines:
            parts.append("[表格]\n" + "\n".join(table_lines))
    return _truncate_for_prompt("\n".join(parts), max_chars)


def _compact_evidence_for_prompt(evidence: Any, limit: int = 4, content_chars: int = 260) -> List[Dict[str, Any]]:
    compacted: List[Dict[str, Any]] = []
    for index, item in enumerate(evidence or [], start=1):
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        next_item["index"] = next_item.get("index") or index
        if "content" in next_item:
            next_item["content"] = _truncate_for_prompt(next_item.get("content"), content_chars)
        compacted.append(next_item)
        if len(compacted) >= limit:
            break
    return compacted


def _source_node_ids_from_evidence(evidence: Any) -> List[str]:
    node_ids: List[str] = []
    seen = set()
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        node_id = str(item.get("node_id") or item.get("id") or metadata.get("id") or "").strip()
        if not node_id or node_id.startswith("ppt_slide::") or node_id in seen:
            continue
        seen.add(node_id)
        node_ids.append(node_id)
    return node_ids


def _attach_slide_source_node_ids(
    slides: List[Dict[str, Any]],
    evidence: Any,
) -> List[Dict[str, Any]]:
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        slide_text = _compact_slide_for_lecture(slide, max_chars=1200)
        slide_evidence = _select_slide_source_evidence(
            evidence,
            slide_text,
            limit=6,
            content_chars=260,
        )
        slide["source_node_ids"] = _source_node_ids_from_evidence(slide_evidence)
    return slides


def _slide_match_tokens(text: Any) -> set[str]:
    value = str(text or "").lower()
    tokens = {token for token in re.findall(r"[a-zA-Z0-9_]{2,}", value) if len(token) >= 2}
    cjk_phrases = re.findall(r"[\u4e00-\u9fff]{2,}", value)
    for phrase in cjk_phrases:
        tokens.update(phrase[index : index + 2] for index in range(max(1, len(phrase) - 1)))
    return {token for token in tokens if token not in _SLIDE_EVIDENCE_STOPWORDS}


_SLIDE_EVIDENCE_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "into",
    "slide",
    "page",
    "ppt",
    "title",
    "content",
    "chapter",
    "figure",
    "table",
    "equation",
    "example",
    "标题",
    "内容",
    "本页",
    "课件",
    "章节",
}


def _slide_evidence_score(item: Dict[str, Any], slide_text: str, slide_tokens: set[str]) -> int:
    label = str(item.get("label") or item.get("title") or item.get("id") or "")
    content = str(item.get("content") or "")
    target = f"{label}\n{content}".lower()
    score = sum(1 for token in slide_tokens if token in target)
    label_tokens = _slide_match_tokens(label)
    score += 3 * sum(1 for token in label_tokens if token in slide_text.lower())
    for formula_id in re.findall(r"\b(?:Equation|Eq\.)\s+([A-Za-z]?\d+(?:\.\d+)*[a-z]?)\b|\(([A-Za-z]?\d+(?:\.\d+)*[a-z]?)\)", slide_text, flags=re.I):
        value = next((part for part in formula_id if part), "")
        if value and value.lower() in target:
            score += 6
    return score


def _select_slide_evidence_for_prompt(
    evidence: Any,
    slide_text: str,
    *,
    limit: int = 4,
    content_chars: int = 260,
) -> List[Dict[str, Any]]:
    items = [item for item in (evidence or []) if isinstance(item, dict)]
    if not items:
        return []
    slide_tokens = _slide_match_tokens(slide_text)
    if not slide_tokens:
        return []
    scored: List[tuple[int, int, Dict[str, Any]]] = []
    for index, item in enumerate(items):
        score = _slide_evidence_score(item, slide_text, slide_tokens)
        if score <= 0:
            continue
        scored.append((score, -index, item))
    scored.sort(reverse=True)
    return _compact_evidence_for_prompt([item for _, _, item in scored], limit=limit, content_chars=content_chars)


def _select_slide_source_evidence(
    evidence: Any,
    slide_text: str,
    *,
    limit: int = 6,
    content_chars: int = 260,
) -> List[Dict[str, Any]]:
    items = [item for item in (evidence or []) if isinstance(item, dict)]
    if not items:
        return []
    slide_tokens = _slide_match_tokens(slide_text)
    if not slide_tokens:
        return []
    scored: List[tuple[int, int, Dict[str, Any]]] = []
    for index, item in enumerate(items):
        score = _slide_evidence_score(item, slide_text, slide_tokens)
        if score <= 0:
            continue
        scored.append((score, -index, item))
    if not scored:
        return []
    scored.sort(reverse=True)
    top_score = scored[0][0]
    min_score = max(2, int(top_score * 0.35))
    strong_matches = [item for score, _, item in scored if score >= min_score]
    return _compact_evidence_for_prompt(strong_matches, limit=limit, content_chars=content_chars)


def _build_slide_transition_context(slides: List[Dict[str, Any]], slide_index: Any) -> str:
    if not isinstance(slide_index, int):
        return ""
    ordered = [
        slide
        for slide in sorted(slides, key=lambda item: int(item.get("index") or 0))
        if isinstance(slide, dict) and isinstance(slide.get("index"), int)
    ]
    position = next((idx for idx, slide in enumerate(ordered) if slide.get("index") == slide_index), -1)
    if position < 0:
        return ""
    lines: List[str] = []
    if position > 0:
        previous_slide = ordered[position - 1]
        lines.append(f"Previous slide: {_truncate_for_prompt(_compact_slide_for_lecture(previous_slide, max_chars=360), 360)}")
    if position + 1 < len(ordered):
        next_slide = ordered[position + 1]
        lines.append(f"Next slide: {_truncate_for_prompt(_compact_slide_for_lecture(next_slide, max_chars=360), 360)}")
    return "\n\n".join(lines)


def _slide_feedback_for_index(slide_feedback: Optional[Dict[int, str]], slide_index: Any) -> str:
    if not slide_feedback:
        return ""
    keys = [slide_index]
    try:
        keys.append(int(slide_index))
    except (TypeError, ValueError):
        pass
    keys.append(str(slide_index))
    for key in keys:
        value = slide_feedback.get(key)  # type: ignore[arg-type]
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _courseware_asset_urls_from_map(asset_map: Any) -> dict[str, str]:
    if not isinstance(asset_map, dict):
        return {}
    asset_urls: dict[str, str] = {}

    def add_key(key: Any, value: str) -> None:
        normalized = str(key or "").strip().replace("\\", "/").lstrip("./")
        if not normalized:
            return
        asset_urls.setdefault(normalized, value)
        basename = posixpath.basename(normalized)
        if basename:
            asset_urls.setdefault(basename, value)
            asset_urls.setdefault(f"fig/{basename}", value)

    for asset in asset_map.values():
        if not isinstance(asset, dict):
            continue
        value = str(asset.get("data_uri") or asset.get("url") or asset.get("path") or "").strip()
        if not value:
            continue
        add_key(asset.get("source_path"), value)
        add_key(asset.get("tex_ref"), value)
        add_key(asset.get("name"), value)
        for alias in asset.get("aliases") or []:
            add_key(alias, value)
    return asset_urls


def _safe_render_namespace(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "").strip("._")
    return safe[:96] or "preview"


def _externalize_rendered_pages(pages: list[dict], namespace: str) -> list[dict]:
    safe_namespace = _safe_render_namespace(namespace)
    output_dir = RENDERED_PAGE_DIR / safe_namespace
    converted: list[dict] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        item = dict(page)
        image = str(item.get("image") or "")
        if image.startswith("data:image/") and "," in image:
            header, payload = image.split(",", 1)
            extension = "jpg" if "jpeg" in header.lower() else "png"
            page_index = int(item.get("page_index") or len(converted))
            filename = f"page_{page_index + 1:03d}.{extension}"
            output_dir.mkdir(parents=True, exist_ok=True)
            image_bytes = base64.b64decode(payload, validate=False)
            (output_dir / filename).write_bytes(image_bytes)
            digest = hashlib.md5(image_bytes).hexdigest()[:10]
            item["image"] = f"/api/education/rendered-pages/{safe_namespace}/{filename}?v={digest}"
        converted.append(item)
    if converted:
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "pages.json"
        manifest_path.write_text(json.dumps(converted, ensure_ascii=False), encoding="utf-8")
    return converted


def _load_rendered_page_cache(namespace: str) -> list[dict]:
    safe_namespace = _safe_render_namespace(namespace)
    if safe_namespace != namespace:
        return []
    manifest_path = RENDERED_PAGE_DIR / safe_namespace / "pages.json"
    try:
        pages = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(pages, list):
        return []
    for page in pages:
        if not isinstance(page, dict):
            return []
        image = str(page.get("image") or "")
        filename = Path(image.split("?", 1)[0]).name
        if not re.fullmatch(r"page_\d+\.(?:png|jpg|jpeg)", filename, re.I):
            return []
        if not (RENDERED_PAGE_DIR / safe_namespace / filename).is_file():
            return []
    return pages


def _cleanup_rendered_pages(pages: Any) -> None:
    namespaces: set[str] = set()
    for page in pages if isinstance(pages, list) else []:
        if not isinstance(page, dict):
            continue
        match = re.search(r"/api/education/rendered-pages/([^/]+)/", str(page.get("image") or ""))
        if match:
            namespaces.add(_safe_render_namespace(match.group(1)))
    # A ZIP content hash can be shared by multiple records; retain caches that
    # are still referenced by another persisted chapter or project.
    referenced: set[str] = set()
    try:
        for chapter in chapter_store.list_chapters():
            for page in chapter.get("rendered_pages") or []:
                if not isinstance(page, dict):
                    continue
                match = re.search(r"/api/education/rendered-pages/([^/]+)/", str(page.get("image") or ""))
                if match:
                    referenced.add(_safe_render_namespace(match.group(1)))
    except Exception:
        logger.debug("Unable to scan chapter rendered-page references", exc_info=True)
    project_dir = RUNTIME_DIR / "courseware" / "projects"
    try:
        for manifest_path in project_dir.rglob("*.json"):
            try:
                record = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            project_pages = record.get("rendered_pages") or [] if isinstance(record, dict) else []
            for page in project_pages:
                if not isinstance(page, dict):
                    continue
                match = re.search(r"/api/education/rendered-pages/([^/]+)/", str(page.get("image") or ""))
                if match:
                    referenced.add(_safe_render_namespace(match.group(1)))
    except OSError:
        logger.debug("Unable to scan project rendered-page references", exc_info=True)
    for namespace in namespaces:
        if namespace in referenced:
            continue
        target = (RENDERED_PAGE_DIR / namespace).resolve()
        try:
            target.relative_to(RENDERED_PAGE_DIR.resolve())
        except ValueError:
            continue
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)


def _render_courseware_pdf_pages(tex_content: str, asset_map: Any = None, namespace: str | None = None) -> tuple[list[dict], str]:
    if not tex_content.strip():
        return [], ""
    try:
        pdf_bytes = _compile_latex_to_pdf_bytes(tex_content, _courseware_asset_urls_from_map(asset_map))
        rendered_pages = _render_pdf_bytes_to_pages(pdf_bytes)
        render_namespace = namespace or hashlib.md5(tex_content.encode("utf-8")).hexdigest()
        return _externalize_rendered_pages(rendered_pages, render_namespace), ""
    except Exception as exc:
        return [], str(exc)


def _render_zip_courseware_pdf_pages(file_bytes: bytes, tex_source_file: str, namespace: str | None = None) -> tuple[list[dict], str]:
    """Compile the uploaded ZIP as a real LaTeX project, preserving its assets."""
    render_namespace = namespace or hashlib.md5(file_bytes).hexdigest()
    cached = _load_rendered_page_cache(render_namespace)
    if cached:
        return cached, ""
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive, tempfile.TemporaryDirectory(prefix="kg-courseware-zip-") as temp_name:
            temp_dir = Path(temp_name).resolve()
            source_name = str(tex_source_file or "").replace("\\", "/").lstrip("/")
            if not source_name or any(part in {"", ".", ".."} for part in Path(source_name).parts):
                return [], "ZIP 主 TeX 路径无效"
            for info in archive.infolist():
                name = str(info.filename or "").replace("\\", "/")
                if not name or name.startswith("/") or any(part in {"", ".", ".."} for part in Path(name).parts):
                    continue
                target = (temp_dir / name).resolve()
                try:
                    target.relative_to(temp_dir)
                except ValueError:
                    continue
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(info))
            tex_path = (temp_dir / source_name).resolve()
            try:
                tex_path.relative_to(temp_dir)
            except ValueError:
                return [], "ZIP 主 TeX 路径无效"
            if not tex_path.is_file():
                return [], "ZIP 主 TeX 文件不存在"
            pdf_bytes = _compile_latex_file_to_pdf_bytes(tex_path)
            return _externalize_rendered_pages(_render_pdf_bytes_to_pages(pdf_bytes), render_namespace), ""
    except zipfile.BadZipFile as exc:
        return [], f"ZIP 文件损坏：{exc}"
    except Exception as exc:
        return [], str(exc)


def _update_courseware_render_job(job_id: str, **updates: Any) -> None:
    with COURSEWARE_RENDER_JOBS_LOCK:
        job = COURSEWARE_RENDER_JOBS.get(job_id)
        if job is not None:
            job.update(updates)


def _run_courseware_render_job(job_id: str, zip_path: Path, tex_source_file: str, namespace: str) -> None:
    _update_courseware_render_job(job_id, status="running", started_at=datetime.now().isoformat())
    try:
        # The executor has one worker deliberately: LaTeX and PDF rasterization
        # can briefly use more memory than the uploaded archive itself.
        rendered_pages, render_error = _render_zip_courseware_pdf_pages(zip_path.read_bytes(), tex_source_file, namespace)
        if render_error:
            _update_courseware_render_job(
                job_id,
                status="failed",
                rendered_pages=[],
                rendered_page_count=0,
                render_error=render_error,
                finished_at=datetime.now().isoformat(),
            )
        else:
            _update_courseware_render_job(
                job_id,
                status="completed",
                rendered_pages=rendered_pages,
                rendered_page_count=len(rendered_pages),
                finished_at=datetime.now().isoformat(),
            )
    except Exception as exc:
        logger.exception("Courseware render job %s failed", job_id)
        _update_courseware_render_job(
            job_id,
            status="failed",
            rendered_pages=[],
            rendered_page_count=0,
            render_error=str(exc),
            finished_at=datetime.now().isoformat(),
        )
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            logger.warning("Unable to remove queued courseware archive %s", zip_path)


def _queue_courseware_render(file_bytes: bytes, tex_source_file: str, namespace: str) -> str:
    job_id = uuid.uuid4().hex
    COURSEWARE_RENDER_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = COURSEWARE_RENDER_QUEUE_DIR / f"{job_id}.zip"
    zip_path.write_bytes(file_bytes)
    with COURSEWARE_RENDER_JOBS_LOCK:
        COURSEWARE_RENDER_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "rendered_pages": [],
            "rendered_page_count": 0,
            "namespace": namespace,
            "created_at": datetime.now().isoformat(),
        }
    try:
        COURSEWARE_RENDER_EXECUTOR.submit(_run_courseware_render_job, job_id, zip_path, tex_source_file, namespace)
    except Exception:
        with COURSEWARE_RENDER_JOBS_LOCK:
            COURSEWARE_RENDER_JOBS.pop(job_id, None)
        zip_path.unlink(missing_ok=True)
        raise
    return job_id


@router.get("/education/courseware/render-jobs/{job_id}")
async def get_courseware_render_job(job_id: str):
    with COURSEWARE_RENDER_JOBS_LOCK:
        job = dict(COURSEWARE_RENDER_JOBS.get(job_id) or {})
    if not job:
        raise HTTPException(status_code=404, detail="渲染任务不存在或已过期")
    return {"success": True, **job}


def _chapter_list_item(chapter: Dict[str, Any]) -> Dict[str, Any]:
    content = str(chapter.get("content") or "")
    rendered_pages = chapter.get("rendered_pages") or []
    ppt_slides = chapter.get("ppt_slides") or []
    slide_lectures = chapter.get("slide_lectures") or []
    return {
        "id": chapter.get("id"),
        "course_id": chapter.get("course_id"),
        "title": chapter.get("title"),
        "content": content[:240],
        "content_preview": content[:240],
        "created_at": chapter.get("created_at"),
        "updated_at": chapter.get("updated_at"),
        "source_type": chapter.get("source_type"),
        "source_node_ids": chapter.get("source_node_ids") or [],
        "source_scope": chapter.get("source_scope"),
        "has_content": bool(content.strip()),
        "has_lecture_content": bool(str(chapter.get("lecture_content") or "").strip()),
        "has_tex_content": bool(str(chapter.get("tex_content") or "").strip()),
        "has_rendered_pages": bool(rendered_pages),
        "rendered_page_count": len(rendered_pages) if isinstance(rendered_pages, list) else 0,
        "ppt_slide_count": len(ppt_slides) if isinstance(ppt_slides, list) else 0,
        "slide_lecture_count": len(slide_lectures) if isinstance(slide_lectures, list) else 0,
        "exercise_count": len(chapter.get("exercise_bank") or []),
    }


def _format_slide_visible_elements_for_prompt(slide: Dict[str, Any]) -> str:
    lines: List[str] = []
    title = str(slide.get("title") or "").strip()
    if title:
        lines.append(f"- title: {title}")
    body_texts = [str(item).strip() for item in (slide.get("body_texts") or []) if str(item).strip()]
    if body_texts:
        for index, text in enumerate(body_texts[:8], start=1):
            lines.append(f"- visible text {index}: {_truncate_for_prompt(text, 220)}")
    images = _slide_visible_images(slide)
    for index, image in enumerate(images[:6], start=1):
        label = str(image.get("label") or image.get("figure_id") or image.get("source_path") or image.get("tex_ref") or image.get("name") or f"image {index}").strip()
        detail_parts = []
        for key in ("caption", "alt", "source_path", "tex_ref"):
            value = str(image.get(key) or "").strip()
            if value and value not in detail_parts:
                detail_parts.append(value)
        detail = "; ".join(detail_parts)
        lines.append(f"- visible image {index}: {label}{f' ({_truncate_for_prompt(detail, 180)})' if detail else ''}")
    for table_index, table_data in enumerate((slide.get("tables") or [])[:2], start=1):
        if not isinstance(table_data, dict):
            continue
        rows = table_data.get("rows") or []
        row_texts = [
            " | ".join(str(cell) for cell in row)
            for row in rows[:4]
            if isinstance(row, list)
        ]
        if row_texts:
            lines.append(f"- visible table {table_index}: {_truncate_for_prompt('; '.join(row_texts), 260)}")
    return "\n".join(lines)


def _slide_visible_images(slide: Dict[str, Any]) -> List[Dict[str, Any]]:
    images: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add(image: Any) -> None:
        if not isinstance(image, dict):
            return
        key = str(image.get("source_path") or image.get("tex_ref") or image.get("data_uri") or image.get("name") or image.get("figure_id") or "")
        if key and key in seen:
            return
        if key:
            seen.add(key)
        images.append(image)

    for image in slide.get("images") or []:
        add(image)
    layout = slide.get("layout") if isinstance(slide.get("layout"), dict) else {}
    for column in layout.get("columns") or []:
        if isinstance(column, dict):
            for image in column.get("images") or []:
                add(image)
    return images


def _compact_source_for_response(item: Any, content_chars: int = 180) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    allowed_keys = ("id", "label", "title", "name", "type", "source", "index", "content")
    compacted = {key: item.get(key) for key in allowed_keys if item.get(key) is not None}
    if "content" in compacted:
        compacted["content"] = _truncate_for_prompt(compacted.get("content"), content_chars)
    return compacted


def _compact_learning_plan_for_response(plan: Any) -> Any:
    if not isinstance(plan, dict):
        return plan
    compacted = {
        key: plan.get(key)
        for key in ("query", "task", "learner_intent", "learning_level", "allowed_concepts", "coverage")
        if plan.get(key) is not None
    }
    compacted["evidence"] = [
        item
        for item in (_compact_source_for_response(source) for source in (plan.get("evidence") or [])[:6])
        if item
    ]
    return compacted


def _compact_slide_lecture_for_response(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    compacted = {
        key: value
        for key, value in item.items()
        if key not in {"retrieval_stats", "graphrag_context", "vector_hits"}
    }
    compacted["sources"] = [
        source
        for source in (_compact_source_for_response(source) for source in (item.get("sources") or [])[:6])
        if source
    ]
    compacted["graph_paths"] = (item.get("graph_paths") or [])[:4]
    compacted["formula_context"] = (item.get("formula_context") or [])[:4]
    compacted["learning_plan"] = _compact_learning_plan_for_response(item.get("learning_plan"))
    return compacted


def _compact_slide_lectures_for_response(items: Any) -> List[Any]:
    return [_compact_slide_lecture_for_response(item) for item in (items or [])]


def _compact_slide_lecture_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    compacted = dict(payload)
    compacted["slide_lectures"] = _compact_slide_lectures_for_response(payload.get("slide_lectures") or [])
    compacted["learning_plan"] = _compact_learning_plan_for_response(payload.get("learning_plan"))
    compacted.pop("graphrag_context", None)
    compacted.pop("vector_hits", None)
    compacted.pop("retrieval_stats", None)
    compacted["graph_paths"] = (payload.get("graph_paths") or [])[:8]
    compacted["formula_context"] = (payload.get("formula_context") or [])[:8]
    return compacted


def _fallback_slide_learning_plan(
    *,
    chapter_title: str,
    slide: Dict[str, Any],
    slide_text: str,
    evidence: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    slide_index = slide.get("index", "?")
    slide_evidence = {
        "index": 1,
        "id": f"ppt_slide::{hashlib.md5(f'{chapter_title}:{slide_index}:{slide_text}'.encode('utf-8')).hexdigest()[:10]}",
        "label": str(slide.get("title") or f"第 {slide_index} 页"),
        "type": "ppt_slide",
        "content": _truncate_for_prompt(slide_text, 900),
        "source": "ppt",
    }
    return build_learning_plan(
        query=f"{chapter_title} 第 {slide.get('index')} 页",
        evidence=_dedupe_evidence([*list(evidence or [])[:3], slide_evidence]),
        learner_intent="explain",
        learning_level="beginner",
        task="lecture",
        chapter_data={
            "title": f"{chapter_title} - 第 {slide.get('index')} 页",
            "content": _truncate_for_prompt(slide_text, 1000),
        },
    )


def _fallback_slide_lecture_text(*, chapter_title: str, slide: Dict[str, Any], slide_text: str) -> str:
    title = str(slide.get("title") or f"第 {slide.get('index', '?')} 页").strip()
    clean_lines = [
        line.strip(" -\t")
        for line in re.split(r"[\n\r]+", str(slide_text or ""))
        if line.strip(" -\t")
    ]
    body_lines = clean_lines[:6] or [title]
    bullets = "\n".join(f"- {line}" for line in body_lines)
    return (
        f"这一页围绕 **{title}** 展开。课堂讲解时可以先把它放回《{chapter_title}》的整体脉络中，"
        "说明本页要解决的问题，再逐条解释页面上的关键概念。\n\n"
        f"{bullets}\n\n"
        "讲授时建议最后用一句话收束：本页的核心作用是帮助学生把这些要点和前后页面的知识链条连接起来。"
    )


def _normalize_slide_lecture_duration_minutes(value: Optional[float]) -> float:
    try:
        duration = float(value if value is not None else DEFAULT_SLIDE_LECTURE_DURATION_MINUTES)
    except (TypeError, ValueError):
        duration = DEFAULT_SLIDE_LECTURE_DURATION_MINUTES
    return max(0.1, min(duration, 180.0))


def _normalize_slide_lecture_speech_rate_cpm(value: Optional[int]) -> int:
    try:
        rate = int(value if value is not None else DEFAULT_SLIDE_LECTURE_SPEECH_RATE_CPM)
    except (TypeError, ValueError):
        rate = DEFAULT_SLIDE_LECTURE_SPEECH_RATE_CPM
    return max(80, min(rate, 800))


def _slide_lecture_concurrency() -> int:
    try:
        concurrency = int(os.getenv("KGTS_SLIDE_LECTURE_CONCURRENCY", "3"))
    except (TypeError, ValueError):
        concurrency = 3
    return max(1, min(concurrency, 6))


def _optional_timeout_env(name: str, default: float | None) -> float | None:
    text = str(os.getenv(name, "")).strip().lower()
    if not text or text == "default":
        return default
    if text in {"0", "none", "off", "false"}:
        return None
    try:
        return max(float(text), 1.0)
    except ValueError:
        return default


def _slide_lecture_pacing_timeout() -> float | None:
    return _optional_timeout_env("KGTS_SLIDE_LECTURE_PACING_TIMEOUT_SECONDS", None)


def _slide_lecture_read_timeout(phase: str) -> float | None:
    if phase == "flash_fallback":
        return _optional_timeout_env("KGTS_SLIDE_LECTURE_FLASH_READ_TIMEOUT_SECONDS", None)
    return _optional_timeout_env("KGTS_SLIDE_LECTURE_READ_TIMEOUT_SECONDS", None)


def _slide_lecture_completion_timeout() -> float | None:
    return _optional_timeout_env("KGTS_SLIDE_LECTURE_COMPLETION_TIMEOUT_SECONDS", None)


def _slide_lecture_max_output_tokens() -> int:
    try:
        value = int(os.getenv("KGTS_SLIDE_LECTURE_MAX_OUTPUT_TOKENS", str(DEFAULT_SLIDE_LECTURE_MAX_OUTPUT_TOKENS)))
    except (TypeError, ValueError):
        value = DEFAULT_SLIDE_LECTURE_MAX_OUTPUT_TOKENS
    return max(1200, min(value, 12000))


def _count_speech_chars(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", str(text or "")))


def _slide_budget_source_text(slide: Dict[str, Any]) -> str:
    layout = slide.get("layout") if isinstance(slide.get("layout"), dict) else {}
    if str(layout.get("mode") or "") == "title":
        return ""
    parts: List[str] = []
    for key in ("title", "content", "notes", "raw_text"):
        value = str(slide.get(key) or "").strip()
        if value:
            parts.append(value)
    for table_data in slide.get("tables", []) or []:
        if not isinstance(table_data, dict):
            continue
        for row in table_data.get("rows", []) or []:
            if isinstance(row, list):
                parts.append(" ".join(str(cell) for cell in row))
    return "\n".join(parts)


def _build_slide_lecture_pacing(
    slides: List[Dict[str, Any]],
    *,
    target_duration_minutes: Optional[float] = None,
    speech_rate_cpm: Optional[int] = None,
) -> Dict[str, Any]:
    duration = _normalize_slide_lecture_duration_minutes(target_duration_minutes)
    rate = _normalize_slide_lecture_speech_rate_cpm(speech_rate_cpm)
    total_budget = max(1, int(round(duration * rate)))
    normalized_slides = [
        slide
        for slide in slides
        if isinstance(slide, dict) and isinstance(slide.get("index"), int)
    ]
    if not normalized_slides:
        return {
            "target_duration_minutes": duration,
            "speech_rate_cpm": rate,
            "total_target_chars": total_budget,
            "slides": {},
        }

    raw_counts = {
        int(slide["index"]): _count_speech_chars(_slide_budget_source_text(slide))
        for slide in normalized_slides
    }
    weights: Dict[int, float] = {}
    for slide in normalized_slides:
        index = int(slide["index"])
        text_chars = raw_counts.get(index, 0)
        title_only = bool(str(slide.get("title") or "").strip()) and text_chars <= _count_speech_chars(str(slide.get("title") or ""))
        if text_chars <= 0:
            weight = 0.35
        elif title_only or text_chars <= 18:
            weight = 0.65
        else:
            weight = max(float(text_chars), 28.0)
        weights[index] = weight

    total_weight = sum(weights.values()) or float(len(normalized_slides))
    remaining = total_budget
    allocations: Dict[int, int] = {}
    remainders: List[tuple[float, int]] = []
    minimum = 30 if total_budget >= len(normalized_slides) * 30 else 1
    for slide in normalized_slides:
        index = int(slide["index"])
        exact = total_budget * weights[index] / total_weight
        target = max(minimum, int(exact))
        allocations[index] = target
        remaining -= target
        remainders.append((exact - int(exact), index))

    if remaining > 0:
        for _, index in sorted(remainders, reverse=True):
            if remaining <= 0:
                break
            allocations[index] += 1
            remaining -= 1
    elif remaining < 0:
        for _, index in sorted(remainders):
            if remaining >= 0:
                break
            removable = min(allocations[index] - 1, -remaining)
            if removable <= 0:
                continue
            allocations[index] -= removable
            remaining += removable

    by_index: Dict[int, Dict[str, Any]] = {}
    for slide in normalized_slides:
        index = int(slide["index"])
        target_chars = max(1, allocations.get(index, 1))
        by_index[index] = {
            "target_chars": target_chars,
            "source_chars": raw_counts.get(index, 0),
            "target_duration_seconds": int(round(target_chars / rate * 60)),
        }

    return {
        "target_duration_minutes": duration,
        "speech_rate_cpm": rate,
        "total_target_chars": sum(item["target_chars"] for item in by_index.values()),
        "slides": by_index,
    }


def _apply_model_slide_lecture_allocations(
    base_pacing: Dict[str, Any],
    allocations: Any,
) -> Dict[str, Any]:
    """Apply model-planned slide character budgets while preserving a valid total."""
    base_slides = base_pacing.get("slides") if isinstance(base_pacing.get("slides"), dict) else {}
    if not base_slides:
        return base_pacing

    planned: Dict[int, int] = {}
    if isinstance(allocations, dict):
        allocations = allocations.get("slides") or allocations.get("allocations") or allocations.get("pages") or []
    for item in allocations or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index") or item.get("slide") or item.get("page"))
            target_chars = int(round(float(item.get("target_chars") or item.get("chars") or item.get("words") or 0)))
        except (TypeError, ValueError):
            continue
        if index not in base_slides or target_chars <= 0:
            continue
        planned[index] = target_chars
    if not planned:
        return base_pacing

    total_target = int(base_pacing.get("total_target_chars") or sum(int(item.get("target_chars") or 0) for item in base_slides.values()))
    minimum = 30 if total_target >= len(base_slides) * 30 else 1
    allocations_by_index: Dict[int, int] = {
        int(index): max(minimum, int(planned.get(int(index), item.get("target_chars") or minimum)))
        for index, item in base_slides.items()
    }

    diff = total_target - sum(allocations_by_index.values())
    if diff > 0:
        ranked = sorted(allocations_by_index, key=lambda index: allocations_by_index[index], reverse=True)
        cursor = 0
        while diff > 0 and ranked:
            allocations_by_index[ranked[cursor % len(ranked)]] += 1
            diff -= 1
            cursor += 1
    elif diff < 0:
        ranked = sorted(allocations_by_index, key=lambda index: allocations_by_index[index], reverse=True)
        for index in ranked:
            if diff >= 0:
                break
            removable = min(allocations_by_index[index] - minimum, -diff)
            if removable <= 0:
                continue
            allocations_by_index[index] -= removable
            diff += removable

    rate = _normalize_slide_lecture_speech_rate_cpm(base_pacing.get("speech_rate_cpm"))
    next_slides: Dict[int, Dict[str, Any]] = {}
    for raw_index, item in base_slides.items():
        index = int(raw_index)
        target_chars = max(1, allocations_by_index.get(index, int(item.get("target_chars") or 1)))
        next_slides[index] = {
            **item,
            "target_chars": target_chars,
            "target_duration_seconds": int(round(target_chars / rate * 60)),
            "budget_source": "deepseek-v4-pro",
        }

    return {
        **base_pacing,
        "total_target_chars": sum(item["target_chars"] for item in next_slides.values()),
        "slides": next_slides,
        "budget_source": "deepseek-v4-pro",
    }


async def _build_slide_lecture_pacing_with_model(
    client: DeepSeekAPIClient,
    slides: List[Dict[str, Any]],
    *,
    chapter_title: str,
    style: str,
    target_duration_minutes: Optional[float] = None,
    speech_rate_cpm: Optional[int] = None,
    teacher_guidance: str = "",
    style_reference_guidance: str = "",
) -> Dict[str, Any]:
    base_pacing = _build_slide_lecture_pacing(
        slides,
        target_duration_minutes=target_duration_minutes,
        speech_rate_cpm=speech_rate_cpm,
    )
    normalized_slides = [
        slide
        for slide in slides
        if isinstance(slide, dict) and isinstance(slide.get("index"), int)
    ]
    if len(normalized_slides) <= 1:
        return base_pacing

    slide_summaries = []
    for slide in normalized_slides:
        index = int(slide["index"])
        local = (base_pacing.get("slides") or {}).get(index) or {}
        slide_summaries.append(
            {
                "index": index,
                "title": str(slide.get("title") or "")[:80],
                "source_chars": local.get("source_chars") or _count_speech_chars(_slide_budget_source_text(slide)),
                "local_target_chars": local.get("target_chars"),
                "content": _compact_slide_for_lecture(slide, max_chars=420),
            }
        )

    prompt = f"""请为一份 PPT 的逐页课堂讲稿分配每页目标字数。

课程标题：{chapter_title}
讲课风格：{style}
总时长：{base_pacing.get("target_duration_minutes")} 分钟
语速估算：{base_pacing.get("speech_rate_cpm")} 中文字符/分钟
总目标字数：{base_pacing.get("total_target_chars")}

教师输入/偏好：
{_truncate_for_prompt(teacher_guidance, 900) or "（无）"}

参考课件风格提示：
{_truncate_for_prompt(style_reference_guidance, 700) or "（无）"}

页面摘要 JSON：
{json.dumps(slide_summaries, ensure_ascii=False)}

要求：
1. 结合页面内容密度、标题页/过渡页、公式页、例题页和教师输入分配每页 target_chars。
2. 所有 target_chars 之和必须等于总目标字数。
3. 每页必须给正整数；内容少的页面可以少，但不要为非空页面分配 0。
4. 只输出 JSON，不要解释，格式为：
{{"slides":[{{"index":1,"target_chars":180,"reason":"标题页简短导入"}}]}}"""
    try:
        response = await client._call_deepseek(
            prompt,
            max_tokens=1600,
            system_prompt="你是课程讲稿节奏规划器。只返回可解析 JSON。",
            read_timeout_seconds=_slide_lecture_pacing_timeout(),
        )
        payload = json.loads(_strip_json_fence(response))
        planned = _apply_model_slide_lecture_allocations(base_pacing, payload)
        planned["budget_model"] = client.model
        return planned
    except Exception as exc:
        return {
            **base_pacing,
            "budget_source": "local_fallback",
            "budget_model": client.model,
            "budget_error": str(exc).strip() or exc.__class__.__name__,
        }


def _attach_slide_lecture_timing(
    item: Dict[str, Any],
    pacing: Optional[Dict[str, Any]],
    speech_rate_cpm: int,
) -> Dict[str, Any]:
    lecture = str(item.get("lecture") or "")
    if "speech_cues" not in item:
        item["speech_cues"] = _fallback_speech_cues_for_lecture(lecture, max_cues=1)
    else:
        item["speech_cues"] = normalize_speech_cues(lecture, item.get("speech_cues"), max_repeat_cues=2)
    spoken_text = apply_speech_cues_for_tts(lecture, item.get("speech_cues") or [])
    estimated_chars = _count_speech_chars(spoken_text)
    target_chars = int((pacing or {}).get("target_chars") or max(estimated_chars, 0))
    item["target_chars"] = target_chars
    if pacing:
        item["target_duration_seconds"] = int((pacing or {}).get("target_duration_seconds") or 0)
        item["budget_source"] = str((pacing or {}).get("budget_source") or "")
    item["estimated_chars"] = estimated_chars
    item["estimated_duration_seconds"] = int(round((estimated_chars / max(speech_rate_cpm, 1)) * 60))
    return item


def _speech_plan_sentence_candidates(lecture: str) -> List[str]:
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", str(lecture or ""))
    text = re.sub(r"`([^`]*)`", r"\1", text)
    raw_parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    candidates: List[str] = []
    for raw in raw_parts:
        sentence = re.sub(r"^[#>*\-\d.\s]+", "", raw).strip()
        sentence = re.sub(r"\s+", " ", sentence).strip(" ，。；：、")
        if len(sentence) < 10 or len(sentence) > 80:
            continue
        if sentence in candidates:
            continue
        candidates.append(sentence)
    return candidates


def _fallback_speech_cues_for_lecture(lecture: str, *, max_cues: int = 1) -> List[Dict[str, str]]:
    if max_cues <= 0:
        return []
    keyword_patterns = (
        r"关键|核心|重点|重要|注意|本质|决定|影响|意味着|因此|所以|结论|记住",
        r"Ne|N_e|选择|漂变|固定概率|遗传|方差|均值|适合度",
    )
    scored: List[tuple[int, str]] = []
    for sentence in _speech_plan_sentence_candidates(lecture):
        score = 0
        for pattern in keyword_patterns:
            if re.search(pattern, sentence, flags=re.I):
                score += 2
        score += min(len(sentence), 60) // 20
        scored.append((score, sentence))
    scored.sort(key=lambda item: (-item[0], len(item[1])))
    cues = [
        {"type": "repeat", "target_text": sentence, "style": "key_point", "priority": index + 1}
        for index, (_score, sentence) in enumerate(scored[:max_cues])
    ]
    return normalize_speech_cues(lecture, cues, max_repeat_cues=max_cues)


def _parse_speech_plan_payload(raw: str) -> List[Dict[str, Any]]:
    text = _strip_json_fence(str(raw or "").strip())
    try:
        data = json.loads(text)
    except Exception:
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except Exception:
            return []
    if isinstance(data, dict):
        cues = data.get("speech_cues") or data.get("cues") or []
    else:
        cues = data
    return cues if isinstance(cues, list) else []


async def _plan_slide_speech_cues_with_model(request: PlanSlideSpeechRequest) -> List[Dict[str, str]]:
    lecture = str(request.lecture or "").strip()
    max_cues = max(0, min(int(request.max_cues or 1), 3))
    if not lecture or max_cues <= 0:
        return []
    slide = request.slide or {}
    slide_text = _compact_slide_for_lecture(slide, max_chars=900) if isinstance(slide, dict) else ""
    prompt = f"""请为一页 PPT 讲稿生成语音规划，只标记真正需要口播重复的重点。

课程标题：{request.chapter_title or ""}
页面标题：{slide.get("title", "") if isinstance(slide, dict) else ""}

页面内容：
{slide_text}

讲稿正文：
{lecture}

约束：
1. 只返回 JSON，不要解释。
2. JSON 格式为 {{"speech_cues":[{{"type":"repeat","target_text":"讲稿中的原句或短语","style":"key_point","priority":1}}]}}。
3. target_text 必须是讲稿正文中逐字存在的连续文本，不要改写，不要新增讲稿里没有的句子。
4. 最多返回 {max_cues} 个重点；如果没有值得重复的重点，返回 {{"speech_cues":[]}}。
5. target_text 长度控制在 10 到 80 个字符，优先选择承载本页结论、定义、公式含义或易错点的短句。
"""
    if request.teacher_guidance:
        prompt += "\n教师强调偏好：\n" + _truncate_for_prompt(str(request.teacher_guidance), 500)
    client = DeepSeekAPIClient(api_key=request.api_key, model=request.model or get_deepseek_model("flash"))
    try:
        raw = await client._call_deepseek(
            prompt,
            max_tokens=900,
            system_prompt=KG_CONSTRAINED_SYSTEM_PROMPT,
            read_timeout_seconds=60,
        )
        cues = normalize_speech_cues(lecture, _parse_speech_plan_payload(raw), max_repeat_cues=max_cues)
    except Exception:
        cues = []
    return cues or _fallback_speech_cues_for_lecture(lecture, max_cues=max_cues)


def _summarize_slide_lecture_pacing(slide_lectures: List[Dict[str, Any]], pacing: Dict[str, Any]) -> Dict[str, Any]:
    estimated_chars = sum(int(item.get("estimated_chars") or _count_speech_chars(str(item.get("lecture") or ""))) for item in slide_lectures)
    rate = _normalize_slide_lecture_speech_rate_cpm(pacing.get("speech_rate_cpm"))
    return {
        "target_duration_minutes": pacing.get("target_duration_minutes", DEFAULT_SLIDE_LECTURE_DURATION_MINUTES),
        "speech_rate_cpm": rate,
        "total_target_chars": pacing.get("total_target_chars", 0),
        "estimated_chars": estimated_chars,
        "estimated_duration_seconds": int(round((estimated_chars / max(rate, 1)) * 60)),
    }


def _normalize_target_slide_indices(target_slide_indices: Optional[List[int]], slides: List[Dict[str, Any]]) -> Optional[List[int]]:
    if not target_slide_indices:
        return None
    valid_indices = {
        int(slide.get("index"))
        for slide in slides
        if isinstance(slide, dict) and isinstance(slide.get("index"), int)
    }
    normalized: List[int] = []
    seen = set()
    for raw_index in target_slide_indices:
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="target_slide_indices 必须是页面编号数组")
        if index not in valid_indices:
            raise HTTPException(status_code=400, detail=f"页面 {index} 不存在，无法重生成")
        if index in seen:
            continue
        seen.add(index)
        normalized.append(index)
    return normalized or None


def _merge_existing_slide_lectures(
    existing_slide_lectures: Optional[List[Dict[str, Any]]],
    generated_slide_lectures: List[Dict[str, Any]],
    slides: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    generated_by_index = {
        int(item.get("index")): item
        for item in generated_slide_lectures
        if isinstance(item, dict) and isinstance(item.get("index"), int)
    }
    existing_by_index = {
        int(item.get("index")): item
        for item in (existing_slide_lectures or [])
        if isinstance(item, dict) and isinstance(item.get("index"), int)
    }
    merged: List[Dict[str, Any]] = []
    for slide in slides:
        if not isinstance(slide, dict) or not isinstance(slide.get("index"), int):
            continue
        index = int(slide["index"])
        if index in generated_by_index:
            merged.append(generated_by_index[index])
        elif index in existing_by_index:
            existing = dict(existing_by_index[index])
            if not existing.get("title") and slide.get("title"):
                existing["title"] = slide.get("title", "")
            merged.append(existing)
        else:
            merged.append(
                {
                    "index": index,
                    "title": slide.get("title", ""),
                    "lecture": "",
                    "skipped": True,
                }
            )
    return merged


def _build_source_drift_report(ppt_source_node_ids: List[str], lecture_source_node_ids: List[str]) -> Dict[str, Any]:
    ppt_set = {str(item) for item in (ppt_source_node_ids or []) if str(item).strip()}
    lecture_set = {str(item) for item in (lecture_source_node_ids or []) if str(item).strip()}
    if not ppt_set:
        return {"status": "unknown", "changed": False}
    added = sorted(lecture_set - ppt_set)
    removed = sorted(ppt_set - lecture_set)
    changed = bool(added or removed)
    report: Dict[str, Any] = {
        "status": "changed" if changed else "aligned",
        "changed": changed,
        "added_node_ids": added,
        "removed_node_ids": removed,
        "ppt_source_node_ids": sorted(ppt_set),
        "lecture_source_node_ids": sorted(lecture_set),
    }
    if changed:
        report["warning"] = "讲解阶段选择的图谱范围与生成 PPT/TeX 时不完全一致，系统已用页面内容作为主锚点降低漂移。"
    return report


@router.post("/education/ask-question")
async def ask_question(request: AskQuestionRequest):
    try:
        return await answer_with_retrieval(request.question, request.api_key, timeout_seconds=40)
    except ValueError as e:
        return _build_question_fallback_response(
            request.question,
            model=get_deepseek_model("flash"),
            warning=f"问答模型不可用，已使用本地图谱检索回答：{e}",
        )
    except Exception as e:
        return _build_question_fallback_response(
            request.question,
            model=get_deepseek_model("flash"),
            warning=f"问答服务异常，已使用本地图谱检索回答：{e}",
        )


@router.post("/education/learning-plan")
async def create_learning_plan(request: LearningPlanRequest):
    try:
        chapter = chapter_store.get_chapter(request.chapter_id) if request.chapter_id else None
        graph_data = None
        if chapter and chapter.get("graph_data"):
            graph_data = chapter.get("graph_data")
        else:
            try:
                graph_data = build_frontend_graph()
            except Exception:
                graph_data = None

        plan = _build_plan_from_graph(
            query=request.query,
            graph_data=graph_data,
            task=request.task,
            chapter_data=chapter,
            learning_level=request.learning_level,
        )
        if not plan.get("evidence"):
            rag = build_rag_context(request.query, limit=6)
            plan = build_learning_plan(
                query=request.query,
                evidence=evidence_from_rag(rag.get("llm_context") or [], limit=6),
                learner_intent=None,
                learning_level=request.learning_level,
                task=request.task,
                chapter_data=chapter,
            )
        return {
            "success": True,
            "learning_plan": plan,
            "created_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"构建学习计划失败: {str(e)}")


@router.post("/education/natural-supplement")
async def natural_supplement(request: NaturalSupplementRequest):
    query = f"{request.supplement}\n{request.original_text[:800]}"
    graph_data = None
    warning = None
    try:
        graph_data = build_frontend_graph()
    except Exception as exc:
        warning = f"图谱读取失败，已尝试使用 RAG 检索：{exc}"

    learning_plan = _build_plan_from_graph(
        query=query,
        graph_data=graph_data if isinstance(graph_data, dict) else None,
        task="lecture",
        chapter_data={
            "title": "自然补充",
            "content": f"{request.original_text}\n\n{request.supplement}",
        },
    )
    sources = learning_plan.get("evidence") or []
    retrieval_context = ""
    if not sources:
        try:
            rag = build_rag_context(query, limit=6)
            retrieval_context = rag.get("context") or ""
            sources = rag.get("llm_context") or []
            learning_plan = build_learning_plan(
                query=query,
                evidence=evidence_from_rag(sources, limit=6),
                learner_intent="explain",
                learning_level="beginner",
                task="lecture",
                chapter_data={
                    "title": "自然补充",
                    "content": f"{request.original_text}\n\n{request.supplement}",
                },
            )
            warning = warning or "图谱未命中，已使用 RAG 检索证据生成补充文案。"
        except Exception as exc:
            warning = f"{warning + '；' if warning else ''}RAG 检索失败，已退回纯模型补充：{exc}"

    try:
        claude_client = DeepSeekAPIClient(
            api_key=request.api_key,
            model=get_deepseek_model("pro"),
        )
        prompt = build_constrained_generation_prompt(
            task_title="将补充内容自然融入授课文案",
            user_input=request.supplement,
            source_content=request.original_text,
            learning_plan=learning_plan,
            requirements=[
                "保持原文逻辑结构和语气，输出整段融合后的最终文案。",
                "优先使用证据中出现的概念、公式、术语和关系。",
                "如果补充内容中有证据未覆盖的扩展，请在措辞中保持限定，不编造课程私有事实。",
                "不要输出提纲、自检说明或证据清单。",
            ],
        )
        result = expand_formula_references(
            await claude_client._call_deepseek(
                prompt,
                max_tokens=2600,
                system_prompt=KG_CONSTRAINED_SYSTEM_PROMPT,
                read_timeout_seconds=60.0,
            ),
            expand_labels=True,
        )
        payload = {
            "success": True,
            "result": result,
            "model": claude_client.model,
            "retrieval_context": retrieval_context,
            "sources": sources,
            "learning_plan": learning_plan,
            "consistency_report": _safe_consistency_report(result, learning_plan, task="lecture"),
        }
        if warning:
            payload["warning"] = warning
        return payload
    except ValueError as e:
        error_text = str(e)
        if "API密钥" in error_text or "API" in error_text.upper() or "KEY" in error_text.upper():
            fallback_result = f"{request.original_text}\n\n{request.supplement}"
            return {
                "success": True,
                "result": fallback_result,
                "warning": "DeepSeek API未配置，请在设置中配置API密钥",
                "retrieval_context": retrieval_context,
                "sources": sources,
                "learning_plan": learning_plan,
                "consistency_report": _safe_consistency_report(fallback_result, learning_plan, task="lecture"),
            }
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"自然补充失败: {str(e)}")


@router.get("/education/graph")
async def get_graph():
    try:
        delete_generated_lecture_nodes()
        return {
            "success": True,
            "data": build_frontend_graph(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取知识图谱失败: {str(e)}")


@router.get("/education/graph/node-context")
async def get_graph_node_context(node_id: List[str] = Query(...), max_nodes: int = 260):
    try:
        result = build_node_contexts(node_id, max_nodes=max(1, min(max_nodes, 600)))
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=result.get("error") or "Graph node not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"构建节点备课上下文失败: {str(e)}")


@router.get("/education/artifacts/{filename}")
async def get_generated_artifact(filename: str):
    safe_name = Path(filename).name
    path = ARTIFACT_DIR / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="生成文件不存在")
    media_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if safe_name.lower().endswith(".tex"):
        media_type = "application/x-tex"
    return FileResponse(path, media_type=media_type, filename=safe_name)


@router.post("/education/cleanup-lecture-nodes")
async def cleanup_lecture_nodes():
    try:
        return delete_generated_lecture_nodes()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cleanup lecture nodes failed: {str(e)}")


@router.post("/education/add-node")
async def add_node(request: AddNodeRequest):
    try:
        result = await call_mcp_tool(
            "add_memory",
            {
                "content": request.content,
                "type": request.type,
                "metadata": request.metadata,
            },
        )
        return {
            "success": True,
            "node": result,
            "message": "节点已添加",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加节点失败: {str(e)}")


@router.put("/education/update-node")
async def update_node(request: UpdateNodeRequest):
    try:
        result = await call_mcp_tool(
            "update_memory",
            {
                "node_id": request.node_id,
                "content": request.content,
                "metadata": request.metadata,
            },
        )
        return {
            "success": True,
            "updated": result,
            "message": "节点已更新",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新节点失败: {str(e)}")


@router.get("/education/search-nodes")
async def search_nodes(keyword: str, node_type: Optional[str] = None, limit: int = 10):
    try:
        return {
            "success": True,
            "results": backend_search_nodes(keyword, node_type=node_type, limit=limit),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索节点失败: {str(e)}")


@router.get("/education/schema")
async def get_schema():
    try:
        return {
            "success": True,
            "schema": get_graph_schema(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取图谱结构失败: {str(e)}")


@router.post("/education/upload-graph")
async def upload_graph(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    filename = file.filename
    lower_name = filename.lower()
    if not lower_name.endswith((".json", ".graphml", ".xml", ".db", ".sqlite", ".sqlite3")):
        raise HTTPException(status_code=400, detail="仅支持 .json、.graphml、.xml、.db、.sqlite 或 .sqlite3 图谱文件")

    try:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="上传的文件为空")

        if lower_name.endswith((".db", ".sqlite", ".sqlite3")):
            result = import_graph_db_payload(file_bytes)
            parsed_stats = result.get("sqlite_stats") if isinstance(result, dict) else {}
            parsed = {
                "nodes": parsed_stats.get("nodes_parsed", 0),
                "relations": parsed_stats.get("relations_parsed", 0),
            }
            graph_type = "sqlite"
        else:
            text = file_bytes.decode("utf-8-sig")
            if lower_name.endswith(".json"):
                payload = json.loads(text)
                if not isinstance(payload, dict):
                    raise HTTPException(status_code=400, detail="JSON 图谱文件必须是对象")
                graph_data = payload.get("graph_data") if isinstance(payload.get("graph_data"), dict) else payload
                result = import_graph_payload(graph_data)
                parsed = {
                    "nodes": len(graph_data.get("nodes") or []),
                    "relations": len(graph_data.get("relations") or graph_data.get("edges") or []),
                }
                graph_type = "json"
            else:
                result = import_graphml_payload(file_content=text)
                parsed_stats = result.get("graphml_stats") if isinstance(result, dict) else {}
                parsed = {
                    "nodes": parsed_stats.get("nodes_parsed", 0),
                    "relations": parsed_stats.get("edges_parsed", 0),
                }
                graph_type = "graphml"

        return {
            "success": True,
            "file_name": filename,
            "graph_type": graph_type,
            "chapter_hint": {
                "title": "",
                "content": f"请基于已导入的知识图谱生成授课文案。图谱文件：{filename}",
            },
            "parsed": parsed,
            "result": result,
            "message": "图谱文件导入成功",
            "imported_at": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件必须使用 UTF-8 编码")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e.msg}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图谱文件导入失败: {str(e)}")


@router.post("/education/save-chapter")
async def save_chapter(request: SaveChapterRequest):
    try:
        source_node_ids = _normalize_source_node_ids(None, request.source_node_ids)
        chapter_data = chapter_store.save_chapter(
            title=request.title,
            content=request.content,
            graph_data=request.graph_data,
            chapter_id=request.chapter_id,
            course_id=request.course_id,
            source_type=request.source_type,
            source_node_ids=source_node_ids or request.source_node_ids,
            source_scope=request.source_scope,
            ppt_slides=request.ppt_slides,
            slide_lectures=request.slide_lectures,
            tex_content=request.tex_content,
            editable_model=request.editable_model,
            asset_map=request.asset_map,
            rendered_pages=request.rendered_pages,
            render_source=request.render_source,
            render_error=request.render_error,
            ppt_artifact=request.ppt_artifact,
            ppt_source_node_ids=request.ppt_source_node_ids,
            lecture_source_node_ids=request.lecture_source_node_ids,
            lecture_target_duration_minutes=request.lecture_target_duration_minutes,
            lecture_speech_rate_cpm=request.lecture_speech_rate_cpm,
            lecture_pacing=request.lecture_pacing,
        )
        return {
            "success": True,
            "chapter_id": chapter_data["id"],
            "chapter": chapter_data,
            "message": "章节保存成功",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存章节失败: {str(e)}")


@router.get("/education/list-chapters")
async def list_chapters(course_id: Optional[str] = None):
    try:
        return {
            "success": True,
            "chapters": [_chapter_list_item(chapter) for chapter in chapter_store.list_chapters(course_id)],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取章节列表失败: {str(e)}")


@router.get("/education/get-chapter")
async def get_chapter(chapter_id: str, include_assets: bool = False, compact_strings: bool = False):
    try:
        chapter = chapter_store.get_chapter(chapter_id)
        if not chapter:
            return {"success": False, "error": "章节不存在"}
        cleaned_bank = _normalize_exercise_bank(chapter.get("exercise_bank") or chapter.get("exercises"))
        approved_bank = _normalize_exercise_bank(chapter.get("approved_exercise_bank"))
        chapter = dict(chapter)
        chapter["exercise_bank"] = cleaned_bank
        chapter["approved_exercise_bank"] = approved_bank
        chapter["exercises"] = cleaned_bank[0] if cleaned_bank else None
        if not include_assets and chapter.get("rendered_pages"):
            content = str(chapter.get("content") or "")
            chapter["content"] = content[:240]
            chapter["content_preview"] = content[:240]
            chapter.pop("tex_content", None)
            chapter.pop("graph_data", None)
            compact_slides = []
            for slide in chapter.get("ppt_slides") or []:
                if not isinstance(slide, dict):
                    continue
                compact_slides.append(
                    {
                        "index": slide.get("index"),
                        "title": slide.get("title"),
                        "content": slide.get("content"),
                        "raw_text": slide.get("raw_text"),
                        "notes": slide.get("notes"),
                    }
                )
            chapter["ppt_slides"] = compact_slides
            chapter.pop("editable_model", None)
            chapter.pop("asset_map", None)
            chapter.pop("ppt_artifact", None)
        response = {"success": True, "chapter": chapter}
        return pack_courseware(response) if compact_strings else response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取章节数据失败: {str(e)}")


@router.get("/education/rendered-pages/{namespace}/{filename}")
async def get_rendered_page_image(namespace: str, filename: str):
    safe_namespace = _safe_render_namespace(namespace)
    safe_filename = Path(filename).name
    if safe_namespace != namespace or not re.fullmatch(r"page_\d+\.(?:png|jpg|jpeg)", safe_filename, re.I):
        raise HTTPException(status_code=404, detail="图片不存在")
    path = (RENDERED_PAGE_DIR / safe_namespace / safe_filename).resolve()
    try:
        path.relative_to(RENDERED_PAGE_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="图片不存在") from None
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    media_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return FileResponse(path, media_type=media_type)


@router.delete("/education/delete-chapter")
async def delete_chapter(chapter_id: str):
    try:
        existing = chapter_store.get_chapter(chapter_id) or {}
        result = chapter_store.delete_chapter(chapter_id)
        if not result.get("success"):
            return {"success": False, "error": "Chapter not found", "chapter_id": chapter_id}
        result["tts_cache"] = clear_course_tts_cache(chapter_id)
        _cleanup_rendered_pages(existing.get("rendered_pages"))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete chapter failed: {str(e)}")


@router.post("/education/save-lecture")
async def save_lecture(request: SaveLectureRequest):
    try:
        existing_chapter = chapter_store.get_chapter(request.chapter_id) or {}
        source_node_ids = _normalize_source_node_ids(None, request.source_node_ids)
        cleaned_content = clean_generated_lecture_output(request.lecture_content)
        cleaned_slide_lectures = request.slide_lectures
        if isinstance(request.slide_lectures, list):
            cleaned_slide_lectures = [
                {**item, "lecture": clean_generated_lecture_output(item.get("lecture") or "")}
                if isinstance(item, dict) else item
                for item in request.slide_lectures
            ]
        learning_plan = request.learning_plan if request.learning_plan is not None else existing_chapter.get("lecture_learning_plan")
        if not isinstance(learning_plan, dict) and isinstance(request.slide_lectures, list):
            plans = [item.get("learning_plan") for item in request.slide_lectures if isinstance(item, dict) and isinstance(item.get("learning_plan"), dict)]
            evidence = []
            for plan in plans:
                evidence.extend(plan.get("evidence") or [])
            if plans:
                learning_plan = {**plans[0], "evidence": evidence}
        consistency_report = request.consistency_report if request.consistency_report is not None else None
        if consistency_report is None and isinstance(learning_plan, dict):
            consistency_report = _safe_consistency_report(cleaned_content, learning_plan, task="lecture")
        chapter = chapter_store.save_lecture(
            chapter_id=request.chapter_id,
            course_id=request.course_id,
            lecture_content=cleaned_content,
            graph_data=request.graph_data,
            source_type=request.source_type,
            source_node_ids=source_node_ids or request.source_node_ids,
            source_scope=request.source_scope,
            ppt_slides=request.ppt_slides,
            slide_lectures=cleaned_slide_lectures,
            tex_content=request.tex_content,
            editable_model=request.editable_model,
            asset_map=request.asset_map,
            rendered_pages=request.rendered_pages,
            render_source=request.render_source,
            render_error=request.render_error,
            ppt_artifact=request.ppt_artifact,
            ppt_source_node_ids=request.ppt_source_node_ids,
            lecture_source_node_ids=request.lecture_source_node_ids,
            lecture_target_duration_minutes=request.lecture_target_duration_minutes,
            lecture_speech_rate_cpm=request.lecture_speech_rate_cpm,
            lecture_pacing=request.lecture_pacing,
            learning_plan=learning_plan if isinstance(learning_plan, dict) else None,
            consistency_report=consistency_report if isinstance(consistency_report, dict) else None,
        )
        # Text changes invalidate chapter audio; remove old segments so the
        # next playback cannot retain stale audio files on the Azure VM.
        cache_cleanup = clear_course_tts_cache(request.chapter_id)
        return {
            "success": True,
            "chapter": chapter,
            "tts_cache": cache_cleanup,
            "message": "授课文案保存成功",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存授课文案失败: {str(e)}")


@router.post("/education/upload-ppt")
async def upload_ppt(
    file: UploadFile = File(...),
    style: str = Form("引导式教学"),
    target_duration_minutes: Optional[float] = Form(None),
    speech_rate_cpm: Optional[int] = Form(None),
    api_key: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    source_node_id: Optional[str] = Form(None),
    source_node_ids: Optional[List[str]] = Form(None),
    graph_scope: Optional[str] = Form(None),
    teacher_guidance: Optional[str] = Form(None),
    course_id: Optional[str] = Form(None),
    teacher_profile_id: Optional[str] = Form(None),
    allow_no_node: bool = Form(False),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    if not file.filename.lower().endswith(tuple(SUPPORTED_COURSEWARE_EXTENSIONS)):
        raise HTTPException(status_code=400, detail=f"仅支持 {SUPPORTED_COURSEWARE_FORMATS_TEXT} 格式的课件文件")

    try:
        file_bytes = await file.read()
        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail="上传的文件为空")

        from KGTS.education.ppt_parser import parse_courseware, build_ppt_lecture_prompt_data

        parse_result = parse_courseware(file_bytes, file.filename)
        if not parse_result.get("success"):
            raise HTTPException(status_code=400, detail=parse_result.get("error", "课件解析失败"))

        prompt_data = build_ppt_lecture_prompt_data(parse_result)
        editable_model = build_editable_model(parse_result, prompt_data)
        image_warning = _courseware_image_warning(parse_result)

        chapter_title = prompt_data["chapter_title"]
        effective_teacher_guidance, teacher_profile = _resolve_teacher_guidance(
            teacher_guidance,
            course_id=course_id,
            title=chapter_title,
            profile_id=teacher_profile_id,
        )
        chapter_content = prompt_data["chapter_content"]
        slide_details = prompt_data["slide_details"]
        normalized_source_node_ids = _normalize_source_node_ids(source_node_id, source_node_ids)
        auto_selected_nodes = False
        if not normalized_source_node_ids and not allow_no_node:
            upload_query = "\n".join(
                [chapter_title, chapter_content[:2200]]
                + [str(slide.get("raw_text") or slide.get("content") or slide.get("title") or "")[:500] for slide in slide_details[:8]]
            )
            normalized_source_node_ids = await _auto_select_source_node_ids(upload_query, api_key)
            auto_selected_nodes = bool(normalized_source_node_ids)
        selected_context = None
        graph_data = None
        source_scope = None
        graph_context_content = ""
        selected_evidence: List[Dict[str, Any]] = []

        if normalized_source_node_ids:
            selected_context = build_node_contexts(normalized_source_node_ids)
            if not selected_context.get("success"):
                raise HTTPException(status_code=404, detail=selected_context.get("error") or "Graph node not found")
            graph_data = selected_context.get("graph_data")
            source_scope = selected_context.get("scope")
            graph_context_content = str(selected_context.get("chapter_content") or "").strip()
            selected_evidence = selected_context.get("evidence") or []

        if not chapter_content.strip():
            pacing = _build_slide_lecture_pacing(
                slide_details,
                target_duration_minutes=target_duration_minutes,
                speech_rate_cpm=speech_rate_cpm,
            )
            slide_lectures = [
                {
                    "index": slide.get("index"),
                    "title": slide.get("title", ""),
                    "lecture": "",
                    "skipped": True,
                    **(pacing.get("slides", {}).get(slide.get("index")) or {}),
                    "estimated_chars": 0,
                    "estimated_duration_seconds": 0,
                }
                for slide in slide_details
            ]
            payload = {
                "success": True,
                "chapter_title": chapter_title,
                "slide_count": prompt_data["total_slides"],
                "slides": slide_details,
                "full_text": chapter_content,
                "tex_content": parse_result.get("tex_content") or "",
                "tex_source_file": parse_result.get("tex_source_file") or "",
                "editable_model": editable_model,
                "asset_map": editable_model.get("assets") or {},
                "layout": editable_model.get("layout") or {},
                "source_tex": parse_result.get("tex_content") or "",
                "missing_image_refs": parse_result.get("missing_image_refs") or [],
                "lecture_content": "",
                "slide_lectures": slide_lectures,
                "lecture_pacing": _summarize_slide_lecture_pacing(slide_lectures, pacing),
                "teacher_profile_id": (teacher_profile or {}).get("profile_id") if teacher_profile else None,
                "auto_selected_nodes": auto_selected_nodes,
                "warning": image_warning or "课件中未提取到有效文本内容，可能是纯图片或扫描件",
            }
            if normalized_source_node_ids:
                payload.update(
                    {
                        "source_node_id": normalized_source_node_ids[0],
                        "source_node_ids": normalized_source_node_ids,
                        "source_scope": source_scope,
                    }
                )
            return payload

        if graph_data is None:
            try:
                graph_data = await call_mcp_tool("read_graph")
                if isinstance(graph_data, dict):
                    graph_data = build_frontend_graph(graph_data)
            except Exception:
                try:
                    graph_data = build_frontend_graph()
                except Exception:
                    graph_data = None

        claude_client = DeepSeekAPIClient(
            api_key=api_key,
            model=model or get_deepseek_model("pro"),
        )
        pacing = await _build_slide_lecture_pacing_with_model(
            claude_client,
            slide_details,
            chapter_title=chapter_title,
            style=style,
            target_duration_minutes=target_duration_minutes,
            speech_rate_cpm=speech_rate_cpm,
            teacher_guidance=effective_teacher_guidance,
        )

        slide_lectures = await _generate_per_slide_lectures(
            claude_client,
            slide_details,
            style,
            chapter_title,
            graph_data if isinstance(graph_data, dict) else None,
            selected_evidence=selected_evidence,
            selected_graph_context=graph_context_content,
            source_node_ids=normalized_source_node_ids or None,
            teacher_guidance=effective_teacher_guidance,
            slide_feedback={},
            pacing_by_index=pacing["slides"],
            speech_rate_cpm=pacing["speech_rate_cpm"],
        )
        if _nonempty_slide_lecture_count(slide_lectures) == 0 and any(_compact_slide_for_lecture(slide).strip() for slide in slide_details):
            message = _slide_lecture_error_summary(slide_lectures) or "AI 未返回任何逐页讲解内容，请检查 DeepSeek 配置、模型返回或稍后重试。"
            return _compact_slide_lecture_response({
                "success": False,
                "error": "slide_lecture_generation_empty",
                "message": message,
                "chapter_title": chapter_title,
                "slide_count": prompt_data["total_slides"],
                "slides": slide_details,
                "full_text": chapter_content,
                "tex_content": parse_result.get("tex_content") or "",
                "tex_source_file": parse_result.get("tex_source_file") or "",
                "editable_model": editable_model,
                "asset_map": editable_model.get("assets") or {},
                "layout": editable_model.get("layout") or {},
                "source_tex": parse_result.get("tex_content") or "",
                "missing_image_refs": parse_result.get("missing_image_refs") or [],
                "lecture_content": "",
                "slide_lectures": slide_lectures,
                "source_node_id": normalized_source_node_ids[0] if normalized_source_node_ids else None,
                "source_node_ids": normalized_source_node_ids,
                "source_scope": source_scope,
                "teacher_profile_id": (teacher_profile or {}).get("profile_id") if teacher_profile else None,
                "model": claude_client.model,
                "generated_at": datetime.now().isoformat(),
            })
        lecture_content = _merge_slide_lectures(slide_lectures)
        try:
            lecture_learning_plan = _build_ppt_learning_plan(
                chapter_title=chapter_title,
                chapter_content=_truncate_for_prompt(chapter_content, 1800),
                graph_data=graph_data if isinstance(graph_data, dict) else None,
                selected_evidence=_compact_evidence_for_prompt(selected_evidence, limit=4, content_chars=260),
            )
        except Exception:
            lecture_learning_plan = build_learning_plan(
                query=chapter_title,
                evidence=_compact_evidence_for_prompt(selected_evidence, limit=4, content_chars=260),
                learner_intent="explain",
                learning_level="beginner",
                task="lecture",
                chapter_data={"title": chapter_title, "content": _truncate_for_prompt(lecture_content, 1200)},
            )
        lecture_graph_paths = graph_paths_for_evidence(
            graph_data if isinstance(graph_data, dict) else None,
            lecture_learning_plan.get("evidence") or [],
            limit=4,
        )
        lecture_formula_context = formula_context_for_text(chapter_content[:1800], limit=4)
        lecture_consistency_report = _safe_consistency_report(lecture_content, lecture_learning_plan, task="lecture")

        return _compact_slide_lecture_response({
            "success": True,
            "chapter_title": chapter_title,
            "slide_count": prompt_data["total_slides"],
            "slides": slide_details,
            "full_text": chapter_content,
            "tex_content": parse_result.get("tex_content") or "",
            "tex_source_file": parse_result.get("tex_source_file") or "",
            "editable_model": editable_model,
            "asset_map": editable_model.get("assets") or {},
            "layout": editable_model.get("layout") or {},
            "source_tex": parse_result.get("tex_content") or "",
            "missing_image_refs": parse_result.get("missing_image_refs") or [],
            "lecture_content": lecture_content,
            "slide_lectures": slide_lectures,
            "lecture_pacing": _summarize_slide_lecture_pacing(slide_lectures, pacing),
            "learning_plan": lecture_learning_plan,
            "graph_paths": lecture_graph_paths,
            "formula_context": lecture_formula_context,
            "consistency_report": lecture_consistency_report,
            "source_node_id": normalized_source_node_ids[0] if normalized_source_node_ids else None,
            "source_node_ids": normalized_source_node_ids,
            "source_scope": source_scope,
            "style": style,
            "teacher_profile_id": (teacher_profile or {}).get("profile_id") if teacher_profile else None,
            "auto_selected_nodes": auto_selected_nodes,
            "model": claude_client.model,
            "generated_at": datetime.now().isoformat(),
            **({"warning": image_warning} if image_warning else {}),
        })

    except HTTPException:
        raise
    except ValueError as e:
        if "API" in str(e).upper():
            return {
                "success": False,
                "error": "DeepSeek API is not configured",
                "message": "Please configure a DeepSeek API key in settings or DEEPSEEK_API_KEY.",
            }
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"课件处理失败: {str(e)}")


async def _generate_per_slide_lectures(
    client: DeepSeekAPIClient,
    slide_details: List[Dict],
    style: str,
    chapter_title: str,
    graph_data: Optional[Dict[str, Any]] = None,
    selected_evidence: Optional[List[Dict[str, Any]]] = None,
    selected_graph_context: str = "",
    source_node_ids: Optional[List[str]] = None,
    teacher_guidance: str = "",
    slide_feedback: Optional[Dict[int, str]] = None,
    style_reference_guidance: str = "",
    target_slide_indices: Optional[List[int]] = None,
    pacing_by_index: Optional[Dict[int, Dict[str, Any]]] = None,
    speech_rate_cpm: int = DEFAULT_SLIDE_LECTURE_SPEECH_RATE_CPM,
    progress: Optional[Callable[[str, str], None]] = None,
) -> List[Dict]:
    def mark(stage: str, message: str) -> None:
        if progress:
            progress(stage, message)

    results_by_index: Dict[int, Dict[str, Any]] = {}
    work_items: List[Dict[str, Any]] = []
    target_set = set(target_slide_indices or [])
    speech_rate_cpm = _normalize_slide_lecture_speech_rate_cpm(speech_rate_cpm)
    for slide in slide_details:
        slide_index = slide.get("index")
        if target_set and slide_index not in target_set:
            continue
        pacing = (pacing_by_index or {}).get(int(slide_index)) if isinstance(slide_index, int) else None
        slide_text = _compact_slide_for_lecture(slide, max_chars=1200)
        if not slide_text.strip():
            learning_plan = _fallback_slide_learning_plan(
                chapter_title=chapter_title,
                slide=slide,
                slide_text="",
            )
            if isinstance(slide_index, int):
                results_by_index[slide_index] = _attach_slide_lecture_timing(
                    {
                        "index": slide["index"],
                        "title": slide.get("title", ""),
                        "lecture": "",
                        "skipped": True,
                        "source_node_ids": [],
                        "learning_plan": learning_plan,
                        "sources": [],
                        "graph_paths": [],
                        "formula_context": [],
                        "generation_status": "skipped_empty_slide",
                    },
                    pacing,
                    speech_rate_cpm,
                )
            continue

        chapter_data = {
            "title": f"{chapter_title} - 第 {slide['index']} 页",
            "content": (
                f"Selected graph subtree context:\n{_truncate_for_prompt(selected_graph_context, 900)}\n\nPPT slide content:\n{slide_text}"
                if selected_graph_context
                else slide_text
            ),
        }
        slide_graphrag_context = None
        slide_graph_data = graph_data
        slide_selected_evidence = _select_slide_evidence_for_prompt(selected_evidence, slide_text, limit=4, content_chars=260)
        if source_node_ids and not selected_evidence:
            try:
                slide_graphrag_context = build_graphrag_context(
                    f"{chapter_title}\n{slide_text[:700]}",
                    seed_node_ids=source_node_ids,
                    limit=4,
                )
                slide_graph_data = slide_graphrag_context.get("graph_data") or graph_data
                slide_selected_evidence = _select_slide_evidence_for_prompt(
                    evidence_from_rag(slide_graphrag_context.get("llm_context") or [], limit=6),
                    slide_text,
                    limit=4,
                    content_chars=260,
                )
                if slide_graphrag_context.get("context"):
                    chapter_data["content"] = (
                        f"GraphRAG scoped context:\n{_truncate_for_prompt(_format_graphrag_generation_context(slide_graphrag_context), 1000)}\n\n"
                        f"PPT slide content:\n{slide_text}"
                    )
            except Exception:
                slide_graphrag_context = None
        elif selected_graph_context:
            slide_graphrag_context = None

        try:
            learning_plan = _build_ppt_learning_plan(
                chapter_title=chapter_title,
                chapter_content=slide_text,
                graph_data=slide_graph_data,
                chapter_data=chapter_data,
                query=f"{chapter_title}\n{slide_text[:650]}",
                selected_evidence=slide_selected_evidence,
            )
        except Exception:
            learning_plan = _fallback_slide_learning_plan(
                chapter_title=chapter_title,
                slide=slide,
                slide_text=slide_text,
                evidence=slide_selected_evidence,
            )
        sources = learning_plan.get("evidence") or []
        if not sources:
            try:
                rag = build_rag_context(
                    f"{chapter_title}\n{slide_text[:650]}",
                    limit=3,
                    seed_node_ids=source_node_ids,
                )
                sources = rag.get("llm_context") or []
                learning_plan = build_learning_plan(
                    query=f"{chapter_title}\n{slide_text[:650]}",
                    evidence=_compact_evidence_for_prompt(evidence_from_rag(sources, limit=3), limit=3, content_chars=240),
                    learner_intent="explain",
                    learning_level="beginner",
                    task="lecture",
                    chapter_data=chapter_data,
                )
            except Exception:
                sources = []
        prompt_evidence = _compact_evidence_for_prompt(learning_plan.get("evidence") or [], limit=4, content_chars=260)
        prompt_evidence = _select_slide_evidence_for_prompt(prompt_evidence, slide_text, limit=4, content_chars=260)
        source_evidence_for_slide = _select_slide_source_evidence(prompt_evidence, slide_text, limit=4, content_chars=260)
        graph_paths = graph_paths_for_evidence(slide_graph_data, prompt_evidence, limit=4)[:4]
        formula_context = ((slide_graphrag_context or {}).get("formula_context") or formula_context_for_text(slide_text, limit=4))[:4]
        transition_context = _build_slide_transition_context(slide_details, slide.get("index"))
        visible_elements = _format_slide_visible_elements_for_prompt(slide)
        page_feedback = _slide_feedback_for_index(slide_feedback, slide.get("index"))
        sources = prompt_evidence
        slide_source_node_ids = _source_node_ids_from_evidence(source_evidence_for_slide)

        requirements = [
            *build_lecture_gc_dpg_requirements(style, slide_level=True),
            "Use only content visible on this slide as the primary teaching object: its title, bullet text, formulas, tables, and images/figure labels. Do not teach figures, examples, or graph entities that are not visible on the current slide.",
            "If you mention a figure such as Figure 26.5, it must appear in the current slide text or current slide image metadata. Otherwise do not mention it.",
            "Start with a concise overview of this slide's topic. When helpful, add one sentence explaining how the previous slide leads into this slide.",
            "When the slide is a bullet list, first restate the visible bullet points in your own classroom wording, then explain each point. Do not skip bullet restatement.",
            "Explain this slide in accessible classroom language.",
            "Expand important biological and mathematical concepts enough to make the bullet points clear, using the expansion as a brief review of prior knowledge, but keep the expansion anchored to this slide.",
            "Use the graph relation paths as the main teaching order when they are relevant.",
            "If formulas appear, explain the meaning of the main symbols.",
            "When the same symbol can mean different things elsewhere, explain only the meaning in this slide/chapter scope.",
            "If a formula is derived from earlier formulas, mention the immediate derivation dependency in teacher-friendly language.",
            "Use the neighboring-slide context only to add one natural transition sentence when helpful; do not teach the previous or next slide here.",
            "If you ask a classroom question, answer it immediately after asking. Keep the question supportive rather than requiring students to solve a new open-ended problem alone.",
            "Output directly usable Markdown prose for this slide. Do not output analysis, reasoning, planning notes, entity selection notes, or comments about the prompt.",
        ]
        if pacing:
            target_chars = int(pacing.get("target_chars") or 0)
            target_seconds = int(pacing.get("target_duration_seconds") or 0)
            if target_chars > 0:
                requirements.append(
                    f"Target pacing for this slide: about {target_chars} Chinese characters, approximately {max(1, round(target_seconds / 60, 1))} minutes at {speech_rate_cpm} characters per minute. "
                    "Stay close to this budget with natural classroom prose; do not pad with empty transitions and do not remove required factual explanations just to hit the number exactly."
                )
        if teacher_guidance:
            requirements.append(
                "Teacher guidance for emphasis, selection, and pacing. Treat it as generation guidance only; it must not override source/graph facts:\n"
                + _truncate_for_prompt(teacher_guidance, 1800)
            )
        if page_feedback:
            requirements.append(
                "Specific feedback for regenerating this slide. Follow it for this slide only, while preserving the visible-slide evidence boundary:\n"
                + _truncate_for_prompt(page_feedback, 900)
            )
        if style_reference_guidance:
            requirements.append(
                "Reference courseware style guidance. Use it for pacing, visual-language-aware wording, and classroom tone only; do not copy reference facts, dates, authors, logos, or figures:\n"
                + _truncate_for_prompt(style_reference_guidance, 800)
            )
        requirement_text = "\n".join(
            f"{index}. {item}" for index, item in enumerate(requirements, start=1)
        )
        selected_context_text = ""
        if selected_graph_context.strip():
            selected_context_text = f"""
Selected graph subtree context:
{_truncate_for_prompt(selected_graph_context, 900)}
"""
        prompt = f"""You are a teacher. Generate the lecture script for one PPT slide.

Chapter topic: {chapter_title}
Current slide: {slide['index']}
{selected_context_text}

Slide content:
{slide_text}

Visible slide-only elements:
{visible_elements or "No extra visible element metadata."}

Neighboring-slide context for continuity:
{transition_context or "No neighboring-slide context."}

Available graph/RAG evidence for this slide:
{format_evidence(prompt_evidence)}

Graph relation paths to use when teaching:
{format_graph_paths(graph_paths)}

Formula derivation and scoped symbol context:
{format_formula_context(formula_context)}

Requirements:
{requirement_text}

Output only the final slide lecture script."""

        work_items.append({
            "slide": slide,
            "slide_text": slide_text,
            "prompt": prompt,
            "pacing": pacing,
            "sources": sources,
            "source_node_ids": slide_source_node_ids,
            "retrieval_mode": (slide_graphrag_context or {}).get("retrieval_mode"),
            "retrieval_stats": (slide_graphrag_context or {}).get("retrieval_stats"),
            "graphrag_context": slide_graphrag_context,
            "vector_hits": (slide_graphrag_context or {}).get("vector_hits"),
            "graph_paths": graph_paths,
            "formula_context": formula_context,
            "learning_plan": learning_plan,
        })

    async def run_items(
        items: List[Dict[str, Any]],
        item_client: DeepSeekAPIClient,
        *,
        phase: str,
        attempt: int,
    ) -> List[tuple[Dict[str, Any], Dict[str, Any]]]:
        semaphore = asyncio.Semaphore(_slide_lecture_concurrency())

        async def run_one(item: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
            async with semaphore:
                slide = item.get("slide") or {}
                mark("generating", f"正在生成第 {slide.get('index', '?')} 页讲解（{phase}）")
                result = await _try_generate_slide_lecture_item(
                    item_client,
                    item,
                    speech_rate_cpm=speech_rate_cpm,
                    phase=phase,
                    attempt=attempt,
                )
                if result.get("lecture"):
                    mark("generating", f"第 {slide.get('index', '?')} 页讲解已生成")
                else:
                    mark("retrying", f"第 {slide.get('index', '?')} 页讲解生成未成功，准备重试")
                return item, result

        return await asyncio.gather(*(run_one(item) for item in items))

    failed_items: List[Dict[str, Any]] = []
    for item, result in await run_items(work_items, client, phase="initial", attempt=1):
        if result.get("lecture"):
            results_by_index[int(item["slide"]["index"])] = result
        else:
            item["last_error"] = result.get("error") or "AI 返回为空"
            failed_items.append(item)

    for attempt in range(1, 4):
        if not failed_items:
            break
        retry_items = failed_items
        failed_items = []
        for item, result in await run_items(retry_items, client, phase="pro_retry", attempt=attempt):
            if result.get("lecture"):
                results_by_index[int(item["slide"]["index"])] = result
            else:
                item["last_error"] = result.get("error") or item.get("last_error") or "AI 返回为空"
                failed_items.append(item)

    if failed_items:
        flash_client = DeepSeekAPIClient(api_key=client.api_key, model=get_deepseek_model("flash"))
        retry_items = failed_items
        failed_items = []
        for item, result in await run_items(retry_items, flash_client, phase="flash_fallback", attempt=1):
            if result.get("lecture"):
                results_by_index[int(item["slide"]["index"])] = result
            else:
                item["last_error"] = result.get("error") or item.get("last_error") or "AI 返回为空"
                failed_items.append(item)

    if results_by_index:
        flash_client = DeepSeekAPIClient(api_key=client.api_key, model=get_deepseek_model("flash"))
        for item in work_items:
            slide_index = int(item["slide"]["index"])
            result = results_by_index.get(slide_index)
            if not result or result.get("generation_status") == "fallback":
                continue
            if not _slide_lecture_needs_flash_completion(str(result.get("lecture") or ""), item.get("pacing")):
                continue
            completed = await _complete_short_slide_lecture_with_flash(
                flash_client,
                item,
                result,
                speech_rate_cpm=speech_rate_cpm,
            )
            results_by_index[slide_index] = completed

    for item in failed_items:
        slide = item["slide"]
        slide_text = item["slide_text"]
        lecture = _fallback_slide_lecture_text(
            chapter_title=chapter_title,
            slide=slide,
            slide_text=slide_text,
        )
        error = str(item.get("last_error") or "AI 生成失败，已使用兜底讲解").strip()
        results_by_index[int(slide["index"])] = _finalize_slide_lecture_result(
            item,
            lecture=lecture,
            speech_rate_cpm=speech_rate_cpm,
            error=error,
            generation_model="fallback",
            generation_status="fallback",
            generation_attempts=5,
        )

    ordered: List[Dict[str, Any]] = []
    for slide in slide_details:
        slide_index = slide.get("index")
        if target_set and slide_index not in target_set:
            continue
        if isinstance(slide_index, int) and slide_index in results_by_index:
            ordered.append(results_by_index[slide_index])
    return ordered


async def _try_generate_slide_lecture_item(
    client: DeepSeekAPIClient,
    item: Dict[str, Any],
    *,
    speech_rate_cpm: int,
    phase: str,
    attempt: int,
) -> Dict[str, Any]:
    pacing = item.get("pacing") if isinstance(item.get("pacing"), dict) else {}
    prompt = str(item.get("prompt") or "")
    last_error = str(item.get("last_error") or "").strip()
    retry_note = ""
    if phase == "pro_retry":
        retry_note = (
            f"\n\n上一次第 {attempt} 轮重试前的失败信息：{_truncate_for_prompt(last_error, 260)}\n"
            "请重新生成完整的本页讲课文案，只输出最终文案。"
        )
    elif phase == "flash_fallback":
        retry_note = (
            f"\n\n前序 deepseek-v4-pro 多轮生成失败：{_truncate_for_prompt(last_error, 260)}\n"
            "请用更稳健的方式生成完整的本页讲课文案，只输出最终文案。"
        )
    try:
        lecture = expand_formula_references(
            await client._call_deepseek(
                prompt + retry_note,
                max_tokens=min(
                    _slide_lecture_max_output_tokens(),
                    max(1800, int(pacing.get("target_chars") or 700) * 5),
                ),
                system_prompt=KG_CONSTRAINED_SYSTEM_PROMPT,
                read_timeout_seconds=_slide_lecture_read_timeout(phase),
            ),
            expand_labels=True,
        )
        lecture = clean_generated_lecture_output(lecture)
        if not lecture.strip():
            raise ValueError("AI 返回为空")
        if _looks_like_reasoning_contaminated_lecture(lecture):
            raise ValueError("AI 返回疑似思考过程而非逐页文案")
        return _finalize_slide_lecture_result(
            item,
            lecture=lecture,
            speech_rate_cpm=speech_rate_cpm,
            error="",
            generation_model=client.model,
            generation_status=phase,
            generation_attempts=_generation_attempt_count(phase, attempt),
        )
    except Exception as exc:
        return {
            "index": item.get("slide", {}).get("index"),
            "title": item.get("slide", {}).get("title", ""),
            "lecture": "",
            "skipped": True,
            "error": str(exc).strip() or exc.__class__.__name__,
            "generation_model": client.model,
            "generation_status": f"{phase}_failed",
            "generation_attempts": _generation_attempt_count(phase, attempt),
        }


def _generation_attempt_count(phase: str, attempt: int) -> int:
    if phase == "initial":
        return 1
    if phase == "pro_retry":
        return 1 + max(1, attempt)
    if phase == "flash_fallback":
        return 5
    return max(1, attempt)


def _looks_like_reasoning_contaminated_lecture(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    head = value[:1400]
    reasoning_markers = [
        r"\bI\s+need\s+to\b",
        r"\bWe\s+need\s+to\b",
        r"\bthe\s+user\s+(?:wants|asks|requested)\b",
        r"\bI\s+should\b",
        r"\bLet's\s+(?:craft|generate|answer|analyze)\b",
        r"\bNeed\s+to\s+(?:craft|generate|answer|include)\b",
        r"\bfinal\s+answer\b",
        r"\bchain\s+of\s+thought\b",
        r"\breasoning\b",
        r"思考过程",
        r"推理过程",
        r"我需要先",
        r"我会先",
        r"先分析",
    ]
    marker_hits = sum(1 for pattern in reasoning_markers if re.search(pattern, head, flags=re.I))
    ascii_words = re.findall(r"[A-Za-z]{3,}", head)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", head)
    if marker_hits >= 2:
        return True
    if marker_hits >= 1 and len(ascii_words) > max(40, len(cjk_chars) * 2):
        return True
    return False


def _finalize_slide_lecture_result(
    item: Dict[str, Any],
    *,
    lecture: str,
    speech_rate_cpm: int,
    error: str,
    generation_model: str,
    generation_status: str,
    generation_attempts: int,
) -> Dict[str, Any]:
    slide = item["slide"]
    learning_plan = item.get("learning_plan") or {}
    return _attach_slide_lecture_timing({
        "index": slide["index"],
        "title": slide.get("title", ""),
        "lecture": lecture,
        "skipped": not lecture.strip(),
        "error": error,
        "source_node_ids": item.get("source_node_ids") or [],
        "sources": item.get("sources") or [],
        "retrieval_mode": item.get("retrieval_mode"),
        "retrieval_stats": item.get("retrieval_stats"),
        "graphrag_context": item.get("graphrag_context"),
        "vector_hits": item.get("vector_hits"),
        "graph_paths": item.get("graph_paths") or [],
        "formula_context": item.get("formula_context") or [],
        "learning_plan": learning_plan,
        "consistency_report": _safe_consistency_report(lecture, learning_plan, task="lecture"),
        "generation_model": generation_model,
        "generation_status": generation_status,
        "generation_attempts": generation_attempts,
    }, item.get("pacing") if isinstance(item.get("pacing"), dict) else None, speech_rate_cpm)


def _slide_lecture_needs_flash_completion(
    lecture: str,
    pacing: Optional[Dict[str, Any]],
) -> bool:
    if _slide_lecture_has_abrupt_trailing_text(lecture):
        return True
    if not pacing:
        return False
    target_chars = int(pacing.get("target_chars") or 0)
    if target_chars <= 0:
        return False
    estimated_chars = _count_speech_chars(lecture)
    if target_chars < 160:
        return estimated_chars < max(45, int(target_chars * 0.55))
    return estimated_chars < max(100, int(target_chars * 0.72))


def _slide_lecture_has_abrupt_trailing_text(lecture: str) -> bool:
    text = re.sub(r"\s+", " ", str(lecture or "")).strip()
    if not text:
        return True
    tail = text[-80:].strip()
    if tail.endswith(("，", "、", "：", ":", ";", "；", ",", "、", "(", "（")):
        return True
    abrupt_markers = (
        "例如",
        "比如",
        "包括",
        "如下",
        "首先",
        "然后",
        "接下来",
        "也就是说",
        "这说明",
        "这里要注意",
        "我们可以看到",
        "可以看到",
        "如果",
        "当",
        "因为",
        "因此",
        "所以",
        "分别是",
    )
    return tail.endswith(abrupt_markers)


async def _complete_short_slide_lecture_with_flash(
    flash_client: DeepSeekAPIClient,
    item: Dict[str, Any],
    result: Dict[str, Any],
    *,
    speech_rate_cpm: int,
) -> Dict[str, Any]:
    pacing = item.get("pacing") if isinstance(item.get("pacing"), dict) else {}
    target_chars = int(pacing.get("target_chars") or 0)
    current_chars = _count_speech_chars(str(result.get("lecture") or ""))
    gap = max(80, target_chars - current_chars)
    prompt = f"""请补全一页 PPT 的讲课文案，使其更接近目标字数，并修复可能被截断的结尾。

页面内容：
{item.get("slide_text") or ""}

当前页可见元素：
{_format_slide_visible_elements_for_prompt(item.get("slide") or {}) or "无额外可见元素元数据。"}

当前讲稿：
{result.get("lecture") or ""}

目标：补充约 {gap} 个中文字符。补充内容必须自然衔接、可直接朗读，并围绕本页内容展开。
限制：
1. 不要重复当前讲稿已有句子。
2. 只围绕当前页可见标题、正文、公式、表格和图片展开；不要引入当前页没有出现的图、例子或新事实。
3. 只输出要追加的补全文案，不要输出标题、说明或 JSON。"""
    try:
        addition = expand_formula_references(
            await flash_client._call_deepseek(
                prompt,
                max_tokens=min(3000, max(600, gap * 3)),
                system_prompt=KG_CONSTRAINED_SYSTEM_PROMPT,
                read_timeout_seconds=_slide_lecture_completion_timeout(),
            ),
            expand_labels=True,
        )
        addition = clean_generated_lecture_output(addition)
        if not addition.strip():
            raise ValueError("flash 补全返回为空")
        if _looks_like_reasoning_contaminated_lecture(addition):
            raise ValueError("flash 补全返回疑似思考过程")
        lecture = f"{str(result.get('lecture') or '').rstrip()}\n\n{addition.strip()}"
        completed = _finalize_slide_lecture_result(
            item,
            lecture=lecture,
            speech_rate_cpm=speech_rate_cpm,
            error=str(result.get("error") or ""),
            generation_model=str(result.get("generation_model") or ""),
            generation_status=f"{result.get('generation_status') or 'generated'}+flash_completion",
            generation_attempts=int(result.get("generation_attempts") or 1),
        )
        completed["completion_model"] = flash_client.model
        completed["completion_added_chars"] = _count_speech_chars(addition)
        return completed
    except Exception as exc:
        result = dict(result)
        warning = str(exc).strip() or exc.__class__.__name__
        result["completion_model"] = flash_client.model
        result["completion_error"] = warning
        result["warning"] = f"字数不足，flash 补全失败：{warning}"
        return result


def _build_ppt_learning_plan(
    *,
    chapter_title: str,
    chapter_content: str,
    graph_data: Optional[Dict[str, Any]],
    chapter_data: Optional[Dict[str, Any]] = None,
    query: Optional[str] = None,
    selected_evidence: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    chapter_payload = chapter_data or {"title": chapter_title, "content": chapter_content}
    search_query = query or f"{chapter_title}\n{chapter_content[:1200]}"
    subtree_evidence = [
        item
        for item in (selected_evidence or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    if subtree_evidence:
        matched_graph_evidence = evidence_from_graph(
            graph_data,
            query=search_query,
            chapter_data=chapter_payload,
            limit=8,
        )
        evidence = _dedupe_evidence([*subtree_evidence[:12], *matched_graph_evidence])
    else:
        evidence = evidence_from_graph(
            graph_data,
            query=search_query,
            chapter_data=chapter_payload,
            limit=10,
        )
    if chapter_content.strip():
        slide_evidence = {
            "index": 1,
            "id": f"ppt_slide::{hashlib.md5(search_query.encode('utf-8')).hexdigest()[:10]}",
            "label": str(chapter_payload.get("title") or chapter_title or "PPT slide"),
            "type": "ppt_slide",
            "content": chapter_content[:1800],
            "source": "ppt",
        }
        evidence = _dedupe_evidence([*evidence, slide_evidence] if subtree_evidence else [slide_evidence, *evidence])
    relations = graph_paths_for_evidence(graph_data, evidence, limit=12)
    learning_relations = [
        {
            "source": item.get("source"),
            "target": item.get("target"),
            "type": item.get("type") or "related",
            "metadata": {"description": item.get("description", "")},
        }
        for item in relations
    ]
    return build_learning_plan(
        query=search_query,
        evidence=evidence,
        relations=learning_relations,
        learner_intent="explain",
        learning_level="beginner",
        task="lecture",
        chapter_data=chapter_payload,
    )


def _dedupe_evidence(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in evidence:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        key = item_id or f"{item.get('source')}::{item.get('label')}::{item.get('content')}"
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _format_ppt_generation_context(
    *,
    learning_plan: Dict[str, Any],
    graph_paths: List[Dict[str, Any]],
    formulas: List[Dict[str, Any]],
) -> str:
    return "\n\n".join(
        [
            "Matched graph evidence:\n" + format_evidence(learning_plan.get("evidence") or []),
            "Graph relation paths:\n" + format_graph_paths(graph_paths),
            "Formula derivation and scoped symbol context:\n" + format_formula_context(formulas),
        ]
    )


@router.post("/education/upload-ppt-preview")
async def upload_ppt_preview(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    if not file.filename.lower().endswith(tuple(SUPPORTED_COURSEWARE_EXTENSIONS)):
        raise HTTPException(status_code=400, detail=f"仅支持 {SUPPORTED_COURSEWARE_FORMATS_TEXT} 格式")

    try:
        file_bytes = await file.read()
        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail="上传的文件为空")
        from KGTS.education.ppt_parser import parse_courseware, build_ppt_lecture_prompt_data

        parse_result = parse_courseware(file_bytes, file.filename)
        if not parse_result.get("success"):
            raise HTTPException(status_code=400, detail=parse_result.get("error", "课件解析失败"))

        prompt_data = build_ppt_lecture_prompt_data(parse_result)
        editable_model = build_editable_model(parse_result, prompt_data)
        image_warning = _courseware_image_warning(parse_result)
        if file.filename.lower().endswith(".zip"):
            namespace = hashlib.md5(file_bytes).hexdigest()
            rendered_pages = _load_rendered_page_cache(namespace)
            render_error = ""
            if rendered_pages:
                render_job_id = ""
            else:
                render_job_id = _queue_courseware_render(
                    file_bytes,
                    parse_result.get("tex_source_file") or "",
                    namespace,
                )
        else:
            render_job_id = ""
            rendered_pages, render_error = _render_courseware_pdf_pages(
                parse_result.get("tex_content") or "", editable_model.get("assets") or {}
            )

        return {
            "success": True,
            "chapter_title": prompt_data["chapter_title"],
            "slide_count": prompt_data["total_slides"],
            "slides": prompt_data["slide_details"],
            "full_text": prompt_data["chapter_content"],
            "tex_content": parse_result.get("tex_content") or "",
            "tex_source_file": parse_result.get("tex_source_file") or "",
            "editable_model": editable_model,
            "asset_map": editable_model.get("assets") or {},
            "layout": editable_model.get("layout") or {},
            "source_tex": parse_result.get("tex_content") or "",
            "missing_image_refs": parse_result.get("missing_image_refs") or [],
            "rendered_pages": rendered_pages,
            **({"render_job_id": render_job_id, "render_status": "queued"} if render_job_id else {}),
            **({"render_source": "latex_project" if file.filename.lower().endswith(".zip") else "latex"} if rendered_pages else {}),
            **({"render_error": render_error} if render_error else {}),
            **({
                "warning": "；".join(
                    item for item in (image_warning, render_error) if item
                )
            } if image_warning or render_error else {}),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"课件预览失败: {str(e)}")


@router.post("/education/preview-tex")
async def preview_tex(request: PreviewTexRequest):
    tex_content = str(request.tex_content or "").replace("\r\n", "\n").replace("\r", "\n")
    if not tex_content.strip():
        raise HTTPException(status_code=400, detail="TeX 源码为空")

    try:
        from KGTS.education.ppt_parser import parse_text_courseware, build_ppt_lecture_prompt_data

        parse_result = parse_text_courseware(tex_content.encode("utf-8"), request.filename or "edited.tex")
        if not parse_result.get("success"):
            raise HTTPException(status_code=400, detail=parse_result.get("error", "TeX 解析失败"))

        prompt_data = build_ppt_lecture_prompt_data(parse_result)
        editable_model = build_editable_model(parse_result, prompt_data)
        image_warning = _courseware_image_warning(parse_result)
        # The client retains uploaded images between edits. Parsing edited TeX
        # can omit those assets, so use the supplied map while recompiling.
        render_assets = request.asset_map or editable_model.get("assets") or {}
        rendered_pages, render_error = _render_courseware_pdf_pages(tex_content, render_assets)
        return {
            "success": True,
            "chapter_title": prompt_data["chapter_title"],
            "slide_count": prompt_data["total_slides"],
            "slides": prompt_data["slide_details"],
            "full_text": prompt_data["chapter_content"],
            "tex_content": tex_content,
            "editable_model": editable_model,
            "asset_map": render_assets,
            "layout": editable_model.get("layout") or {},
            "source_tex": tex_content,
            "missing_image_refs": parse_result.get("missing_image_refs") or [],
            "rendered_pages": rendered_pages,
            **({"render_source": "latex"} if rendered_pages else {}),
            **({"render_error": render_error} if render_error else {}),
            **({"warning": image_warning} if image_warning else {}),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TeX 预览失败: {str(e)}")


@router.post("/education/courseware/assets")
async def upload_courseware_assets(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")
    lower_name = file.filename.lower()
    if not lower_name.endswith((".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg")):
        raise HTTPException(status_code=400, detail="仅支持图片文件或 ZIP 图片包")
    try:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="上传的文件为空")
        assets = assets_from_upload(file_bytes, file.filename)
        return {
            "success": True,
            "asset_map": assets,
            "assets": list(assets.values()),
            "asset_count": len(assets),
            "uploaded_at": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图片资源上传失败: {str(e)}")


@router.post("/education/courseware/projects")
async def save_courseware_project_route(request: CoursewareProjectSaveRequest):
    try:
        model = request.editable_model or {}
        tex_content = request.tex_content or (model.get("source_tex") if isinstance(model, dict) else "")
        if not tex_content and model:
            tex_content = serialize_editable_model_to_tex(model, title=request.title)
        if isinstance(model, dict) and tex_content and not model.get("source_tex"):
            model = {**model, "source_tex": tex_content}
        record = save_courseware_project(
            {
                "project_id": request.project_id,
                "course_id": request.course_id,
                "title": request.title,
                "editable_model": model,
                "asset_map": request.asset_map,
                "slides": request.slides,
                "tex_content": tex_content or "",
                "rendered_pages": request.rendered_pages or [],
                "render_source": request.render_source or "",
                "render_error": request.render_error or "",
                "ppt_artifact": request.ppt_artifact,
                "source_node_ids": request.source_node_ids or [],
                "lecture_target_duration_minutes": request.lecture_target_duration_minutes,
                "lecture_speech_rate_cpm": request.lecture_speech_rate_cpm,
                "lecture_pacing": request.lecture_pacing,
            }
        )
        return {"success": True, "project": record, "project_id": record["id"], "message": "课件项目保存成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"课件项目保存失败: {str(e)}")


@router.get("/education/courseware/projects")
async def list_courseware_projects_route(course_id: Optional[str] = None):
    try:
        return {"success": True, "projects": list_courseware_projects(course_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"课件项目列表读取失败: {str(e)}")


@router.get("/education/courseware/projects/{project_id}")
async def load_courseware_project_route(project_id: str, course_id: Optional[str] = None, compact_strings: bool = False):
    try:
        # Keep large JSON parsing and string packing off the event loop.
        record = await asyncio.to_thread(load_courseware_project, project_id, course_id)
        if not record:
            raise HTTPException(status_code=404, detail="课件项目不存在")
        if compact_strings and isinstance(record, dict):
            # The editable model already contains slides and assets. Avoid
            # sending legacy duplicate copies on the first editor request.
            record = dict(record)
            record.pop("slides", None)
            record.pop("asset_map", None)
        response = {"success": True, "project": record}
        return await asyncio.to_thread(pack_courseware, response) if compact_strings else response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"课件项目读取失败: {str(e)}")


@router.delete("/education/courseware/projects/{project_id}")
async def delete_courseware_project_route(project_id: str, course_id: Optional[str] = None):
    try:
        existing = load_courseware_project(project_id, course_id) or {}
        deleted = delete_courseware_project(project_id, course_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="课件项目不存在")
        _cleanup_rendered_pages(existing.get("rendered_pages"))
        return {"success": True, "project_id": project_id, "message": "课件项目已删除"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"课件项目删除失败: {str(e)}")


@router.post("/education/courseware/export-pptx")
async def export_courseware_pptx(request: CoursewareExportPptxRequest):
    try:
        model = request.editable_model or {}
        if not model.get("slides"):
            raise HTTPException(status_code=400, detail="缺少可导出的课件页面")
        title = request.title or str(model.get("title") or "未命名课件")
        artifact = build_pptx_artifact_from_editable_model(
            title,
            model,
            source_node_ids=_normalize_source_node_ids(None, request.source_node_ids),
        )
        return {"success": True, "ppt_artifact": artifact, "artifact": artifact}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PPTX 导出失败: {str(e)}")
