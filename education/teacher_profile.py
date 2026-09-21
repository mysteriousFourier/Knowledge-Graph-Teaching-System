"""Course-scoped teacher pedagogy profiles.

Profiles are compact, human-reviewable JSON files.  They describe teaching
behaviour only; source facts remain in the selected course/graph context.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_DIR = PROJECT_ROOT / "data" / "teacher_profiles"
MAX_PROFILE_GUIDANCE_CHARS = 2600


def _profile_dir() -> Path:
    configured = str(os.getenv("KGTS_TEACHER_PROFILE_DIR") or "").strip()
    path = Path(configured) if configured else DEFAULT_PROFILE_DIR
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _auto_enabled() -> bool:
    return str(os.getenv("KGTS_AUTO_TEACHER_PROFILE", "1")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _safe_token(value: Any) -> str:
    return str(value or "").strip().casefold()


def _load_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(profile_id or "").strip())
    if not safe_id:
        return None
    path = _profile_dir() / f"{safe_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_strings(item)


def _matches(profile: Dict[str, Any], course_id: str, title: str) -> bool:
    course_token = _safe_token(course_id)
    title_token = _safe_token(title)
    profile_ids = {_safe_token(item) for item in _iter_strings(profile.get("course_ids"))}
    if course_token and course_token in profile_ids:
        return True
    keywords = list(_iter_strings(profile.get("title_keywords")))
    return bool(title_token and any(_safe_token(keyword) in title_token for keyword in keywords))


def get_teacher_profile(
    *,
    course_id: str = "",
    title: str = "",
    profile_id: str = "",
) -> Optional[Dict[str, Any]]:
    """Resolve one profile without making profile configuration mandatory."""
    if not _auto_enabled() and not profile_id:
        return None
    if profile_id:
        return _load_profile(profile_id)

    preferred_id = str(os.getenv("KGTS_DEFAULT_TEACHER_PROFILE_ID") or "").strip()
    preferred = _load_profile(preferred_id) if preferred_id else None
    if preferred and _matches(preferred, course_id, title):
        return preferred

    directory = _profile_dir()
    try:
        candidates = sorted(directory.glob("*.json"))
    except OSError:
        return None
    for path in candidates:
        profile = _load_profile(path.stem)
        if profile and _matches(profile, course_id, title):
            return profile
    return None


def format_teacher_profile_guidance(profile: Optional[Dict[str, Any]], max_chars: int = MAX_PROFILE_GUIDANCE_CHARS) -> str:
    """Turn a profile into concise, safe generation instructions."""
    if not isinstance(profile, dict):
        return ""
    lines = [
        "教师授课画像（只约束表达方式，不提供课程事实）：",
    ]
    sections = (
        ("硬性禁止", profile.get("forbidden_patterns")),
        ("风格指纹", profile.get("style_signature")),
        ("成品示例（只模仿表达，不照抄事实）", profile.get("style_exemplars")),
        ("课件到讲稿扩展", profile.get("source_to_speech_expansion")),
        ("知识图谱扩展", profile.get("knowledge_graph_expansion")),
        ("授课结构", profile.get("lesson_structure")),
        ("讲解规则", profile.get("generation_rules")),
        ("语言与例子", profile.get("language_rules")),
        ("开放问题处理", profile.get("open_question_strategy")),
    )
    for label, values in sections:
        items = [item for item in _iter_strings(values) if item]
        if items:
            lines.append(f"{label}：" + "；".join(items))
    return "\n".join(lines).strip()[:max(200, int(max_chars or MAX_PROFILE_GUIDANCE_CHARS))]


def merge_teacher_guidance(
    teacher_guidance: str = "",
    *,
    course_id: str = "",
    title: str = "",
    profile_id: str = "",
) -> tuple[str, Optional[Dict[str, Any]]]:
    profile = get_teacher_profile(course_id=course_id, title=title, profile_id=profile_id)
    profile_guidance = format_teacher_profile_guidance(profile)
    user_guidance = str(teacher_guidance or "").strip()
    if not profile_guidance:
        return user_guidance, profile
    if user_guidance:
        return f"{profile_guidance}\n\n教师本次补充要求：\n{user_guidance}", profile
    return profile_guidance, profile


# These expressions intentionally require a teaching-action cue.  Ordinary
# occurrences of "写" in a conceptual explanation should remain available.
BLACKBOARD_PATTERNS = (
    re.compile(r"(?:黑板|板书|白板|粉笔|擦黑板|看黑板)"),
    re.compile(r"(?:把|将).{0,12}(?:它|这个|横坐标|纵坐标|曲线|箭头|公式|图|圈|点).{0,14}(?:画成|画出|画个|画一下|写成|写出|写个|标出|标上|抄下来|写下来)"),
    re.compile(r"(?:这里|现在|接下来|先).{0,15}(?:我们|我)?(?:写一下|画一下|标一下|抄一下|写一个|画一个|标出|画出|写出)"),
    re.compile(r"(?:我|我们)(?:在(?:图上|这里|黑板上))?(?:画|写|标|抄)(?:个|一个|一下|出|上|下来)"),
    re.compile(r"(?:往|向).{0,12}(?:画成|画出|标出)"),
    re.compile(r"(?:这里|图上|你们看|我们会看到).{0,12}(?:画的是|画成|画个|写的是|标的是)"),
    re.compile(r"(?:横坐标|纵坐标).{0,45}(?:纵坐标|横坐标).{0,35}(?:这样|画|点|线|曲线)"),
)
RECORDING_PATTERNS = (
    re.compile(r"(?:开始录制|不是开始上课|录音|录制一下)"),
)


def is_blackboard_segment(text: str) -> bool:
    value = str(text or "")
    return any(pattern.search(value) for pattern in BLACKBOARD_PATTERNS)


def clean_transcript_segments(segments: Iterable[Dict[str, Any]]) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    """Remove board-action speech while preserving independent explanations."""
    kept: list[Dict[str, Any]] = []
    removed_count = 0
    recording_removed = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        if any(pattern.search(text) for pattern in RECORDING_PATTERNS):
            recording_removed += 1
            continue
        sentences = list(re.split(r"(?<=[。！？!?；;])\s*", text))
        cleaned_sentences = [sentence.strip() for sentence in sentences if sentence.strip() and not is_blackboard_segment(sentence)]
        cleaned_text = "".join(cleaned_sentences).strip()
        if cleaned_text != text:
            removed_count += 1
        if cleaned_text:
            kept.append({**segment, "text": cleaned_text})
    return kept, {
        "removed_blackboard_segments": removed_count,
        "removed_recording_segments": recording_removed,
        "rules": [
            "删除明确描述黑板/板书/白板动作的时间段。",
            "删除写画公式、坐标、箭头等纯板书过程，不删除独立的概念解释。",
            "不把板书动作作为教师风格示例。",
        ],
    }
