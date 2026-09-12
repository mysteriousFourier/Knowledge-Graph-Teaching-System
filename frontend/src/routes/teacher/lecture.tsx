import { createFileRoute } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useState, type ReactNode } from "react"
import { BookOpen, ChevronLeft, ChevronRight, Edit3, Eye, Maximize2, Minimize2, Pause, Play, RefreshCw, RotateCcw, Save, Trash2, X } from "lucide-react"
import { useDeleteChapter, useSaveLecture, useTeacherChapter, useTeacherChapters } from "@/api/teacher"
import { EvidenceSummary } from "@/components/common/EvidenceSummary"
import { LectureReviewPanel } from "@/components/common/LectureReviewPanel"
import { EmptyState } from "@/components/common/EmptyState"
import { LoadingSpinner } from "@/components/common/LoadingSpinner"
import { PlaybackProgress } from "@/components/common/PlaybackProgress"
import { RichTextContent } from "@/components/renderers/RichTextContent"
import { useLecturePlayback } from "@/hooks/useLecturePlayback"
import { cn } from "@/lib/utils"
import type { Chapter } from "@/types/chapter"
import type { CoursewareAsset, EditableSlideObject, PptSlideDetail } from "@/types/education"

export const Route = createFileRoute("/teacher/lecture")({
  component: LecturePage,
  validateSearch: (search: Record<string, unknown>) => ({
    chapterId: typeof search.chapterId === "string" ? search.chapterId : "",
    courseId: typeof search.courseId === "string" && search.courseId ? search.courseId : undefined,
  }),
})

type SlideImage = NonNullable<PptSlideDetail["images"]>[number]
type SlideLayout = NonNullable<PptSlideDetail["layout"]>
type SlideLayoutColumn = NonNullable<SlideLayout["columns"]>[number]
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

const CANVAS_WIDTH = 1000
const CANVAS_HEIGHT = 562.5

function LecturePage() {
  const { chapterId, courseId } = Route.useSearch()
  const queryClient = useQueryClient()
  const [selectedChapterId, setSelectedChapterId] = useState("")
  const [isEditing, setIsEditing] = useState(false)
  const [editorMode, setEditorMode] = useState<"edit" | "preview">("edit")
  const [draftContent, setDraftContent] = useState("")
  const [saveMessage, setSaveMessage] = useState("")
  const [isPresentationFullscreen, setIsPresentationFullscreen] = useState(false)

  const { data: chaptersData, isLoading } = useTeacherChapters(courseId)
  const { data: selectedChapterData } = useTeacherChapter(selectedChapterId)
  const saveLecture = useSaveLecture()
  const deleteChapter = useDeleteChapter()
  const chapters = chaptersData?.chapters || []
  const selectedChapterSummary = chapters.find((chapter) => chapter.id === selectedChapterId)
  const selectedChapter = selectedChapterData?.chapter || selectedChapterSummary
  const slideLectures = selectedChapter?.slide_lectures || []
  const lectureContent = isEditing ? draftContent : selectedChapter?.lecture_content || ""
  const coursewareSlides = useMemo(() => buildLectureSlides(selectedChapter), [selectedChapter])
  const markdownSlides = lectureContent.trim() ? [lectureContent] : []
  const isCoursewareChapter = !isEditing && coursewareSlides.length > 0
  const segmentCount = isCoursewareChapter ? coursewareSlides.length : markdownSlides.length
  const playback = useLecturePlayback({
    segmentCount,
    chapterId: selectedChapter?.id,
    getSegmentId: (segment) => {
      if (isCoursewareChapter) {
        const slide = coursewareSlides[segment]
        return slide ? `slide-${slide.index}` : `slide-${segment + 1}`
      }
      return "lecture"
    },
    getSegmentText: (segment) => {
      if (isCoursewareChapter) {
        const slide = coursewareSlides[segment]
        const lecture = slideLectures.find((item) => ((slide?.slide_id && item.slide_id === slide.slide_id) || item.index === slide?.index) && item.lecture?.trim())
        return lecture?.lecture || slide?.notes || slide?.content || slide?.raw_text || ""
      }
      return markdownSlides[segment] || ""
    },
    getSegmentSpeechCues: (segment) => {
      if (!isCoursewareChapter) return undefined
      const slide = coursewareSlides[segment]
      return slideLectures.find((item) => ((slide?.slide_id && item.slide_id === slide.slide_id) || item.index === slide?.index) && item.lecture?.trim())?.speech_cues
    },
  })
  const currentSlide = playback.currentSegment

  useEffect(() => {
    playback.reset(0)
    setIsPresentationFullscreen(false)
  }, [selectedChapterId, isEditing, editorMode])

  useEffect(() => {
    if (!chapters.length || selectedChapterId) return
    const fromSearch = chapterId ? chapters.find((chapter) => chapter.id === chapterId) : undefined
    const withLecture = chapters.find((chapter) => chapter.has_lecture_content || !!chapter.lecture_content)
    setSelectedChapterId((fromSearch || withLecture || chapters[0]).id)
  }, [chapterId, chapters, selectedChapterId])

  const currentCoursewareSlide = coursewareSlides[currentSlide]
  const currentSlideLecture = useMemo(() => {
    if (!slideLectures.length || !currentCoursewareSlide) return undefined
    return slideLectures.find((item) => ((currentCoursewareSlide.slide_id && item.slide_id === currentCoursewareSlide.slide_id) || item.index === currentCoursewareSlide.index) && item.lecture?.trim())
  }, [currentCoursewareSlide, slideLectures])
  const canNavigateSlides = !isEditing && segmentCount > 1

  useEffect(() => {
    if (!canNavigateSlides) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (isInteractiveElement(event.target)) return
      if (event.altKey || event.ctrlKey || event.metaKey) return
      if (["ArrowRight", "PageDown", " ", "Enter"].includes(event.key)) {
        event.preventDefault()
        playback.setCurrentSegment((current) => current + 1)
      }
      if (["ArrowLeft", "PageUp", "Backspace"].includes(event.key)) {
        event.preventDefault()
        playback.setCurrentSegment((current) => current - 1)
      }
      if (event.key === "Home") {
        event.preventDefault()
        playback.setCurrentSegment(0)
      }
      if (event.key === "End") {
        event.preventDefault()
        playback.setCurrentSegment(segmentCount - 1)
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [canNavigateSlides, playback, segmentCount])

  useEffect(() => {
    if (!isPresentationFullscreen) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsPresentationFullscreen(false)
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [isPresentationFullscreen])

  const handleStartEdit = () => {
    setDraftContent(selectedChapter?.lecture_content || "")
    setIsEditing(true)
    setEditorMode("edit")
    playback.pause()
    setSaveMessage("")
  }

  const handleCancelEdit = () => {
    setDraftContent("")
    setIsEditing(false)
    setEditorMode("edit")
    setSaveMessage("")
    playback.reset(0)
  }

  const handleSave = async () => {
    if (!selectedChapter) return
    const result = await saveLecture.mutateAsync({
      chapter_id: selectedChapter.id,
      course_id: courseId || selectedChapter.course_id,
      lecture_content: draftContent,
      learning_plan: selectedChapter.lecture_learning_plan,
    })
    if (result.success) {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["teacher-chapters"] }),
        queryClient.invalidateQueries({ queryKey: ["student-chapters"] }),
      ])
      setIsEditing(false)
      setEditorMode("edit")
      playback.reset(0)
      setSaveMessage("授课文案已保存")
    }
  }

  const handleDelete = async () => {
    if (!selectedChapter || !window.confirm(`删除课程「${selectedChapter.title}」？`)) return
    const result = await deleteChapter.mutateAsync(selectedChapter.id)
    if (!result.success) {
      window.alert(result.error || "删除失败")
      return
    }
    setSelectedChapterId("")
    setDraftContent("")
    setIsEditing(false)
    setEditorMode("edit")
    setSaveMessage("")
    playback.reset(0)
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["teacher-chapters"] }),
      queryClient.invalidateQueries({ queryKey: ["student-chapters"] }),
      queryClient.invalidateQueries({ queryKey: ["student-progress"] }),
    ])
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">授课模式</h1>
        <p className="text-muted-foreground">播放讲解内容</p>
      </div>

      <div className="bg-card border rounded-xl p-4">
        <label className="block text-sm font-medium mb-2">选择课程</label>
        {isLoading ? (
          <LoadingSpinner size={20} text="加载中..." />
        ) : (
          <select
            value={selectedChapterId}
            onChange={(event) => {
              setSelectedChapterId(event.target.value)
              playback.reset(0)
              setIsEditing(false)
              setEditorMode("edit")
              setDraftContent("")
              setSaveMessage("")
            }}
            className="w-full px-3 py-2.5 bg-background border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
          >
            <option value="">-- 请选择课程 --</option>
            {chapters.map((chapter) => (
              <option key={chapter.id} value={chapter.id}>
                {chapter.title}
              </option>
            ))}
          </select>
        )}
      </div>

      {selectedChapter && (
        <div className="bg-card border rounded-xl">
          <div className="p-4 border-b flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h2 className="font-semibold">{selectedChapter.title}</h2>
              {saveMessage && <p className="mt-1 text-sm text-emerald-600">{saveMessage}</p>}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {isEditing ? (
                <>
                  <button
                    onClick={() => {
                      setEditorMode((mode) => (mode === "edit" ? "preview" : "edit"))
                      playback.reset(0)
                    }}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
                  >
                    {editorMode === "edit" ? <Eye size={14} /> : <Edit3 size={14} />}
                    {editorMode === "edit" ? "预览" : "编辑"}
                  </button>
                  <button
                    onClick={handleSave}
                    disabled={saveLecture.isPending}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-green-100 text-green-700 hover:bg-green-200 disabled:opacity-50 transition-colors"
                  >
                    <Save size={14} />
                    {saveLecture.isPending ? "保存中..." : "保存"}
                  </button>
                  <button
                    onClick={handleCancelEdit}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-muted text-muted-foreground hover:bg-muted/80 transition-colors"
                  >
                    <X size={14} />
                    取消
                  </button>
                  <button
                    onClick={handleDelete}
                    disabled={deleteChapter.isPending}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border text-destructive hover:bg-destructive/10 disabled:opacity-50 transition-colors"
                  >
                    <Trash2 size={14} />
                    删除
                  </button>
                </>
              ) : (
                <>
                  <button
                    onClick={handleDelete}
                    disabled={deleteChapter.isPending}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border text-destructive hover:bg-destructive/10 disabled:opacity-50 transition-colors"
                  >
                    <Trash2 size={14} />
                    删除
                  </button>
                  <button
                    onClick={handleStartEdit}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
                  >
                    <Edit3 size={14} />
                    编辑
                  </button>
                  <button
                    onClick={playback.toggle}
                    disabled={!playback.hasSegments}
                    className={cn(
                      "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50",
                      playback.isPlaying ? "bg-amber-100 text-amber-700 hover:bg-amber-200" : "bg-primary/10 text-primary hover:bg-primary/20"
                    )}
                    title={playback.providerLabel}
                  >
                    {playback.isPlaying || playback.isLoadingAudio ? <Pause size={14} /> : <Play size={14} />}
                    {playback.isPlaying || playback.isLoadingAudio ? "暂停" : "播放"}
                  </button>
                  <button
                    onClick={() => setIsPresentationFullscreen(true)}
                    disabled={!isCoursewareChapter || !coursewareSlides.length}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border hover:bg-accent disabled:opacity-50 transition-colors"
                    title={isCoursewareChapter && coursewareSlides.length ? "全屏显示当前 TeX 课件页" : "当前课程没有可全屏展示的 TeX 课件页"}
                  >
                    <Maximize2 size={14} />
                    全屏授课
                  </button>
                  <button
                    onClick={() => playback.replay(currentSlide)}
                    disabled={!playback.hasSegments}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border hover:bg-accent disabled:opacity-50 transition-colors"
                    title={playback.providerLabel}
                  >
                    <RotateCcw size={14} />
                    重播当前页
                  </button>
                  <button
                    onClick={() => playback.regenerate(currentSlide)}
                    disabled={!playback.hasSegments || playback.isLoadingAudio}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border hover:bg-accent disabled:opacity-50 transition-colors"
                    title="重新生成当前页语音并覆盖缓存"
                  >
                    <RefreshCw size={14} className={playback.isLoadingAudio ? "animate-spin" : ""} />
                    重新生成语音
                  </button>
                </>
              )}
            </div>
          </div>

          {isEditing && editorMode === "edit" ? (
            <div className="p-4 sm:p-6">
              <label className="mb-2 block text-sm font-medium">Markdown / LaTeX 原文</label>
              <textarea
                value={draftContent}
                onChange={(event) => {
                  setDraftContent(event.target.value)
                  playback.reset(0)
                }}
                placeholder="输入授课文案，支持 Markdown、$...$ 和 $$...$$ 公式"
                className="min-h-[300px] w-full resize-y rounded-lg border bg-background px-3 py-2.5 font-mono text-sm leading-relaxed focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 sm:min-h-[420px]"
              />
            </div>
          ) : isEditing && editorMode === "preview" ? (
            <div className="min-h-[240px] p-4 sm:min-h-[300px] sm:p-6">
              {draftContent.trim() ? (
                <RichTextContent content={draftContent} />
              ) : (
                <EmptyState title="暂无预览内容" description="请先在编辑模式输入授课文案。" icon={<BookOpen size={48} />} />
              )}
            </div>
          ) : isCoursewareChapter && coursewareSlides.length ? (
            <>
              <PlaybackProgress
                progress={playback.progress}
                statusText={playback.statusText}
                audioPosition={playback.audioPosition}
                onSeek={playback.seekAudio}
              />
              <div>
                <div className="border-b p-4 xl:p-5">
                  {currentCoursewareSlide ? (
                    <div className="relative">
                      <PptSlidePreview
                        slide={currentCoursewareSlide}
                        assetMap={selectedChapter.asset_map || selectedChapter.editable_model?.assets || {}}
                        onNext={currentSlide < coursewareSlides.length - 1 ? () => playback.setCurrentSegment((current) => current + 1) : undefined}
                      />
                      <SlideSideNav
                        current={currentSlide}
                        total={coursewareSlides.length}
                        onPrev={() => playback.setCurrentSegment((current) => current - 1)}
                        onNext={() => playback.setCurrentSegment((current) => current + 1)}
                      />
                    </div>
                  ) : (
                    <EmptyState title="暂无页面" description="该课件缺少页面预览数据。" />
                  )}
                </div>
                <section className="border-b bg-muted/20 p-4 xl:p-5">
                  <div className="mx-auto max-w-5xl">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <div className="text-sm font-semibold">第 {currentCoursewareSlide?.index || currentSlide + 1} 页讲稿</div>
                      <div className="text-xs text-muted-foreground">只显示当前课件页对应文案</div>
                    </div>
                    {currentSlideLecture?.lecture ? (
                      <RichTextContent content={currentSlideLecture.lecture} />
                    ) : (
                      <EmptyState title="暂无本页讲稿" description="逐页讲稿需要在备课工作台生成；这里不会混用整章文案。" />
                    )}
                    {(currentSlideLecture?.learning_plan || currentSlideLecture?.sources?.length) && (
                      <div className="mt-4">
                        <EvidenceSummary
                          learningPlan={currentSlideLecture.learning_plan}
                          sources={currentSlideLecture.sources}
                        />
                      </div>
                    )}
                    <LectureReviewPanel
                      className="mt-4"
                      learningPlan={currentSlideLecture?.learning_plan}
                      sources={currentSlideLecture?.sources}
                      consistencyReport={currentSlideLecture?.consistency_report}
                    />
                  </div>
                </section>
              </div>

              <Pager
                current={currentSlide}
                total={coursewareSlides.length}
                onPrev={() => playback.setCurrentSegment((prev) => prev - 1)}
                onNext={() => playback.setCurrentSegment((prev) => prev + 1)}
                onJump={(next) => playback.setCurrentSegment(next)}
              />
            </>
          ) : markdownSlides.length > 0 ? (
            <>
              <PlaybackProgress
                progress={playback.progress}
                statusText={playback.statusText}
                audioPosition={playback.audioPosition}
                onSeek={playback.seekAudio}
              />
              <div className="min-h-[240px] p-4 sm:min-h-[300px] sm:p-6">
                <RichTextContent content={markdownSlides[currentSlide]} />
                <LectureReviewPanel
                  className="mt-6"
                  learningPlan={selectedChapter.lecture_learning_plan}
                  consistencyReport={selectedChapter.lecture_consistency_report}
                />
              </div>

              <Pager
                current={currentSlide}
                total={markdownSlides.length}
                onPrev={() => playback.setCurrentSegment((prev) => prev - 1)}
                onNext={() => playback.setCurrentSegment((prev) => prev + 1)}
                onJump={(next) => playback.setCurrentSegment(next)}
              />
            </>
          ) : (
            <div className="p-8">
              <EmptyState
                title="暂无授课文案"
                description="该课程尚未生成授课文稿，请在备课工作台生成。"
                icon={<BookOpen size={48} />}
              />
            </div>
          )}
          {isPresentationFullscreen && currentCoursewareSlide ? (
            <LectureFullscreenView
              slide={currentCoursewareSlide}
              assetMap={selectedChapter.asset_map || selectedChapter.editable_model?.assets || {}}
              playback={playback}
              current={currentSlide}
              total={coursewareSlides.length}
              onExit={() => setIsPresentationFullscreen(false)}
              onPrev={() => playback.setCurrentSegment((prev) => prev - 1)}
              onNext={() => playback.setCurrentSegment((prev) => prev + 1)}
              onJump={(next) => playback.setCurrentSegment(next)}
              onRegenerate={() => playback.regenerate(currentSlide)}
            />
          ) : null}
        </div>
      )}
    </div>
  )
}

function isInteractiveElement(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false
  return Boolean(target.closest("input, textarea, select, button, a, [contenteditable='true'], [role='slider']"))
}

function Pager({
  current,
  total,
  onPrev,
  onNext,
  onJump,
}: {
  current: number
  total: number
  onPrev: () => void
  onNext: () => void
  onJump: (next: number) => void
}) {
  return (
    <div className="grid gap-4 border-t bg-muted/25 p-4 md:grid-cols-[auto_minmax(220px,1fr)_auto] md:items-center">
      <button
        onClick={onPrev}
        disabled={current === 0}
        className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg border bg-background px-5 text-base font-medium shadow-sm transition-colors hover:bg-accent disabled:opacity-50"
      >
        <ChevronLeft size={20} />
        上一页
      </button>
      <div className="min-w-0 space-y-2">
        <div className="flex items-center justify-between gap-3 text-sm text-muted-foreground">
          <span>第 {current + 1} 页</span>
          <span>共 {total} 页</span>
        </div>
        <input
          type="range"
          min={1}
          max={Math.max(total, 1)}
          step={1}
          value={current + 1}
          aria-label="课件页面"
          onChange={(event) => onJump(Number(event.target.value) - 1)}
          className="h-3 w-full cursor-pointer accent-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </div>
      <button
        onClick={onNext}
        disabled={current === total - 1}
        className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-primary px-5 text-base font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:opacity-50"
      >
        下一页
        <ChevronRight size={20} />
      </button>
      <div className="text-center text-xs text-muted-foreground md:col-span-3">
        键盘：←/→、PageUp/PageDown 翻页，空格或 Enter 下一页，Home/End 跳到首尾
      </div>
    </div>
  )
}

function SlideSideNav({ current, total, onPrev, onNext }: { current: number; total: number; onPrev: () => void; onNext: () => void }) {
  return (
    <div className="pointer-events-none absolute inset-y-10 left-0 right-0 hidden items-center justify-between px-2 md:flex">
      <button
        type="button"
        onClick={onPrev}
        disabled={current === 0}
        aria-label="上一页"
        className="pointer-events-auto flex h-16 w-12 items-center justify-center rounded-lg border bg-background/90 text-foreground shadow-sm backdrop-blur transition hover:bg-accent disabled:opacity-30"
      >
        <ChevronLeft size={24} />
      </button>
      <button
        type="button"
        onClick={onNext}
        disabled={current === total - 1}
        aria-label="下一页"
        className="pointer-events-auto flex h-16 w-12 items-center justify-center rounded-lg border bg-background/90 text-foreground shadow-sm backdrop-blur transition hover:bg-accent disabled:opacity-30"
      >
        <ChevronRight size={24} />
      </button>
    </div>
  )
}

function LectureFullscreenView({
  slide,
  assetMap,
  playback,
  current,
  total,
  onExit,
  onPrev,
  onNext,
  onJump,
  onRegenerate,
}: {
  slide: PptSlideDetail
  assetMap: Record<string, CoursewareAsset>
  playback: ReturnType<typeof useLecturePlayback>
  current: number
  total: number
  onExit: () => void
  onPrev: () => void
  onNext: () => void
  onJump: (next: number) => void
  onRegenerate: () => void
}) {
  const images = collectSlideImages(slide, assetMap)
  const hasVisualContent = Boolean(slide.title || slide.content || slide.raw_text || images.length || slide.tables?.length)
  const canSeek = Boolean(playback.audioPosition.seekable)
  const pageAspect = renderedPageAspectNumber(slide.rendered_page)

  return (
    <div className="fixed inset-0 z-[80] flex flex-col bg-slate-950 text-white">
      <main className="flex min-h-0 flex-1 items-center justify-center p-3 sm:p-5">
        <div
          className="relative max-h-[calc(100dvh-150px)] overflow-hidden rounded-sm bg-white text-slate-950 shadow-2xl ring-1 ring-white/15"
          style={{
            aspectRatio: renderedPageAspectRatio(slide.rendered_page),
            width: `min(100%, calc((100dvh - 150px) * ${pageAspect}))`,
          }}
        >
          {hasVisualContent ? (
            <ReadonlySlideFrame slide={slide} images={images} assetMap={assetMap} />
          ) : (
            <div className="flex h-full items-center justify-center px-6 text-center text-sm text-slate-500">该页暂无可展示内容</div>
          )}
        </div>
      </main>

      <footer className="border-t border-white/10 bg-slate-950/95 px-3 py-3 shadow-2xl sm:px-5">
        <div className="mx-auto grid max-w-7xl gap-3 lg:grid-cols-[auto_minmax(220px,1fr)_auto_auto_minmax(240px,0.65fr)_auto] lg:items-center">
          <button
            type="button"
            onClick={onPrev}
            disabled={current === 0}
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-white/15 bg-white/8 px-4 text-sm font-medium text-white transition hover:bg-white/15 disabled:opacity-35"
          >
            <ChevronLeft size={18} />
            上一页
          </button>
          <div className="min-w-0 space-y-1">
            <div className="flex items-center justify-between gap-3 text-xs text-white/70">
              <span>第 {current + 1} 页</span>
              <span>共 {total} 页</span>
            </div>
            <input
              type="range"
              min={1}
              max={Math.max(total, 1)}
              step={1}
              value={current + 1}
              aria-label="全屏课件页面"
              onChange={(event) => onJump(Number(event.target.value) - 1)}
              className="h-2 w-full cursor-pointer accent-primary"
            />
          </div>
          <button
            type="button"
            onClick={onNext}
            disabled={current === total - 1}
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 disabled:opacity-35"
          >
            下一页
            <ChevronRight size={18} />
          </button>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={playback.toggle}
              disabled={!playback.hasSegments}
              className={cn(
                "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 text-sm font-medium transition disabled:opacity-35",
                playback.isPlaying ? "bg-amber-200 text-slate-950 hover:bg-amber-100" : "bg-white text-slate-950 hover:bg-white/90",
              )}
              title={playback.providerLabel}
            >
              {playback.isPlaying || playback.isLoadingAudio ? <Pause size={18} /> : <Play size={18} />}
              {playback.isPlaying || playback.isLoadingAudio ? "暂停" : "播放"}
            </button>
            <button
              type="button"
              onClick={() => playback.replay(current)}
              disabled={!playback.hasSegments}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-white/15 bg-white/8 px-4 text-sm font-medium text-white transition hover:bg-white/15 disabled:opacity-35"
              title={playback.providerLabel}
            >
              <RotateCcw size={17} />
              重播
            </button>
            <button
              type="button"
              onClick={onRegenerate}
              disabled={!playback.hasSegments || playback.isLoadingAudio}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-white/15 bg-white/8 px-4 text-sm font-medium text-white transition hover:bg-white/15 disabled:opacity-35"
              title="重新生成当前页语音并覆盖缓存"
            >
              <RefreshCw size={17} className={playback.isLoadingAudio ? "animate-spin" : ""} />
              重生成
            </button>
          </div>
          <div className="min-w-0 space-y-1">
            <div className="truncate text-xs text-white/70">{playback.statusText}</div>
            <input
              type="range"
              min={0}
              max={100}
              step={0.1}
              value={playback.audioPosition.percent || 0}
              disabled={!canSeek}
              aria-label="全屏语音播放进度"
              onChange={(event) => playback.seekAudio(Number(event.target.value))}
              className="h-2 w-full cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-35"
            />
          </div>
          <button
            type="button"
            onClick={onExit}
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-white/15 bg-white/8 px-4 text-sm font-medium text-white transition hover:bg-white/15"
          >
            <Minimize2 size={17} />
            退出
          </button>
        </div>
      </footer>
    </div>
  )
}

function PptSlidePreview({
  slide,
  assetMap = {},
  onNext,
}: {
  slide: PptSlideDetail
  assetMap?: Record<string, CoursewareAsset>
  onNext?: () => void
}) {
  const images = collectSlideImages(slide, assetMap)
  const hasVisualContent = Boolean(slide.title || slide.content || slide.raw_text || images.length || slide.tables?.length)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">页面 {slide.index}</div>
        <div className="text-xs text-muted-foreground">{formatSlideKind(slide)}</div>
      </div>

      <div className="mx-auto w-full max-w-5xl">
        <div
          role={onNext ? "button" : undefined}
          tabIndex={onNext ? 0 : undefined}
          onClick={onNext}
          className="relative block w-full overflow-hidden rounded-lg border bg-white text-left text-slate-950 shadow-sm transition hover:border-primary/70 focus:outline-none focus:ring-2 focus:ring-primary/30"
          style={{ aspectRatio: renderedPageAspectRatio(slide.rendered_page) }}
          title="点击课件区域进入下一页"
        >
          {hasVisualContent ? (
            <ReadonlySlideFrame slide={slide} images={images} assetMap={assetMap} />
          ) : (
            <div className="flex h-full items-center justify-center px-6 text-center text-sm text-slate-500">该页暂无可展示内容</div>
          )}
        </div>
      </div>

      {slide.notes && (
        <section className="rounded-lg border bg-muted/35 p-3">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">备注</div>
          <div className="mt-2 text-sm">
            <RichTextContent content={slide.notes} />
          </div>
        </section>
      )}
    </div>
  )
}

function ReadonlySlideFrame({ slide, images, assetMap }: { slide: PptSlideDetail; images: SlideImage[]; assetMap: Record<string, CoursewareAsset> }) {
  if (slide.rendered_page?.image) {
    return <CompiledSlideFrame slide={slide} />
  }
  if (slide.layout?.canvas?.items?.length) {
    return <CanvasSlidePreview slide={slide} images={images} assetMap={assetMap} />
  }
  if (slide.layout?.mode === "title") {
    return <TitleSlidePreview slide={slide} images={images} />
  }
  if (slide.layout?.mode === "columns" && slide.layout.columns?.length) {
    return <ColumnSlidePreview slide={slide} columns={slide.layout.columns} assetMap={assetMap} />
  }
  if (slide.layout?.mode === "image_only" && images.length) {
    return <ImageOnlySlidePreview slide={slide} images={images} />
  }
  if ((slide.layout?.mode === "image_text" || slide.layout?.mode === "text_image") && images.length) {
    return <ImageTextSlidePreview slide={slide} images={images} />
  }
  return <DefaultSlidePreview slide={slide} images={images} />
}

function CompiledSlideFrame({ slide }: { slide: PptSlideDetail }) {
  return (
    <div className="h-full w-full bg-white">
      <img
        src={slide.rendered_page?.image || ""}
        alt={slide.title || `PDF rendered slide ${slide.index}`}
        className="h-full w-full object-contain"
        draggable={false}
      />
    </div>
  )
}

function renderedPageAspectRatio(page: PptSlideDetail["rendered_page"]) {
  const width = Number(page?.width)
  const height = Number(page?.height)
  return width > 0 && height > 0 ? `${width} / ${height}` : "16 / 9"
}

function renderedPageAspectNumber(page: PptSlideDetail["rendered_page"]) {
  const width = Number(page?.width)
  const height = Number(page?.height)
  return width > 0 && height > 0 ? width / height : 16 / 9
}

function TitleSlidePreview({ slide, images }: { slide: PptSlideDetail; images: SlideImage[] }) {
  const footerImage = titleFooterImage(images)
  const logoImages = images.filter((image) => image !== footerImage).slice(0, 2)
  const detailLines = titleDetailLines(slide)
  return (
    <div className="relative h-full w-full overflow-hidden bg-white text-slate-950">
      <div className="absolute inset-x-0 top-[7.1%] h-[3px] bg-[#007470]" />
      <div className="absolute left-0 top-0 flex h-[7%] w-[25%] items-center justify-center bg-slate-200 px-3 text-center text-[clamp(0.52rem,0.82vw,0.82rem)] leading-tight text-slate-900">
        Public course in BIMSA in 2026 spring semester
      </div>
      <div className="absolute right-[0.5%] top-[0.5%] flex h-[6.8%] items-start justify-end gap-1">
        {logoImages.map((image, index) => (
          <div key={`${image.source_path || image.tex_ref || index}-${index}`} className="h-full w-[8.8%] min-w-[54px]">
            {image.data_uri ? (
              <img src={image.data_uri} alt={image.source_path || `标题页 logo ${index + 1}`} className="h-full w-full object-contain" />
            ) : (
              <MissingImageBox image={image} />
            )}
          </div>
        ))}
      </div>

      <div className="absolute inset-x-[10%] top-[23%] flex flex-col items-center text-center">
        <div className="bg-white px-[3%] py-[2%] text-[clamp(1.35rem,3vw,3.1rem)] font-bold leading-tight text-slate-950">
          {slide.title || `第 ${slide.index} 页`}
        </div>
        {detailLines[0] ? (
          <div className="mt-[1.4%] max-w-[82%] rounded-sm bg-white px-[2.2%] py-[1.2%] text-[clamp(0.78rem,1.45vw,1.45rem)] leading-snug text-slate-950">
            {detailLines[0]}
          </div>
        ) : null}
        {detailLines.length > 1 ? (
          <div className="mt-[1.4%] max-w-[68%] bg-white px-[2%] py-[1%] text-[clamp(0.66rem,1.05vw,1.05rem)] leading-relaxed text-slate-950">
            {detailLines.slice(1).map((line, index) => (
              <div key={`${line}-${index}`}>{line}</div>
            ))}
          </div>
        ) : null}
      </div>

      {footerImage ? (
        <div className="absolute inset-x-0 bottom-0 h-[11%] overflow-hidden">
          {footerImage.data_uri ? (
            <img src={footerImage.data_uri} alt={footerImage.source_path || "标题页页脚"} className="h-full w-full object-cover" />
          ) : (
            <MissingImageBox image={footerImage} />
          )}
        </div>
      ) : null}
    </div>
  )
}

function DefaultSlidePreview({ slide, images }: { slide: PptSlideDetail; images: SlideImage[] }) {
  return (
    <>
      <div className="absolute inset-x-0 top-0 h-1 bg-primary" />
      <div className="flex h-full flex-col p-[4.8%]">
        <SlideTitle slide={slide} />
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-5 pt-4 lg:grid-cols-[minmax(0,1fr)_minmax(210px,0.42fr)]">
          <div className="min-h-0 overflow-hidden text-[clamp(0.82rem,1.12vw,1.08rem)] leading-relaxed text-slate-800">
            <RichTextContent content={slide.content || slide.raw_text || "无正文文本"} className="lecture-slide-prose" />
          </div>
          {(images.length > 0 || !!slide.tables?.length) && (
            <div className="min-h-0 space-y-3 overflow-hidden">
              <SlideImageStrip images={images} />
              <SlideTableStrip slide={slide} />
            </div>
          )}
        </div>
      </div>
    </>
  )
}

function SlideTitle({ slide }: { slide: PptSlideDetail }) {
  return (
    <div className="min-h-[13%] border-b border-slate-200 pb-3">
      <h3 className="text-balance text-[clamp(1.15rem,2.2vw,2.45rem)] font-semibold leading-tight tracking-normal text-slate-950">
        {slide.title || `第 ${slide.index} 页`}
      </h3>
    </div>
  )
}

function titleFooterImage(images: SlideImage[]) {
  return images.find((image) => {
    const source = `${image.source_path || ""} ${image.tex_ref || ""}`.toLowerCase()
    const options = String(image.tex_options || "")
    return options.includes("\\paperwidth") || source.includes("图片3") || source.includes("picture3")
  }) || images[images.length - 1]
}

function titleDetailLines(slide: PptSlideDetail) {
  const text = String(slide.content || slide.raw_text || "")
  return text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter((line) => line && line !== slide.title)
}

function ColumnSlidePreview({ slide, columns, assetMap }: { slide: PptSlideDetail; columns: SlideLayoutColumn[]; assetMap: Record<string, CoursewareAsset> }) {
  const visibleColumns = columns.filter((column) => column.content?.trim() || column.images?.length)
  return (
    <>
      <div className="absolute inset-x-0 top-0 h-1 bg-primary" />
      <div className="flex h-full flex-col p-[4.8%]">
        <SlideTitle slide={slide} />
        {slide.layout?.outside_content ? (
          <div className="pt-3 text-[clamp(0.72rem,0.95vw,0.92rem)] leading-relaxed text-slate-700">
            <RichTextContent content={slide.layout.outside_content} className="lecture-slide-prose" />
          </div>
        ) : null}
        <div
          className="grid min-h-0 flex-1 gap-5 pt-4"
          style={{ gridTemplateColumns: buildColumnTemplate(visibleColumns) }}
        >
          {visibleColumns.map((column, index) => (
            <div key={index} className="min-h-0 overflow-hidden text-[clamp(0.78rem,1.02vw,1rem)] leading-relaxed text-slate-800">
              {column.image_first ? <SlideImageStrip images={(column.images || []).map((image) => hydrateImage(image, assetMap))} /> : null}
              {column.content?.trim() ? <RichTextContent content={column.content} className="lecture-slide-prose" /> : null}
              {!column.image_first ? <SlideImageStrip images={(column.images || []).map((image) => hydrateImage(image, assetMap))} /> : null}
            </div>
          ))}
        </div>
      </div>
    </>
  )
}

function ImageOnlySlidePreview({ slide, images }: { slide: PptSlideDetail; images: SlideImage[] }) {
  return (
    <div className="flex h-full flex-col p-[4.8%]">
      <SlideTitle slide={slide} />
      <div className="min-h-0 flex-1 pt-4">
        <SlideImageStrip images={images} containerClassName="grid h-full max-h-none gap-2" imageClassName="h-full max-h-full w-full object-contain" />
      </div>
    </div>
  )
}

function ImageTextSlidePreview({ slide, images }: { slide: PptSlideDetail; images: SlideImage[] }) {
  const imageFirst = Boolean(slide.layout?.image_first || slide.layout?.mode === "image_text")
  const imagePane = (
    <div className="min-h-0 overflow-hidden">
      <SlideImageStrip images={images} containerClassName="grid h-full max-h-none gap-2" imageClassName="h-full max-h-full w-full object-contain" />
    </div>
  )
  const textPane = (
    <div className="min-h-0 overflow-hidden text-[clamp(0.82rem,1.1vw,1.05rem)] leading-relaxed text-slate-800">
      <RichTextContent content={slide.content || slide.raw_text || ""} className="lecture-slide-prose" />
    </div>
  )
  return (
    <>
      <div className="absolute inset-x-0 top-0 h-1 bg-primary" />
      <div className="flex h-full flex-col p-[4.8%]">
        <SlideTitle slide={slide} />
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-5 pt-4 lg:grid-cols-2">
          {imageFirst ? imagePane : textPane}
          {imageFirst ? textPane : imagePane}
        </div>
      </div>
    </>
  )
}

function CanvasSlidePreview({ slide, images, assetMap }: { slide: PptSlideDetail; images: SlideImage[]; assetMap: Record<string, CoursewareAsset> }) {
  const items = canvasLayoutFromSlide(slide)
  return (
    <div className="relative h-full w-full bg-white">
      {items.map((item) => {
        if (item.type === "image") {
          const image = images.find((candidate, imageIndex) => imageMatchesCanvasItem(candidate, item, imageIndex)) || imageForCanvasItem(items, item, images)
          const asset = image ? findAssetForImage(image, assetMap) : undefined
          return (
            <CanvasBox key={item.id} item={item}>
              {asset?.data_uri || image?.data_uri ? (
                <img src={asset?.data_uri || image?.data_uri || ""} alt={asset?.source_path || image?.source_path || "课件图片"} className="h-full w-full object-contain" />
              ) : (
                <MissingImageBox image={image} />
              )}
            </CanvasBox>
          )
        }
        if (item.type === "title") {
          return (
            <CanvasBox key={item.id} item={item}>
              <div className="flex h-full items-center text-[clamp(1rem,2.1vw,2.25rem)] font-semibold leading-tight text-slate-950">
                {slide.title || `第 ${slide.index} 页`}
              </div>
            </CanvasBox>
          )
        }
        return (
          <CanvasBox key={item.id} item={item}>
            <div className="h-full overflow-hidden text-[clamp(0.74rem,1.02vw,1rem)] leading-relaxed text-slate-800">
              <RichTextContent content={slide.content || slide.raw_text || ""} className="lecture-slide-prose" />
            </div>
          </CanvasBox>
        )
      })}
    </div>
  )
}

function CanvasBox({ item, children }: { item: CanvasItem; children: ReactNode }) {
  return (
    <div
      className="absolute overflow-hidden p-1"
      style={{
        left: `${(item.x / CANVAS_WIDTH) * 100}%`,
        top: `${(item.y / CANVAS_HEIGHT) * 100}%`,
        width: `${(item.width / CANVAS_WIDTH) * 100}%`,
        height: `${(item.height / CANVAS_HEIGHT) * 100}%`,
      }}
    >
      {children}
    </div>
  )
}

function SlideImageStrip({
  images,
  containerClassName,
  imageClassName = "h-full min-h-0 w-full object-contain",
}: {
  images: PptSlideDetail["images"]
  containerClassName?: string
  imageClassName?: string
}) {
  if (!images?.length) return null
  const gridClassName = containerClassName || slideImageStripGridClass(images.length)
  return (
    <div className={gridClassName}>
      {images.map((image, index) => (
        <figure key={`${image.source_path || image.tex_ref || index}-${index}`} className="flex min-h-0 overflow-hidden rounded border border-slate-200 bg-slate-50 p-1.5">
          {image.data_uri ? (
            <img src={image.data_uri} alt={image.source_path || `课件图片 ${index + 1}`} className={imageClassName} />
          ) : (
            <MissingImageBox image={image} />
          )}
        </figure>
      ))}
    </div>
  )
}

function slideImageStripGridClass(count: number) {
  if (count <= 1) return "grid h-full min-h-0 gap-2"
  if (count === 2) return "grid h-full min-h-0 grid-cols-2 gap-2"
  return "grid h-full min-h-0 grid-cols-3 gap-2"
}

function MissingImageBox({ image }: { image?: SlideImage }) {
  return (
    <div className="flex h-24 min-h-full items-center justify-center px-2 text-center text-xs text-slate-500">
      {image?.oversized ? "图片过大，未内嵌预览" : image?.source_path || image?.tex_ref ? `图片：${image.source_path || image.tex_ref}` : "图片无法预览"}
    </div>
  )
}

function SlideTableStrip({ slide }: { slide: PptSlideDetail }) {
  if (!slide.tables?.length) return null
  return (
    <div className="max-h-44 overflow-hidden rounded border border-slate-200 bg-white text-[0.62rem] text-slate-700">
      {slide.tables[0].rows.slice(0, 5).map((row, rowIndex) => (
        <div key={rowIndex} className="grid grid-flow-col auto-cols-fr border-b border-slate-200 last:border-b-0">
          {row.slice(0, 4).map((cell, cellIndex) => (
            <div key={cellIndex} className="min-w-0 border-r border-slate-200 px-1.5 py-1 last:border-r-0">
              <RichTextContent content={cell} inline />
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function PptSlideImages({ slide }: { slide: PptSlideDetail }) {
  const images = slide.images || []
  if (!images.length) {
    return <div className="text-sm text-muted-foreground">图片数量：{slide.image_count || 0}</div>
  }

  return (
    <div>
      <div className="text-xs font-medium uppercase text-muted-foreground">图片</div>
      <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">
        {images.map((image, index) => (
          <figure key={`${image.source_path || index}-${index}`} className="rounded-lg border bg-background p-2">
            {image.data_uri ? (
              <img
                src={image.data_uri}
                alt={image.source_path || `课件图片 ${index + 1}`}
                className="max-h-80 w-full rounded object-contain"
              />
            ) : (
              <div className="flex min-h-32 items-center justify-center rounded bg-muted px-3 text-center text-xs text-muted-foreground">
                {image.oversized ? "图片过大，未内嵌预览" : "图片无法预览"}
              </div>
            )}
            {image.source_path ? <figcaption className="mt-2 truncate text-xs text-muted-foreground">{image.source_path}</figcaption> : null}
          </figure>
        ))}
      </div>
    </div>
  )
}

function buildLectureSlides(chapter?: Chapter): PptSlideDetail[] {
  if (!chapter) return []
  if (chapter.ppt_slides?.length) return attachRenderedPagesToSlides(chapter.ppt_slides, chapter.rendered_pages)
  const editableSlides = chapter.editable_model?.slides || []
  if (editableSlides.length) {
    const assetMap = { ...(chapter.asset_map || {}), ...(chapter.editable_model?.assets || {}) }
    return attachRenderedPagesToSlides(
      editableSlides.map((slide, index) => slideFromEditableSlide(slide, index + 1, assetMap)),
      chapter.rendered_pages,
    )
  }
  if (chapter.tex_content?.trim()) return attachRenderedPagesToSlides(parseTexSlides(chapter.tex_content), chapter.rendered_pages)
  return []
}

function attachRenderedPagesToSlides(
  slides: PptSlideDetail[],
  renderedPages: NonNullable<Chapter["rendered_pages"]> | undefined,
): PptSlideDetail[] {
  if (!renderedPages?.length) return slides
  return slides.map((slide, index) => ({
    ...slide,
    rendered_page:
      slide.rendered_page ||
      renderedPages.find((page) => slide.slide_id && page.slide_id === slide.slide_id) ||
      renderedPages.find((page) => page.page_index === (slide.rendered_page_index ?? slide.index - 1)) ||
      renderedPages[index],
  }))
}

function slideFromEditableSlide(
  slide: NonNullable<Chapter["editable_model"]>["slides"][number],
  fallbackIndex: number,
  assetMap: Record<string, CoursewareAsset>,
): PptSlideDetail {
  const objects = dedupeEditableObjects([...(slide.objects || []), ...(slide.items || [])])
  const titleObject = objects.find((item) => item.type === "title" || item.role === "title")
  const contentBlocks = objects
    .filter((item) => item !== titleObject && item.type !== "image" && item.type !== "placeholder")
    .map(formatEditableObject)
    .filter(Boolean)
  const imageObjects = objects.filter((item) => item.type === "image" || item.type === "placeholder")
  const images = imageObjects.map((object) => imageFromEditableObject(object, assetMap))
  const tables = objects.filter((item) => item.rows?.length).map((item) => ({ rows: item.rows || [] }))

  return {
    index: slide.index || fallbackIndex,
    title: slide.title || titleObject?.text || titleObject?.title || `第 ${slide.index || fallbackIndex} 页`,
    content: contentBlocks.join("\n\n"),
    notes: slide.notes,
    images,
    image_count: images.length,
    has_images: images.length > 0,
    tables,
    raw_text: contentBlocks.join("\n"),
    source_tex: slide.source_tex,
    source_body_tex: slide.source_body_tex,
    layout: slide.layout,
  }
}

function dedupeEditableObjects(objects: EditableSlideObject[]) {
  const seen = new Set<string>()
  return objects.filter((object, index) => {
    const key = object.id || `${object.type}-${index}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function formatEditableObject(object: EditableSlideObject) {
  if (object.type === "equation" || object.latex) return object.latex ? `$$\n${object.latex}\n$$` : ""
  if (object.rows?.length) return object.rows.map((row) => row.join(" | ")).join("\n")
  return String(object.text || object.rich_html || object.label || "").trim()
}

function imageFromEditableObject(object: EditableSlideObject, assetMap: Record<string, CoursewareAsset>): NonNullable<PptSlideDetail["images"]>[number] {
  const asset = object.asset_id ? assetMap[object.asset_id] : undefined
  return {
    data_uri: asset?.data_uri || null,
    width_emu: 0,
    height_emu: 0,
    left_emu: 0,
    top_emu: 0,
    source_path: object.source_path || asset?.source_path || asset?.name,
    tex_ref: object.tex_ref || asset?.tex_ref,
    width_ratio: object.width_ratio,
    oversized: asset?.oversized,
  }
}

function collectSlideImages(slide: PptSlideDetail, assetMap: Record<string, CoursewareAsset>) {
  const images: SlideImage[] = []
  const seen = new Set<string>()
  const add = (image?: SlideImage) => {
    if (!image) return
    const hydrated = hydrateImage(image, assetMap)
    const key = normalizeAssetKey(hydrated.source_path || hydrated.tex_ref || `${images.length}`)
    if (seen.has(key)) return
    seen.add(key)
    images.push(hydrated)
  }
  ;(slide.images || []).forEach(add)
  ;(slide.layout?.columns || []).forEach((column) => (column.images || []).forEach(add))
  return images
}

function hydrateImage(image: SlideImage, assetMap: Record<string, CoursewareAsset>): SlideImage {
  if (image.data_uri) return image
  const assets = Object.values(assetMap)
  const key = normalizeAssetKey(image.source_path || image.tex_ref || "")
  const asset = assets.find((candidate) => {
    const candidates = [candidate.id, candidate.source_path, candidate.tex_ref, candidate.name, ...(candidate.aliases || [])]
    return candidates.some((item) => normalizeAssetKey(item || "") === key)
  })
  return asset?.data_uri ? { ...image, data_uri: asset.data_uri, oversized: asset.oversized } : image
}

function findAssetForImage(image: SlideImage, assetMap: Record<string, CoursewareAsset>) {
  const keys = new Set([image.source_path, image.tex_ref, normalizeAssetKey(image.source_path || ""), normalizeAssetKey(image.tex_ref || "")].filter(Boolean))
  return Object.values(assetMap).find((asset) => {
    const candidates = [asset.id, asset.source_path, asset.tex_ref, asset.name, ...(asset.aliases || [])]
    return candidates.some((candidate) => keys.has(candidate || "") || keys.has(normalizeAssetKey(candidate || "")))
  })
}

function buildColumnTemplate(columns: SlideLayout["columns"]) {
  const safeColumns = columns || []
  if (safeColumns.length <= 1) return "minmax(0, 1fr)"
  return safeColumns
    .map((column) => {
      const width = typeof column.width_ratio === "number" && Number.isFinite(column.width_ratio) ? column.width_ratio : 1
      return `minmax(0, ${Math.max(width, 0.2)}fr)`
    })
    .join(" ")
}

function canvasLayoutFromSlide(slide: PptSlideDetail): CanvasItem[] {
  const savedItems = Array.isArray(slide.layout?.canvas?.items) ? slide.layout?.canvas?.items : []
  return savedItems
    .map((item, index) => {
      const type: CanvasItemKind = item.type === "image" || item.type === "title" ? item.type : "content"
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

function imageForCanvasItem(items: CanvasItem[], item: CanvasItem, images: SlideImage[]) {
  const imagePosition = items.filter((candidate) => candidate.type === "image").findIndex((candidate) => candidate.id === item.id)
  return imagePosition >= 0 ? images[imagePosition] : undefined
}

function clampCanvasNumber(value: unknown, fallback: number, min: number, max: number) {
  const numeric = typeof value === "number" && Number.isFinite(value) ? value : fallback
  return Math.min(Math.max(numeric, min), max)
}

function imageMatchesCanvasItem(image: SlideImage, item: CanvasItem, index: number) {
  const ref = normalizeAssetKey(item.ref || "")
  if (!ref) return item.id === `image-${index}` || item.id.endsWith(String(index))
  return [image.source_path, image.tex_ref].some((value) => normalizeAssetKey(value || "") === ref)
}

function normalizeAssetKey(value: string) {
  return (
    String(value || "")
      .replace(/\\/g, "/")
      .split("/")
      .pop()
      ?.replace(/\.(png|jpe?g|gif|webp|bmp|svg)$/i, "")
      .toLowerCase()
      .trim() || ""
  )
}

function parseTexSlides(texContent: string): PptSlideDetail[] {
  const source = texContent.replace(/\r\n/g, "\n").replace(/\r/g, "\n")
  const frames = Array.from(source.matchAll(/\\begin\{frame\}([\s\S]*?)\\end\{frame\}/g))
  if (!frames.length) {
    const readable = texToReadableMarkdownOverleaf(source)
    return readable ? [{ index: 1, title: "TeX 课件", content: readable, raw_text: readable, source_tex: source }] : []
  }
  return frames.map((match, index) => {
    const frameSource = match[0]
    const body = match[1] || ""
    const title = extractFrameTitle(frameSource) || `第 ${index + 1} 页`
    const bodyWithoutTitle = removeFrameTitle(body)
    const images = extractTexImages(bodyWithoutTitle)
    const layout = inferTexFrameLayout(bodyWithoutTitle, images)
    const content = texToReadableMarkdownOverleaf(bodyWithoutTitle)
    return {
      index: index + 1,
      slide_id: `frame-${index + 1}-overlay-1`,
      parent_slide_index: index + 1,
      overlay_index: 1,
      overlay_count: 1,
      title,
      content,
      raw_text: content,
      source_tex: frameSource,
      source_body_tex: body,
      images,
      image_count: images.length,
      has_images: images.length > 0,
      layout,
    }
  })
}

function extractTexImages(value: string): SlideImage[] {
  const images: SlideImage[] = []
  const seen = new Set<string>()
  const pattern = /\\(?<cmd>includegraphics|safecontentimage|safeverticalimage|safelogoimage)(?:\[(?<options>[^\]]*)\])?\{(?<path>[^}]+)\}/g
  for (const match of value.matchAll(pattern)) {
    const sourcePath = String(match.groups?.path || "").trim()
    if (!sourcePath || seen.has(sourcePath)) continue
    seen.add(sourcePath)
    const command = match.groups?.cmd || "includegraphics"
    const options = match.groups?.options || ""
    const widthRatio = extractTexWidthRatio(options, command)
    images.push({
      data_uri: null,
      width_emu: 0,
      height_emu: 0,
      left_emu: 0,
      top_emu: 0,
      source_path: sourcePath,
      tex_ref: sourcePath,
      width_ratio: widthRatio,
    })
  }
  return images
}

function extractTexWidthRatio(options: string, command: string) {
  if (command === "safeverticalimage") return 0.46
  if (command === "safecontentimage") return 0.7
  const widthMatch = /width\s*=\s*([0-9.]+)\s*\\textwidth/.exec(options)
  if (widthMatch?.[1]) return Math.min(Math.max(Number(widthMatch[1]), 0.05), 1)
  return undefined
}

function inferTexFrameLayout(body: string, images: SlideImage[]): PptSlideDetail["layout"] {
  const columns = extractTexColumns(body)
  const contentWithoutColumns = removeTexColumnsBlocks(body)
  const outsideContent = columns.length ? texToReadableMarkdownOverleaf(contentWithoutColumns) : ""
  if (columns.length > 1) {
    return {
      mode: "columns",
      has_columns: true,
      column_count: columns.length,
      columns,
      outside_content: outsideContent,
      image_count: images.length,
    }
  }
  const contentWithoutImages = texToReadableMarkdownOverleaf(stripTexImages(body))
  const imageFirst = firstTexImageComesBeforeText(body)
  const imageOnly = images.length > 0 && !contentWithoutImages.trim()
  if (/\\titlepage\b/.test(body)) {
    return { mode: "title", has_columns: false, column_count: 0, columns: [], image_count: images.length }
  }
  if (imageOnly) {
    return { mode: "image_only", has_columns: false, column_count: 0, columns: [], image_count: images.length, image_first: true }
  }
  if (images.length) {
    return {
      mode: imageFirst ? "image_text" : "text_image",
      has_columns: false,
      column_count: 0,
      columns: [],
      image_count: images.length,
      image_first: imageFirst,
      max_image_width: images[0]?.width_ratio,
    }
  }
  return { mode: "text", has_columns: columns.length > 0, column_count: columns.length, columns, outside_content: outsideContent, image_count: 0 }
}

function extractTexColumns(body: string): SlideLayoutColumn[] {
  const columns: SlideLayoutColumn[] = []
  const columnPattern = /\\begin\{column\}(?:\[(?:[^\]]*)\])?\{([^}]*)\}([\s\S]*?)\\end\{column\}/g
  for (const match of body.matchAll(columnPattern)) {
    const columnSource = match[2] || ""
    const columnImages = extractTexImages(columnSource)
    columns.push({
      width_ratio: parseColumnWidthRatio(match[1]),
      content: texToReadableMarkdownOverleaf(stripTexImages(columnSource)),
      images: columnImages,
      image_count: columnImages.length,
      image_first: firstTexImageComesBeforeText(columnSource),
      source_tex: match[0],
    })
  }
  return columns
}

function parseColumnWidthRatio(value: string) {
  const numeric = /([0-9.]+)\s*\\textwidth/.exec(value || "")
  if (numeric?.[1]) return Math.min(Math.max(Number(numeric[1]), 0.05), 1)
  return null
}

function removeTexColumnsBlocks(value: string) {
  return value.replace(/\\begin\{columns\}(?:\[[^\]]*\])?[\s\S]*?\\end\{columns\}/g, "")
}

function stripTexImages(value: string) {
  return value.replace(/\\(?:includegraphics|safecontentimage|safeverticalimage|safelogoimage)(?:\[[^\]]*\])?\{[^}]+\}/g, "")
}

function firstTexImageComesBeforeText(value: string) {
  const imageIndex = value.search(/\\(?:includegraphics|safecontentimage|safeverticalimage|safelogoimage)(?:\[[^\]]*\])?\{[^}]+\}/)
  if (imageIndex < 0) return false
  const before = texToReadableMarkdownOverleaf(stripTexImages(value.slice(0, imageIndex))).trim()
  return !before
}

function extractFrameTitle(frameSource: string) {
  const beginTitle = /\\begin\{frame\}(?:\s*(?:<[^>]*>|\[[^\]]*\]))*\s*\{([^{}]*)\}/.exec(frameSource)
  if (beginTitle?.[1]) return cleanTexInline(beginTitle[1])
  const frameTitle = /\\frametitle(?:\s*(?:<[^>]*>|\[[^\]]*\]))*\s*\{([^{}]*)\}/.exec(frameSource)
  if (frameTitle?.[1]) return cleanTexInline(frameTitle[1])
  return ""
}

function removeFrameTitle(value: string) {
  return value
    .replace(/^\s*(?:<[^>]*>|\[[^\]]*\])*\s*\{[^{}]*\}/, "")
    .replace(/\\frametitle(?:\s*(?:<[^>]*>|\[[^\]]*\]))*\s*\{[^{}]*\}/g, "")
}

function texToReadableMarkdownOverleaf(value: string) {
  const mathBlocks: string[] = []
  const storeMath = (formula: string, display = true) => {
    const marker = `@@KGTS_MATH_${mathBlocks.length}@@`
    mathBlocks.push(display ? `\n\n$$\n${normalizePreviewFormula(formula)}\n$$\n\n` : `$${formula.trim()}$`)
    return marker
  }

  const cleaned = value
    .replace(/%.*$/gm, "")
    .replace(/\\begin\{(equation|align|alignat|aligned|alignedat|flalign|gather|gathered|multline|split|cases|matrix|pmatrix|bmatrix|vmatrix|Vmatrix)\*?\}((?:\s*(?:\[[^\]]*\]|\{[^{}]*\}))*)([\s\S]*?)\\end\{\1\*?\}/g, (_match, env, args, formula) => {
      return storeMath(normalizePreviewEnvironment(String(env), String(args || ""), String(formula || "")))
    })
    .replace(/\\\[((?:.|\n)*?)\\\]/g, (_match, formula) => storeMath(String(formula)))
    .replace(/\$\$([\s\S]*?)\$\$/g, (_match, formula) => storeMath(String(formula)))
    .replace(/\\\(((?:.|\n)*?)\\\)/g, (_match, formula) => storeMath(String(formula), false))
    .replace(/\\begin\{(?:itemize|enumerate)\}/g, "\n")
    .replace(/\\end\{(?:itemize|enumerate)\}/g, "\n")
    .replace(/\\item(?:<[^>]*>)?(?:\[[^\]]*\])?/g, "\n- ")
    .replace(/\\(?:includegraphics|safecontentimage|safeverticalimage|safelogoimage)(?:\[[^\]]*\])?\{([^}]+)\}/g, "\n\n[图：$1]\n\n")
    .replace(/\\(?:begin|end)\{(?:center|columns|column|minipage|block|alertblock|exampleblock|tikzpicture|picture|scope)\}(?:\[[^\]]*\])?(?:\{[^{}]*\})*/g, "\n")
    .replace(/\\(?:textcolor|colorbox|href|parbox|makebox)(?:\[[^\]]*\])?\{[^{}]*\}\{([^{}]*)\}/g, "$1")
    .replace(/\\(?:textbf|textit|emph|alert|underline)\{([^{}]*)\}/g, "$1")
    .replace(/\\(?:small|footnotesize|scriptsize|tiny|normalsize|large|Large|LARGE|huge|Huge|centering|raggedright|raggedleft|pause|vfill|hfill|noindent)\b/g, "")
    .replace(/\\(?:vspace|hspace)(?:\*)?(?:\[[^\]]*\])?\{[^{}]*\}/g, " ")
    .replace(/\\[a-zA-Z]+\*?(?:<[^>]*>)?(?:\[[^\]]*\])?(?:\{([^{}]*)\})?/g, (_match, inner) => inner || "")
    .replace(/[{}]/g, "")
    .split("\n")
    .map((line) => cleanTexInline(line).trim())
    .filter(Boolean)
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()

  return cleaned.replace(/@@KGTS_MATH_(\d+)@@/g, (_match, index) => mathBlocks[Number(index)] || "").replace(/\n{3,}/g, "\n\n").trim()
}

function normalizePreviewEnvironment(env: string, args: string, body: string) {
  const normalizedEnv = env.replace(/\*$/, "")
  const trimmedBody = body.trim()
  switch (normalizedEnv) {
    case "equation":
      return trimmedBody
    case "align":
    case "flalign":
    case "split":
      return `\\begin{aligned}\n${trimmedBody}\n\\end{aligned}`
    case "alignat":
      return `\\begin{alignedat}${args || "{2}"}\n${trimmedBody}\n\\end{alignedat}`
    case "gather":
    case "multline":
      return `\\begin{gathered}\n${trimmedBody}\n\\end{gathered}`
    default:
      return `\\begin{${normalizedEnv}}${args || ""}\n${trimmedBody}\n\\end{${normalizedEnv}}`
  }
}

function normalizePreviewFormula(value: string) {
  return value
    .replace(/\\notag\b/g, "")
    .replace(/\\nonumber\b/g, "")
    .replace(/\\\\\s*\[[^\]]*\]/g, "\\\\")
    .trim()
}

function texToReadableMarkdown(value: string) {
  return value
    .replace(/%.*$/gm, "")
    .replace(/\\begin\{(?:itemize|enumerate)\}/g, "\n")
    .replace(/\\end\{(?:itemize|enumerate)\}/g, "\n")
    .replace(/\\item(?:<[^>]*>)?(?:\[[^\]]*\])?/g, "\n- ")
    .replace(/\\begin\{(?:equation|align|aligned|gather|split)\*?\}([\s\S]*?)\\end\{(?:equation|align|aligned|gather|split)\*?\}/g, (_match, formula) => `\n\n$$\n${formula.trim()}\n$$\n\n`)
    .replace(/\\\[((?:.|\n)*?)\\\]/g, (_match, formula) => `\n\n$$\n${formula.trim()}\n$$\n\n`)
    .replace(/\\\(((?:.|\n)*?)\\\)/g, (_match, formula) => `$${formula.trim()}$`)
    .replace(/\$\$([\s\S]*?)\$\$/g, (_match, formula) => `\n\n$$\n${formula.trim()}\n$$\n\n`)
    .replace(/\\(?:includegraphics|safecontentimage|safeverticalimage|safelogoimage)(?:\[[^\]]*\])?\{([^}]+)\}/g, "\n\n[图：$1]\n\n")
    .replace(/\\(textbf|textit|emph|alert)\{([^{}]*)\}/g, "$2")
    .replace(/\\(small|footnotesize|scriptsize|large|Large|centering|pause)\b/g, "")
    .replace(/\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{([^{}]*)\})?/g, (_match, inner) => inner || "")
    .replace(/[{}]/g, "")
    .split("\n")
    .map((line) => cleanTexInline(line).trim())
    .filter(Boolean)
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

function cleanTexInline(value: string) {
  return String(value || "")
    .replace(/\\&/g, "&")
    .replace(/\\%/g, "%")
    .replace(/\\_/g, "_")
    .replace(/\\#/g, "#")
    .replace(/~/g, " ")
    .trim()
}

function formatSlideKind(slide: PptSlideDetail) {
  const parts = []
  if (slide.images?.length || slide.image_count) parts.push(`${slide.images?.length || slide.image_count} 图`)
  if (slide.tables?.length) parts.push(`${slide.tables.length} 表`)
  if (slide.source_tex) parts.push("TeX")
  return parts.length ? parts.join(" · ") : "讲授展示"
}
