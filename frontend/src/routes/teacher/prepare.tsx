import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  ChevronsUpDown,
  CheckCircle2,
  Clipboard,
  Copy,
  Download,
  FileText,
  FileUp,
  ImagePlus,
  Minus,
  LayoutPanelTop,
  Maximize2,
  MessageSquareText,
  Minimize2,
  Move,
  Network,
  Pause,
  Play,
  Plus,
  RotateCcw,
  RotateCw,
  Save,
  Square,
  Trash2,
  Wand2,
} from "lucide-react"
import {
  useExportCoursewarePptx,
  useCoursewareProject,
  useCoursewareProjects,
  useDeleteCoursewareProject,
  useGeneratePptLectures,
  useGeneratePptTex,
  useGenerateSlideLectures,
  useGraphNodeContext,
  usePlanSlideSpeech,
  usePreviewTex,
  usePreviewPpt,
  useSaveCoursewareProject,
  useUploadCoursewareAssets,
  useUploadCoursewareStyleReference,
  createCourseTtsJob,
  getCourseTtsJob,
  getLatestCourseTtsJob,
  getCoursewareRenderJob,
  getSlideLectureJob,
  getTtsStatus,
  stopCourseTtsJob,
} from "@/api/education"
import { useGraphScopeTree } from "@/api/graph"
import { useDeleteChapter, useSaveChapter, useSaveLecture, useTeacherChapter } from "@/api/teacher"
import {
  GraphContextPanel,
  GraphTreePanel,
  buildGraphScopeTree,
  buildParentByChild,
  resolveNextGraphScopeSelection,
  sortGraphScopeNodeIds,
} from "@/components/common/GraphScopeSelector"
import { LoadingSpinner } from "@/components/common/LoadingSpinner"
import { PlaybackProgress } from "@/components/common/PlaybackProgress"
import { RichTextContent } from "@/components/renderers/RichTextContent"
import { TTS_CHUNK_CHARS, useLecturePlayback } from "@/hooks/useLecturePlayback"
import type {
  GraphSourceScope,
  CoursewareAsset,
  CoursewareStyleReference,
  EditableSlideModel,
  EditableSlideObject,
  PptArtifact,
  PptPreviewResponse,
  PptSlideDetail,
  PptSlideLecture,
  SourceDriftReport,
  CoursewareProject,
  SpeechCue,
  TtsCourseJobResponse,
} from "@/types/education"
import type { Chapter } from "@/types/chapter"
import { getRuntimeConfig } from "@/lib/config"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/teacher/prepare")({
  component: TeacherPreparePage,
  validateSearch: (search: Record<string, unknown>) => ({
    chapterId: typeof search.chapterId === "string" ? search.chapterId : "",
    nodeId: typeof search.nodeId === "string" ? search.nodeId : "",
    courseId: typeof search.courseId === "string" ? search.courseId : "",
  }),
})

type GenerationMode = "graph" | "upload"
type SlideLayout = NonNullable<PptSlideDetail["layout"]>
type SlideLayoutColumn = NonNullable<SlideLayout["columns"]>[number]
type SlideImage = NonNullable<PptSlideDetail["images"]>[number]
type CanvasItemKind = "title" | "content" | "image"
type CanvasItem = {
  id: string
  type: CanvasItemKind
  ref?: string
  x: number
  y: number
  width: number
  height: number
}
type ResizeHandle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw"
type CanvasInteraction = {
  id: string
  mode: "move" | "resize"
  handle?: ResizeHandle
  startX: number
  startY: number
  item: CanvasItem
}
type LayoutNormalizeOptions = {
  force?: boolean
}
const COURSEWARE_ACCEPT = ".ppt,.pptx,.tex,.md,.markdown,.txt,.rst,.csv,.json,.html,.htm,.rtf,.docx,.pdf,.zip"
const COURSEWARE_FORMAT_LABEL = "PPT/PPTX、TeX、Markdown、TXT、RST、CSV、JSON、HTML、RTF、DOCX、PDF、ZIP"
const CANVAS_WIDTH = 1000
const CANVAS_HEIGHT = 562.5
const DEFAULT_LECTURE_DURATION_MINUTES = 10
const DEFAULT_SPEECH_RATE_CPM = 250
const SLIDE_LECTURE_JOB_STORAGE_KEY = "kgts.prepare.slideLectureJob.v1"
const COURSE_TTS_JOB_STORAGE_KEY = "kgts.prepare.courseTtsJob.v1"

type StoredSlideLectureJob = {
  jobId: string
  title: string
  createdAt: string
  selectedIndex?: number
}

type StoredCourseAudioJob = {
  jobId: string
  chapterId: string
  title: string
  createdAt: string
}

type CourseAudioProgress = {
  running: boolean
  currentSlide: number
  slideCount: number
  readyChunks: number
  totalChunks: number
  cacheHits: number
  message?: string
  error?: string
}

function normalizeTexNewlines(value: string) {
  return value.replace(/\r\n/g, "\n").replace(/\r/g, "\n")
}

function normalizeImageSourcePath(value?: string) {
  const normalized = String(value || "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\.\/+/, "")
    .toLowerCase()
  return normalized
}

function imageSourcePathKeys(value?: string) {
  const normalized = normalizeImageSourcePath(value)
  if (!normalized) return []
  const basename = normalized.split("/").pop() || normalized
  const withoutExtension = normalized.replace(/\.(png|jpe?g|gif|webp|bmp|tiff?|svg)$/i, "")
  const basenameWithoutExtension = basename.replace(/\.(png|jpe?g|gif|webp|bmp|tiff?|svg)$/i, "")
  const parts = normalized.split("/").filter(Boolean)
  const suffixes = parts.flatMap((_, index) => {
    const suffix = parts.slice(index).join("/")
    const suffixWithoutExtension = suffix.replace(/\.(png|jpe?g|gif|webp|bmp|tiff?|svg)$/i, "")
    return [suffix, suffixWithoutExtension]
  })
  return Array.from(new Set([normalized, withoutExtension, basename, basenameWithoutExtension, ...suffixes].filter(Boolean)))
}

function clampLectureDurationMinutes(value: number) {
  if (!Number.isFinite(value)) return DEFAULT_LECTURE_DURATION_MINUTES
  return Math.min(180, Math.max(1, Math.round(value * 10) / 10))
}

function mergeSlideLectures(previous: PptSlideLecture[], incoming: PptSlideLecture[]) {
  const byIndex = new Map(previous.map((lecture) => [lecture.index, lecture]))
  incoming.forEach((lecture) => {
    byIndex.set(lecture.index, lecture)
  })
  return Array.from(byIndex.values()).sort((a, b) => a.index - b.index)
}

function hasUsableSlideLecture(lecture?: PptSlideLecture): lecture is PptSlideLecture & { lecture: string } {
  return Boolean(lecture?.lecture?.trim())
}

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message
  if (typeof error === "object" && error && "message" in error) return String((error as { message?: unknown }).message || "")
  return String(error || "未知错误")
}

function formatSlideLectureJobProgress(job: { message?: string; stage?: string; elapsed_seconds?: number }) {
  const message = String(job.message || job.stage || "正在运行逐页讲解生成").trim()
  const elapsed = typeof job.elapsed_seconds === "number" && Number.isFinite(job.elapsed_seconds) ? Math.max(0, Math.round(job.elapsed_seconds)) : 0
  if (!elapsed) return message
  return `${message}（已运行 ${elapsed} 秒）`
}

function readStoredSlideLectureJob(): StoredSlideLectureJob | null {
  if (typeof window === "undefined") return null
  try {
    const raw = window.localStorage.getItem(SLIDE_LECTURE_JOB_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<StoredSlideLectureJob>
    if (!parsed.jobId) return null
    return {
      jobId: String(parsed.jobId),
      title: String(parsed.title || "逐页讲解"),
      createdAt: String(parsed.createdAt || new Date().toISOString()),
      selectedIndex: typeof parsed.selectedIndex === "number" ? parsed.selectedIndex : undefined,
    }
  } catch {
    return null
  }
}

function writeStoredSlideLectureJob(job: StoredSlideLectureJob | null) {
  if (typeof window === "undefined") return
  if (!job) {
    window.localStorage.removeItem(SLIDE_LECTURE_JOB_STORAGE_KEY)
    return
  }
  window.localStorage.setItem(SLIDE_LECTURE_JOB_STORAGE_KEY, JSON.stringify(job))
}

function readStoredCourseAudioJob(): StoredCourseAudioJob | null {
  if (typeof window === "undefined") return null
  try {
    const raw = window.localStorage.getItem(COURSE_TTS_JOB_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<StoredCourseAudioJob>
    if (!parsed.jobId) return null
    return {
      jobId: String(parsed.jobId),
      chapterId: String(parsed.chapterId || ""),
      title: String(parsed.title || "全课语音"),
      createdAt: String(parsed.createdAt || new Date().toISOString()),
    }
  } catch {
    return null
  }
}

function writeStoredCourseAudioJob(job: StoredCourseAudioJob | null) {
  if (typeof window === "undefined") return
  if (!job) {
    window.localStorage.removeItem(COURSE_TTS_JOB_STORAGE_KEY)
    return
  }
  window.localStorage.setItem(COURSE_TTS_JOB_STORAGE_KEY, JSON.stringify(job))
}

const emptyCourseAudioProgress: CourseAudioProgress = {
  running: false,
  currentSlide: 0,
  slideCount: 0,
  readyChunks: 0,
  totalChunks: 0,
  cacheHits: 0,
}

function courseAudioStatusText(progress: CourseAudioProgress) {
  if (progress.error) return progress.error
  if (!progress.running && progress.totalChunks > 0) {
    return `全课语音已生成：${progress.readyChunks}/${progress.totalChunks} 段，缓存命中 ${progress.cacheHits} 段`
  }
  if (!progress.running) return "全课语音未生成"
  const prefix = progress.message || "全课语音生成中"
  return `${prefix}：第 ${progress.currentSlide}/${progress.slideCount} 页，${progress.readyChunks}/${progress.totalChunks || "?"} 段，缓存命中 ${progress.cacheHits} 段`
}

function courseAudioProgressFromJob(job: TtsCourseJobResponse): CourseAudioProgress {
  const running = ["queued", "running", "stopping"].includes(job.status)
  const failed = job.status === "failed"
  const cancelled = job.status === "cancelled"
  return {
    running,
    currentSlide: running && job.slide_count > 0 ? Math.max(1, job.current_slide || 1) : job.current_slide || 0,
    slideCount: job.slide_count || 0,
    readyChunks: job.ready_chunks || 0,
    totalChunks: job.total_chunks || 0,
    cacheHits: job.cache_hits || 0,
    message: job.message || undefined,
    error: failed ? `全课语音生成失败：${job.error || job.message || "任务失败"}` : cancelled ? job.message || "已停止全课语音生成" : undefined,
  }
}

function estimateSlideDurationMinutes(slide: PptSlideDetail, allSlides: PptSlideDetail[], targetDurationMinutes: number) {
  const slideTextLength = Math.max(1, [slide.title, slide.content, slide.notes, slide.raw_text].join("").length)
  const totalTextLength = Math.max(
    slideTextLength,
    allSlides.reduce((sum, item) => sum + Math.max(1, [item.title, item.content, item.notes, item.raw_text].join("").length), 0),
  )
  const estimated = (targetDurationMinutes * slideTextLength) / totalTextLength
  return Math.min(180, Math.max(0.1, Math.round(estimated * 10) / 10))
}

function safeDownloadFilename(value: string) {
  return (
    value
      .trim()
      .replace(/[\\/:*?"<>|]+/g, "-")
      .replace(/\s+/g, " ")
      .slice(0, 80) || "课件文案"
  )
}

function compactSlideForLectureRequest(slide: PptSlideDetail): PptSlideDetail {
  return {
    index: slide.index,
    title: slide.title,
    content: slide.content,
    notes: slide.notes,
    raw_text: slide.raw_text,
    tables: slide.tables?.slice(0, 2).map((table) => ({ rows: table.rows.slice(0, 8) })),
    layout: slide.layout,
    image_count: slide.image_count,
  }
}

function textFromSlides(slides: PptSlideDetail[]) {
  return slides
    .map((slide) => [slide.title, slide.content, slide.notes, slide.raw_text].map((part) => String(part || "").trim()).filter(Boolean).join("\n"))
    .filter(Boolean)
    .join("\n\n")
}

function coursewareProjectToPreview(project: CoursewareProject): PptPreviewResponse | null {
  const slides = (project.slides || []) as PptSlideDetail[]
  const editableModel = project.editable_model || null
  const modelSlides = editableModel?.slides || []
  if (!slides.length && !modelSlides.length) return null
  const restoredSlides = slides.length
    ? slides
    : modelSlides.map((slide) => ({
        index: slide.index,
        title: slide.title || `第 ${slide.index} 页`,
        content: (slide.objects || slide.items || []).map((object) => object.text || object.latex || "").filter(Boolean).join("\n"),
        source_tex: slide.source_tex || "",
        source_body_tex: slide.source_body_tex || "",
        source_start: slide.source_start,
        source_end: slide.source_end,
        layout: slide.layout,
        notes: slide.notes,
      }))
  const texContent = project.tex_content || editableModel?.source_tex || ""
  return {
    success: true,
    chapter_title: project.title || editableModel?.title || "未命名课件",
    slide_count: restoredSlides.length,
    slides: restoredSlides,
    full_text: textFromSlides(restoredSlides),
    tex_content: texContent,
    editable_model: editableModel || undefined,
    asset_map: project.asset_map || editableModel?.assets || {},
    layout: editableModel?.layout,
    source_tex: texContent,
    rendered_pages: project.rendered_pages,
    render_source: project.render_source,
    render_error: project.render_error,
  }
}

function attachRenderedPagesToPreview(result: PptPreviewResponse): PptPreviewResponse {
  const renderedPages = result.rendered_pages || []
  if (!renderedPages.length) return result
  return {
    ...result,
    slides: result.slides.map((slide, index) => ({
      ...slide,
      rendered_page: renderedPages.find((page) => page.page_index === slide.index - 1) || renderedPages[index],
    })),
  }
}

function preserveRenderedPages(previous: PptPreviewResponse | null | undefined, result: PptPreviewResponse): PptPreviewResponse {
  if ((result.rendered_pages || []).length || !(previous?.rendered_pages || []).length) return result
  return { ...result, rendered_pages: previous?.rendered_pages, render_source: previous?.render_source }
}

function chapterToCoursewareProject(chapter: Chapter): CoursewareProject | null {
  const slides = chapter.ppt_slides || []
  if (!slides.length && !chapter.editable_model && !chapter.tex_content) return null
  return {
    id: chapter.id,
    title: chapter.title,
    editable_model: chapter.editable_model,
    asset_map: chapter.asset_map,
    slides,
    tex_content: chapter.tex_content,
    rendered_pages: chapter.rendered_pages,
    render_source: chapter.render_source,
    render_error: chapter.render_error,
    ppt_artifact: chapter.ppt_artifact,
    source_node_ids: chapter.source_node_ids,
    created_at: typeof chapter.created_at === "number" ? String(chapter.created_at) : chapter.created_at,
    updated_at: typeof chapter.updated_at === "number" ? String(chapter.updated_at) : chapter.updated_at,
  }
}

function formatDuration(seconds?: number) {
  const value = Math.max(0, Math.round(seconds || 0))
  if (value <= 0) return "0 分钟"
  const minutes = Math.floor(value / 60)
  const rest = value % 60
  if (minutes <= 0) return `${rest} 秒`
  if (rest === 0) return `${minutes} 分钟`
  return `${minutes} 分 ${rest} 秒`
}

function coursewareProjectDedupeKey(project: CoursewareProject) {
  const title = String(project.title || "").trim().toLowerCase()
  const sourceIds = (project.source_node_ids || []).map((id) => String(id || "").trim()).filter(Boolean).sort().join("|")
  const slideCount = project.slide_count ?? project.slides?.length ?? project.editable_model?.slides?.length ?? 0
  return `${title || project.id}::${sourceIds}::${slideCount}`
}

function hydratePreviewImages(images: SlideImage[], imageBySourcePath: Map<string, SlideImage>) {
  return images.map((image) => {
    if (image.data_uri || !image.source_path) return image
    const previous = imageSourcePathKeys(image.source_path)
      .map((key) => imageBySourcePath.get(key))
      .find(Boolean)
    return previous?.data_uri ? { ...image, data_uri: previous.data_uri, oversized: previous.oversized } : image
  })
}

function hydrateLayoutImages(layout: PptSlideDetail["layout"], imageBySourcePath: Map<string, SlideImage>) {
  if (!layout?.columns?.length) return layout
  return {
    ...layout,
    columns: layout.columns.map((column) => ({
      ...column,
      images: hydratePreviewImages(column.images || [], imageBySourcePath),
    })),
  }
}

function escapeLatexText(value: string) {
  return value
    .replace(/\\/g, "\\textbackslash{}")
    .replace(/([{}#$%&_])/g, "\\$1")
    .replace(/\^/g, "\\^{}")
    .replace(/~/g, "\\~{}")
}

function normalizeEditableText(value: string) {
  return value.replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim()
}

function replaceFrameTitle(source: string, nextTitle: string) {
  const title = escapeLatexText(normalizeEditableText(nextTitle))
  const beginMatch = /\\begin\{frame\}/.exec(source)
  if (!beginMatch) return source
  let cursor = beginMatch.index + beginMatch[0].length
  while (cursor < source.length && /\s/.test(source[cursor])) cursor += 1
  while (source[cursor] === "<" || source[cursor] === "[") {
    const close = source[cursor] === "<" ? ">" : "]"
    const end = source.indexOf(close, cursor + 1)
    if (end < 0) break
    cursor = end + 1
    while (cursor < source.length && /\s/.test(source[cursor])) cursor += 1
  }
  if (source[cursor] === "{") {
    const end = findMatchingBrace(source, cursor)
    if (end > cursor) return `${source.slice(0, cursor + 1)}${title}${source.slice(end)}`
  }
  const frameTitleMatch = /\\frametitle(?![A-Za-z@])/.exec(source)
  if (frameTitleMatch) {
    let titleCursor = frameTitleMatch.index + frameTitleMatch[0].length
    while (titleCursor < source.length && /\s/.test(source[titleCursor])) titleCursor += 1
    while (source[titleCursor] === "<" || source[titleCursor] === "[") {
      const close = source[titleCursor] === "<" ? ">" : "]"
      const end = source.indexOf(close, titleCursor + 1)
      if (end < 0) break
      titleCursor = end + 1
      while (titleCursor < source.length && /\s/.test(source[titleCursor])) titleCursor += 1
    }
    if (source[titleCursor] === "{") {
      const end = findMatchingBrace(source, titleCursor)
      if (end > titleCursor) return `${source.slice(0, titleCursor + 1)}${title}${source.slice(end)}`
    }
  }
  return `${source.slice(0, cursor)}{${title}}\n${source.slice(cursor)}`
}

function findMatchingBrace(source: string, start: number) {
  let depth = 0
  for (let index = start; index < source.length; index += 1) {
    const character = source[index]
    if (character === "\\" && index + 1 < source.length) {
      index += 1
      continue
    }
    if (character === "{") depth += 1
    if (character === "}") {
      depth -= 1
      if (depth === 0) return index
    }
  }
  return -1
}

function splitEditableLines(value: string) {
  return normalizeEditableText(value)
    .split(/\n+/)
    .map((line) => line.replace(/^\s*[-*]\s*/, "").trim())
    .filter(Boolean)
}

function replaceFrameItems(source: string, nextContent: string) {
  const lines = splitEditableLines(nextContent)
  if (!lines.length || !/\\item/.test(source)) return source
  let lineIndex = 0
  let changed = false
  const nextSource = source.replace(
    /(\\item(?:<[^>]*>)?(?:\[[^\]]*\])?\s*)([\s\S]*?)(?=(?:\n\s*\\item(?:<[^>]*>)?(?:\[[^\]]*\])?\s*)|\n\s*\\end\{itemize\})/g,
    (match, prefix: string, body: string) => {
      if (lineIndex >= lines.length) return match
      if (/\\(includegraphics|begin\{|end\{|[\[\]])/.test(body)) return match
      changed = true
      const replacement = `${prefix}${escapeLatexText(lines[lineIndex])}`
      lineIndex += 1
      return replacement
    },
  )
  return changed ? nextSource : source
}

function replacePlainFrameBody(source: string, nextContent: string) {
  const bodyMatch = /(\\begin\{frame\}(?:\s*(?:<[^>]*>|\[[^\]]*\]))*(?:\s*\{(?:[^{}]|\\.|{[^{}]*})*\})?)([\s\S]*?)(\\end\{frame\})/.exec(source)
  if (!bodyMatch) return source
  const body = bodyMatch[2]
  if (/\\(includegraphics|begin\{|end\{|titlepage|frametitle|[\[\]])/.test(body)) return source
  const lines = splitEditableLines(nextContent)
  const replacement = lines.map((line) => escapeLatexText(line)).join("\n\n")
  return `${source.slice(0, bodyMatch.index)}${bodyMatch[1]}\n${replacement}\n${bodyMatch[3]}${source.slice(bodyMatch.index + bodyMatch[0].length)}`
}

function replaceFrameText(source: string, nextContent: string) {
  const itemReplaced = replaceFrameItems(source, nextContent)
  if (itemReplaced !== source) return itemReplaced
  return replacePlainFrameBody(source, nextContent)
}

function optionEntries(options: string) {
  return options
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
}

function upsertGraphicsWidthOption(options: string, ratio: number) {
  const widthValue = `width=${ratio.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")}\\textwidth`
  const entries = optionEntries(options).filter((entry) => !/^width\s*=/.test(entry))
  return [widthValue, ...entries].join(",")
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

function replaceImageWidth(source: string, image: SlideImage, ratio: number) {
  const refs = [image.tex_ref, image.source_path, ...(imageSourcePathKeys(image.source_path).length ? imageSourcePathKeys(image.source_path) : [])]
    .map((ref) => String(ref || "").trim())
    .filter(Boolean)
  for (const ref of Array.from(new Set(refs))) {
    const pattern = new RegExp(`(\\\\includegraphics)(?:\\[([^\\]]*)\\])?\\{(${escapeRegExp(ref)})\\}`)
    if (!pattern.test(source)) continue
    return source.replace(pattern, (_match, command: string, options: string = "", foundRef: string) => {
      const nextOptions = upsertGraphicsWidthOption(options, ratio)
      return `${command}[${nextOptions}]{${foundRef}}`
    })
  }
  return source
}

function replaceColumnWidth(source: string, column: SlideLayoutColumn, ratio: number) {
  const columnSource = String(column.source_tex || "")
  if (!columnSource) return source
  const nextWidth = `${ratio.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")}\\textwidth`
  const replacedColumn = columnSource.replace(/(\\begin\{column\}(?:\s*(?:<[^>]*>|\[[^\]]*\]))*\s*)\{[^}]*\}/, `$1{${nextWidth}}`)
  if (replacedColumn === columnSource) return source
  return source.replace(columnSource, replacedColumn)
}

function canvasLayoutFromSlide(slide: PptSlideDetail): CanvasItem[] {
  const savedItems = Array.isArray(slide.layout?.canvas?.items) ? slide.layout?.canvas?.items : []
  if (savedItems?.length) {
    return savedItems
      .map((item, index) => {
        const type: CanvasItemKind = item.type === "image" || item.type === "content" || item.type === "title" ? item.type : "content"
        return {
          id: String(item.id || `${type}-${index}`),
          type,
          ref: typeof item.ref === "string" ? item.ref : undefined,
          x: clampCanvasNumber(item.x, 5, 0, CANVAS_WIDTH - 30),
          y: clampCanvasNumber(item.y, 5, 0, CANVAS_HEIGHT - 30),
          width: clampCanvasNumber(item.width, 320, 30, CANVAS_WIDTH),
          height: clampCanvasNumber(item.height, 100, 24, CANVAS_HEIGHT),
        }
      })
      .filter((item) => item.type === "title" || item.type === "content" || item.type === "image")
  }

  const images = collectSlideImages(slide)
  if (slide.layout?.mode === "title") {
    return titleCanvasLayoutFromSlide(slide, images)
  }
  const items: CanvasItem[] = []
  const hasImages = images.length > 0
  const content = String(slide.content || "").trim()
  const titleY = slide.layout?.mode === "title" ? 58 : 34
  const titleHeight = estimateCanvasTextHeight(String(slide.title || ""), 904, 28, 1.12, 16, slide.layout?.mode === "title" ? 116 : 96)
  const contentTop = Math.max(slide.layout?.mode === "title" ? 150 : 118, titleY + titleHeight + 20)
  items.push({
    id: "title",
    type: "title",
    x: 48,
    y: titleY,
    width: 904,
    height: titleHeight,
  })

  if (content) {
    items.push({
      id: "content",
      type: "content",
      x: hasImages && slide.layout?.image_first ? 72 : 64,
      y: hasImages && slide.layout?.image_first ? 348 : contentTop,
      width: hasImages && !slide.layout?.image_first ? 480 : 872,
      height: hasImages && slide.layout?.image_first ? 150 : Math.max(120, CANVAS_HEIGHT - contentTop - 40),
    })
  }

  images.forEach((image, index) => {
    const defaultWidth = Math.round(Math.min(Math.max(Number(image.width_ratio || slide.layout?.max_image_width || 0.55), 0.2), 0.95) * CANVAS_WIDTH)
    const stacked = images.length > 1
    items.push({
      id: imageCanvasId(image, index),
      type: "image",
      ref: image.tex_ref || image.source_path || `image-${index}`,
      x: hasImages && content && !slide.layout?.image_first ? 575 : Math.max(56, Math.round((CANVAS_WIDTH - defaultWidth) / 2)),
      y: slide.layout?.mode === "title" ? 150 + index * 104 : slide.layout?.image_first ? 120 + index * 150 : 130 + index * 140,
      width: stacked ? Math.min(defaultWidth, 390) : defaultWidth,
      height: stacked ? 128 : 300,
    })
  })

  return items
}

function titleCanvasLayoutFromSlide(slide: PptSlideDetail, images: SlideImage[]): CanvasItem[] {
  const footerImage = titleFooterImage(images)
  const logoImages = images.filter((image) => image !== footerImage).slice(0, 2)
  const items: CanvasItem[] = [
    {
      id: "title",
      type: "title",
      x: 160,
      y: 160,
      width: 680,
      height: estimateCanvasTextHeight(String(slide.title || ""), 680, 34, 1.12, 18, 116),
    },
  ]
  if (String(slide.content || "").trim()) {
    items.push({
      id: "content",
      type: "content",
      x: 260,
      y: 292,
      width: 480,
      height: 140,
    })
  }
  logoImages.forEach((image, index) => {
    items.push({
      id: `title-logo-${index}`,
      type: "image",
      ref: image.tex_ref || image.source_path || `title-logo-${index}`,
      x: 1000 - 16 - (logoImages.length - index) * 100,
      y: 3,
      width: 92,
      height: 38,
    })
  })
  if (footerImage) {
    items.push({
      id: "title-footer",
      type: "image",
      ref: footerImage.tex_ref || footerImage.source_path || "title-footer",
      x: 0,
      y: CANVAS_HEIGHT - 62,
      width: CANVAS_WIDTH,
      height: 62,
    })
  }
  return items
}

function clampCanvasNumber(value: unknown, fallback: number, min: number, max: number) {
  const numeric = typeof value === "number" && Number.isFinite(value) ? value : fallback
  return Math.min(Math.max(numeric, min), max)
}

function imageCanvasId(image: SlideImage, index: number) {
  return `image-${normalizeImageSourcePath(image.tex_ref || image.source_path || String(index)).replace(/[^a-z0-9]+/g, "-") || index}`
}

function canvasLayoutById(items: CanvasItem[]) {
  return new Map(items.map((item) => [item.id, item]))
}

function imageForCanvasItem(items: CanvasItem[], item: CanvasItem, images: SlideImage[]) {
  const imagePosition = items.filter((candidate) => candidate.type === "image").findIndex((candidate) => candidate.id === item.id)
  return imagePosition >= 0 ? images[imagePosition] : undefined
}

function titleFooterImage(images: SlideImage[]) {
  return images.find((image) => {
    const source = `${image.source_path || ""} ${image.tex_ref || ""}`.toLowerCase()
    const options = String(image.tex_options || "")
    return options.includes("\\paperwidth") || source.includes("图片3") || source.includes("picture3")
  }) || images[images.length - 1]
}

function assetSourceKeys(asset: CoursewareAsset) {
  return Array.from(
    new Set(
      [asset.source_path, asset.tex_ref, asset.name, ...(asset.aliases || [])]
        .flatMap((value) => imageSourcePathKeys(value))
        .filter(Boolean),
    ),
  )
}

function findAssetForImage(image: SlideImage, assetMap: Record<string, CoursewareAsset>) {
  const keys = new Set([image.source_path, image.tex_ref, ...imageSourcePathKeys(image.source_path), ...imageSourcePathKeys(image.tex_ref)].filter(Boolean))
  return Object.values(assetMap).find((asset) => assetSourceKeys(asset).some((key) => keys.has(key)))
}

function findAssetForObject(object: EditableSlideObject | undefined, assetMap: Record<string, CoursewareAsset>) {
  if (!object) return undefined
  if (object.asset_id && assetMap[object.asset_id]?.data_uri) return assetMap[object.asset_id]
  const keys = new Set([object.source_path, object.tex_ref, object.label, ...imageSourcePathKeys(object.source_path), ...imageSourcePathKeys(object.tex_ref)].filter(Boolean))
  return (
    Object.values(assetMap).find((asset) => asset.data_uri && assetSourceKeys(asset).some((key) => keys.has(key))) ||
    (object.asset_id ? assetMap[object.asset_id] : undefined) ||
    Object.values(assetMap).find((asset) => assetSourceKeys(asset).some((key) => keys.has(key)))
  )
}

function editableCanvasItemsFromModel(model: EditableSlideModel | null, slideIndex: number): CanvasItem[] {
  const slide = model?.slides.find((item) => item.index === slideIndex)
  const objects = slide?.objects || slide?.items || []
  if (!objects.length) return []
  return objects.map((object, index) => {
    const kind = object.type === "image" || object.type === "placeholder" ? "image" : object.type === "title" ? "title" : "content"
    return {
      id: object.id || `${kind}-${index}`,
      type: kind,
      ref: object.tex_ref || object.source_path || object.asset_id,
      x: clampCanvasNumber(object.bbox?.x, 48, 0, CANVAS_WIDTH - 24),
      y: clampCanvasNumber(object.bbox?.y, 48, 0, CANVAS_HEIGHT - 24),
      width: clampCanvasNumber(object.bbox?.width, 240, 24, CANVAS_WIDTH),
      height: clampCanvasNumber(object.bbox?.height, 96, 20, CANVAS_HEIGHT),
    }
  })
}

function normalizeEditableModelLayout(model: EditableSlideModel | null, options: LayoutNormalizeOptions = {}): EditableSlideModel | null {
  if (!model) return model
  return {
    ...model,
    slides: model.slides.map((slide) => {
      const objects = normalizeSlideObjectLayout(slide.objects || slide.items || [], options)
      return { ...slide, objects, items: objects }
    }),
    updated_at: new Date().toISOString(),
  }
}

function normalizeSlideObjectLayout(objects: EditableSlideObject[], options: LayoutNormalizeOptions = {}) {
  if (!objects.length) return objects
  const normalized = objects.map((object) => normalizeObjectStyleAndSize(object))
  const originalById = new Map(objects.map((object) => [object.id, object]))
  const images = normalized.filter((object) => isImageLikeObject(object))
  const textObjects = normalized.filter((object) => !isImageLikeObject(object) && object.type !== "title")
  const hasImages = images.length > 0
  const titleObjects = normalized.filter((object) => object.type === "title")
  const placed = new Map<string, EditableSlideObject>()

  titleObjects.forEach((object) => {
    const bbox = normalizeObjectBBox(object, { x: 48, y: 34, width: 904, height: 58 })
    placed.set(object.id, {
      ...object,
      bbox: {
        ...bbox,
        height: Math.max(42, Math.min(bbox.height, 72)),
      },
    })
  })

  const contentX = hasImages ? 64 : 72
  const contentWidth = hasImages ? 500 : 856
  const imageX = 570
  const imageWidth = 380
  const gap = 16
  let contentCursor = Math.max(112, ...titleObjects.map((object) => Number(object.bbox?.y || 34) + Number(object.bbox?.height || 58) + 20))

  textObjects
    .sort((a, b) => Number(a.z || 0) - Number(b.z || 0))
    .forEach((object) => {
      const bbox = normalizeObjectBBox(object, {
        x: contentX,
        y: contentCursor,
        width: contentWidth,
        height: 96,
      })
      const shouldRepair = Boolean(options.force && shouldRepairObjectLayout(object, objects, hasImages, originalById.get(object.id)))
      const nextX = shouldRepair ? contentX : Math.min(Math.max(bbox.x, 24), CANVAS_WIDTH - Math.max(bbox.width, 80))
      const nextWidth = shouldRepair ? contentWidth : Math.min(Math.max(bbox.width, 80), CANVAS_WIDTH - nextX)
      const yForFit = shouldRepair ? contentCursor : bbox.y
      const fitHeight = fitObjectHeightToCanvas(
        { ...object, bbox: { ...bbox, x: nextX, y: yForFit, width: nextWidth } },
        estimateObjectHeight({ ...object, bbox: { ...bbox, width: nextWidth } }, objectFontSize(object, defaultFontSizeForObject(object)), objectLineHeight(object)),
      )
      const nextY = yForFit
      const maxHeight = Math.max(36, CANVAS_HEIGHT - nextY - 24)
      const nextObject = {
        ...object,
        bbox: {
          x: Math.round(nextX * 10) / 10,
          y: Math.round(nextY * 10) / 10,
          width: Math.round(nextWidth * 10) / 10,
          height: Math.round(Math.min(Math.max(fitHeight, bbox.height && !shouldRepair ? Math.min(bbox.height, maxHeight) : fitHeight), maxHeight) * 10) / 10,
        },
      }
      placed.set(object.id, nextObject)
      contentCursor = Math.max(contentCursor, nextObject.bbox.y + nextObject.bbox.height + gap)
    })

  images
    .sort((a, b) => Number(a.z || 0) - Number(b.z || 0))
    .forEach((object, index) => {
      const bbox = normalizeObjectBBox(object, {
        x: hasImages && textObjects.length ? imageX : 270,
        y: 126 + index * 140,
        width: hasImages && textObjects.length ? imageWidth : 460,
        height: images.length > 1 ? 118 : 240,
      })
      const repair = Boolean(options.force && shouldRepairObjectLayout(object, objects, hasImages, originalById.get(object.id)))
      const nextWidth = repair ? (textObjects.length ? imageWidth : 460) : Math.min(Math.max(bbox.width, 120), CANVAS_WIDTH - bbox.x)
      const nextX = repair ? (textObjects.length ? imageX : (CANVAS_WIDTH - nextWidth) / 2) : bbox.x
      const nextY = repair ? 126 + index * (images.length > 1 ? 132 : 156) : bbox.y
      const nextHeight = repair ? (images.length > 1 ? 118 : 240) : bbox.height
      placed.set(object.id, {
        ...object,
        bbox: {
          x: Math.round(Math.min(Math.max(nextX, 24), CANVAS_WIDTH - nextWidth - 24) * 10) / 10,
          y: Math.round(Math.min(Math.max(nextY, 86), CANVAS_HEIGHT - 80) * 10) / 10,
          width: Math.round(nextWidth * 10) / 10,
          height: Math.round(Math.min(Math.max(nextHeight, 80), CANVAS_HEIGHT - nextY - 24) * 10) / 10,
        },
      })
    })

  return normalized.map((object) => placed.get(object.id) || object)
}

function normalizeObjectStyleAndSize(object: EditableSlideObject): EditableSlideObject {
  const style = {
    ...(object.style || {}),
    fontSize: objectFontSize(object, defaultFontSizeForObject(object)),
    lineHeight: objectLineHeight(object),
  }
  const bbox = normalizeObjectBBox(object, { x: 72, y: 120, width: 500, height: 96 })
  const shouldAutoHeight = canAutoFitObject(object)
  return {
    ...object,
    style,
    bbox: {
      ...bbox,
      height: shouldAutoHeight
        ? fitObjectHeightToCanvas({ ...object, style, bbox }, estimateObjectHeight({ ...object, style, bbox }, objectFontSize({ ...object, style }, defaultFontSizeForObject(object)), objectLineHeight({ ...object, style })))
        : bbox.height,
    },
  }
}

function normalizeObjectBBox(object: EditableSlideObject, fallback: EditableSlideObject["bbox"]) {
  const bbox = object.bbox || fallback
  const x = clampCanvasNumber(bbox.x, fallback.x, 0, CANVAS_WIDTH - 24)
  const y = clampCanvasNumber(bbox.y, fallback.y, 0, CANVAS_HEIGHT - 24)
  const width = clampCanvasNumber(bbox.width, fallback.width, 48, CANVAS_WIDTH - x)
  const height = clampCanvasNumber(bbox.height, fallback.height, 36, CANVAS_HEIGHT - y)
  return { x, y, width, height }
}

function isImageLikeObject(object: EditableSlideObject | undefined) {
  return object?.type === "image" || object?.type === "placeholder"
}

function shouldRepairObjectLayout(
  object: EditableSlideObject,
  siblings: EditableSlideObject[],
  hasImages: boolean,
  sourceObject: EditableSlideObject = object,
) {
  const bbox = sourceObject.bbox || object.bbox
  const hasStyle = sourceObject.style && ("fontSize" in sourceObject.style || "lineHeight" in sourceObject.style)
  if (!bbox) return true
  if (bbox.x < 0 || bbox.y < 0 || bbox.x + bbox.width > CANVAS_WIDTH || bbox.y + bbox.height > CANVAS_HEIGHT) return true
  if (!hasStyle && canAutoFitObject(object)) return true
  if (object.type === "richText" && Number(bbox?.height || 0) >= 260) return true
  if (isImageLikeObject(object) && hasImages && bbox.x < 520) return true
  if (hasImages && !isImageLikeObject(object) && object.type !== "title" && Number(bbox?.width || 0) > 560) return true
  return siblings.some((candidate) => {
    if (candidate.id === object.id || candidate.type === "title") return false
    if (!boxesOverlap(bbox, candidate.bbox)) return false
    return true
  })
}

function boxesOverlap(a: EditableSlideObject["bbox"] | undefined, b: EditableSlideObject["bbox"] | undefined) {
  if (!a || !b) return false
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y
}

function mergeCanvasItemsIntoEditableModel(model: EditableSlideModel | null, slideIndex: number, items: CanvasItem[]) {
  if (!model) return model
  return {
    ...model,
    slides: model.slides.map((slide) => {
      if (slide.index !== slideIndex) return slide
      const byId = new Map(items.map((item) => [item.id, item]))
      const objects = (slide.objects || slide.items || []).map((object) => {
        const item = byId.get(object.id)
        if (!item) return object
        return {
          ...object,
          bbox: {
            x: Math.round(item.x * 10) / 10,
            y: Math.round(item.y * 10) / 10,
            width: Math.round(item.width * 10) / 10,
            height: Math.round(item.height * 10) / 10,
          },
          width_ratio: object.type === "image" || object.type === "placeholder" ? Math.min(Math.max(item.width / CANVAS_WIDTH, 0.05), 1) : object.width_ratio,
        }
      })
      return { ...slide, items: objects, objects }
    }),
    updated_at: new Date().toISOString(),
  }
}

function updateEditableSlideObject(
  model: EditableSlideModel | null,
  slideIndex: number,
  objectId: string,
  updater: (object: EditableSlideObject) => EditableSlideObject,
) {
  if (!model) return model
  return {
    ...model,
    slides: model.slides.map((slide) => {
      if (slide.index !== slideIndex) return slide
      const objects = (slide.objects || slide.items || []).map((object) => (object.id === objectId ? updater(object) : object))
      return { ...slide, title: objects.find((object) => object.type === "title")?.text || slide.title, items: objects, objects }
    }),
    updated_at: new Date().toISOString(),
  }
}

function updateEditableSlideObjectWithAutoFit(
  model: EditableSlideModel | null,
  slideIndex: number,
  objectId: string,
  patch: Partial<EditableSlideObject>,
) {
  return updateEditableSlideObject(model, slideIndex, objectId, (object) => {
    const mergedStyle = { ...(object.style || {}), ...(patch.style || {}) }
    const nextObject = {
      ...object,
      ...patch,
      style: mergedStyle,
    }
    const fontSize = objectFontSize(nextObject, defaultFontSizeForObject(nextObject))
    const lineHeight = objectLineHeight(nextObject)
    if (!patch.bbox && canAutoFitObject(nextObject)) {
      const estimatedHeight = fitObjectHeightToCanvas(nextObject, estimateObjectHeight(nextObject, fontSize, lineHeight))
      const styleChanged = Boolean(patch.style && ("fontSize" in patch.style || "lineHeight" in patch.style))
      const contentChanged = "text" in patch || "latex" in patch || "rich_html" in patch || "rows" in patch || "label" in patch
      if (styleChanged || contentChanged) {
        nextObject.bbox = {
          ...(nextObject.bbox || { x: 0, y: 0, width: 320, height: 80 }),
          height: estimatedHeight,
        }
      }
    }
    return nextObject
  })
}

function mutateEditableSlide(
  model: EditableSlideModel | null,
  slideIndex: number,
  mutator: (objects: EditableSlideObject[]) => EditableSlideObject[],
) {
  if (!model) return model
  return {
    ...model,
    slides: model.slides.map((slide) => {
      if (slide.index !== slideIndex) return slide
      const objects = mutator([...(slide.objects || slide.items || [])])
      return { ...slide, title: objects.find((object) => object.type === "title")?.text || slide.title, items: objects, objects }
    }),
    updated_at: new Date().toISOString(),
  }
}

function imageMatchesCanvasItem(image: SlideImage, item: CanvasItem, index: number) {
  if (item.id === imageCanvasId(image, index)) return true
  if (!item.ref) return false
  const keys = new Set([image.tex_ref, image.source_path, ...imageSourcePathKeys(image.tex_ref), ...imageSourcePathKeys(image.source_path)].filter(Boolean))
  return keys.has(item.ref)
}

function objectMatchesCanvasItem(object: EditableSlideObject | undefined, item: CanvasItem) {
  if (!object) return false
  if (object.id === item.id) return true
  if (!item.ref) return false
  const keys = new Set([object.tex_ref, object.source_path, object.asset_id, object.label, ...imageSourcePathKeys(object.tex_ref), ...imageSourcePathKeys(object.source_path)].filter(Boolean))
  return keys.has(item.ref)
}

function objectFontSize(object: EditableSlideObject | undefined, fallback = 18) {
  const raw = object?.style?.fontSize
  const numeric = typeof raw === "number" ? raw : typeof raw === "string" ? Number(raw) : NaN
  return Number.isFinite(numeric) ? Math.min(Math.max(numeric, 8), 64) : fallback
}

function defaultFontSizeForObject(object: EditableSlideObject | undefined, itemType?: CanvasItemKind) {
  if (object?.type === "title" || itemType === "title") return 28
  if (object?.type === "equation") return 24
  if (object?.type === "table") return 14
  if (object?.type === "callout") return 16
  return 18
}

function estimateCanvasTextHeight(text: string, width: number, fontSize: number, lineHeight: number, padding = 20, maxHeight = CANVAS_HEIGHT) {
  const safeWidth = Math.max(width - 10, 80)
  const explicitLines = Math.max(String(text || "").split(/\n/).length, 1)
  const wrappedLines = String(text || "")
    .split(/\n/)
    .reduce((total, line) => total + estimateWrappedLineCount(line, safeWidth, fontSize), 0)
  const lines = Math.max(explicitLines, wrappedLines, 1)
  return Math.min(Math.max(Math.ceil(lines * fontSize * lineHeight + padding), 42), maxHeight)
}

function objectLineHeight(object: EditableSlideObject | undefined) {
  const raw = object?.style?.lineHeight
  const numeric = typeof raw === "number" ? raw : typeof raw === "string" ? Number(raw) : NaN
  if (Number.isFinite(numeric)) return Math.min(Math.max(numeric, 1), 1.8)
  if (object?.type === "title") return 1.12
  if (object?.type === "equation") return 1.25
  if (object?.type === "table") return 1.35
  return 1.32
}

function objectColor(object: EditableSlideObject | undefined) {
  const raw = object?.style?.color || object?.style?.foreground || object?.style?.textColor
  if (typeof raw !== "string" || !raw.trim()) return undefined
  const color = raw.trim().toLowerCase()
  const named: Record<string, string> = {
    black: "#111111",
    gray: "#6b7280",
    grey: "#6b7280",
    darkgray: "#4b5563",
    darkgrey: "#4b5563",
    lightgray: "#9ca3af",
    lightgrey: "#9ca3af",
    red: "#dc2626",
    blue: "#2563eb",
    green: "#15803d",
    myblue: "#2864b4",
    myline: "#007470",
  }
  const mixedGray = /^(?:gray|grey)!(\d{1,3})$/.exec(color)
  if (mixedGray) {
    const percent = Math.min(Math.max(Number(mixedGray[1]), 0), 100)
    const channel = Math.round(255 - (percent / 100) * 255)
    return `rgb(${channel}, ${channel}, ${channel})`
  }
  if (/^#[0-9a-f]{3}(?:[0-9a-f]{3})?$/.test(color)) return color
  if (/^[a-z][a-z0-9-]*$/.test(color)) return named[color] || color
  return undefined
}

function objectTextStyle(object: EditableSlideObject | undefined, fallback = 18): CSSProperties {
  const color = objectColor(object)
  return {
    fontSize: objectFontSize(object, fallback),
    lineHeight: objectLineHeight(object),
    ...(color ? { color } : {}),
  }
}

function canAutoFitObject(object: EditableSlideObject | undefined) {
  return ["title", "richText", "textbox", "equation", "table", "callout"].includes(String(object?.type || ""))
}

function estimateObjectHeight(object: EditableSlideObject, fontSize: number, lineHeight = objectLineHeight(object)) {
  const bbox = object.bbox || { width: 320, height: 80, x: 0, y: 0 }
  const width = Math.max(Number(bbox.width || 320) - 10, 80)
  const text = String(object.text || object.latex || object.label || textFromRichHtml(object.rich_html) || "")
  if (object.type === "equation") {
    const rows = Math.max(text.split(/\n/).length, 1)
    return Math.ceil(fontSize * lineHeight * rows + fontSize * 1.45)
  }
  if (object.type === "table") {
    const rowCount = Math.max(object.rows?.length || 1, 1)
    return Math.ceil(rowCount * fontSize * lineHeight + 34)
  }
  const explicitLines = text.split(/\n/).length
  const wrappedLines = text
    .split(/\n/)
    .reduce((total, line) => total + estimateWrappedLineCount(line, width, fontSize), 0)
  const lines = Math.max(explicitLines, wrappedLines, 1)
  const padding = object.type === "title" ? 12 : object.type === "callout" ? 34 : 20
  return Math.ceil(lines * fontSize * lineHeight + padding)
}

function textFromRichHtml(value?: string) {
  return String(value || "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|li|tr|h[1-6])>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"")
    .trim()
}

function estimateWrappedLineCount(line: string, width: number, fontSize: number) {
  const content = line.replace(/^\s*(?:[-*•]|\d+\.)\s*/, "").trim()
  if (!content) return 1
  const lineUnits = Math.max(width / Math.max(fontSize, 8), 4)
  const usedUnits = Array.from(content).reduce((total, character) => total + characterVisualUnits(character), 0)
  return Math.max(Math.ceil(usedUnits / lineUnits), 1)
}

function characterVisualUnits(character: string) {
  if (/[\u2e80-\u9fff\uff00-\uffef]/.test(character)) return 1
  if (/\s/.test(character)) return 0.32
  if (/[A-Z0-9]/.test(character)) return 0.64
  return 0.56
}

function fitObjectHeightToCanvas(object: EditableSlideObject, intrinsicHeight: number) {
  const y = Number(object.bbox?.y || 0)
  const minHeight = object.type === "title" ? 42 : object.type === "table" ? 54 : object.type === "equation" ? 52 : 44
  const maxHeight = Math.max(minHeight, CANVAS_HEIGHT - y)
  return Math.min(Math.max(Math.ceil(intrinsicHeight), minHeight), maxHeight)
}

function autoFitEditableSlideObject(
  model: EditableSlideModel | null,
  slideIndex: number,
  objectId: string,
) {
  return updateEditableSlideObject(model, slideIndex, objectId, (object) => {
    if (!canAutoFitObject(object)) return object
    return {
      ...object,
      bbox: {
        ...(object.bbox || { x: 0, y: 0, width: 320, height: 80 }),
        height: fitObjectHeightToCanvas(
          object,
          estimateObjectHeight(object, objectFontSize(object, defaultFontSizeForObject(object)), objectLineHeight(object)),
        ),
      },
    }
  })
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

function withCanvasLayout(source: string, items: CanvasItem[]) {
  const safeItems = items.map((item) => ({
    id: item.id,
    type: item.type,
    ref: item.ref,
    x: Math.round(item.x * 10) / 10,
    y: Math.round(item.y * 10) / 10,
    width: Math.round(item.width * 10) / 10,
    height: Math.round(item.height * 10) / 10,
  }))
  const comment = `% KGTS_LAYOUT ${JSON.stringify({ items: safeItems })}`
  if (/^\s*%\s*KGTS_LAYOUT\s+{.*}\s*$/m.test(source)) {
    return source.replace(/^\s*%\s*KGTS_LAYOUT\s+{.*}\s*$/m, comment)
  }
  const beginMatch = /\\begin\{frame\}(?:\s*(?:<[^>]*>|\[[^\]]*\]))*(?:\s*\{(?:[^{}]|\\.|{[^{}]*})*\})?/.exec(source)
  if (!beginMatch) return `${comment}\n${source}`
  const insertAt = beginMatch.index + beginMatch[0].length
  return `${source.slice(0, insertAt)}\n${comment}${source.slice(insertAt)}`
}

function applyCanvasLayoutToTex(source: string, previousItems: CanvasItem[], nextItems: CanvasItem[], images: SlideImage[]) {
  let nextSource = withCanvasLayout(source, nextItems)
  const previousById = canvasLayoutById(previousItems)
  for (const item of nextItems) {
    if (item.type !== "image") continue
    const previous = previousById.get(item.id)
    if (previous && Math.abs(previous.width - item.width) < 0.5) continue
    const image = images.find((candidate, index) => imageMatchesCanvasItem(candidate, item, index))
    if (!image) continue
    nextSource = replaceImageWidth(nextSource, image, Math.min(Math.max(item.width / CANVAS_WIDTH, 0.05), 1))
  }
  return nextSource
}

function applyEditableCanvasLayoutToTex(
  source: string,
  previousItems: CanvasItem[],
  nextItems: CanvasItem[],
  images: SlideImage[],
  objects: EditableSlideObject[],
) {
  let nextSource = withCanvasLayout(source, nextItems)
  const previousById = canvasLayoutById(previousItems)
  for (const item of nextItems) {
    if (item.type !== "image") continue
    const previous = previousById.get(item.id)
    if (previous && Math.abs(previous.width - item.width) < 0.5) continue
    const image = images.find((candidate, index) => imageMatchesCanvasItem(candidate, item, index))
    if (image) {
      nextSource = replaceImageWidth(nextSource, image, Math.min(Math.max(item.width / CANVAS_WIDTH, 0.05), 1))
      continue
    }
    const object = objects.find((candidate) => objectMatchesCanvasItem(candidate, item))
    if (!object) continue
    const pseudoImage: SlideImage = {
      data_uri: null,
      width_emu: 0,
      height_emu: 0,
      left_emu: 0,
      top_emu: 0,
      source_path: object.source_path,
      tex_ref: object.tex_ref,
      width_ratio: object.width_ratio,
    }
    nextSource = replaceImageWidth(nextSource, pseudoImage, Math.min(Math.max(item.width / CANVAS_WIDTH, 0.05), 1))
  }
  return nextSource
}

function collectSlideImages(slide: PptSlideDetail) {
  const images: SlideImage[] = []
  const seen = new Set<string>()
  const add = (image?: SlideImage) => {
    if (!image) return
    const key = image.source_path || image.tex_ref || `${images.length}`
    if (seen.has(key)) return
    seen.add(key)
    images.push(image)
  }
  ;(slide.images || []).forEach(add)
  ;(slide.layout?.columns || []).forEach((column) => (column.images || []).forEach(add))
  return images
}

function TeacherPreparePage() {
  const { chapterId, nodeId, courseId } = Route.useSearch()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<GenerationMode>("graph")
  const [file, setFile] = useState<File | null>(null)
  const [style, setStyle] = useState("引导式教学")
  const [chapterTitle, setChapterTitle] = useState("")
  const [durationDraftMinutes, setDurationDraftMinutes] = useState(DEFAULT_LECTURE_DURATION_MINUTES)
  const [targetDurationMinutes, setTargetDurationMinutes] = useState(DEFAULT_LECTURE_DURATION_MINUTES)
  const [preview, setPreview] = useState<PptPreviewResponse | null>(null)
  const [editableModel, setEditableModel] = useState<EditableSlideModel | null>(null)
  const [assetMap, setAssetMap] = useState<Record<string, CoursewareAsset>>({})
  const [projectId, setProjectId] = useState("")
  const [loadedRecordId, setLoadedRecordId] = useState("")
  const [texContent, setTexContent] = useState("")
  const [texDraft, setTexDraft] = useState("")
  const [frameDrafts, setFrameDrafts] = useState<Record<number, string>>({})
  const [pptArtifact, setPptArtifact] = useState<PptArtifact | null>(null)
  const [slideLectures, setSlideLectures] = useState<PptSlideLecture[]>([])
  const [selectedIndex, setSelectedIndex] = useState(1)
  const [pptNodeIds, setPptNodeIds] = useState<string[]>(nodeId ? [nodeId] : [])
  const [lectureNodeIds, setLectureNodeIds] = useState<string[]>(nodeId ? [nodeId] : [])
  const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set(["toc::root"]))
  const [treeSearch, setTreeSearch] = useState("")
  const [teacherGuidance, setTeacherGuidance] = useState("")
  const [allowNoNodeGeneration, setAllowNoNodeGeneration] = useState(false)
  const [slideFeedbackDrafts, setSlideFeedbackDrafts] = useState<Record<number, string>>({})
  const [styleReference, setStyleReference] = useState<CoursewareStyleReference | null>(null)
  const [pptSourceScope, setPptSourceScope] = useState<GraphSourceScope | null>(null)
  const [lectureSourceScope, setLectureSourceScope] = useState<GraphSourceScope | null>(null)
  const [driftReport, setDriftReport] = useState<SourceDriftReport | null>(null)
  const [isGeneratingSlideLecturesBatch, setIsGeneratingSlideLecturesBatch] = useState(false)
  const [activeSlideLectureJob, setActiveSlideLectureJob] = useState<StoredSlideLectureJob | null>(() => readStoredSlideLectureJob())
  const [activeCourseAudioJob, setActiveCourseAudioJob] = useState<StoredCourseAudioJob | null>(() => readStoredCourseAudioJob())
  const [isPreviewFullscreen, setIsPreviewFullscreen] = useState(false)
  const [courseAudioProgress, setCourseAudioProgress] = useState<CourseAudioProgress>(emptyCourseAudioProgress)
  const [graphScopeEnabled, setGraphScopeEnabled] = useState(() => !chapterId)
  const [status, setStatus] = useState("")
  const courseAudioAbortRef = useRef(false)
  const recoveredCourseAudioKeyRef = useRef("")

  // Saved courseware opens directly in the editor and does not need the graph tree.
  useEffect(() => {
    // Existing chapters/courseware open in the editor without the large graph
    // payload. Graph context is enabled only after an explicit node selection.
    setGraphScopeEnabled(!chapterId)
  }, [chapterId, nodeId])

  const previewPpt = usePreviewPpt()
  const previewTex = usePreviewTex()
  const uploadCoursewareAssets = useUploadCoursewareAssets()
  const uploadCoursewareStyleReference = useUploadCoursewareStyleReference()
  const saveCoursewareProject = useSaveCoursewareProject()
  const exportCoursewarePptx = useExportCoursewarePptx()
  const generateUploadedPptLectures = useGeneratePptLectures()
  const generatePptTex = useGeneratePptTex()
  const generateSlideLectures = useGenerateSlideLectures()
  const planSlideSpeech = usePlanSlideSpeech()
  const deleteCoursewareProject = useDeleteCoursewareProject()
  const deleteChapter = useDeleteChapter()
  const saveChapter = useSaveChapter()
  const saveLecture = useSaveLecture()
  const savedCoursewareProject = useCoursewareProject(chapterId.startsWith("cw_") ? chapterId : "", courseId)
  const { data: coursewareProjectsData, isLoading: coursewareProjectsLoading } = useCoursewareProjects(courseId)
  const savedTeacherChapter = useTeacherChapter(chapterId && !chapterId.startsWith("cw_") ? chapterId : "", true)
  const allCoursewareProjects = coursewareProjectsData?.projects || []
  const coursewareProjects = useMemo(() => {
    const latestByKey = new Map<string, CoursewareProject>()
    for (const project of allCoursewareProjects) {
      const key = coursewareProjectDedupeKey(project)
      const previous = latestByKey.get(key)
      if (!previous || String(project.updated_at || "") > String(previous.updated_at || "")) {
        latestByKey.set(key, project)
      }
    }
    return Array.from(latestByKey.values()).sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))
  }, [allCoursewareProjects])
  const duplicateCoursewareCount = Math.max(0, allCoursewareProjects.length - coursewareProjects.length)
  const selectedCoursewareProject = allCoursewareProjects.find((project) => project.id === projectId || project.id === chapterId)
  const selectedCoursewareProjectIsHidden = Boolean(
    selectedCoursewareProject && !coursewareProjects.some((project) => project.id === selectedCoursewareProject.id),
  )
  const shouldLoadGraphScope = graphScopeEnabled
  const { data: scopeTreeData, isLoading: scopeTreeLoading } = useGraphScopeTree(shouldLoadGraphScope)
  const { data: pptNodeContext, isLoading: pptContextLoading } = useGraphNodeContext(pptNodeIds, shouldLoadGraphScope)
  const { data: lectureNodeContext, isLoading: lectureContextLoading } = useGraphNodeContext(lectureNodeIds, shouldLoadGraphScope)

  const selectedSlide = preview?.slides.find((slide) => slide.index === selectedIndex)
  const selectedLecture = slideLectures.find((lecture) => lecture.index === selectedIndex)
  const selectedSlideFeedback = selectedSlide ? slideFeedbackDrafts[selectedSlide.index] || "" : ""
  const nodes = useMemo(() => scopeTreeData?.nodes || [], [scopeTreeData?.nodes])
  const relationships = useMemo(() => scopeTreeData?.relationships || [], [scopeTreeData?.relationships])
  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes])
  const tree = useMemo(() => buildGraphScopeTree(nodes, relationships, treeSearch), [nodes, relationships, treeSearch])
  const parentByChild = useMemo(() => buildParentByChild(tree), [tree])
  const defaultExpandedNodeIds = useMemo(() => {
    const root = tree.find((node) => node.id === "toc::root")
    if (!root) return ["toc::root"]
    const next = new Set(["toc::root"])
    root.children.forEach((node) => {
      next.add(node.id)
    })
    return Array.from(next)
  }, [tree])
  const isGraphLoading = shouldLoadGraphScope && scopeTreeLoading
  const isGeneratingPpt = generatePptTex.isPending || previewPpt.isPending || previewTex.isPending
  const isGeneratingLectures = isGeneratingSlideLecturesBatch || generateSlideLectures.isPending || generateUploadedPptLectures.isPending
  const hasGeneratedSlideLectures = useMemo(() => slideLectures.some(hasUsableSlideLecture), [slideLectures])
  const durationDraftChanged = durationDraftMinutes !== targetDurationMinutes
  const selectedLectureError = selectedLecture?.error?.trim() || ""
  const selectedSpeechCueCount = selectedLecture?.speech_cues?.length || 0
  const lectureStatusText = hasUsableSlideLecture(selectedLecture)
    ? "已生成"
    : selectedLectureError
      ? "生成失败"
      : isGeneratingLectures
        ? "正在生成"
        : "未生成"
  const hasTexDraftChanges = Boolean(texContent) && texDraft !== texContent
  const selectedFrameDraft = selectedSlide ? frameDrafts[selectedSlide.index] ?? selectedSlide.source_tex ?? "" : ""
  const hasSelectedFrameChanges = Boolean(selectedSlide?.source_tex) && selectedFrameDraft !== (selectedSlide?.source_tex || "")
  const totalTargetChars = Math.round(targetDurationMinutes * DEFAULT_SPEECH_RATE_CPM)
  const generatedLectureChars = useMemo(
    () => slideLectures.reduce((sum, lecture) => sum + (lecture.estimated_chars ?? lecture.lecture?.length ?? 0), 0),
    [slideLectures],
  )
  const generatedLectureSeconds = Math.round((generatedLectureChars / DEFAULT_SPEECH_RATE_CPM) * 60)
  const currentLectureSeconds =
    selectedLecture?.estimated_duration_seconds ?? Math.round(((selectedLecture?.estimated_chars ?? selectedLecture?.lecture?.length ?? 0) / DEFAULT_SPEECH_RATE_CPM) * 60)
  const lecturePlayback = useLecturePlayback({
    segmentCount: preview?.slides.length || 0,
    initialSegment: Math.max(selectedIndex - 1, 0),
    chapterId: chapterId || preview?.chapter_title,
    getSegmentId: (segment) => {
      const slide = preview?.slides[segment]
      return slide ? `slide-${slide.index}` : `slide-${segment + 1}`
    },
    getSegmentText: (segment) => {
      const slide = preview?.slides[segment]
      if (!slide) return ""
      const lecture = slideLectures.find((item) => item.index === slide.index)
      return lecture?.lecture || slide.notes || slide.content || slide.raw_text || ""
    },
    getSegmentSpeechCues: (segment) => {
      const slide = preview?.slides[segment]
      if (!slide) return undefined
      return slideLectures.find((item) => item.index === slide.index)?.speech_cues
    },
  })
  const imageBySourcePath = useMemo(() => {
    const map = new Map<string, NonNullable<PptSlideDetail["images"]>[number]>()
    for (const slide of preview?.slides || []) {
      for (const image of slide.images || []) {
        if (image.source_path && image.data_uri) {
          for (const key of imageSourcePathKeys(image.source_path)) {
            map.set(key, image)
          }
        }
      }
    }
    return map
  }, [preview?.slides])
  const mergedAssetMap = useMemo(
    () => ({ ...(editableModel?.assets || {}), ...assetMap }),
    [assetMap, editableModel?.assets],
  )

  const mergedLecture = useMemo(
    () =>
      slideLectures
        .map((item) => {
          const title = item.title || `第 ${item.index} 页`
          const body = item.lecture?.trim() || "_本页未生成文案_"
          return `## 第 ${item.index} 页：${title}\n\n${body}`
        })
        .join("\n\n---\n\n"),
    [slideLectures],
  )

  const selectPptNode = (id: string) => {
    setPptNodeIds((previous) => {
      const resolved = resolveNextGraphScopeSelection(previous, id, parentByChild)
      const sorted = sortGraphScopeNodeIds(resolved, nodeById)
      if (!preview?.slides.length) setLectureNodeIds(sorted)
      return sorted
    })
  }

  const selectLectureNode = (id: string) => {
    setLectureNodeIds((previous) => {
      const resolved = resolveNextGraphScopeSelection(previous, id, parentByChild)
      return sortGraphScopeNodeIds(resolved, nodeById)
    })
  }

  const toggleNode = (id: string) => {
    setExpandedNodeIds((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  useEffect(() => {
    if (!defaultExpandedNodeIds.length) return
    setExpandedNodeIds((previous) => {
      if (previous.size > 1 || !previous.has("toc::root")) return previous
      const next = new Set(previous)
      defaultExpandedNodeIds.forEach((id) => next.add(id))
      return next
    })
  }, [defaultExpandedNodeIds])

  useEffect(() => {
    const slidePosition = Math.max(
      0,
      (preview?.slides || []).findIndex((slide) => slide.index === selectedIndex),
    )
    lecturePlayback.reset(slidePosition)
  }, [preview?.slides, selectedIndex])

  useEffect(() => {
    const jobId = preview?.render_job_id
    if (!jobId || preview?.render_status === "completed" || preview?.render_status === "failed") return
    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      try {
        const job = await getCoursewareRenderJob(jobId)
        if (cancelled) return
        if (job.status === "completed" || job.status === "failed") {
          setPreview((previous) => {
            if (!previous || previous.render_job_id !== jobId) return previous
            const pages = job.rendered_pages || []
            const renderError = job.render_error || ""
            return attachRenderedPagesToPreview({
              ...previous,
              render_status: job.status,
              rendered_pages: pages,
              render_source: pages.length ? "latex_project" : previous.render_source,
              render_error: renderError || undefined,
              warning: [previous.warning, renderError].filter(Boolean).join("；") || undefined,
            })
          })
          setStatus(
            job.status === "completed"
              ? `PDF 预览渲染完成${job.rendered_page_count ? `：${job.rendered_page_count} 页` : ""}`
              : `PDF 预览渲染失败，已保留解析内容：${job.render_error || "任务失败"}`,
          )
          return
        }
        setPreview((previous) => (previous && previous.render_job_id === jobId ? { ...previous, render_status: job.status } : previous))
        setStatus(job.status === "queued" ? "课件已解析，PDF 预览正在排队" : "课件已解析，PDF 预览渲染中")
        timer = window.setTimeout(poll, 2500)
      } catch (error) {
        if (cancelled) return
        setStatus(`PDF 预览任务查询失败，将继续重试：${errorMessage(error)}`)
        timer = window.setTimeout(poll, 5000)
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [preview?.render_job_id])

  useEffect(() => {
    const project = savedCoursewareProject.data?.project
    if (!chapterId || !chapterId.startsWith("cw_") || !project || loadedRecordId === project.id) return
    restoreCoursewareProject(project, "project")
  }, [chapterId, loadedRecordId, savedCoursewareProject.data?.project])

  useEffect(() => {
    const chapter = savedTeacherChapter.data?.chapter
    if (!chapterId || chapterId.startsWith("cw_") || !chapter || loadedRecordId === chapter.id) return
    const project = chapterToCoursewareProject(chapter)
    if (project) {
      restoreCoursewareProject(project, "chapter")
      setSlideLectures(chapter.slide_lectures || [])
      setLectureNodeIds(chapter.lecture_source_node_ids || chapter.source_node_ids || [])
      setPptNodeIds(chapter.ppt_source_node_ids || chapter.source_node_ids || [])
      restoreLectureTiming(chapter)
    }
  }, [chapterId, loadedRecordId, savedTeacherChapter.data?.chapter])

  useEffect(() => {
    if (!activeSlideLectureJob) {
      writeStoredSlideLectureJob(null)
      return
    }
    writeStoredSlideLectureJob(activeSlideLectureJob)
  }, [activeSlideLectureJob])

  useEffect(() => {
    if (!activeSlideLectureJob) return
    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      try {
        const job = await getSlideLectureJob(activeSlideLectureJob.jobId)
        if (cancelled) return
        if (job.status === "completed" && job.result) {
          const result = job.result
          if (result.success) {
            if (result.slide_lectures?.length) setSlideLectures(result.slide_lectures)
            if (result.source_scope) setLectureSourceScope(result.source_scope)
            if (result.drift_report) setDriftReport(result.drift_report)
            const nextSelected =
              result.slide_lectures?.find((lecture) => lecture.lecture?.trim())?.index ||
              activeSlideLectureJob.selectedIndex ||
              selectedIndex
            setSelectedIndex(nextSelected)
            setStatus(result.warning || "已完成逐页讲解生成")
          } else {
            setStatus(result.message || result.error || "逐页讲解生成失败")
          }
          setIsGeneratingSlideLecturesBatch(false)
          setActiveSlideLectureJob(null)
          return
        }
        if (job.status === "failed") {
          setStatus(`逐页讲解生成失败：${job.error || job.message || "任务失败"}`)
          setIsGeneratingSlideLecturesBatch(false)
          setActiveSlideLectureJob(null)
          return
        }
        setIsGeneratingSlideLecturesBatch(true)
        setStatus(formatSlideLectureJobProgress(job))
        timer = window.setTimeout(poll, 3000)
      } catch (error) {
        if (cancelled) return
        setStatus(`逐页讲解任务恢复失败：${errorMessage(error)}`)
        setIsGeneratingSlideLecturesBatch(false)
        setActiveSlideLectureJob(null)
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [activeSlideLectureJob, selectedIndex])

  useEffect(() => {
    if (!activeCourseAudioJob) {
      writeStoredCourseAudioJob(null)
      return
    }
    writeStoredCourseAudioJob(activeCourseAudioJob)
    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      try {
        const job = await getCourseTtsJob(activeCourseAudioJob.jobId)
        if (cancelled) return
        const progress = courseAudioProgressFromJob(job)
        setCourseAudioProgress(progress)
        if (job.status === "completed") {
          setStatus(job.message || `已生成全课语音：${job.ready_chunks}/${job.total_chunks} 段，缓存命中 ${job.cache_hits} 段`)
          setActiveCourseAudioJob(null)
          writeStoredCourseAudioJob(null)
          return
        }
        if (job.status === "failed" || job.status === "cancelled") {
          setStatus(progress.error || job.message || "全课语音任务已结束")
          setActiveCourseAudioJob(null)
          writeStoredCourseAudioJob(null)
          return
        }
        setStatus(job.message || "全课语音生成中，关闭网页后会继续生成")
        timer = window.setTimeout(poll, 3000)
      } catch (error) {
        if (cancelled) return
        const message = `全课语音任务恢复失败，稍后重试：${errorMessage(error)}`
        setStatus(message)
        setCourseAudioProgress((previous) => ({ ...previous, running: true, message }))
        timer = window.setTimeout(poll, 5000)
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [activeCourseAudioJob])

  useEffect(() => {
    if (activeCourseAudioJob) return
    const chapterKey = (chapterId || preview?.chapter_title || chapterTitle || "").trim()
    if (!chapterKey || recoveredCourseAudioKeyRef.current === chapterKey) return
    recoveredCourseAudioKeyRef.current = chapterKey
    let cancelled = false

    const recover = async () => {
      try {
        const response = await getLatestCourseTtsJob(chapterKey)
        if (cancelled || !response.job) return
        const job = response.job
        const progress = courseAudioProgressFromJob(job)
        setCourseAudioProgress(progress)
        if (["queued", "running", "stopping"].includes(job.status)) {
          const storedJob = {
            jobId: job.job_id,
            chapterId: chapterKey,
            title: chapterTitle || preview?.chapter_title || "全课语音",
            createdAt: job.created_at || new Date().toISOString(),
          }
          setActiveCourseAudioJob(storedJob)
          writeStoredCourseAudioJob(storedJob)
          setStatus(job.message || "已恢复正在生成的全课语音任务")
          return
        }
        if (job.status === "completed") {
          setStatus(job.message || `已生成全课语音：${job.ready_chunks}/${job.total_chunks} 段，缓存命中 ${job.cache_hits} 段`)
        }
      } catch {
        // Recovery is opportunistic; normal generation remains available.
      }
    }

    void recover()
    return () => {
      cancelled = true
    }
  }, [activeCourseAudioJob, chapterId, chapterTitle, preview?.chapter_title])

  useEffect(() => {
    if (!isPreviewFullscreen) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsPreviewFullscreen(false)
    }
    document.body.style.overflow = "hidden"
    window.addEventListener("keydown", handleKeyDown)
    return () => {
      document.body.style.overflow = ""
      window.removeEventListener("keydown", handleKeyDown)
    }
  }, [isPreviewFullscreen])

  const resetGeneratedLectures = () => {
    lecturePlayback.pause()
    courseAudioAbortRef.current = true
    if (activeCourseAudioJob) {
      void stopCourseTtsJob(activeCourseAudioJob.jobId).catch(() => undefined)
    }
    setCourseAudioProgress(emptyCourseAudioProgress)
    setActiveCourseAudioJob(null)
    writeStoredCourseAudioJob(null)
    setActiveSlideLectureJob(null)
    setSlideLectures([])
    setLectureSourceScope(null)
    setDriftReport(null)
  }

  const resetTexState = (content: string) => {
    const normalized = normalizeTexNewlines(content)
    setTexContent(normalized)
    setTexDraft(normalized)
    setFrameDrafts({})
  }

  const currentEditableModelForSave = (title: string) =>
    editableModel
      ? {
          ...editableModel,
          title,
          source_tex: texContent || editableModel.source_tex || "",
          assets: mergedAssetMap,
        }
      : undefined

  const effectiveCoursewareTitle = (...fallbacks: Array<string | null | undefined>) =>
    chapterTitle.trim() ||
    preview?.chapter_title?.trim() ||
    fallbacks.map((value) => String(value || "").trim()).find(Boolean) ||
    "未命名课件"

  const applyPreviewResult = (result: PptPreviewResponse, fallbackTitle?: string) => {
    const editable = normalizeEditableModelLayout(result.editable_model || null)
    setPreview(attachRenderedPagesToPreview(result))
    setEditableModel(editable)
    setAssetMap(result.asset_map || editable?.assets || {})
    resetTexState(result.tex_content || result.source_tex || "")
    setChapterTitle(result.chapter_title || fallbackTitle || chapterTitle)
    setSelectedIndex(result.slides[0]?.index || 1)
  }

  const restoreCoursewareProject = (project: CoursewareProject, source: "project" | "chapter") => {
    const restored = coursewareProjectToPreview(project)
    if (!restored) {
      setStatus("已找到保存记录，但其中没有可恢复的课件页面或 TeX")
      return
    }
    setMode("upload")
    setFile(null)
    setLoadedRecordId(project.id)
    setProjectId(source === "project" ? project.id : "")
    applyPreviewResult(restored, project.title)
    setPptArtifact(project.ppt_artifact || null)
    setPptNodeIds(project.source_node_ids || [])
    setLectureNodeIds(project.source_node_ids || [])
    setSlideLectures([])
    setLectureSourceScope(null)
    setDriftReport(null)
    restoreLectureTiming(project)
    setStatus(`已恢复保存的课件：${project.title || project.id}`)
  }

  const restoreLectureTiming = (record: {
    lecture_target_duration_minutes?: number
    lecture_speech_rate_cpm?: number
    lecture_pacing?: { target_duration_minutes?: number }
  }) => {
    const restoredDuration = clampLectureDurationMinutes(
      Number(record.lecture_target_duration_minutes ?? record.lecture_pacing?.target_duration_minutes ?? DEFAULT_LECTURE_DURATION_MINUTES),
    )
    setTargetDurationMinutes(restoredDuration)
    setDurationDraftMinutes(restoredDuration)
  }

  const currentLecturePacingForSave = () => ({
    target_duration_minutes: targetDurationMinutes,
    speech_rate_cpm: DEFAULT_SPEECH_RATE_CPM,
    total_target_chars: totalTargetChars,
    estimated_chars: generatedLectureChars,
    estimated_duration_seconds: generatedLectureSeconds,
  })

  const persistLectureTiming = async (duration: number, options: { clearLectures?: boolean } = {}) => {
    if (!preview || !chapterId || chapterId.startsWith("cw_")) return false
    const existingChapter = savedTeacherChapter.data?.chapter
    if (!existingChapter) return false
    const title = effectiveCoursewareTitle(existingChapter.title, "未命名PPT")
    const saveSourceNodeIds = lectureNodeIds.length ? lectureNodeIds : pptNodeIds
    const saveSourceScope = lectureSourceScope || pptSourceScope || (lectureNodeContext?.success ? lectureNodeContext.scope : existingChapter.source_scope)
    await saveChapter.mutateAsync({
      chapter_id: existingChapter.id || chapterId,
      title,
      content: preview.full_text || existingChapter.content,
      source_type: existingChapter.source_type || (mode === "graph" ? "graph_ppt_tex" : "courseware"),
      source_node_ids: saveSourceNodeIds.length ? saveSourceNodeIds : existingChapter.source_node_ids,
      source_scope: saveSourceScope,
      ppt_slides: preview.slides,
      slide_lectures: options.clearLectures ? [] : slideLectures,
      tex_content: texContent || existingChapter.tex_content,
      editable_model: currentEditableModelForSave(title) || existingChapter.editable_model,
      asset_map: Object.keys(mergedAssetMap).length ? mergedAssetMap : existingChapter.asset_map,
      rendered_pages: preview.rendered_pages || existingChapter.rendered_pages,
      render_source: preview.render_source || existingChapter.render_source,
      render_error: preview.render_error ?? existingChapter.render_error,
      ppt_artifact: pptArtifact || existingChapter.ppt_artifact,
      ppt_source_node_ids: pptNodeIds.length ? pptNodeIds : existingChapter.ppt_source_node_ids,
      lecture_source_node_ids: saveSourceNodeIds.length ? saveSourceNodeIds : existingChapter.lecture_source_node_ids,
      lecture_target_duration_minutes: duration,
      lecture_speech_rate_cpm: DEFAULT_SPEECH_RATE_CPM,
      lecture_pacing: {
        ...currentLecturePacingForSave(),
        target_duration_minutes: duration,
        total_target_chars: Math.round(duration * DEFAULT_SPEECH_RATE_CPM),
      },
    })
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["teacher-chapter", existingChapter.id || chapterId] }),
      queryClient.invalidateQueries({ queryKey: ["teacher-chapters"] }),
      queryClient.invalidateQueries({ queryKey: ["student-chapters"] }),
    ])
    return true
  }

  const handleConfirmLectureDuration = async () => {
    const nextDuration = clampLectureDurationMinutes(durationDraftMinutes)
    const changed = nextDuration !== targetDurationMinutes
    setDurationDraftMinutes(nextDuration)
    setTargetDurationMinutes(nextDuration)
    if (changed) resetGeneratedLectures()
    try {
      const persisted = await persistLectureTiming(nextDuration, { clearLectures: changed })
      setStatus(
        persisted
          ? `已确认并保存讲解时长：${nextDuration} 分钟。下次打开该课程会继续使用该时长。`
          : `已确认讲解时长：${nextDuration} 分钟。保存为课程后会持久化。`,
      )
    } catch (error) {
      setStatus(`讲解时长已在当前页面确认，但保存失败：${errorMessage(error)}`)
    }
  }

  const handleGeneratePptTex = async () => {
    if (!pptNodeIds.length && !allowNoNodeGeneration && !chapterTitle.trim()) {
      setStatus("请先选择节点，或勾选无节点自动生成文案并填写标题")
      return
    }
    setGraphScopeEnabled(true)
    setMode("graph")
    setStatus("")
    resetGeneratedLectures()
    const result = await generatePptTex.mutateAsync({
      chapter_title: chapterTitle.trim() || undefined,
      content: [chapterTitle.trim(), teacherGuidance.trim()].filter(Boolean).join("\n\n") || undefined,
      allow_no_node: allowNoNodeGeneration,
      course_id: courseId || undefined,
      style,
      source_node_ids: pptNodeIds,
      graph_scope: "subtree",
      teacher_guidance: teacherGuidance,
      style_reference: styleReference,
      max_slides: 12,
    })
    applyPreviewResult({
      success: result.success,
      chapter_title: result.chapter_title,
      slide_count: result.slide_count,
      slides: result.slides,
      full_text: result.full_text,
      tex_content: result.tex_content,
      editable_model: result.editable_model,
      asset_map: result.asset_map,
      layout: result.layout,
      source_tex: result.source_tex,
      rendered_pages: result.rendered_pages,
      render_source: result.render_source,
      render_error: result.render_error,
      warning: result.warning,
      error: result.error,
    })
    setPptArtifact(result.ppt_artifact || null)
    setPptSourceScope(result.source_scope || null)
    setLectureNodeIds(result.source_node_ids?.length ? result.source_node_ids : pptNodeIds)
    setStatus(result.render_error ? `LaTeX 编译未成功，当前为解析预览：${result.render_error}` : result.warning || "已根据图谱课程树生成 PPT/TeX 页面内容")
  }

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = event.target.files?.[0]
    if (!selectedFile) return
    setMode("upload")
    setFile(selectedFile)
    setProjectId("")
    setLoadedRecordId("")
    resetTexState("")
    setEditableModel(null)
    setAssetMap({})
    setPptArtifact(null)
    resetGeneratedLectures()
    setStatus("")
    const result = await previewPpt.mutateAsync(selectedFile)
    applyPreviewResult(result, selectedFile.name.replace(/\.[^.]+$/, ""))
    setLectureNodeIds(pptNodeIds)
    setStatus(
      result.render_job_id
        ? "课件已解析，PDF 预览正在排队"
        : result.warning || "",
    )
  }

  const handleGenerateLectures = async () => {
    if (!preview?.slides.length) return
    setStatus("")
    if (mode === "upload" && file && !texContent) {
      const result = await generateUploadedPptLectures.mutateAsync({
        file,
        style,
        courseId: courseId || undefined,
        targetDurationMinutes,
        speechRateCpm: DEFAULT_SPEECH_RATE_CPM,
        sourceNodeIds: lectureNodeIds.length ? lectureNodeIds : pptNodeIds,
        teacherGuidance,
        allowNoNode: allowNoNodeGeneration,
      })
      if (!result.success) {
        setStatus(result.message || result.error || "上传课件逐页讲解生成失败")
        return
      }
      applyPreviewResult({
        success: result.success,
        chapter_title: result.chapter_title,
        slide_count: result.slide_count,
        slides: result.slides,
        full_text: result.full_text,
        tex_content: result.tex_content,
        editable_model: result.editable_model,
        asset_map: result.asset_map,
        layout: result.layout,
        source_tex: result.source_tex,
        missing_image_refs: result.missing_image_refs,
        rendered_pages: result.rendered_pages,
        render_source: result.render_source,
        render_error: result.render_error,
        warning: result.warning,
        error: result.error,
      })
      setSlideLectures(result.slide_lectures || [])
      setLectureSourceScope(result.source_scope || null)
      setStatus(result.warning || result.message || "已生成上传 PPT 的逐页讲解")
      return
    }

    const sourceNodeIds = lectureNodeIds.length ? lectureNodeIds : pptNodeIds
    setSlideLectures([])
    setIsGeneratingSlideLecturesBatch(true)
    setActiveSlideLectureJob(null)
    try {
      setStatus("正在分配每页字数并逐页生成讲解...")
      const result = await generateSlideLectures.mutateAsync({
        chapter_title: effectiveCoursewareTitle(),
        course_id: courseId || undefined,
        allow_no_node: allowNoNodeGeneration,
        slides: preview.slides.map(compactSlideForLectureRequest),
        tex_content: texContent,
        style,
        target_duration_minutes: targetDurationMinutes,
        speech_rate_cpm: DEFAULT_SPEECH_RATE_CPM,
        source_node_ids: sourceNodeIds,
        graph_scope: "subtree",
        teacher_guidance: teacherGuidance,
        style_reference: styleReference,
        ppt_source_node_ids: pptNodeIds,
        ppt_source_scope: pptSourceScope,
        onJobStarted: (job) => {
          setActiveSlideLectureJob({
            jobId: job.job_id,
            title: effectiveCoursewareTitle(),
            createdAt: job.created_at || new Date().toISOString(),
            selectedIndex,
          })
        },
        onProgress: (job) => {
          if (job.status === "running") {
            setStatus(formatSlideLectureJobProgress(job))
          }
        },
      })
      if (!result.success) {
        if (result.slide_lectures?.length) {
          setSlideLectures(result.slide_lectures)
        }
        setStatus(result.message || result.error || "逐页讲解生成失败")
        return
      }
      setSlideLectures(result.slide_lectures || [])
      setSelectedIndex(result.slide_lectures?.find((lecture) => lecture.lecture?.trim())?.index || preview.slides[0]?.index || 1)
      setLectureSourceScope(result.source_scope || null)
      setDriftReport(result.drift_report || null)
      setStatus(result.warning || "已完成逐页讲解生成")
      setActiveSlideLectureJob(null)
    } catch (error) {
      setStatus(`逐页讲解生成失败：${errorMessage(error)}`)
      setActiveSlideLectureJob(null)
    } finally {
      setIsGeneratingSlideLecturesBatch(false)
    }
  }

  const handleSelectedSlideFeedbackChange = (value: string) => {
    if (!selectedSlide) return
    setSlideFeedbackDrafts((drafts) => ({
      ...drafts,
      [selectedSlide.index]: value,
    }))
  }

  const handleRegenerateCurrentLecture = async () => {
    if (!preview?.slides.length || !selectedSlide) return
    setStatus("")
    const sourceNodeIds = lectureNodeIds.length ? lectureNodeIds : pptNodeIds
    const slideIndex = selectedSlide.index
    const pageFeedback = selectedSlideFeedback.trim()
    try {
      const result = await generateSlideLectures.mutateAsync({
        chapter_title: effectiveCoursewareTitle(),
        course_id: courseId || undefined,
        slides: [compactSlideForLectureRequest(selectedSlide)],
        tex_content: "",
        style,
        target_duration_minutes: estimateSlideDurationMinutes(selectedSlide, preview.slides, targetDurationMinutes),
        speech_rate_cpm: DEFAULT_SPEECH_RATE_CPM,
        source_node_ids: sourceNodeIds,
        graph_scope: "subtree",
        teacher_guidance: teacherGuidance,
        slide_feedback: pageFeedback ? { [slideIndex]: pageFeedback } : undefined,
        style_reference: styleReference,
        target_slide_indices: [slideIndex],
        existing_slide_lectures: slideLectures,
        ppt_source_node_ids: pptNodeIds,
        ppt_source_scope: pptSourceScope,
        onJobStarted: (job) => {
          setActiveSlideLectureJob({
            jobId: job.job_id,
            title: `${effectiveCoursewareTitle()} · 第 ${slideIndex} 页`,
            createdAt: job.created_at || new Date().toISOString(),
            selectedIndex: slideIndex,
          })
        },
        onProgress: (job) => {
          if (job.status === "running") {
            setStatus(formatSlideLectureJobProgress(job))
          }
        },
      })
      if (!result.success) {
        throw new Error(result.message || result.error || `第 ${slideIndex} 页讲解生成失败`)
      }
      setSlideLectures(mergeSlideLectures(slideLectures, result.slide_lectures || []))
      setLectureSourceScope(result.source_scope || null)
      setDriftReport(result.drift_report || null)
      setStatus(result.warning || `已重生成第 ${slideIndex} 页讲解`)
      setActiveSlideLectureJob(null)
    } catch (error) {
      setStatus(`当前页讲解生成失败：${errorMessage(error)}`)
      setActiveSlideLectureJob(null)
    }
  }

  const handlePlanCurrentSpeech = async () => {
    if (!preview?.slides.length || !selectedSlide || !selectedLecture?.lecture?.trim()) return
    setStatus("")
    try {
      const result = await planSlideSpeech.mutateAsync({
        chapter_title: effectiveCoursewareTitle(),
        slide: compactSlideForLectureRequest(selectedSlide),
        lecture: selectedLecture.lecture,
        max_cues: 1,
        teacher_guidance: teacherGuidance,
      })
      setSlideLectures((previous) =>
        previous.map((item) =>
          item.index === selectedLecture.index
            ? {
                ...item,
                speech_cues: result.speech_cues || [],
                estimated_chars: result.estimated_chars ?? item.estimated_chars,
              }
            : item,
        ),
      )
      setStatus((result.speech_cues || []).length ? `已更新第 ${selectedLecture.index} 页语音规划` : `第 ${selectedLecture.index} 页没有需要重复强调的重点`)
    } catch (error) {
      setStatus(`语音规划生成失败：${errorMessage(error)}`)
    }
  }

  const handleStopCourseAudio = async () => {
    courseAudioAbortRef.current = true
    if (!activeCourseAudioJob) {
      setCourseAudioProgress((previous) => ({ ...previous, running: false, error: "已停止全课语音生成" }))
      setStatus("已停止全课语音生成")
      return
    }
    try {
      const job = await stopCourseTtsJob(activeCourseAudioJob.jobId)
      setCourseAudioProgress(courseAudioProgressFromJob(job))
      setStatus(job.message || "正在停止全课语音生成，当前段完成后停止")
    } catch (error) {
      setStatus(`停止全课语音失败：${errorMessage(error)}`)
    }
  }

  const handleGenerateCourseAudio = async () => {
    const slides = preview?.slides || []
    const lectureItems = slides
      .map((slide, position) => {
        const lecture = slideLectures.find((item) => item.index === slide.index)
        const text = (lecture?.lecture || slide.notes || slide.content || slide.raw_text || "").trim()
        const speechCues = (lecture?.speech_cues || []).filter((cue) => cue.target_text?.trim())
        return {
          slide,
          position,
          text,
          speechCues,
        }
      })
      .filter((item) => item.text)

    if (!lectureItems.length) {
      setStatus("没有可生成语音的讲稿，请先生成逐页讲解")
      return
    }

    courseAudioAbortRef.current = false
    setStatus("正在检查语音服务...")
    setCourseAudioProgress({
      ...emptyCourseAudioProgress,
      running: true,
      slideCount: lectureItems.length,
      message: "正在启动全课语音后台任务",
    })

    try {
      const ttsStatus = await getTtsStatus()
      if (!ttsStatus.enabled || !ttsStatus.available) {
        throw new Error(ttsStatus.detail || "语音接口未接入")
      }

      const chapterKey = chapterId || preview?.chapter_title || effectiveCoursewareTitle()
      const job = await createCourseTtsJob({
        chapter_id: chapterKey,
        max_chars: TTS_CHUNK_CHARS,
        slides: lectureItems.map((item) => ({
          slide_index: Number(item.slide.index),
          position: item.position,
          text: item.text,
          speech_cues: item.speechCues as SpeechCue[],
        })),
      })
      const storedJob = {
        jobId: job.job_id,
        chapterId: chapterKey,
        title: effectiveCoursewareTitle(),
        createdAt: job.created_at || new Date().toISOString(),
      }
      setActiveCourseAudioJob(storedJob)
      writeStoredCourseAudioJob(storedJob)
      setCourseAudioProgress(courseAudioProgressFromJob(job))
      setStatus(job.message || "全课语音后台任务已启动，关闭网页后会继续生成")
    } catch (error) {
      const message = `全课语音生成失败：${errorMessage(error)}`
      setCourseAudioProgress((previous) => ({ ...previous, running: false, error: message }))
      setStatus(message)
    } finally {
      courseAudioAbortRef.current = false
    }
  }

  const handleApplyTexDraft = async () => {
    const draft = texDraft.trim()
    if (!draft || draft === texContent) return
    setStatus("")
    resetGeneratedLectures()
    const result = await previewTex.mutateAsync({
      tex_content: texDraft,
      filename: file?.name?.toLowerCase().endsWith(".tex") ? file.name : "edited.tex",
      asset_map: mergedAssetMap,
    })
    const slides = result.slides.map((slide) => ({
      ...slide,
      images: hydratePreviewImages(slide.images || [], imageBySourcePath),
      layout: hydrateLayoutImages(slide.layout, imageBySourcePath),
    }))
    applyPreviewResult(
      {
        ...preserveRenderedPages(preview, result),
        slides,
        editable_model: result.editable_model
          ? {
              ...result.editable_model,
              assets: { ...(result.editable_model.assets || {}), ...mergedAssetMap },
            }
          : result.editable_model,
        asset_map: { ...(result.asset_map || {}), ...mergedAssetMap },
      },
      chapterTitle,
    )
    resetTexState(result.tex_content || texDraft)
    setStatus(result.render_error ? `LaTeX 编译未成功，当前为解析预览：${result.render_error}` : "已应用 TeX 修改并刷新编译预览")
  }

  const handleFrameDraftChange = (value: string) => {
    if (!selectedSlide) return
    setFrameDrafts((previous) => ({ ...previous, [selectedSlide.index]: value }))
  }

  const handleApplyFrameDraft = async () => {
    if (!selectedSlide?.source_tex || !hasSelectedFrameChanges) return
    const start = typeof selectedSlide.source_start === "number" ? selectedSlide.source_start : -1
    const end = typeof selectedSlide.source_end === "number" ? selectedSlide.source_end : -1
    const nextTex =
      start >= 0 && end > start && texDraft.slice(start, end) === selectedSlide.source_tex
        ? `${texDraft.slice(0, start)}${selectedFrameDraft}${texDraft.slice(end)}`
        : texDraft.includes(selectedSlide.source_tex)
          ? texDraft.replace(selectedSlide.source_tex, selectedFrameDraft)
          : texDraft
    if (nextTex === texDraft) {
      setStatus("无法定位当前页 TeX 源码，请改用完整 TeX 文件应用")
      return
    }
    setTexDraft(nextTex)
    const result = await previewTex.mutateAsync({
      tex_content: nextTex,
      filename: file?.name?.toLowerCase().endsWith(".tex") ? file.name : "edited.tex",
      asset_map: mergedAssetMap,
    })
    const slides = result.slides.map((slide) => ({
      ...slide,
      images: hydratePreviewImages(slide.images || [], imageBySourcePath),
      layout: hydrateLayoutImages(slide.layout, imageBySourcePath),
    }))
    applyPreviewResult(
      {
        ...preserveRenderedPages(preview, result),
        slides,
        editable_model: result.editable_model
          ? {
              ...result.editable_model,
              assets: { ...(result.editable_model.assets || {}), ...mergedAssetMap },
            }
          : result.editable_model,
        asset_map: { ...(result.asset_map || {}), ...mergedAssetMap },
      },
      chapterTitle,
    )
    resetTexState(result.tex_content || nextTex)
    setSelectedIndex(Math.min(selectedSlide.index, result.slides.length || 1))
    setStatus(result.render_error ? `LaTeX 编译未成功，当前为解析预览：${result.render_error}` : "已应用当前页 TeX 修改并刷新编译预览")
  }

  const updateEditableModelForSlide = (
    slideIndex: number,
    updater: (model: EditableSlideModel | null) => EditableSlideModel | null,
  ) => {
    setEditableModel((previous) => updater(previous))
    resetGeneratedLectures()
  }

  const handleNormalizeEditableLayout = () => {
    setEditableModel((previous) => normalizeEditableModelLayout(previous, { force: true }))
    resetGeneratedLectures()
    setStatus("已强制整理当前课件布局并重新分配文本/图片区")
  }

  const handleEditableObjectChange = (slideIndex: number, objectId: string, patch: Partial<EditableSlideObject>) => {
    updateEditableModelForSlide(slideIndex, (model) => updateEditableSlideObjectWithAutoFit(model, slideIndex, objectId, patch))
  }

  const handleEditableObjectAutoFit = (slideIndex: number, objectId: string) => {
    updateEditableModelForSlide(slideIndex, (model) => autoFitEditableSlideObject(model, slideIndex, objectId))
  }

  const handleEditableLayoutCommit = (slideIndex: number, items: CanvasItem[]) => {
    updateEditableModelForSlide(slideIndex, (model) => mergeCanvasItemsIntoEditableModel(model, slideIndex, items))
  }

  const handleEditableObjectDelete = (slideIndex: number, objectId: string) => {
    updateEditableModelForSlide(slideIndex, (model) => mutateEditableSlide(model, slideIndex, (objects) => objects.filter((object) => object.id !== objectId)))
  }

  const handleDeleteCurrentSlide = () => {
    if (!selectedSlide || !preview || preview.slides.length <= 1) {
      setStatus("至少保留一页课件")
      return
    }
    if (!window.confirm(`删除第 ${selectedSlide.index} 页？此操作只删除当前编辑页，不修改源 TeX。`)) return
    const deletedIndex = selectedSlide.index
    const nextSlides = preview.slides
      .filter((slide) => slide.index !== deletedIndex)
      .map((slide, index) => ({ ...slide, index: index + 1 }))
    setPreview({ ...preview, slides: nextSlides })
    setEditableModel((model) => model ? { ...model, slides: model.slides.filter((slide) => slide.index !== deletedIndex).map((slide, index) => ({ ...slide, index: index + 1 })) } : model)
    setSlideLectures((items) => items.filter((item) => item.index !== deletedIndex).map((item, index) => ({ ...item, index: index + 1 })))
    setSelectedIndex(Math.min(deletedIndex, nextSlides.length))
    setStatus(`已删除第 ${deletedIndex} 页。图片资源已保留，浏览内容已修复。`)
  }

  const handleEditableObjectDuplicate = (slideIndex: number, objectId: string) => {
    updateEditableModelForSlide(slideIndex, (model) =>
      mutateEditableSlide(model, slideIndex, (objects) => {
        const target = objects.find((object) => object.id === objectId)
        if (!target) return objects
        const clone = {
          ...target,
          id: `${target.id}-copy-${Date.now()}`,
          bbox: {
            ...target.bbox,
            x: Math.min((target.bbox?.x || 0) + 28, CANVAS_WIDTH - (target.bbox?.width || 120)),
            y: Math.min((target.bbox?.y || 0) + 28, CANVAS_HEIGHT - (target.bbox?.height || 80)),
          },
          z: Math.max(...objects.map((object) => Number(object.z || 0)), 0) + 1,
        }
        return [...objects, clone]
      }),
    )
  }

  const handleEditableAddObject = (slideIndex: number, type: "richText" | "equation" | "table" | "textbox" | "callout" | "placeholder") => {
    updateEditableModelForSlide(slideIndex, (model) =>
      mutateEditableSlide(model, slideIndex, (objects) => {
        const nextZ = Math.max(...objects.map((object) => Number(object.z || 0)), 0) + 1
        const base = {
          id: `${type}-${Date.now()}`,
          type,
          bbox:
            type === "placeholder"
              ? { x: 570, y: 140 + (nextZ % 3) * 28, width: 360, height: 210 }
              : { x: 120 + (nextZ % 4) * 22, y: 140 + (nextZ % 5) * 18, width: 360, height: type === "table" ? 150 : 96 },
          z: nextZ,
          locked: false,
          style: {
            fontSize: type === "equation" ? 24 : type === "table" ? 14 : type === "callout" ? 16 : 18,
            lineHeight: type === "equation" ? 1.25 : type === "table" ? 1.35 : 1.32,
          },
        } satisfies EditableSlideObject
        if (type === "equation") return [...objects, { ...base, latex: "E = mc^2", text: "E = mc^2" }]
        if (type === "table") return [...objects, { ...base, rows: [["变量", "含义"], ["x", "示例"]] }]
        if (type === "placeholder") return [...objects, { ...base, label: "图片占位符", width_ratio: 0.36 }]
        if (type === "callout") return [...objects, { ...base, title: "提示", text: "在这里输入标注", rich_html: "<p>在这里输入标注</p>" }]
        return [...objects, { ...base, text: "新文本", rich_html: "<p>新文本</p>" }]
      }),
    )
  }

  const handleInsertAsset = (slideIndex: number, asset: CoursewareAsset) => {
    updateEditableModelForSlide(slideIndex, (model) =>
      mutateEditableSlide(model, slideIndex, (objects) => {
        const nextZ = Math.max(...objects.map((object) => Number(object.z || 0)), 0) + 1
        const object: EditableSlideObject = {
          id: `image-${asset.id}-${Date.now()}`,
          type: asset.data_uri ? "image" : "placeholder",
          bbox: { x: 560, y: 150, width: 360, height: 210 },
          z: nextZ,
          locked: false,
          style: {},
          asset_id: asset.id,
          source_path: asset.source_path,
          tex_ref: asset.tex_ref || asset.source_path,
          label: asset.name || asset.source_path || "图片",
          width_ratio: 0.36,
        }
        return [...objects, object]
      }),
    )
  }

  const handleReplaceImageAsset = (slideIndex: number, objectId: string, asset: CoursewareAsset) => {
    updateEditableModelForSlide(slideIndex, (model) =>
      updateEditableSlideObject(model, slideIndex, objectId, (object) => ({
        ...object,
        type: asset.data_uri ? "image" : "placeholder",
        asset_id: asset.id,
        source_path: asset.source_path,
        tex_ref: asset.tex_ref || asset.source_path,
        label: asset.name || asset.source_path || object.label,
      })),
    )
  }

  const handleRemoveAsset = (assetId: string) => {
    setAssetMap((previous) => {
      const next = { ...previous }
      delete next[assetId]
      return next
    })
    setEditableModel((previous) => {
      if (!previous) return previous
      const nextAssets = { ...(previous.assets || {}) }
      delete nextAssets[assetId]
      return {
        ...previous,
        assets: nextAssets,
        slides: previous.slides.map((slide) => {
          const objects = (slide.objects || slide.items || []).map((object) =>
            object.asset_id === assetId ? { ...object, type: "placeholder", asset_id: undefined } : object,
          )
          return { ...slide, objects, items: objects }
        }),
        updated_at: new Date().toISOString(),
      }
    })
  }

  const handleEditableLayer = (slideIndex: number, objectId: string, direction: "front" | "back") => {
    updateEditableModelForSlide(slideIndex, (model) =>
      mutateEditableSlide(model, slideIndex, (objects) => {
        const values = objects.map((object) => Number(object.z || 0))
        const targetZ = direction === "front" ? Math.max(...values, 0) + 1 : Math.min(...values, 0) - 1
        return objects.map((object) => (object.id === objectId ? { ...object, z: targetZ } : object))
      }),
    )
  }

  const handleEditableAlign = (slideIndex: number, objectId: string, align: "left" | "center" | "right" | "top" | "middle" | "bottom") => {
    updateEditableModelForSlide(slideIndex, (model) =>
      updateEditableSlideObject(model, slideIndex, objectId, (object) => {
        const bbox = object.bbox || { x: 0, y: 0, width: 120, height: 80 }
        const next = { ...bbox }
        if (align === "left") next.x = 48
        if (align === "center") next.x = (CANVAS_WIDTH - bbox.width) / 2
        if (align === "right") next.x = CANVAS_WIDTH - bbox.width - 48
        if (align === "top") next.y = 48
        if (align === "middle") next.y = (CANVAS_HEIGHT - bbox.height) / 2
        if (align === "bottom") next.y = CANVAS_HEIGHT - bbox.height - 48
        return { ...object, bbox: next }
      }),
    )
  }

  const handleAssetUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = event.target.files?.[0]
    event.target.value = ""
    if (!selectedFile) return
    const result = await uploadCoursewareAssets.mutateAsync(selectedFile)
    const nextAssets = result.asset_map || {}
    setAssetMap((previous) => ({ ...previous, ...nextAssets }))
    setEditableModel((previous) =>
      previous
        ? {
            ...previous,
            assets: { ...(previous.assets || {}), ...nextAssets },
            updated_at: new Date().toISOString(),
          }
        : previous,
    )
    setStatus(`已导入 ${result.asset_count || Object.keys(nextAssets).length} 个图片资源`)
  }

  const handleStyleReferenceUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = event.target.files?.[0]
    event.target.value = ""
    if (!selectedFile) return
    const result = await uploadCoursewareStyleReference.mutateAsync(selectedFile)
    setStyleReference(result)
    resetGeneratedLectures()
    const profile = result.profile || {}
    const theme = profile.themes?.[0]?.replace(/^usetheme:/, "")
    const frameCount = profile.layout_summary?.frame_count
    setStatus(
      result.warning ||
        `已读取参考风格：${profile.document_class || "Beamer"}${theme ? ` / ${theme}` : ""}${frameCount ? `，${frameCount} 页样例` : ""}`,
    )
  }

  const handleClearStyleReference = () => {
    setStyleReference(null)
    resetGeneratedLectures()
    setStatus("已移除参考风格约束")
  }

  const handleSaveCoursewareProject = async () => {
    if (!editableModel) return
    const title = effectiveCoursewareTitle(file?.name.replace(/\.[^.]+$/, ""), "未命名课件")
    const modelForSave = currentEditableModelForSave(title)
    if (!modelForSave) return
    const result = await saveCoursewareProject.mutateAsync({
      // Keep the loaded project's identity when the route was opened directly.
      project_id: projectId || (chapterId.startsWith("cw_") ? chapterId : undefined),
      course_id: courseId || undefined,
      title,
      editable_model: modelForSave,
      asset_map: mergedAssetMap,
      slides: preview?.slides || [],
      tex_content: texContent || modelForSave.source_tex || undefined,
      rendered_pages: preview?.rendered_pages || [],
      render_source: preview?.render_source,
      render_error: preview?.render_error,
      ppt_artifact: pptArtifact || undefined,
      source_node_ids: pptNodeIds,
      lecture_target_duration_minutes: targetDurationMinutes,
      lecture_speech_rate_cpm: DEFAULT_SPEECH_RATE_CPM,
      lecture_pacing: currentLecturePacingForSave(),
    })
    setProjectId(result.project_id)
    navigate({ to: "/teacher/prepare", search: { chapterId: result.project_id, nodeId: "", courseId }, replace: true })
    await queryClient.invalidateQueries({ queryKey: ["courseware-projects"] })
    setStatus(result.message || "课件项目已保存")
  }

  const handleOpenCoursewareProject = (nextProjectId: string) => {
    if (!nextProjectId) return
    setLoadedRecordId("")
    navigate({ to: "/teacher/prepare", search: { chapterId: nextProjectId, nodeId: "", courseId } })
  }

  const handleDeleteCoursewareProject = async () => {
    const target = selectedCoursewareProject
    if (!target) {
      setStatus("请先选择要删除的已保存课件")
      return
    }
    const label = target.title || target.id
    if (!window.confirm(`删除已保存课件「${label}」？当前画布内容不会自动清空。`)) return
    const result = await deleteCoursewareProject.mutateAsync({ projectId: target.id, courseId })
    if (projectId === target.id) setProjectId("")
    if (chapterId === target.id) navigate({ to: "/teacher/prepare", search: { chapterId: "", nodeId: nodeId || "", courseId }, replace: true })
    await queryClient.invalidateQueries({ queryKey: ["courseware-projects"] })
    await queryClient.removeQueries({ queryKey: ["courseware-project", target.id] })
    setStatus(result.message || "课件项目已删除")
  }

  const handleExportCoursewarePptx = async () => {
    if (!editableModel) return
    const title = effectiveCoursewareTitle(file?.name.replace(/\.[^.]+$/, ""), "未命名课件")
    const result = await exportCoursewarePptx.mutateAsync({
      title,
      editable_model: currentEditableModelForSave(title) || { ...editableModel, title, assets: mergedAssetMap },
      source_node_ids: pptNodeIds,
    })
    setPptArtifact(result.ppt_artifact)
    setStatus("已按当前结构化编辑模型导出 PPTX")
  }

  const handleSave = async () => {
    if (!preview) return
    if (!hasGeneratedSlideLectures) {
      setStatus("暂无可保存的逐页讲解，请先生成至少一页有效文案")
      return
    }
    const title = effectiveCoursewareTitle(file?.name.replace(/\.[^.]+$/, ""), "未命名PPT")
    const targetChapterId = chapterId && !chapterId.startsWith("cw_") ? chapterId : `ppt_${Date.now()}`
    const saveSourceNodeIds = lectureNodeIds.length ? lectureNodeIds : pptNodeIds
    const saveSourceScope = lectureSourceScope || pptSourceScope || (lectureNodeContext?.success ? lectureNodeContext.scope : undefined)
    const modelForSave = currentEditableModelForSave(title)
    const chapterResult = await saveChapter.mutateAsync({
      course_id: courseId || undefined,
      chapter_id: targetChapterId,
      title,
      content: preview.full_text,
      source_type: mode === "graph" ? "graph_ppt_tex" : "courseware",
      source_node_ids: saveSourceNodeIds.length ? saveSourceNodeIds : undefined,
      source_scope: saveSourceScope,
      ppt_slides: preview.slides,
      slide_lectures: slideLectures,
      tex_content: texContent || undefined,
      editable_model: modelForSave,
      asset_map: Object.keys(mergedAssetMap).length ? mergedAssetMap : undefined,
      rendered_pages: preview.rendered_pages || [],
      render_source: preview.render_source,
      render_error: preview.render_error,
      ppt_artifact: pptArtifact || undefined,
      ppt_source_node_ids: pptNodeIds.length ? pptNodeIds : undefined,
      lecture_source_node_ids: saveSourceNodeIds.length ? saveSourceNodeIds : undefined,
      lecture_target_duration_minutes: targetDurationMinutes,
      lecture_speech_rate_cpm: DEFAULT_SPEECH_RATE_CPM,
      lecture_pacing: currentLecturePacingForSave(),
    })
    const savedChapterId = chapterResult.chapter?.id || chapterResult.chapter_id || targetChapterId
    await saveLecture.mutateAsync({
      course_id: courseId || undefined,
      chapter_id: savedChapterId,
      lecture_content: mergedLecture,
      source_type: mode === "graph" ? "graph_ppt_tex" : "courseware",
      source_node_ids: saveSourceNodeIds.length ? saveSourceNodeIds : undefined,
      source_scope: saveSourceScope,
      ppt_slides: preview.slides,
      slide_lectures: slideLectures,
      tex_content: texContent || undefined,
      editable_model: modelForSave,
      asset_map: Object.keys(mergedAssetMap).length ? mergedAssetMap : undefined,
      rendered_pages: preview.rendered_pages || [],
      render_source: preview.render_source,
      render_error: preview.render_error,
      ppt_artifact: pptArtifact || undefined,
      ppt_source_node_ids: pptNodeIds.length ? pptNodeIds : undefined,
      lecture_source_node_ids: saveSourceNodeIds.length ? saveSourceNodeIds : undefined,
      lecture_target_duration_minutes: targetDurationMinutes,
      lecture_speech_rate_cpm: DEFAULT_SPEECH_RATE_CPM,
      lecture_pacing: currentLecturePacingForSave(),
    })
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["teacher-chapters"] }),
      queryClient.invalidateQueries({ queryKey: ["student-chapters"] }),
    ])
    await queryClient.invalidateQueries({ queryKey: ["teacher-chapter", savedChapterId] })
    await queryClient.invalidateQueries({ queryKey: ["courses"] })
    setLoadedRecordId(savedChapterId)
    navigate({ to: "/teacher/prepare", search: { chapterId: savedChapterId, nodeId: "", courseId }, replace: true })
    setStatus("已保存为课程授课文案")
  }

  const handleDeleteCurrent = async () => {
    if (!chapterId || !window.confirm(`删除课件“${chapterTitle || chapterId}”？`)) return
    try {
      const result = chapterId.startsWith("cw_")
        ? await deleteCoursewareProject.mutateAsync({ projectId: chapterId, courseId })
        : await deleteChapter.mutateAsync(chapterId)
      if (!result.success) throw new Error("删除失败")
      queryClient.removeQueries({ queryKey: ["teacher-chapter", chapterId] })
      queryClient.removeQueries({ queryKey: ["courseware-project", chapterId] })
      await Promise.all(["teacher-chapters", "student-chapters", "courseware-projects", "courses"].map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
      navigate({ to: "/teacher" })
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "删除失败，请重试")
    }
  }

  const handleCopy = async () => {
    if (!selectedLecture?.lecture) return
    await navigator.clipboard.writeText(selectedLecture.lecture)
    setStatus("已复制当前页文案")
  }

  const handleExportLectureMarkdown = () => {
    if (!hasGeneratedSlideLectures || !mergedLecture.trim()) return
    const title = effectiveCoursewareTitle("课件文案")
    const generatedAt = new Date().toLocaleString("zh-CN", { hour12: false })
    const content = [`# ${title}`, "", `导出时间：${generatedAt}`, `页面数：${preview?.slides.length || slideLectures.length}`, "", mergedLecture].join("\n")
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = `${safeDownloadFilename(title)}-逐页文案.md`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
    setStatus("已导出全部逐页文案")
  }

  const artifactUrl = (url?: string) => {
    if (!url) return ""
    return url.startsWith("http") ? url : `${getRuntimeConfig().educationApiBaseUrl}${url}`
  }

  return (
    <div className="teacher-prepare-workbench space-y-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-bold">备课工作台</h1>
          {chapterId && <button type="button" onClick={handleDeleteCurrent} disabled={deleteChapter.isPending || deleteCoursewareProject.isPending} className="mt-2 inline-flex items-center gap-1 border px-3 py-2 text-sm text-destructive hover:bg-destructive/10 disabled:opacity-50"><Trash2 size={16} />删除课件</button>}
          <p className="text-muted-foreground">从图谱课程树生成 PPT/TeX 课件，并基于同一证据链生成逐页讲解</p>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:flex sm:flex-wrap">
          <select
            value={style}
            onChange={(event) => setStyle(event.target.value)}
            className="rounded-lg border bg-background px-3 py-2 text-sm"
          >
            <option value="引导式教学">引导式教学</option>
            <option value="讲授式教学">讲授式教学</option>
            <option value="探究式教学">探究式教学</option>
          </select>
          <button
            onClick={handleGeneratePptTex}
            disabled={(!pptNodeIds.length && !allowNoNodeGeneration) || generatePptTex.isPending}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {generatePptTex.isPending ? <LoadingSpinner size={16} /> : <Wand2 size={16} />}
            生成PPT/TeX
          </button>
          <button
            onClick={handleGenerateLectures}
            disabled={!preview?.slides.length || isGeneratingLectures}
            className="inline-flex items-center gap-2 rounded-lg bg-secondary px-3 py-2 text-sm font-medium text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50"
          >
            {isGeneratingLectures ? <LoadingSpinner size={16} /> : <Network size={16} />}
            生成逐页讲解
          </button>
          <button
            onClick={handleRegenerateCurrentLecture}
            disabled={!selectedSlide || !preview?.slides.length || isGeneratingLectures}
            className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
          >
            {isGeneratingLectures ? <LoadingSpinner size={16} /> : <RotateCcw size={16} />}
            重生成当前页
          </button>
          <button
            onClick={handleSave}
            disabled={!preview || !hasGeneratedSlideLectures || saveChapter.isPending || saveLecture.isPending}
            className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
          >
            <Save size={16} />
            保存为课程
          </button>
          <button
            onClick={handleExportLectureMarkdown}
            disabled={!hasGeneratedSlideLectures}
            className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
          >
            <Download size={16} />
            导出文案
          </button>
          <button
            onClick={courseAudioProgress.running ? handleStopCourseAudio : handleGenerateCourseAudio}
            disabled={!hasGeneratedSlideLectures}
            className={cn(
              "inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50",
              courseAudioProgress.running && "text-amber-700",
            )}
            title={courseAudioStatusText(courseAudioProgress)}
          >
            {courseAudioProgress.running ? <Pause size={16} /> : <Play size={16} />}
            {courseAudioProgress.running ? "停止语音" : "生成全课语音"}
          </button>
          <button
            onClick={handleNormalizeEditableLayout}
            disabled={!editableModel}
            className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
          >
            <LayoutPanelTop size={16} />
            整理布局
          </button>
          <button
            onClick={handleSaveCoursewareProject}
            disabled={!editableModel || saveCoursewareProject.isPending}
            className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
          >
            {saveCoursewareProject.isPending ? <LoadingSpinner size={16} /> : <Save size={16} />}
            保存课件项目
          </button>
          <button
            onClick={handleExportCoursewarePptx}
            disabled={!editableModel || exportCoursewarePptx.isPending}
            className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
          >
            {exportCoursewarePptx.isPending ? <LoadingSpinner size={16} /> : <Download size={16} />}
            导出PPTX
          </button>
        </div>
      </div>

      {chapterId && (savedTeacherChapter.isFetching || savedCoursewareProject.isFetching) && <LoadingSpinner text="正在加载课件..." />}
      {chapterId && (savedTeacherChapter.isError || savedCoursewareProject.isError) && <div role="alert" className="flex items-center gap-3 border p-3 text-sm text-destructive">课件加载失败<button type="button" className="border px-3 py-1" onClick={() => chapterId.startsWith("cw_") ? savedCoursewareProject.refetch() : savedTeacherChapter.refetch()}>重试</button></div>}
      {status && (
        <div className="flex items-center gap-2 rounded-lg border bg-card px-4 py-3 text-sm text-muted-foreground">
          {driftReport?.changed || status.includes("图片引用") || status.includes("编译未成功") ? <AlertTriangle size={16} className="text-amber-600" /> : <CheckCircle2 size={16} className="text-primary" />}
          {status}
        </div>
      )}

      <div className="space-y-6">
        {preview?.render_error ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            LaTeX 编译未成功，备课页暂时显示解析预览；安装/修复 LaTeX 编译器后会显示与 Overleaf 一致的 PDF 页面。错误：{preview.render_error}
          </div>
        ) : preview?.rendered_pages?.length ? (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
            当前使用 LaTeX 编译后的 PDF 页面预览，备课页与授课页会显示同一份页面渲染结果。
          </div>
        ) : null}

        <CoursewareImageWarning preview={preview} />

        {shouldLoadGraphScope ? (
          <section className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <div className="space-y-4">
              <GraphTreePanel
                tree={tree}
                search={treeSearch}
                selectedNodeIds={pptNodeIds}
                expandedNodeIds={expandedNodeIds}
                isLoading={isGraphLoading}
                nodeCount={nodes.length}
                relationCount={relationships.length}
                onSearch={setTreeSearch}
                onSelect={selectPptNode}
                onToggle={toggleNode}
              />
              <GraphContextPanel
                isLoading={pptContextLoading}
                context={pptNodeContext}
                emptyText="第一步选择课程树，系统会按所选子树生成 PPT/TeX 页面内容。"
              />
              <EmbeddedPptGeneratePanel />
            </div>

            <div className="space-y-4">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Network size={18} />
                讲解范围确认
              </div>
              <GraphTreePanel
                tree={tree}
                search={treeSearch}
                selectedNodeIds={lectureNodeIds}
                expandedNodeIds={expandedNodeIds}
                isLoading={isGraphLoading}
                nodeCount={nodes.length}
                relationCount={relationships.length}
                onSearch={setTreeSearch}
                onSelect={selectLectureNode}
                onToggle={toggleNode}
              />
              <GraphContextPanel
                isLoading={lectureContextLoading}
                context={lectureNodeContext}
                emptyText="默认继承第一步选择；也可以在生成讲解前收窄范围以减少漂移。"
              />
              <EmbeddedPptLatexPanel />
            </div>
          </section>
        ) : (
          <section className="rounded-lg border bg-card p-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-2 font-medium">
                  <Network size={18} />
                  图谱范围
                </div>
                <div className="mt-1 text-sm text-muted-foreground">
                  已保存课程会先打开课件和文案；需要重新选择图谱范围时再加载章节树。
                </div>
              </div>
              <button
                type="button"
                onClick={() => setGraphScopeEnabled(true)}
                className="inline-flex items-center justify-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent"
              >
                <Network size={16} />
                加载图谱范围
              </button>
            </div>
          </section>
        )}

        <section className="rounded-lg border bg-card p-4">
          <div className="mb-3 flex items-center gap-2 font-medium">
            <FileText size={18} />
            课件设置
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(180px,0.26fr)_minmax(220px,0.3fr)_minmax(190px,0.22fr)_minmax(0,1fr)_auto_auto_auto]">
            <input
              value={chapterTitle}
              onChange={(event) => setChapterTitle(event.target.value)}
              placeholder="可选：覆盖课件标题"
              className="w-full rounded-lg border bg-background px-3 py-2 text-sm"
            />
            <div className="min-w-0">
              <div className="flex min-w-0 gap-2">
                <select
                  value={chapterId.startsWith("cw_") ? chapterId : ""}
                  onChange={(event) => handleOpenCoursewareProject(event.target.value)}
                  disabled={coursewareProjectsLoading || !coursewareProjects.length}
                  className="min-w-0 flex-1 rounded-lg border bg-background px-3 py-2 text-sm disabled:opacity-50"
                  title="打开已保存课件项目"
                >
                  <option value="">{coursewareProjectsLoading ? "加载课件项目..." : "打开已保存课件"}</option>
                  {coursewareProjects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.title || project.id}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={handleDeleteCoursewareProject}
                  disabled={!selectedCoursewareProject || deleteCoursewareProject.isPending}
                  className="inline-flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-lg border bg-card text-muted-foreground hover:bg-destructive hover:text-destructive-foreground disabled:cursor-not-allowed disabled:opacity-50"
                  title="删除选中的已保存课件"
                  aria-label="删除选中的已保存课件"
                >
                  {deleteCoursewareProject.isPending ? <LoadingSpinner size={15} /> : <Trash2 size={15} />}
                </button>
              </div>
              {duplicateCoursewareCount > 0 ? (
                <div className="mt-1 truncate text-xs text-muted-foreground">
                  已隐藏 {duplicateCoursewareCount} 条重复保存记录{selectedCoursewareProjectIsHidden ? "，当前打开项可直接删除" : ""}
                </div>
              ) : null}
            </div>
            <div className="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground">
              <span>预计讲课时长</span>
              <div className="flex min-w-0 gap-2">
                <input
                  type="number"
                  min={1}
                  max={180}
                  step={1}
                  value={durationDraftMinutes}
                  onChange={(event) => setDurationDraftMinutes(clampLectureDurationMinutes(Number(event.target.value)))}
                  className={cn(
                    "min-w-0 flex-1 rounded-lg border bg-background px-3 py-2 text-sm text-foreground",
                    durationDraftChanged && "border-amber-500 focus:border-amber-600",
                  )}
                  title="修改后需要点击确认，逐页生成才会使用新时长"
                />
                <button
                  type="button"
                  onClick={handleConfirmLectureDuration}
                  disabled={!preview || saveChapter.isPending}
                  className="inline-flex h-[38px] shrink-0 items-center justify-center gap-1.5 rounded-lg border bg-card px-2.5 text-sm font-medium hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
                  title="确认并持久化讲解时长"
                >
                  {saveChapter.isPending ? <LoadingSpinner size={14} /> : <CheckCircle2 size={14} />}
                  确认
                </button>
              </div>
              <span className={cn("truncate", durationDraftChanged ? "text-amber-600" : "text-muted-foreground")}>
                {durationDraftChanged ? `未确认，生成仍按 ${targetDurationMinutes} 分钟` : `已确认 ${targetDurationMinutes} 分钟`}
              </span>
            </div>
            <textarea
              value={teacherGuidance}
              onChange={(event) => setTeacherGuidance(event.target.value)}
              placeholder="例如：按这棵子树顺序讲，重点说明公式适用条件，减少历史背景。"
              className="min-h-[88px] w-full resize-y rounded-lg border bg-background px-3 py-2.5 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 lg:min-h-[42px]"
            />
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent">
              <input
                type="checkbox"
                checked={allowNoNodeGeneration}
                onChange={(event) => setAllowNoNodeGeneration(event.target.checked)}
                className="h-4 w-4 accent-primary"
              />
              无节点自动生成文案
            </label>
            <label className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent">
              <FileUp size={16} />
              上传课件
              <input type="file" accept={COURSEWARE_ACCEPT} onChange={handleFileChange} className="hidden" />
            </label>
            <label className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent">
              {uploadCoursewareStyleReference.isPending ? <LoadingSpinner size={16} /> : <Wand2 size={16} />}
              参考风格
              <input type="file" accept=".zip,.tex" onChange={handleStyleReferenceUpload} className="hidden" />
            </label>
            <label className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent">
              {uploadCoursewareAssets.isPending ? <LoadingSpinner size={16} /> : <ImagePlus size={16} />}
              图片包
              <input type="file" accept=".zip,.png,.jpg,.jpeg,.gif,.webp,.bmp,.tif,.tiff,.svg" onChange={handleAssetUpload} className="hidden" />
            </label>
          </div>
            {styleReference?.profile ? (
              <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">参考风格</span>
                <span>{styleReference.profile.document_class || "Beamer"}</span>
                {styleReference.profile.themes?.[0] ? <span>{styleReference.profile.themes[0].replace(/^usetheme:/, "")}</span> : null}
              {styleReference.profile.layout_summary?.frame_count ? <span>{styleReference.profile.layout_summary.frame_count} 页样例</span> : null}
              {styleReference.profile.style_signals?.slice(0, 3).map((signal) => (
                <span key={signal} className="rounded border bg-background px-2 py-0.5">
                  {signal}
                </span>
              ))}
                <button onClick={handleClearStyleReference} className="ml-auto inline-flex items-center gap-1 rounded px-2 py-1 hover:bg-accent">
                  <Trash2 size={13} />
                  移除
                </button>
              </div>
            ) : null}
            {styleReference && file ? (
              <div className="mt-2 text-xs text-muted-foreground">参考风格会用于图谱生成 PPT/TeX 和逐页讲解；上传已有课件时保留原课件样式。</div>
            ) : null}
          {file ? (
            <div className="mt-3 text-xs text-muted-foreground">
              已选择：{file.name}。支持 {COURSEWARE_FORMAT_LABEL}。
            </div>
          ) : (
            <div className="mt-3 text-xs text-muted-foreground">可上传已有 {COURSEWARE_FORMAT_LABEL} 课件，也可直接由图谱生成。</div>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            <span>总预算约 {totalTargetChars} 字</span>
            <span>已生成约 {generatedLectureChars} 字 / {formatDuration(generatedLectureSeconds)}</span>
            <span>当前页约 {selectedLecture?.estimated_chars || 0} 字 / {formatDuration(currentLectureSeconds)}</span>
            <span>{courseAudioStatusText(courseAudioProgress)}</span>
          </div>
        </section>

        <section className="rounded-xl border bg-card">
          <div className="flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="font-semibold">页面内容 / TeX</h2>
            <ArtifactLinks artifact={pptArtifact} artifactUrl={artifactUrl} />
          </div>
          <div className="grid grid-cols-1 gap-4 p-4 xl:grid-cols-[240px_minmax(0,1fr)] 2xl:grid-cols-[260px_minmax(0,1fr)]">
            <SlideList
              slides={preview?.slides || []}
              selectedIndex={selectedIndex}
              isLoading={isGeneratingPpt}
              onSelect={setSelectedIndex}
            />
            <div className="min-w-0 space-y-4">
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(340px,0.6fr)]">
                {selectedSlide ? (
                  <SlidePreview
                    slide={selectedSlide}
                    lecture={selectedLecture}
                    editableModel={editableModel}
                    assetMap={mergedAssetMap}
                    isFullscreen={isPreviewFullscreen}
                    frameDraft={selectedFrameDraft}
                    hasChanges={hasSelectedFrameChanges}
                    isPending={previewTex.isPending}
                    onToggleFullscreen={() => setIsPreviewFullscreen((value) => !value)}
                    onDeleteSlide={handleDeleteCurrentSlide}
                    onFrameDraftChange={handleFrameDraftChange}
                    onApplyFrameDraft={handleApplyFrameDraft}
                    onEditableObjectChange={handleEditableObjectChange}
                    onEditableLayoutCommit={handleEditableLayoutCommit}
                    onEditableObjectDelete={handleEditableObjectDelete}
                    onEditableObjectDuplicate={handleEditableObjectDuplicate}
                    onEditableObjectAutoFit={handleEditableObjectAutoFit}
                    onEditableAddObject={handleEditableAddObject}
                    onEditableLayer={handleEditableLayer}
                    onEditableAlign={handleEditableAlign}
                    onInsertAsset={handleInsertAsset}
                    onReplaceImageAsset={handleReplaceImageAsset}
                    onRemoveAsset={handleRemoveAsset}
                  />
                ) : (
                  <EmptyPanel text="选择图谱课程树后生成 PPT/TeX 页面，或上传已有课件" />
                )}
                {selectedSlide?.source_tex ? (
                  <CurrentFrameEditor
                    slide={selectedSlide}
                    value={selectedFrameDraft}
                    hasChanges={hasSelectedFrameChanges}
                    isPending={previewTex.isPending}
                    onChange={handleFrameDraftChange}
                    onApply={handleApplyFrameDraft}
                  />
                ) : texContent ? (
                  <EmptyPanel text="当前页面没有可定位的 TeX frame 源码，请使用完整 TeX 文件编辑" />
                ) : null}
              </div>
              {texContent ? (
                <details className="rounded-lg border bg-muted/30 p-3 text-sm">
                  <summary className="cursor-pointer font-medium">完整 TeX 文件</summary>
                  <div className="mt-3 space-y-3">
                    <textarea
                      value={texDraft}
                      onChange={(event) => setTexDraft(normalizeTexNewlines(event.target.value))}
                      spellCheck={false}
                      className="min-h-[420px] w-full resize-y rounded-lg border bg-background px-3 py-2 font-mono text-xs leading-5 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="text-xs text-muted-foreground">
                        {hasTexDraftChanges ? "TeX 修改尚未应用到页面预览" : "当前预览已使用最新 TeX 源码"}
                      </div>
                      <button
                        onClick={handleApplyTexDraft}
                        disabled={!hasTexDraftChanges || previewTex.isPending}
                        className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                      >
                        {previewTex.isPending ? <LoadingSpinner size={16} /> : <FileText size={16} />}
                        应用到预览
                      </button>
                    </div>
                  </div>
                </details>
              ) : null}
            </div>
          </div>
        </section>

        <section className="rounded-xl border bg-card">
          <div className="flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">对应讲解</h2>
              <span className="text-xs text-muted-foreground">状态：{lectureStatusText}</span>
              <span className="text-xs text-muted-foreground">
                语音规划：{selectedSpeechCueCount ? `已标记 ${selectedSpeechCueCount} 个重点` : "未标记"}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {preview?.slides.length ? (
                <span className="text-xs text-muted-foreground">
                  第 {selectedIndex} / {preview.slides.length} 页
                </span>
              ) : null}
              <button
                onClick={handleCopy}
                disabled={!selectedLecture?.lecture}
                className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-muted-foreground hover:bg-accent disabled:opacity-50"
              >
                <Clipboard size={15} />
                复制
              </button>
              <button
                onClick={handlePlanCurrentSpeech}
                disabled={!selectedLecture?.lecture || planSlideSpeech.isPending}
                className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-muted-foreground hover:bg-accent disabled:opacity-50"
                title="只重算当前页语音重点，不改写讲稿正文"
              >
                {planSlideSpeech.isPending ? <LoadingSpinner size={15} /> : <Wand2 size={15} />}
                语音规划
              </button>
              <button
                onClick={lecturePlayback.toggle}
                disabled={!selectedLecture?.lecture}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm hover:bg-accent disabled:opacity-50",
                  lecturePlayback.isPlaying ? "text-amber-700" : "text-primary",
                )}
                title={lecturePlayback.providerLabel}
              >
                {lecturePlayback.isPlaying || lecturePlayback.isLoadingAudio ? <Pause size={15} /> : <Play size={15} />}
                {lecturePlayback.isPlaying || lecturePlayback.isLoadingAudio ? "暂停" : "播放"}
              </button>
              <button
                onClick={() => lecturePlayback.replay(Math.max(0, (preview?.slides || []).findIndex((slide) => slide.index === selectedIndex)))}
                disabled={!selectedLecture?.lecture}
                className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-muted-foreground hover:bg-accent disabled:opacity-50"
              >
                <RotateCcw size={15} />
                重播
              </button>
            </div>
          </div>
          <PlaybackProgress progress={lecturePlayback.progress} statusText={lecturePlayback.statusText} />
          <div className="p-4">
            {selectedSlide ? (
              <div className="mb-4 rounded-lg border bg-muted/30 p-3">
                <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                  <MessageSquareText size={15} />
                  当前页修改意见
                </div>
                <textarea
                  value={selectedSlideFeedback}
                  onChange={(event) => handleSelectedSlideFeedbackChange(event.target.value)}
                  placeholder="例如：只讲本页图 26.5；先复述三条要点，再解释每条背后的生物学和数学含义；提出问题后直接给答案。"
                  className="min-h-[76px] w-full resize-y rounded-lg border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
                <div className="mt-2 text-xs text-muted-foreground">点击“重生成当前页”时仅作用于当前页，不影响其他页面文案。</div>
              </div>
            ) : null}
            {hasUsableSlideLecture(selectedLecture) ? (
              <div className="space-y-4">
                <DriftTrace driftReport={driftReport} />
                <EvidenceTrace lecture={selectedLecture} />
                <RichTextContent content={selectedLecture.lecture} />
              </div>
            ) : selectedLectureError ? (
              <EmptyPanel text={`当前页讲解生成失败：${selectedLectureError}`} />
            ) : isGeneratingLectures ? (
              <LoadingSpinner text="当前页讲解生成中..." />
            ) : (
              <EmptyPanel text="生成逐页讲解后将在这里显示当前页文案" />
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

function EmbeddedPptGeneratePanel() {
  const [isFullscreen, setIsFullscreen] = useState(false)
  const frameClassName = isFullscreen ? "h-[calc(100dvh-64px)] w-full border-0" : "h-[760px] w-full border-0"

  return (
    <section
      className={cn(
        "overflow-hidden rounded-lg border bg-card",
        isFullscreen && "fixed inset-0 z-50 rounded-none bg-background p-4",
      )}
    >
      <div className="flex items-center justify-between gap-3 border-b px-3 py-2">
        <h2 className="text-sm font-semibold">PPT 生成</h2>
        <button
          type="button"
          onClick={() => setIsFullscreen((value) => !value)}
          className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent"
          aria-label={isFullscreen ? "退出全屏 PPT 生成" : "全屏 PPT 生成"}
        >
          {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          {isFullscreen ? "退出全屏" : "放大"}
        </button>
      </div>
      <iframe
        title="PPT 生成"
        src="/beamer-generator/index.html?v=20260620-manual-outline-v100"
        className={frameClassName}
        allow="clipboard-read; clipboard-write"
        allowFullScreen
      />
    </section>
  )
}
function EmbeddedPptLatexPanel() {
  const [isFullscreen, setIsFullscreen] = useState(false)
  const frameClassName = isFullscreen ? "h-[calc(100dvh-64px)] w-full border-0" : "h-[760px] w-full border-0"

  return (
    <section
      className={cn(
        "overflow-hidden rounded-lg border bg-card",
        isFullscreen && "fixed inset-0 z-50 rounded-none bg-background p-4",
      )}
    >
      <div className="flex items-center justify-between gap-3 border-b px-3 py-2">
        <h2 className="text-sm font-semibold">PPT 转化为 LaTeX</h2>
        <button
          type="button"
          onClick={() => setIsFullscreen((value) => !value)}
          className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent"
          aria-label={isFullscreen ? "退出全屏 PPT 转化为 LaTeX" : "全屏 PPT 转化为 LaTeX"}
        >
          {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          {isFullscreen ? "退出全屏" : "放大"}
        </button>
      </div>
      <iframe
        title="PPT 转化为 LaTeX"
        src="/beamer-generator/index.html?mode=latex-import&v=20260620-manual-outline-v100"
        className={frameClassName}
        allow="clipboard-read; clipboard-write"
        allowFullScreen
      />
    </section>
  )
}
function EmbeddedPptWorkspaceCompact() {
  const [isFullscreen, setIsFullscreen] = useState(false)
  const frameClassName = isFullscreen
    ? "h-[calc(100dvh-132px)] min-h-[640px] w-full border-0"
    : "h-[700px] min-h-[620px] w-full border-0"

  return (
    <section
      className={cn(
        "embedded-ppt-compact overflow-hidden rounded-lg border bg-card",
        isFullscreen && "fixed inset-0 z-50 overflow-auto rounded-none bg-background p-4",
      )}
    >
      <div className="flex items-center justify-between gap-3 border-b p-3">
        <h2 className="text-sm font-semibold">PPT 生成与展示</h2>
        <button
          type="button"
          onClick={() => setIsFullscreen((value) => !value)}
          className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent"
        >
          {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          {isFullscreen ? "退出全屏" : "全屏"}
        </button>
      </div>

      <div className={cn("max-h-[900px] space-y-4 overflow-y-auto p-4", isFullscreen && "mx-auto max-h-none max-w-[1800px]")}>
        <section id="embedded-ppt-generate" className="overflow-hidden rounded-lg border bg-background">
          <div className="border-b px-3 py-2">
            <h3 className="text-sm font-semibold">PPT 生成</h3>
          </div>
          <iframe
            title="PPT 生成"
            src="/beamer-generator/index.html?v=20260527-min-pages-figpath-v75"
            className={frameClassName}
            allowFullScreen
          />
        </section>

        <section id="embedded-ppt-display" className="overflow-hidden rounded-lg border bg-background">
          <div className="border-b px-3 py-2">
            <h3 className="text-sm font-semibold">PPT 展示</h3>
          </div>
          <iframe
            title="PPT 展示"
            src="/beamer-generator/index.html?mode=latex-import&v=20260527-min-pages-figpath-v75"
            className={frameClassName}
            allowFullScreen
          />
        </section>
      </div>
    </section>
  )
}

function EmbeddedPptWorkspace() {
  const [isFullscreen, setIsFullscreen] = useState(false)

  const frameClassName = isFullscreen
    ? "h-[calc(100dvh-108px)] min-h-[640px] w-full border-0"
    : "h-[720px] min-h-[640px] w-full border-0"

  return (
    <section
      className={cn(
        "overflow-hidden rounded-lg border bg-card",
        isFullscreen && "fixed inset-0 z-50 overflow-auto rounded-none bg-background p-4",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div>
          <h2 className="font-semibold">PPT 生成与展示</h2>
          <p className="mt-1 text-xs text-muted-foreground">嵌入旧版 PPT 工作台，生成和展示版面保持原样。</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => document.getElementById("embedded-ppt-generate")?.scrollIntoView({ behavior: "smooth", block: "start" })}
            className="inline-flex items-center rounded-lg border px-3 py-2 text-sm font-medium hover:bg-accent"
          >
            PPT 生成
          </button>
          <button
            type="button"
            onClick={() => document.getElementById("embedded-ppt-display")?.scrollIntoView({ behavior: "smooth", block: "start" })}
            className="inline-flex items-center rounded-lg border px-3 py-2 text-sm font-medium hover:bg-accent"
          >
            PPT 展示
          </button>
          <button
            type="button"
            onClick={() => setIsFullscreen((value) => !value)}
            className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-accent"
          >
            {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
            {isFullscreen ? "退出全屏" : "全屏"}
          </button>
        </div>
      </div>

      <div className={cn("space-y-4 p-4", isFullscreen && "mx-auto max-w-[1800px]")}>
        <section id="embedded-ppt-generate" className="scroll-mt-6 overflow-hidden rounded-lg border bg-background">
          <div className="border-b p-4">
            <h3 className="text-lg font-semibold">PPT 生成</h3>
          </div>
          <iframe
            title="LaTeX Beamer 生成器"
            src="/beamer-generator/index.html?v=20260527-min-pages-figpath-v75"
            className={frameClassName}
            allowFullScreen
          />
        </section>

        <section id="embedded-ppt-display" className="scroll-mt-6 overflow-hidden rounded-lg border bg-background">
          <div className="border-b p-4">
            <h3 className="text-lg font-semibold">PPT 展示</h3>
          </div>
          <iframe
            title="导入 LaTeX 生成可编辑 PPT"
            src="/beamer-generator/index.html?mode=latex-import&v=20260527-min-pages-figpath-v75"
            className={frameClassName}
            allowFullScreen
          />
        </section>
      </div>
    </section>
  )
}

function SlideList({
  slides,
  selectedIndex,
  isLoading,
  onSelect,
}: {
  slides: PptSlideDetail[]
  selectedIndex: number
  isLoading: boolean
  onSelect: (index: number) => void
}) {
  return (
    <aside className="flex max-h-[calc(100vh-170px)] min-h-[360px] flex-col rounded-xl border bg-card p-3">
      <div className="mb-3 flex shrink-0 items-center gap-2 px-1 text-sm font-semibold">
        <FileText size={16} />
        页面
      </div>
      {isLoading ? (
        <LoadingSpinner text="生成页面中..." />
      ) : slides.length ? (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {slides.map((slide) => (
            <button
              key={slide.index}
              onClick={() => onSelect(slide.index)}
              title={`第 ${slide.index} 页：${slide.title || "无标题"}`}
              className={cn(
                "w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors",
                selectedIndex === slide.index ? "border-primary bg-primary/10 text-primary" : "hover:bg-accent",
              )}
            >
              <div className="font-medium">第 {slide.index} 页</div>
              <div className="truncate text-xs text-muted-foreground">{slide.title || "无标题"}</div>
            </button>
          ))}
        </div>
      ) : (
        <div className="py-10 text-center text-sm text-muted-foreground">暂无页面</div>
      )}
    </aside>
  )
}

function ArtifactLinks({
  artifact,
  artifactUrl,
}: {
  artifact: PptArtifact | null
  artifactUrl: (url?: string) => string
}) {
  if (!artifact?.pptx_url && !artifact?.tex_url) return null
  return (
    <div className="flex flex-wrap items-center gap-2">
      {artifact.pptx_url ? (
        <a className="inline-flex items-center gap-1.5 rounded-lg border px-2 py-1 text-sm hover:bg-accent" href={artifactUrl(artifact.pptx_url)}>
          <Download size={15} />
          PPTX
        </a>
      ) : null}
      {artifact.tex_url ? (
        <a className="inline-flex items-center gap-1.5 rounded-lg border px-2 py-1 text-sm hover:bg-accent" href={artifactUrl(artifact.tex_url)}>
          <Download size={15} />
          TeX
        </a>
      ) : null}
    </div>
  )
}

function DriftTrace({ driftReport }: { driftReport: SourceDriftReport | null }) {
  if (!driftReport?.changed) return null
  return (
    <section className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
      <div className="mb-1 flex items-center gap-1.5 font-medium">
        <AlertTriangle size={14} />
        讲解范围与课件生成范围不同
      </div>
      <div>{driftReport.warning}</div>
    </section>
  )
}

function CoursewareImageWarning({ preview }: { preview: PptPreviewResponse | null }) {
  const refs = preview?.missing_image_refs || []
  if (!refs.length && !preview?.warning?.includes("图片引用")) return null
  return (
    <section className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
      <div className="mb-1 flex items-center gap-1.5 font-medium">
        <AlertTriangle size={14} />
        课件图片未完整加载
      </div>
      <div>{preview?.warning || "检测到未匹配的图片引用，请上传包含 TeX 和图片目录的 ZIP，或用图片包补充资源。"}</div>
      {refs.length ? <div className="mt-1 truncate">缺失路径：{refs.slice(0, 8).join("、")}{refs.length > 8 ? ` 等 ${refs.length} 个` : ""}</div> : null}
    </section>
  )
}

function EvidenceTrace({ lecture }: { lecture: PptSlideLecture }) {
  const pathLabels = (lecture.graph_paths || []).map(formatGraphPath).filter(Boolean)
  const formulaLabels = (lecture.formula_context || []).map(formatFormulaContext).filter(Boolean)
  if (!pathLabels.length && !formulaLabels.length) return null

  return (
    <section className="rounded-lg border bg-muted/30 p-3 text-xs text-muted-foreground">
      <div className="mb-2 font-medium text-foreground">图谱与公式依据</div>
      {pathLabels.length ? (
        <div>
          <div className="font-medium text-foreground">关系路径</div>
          <ul className="mt-1 list-disc space-y-1 pl-4">
            {pathLabels.slice(0, 4).map((item, index) => (
              <li key={`${item}-${index}`}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {formulaLabels.length ? (
        <div className={cn(pathLabels.length && "mt-3")}>
          <div className="font-medium text-foreground">公式作用域</div>
          <ul className="mt-1 list-disc space-y-1 pl-4">
            {formulaLabels.slice(0, 4).map((item, index) => (
              <li key={`${item}-${index}`}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  )
}

function formatGraphPath(value: unknown) {
  if (!value || typeof value !== "object") return ""
  const item = value as Record<string, unknown>
  const source = String(item.source_label || item.source || "").trim()
  const target = String(item.target_label || item.target || "").trim()
  const type = String(item.type || "related").trim()
  if (!source || !target) return ""
  return `${source} --${type}--> ${target}`
}

function formatFormulaContext(value: unknown) {
  if (!value || typeof value !== "object") return ""
  const item = value as Record<string, unknown>
  const label = String(item.label || item.id || "").trim()
  const source = item.source && typeof item.source === "object" ? (item.source as Record<string, unknown>) : {}
  const scope = [source.chapter, source.unit_id].map((part) => String(part || "").trim()).filter(Boolean).join(" / ")
  const derivesFrom = Array.isArray(item.derives_from) && item.derives_from.length
    ? `，由 ${item.derives_from.slice(0, 3).join(", ")} 推导`
    : ""
  if (!label) return ""
  return scope ? `${label}（${scope}${derivesFrom}）` : `${label}${derivesFrom}`
}

function SlidePreview({
  slide,
  lecture,
  editableModel,
  assetMap,
  isFullscreen,
  frameDraft,
  hasChanges,
  isPending,
  onToggleFullscreen,
  onDeleteSlide,
  onFrameDraftChange,
  onApplyFrameDraft,
  onEditableObjectChange,
  onEditableLayoutCommit,
  onEditableObjectDelete,
  onEditableObjectDuplicate,
  onEditableObjectAutoFit,
  onEditableAddObject,
  onEditableLayer,
  onEditableAlign,
  onInsertAsset,
  onReplaceImageAsset,
  onRemoveAsset,
}: {
  slide: PptSlideDetail
  lecture?: PptSlideLecture
  editableModel: EditableSlideModel | null
  assetMap: Record<string, CoursewareAsset>
  isFullscreen: boolean
  frameDraft: string
  hasChanges: boolean
  isPending: boolean
  onToggleFullscreen: () => void
  onDeleteSlide: () => void
  onFrameDraftChange: (value: string) => void
  onApplyFrameDraft: () => void
  onEditableObjectChange: (slideIndex: number, objectId: string, patch: Partial<EditableSlideObject>) => void
  onEditableLayoutCommit: (slideIndex: number, items: CanvasItem[]) => void
  onEditableObjectDelete: (slideIndex: number, objectId: string) => void
  onEditableObjectDuplicate: (slideIndex: number, objectId: string) => void
  onEditableObjectAutoFit: (slideIndex: number, objectId: string) => void
  onEditableAddObject: (slideIndex: number, type: "richText" | "equation" | "table" | "textbox" | "callout" | "placeholder") => void
  onEditableLayer: (slideIndex: number, objectId: string, direction: "front" | "back") => void
  onEditableAlign: (slideIndex: number, objectId: string, align: "left" | "center" | "right" | "top" | "middle" | "bottom") => void
  onInsertAsset: (slideIndex: number, asset: CoursewareAsset) => void
  onReplaceImageAsset: (slideIndex: number, objectId: string, asset: CoursewareAsset) => void
  onRemoveAsset: (assetId: string) => void
}) {
  const allImages = collectSlideImages(slide)
  const modelSlide = editableModel?.slides.find((item) => item.index === slide.index)
  const editableObjects = useMemo(() => modelSlide?.objects || modelSlide?.items || [], [modelSlide?.items, modelSlide?.objects])
  const modelCanvasItems = useMemo(() => editableCanvasItemsFromModel(editableModel, slide.index), [editableModel, slide.index])
  const canvasItems = useMemo(() => (modelCanvasItems.length ? modelCanvasItems : canvasLayoutFromSlide(slide)), [modelCanvasItems, slide])
  const canEdit = Boolean(frameDraft || editableModel)
  const renderedPage = slide.rendered_page

  const updateDraft = (nextDraft: string) => {
    if (nextDraft !== frameDraft) onFrameDraftChange(nextDraft)
  }

  const updateTitle = (value: string) => {
    const nextDraft = replaceFrameTitle(frameDraft, value)
    if (nextDraft === frameDraft) return false
    updateDraft(nextDraft)
    return true
  }
  const updateContent = (value: string) => {
    const nextDraft = replaceFrameText(frameDraft, value)
    if (nextDraft === frameDraft) return false
    updateDraft(nextDraft)
    return true
  }
  const updateImageWidth = (image: SlideImage, ratio: number) => {
    const nextDraft = replaceImageWidth(frameDraft, image, ratio)
    if (nextDraft !== frameDraft) updateDraft(nextDraft)
  }
  const updateColumnWidth = (column: SlideLayoutColumn, ratio: number) => {
    const nextDraft = replaceColumnWidth(frameDraft, column, ratio)
    if (nextDraft !== frameDraft) updateDraft(nextDraft)
  }
  const updateCanvasLayout = (items: CanvasItem[]) => {
    onEditableLayoutCommit(slide.index, items)
    const nextDraft = editableObjects.length
      ? applyEditableCanvasLayoutToTex(frameDraft, canvasItems, items, allImages, editableObjects)
      : applyCanvasLayoutToTex(frameDraft, canvasItems, items, allImages)
    if (nextDraft !== frameDraft) updateDraft(nextDraft)
  }

  return (
    <div
      className={cn(
        "space-y-4",
        isFullscreen && "fixed inset-0 z-50 overflow-auto bg-background p-4",
      )}
    >
      <div className="overflow-hidden rounded-lg border bg-background shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="font-medium">
              第 {slide.index} 页 · {formatLayoutLabel(slide)}
            </span>
            {allImages.length ? <span>{allImages.length} 张图片</span> : null}
            {hasChanges ? <span className="text-amber-600">浏览页修改未应用</span> : null}
            {isFullscreen ? <span>Esc 退出全屏</span> : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onToggleFullscreen}
              className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-2.5 py-1.5 text-xs font-medium hover:bg-accent"
            >
              {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              {isFullscreen ? "退出全屏" : "全屏"}
            </button>
            <button
              type="button"
              onClick={onDeleteSlide}
              disabled={!canEdit}
              className="inline-flex items-center gap-1.5 rounded-lg border border-destructive/40 px-2.5 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
              title="删除当前课件页"
            >
              <Trash2 size={14} />删除当前页
            </button>
            <button
              onClick={onApplyFrameDraft}
              disabled={!canEdit || !hasChanges || isPending}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {isPending ? <LoadingSpinner size={14} /> : <FileText size={14} />}
              应用当前页
            </button>
          </div>
        </div>
        {renderedPage ? (
          <CompiledSlidePage page={renderedPage} isFullscreen={isFullscreen} />
        ) : (
          <SlideCanvasEditor
            slide={slide}
            images={allImages}
            editableObjects={editableObjects}
            assetMap={assetMap}
            initialItems={canvasItems}
            canEdit={canEdit}
            isFullscreen={isFullscreen}
            onTitleCommit={updateTitle}
            onContentCommit={updateContent}
            onLayoutCommit={updateCanvasLayout}
            onImageWidthChange={updateImageWidth}
            onColumnWidthChange={updateColumnWidth}
            onObjectChange={(objectId, patch) => onEditableObjectChange(slide.index, objectId, patch)}
            onObjectDelete={(objectId) => onEditableObjectDelete(slide.index, objectId)}
            onObjectDuplicate={(objectId) => onEditableObjectDuplicate(slide.index, objectId)}
            onObjectAutoFit={(objectId) => onEditableObjectAutoFit(slide.index, objectId)}
            onAddObject={(type) => onEditableAddObject(slide.index, type)}
            onLayer={(objectId, direction) => onEditableLayer(slide.index, objectId, direction)}
            onAlign={(objectId, align) => onEditableAlign(slide.index, objectId, align)}
          />
        )}
      </div>

      {!renderedPage ? (
        <AssetPanel
          assets={assetMap}
          activeImageObject={editableObjects.find((object) => object.type === "image" || object.type === "placeholder")}
          onInsert={(asset) => onInsertAsset(slide.index, asset)}
          onReplace={(objectId, asset) => onReplaceImageAsset(slide.index, objectId, asset)}
          onRemove={onRemoveAsset}
        />
      ) : null}
    </div>
  )
}

function CompiledSlidePage({ page, isFullscreen }: { page: NonNullable<PptSlideDetail["rendered_page"]>; isFullscreen: boolean }) {
  return (
    <div className={cn("bg-slate-950/5 p-3", isFullscreen && "min-h-[calc(100vh-96px)]")}>
      <div
        className="mx-auto w-full max-w-6xl overflow-hidden bg-white shadow-sm"
        style={{ aspectRatio: renderedPageAspectRatio(page) }}
      >
        <img
          src={page.image}
          alt={`PDF rendered slide ${page.page_index + 1}`}
          className="h-full w-full object-contain"
          draggable={false}
        />
      </div>
    </div>
  )
}

function renderedPageAspectRatio(page: NonNullable<PptSlideDetail["rendered_page"]>) {
  const width = Number(page.width)
  const height = Number(page.height)
  return width > 0 && height > 0 ? `${width} / ${height}` : "16 / 9"
}

function formatLayoutLabel(slide: PptSlideDetail) {
  const mode = slide.layout?.mode || "text"
  if (mode === "title") return "标题页"
  if (mode === "columns") return `${slide.layout?.column_count || slide.layout?.columns?.length || 0} 列`
  if (mode === "image_only") return "图片页"
  if (mode === "image_text") return "图文"
  if (mode === "text_image") return "文图"
  return "文本"
}

function AssetPanel({
  assets,
  activeImageObject,
  onInsert,
  onReplace,
  onRemove,
}: {
  assets: Record<string, CoursewareAsset>
  activeImageObject?: EditableSlideObject
  onInsert: (asset: CoursewareAsset) => void
  onReplace: (objectId: string, asset: CoursewareAsset) => void
  onRemove: (assetId: string) => void
}) {
  const assetList = Object.values(assets)
  if (!assetList.length) return null
  return (
    <section className="rounded-lg border bg-muted/30 p-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase text-muted-foreground">
        <ImagePlus size={14} />
        图片资源
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
        {assetList.slice(0, 12).map((asset) => (
          <figure key={asset.id} className="overflow-hidden border bg-background p-2">
            <button type="button" onClick={() => onInsert(asset)} className="block aspect-video w-full bg-muted">
              {asset.data_uri ? (
                <img src={asset.data_uri} alt={asset.name || asset.source_path || "图片"} className="h-full w-full object-contain" />
              ) : (
                <span className="flex h-full items-center justify-center px-2 text-center text-xs text-muted-foreground">
                  {asset.oversized ? "过大" : "无预览"}
                </span>
              )}
            </button>
            <figcaption className="mt-1 truncate text-xs text-muted-foreground" title={asset.source_path || asset.name}>
              {asset.name || asset.source_path || asset.id}
            </figcaption>
            <div className="mt-1 flex gap-1">
              <button type="button" onClick={() => onInsert(asset)} className="flex-1 border px-1 py-0.5 text-xs hover:bg-accent">插入</button>
              <button
                type="button"
                disabled={!activeImageObject}
                onClick={() => activeImageObject && onReplace(activeImageObject.id, asset)}
                className="flex-1 border px-1 py-0.5 text-xs hover:bg-accent disabled:opacity-40"
              >
                替换
              </button>
              <button type="button" onClick={() => onRemove(asset.id)} className="border px-1 py-0.5 text-xs hover:bg-accent" aria-label="删除图片资源">
                <Trash2 size={12} />
              </button>
            </div>
          </figure>
        ))}
      </div>
    </section>
  )
}

function buildColumnTemplate(columns: SlideLayout["columns"]) {
  const safeColumns = columns || []
  if (safeColumns.length <= 1) return "minmax(0, 1fr)"
  const ratios = safeColumns.map((column) => {
    const width = typeof column.width_ratio === "number" && Number.isFinite(column.width_ratio) ? column.width_ratio : 1
    return Math.max(width, 0.2)
  })
  return ratios.map((ratio) => `minmax(0, ${ratio}fr)`).join(" ")
}

function SlideCanvasEditor({
  slide,
  images,
  editableObjects,
  assetMap,
  initialItems,
  canEdit,
  isFullscreen = false,
  onTitleCommit,
  onContentCommit,
  onLayoutCommit,
  onImageWidthChange,
  onColumnWidthChange,
  onObjectChange,
  onObjectDelete,
  onObjectDuplicate,
  onObjectAutoFit,
  onAddObject,
  onLayer,
  onAlign,
}: {
  slide: PptSlideDetail
  images: SlideImage[]
  editableObjects: EditableSlideObject[]
  assetMap: Record<string, CoursewareAsset>
  initialItems: CanvasItem[]
  canEdit: boolean
  isFullscreen?: boolean
  onTitleCommit: (value: string) => boolean | void
  onContentCommit: (value: string) => boolean | void
  onLayoutCommit: (items: CanvasItem[]) => void
  onImageWidthChange: (image: SlideImage, ratio: number) => void
  onColumnWidthChange: (column: SlideLayoutColumn, ratio: number) => void
  onObjectChange: (objectId: string, patch: Partial<EditableSlideObject>) => void
  onObjectDelete: (objectId: string) => void
  onObjectDuplicate: (objectId: string) => void
  onObjectAutoFit: (objectId: string) => void
  onAddObject: (type: "richText" | "equation" | "table" | "textbox" | "callout" | "placeholder") => void
  onLayer: (objectId: string, direction: "front" | "back") => void
  onAlign: (objectId: string, align: "left" | "center" | "right" | "top" | "middle" | "bottom") => void
}) {
  const stageRef = useRef<HTMLDivElement | null>(null)
  const [items, setItems] = useState<CanvasItem[]>(initialItems)
  const [activeId, setActiveId] = useState<string>(initialItems[0]?.id || "")
  const [history, setHistory] = useState<CanvasItem[][]>([])
  const [future, setFuture] = useState<CanvasItem[][]>([])
  const interactionRef = useRef<CanvasInteraction | null>(null)
  const activeObject = editableObjects.find((object) => object.id === activeId)

  useEffect(() => {
    setItems(initialItems)
    setActiveId((previous) => (initialItems.some((item) => item.id === previous) ? previous : initialItems[0]?.id || ""))
    setHistory([])
    setFuture([])
  }, [initialItems])

  const commitItems = (nextItems: CanvasItem[], previousItems?: CanvasItem[]) => {
    if (previousItems) setHistory((entries) => [...entries.slice(-20), previousItems])
    setFuture([])
    setItems(nextItems)
    onLayoutCommit(nextItems)
  }

  const nudgeActive = (deltaX: number, deltaY: number) => {
    if (!activeId || !canEdit) return
    const previous = items
    const nextItems = items.map((item) =>
      item.id === activeId
        ? {
            ...item,
            x: Math.min(Math.max(item.x + deltaX, 0), CANVAS_WIDTH - item.width),
            y: Math.min(Math.max(item.y + deltaY, 0), CANVAS_HEIGHT - item.height),
          }
        : item,
    )
    commitItems(nextItems, previous)
  }

  const undo = () => {
    const previous = history[history.length - 1]
    if (!previous) return
    setHistory((entries) => entries.slice(0, -1))
    setFuture((entries) => [items, ...entries.slice(0, 20)])
    setItems(previous)
    onLayoutCommit(previous)
  }

  const redo = () => {
    const next = future[0]
    if (!next) return
    setFuture((entries) => entries.slice(1))
    setHistory((entries) => [...entries.slice(-20), items])
    setItems(next)
    onLayoutCommit(next)
  }

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target
      if (target instanceof HTMLElement && target.closest("textarea,input,select,[contenteditable='true']")) return
      if (!activeId || !canEdit) return
      if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault()
        onObjectDelete(activeId)
        return
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "d") {
        event.preventDefault()
        onObjectDuplicate(activeId)
        return
      }
      const step = event.shiftKey ? 10 : 1
      if (event.key === "ArrowUp") {
        event.preventDefault()
        nudgeActive(0, -step)
      } else if (event.key === "ArrowDown") {
        event.preventDefault()
        nudgeActive(0, step)
      } else if (event.key === "ArrowLeft") {
        event.preventDefault()
        nudgeActive(-step, 0)
      } else if (event.key === "ArrowRight") {
        event.preventDefault()
        nudgeActive(step, 0)
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [activeId, canEdit, nudgeActive, onObjectDelete, onObjectDuplicate])

  useEffect(() => {
    const handleMove = (event: PointerEvent) => {
      const interaction = interactionRef.current
      const stage = stageRef.current
      if (!interaction || !stage) return
      const rect = stage.getBoundingClientRect()
      const scaleX = CANVAS_WIDTH / rect.width
      const scaleY = CANVAS_HEIGHT / rect.height
      const deltaX = (event.clientX - interaction.startX) * scaleX
      const deltaY = (event.clientY - interaction.startY) * scaleY
      setItems((previous) =>
        previous.map((item) => {
          if (item.id !== interaction.id) return item
          if (interaction.mode === "resize") return resizeCanvasItem(interaction.item, deltaX, deltaY, interaction.handle || "se")
          return {
            ...item,
            x: Math.min(Math.max(interaction.item.x + deltaX, 0), CANVAS_WIDTH - item.width),
            y: Math.min(Math.max(interaction.item.y + deltaY, 0), CANVAS_HEIGHT - item.height),
          }
        }),
      )
    }
    const handleUp = () => {
      const interaction = interactionRef.current
      if (!interaction) return
      interactionRef.current = null
      setItems((current) => {
        setHistory((entries) => [...entries.slice(-20), initialItems])
        setFuture([])
        onLayoutCommit(current)
        return current
      })
    }
    window.addEventListener("pointermove", handleMove)
    window.addEventListener("pointerup", handleUp)
    window.addEventListener("pointercancel", handleUp)
    return () => {
      window.removeEventListener("pointermove", handleMove)
      window.removeEventListener("pointerup", handleUp)
      window.removeEventListener("pointercancel", handleUp)
    }
  }, [initialItems, onLayoutCommit])

  const beginInteraction = (event: React.PointerEvent, item: CanvasItem, mode: CanvasInteraction["mode"], handle?: ResizeHandle) => {
    if (!canEdit) return
    event.preventDefault()
    event.stopPropagation()
    setActiveId(item.id)
    interactionRef.current = {
      id: item.id,
      mode,
      handle,
      startX: event.clientX,
      startY: event.clientY,
      item,
    }
  }

  const columns = (slide.layout?.columns || []).filter((column) => column.content?.trim() || column.images?.length)

  return (
    <div className={cn("bg-slate-100 p-3 dark:bg-slate-900/60", isFullscreen && "min-h-[calc(100vh-66px)]")}>
      {canEdit ? (
        <CanvasToolbar
          activeObject={activeObject}
          canUndo={history.length > 0}
          canRedo={future.length > 0}
          onUndo={undo}
          onRedo={redo}
          onNudge={nudgeActive}
          onDelete={() => activeId && onObjectDelete(activeId)}
          onDuplicate={() => activeId && onObjectDuplicate(activeId)}
          onAutoFit={() => activeId && onObjectAutoFit(activeId)}
          onAddObject={onAddObject}
          onLayer={(direction) => activeId && onLayer(activeId, direction)}
          onAlign={(align) => activeId && onAlign(activeId, align)}
          onObjectStyleChange={(patch) => activeId && onObjectChange(activeId, patch)}
        />
      ) : null}
      <div
        ref={stageRef}
        className={cn(
          "relative mx-auto aspect-video overflow-hidden rounded-sm bg-white shadow-inner ring-1 ring-slate-200 dark:bg-slate-950 dark:ring-slate-800",
          isFullscreen ? "w-[min(100%,calc((100vh-170px)*16/9))]" : "kgts-slide-stage",
        )}
        style={{ touchAction: "none" }}
      >
        {slide.layout?.mode === "title" ? <TitleCanvasBackground /> : null}
        {[...items].sort((a, b) => {
          const aObject = editableObjects.find((object) => object.id === a.id)
          const bObject = editableObjects.find((object) => object.id === b.id)
          if (activeId === a.id && activeId !== b.id) return 1
          if (activeId === b.id && activeId !== a.id) return -1
          return Number(aObject?.z || 0) - Number(bObject?.z || 0)
        }).map((item) => {
          const object = editableObjects.find((candidate) => candidate.id === item.id)
          if (item.type === "image") {
            const image = images.find((candidate, imageIndex) => imageMatchesCanvasItem(candidate, item, imageIndex)) || imageForCanvasItem(items, item, images)
            const asset = findAssetForObject(object, assetMap) || (image ? findAssetForImage(image, assetMap) : undefined)
            return (
              <CanvasBox key={item.id} item={item} active={activeId === item.id} canEdit={canEdit} transparent={!canEdit && slide.layout?.mode === "title"} onActivate={setActiveId} onPointerDown={beginInteraction}>
                {asset?.data_uri || image?.data_uri ? (
                  <img
                    src={asset?.data_uri || image?.data_uri || ""}
                    alt={asset?.source_path || image?.source_path || "课件图片"}
                    className={cn("h-full w-full", slide.layout?.mode === "title" && item.id === "title-footer" ? "object-cover" : "object-contain")}
                    draggable={false}
                  />
                ) : (
                  <div className="flex h-full items-center justify-center bg-muted px-3 text-center text-xs text-muted-foreground">
                    {asset?.oversized || image?.oversized ? "图片过大，未内嵌预览" : object?.label || "图片无法预览"}
                  </div>
                )}
              </CanvasBox>
            )
          }
          if (item.type === "title") {
            return (
              <CanvasBox key={item.id} item={item} active={activeId === item.id} canEdit={canEdit} onActivate={setActiveId} onPointerDown={beginInteraction}>
                <EditableSlideText
                  value={object?.text || slide.title || "无标题"}
                  onCommit={(value) => {
                    if (object) onObjectChange(object.id, { text: value, rich_html: `<p>${value}</p>` })
                    return onTitleCommit(value)
                  }}
                  disabled={!canEdit}
                  className="h-full font-semibold"
                  style={objectTextStyle(object, defaultFontSizeForObject(object, item.type))}
                  multiline={false}
                />
              </CanvasBox>
            )
          }
          return (
            <CanvasBox key={item.id} item={item} active={activeId === item.id} canEdit={canEdit} onActivate={setActiveId} onPointerDown={beginInteraction}>
              <EditableObjectContent object={object} slide={slide} canEdit={canEdit} onObjectChange={onObjectChange} onContentCommit={onContentCommit} />
            </CanvasBox>
          )
        })}
      </div>

      {canEdit ? <div className="mt-3 text-xs text-muted-foreground">拖动对象移动，八向手柄缩放；方向键微调，Delete 删除，Ctrl+C/Ctrl+V 由复制按钮完成。</div> : null}
    </div>
  )
}

function TitleCanvasBackground() {
  return (
    <>
      <div className="pointer-events-none absolute inset-x-0 top-[7.1%] h-[3px] bg-[#007470]" />
      <div className="pointer-events-none absolute left-0 top-0 flex h-[7%] w-[25%] items-center justify-center bg-slate-200 px-3 text-center text-[clamp(0.52rem,0.82vw,0.82rem)] leading-tight text-slate-900">
        Public course in BIMSA in 2026 spring semester
      </div>
    </>
  )
}

function CanvasBox({
  item,
  active,
  canEdit,
  transparent = false,
  children,
  onActivate,
  onPointerDown,
}: {
  item: CanvasItem
  active: boolean
  canEdit: boolean
  transparent?: boolean
  children: ReactNode
  onActivate: (id: string) => void
  onPointerDown: (event: React.PointerEvent, item: CanvasItem, mode: CanvasInteraction["mode"], handle?: ResizeHandle) => void
}) {
  const isInteractiveTarget = (target: EventTarget | null) =>
    target instanceof HTMLElement && Boolean(target.closest("textarea,input,button,select,a,[contenteditable='true']"))

  return (
    <div
      className={cn(
        "group absolute overflow-visible border border-transparent p-1 text-slate-950 dark:text-slate-50",
        transparent ? "bg-transparent" : "bg-white/70 dark:bg-slate-950/70",
        canEdit && "cursor-move hover:border-primary/60 hover:bg-primary/5",
        active && canEdit && "border-primary bg-primary/5 shadow-sm",
      )}
      style={{
        left: `${(item.x / CANVAS_WIDTH) * 100}%`,
        top: `${(item.y / CANVAS_HEIGHT) * 100}%`,
        width: `${(item.width / CANVAS_WIDTH) * 100}%`,
        height: `${(item.height / CANVAS_HEIGHT) * 100}%`,
      }}
      onPointerDown={(event) => {
        if (isInteractiveTarget(event.target)) return
        onPointerDown(event, item, "move")
      }}
      onPointerDownCapture={() => {
        if (canEdit) onActivate(item.id)
      }}
      title={canEdit ? "拖动移动位置，拖右下角缩放" : undefined}
    >
      <div className="h-full w-full overflow-visible">{children}</div>
      {canEdit ? (
        <>
          <button
            type="button"
            aria-label="移动"
            onPointerDown={(event) => onPointerDown(event, item, "move")}
            className={cn(
              "absolute left-0 top-0 z-20 inline-flex h-6 w-6 cursor-move items-center justify-center rounded-br border border-primary bg-white/95 text-primary shadow-sm transition-opacity dark:bg-slate-900",
              active ? "opacity-100" : "opacity-70 group-hover:opacity-100",
            )}
          >
            <Move size={13} />
          </button>
          {(["n", "s", "e", "w", "ne", "nw", "se", "sw"] as ResizeHandle[]).map((handle) => (
            <button
              key={handle}
              type="button"
              aria-label={`缩放 ${handle}`}
              onPointerDown={(event) => onPointerDown(event, item, "resize", handle)}
              className={cn(
                "absolute z-20 h-3 w-3 border border-primary bg-white/95 transition-opacity group-hover:opacity-100 dark:bg-slate-900",
                active ? "opacity-100" : "opacity-0",
                handle.includes("n") && "top-0",
                handle.includes("s") && "bottom-0",
                handle.includes("e") && "right-0",
                handle.includes("w") && "left-0",
                handle === "n" && "left-1/2 -translate-x-1/2 cursor-n-resize",
                handle === "s" && "left-1/2 -translate-x-1/2 cursor-s-resize",
                handle === "e" && "top-1/2 -translate-y-1/2 cursor-e-resize",
                handle === "w" && "top-1/2 -translate-y-1/2 cursor-w-resize",
                handle === "ne" && "cursor-ne-resize",
                handle === "nw" && "cursor-nw-resize",
                handle === "se" && "cursor-se-resize",
                handle === "sw" && "cursor-sw-resize",
              )}
            />
          ))}
        </>
      ) : null}
    </div>
  )
}

function resizeCanvasItem(item: CanvasItem, deltaX: number, deltaY: number, handle: ResizeHandle): CanvasItem {
  let x = item.x
  let y = item.y
  let width = item.width
  let height = item.height
  if (handle.includes("e")) width = item.width + deltaX
  if (handle.includes("s")) height = item.height + deltaY
  if (handle.includes("w")) {
    x = item.x + deltaX
    width = item.width - deltaX
  }
  if (handle.includes("n")) {
    y = item.y + deltaY
    height = item.height - deltaY
  }
  width = Math.max(width, 64)
  height = Math.max(height, 36)
  x = Math.min(Math.max(x, 0), CANVAS_WIDTH - width)
  y = Math.min(Math.max(y, 0), CANVAS_HEIGHT - height)
  width = Math.min(width, CANVAS_WIDTH - x)
  height = Math.min(height, CANVAS_HEIGHT - y)
  return { ...item, x, y, width, height }
}

function CanvasToolbar({
  activeObject,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  onNudge,
  onDelete,
  onDuplicate,
  onAutoFit,
  onAddObject,
  onLayer,
  onAlign,
  onObjectStyleChange,
}: {
  activeObject?: EditableSlideObject
  canUndo: boolean
  canRedo: boolean
  onUndo: () => void
  onRedo: () => void
  onNudge: (x: number, y: number) => void
  onDelete: () => void
  onDuplicate: () => void
  onAutoFit: () => void
  onAddObject: (type: "richText" | "equation" | "table" | "textbox" | "callout" | "placeholder") => void
  onLayer: (direction: "front" | "back") => void
  onAlign: (align: "left" | "center" | "right" | "top" | "middle" | "bottom") => void
  onObjectStyleChange: (patch: Partial<EditableSlideObject>) => void
}) {
  return (
    <div className="mb-3 space-y-2 rounded-sm border bg-background p-2">
      <div className="flex flex-wrap items-center gap-1">
        <IconButton label="撤销" disabled={!canUndo} onClick={onUndo}><RotateCcw size={15} /></IconButton>
        <IconButton label="重做" disabled={!canRedo} onClick={onRedo}><RotateCw size={15} /></IconButton>
        <div className="mx-1 h-5 w-px bg-border" />
        <IconButton label="添加文本" onClick={() => onAddObject("richText")}><Plus size={15} /></IconButton>
        <IconButton label="添加公式" onClick={() => onAddObject("equation")}><FileText size={15} /></IconButton>
        <IconButton label="添加表格" onClick={() => onAddObject("table")}><Square size={15} /></IconButton>
        <IconButton label="图片占位" onClick={() => onAddObject("placeholder")}><ImagePlus size={15} /></IconButton>
        <IconButton label="添加标注" onClick={() => onAddObject("callout")}><LayoutPanelTop size={15} /></IconButton>
        <div className="mx-1 h-5 w-px bg-border" />
        <IconButton label="左对齐" disabled={!activeObject} onClick={() => onAlign("left")}><span className="text-xs">L</span></IconButton>
        <IconButton label="水平居中" disabled={!activeObject} onClick={() => onAlign("center")}><span className="text-xs">C</span></IconButton>
        <IconButton label="右对齐" disabled={!activeObject} onClick={() => onAlign("right")}><span className="text-xs">R</span></IconButton>
        <IconButton label="上对齐" disabled={!activeObject} onClick={() => onAlign("top")}><span className="text-xs">T</span></IconButton>
        <IconButton label="垂直居中" disabled={!activeObject} onClick={() => onAlign("middle")}><span className="text-xs">M</span></IconButton>
        <IconButton label="下对齐" disabled={!activeObject} onClick={() => onAlign("bottom")}><span className="text-xs">B</span></IconButton>
        <div className="mx-1 h-5 w-px bg-border" />
        <IconButton label="上移" disabled={!activeObject} onClick={() => onNudge(0, -5)}><span className="text-xs">↑</span></IconButton>
        <IconButton label="下移" disabled={!activeObject} onClick={() => onNudge(0, 5)}><span className="text-xs">↓</span></IconButton>
        <IconButton label="左移" disabled={!activeObject} onClick={() => onNudge(-5, 0)}><span className="text-xs">←</span></IconButton>
        <IconButton label="右移" disabled={!activeObject} onClick={() => onNudge(5, 0)}><span className="text-xs">→</span></IconButton>
        <IconButton label="置顶" disabled={!activeObject} onClick={() => onLayer("front")}><span className="text-xs">F</span></IconButton>
        <IconButton label="置底" disabled={!activeObject} onClick={() => onLayer("back")}><span className="text-xs">K</span></IconButton>
        <IconButton label="适配高度" disabled={!activeObject || !canAutoFitObject(activeObject)} onClick={onAutoFit}><ChevronsUpDown size={15} /></IconButton>
        <IconButton label="复制" disabled={!activeObject} onClick={onDuplicate}><Copy size={15} /></IconButton>
        <IconButton label="删除" disabled={!activeObject} onClick={onDelete}><Trash2 size={15} /></IconButton>
        <span className="ml-auto truncate pl-2 text-xs text-muted-foreground">{activeObject ? activeObject.type : "未选择对象"}</span>
      </div>
      {activeObject ? (
        <ObjectStyleBar object={activeObject} onChange={onObjectStyleChange} />
      ) : null}
    </div>
  )
}

function ObjectStyleBar({
  object,
  onChange,
}: {
  object: EditableSlideObject
  onChange: (patch: Partial<EditableSlideObject>) => void
}) {
  const fontSize = objectFontSize(object, defaultFontSizeForObject(object))
  const setFontSize = (value: number) => onChange({ style: { ...(object.style || {}), fontSize: value } })
  const lineHeight = objectLineHeight(object)
  const setLineHeight = (value: number) => onChange({ style: { ...(object.style || {}), lineHeight: Math.round(value * 100) / 100 } })
  return (
    <div className="flex flex-wrap items-center gap-2 border-t pt-2 text-xs">
      <label className="flex min-w-[220px] flex-1 items-center gap-2">
        <span className="shrink-0 text-muted-foreground">字号</span>
        <IconButton label="减小字号" onClick={() => setFontSize(Math.max(8, fontSize - 1))}><Minus size={14} /></IconButton>
        <input
          type="range"
          min="8"
          max="64"
          step="1"
          value={fontSize}
          onChange={(event) => setFontSize(Number(event.target.value))}
          className="min-w-0 flex-1"
        />
        <input
          type="number"
          min="8"
          max="64"
          value={fontSize}
          onChange={(event) => setFontSize(Math.min(Math.max(Number(event.target.value) || fontSize, 8), 64))}
          className="h-8 w-16 px-2 py-1 text-xs"
        />
        <IconButton label="增大字号" onClick={() => setFontSize(Math.min(64, fontSize + 1))}><Plus size={14} /></IconButton>
      </label>
      <label className="flex min-w-[180px] flex-1 items-center gap-2">
        <span className="shrink-0 text-muted-foreground">行距</span>
        <input
          type="range"
          min="1"
          max="1.8"
          step="0.02"
          value={lineHeight}
          onChange={(event) => setLineHeight(Number(event.target.value))}
          className="min-w-0 flex-1"
        />
        <input
          type="number"
          min="1"
          max="1.8"
          step="0.05"
          value={lineHeight}
          onChange={(event) => setLineHeight(Math.min(Math.max(Number(event.target.value) || lineHeight, 1), 1.8))}
          className="h-8 w-16 px-2 py-1 text-xs"
        />
      </label>
      {object.type === "equation" ? (
        <label className="flex min-w-[260px] flex-[1.4] items-center gap-2">
          <span className="shrink-0 text-muted-foreground">LaTeX</span>
          <input
            value={object.latex || object.text || ""}
            onChange={(event) => onChange({ latex: event.target.value, text: event.target.value })}
            className="h-8 min-w-0 flex-1 px-2 py-1 font-mono text-xs"
          />
        </label>
      ) : null}
    </div>
  )
}

function IconButton({ label, disabled, onClick, children }: { label: string; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center border bg-card text-xs hover:bg-accent disabled:opacity-40"
    >
      {children}
    </button>
  )
}

function EditableObjectContent({
  object,
  slide,
  canEdit,
  onObjectChange,
  onContentCommit,
}: {
  object?: EditableSlideObject
  slide: PptSlideDetail
  canEdit: boolean
  onObjectChange: (objectId: string, patch: Partial<EditableSlideObject>) => void
  onContentCommit: (value: string) => boolean | void
}) {
  if (!object) {
    return <SlideText content={slide.content || ""} editable={canEdit} onCommit={onContentCommit} className="h-full overflow-visible text-sm" />
  }
  if (object.type === "equation") {
    return (
      <div
        className={cn(
          "flex h-full w-full items-center justify-center overflow-visible px-2 text-center kgts-canvas-math-wrap",
          canEdit && "cursor-default",
        )}
        style={objectTextStyle(object, defaultFontSizeForObject(object))}
      >
        <RichTextContent content={`$$\n${object.latex || object.text || ""}\n$$`} className="kgts-canvas-math" />
      </div>
    )
  }
  if (object.type === "table") {
    return <EditableTableObject object={object} canEdit={canEdit} onObjectChange={onObjectChange} />
  }
  if (object.type === "callout") {
    return (
      <div className="h-full overflow-visible border-l-2 border-primary/60 pl-2">
        <div className="text-xs font-medium text-primary">{object.title || "提示"}</div>
        <EditableSlideText
          value={object.text || ""}
          onCommit={(value) => onObjectChange(object.id, { text: value, rich_html: `<p>${value}</p>` })}
          disabled={!canEdit}
          className="min-h-[calc(100%-18px)] overflow-visible"
          style={objectTextStyle(object, defaultFontSizeForObject(object))}
        />
      </div>
    )
  }
  return (
    <EditableSlideText
      value={object.text || object.label || ""}
      onCommit={(value) => onObjectChange(object.id, { text: value, label: object.type === "placeholder" ? value : object.label, rich_html: `<p>${escapeHtml(value)}</p>` })}
      disabled={!canEdit}
      className="min-h-full overflow-visible"
      style={objectTextStyle(object, defaultFontSizeForObject(object))}
    />
  )
}

function EditableTableObject({
  object,
  canEdit,
  onObjectChange,
}: {
  object: EditableSlideObject
  canEdit: boolean
  onObjectChange: (objectId: string, patch: Partial<EditableSlideObject>) => void
}) {
  const rows = object.rows?.length ? object.rows : [["", ""]]
  const updateCell = (rowIndex: number, cellIndex: number, value: string) => {
    const nextRows = rows.map((row, rIndex) => row.map((cell, cIndex) => (rIndex === rowIndex && cIndex === cellIndex ? value : cell)))
    onObjectChange(object.id, { rows: nextRows })
  }
  const addRow = () => {
    const width = Math.max(...rows.map((row) => row.length), 1)
    onObjectChange(object.id, { rows: [...rows, Array.from({ length: width }, () => "")] })
  }
  const addColumn = () => {
    onObjectChange(object.id, { rows: rows.map((row) => [...row, ""]) })
  }
  return (
    <div className="h-full overflow-visible" style={objectTextStyle(object, defaultFontSizeForObject(object))}>
      <div className="grid" style={{ gridTemplateColumns: `repeat(${Math.max(...rows.map((row) => row.length), 1)}, minmax(0, 1fr))` }}>
        {rows.map((row, rowIndex) =>
          row.map((cell, cellIndex) => (
            <input
              key={`${rowIndex}-${cellIndex}`}
              value={cell}
              disabled={!canEdit}
              onChange={(event) => updateCell(rowIndex, cellIndex, event.target.value)}
              className="min-w-0 border bg-white/80 px-1 py-1 dark:bg-slate-900/80"
              style={{ fontSize: "inherit", lineHeight: "inherit" }}
            />
          )),
        )}
      </div>
      {canEdit ? (
        <div className="mt-2 flex gap-1">
          <button type="button" onClick={addRow} className="border px-2 py-1 hover:bg-accent">行</button>
          <button type="button" onClick={addColumn} className="border px-2 py-1 hover:bg-accent">列</button>
        </div>
      ) : null}
    </div>
  )
}

function SlideColumn({
  column,
  editable = false,
  onTextCommit,
  onImageWidthChange,
  onColumnWidthChange,
}: {
  column: SlideLayoutColumn
  editable?: boolean
  onTextCommit?: (value: string) => void
  onImageWidthChange?: (image: SlideImage, ratio: number) => void
  onColumnWidthChange?: (column: SlideLayoutColumn, ratio: number) => void
}) {
  const imageFirst = column.image_first || (!column.content?.trim() && Boolean(column.images?.length))
  const content = column.content || ""
  const images = column.images || []
  return (
    <div className={cn("min-w-0 space-y-3", column.align === "center" && "text-center")}>
      {typeof column.width_ratio === "number" && editable ? (
        <ColumnWidthControl column={column} onColumnWidthChange={onColumnWidthChange} />
      ) : null}
      {imageFirst ? (
        <ImageList images={images} compact editable={editable} onWidthChange={onImageWidthChange} />
      ) : (
        <SlideText content={content} editable={editable} onCommit={onTextCommit} />
      )}
      {imageFirst ? (
        <SlideText content={content} editable={editable} onCommit={onTextCommit} />
      ) : (
        <ImageList images={images} compact editable={editable} onWidthChange={onImageWidthChange} />
      )}
    </div>
  )
}

function CurrentFrameEditor({
  slide,
  value,
  hasChanges,
  isPending,
  onChange,
  onApply,
}: {
  slide: PptSlideDetail
  value: string
  hasChanges: boolean
  isPending: boolean
  onChange: (value: string) => void
  onApply: () => void
}) {
  return (
    <section className="rounded-lg border bg-muted/30 p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 font-medium">
            <LayoutPanelTop size={16} />
            当前页 TeX / 布局
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            直接修改 frame 内的 columns、minipage、includegraphics width、vspace 等布局命令
          </div>
        </div>
        <button
          onClick={onApply}
          disabled={!hasChanges || isPending}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {isPending ? <LoadingSpinner size={16} /> : <FileText size={16} />}
          应用当前页
        </button>
      </div>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        spellCheck={false}
        className="mt-3 min-h-[440px] w-full resize-y rounded-lg border bg-background px-3 py-2 font-mono text-xs leading-5 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
      />
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{hasChanges ? "当前页 TeX 修改尚未应用到预览" : "当前页预览已使用最新 TeX"}</span>
        <span>{formatLayoutLabel(slide)}</span>
      </div>
    </section>
  )
}

function ColumnWidthControl({
  column,
  onColumnWidthChange,
}: {
  column: SlideLayoutColumn
  onColumnWidthChange?: (column: SlideLayoutColumn, ratio: number) => void
}) {
  const initialRatio = typeof column.width_ratio === "number" && Number.isFinite(column.width_ratio) ? column.width_ratio : 0.5
  const [ratio, setRatio] = useState(initialRatio)

  useEffect(() => {
    setRatio(initialRatio)
  }, [initialRatio])

  return (
    <div className="rounded-md border border-dashed border-slate-300 bg-white/80 px-2 py-1.5 text-left text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300">
      <label className="flex items-center gap-2">
        <span className="shrink-0">列宽</span>
        <input
          type="range"
          min="0.2"
          max="1"
          step="0.05"
          value={Math.min(Math.max(ratio, 0.2), 1)}
          onChange={(event) => {
            const nextRatio = Number(event.target.value)
            setRatio(nextRatio)
            onColumnWidthChange?.(column, nextRatio)
          }}
          className="min-w-0 flex-1"
        />
        <span className="w-10 text-right">{Math.round(ratio * 100)}%</span>
      </label>
    </div>
  )
}

function EditableSlideText({
  value,
  onCommit,
  disabled = false,
  className = "",
  multiline = true,
  style,
}: {
  value: string
  onCommit: (value: string) => boolean | void
  disabled?: boolean
  className?: string
  multiline?: boolean
  style?: CSSProperties
}) {
  const [draft, setDraft] = useState(value)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    setDraft(value)
  }, [value])

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea || disabled || !multiline) return
    textarea.style.height = "auto"
    textarea.style.height = `${Math.max(textarea.scrollHeight, textarea.parentElement?.clientHeight || 0, 40)}px`
  }, [draft, disabled, multiline, style])

  const commit = () => {
    const nextValue = normalizeEditableText(draft)
    if (nextValue && nextValue !== normalizeEditableText(value)) {
      const committed = onCommit(nextValue)
      if (committed === false) setDraft(value)
    }
  }

  if (disabled) return <div className={cn("whitespace-pre-wrap break-words", className)} style={style}>{value}</div>

  return (
    <textarea
      ref={textareaRef}
      value={draft}
      rows={multiline ? Math.max(draft.split("\n").length, 2) : 1}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (!multiline && event.key === "Enter") {
          event.preventDefault()
          event.currentTarget.blur()
        }
      }}
      spellCheck={false}
      className={cn(
        "w-full resize-none rounded-md border border-transparent bg-transparent px-1 py-0.5 outline-none transition-colors hover:border-slate-300 focus:border-primary focus:bg-white focus:ring-2 focus:ring-primary/20 dark:focus:bg-slate-900",
        multiline && "min-h-full",
        multiline ? "overflow-hidden whitespace-pre-wrap" : "overflow-hidden whitespace-nowrap",
        className,
      )}
      style={style}
      title="直接编辑当前页文本，失焦后写入当前页 TeX 草稿"
    />
  )
}

function SlideText({
  content,
  className = "",
  editable = false,
  onCommit,
}: {
  content: string
  className?: string
  editable?: boolean
  onCommit?: (value: string) => boolean | void
}) {
  if (editable && onCommit && content.trim()) {
    return <EditableSlideText value={content} onCommit={onCommit} className={className} />
  }
  return content.trim() ? (
    <div className={className}>
      <RichTextContent content={content} />
    </div>
  ) : (
    <div className={cn("text-sm text-muted-foreground", className)}>无正文文本</div>
  )
}

function SlideImages({
  slide,
  compact = false,
  editable = false,
  onWidthChange,
}: {
  slide: PptSlideDetail
  compact?: boolean
  editable?: boolean
  onWidthChange?: (image: SlideImage, ratio: number) => void
}) {
  return <ImageList images={slide.images || []} imageCount={slide.image_count || 0} compact={compact} editable={editable} onWidthChange={onWidthChange} />
}

function ImageList({
  images,
  imageCount = images.length,
  compact = false,
  editable = false,
  onWidthChange,
}: {
  images: NonNullable<PptSlideDetail["images"]>
  imageCount?: number
  compact?: boolean
  editable?: boolean
  onWidthChange?: (image: SlideImage, ratio: number) => void
}) {
  if (!images.length) {
    return compact ? null : <div className="text-sm text-muted-foreground">图片数量：{imageCount}</div>
  }

  return (
    <div>
      {!compact && <div className="text-xs font-medium uppercase text-muted-foreground">图片</div>}
      <div className={cn("grid grid-cols-1 gap-3", compact ? compactImageGridClass(images.length) : "mt-2 md:grid-cols-2")}>
        {images.map((image, index) => (
          <figure key={`${image.source_path || index}-${index}`} className={cn("min-h-0 rounded-lg border bg-background p-2", compact && "bg-white/80 dark:bg-slate-900")}>
            {image.data_uri ? (
              <img
                src={image.data_uri}
                alt={image.source_path || `课件图片 ${index + 1}`}
                className={cn("w-full rounded object-contain", compact ? "max-h-48" : "max-h-80")}
              />
            ) : (
              <div className="flex min-h-32 items-center justify-center rounded bg-muted px-3 text-center text-xs text-muted-foreground">
                {image.oversized ? "图片过大，未内嵌预览" : "图片无法预览"}
              </div>
            )}
            {image.source_path ? <figcaption className="mt-2 truncate text-xs text-muted-foreground">{image.source_path}</figcaption> : null}
            {editable ? (
              <ImageWidthControl image={image} onWidthChange={onWidthChange} />
            ) : null}
          </figure>
        ))}
      </div>
    </div>
  )
}

function compactImageGridClass(count: number) {
  if (count <= 1) return "mt-0"
  if (count === 2) return "mt-0 sm:grid-cols-2"
  return "mt-0 sm:grid-cols-3"
}

function ImageWidthControl({
  image,
  onWidthChange,
}: {
  image: SlideImage
  onWidthChange?: (image: SlideImage, ratio: number) => void
}) {
  const initialRatio = typeof image.width_ratio === "number" && Number.isFinite(image.width_ratio) ? image.width_ratio : 0.6
  const [ratio, setRatio] = useState(initialRatio)

  useEffect(() => {
    setRatio(initialRatio)
  }, [initialRatio])

  return (
    <label className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
      <span className="shrink-0">宽度</span>
      <input
        type="range"
        min="0.2"
        max="1"
        step="0.05"
        value={Math.min(Math.max(ratio, 0.2), 1)}
        onChange={(event) => {
          const nextRatio = Number(event.target.value)
          setRatio(nextRatio)
          onWidthChange?.(image, nextRatio)
        }}
        className="min-w-0 flex-1"
      />
      <span className="w-10 text-right">{Math.round(ratio * 100)}%</span>
    </label>
  )
}

function EmptyPanel({ text }: { text: string }) {
  return <div className="py-16 text-center text-sm text-muted-foreground">{text}</div>
}
