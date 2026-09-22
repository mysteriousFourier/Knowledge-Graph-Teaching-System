from __future__ import annotations

import asyncio
import html
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from KGTS.core.tts_service import (
    genie_tts_service,
    get_tts_settings,
    get_tts_status,
    gpt_sovits_local_service,
    is_tts_cache_admin_enabled,
    tts_audio_cache,
    validate_wav_audio_file,
)
from KGTS.core.tts_text import normalize_tts_text
from KGTS.core.tts_text import resolve_genie_tts_language


router = APIRouter(prefix="/api/tts", tags=["tts"])

DEFAULT_SEGMENT_CHARS = 260
DEFAULT_COURSE_JOB_SEGMENT_CHARS = 120
COURSE_AUDIO_DIR = "course"
SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
COURSE_TTS_JOBS: dict[str, dict[str, Any]] = {}
COURSE_TTS_JOB_LOCK = asyncio.Lock()


def _tts_log(message: str) -> None:
    print(f"[tts] {time.strftime('%H:%M:%S')} {message}", flush=True)


class TtsLoadCharacterRequest(BaseModel):
    character_name: str | None = None
    predefined_character: str | None = None
    model_dir: str | None = None
    language: str | None = None


class TtsReferenceAudioRequest(BaseModel):
    character_name: str | None = None
    audio_path: str
    audio_text: str | None = None
    language: str | None = None


class TtsSpeechCue(BaseModel):
    type: str = Field("repeat", description="Speech cue type, currently repeat")
    target_text: str = Field("", description="Exact text span from the lecture to emphasize")
    style: str | None = Field(None, description="Optional cue style")
    priority: int | None = Field(None, description="Optional priority")


class TtsSynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1)
    character_name: str | None = None
    split_sentence: bool = True
    model_dir: str | None = None
    language: str | None = None
    reference_audio_path: str | None = None
    reference_text: str | None = None
    reference_language: str | None = None
    speed_factor: float | None = None
    top_k: int | None = None
    top_p: float | None = None
    temperature: float | None = None
    repetition_penalty: float | None = None
    text_split_method: str | None = None
    chapter_id: str | None = None
    segment_id: str | None = None
    content_hash: str | None = None
    force: bool = False
    speech_cues: list[TtsSpeechCue] | None = None


class TtsSegmentRequest(BaseModel):
    text: str = Field(..., min_length=1)
    language: str | None = None
    max_chars: int | None = Field(default=None, ge=80, le=800)
    speech_cues: list[TtsSpeechCue] | None = None


class TtsCourseSlideRequest(BaseModel):
    slide_index: int
    position: int = Field(..., ge=0)
    text: str = Field(..., min_length=1)
    speech_cues: list[TtsSpeechCue] | None = None


class TtsCourseJobRequest(BaseModel):
    chapter_id: str = Field(..., min_length=1)
    slides: list[TtsCourseSlideRequest] = Field(..., min_length=1)
    max_chars: int = Field(DEFAULT_COURSE_JOB_SEGMENT_CHARS, ge=80, le=800)
    language: str | None = None


class TtsCourseCacheClearRequest(BaseModel):
    chapter_id: str = Field(..., min_length=1)
    segment_id: str | None = None


class TtsUnloadCharacterRequest(BaseModel):
    character_name: str


def _split_tts_text(text: str, *, max_chars: int) -> list[str]:
    normalized = re.sub(r"[ \t\f\v]+", " ", text.replace("\r\n", "\n")).strip()
    if not normalized:
        return []

    def split_oversized(piece: str) -> list[str]:
        if len(piece) <= max_chars:
            return [piece]
        for pattern in (r"(?<=[。！？!?])\s*", r"(?<=[；;])\s*", r"(?<=[，,、:：])\s+|(?<=[，,、:：])"):
            parts = [part.strip() for part in re.split(pattern, piece) if part.strip()]
            if len(parts) > 1 and max(len(part) for part in parts) < len(piece):
                result: list[str] = []
                for part in parts:
                    result.extend(split_oversized(part))
                return result
        return [piece[start : start + max_chars].strip() for start in range(0, len(piece), max_chars) if piece[start : start + max_chars].strip()]

    pieces: list[str] = []
    for paragraph in [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n+", normalized) if part.strip()] or [re.sub(r"\s+", " ", normalized).strip()]:
        pieces.extend(split_oversized(paragraph))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}{piece}" if not current else f"{current} {piece}"
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _long_text_error(length: int, limit: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=(
            f"Text is too long for one TTS request ({length} normalized characters, limit {limit}). "
            "Call /api/tts/segments first and synthesize segments while playing previous audio."
        ),
    )


def _audio_url(path: Path) -> str:
    settings = get_tts_settings()
    try:
        relative = path.resolve().relative_to(settings.output_dir.resolve())
        return "/api/tts/audio/" + "/".join(relative.parts)
    except ValueError:
        return f"/api/tts/audio/{path.name}"


def _safe_id(value: str | None, fallback: str) -> str:
    text = (value or "").strip()[:120]
    safe = SAFE_ID_RE.sub("-", text).strip(".-")
    return safe or fallback


def _stable_text_hash(text: str) -> str:
    # Match the frontend FNV-1a hash over JavaScript charCodeAt values.
    hash_value = 2166136261
    encoded = text.encode("utf-16-le", errors="surrogatepass")
    for index in range(0, len(encoded), 2):
        code_unit = encoded[index] | (encoded[index + 1] << 8)
        hash_value ^= code_unit
        hash_value = (hash_value * 16777619) & 0xFFFFFFFF
    return f"{hash_value:08x}"


def _stable_speech_cue_hash(speech_cues: list[TtsSpeechCue] | None) -> str:
    if not speech_cues:
        return "no-cues"
    normalized: list[dict[str, Any]] = []
    for cue in speech_cues:
        item: dict[str, Any] = {
            "type": cue.type,
            "target_text": cue.target_text,
        }
        if cue.style is not None:
            item["style"] = cue.style
        if cue.priority is not None:
            item["priority"] = cue.priority
        normalized.append(item)
    return _stable_text_hash(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")))


def _effective_tts_language(provider: str, text: str, normalized_language: str, default_language: str) -> str:
    if provider in {"genie", "genie_server"}:
        return resolve_genie_tts_language(text, normalized_language, default_language)
    return normalized_language


def _spell_latin_for_provider(provider: str) -> bool:
    return provider not in {"azure_speech"}


def _single_request_tts_provider(provider: str) -> bool:
    return provider in {"azure_speech", "genie_server", "gpt_sovits_server"}


def _normalize_payload_text(
    text: str,
    default_language: str,
    language: str | None,
    speech_cues: list[TtsSpeechCue] | None = None,
    *,
    provider: str | None = None,
):
    # Ignore legacy repeat cues so cached and newly synthesized audio always
    # uses the original narration exactly once.
    planned_text = str(text or "")
    return normalize_tts_text(
        planned_text,
        default_language,
        language,
        spell_latin_for_chinese=_spell_latin_for_provider(provider or get_tts_settings().provider),
    )


def _course_audio_path(payload: TtsSynthesizeRequest, normalized_text: str, effective_language: str) -> Path | None:
    if not payload.chapter_id:
        return None
    settings = get_tts_settings()
    text_hash = hashlib.sha256(f"{effective_language}\n{normalized_text}".encode("utf-8")).hexdigest()[:24]
    requested_hash = _safe_id(payload.content_hash, text_hash)
    if requested_hash == "none":
        requested_hash = text_hash
    else:
        requested_hash = _safe_id(f"{effective_language}-{requested_hash}", text_hash)
    chapter = _safe_id(payload.chapter_id, "chapter")
    segment = _safe_id(payload.segment_id, "segment")
    return settings.output_dir / COURSE_AUDIO_DIR / chapter / f"{segment}-{requested_hash}.wav"


def clear_course_tts_cache(chapter_id: str, segment_id: str | None = None) -> dict[str, Any]:
    settings = get_tts_settings()
    chapter = _safe_id(chapter_id, "chapter")
    course_dir = settings.output_dir / COURSE_AUDIO_DIR / chapter
    removed_files = 0
    removed_bytes = 0
    removed_jobs = 0
    safe_segment = _safe_id(segment_id, "segment") if segment_id else ""

    if course_dir.exists():
        targets = list(course_dir.glob(f"{safe_segment}-*.wav")) if safe_segment else list(course_dir.rglob("*.wav"))
        for path in targets:
            try:
                removed_bytes += path.stat().st_size
                path.unlink()
                removed_files += 1
            except OSError:
                pass
        if not safe_segment:
            try:
                shutil.rmtree(course_dir)
            except OSError:
                pass

    if not safe_segment:
        for job_id, job in list(COURSE_TTS_JOBS.items()):
            if str(job.get("chapter_id") or "") != str(chapter_id):
                continue
            task = job.get("task")
            if task and not task.done():
                task.cancel()
            COURSE_TTS_JOBS.pop(job_id, None)
        job_dir = _course_job_dir()
        if job_dir.is_dir():
            for path in job_dir.glob("*.json"):
                try:
                    job = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if str(job.get("chapter_id") or "") == str(chapter_id):
                    try:
                        path.unlink()
                        removed_jobs += 1
                    except OSError:
                        pass

    return {
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "removed_jobs": removed_jobs,
        "chapter_id": chapter_id,
        "segment_id": segment_id,
    }


def _copy_to_course_audio(audio_path: Path, persistent_path: Path) -> Path:
    persistent_path.parent.mkdir(parents=True, exist_ok=True)
    if audio_path.resolve() != persistent_path.resolve():
        shutil.copyfile(audio_path, persistent_path)
    return persistent_path


def _course_job_dir() -> Path:
    return get_tts_settings().output_dir / "course-jobs"


def _course_job_path(job_id: str) -> Path:
    safe_job_id = _safe_id(job_id, "job")
    return _course_job_dir() / f"{safe_job_id}.json"


def _public_course_job(job: dict[str, Any]) -> dict[str, Any]:
    hidden = {"task", "cancel_requested"}
    return {key: value for key, value in job.items() if key not in hidden}


def _write_course_job(job: dict[str, Any]) -> None:
    path = _course_job_path(str(job.get("job_id") or "job"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(_public_course_job(job), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _load_course_job(job_id: str) -> dict[str, Any] | None:
    path = _course_job_path(job_id)
    if not path.is_file():
        return None
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if job.get("status") in {"queued", "running", "stopping"}:
        job["status"] = "failed"
        job["stage"] = "failed"
        job["error"] = "语音任务所在服务已重启，请重新启动全课语音生成"
        job["message"] = job["error"]
        job["updated_at"] = datetime.now().isoformat()
        _write_course_job(job)
    return job


def _iter_course_jobs() -> list[dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = {str(job.get("job_id")): job for job in COURSE_TTS_JOBS.values() if job.get("job_id")}
    job_dir = _course_job_dir()
    if job_dir.is_dir():
        for path in job_dir.glob("*.json"):
            job = _load_course_job(path.stem)
            if job and job.get("job_id"):
                jobs.setdefault(str(job["job_id"]), job)
    return list(jobs.values())


def _update_course_job(job: dict[str, Any], **patch: Any) -> None:
    job.update(patch)
    job["updated_at"] = datetime.now().isoformat()
    _write_course_job(job)


def _course_job_elapsed_seconds(job: dict[str, Any]) -> int:
    started_at = job.get("started_at") or job.get("created_at")
    if not started_at:
        return 0
    try:
        started = datetime.fromisoformat(str(started_at))
    except ValueError:
        return 0
    return max(0, int((datetime.now() - started).total_seconds()))


async def _run_course_tts_job(job_id: str, request: TtsCourseJobRequest) -> None:
    job = COURSE_TTS_JOBS[job_id]
    async with COURSE_TTS_JOB_LOCK:
        if job.get("cancel_requested"):
            _update_course_job(job, status="cancelled", stage="cancelled", message="全课语音生成已停止")
            return
        _update_course_job(
            job,
            status="running",
            stage="starting",
            message="正在准备全课语音生成",
            started_at=datetime.now().isoformat(),
        )
        ready_chunks = 0
        total_chunks = 0
        cache_hits = 0
        results: list[dict[str, Any]] = []
        try:
            settings = get_tts_settings()
            single_request = _single_request_tts_provider(settings.provider)
            for slide in request.slides:
                if job.get("cancel_requested"):
                    break
                # Repeat cues are legacy metadata only; they must not affect
                # the generated course audio or its cache key.
                speech_cues: list[TtsSpeechCue] = []
                should_split = len(slide.text) > request.max_chars and not single_request
                if should_split:
                    _update_course_job(
                        job,
                        stage="splitting",
                        current_slide=slide.position + 1,
                        current_slide_index=slide.slide_index,
                        message=f"正在切分第 {slide.slide_index} 页讲稿",
                    )
                    segment_payload = TtsSegmentRequest(
                        text=slide.text,
                        language=request.language,
                        max_chars=request.max_chars,
                        speech_cues=speech_cues,
                    )
                    segment_result = await split_segments(segment_payload)
                    chunks = segment_result.get("segments") or []
                else:
                    chunks = [{"index": 0, "text": slide.text, "length": len(slide.text)}]
                if not chunks:
                    raise RuntimeError(f"第 {slide.slide_index} 页语音分段失败")

                total_chunks += len(chunks)
                _update_course_job(
                    job,
                    stage="synthesizing",
                    current_slide=slide.position + 1,
                    current_slide_index=slide.slide_index,
                    total_chunks=total_chunks,
                    ready_chunks=ready_chunks,
                    cache_hits=cache_hits,
                    message=f"正在生成第 {slide.slide_index} 页语音",
                )

                for chunk in chunks:
                    if job.get("cancel_requested"):
                        break
                    chunk_index = int(chunk.get("index") or 0)
                    chunk_text = str(chunk.get("text") or "").strip()
                    if not chunk_text:
                        continue
                    cue_hash = _stable_speech_cue_hash(None if should_split else speech_cues)
                    text_hash = _stable_text_hash(chunk_text)
                    synth_payload = TtsSynthesizeRequest(
                        text=chunk_text,
                        split_sentence=True,
                        chapter_id=request.chapter_id,
                        segment_id=f"slide-{slide.slide_index}-chunk-{chunk_index + 1}",
                        content_hash=f"{text_hash}-{cue_hash}",
                        speech_cues=None if should_split else speech_cues,
                        language=request.language,
                    )
                    _update_course_job(
                        job,
                        stage="synthesizing",
                        current_chunk=chunk_index + 1,
                        message=f"正在生成第 {slide.slide_index} 页第 {chunk_index + 1} 段语音",
                    )
                    result = await synthesize(synth_payload)
                    if not result.get("success") or not result.get("audio_url"):
                        raise RuntimeError(str(result.get("detail") or result.get("error") or "语音生成失败"))
                    ready_chunks += 1
                    if result.get("cache_hit"):
                        cache_hits += 1
                    results.append(
                        {
                            "slide_index": slide.slide_index,
                            "chunk_index": chunk_index,
                            "audio_url": result.get("audio_url"),
                            "cache_hit": bool(result.get("cache_hit")),
                        }
                    )
                    _update_course_job(
                        job,
                        ready_chunks=ready_chunks,
                        total_chunks=total_chunks,
                        cache_hits=cache_hits,
                        results=results,
                    )

            if job.get("cancel_requested"):
                _update_course_job(
                    job,
                    status="cancelled",
                    stage="cancelled",
                    message=f"已停止全课语音生成，已完成 {ready_chunks}/{total_chunks} 段",
                    ready_chunks=ready_chunks,
                    total_chunks=total_chunks,
                    cache_hits=cache_hits,
                    results=results,
                )
                return
            _update_course_job(
                job,
                status="completed",
                stage="completed",
                current_slide=request.slides[-1].position + 1,
                ready_chunks=ready_chunks,
                total_chunks=total_chunks,
                cache_hits=cache_hits,
                results=results,
                message=f"已生成全课语音：{ready_chunks}/{total_chunks} 段，缓存命中 {cache_hits} 段",
            )
        except HTTPException as exc:
            detail = str(exc.detail)
            _update_course_job(job, status="failed", stage="failed", error=detail, message=detail)
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            _update_course_job(job, status="failed", stage="failed", error=message, message=message)


def _resolve_audio_path(file_name: str) -> Path:
    settings = get_tts_settings()
    normalized_name = file_name.replace("\\", "/").strip("/")
    requested = Path(normalized_name)
    if requested.is_absolute() or ".." in requested.parts or requested.suffix.lower() != ".wav":
        raise HTTPException(status_code=404, detail="Audio file not found.")

    search_dirs = [
        settings.output_dir / "cache",
        settings.output_dir,
    ]
    for base_dir in search_dirs:
        base = base_dir.resolve()
        candidate = (base / requested).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    raise HTTPException(status_code=404, detail="Audio file not found.")


def _ensure_tts_enabled(provider: str | None = None) -> None:
    settings = get_tts_settings()
    if not settings.enabled:
        raise HTTPException(status_code=503, detail="TTS is disabled. Set KGTS_TTS_ENABLED=1 locally to enable it.")
    if provider and settings.provider != provider:
        raise HTTPException(status_code=400, detail=f"TTS provider is {settings.provider}, not {provider}.")


def _ensure_cache_admin_enabled() -> None:
    if not is_tts_cache_admin_enabled():
        raise HTTPException(status_code=403, detail="TTS cache administration is disabled in this environment.")


async def _synthesize_via_server(payload: TtsSynthesizeRequest) -> Path:
    settings = get_tts_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_payload_text(payload.text, settings.language, payload.language, payload.speech_cues)
    effective_language = _effective_tts_language(settings.provider, normalized.normalized_text, normalized.text_lang, settings.language)
    body: dict[str, Any] = {
        "text": normalized.normalized_text,
        "character_name": payload.character_name or settings.character_name or settings.predefined_character,
        "split_sentence": payload.split_sentence,
    }
    if effective_language:
        body["language"] = effective_language

    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.post(f"{settings.server_url}/tts", json=body)
        response.raise_for_status()
        media_type = response.headers.get("content-type", "")
        if "application/json" in media_type:
            data = response.json()
            audio_url = data.get("audio_url") or data.get("url") or data.get("file")
            if not audio_url:
                raise HTTPException(status_code=502, detail="Genie-TTS server did not return an audio URL.")
            audio_url_text = str(audio_url)
            if audio_url_text.startswith("/"):
                audio_url_text = f"{settings.server_url}{audio_url_text}"
            audio_response = await client.get(audio_url_text)
            audio_response.raise_for_status()
            content = audio_response.content
        else:
            content = response.content

    if not content:
        raise HTTPException(status_code=502, detail="Genie-TTS server returned an empty audio response.")

    audio_path = settings.output_dir / f"tts-server-{hashlib.sha256(content).hexdigest()[:24]}.wav"
    audio_path.write_bytes(content)
    try:
        validate_wav_audio_file(audio_path, label="Genie-TTS server response")
    except RuntimeError as exc:
        audio_path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    genie_tts_service.cleanup_audio_dir(settings.output_dir, settings.max_audio_files)
    return audio_path


async def _synthesize_via_gpt_sovits_server(payload: TtsSynthesizeRequest) -> Path:
    settings = get_tts_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_payload_text(payload.text, settings.language, payload.language, payload.speech_cues)
    body: dict[str, Any] = {
        "text": normalized.normalized_text,
        "text_lang": normalized.text_lang,
        "ref_audio_path": payload.reference_audio_path or settings.reference_audio_path,
        "prompt_text": payload.reference_text or settings.reference_text,
        "prompt_lang": payload.reference_language or settings.reference_language or settings.language,
        "top_k": payload.top_k or settings.top_k,
        "top_p": payload.top_p if payload.top_p is not None else settings.top_p,
        "temperature": payload.temperature if payload.temperature is not None else settings.temperature,
        "text_split_method": payload.text_split_method or settings.text_split_method,
        "batch_size": settings.batch_size,
        "batch_threshold": settings.batch_threshold,
        "split_bucket": settings.split_bucket,
        "speed_factor": payload.speed_factor if payload.speed_factor is not None else settings.speed_factor,
        "streaming_mode": False,
        "parallel_infer": settings.parallel_infer,
        "repetition_penalty": payload.repetition_penalty
        if payload.repetition_penalty is not None
        else settings.repetition_penalty,
    }

    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.post(f"{settings.server_url}/tts", json=body)
        response.raise_for_status()
        media_type = response.headers.get("content-type", "")
        if "application/json" in media_type:
            data = response.json()
            message = data.get("message") or data.get("detail") or data.get("Exception") or str(data)
            raise HTTPException(status_code=502, detail=f"GPT-SoVITS server did not return audio: {message}")
        content = response.content

    if not content:
        raise HTTPException(status_code=502, detail="GPT-SoVITS server returned an empty audio response.")

    audio_path = settings.output_dir / f"tts-gpt-sovits-{hashlib.sha256(content).hexdigest()[:24]}.wav"
    audio_path.write_bytes(content)
    genie_tts_service.cleanup_audio_dir(settings.output_dir, settings.max_audio_files)
    return audio_path


def _azure_speech_voice(language: str | None) -> str:
    voice = os.getenv("KGTS_TTS_AZURE_SPEECH_VOICE", "").strip()
    if voice:
        return voice
    lang = (language or "").lower()
    if lang.startswith("en"):
        return "en-US-JennyNeural"
    return "zh-CN-YunxiNeural"


def _azure_speech_ssml_language(language: str | None, default_language: str) -> str:
    lang = (language or default_language or "zh").lower()
    if lang.startswith("en"):
        return "en-US"
    if lang.startswith("zh") or "zh" in lang:
        return "zh-CN"
    return "zh-CN"


async def _synthesize_via_azure_speech(payload: TtsSynthesizeRequest) -> Path:
    settings = get_tts_settings()
    key = os.getenv("KGTS_TTS_AZURE_SPEECH_KEY", "").strip()
    region = os.getenv("KGTS_TTS_AZURE_SPEECH_REGION", "").strip()
    endpoint = os.getenv("KGTS_TTS_AZURE_SPEECH_ENDPOINT", "").strip()
    output_format = os.getenv("KGTS_TTS_AZURE_SPEECH_FORMAT", "riff-24khz-16bit-mono-pcm").strip()
    if not key:
        raise HTTPException(status_code=503, detail="Azure Speech key is not configured.")
    if not endpoint:
        if not region:
            raise HTTPException(status_code=503, detail="Azure Speech region is not configured.")
        endpoint = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_payload_text(payload.text, settings.language, payload.language, payload.speech_cues)
    effective_language = _effective_tts_language(settings.provider, normalized.normalized_text, normalized.text_lang, settings.language)
    voice = _azure_speech_voice(effective_language)
    ssml_language = _azure_speech_ssml_language(effective_language, settings.language)
    ssml = (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
        f"xml:lang='{html.escape(ssml_language)}'>"
        f"<voice name='{html.escape(voice)}'>{html.escape(normalized.normalized_text)}</voice>"
        "</speak>"
    )
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": output_format,
        "User-Agent": "KGTS",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(endpoint, content=ssml.encode("utf-8"), headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Azure Speech request failed: {exc}") from exc
    if response.status_code >= 400:
        detail = response.text[:500] if response.text else response.reason_phrase
        raise HTTPException(status_code=502, detail=f"Azure Speech returned HTTP {response.status_code}: {detail}")
    content = response.content
    if not content:
        raise HTTPException(status_code=502, detail="Azure Speech returned an empty audio response.")

    audio_path = settings.output_dir / f"tts-azure-{hashlib.sha256(content).hexdigest()[:24]}.wav"
    audio_path.write_bytes(content)
    try:
        validate_wav_audio_file(audio_path, label="Azure Speech response")
    except RuntimeError as exc:
        audio_path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    genie_tts_service.cleanup_audio_dir(settings.output_dir, settings.max_audio_files)
    return audio_path


@router.get("/status")
async def tts_status() -> dict[str, Any]:
    return get_tts_status()


@router.get("/cache/status")
async def cache_status() -> dict[str, Any]:
    _ensure_cache_admin_enabled()
    settings = get_tts_settings()
    return {"success": True, **tts_audio_cache.status(settings)}


@router.post("/cache/clear")
async def clear_audio_cache() -> dict[str, Any]:
    _ensure_cache_admin_enabled()
    settings = get_tts_settings()
    result = tts_audio_cache.clear(settings, preserve_keys=gpt_sovits_local_service.active_cache_keys())
    return {"success": True, **result}


@router.post("/course-cache/clear")
async def clear_course_audio_cache(payload: TtsCourseCacheClearRequest) -> dict[str, Any]:
    return {"success": True, **clear_course_tts_cache(payload.chapter_id, payload.segment_id)}


@router.post("/segments")
async def split_segments(payload: TtsSegmentRequest) -> dict[str, Any]:
    settings = get_tts_settings()
    normalized = _normalize_payload_text(payload.text, settings.language, payload.language, payload.speech_cues)
    effective_language = _effective_tts_language(settings.provider, normalized.normalized_text, normalized.text_lang, settings.language)
    max_chars = payload.max_chars or min(settings.max_chars, DEFAULT_SEGMENT_CHARS)
    segments = _split_tts_text(normalized.normalized_text, max_chars=max_chars)
    _tts_log(
        "segments "
        f"provider={settings.provider} chars={len(normalized.normalized_text)} "
        f"segments={len(segments)} max_chars={max_chars} lang={effective_language}"
    )
    return {
        "success": True,
        "segments": [{"index": index, "text": text, "length": len(text)} for index, text in enumerate(segments)],
        "segment_count": len(segments),
        "normalized_text_length": len(normalized.normalized_text),
        "text_lang": effective_language,
        "max_chars": max_chars,
    }


@router.post("/course-jobs")
async def create_course_tts_job(payload: TtsCourseJobRequest) -> dict[str, Any]:
    _ensure_tts_enabled()
    status = get_tts_status()
    if not status.get("available"):
        raise HTTPException(status_code=503, detail=status.get("detail") or "语音接口未接入")

    job_id = f"course_tts_{uuid.uuid4().hex[:12]}"
    job = {
        "success": True,
        "job_id": job_id,
        "chapter_id": payload.chapter_id,
        "status": "queued",
        "stage": "queued",
        "message": "全课语音生成任务已排队，关闭网页后会继续生成",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "started_at": None,
        "slide_count": len(payload.slides),
        "current_slide": 0,
        "current_slide_index": None,
        "current_chunk": 0,
        "ready_chunks": 0,
        "total_chunks": 0,
        "cache_hits": 0,
        "max_chars": payload.max_chars,
        "results": [],
        "error": "",
        "cancel_requested": False,
    }
    COURSE_TTS_JOBS[job_id] = job
    _write_course_job(job)
    job["task"] = asyncio.create_task(_run_course_tts_job(job_id, payload))
    return {
        **_public_course_job(job),
        "elapsed_seconds": 0,
    }


@router.get("/course-jobs/{job_id}")
async def get_course_tts_job(job_id: str) -> dict[str, Any]:
    job = COURSE_TTS_JOBS.get(job_id) or _load_course_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="全课语音任务不存在或已过期")
    return {
        **_public_course_job(job),
        "success": True,
        "elapsed_seconds": _course_job_elapsed_seconds(job),
    }


@router.get("/course-jobs/latest/by-chapter")
async def get_latest_course_tts_job(chapter_id: str) -> dict[str, Any]:
    chapter = str(chapter_id or "").strip()
    if not chapter:
        raise HTTPException(status_code=400, detail="缺少课程 id")
    matches = [job for job in _iter_course_jobs() if str(job.get("chapter_id") or "") == chapter]
    matches.sort(key=lambda job: str(job.get("created_at") or job.get("updated_at") or ""), reverse=True)
    if not matches:
        return {"success": True, "job": None}
    job = matches[0]
    return {
        "success": True,
        "job": {
            **_public_course_job(job),
            "success": True,
            "elapsed_seconds": _course_job_elapsed_seconds(job),
        },
    }


@router.post("/course-jobs/{job_id}/stop")
async def stop_course_tts_job(job_id: str) -> dict[str, Any]:
    job = COURSE_TTS_JOBS.get(job_id) or _load_course_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="全课语音任务不存在或已过期")
    if job.get("status") in {"completed", "failed", "cancelled"}:
        return {
            **_public_course_job(job),
            "success": True,
            "elapsed_seconds": _course_job_elapsed_seconds(job),
        }
    job["cancel_requested"] = True
    _update_course_job(job, status="stopping", stage="stopping", message="正在停止全课语音生成，当前段完成后停止")
    return {
        **_public_course_job(job),
        "success": True,
        "elapsed_seconds": _course_job_elapsed_seconds(job),
    }


@router.post("/load-character")
async def load_character(payload: TtsLoadCharacterRequest) -> dict[str, Any]:
    _ensure_tts_enabled("genie")
    try:
        character_name = genie_tts_service.load_character(
            character_name=payload.character_name,
            predefined_character=payload.predefined_character,
            model_dir=payload.model_dir,
            language=payload.language,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"success": True, "character_name": character_name}


@router.post("/reference-audio")
async def set_reference_audio(payload: TtsReferenceAudioRequest) -> dict[str, Any]:
    _ensure_tts_enabled("genie")
    try:
        genie_tts_service.set_reference_audio(
            character_name=payload.character_name,
            audio_path=payload.audio_path,
            audio_text=payload.audio_text,
            language=payload.language,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"success": True}


@router.post("/synthesize")
async def synthesize(payload: TtsSynthesizeRequest) -> dict[str, Any]:
    settings = get_tts_settings()
    _ensure_tts_enabled()
    normalized = _normalize_payload_text(payload.text, settings.language, payload.language, payload.speech_cues)
    text = normalized.normalized_text
    effective_language = _effective_tts_language(settings.provider, text, normalized.text_lang, settings.language)
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty after cleaning.")
    if len(text) > settings.max_chars and not _single_request_tts_provider(settings.provider):
        raise _long_text_error(len(text), settings.max_chars)
    persistent_path = _course_audio_path(payload, text, effective_language)
    if persistent_path and payload.force:
        clear_course_tts_cache(payload.chapter_id or "", payload.segment_id)
    if persistent_path and persistent_path.is_file() and not payload.force:
        return {
            "success": True,
            "provider": settings.provider,
            "audio_url": _audio_url(persistent_path),
            "cache_hit": True,
            "cache_key": persistent_path.stem,
            "normalized_text_length": len(text),
            "text_length": len(text),
            "text_lang": effective_language,
        }

    started_at = time.perf_counter()
    request_id = hashlib.sha256(f"{time.time_ns()}:{text}".encode("utf-8")).hexdigest()[:8]
    _tts_log(
        "synthesize:start "
        f"id={request_id} provider={settings.provider} chars={len(text)} "
        f"lang={effective_language}"
    )

    try:
        if settings.provider == "gpt_sovits_local":
            result = await run_in_threadpool(
                gpt_sovits_local_service.synthesize,
                text=text,
                language=payload.language,
                reference_audio_path=payload.reference_audio_path,
                reference_text=payload.reference_text,
                reference_language=payload.reference_language,
                speed_factor=payload.speed_factor,
                top_k=payload.top_k,
                top_p=payload.top_p,
                temperature=payload.temperature,
                repetition_penalty=payload.repetition_penalty,
                text_split_method=payload.text_split_method,
            )
            elapsed = time.perf_counter() - started_at
            _tts_log(
                "synthesize:done "
                f"id={request_id} provider={result.provider} "
                f"cache={'hit' if result.cache_hit else 'miss'} "
                f"file={result.path.name} elapsed={elapsed:.1f}s"
            )
            return {
                "success": True,
                "provider": result.provider,
                "audio_url": _audio_url(result.path),
                "cache_hit": result.cache_hit,
                "cache_key": result.cache_key,
                "normalized_text_length": result.normalized_text_length,
                "text_length": result.normalized_text_length,
                "text_lang": result.text_lang,
            }
        if settings.provider == "genie_server":
            audio_path = await _synthesize_via_server(payload.model_copy(update={"text": text}))
        elif settings.provider == "azure_speech":
            audio_path = await _synthesize_via_azure_speech(payload.model_copy(update={"text": text}))
        elif settings.provider == "gpt_sovits_server":
            audio_path = await _synthesize_via_gpt_sovits_server(payload.model_copy(update={"text": text}))
        elif settings.provider == "genie":
            audio_path = await run_in_threadpool(
                genie_tts_service.synthesize,
                text=text,
                character_name=payload.character_name,
                split_sentence=payload.split_sentence,
                model_dir=payload.model_dir,
                language=effective_language,
                reference_audio_path=payload.reference_audio_path,
                reference_text=payload.reference_text,
                reference_language=payload.reference_language,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported TTS provider: {settings.provider}")
    except HTTPException:
        elapsed = time.perf_counter() - started_at
        _tts_log(
            "synthesize:failed "
            f"id={request_id} provider={settings.provider} elapsed={elapsed:.1f}s "
            f"status=HTTPException"
        )
        raise
    except ValueError as exc:
        elapsed = time.perf_counter() - started_at
        _tts_log(
            "synthesize:failed "
            f"id={request_id} provider={settings.provider} elapsed={elapsed:.1f}s "
            f"error={exc}"
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        elapsed = time.perf_counter() - started_at
        _tts_log(
            "synthesize:failed "
            f"id={request_id} provider={settings.provider} elapsed={elapsed:.1f}s "
            f"error={exc}"
        )
        raise HTTPException(status_code=502, detail=f"TTS server request failed: {exc}") from exc
    except Exception as exc:
        elapsed = time.perf_counter() - started_at
        _tts_log(
            "synthesize:failed "
            f"id={request_id} provider={settings.provider} elapsed={elapsed:.1f}s "
            f"error={exc}"
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    elapsed = time.perf_counter() - started_at
    if persistent_path:
        audio_path = _copy_to_course_audio(audio_path, persistent_path)
    _tts_log(
        "synthesize:done "
        f"id={request_id} provider={settings.provider} cache=miss "
        f"file={audio_path.name} elapsed={elapsed:.1f}s"
    )
    return {
        "success": True,
        "provider": settings.provider,
        "audio_url": _audio_url(audio_path),
        "cache_hit": False,
        "cache_key": None,
        "normalized_text_length": len(text),
        "text_length": len(text),
        "text_lang": effective_language,
    }


@router.get("/audio/{file_path:path}", include_in_schema=False)
async def audio_file(file_path: str) -> FileResponse:
    path = _resolve_audio_path(file_path)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=path.name,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/unload-character")
async def unload_character(payload: TtsUnloadCharacterRequest) -> dict[str, Any]:
    _ensure_tts_enabled("genie")
    try:
        unloaded = genie_tts_service.unload_character(payload.character_name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"success": True, "unloaded": unloaded}


@router.post("/stop")
async def stop_tts() -> dict[str, Any]:
    _ensure_tts_enabled("genie")
    try:
        stopped = genie_tts_service.stop()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"success": True, "stopped": stopped}


@router.post("/clear-cache")
async def clear_legacy_cache() -> dict[str, Any]:
    _ensure_tts_enabled()
    settings = get_tts_settings()
    try:
        cleared_provider_cache = False
        if settings.provider == "genie":
            cleared_provider_cache = genie_tts_service.clear_reference_audio_cache()
        removed_audio_files = 0
        if settings.output_dir.exists():
            for path in settings.output_dir.glob("*.wav"):
                path.unlink(missing_ok=True)
                removed_audio_files += 1
        temp_dir = settings.output_dir / "tmp"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        cache_result = tts_audio_cache.clear(settings, preserve_keys=gpt_sovits_local_service.active_cache_keys())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "success": True,
        "cleared_provider_cache": cleared_provider_cache,
        "removed_audio_files": removed_audio_files + int(cache_result.get("removed_files", 0)),
    }
