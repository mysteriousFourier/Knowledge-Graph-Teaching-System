import { createFileRoute } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { useMemo, useState } from "react"
import { BookOpen, CheckCircle, ChevronLeft, ChevronRight, Pause, Play, RotateCcw, Send, Sparkles, Undo2 } from "lucide-react"
import { useMarkChapter, useStudentAskQuestion, useStudentChapter, useStudentChapters, useStudentProgress, type ChapterProgressStatus } from "@/api/student"
import { ConsistencyPanel } from "@/components/common/ConsistencyPanel"
import { EvidenceSummary } from "@/components/common/EvidenceSummary"
import { EmptyState } from "@/components/common/EmptyState"
import { LoadingSpinner } from "@/components/common/LoadingSpinner"
import { PlaybackProgress } from "@/components/common/PlaybackProgress"
import { RichTextContent } from "@/components/renderers/RichTextContent"
import { useLecturePlayback } from "@/hooks/useLecturePlayback"
import { cn } from "@/lib/utils"
import type { PptSlideDetail, StudentQuestionResponse } from "@/types/education"

export const Route = createFileRoute("/student/learn")({
  component: LearnPage,
})

function LearnPage() {
  const queryClient = useQueryClient()
  const [selectedChapterId, setSelectedChapterId] = useState("")
  const [question, setQuestion] = useState("")
  const [answer, setAnswer] = useState<StudentQuestionResponse | null>(null)

  const { data, isLoading } = useStudentChapters()
  const { data: selectedChapterData, isLoading: isSelectedChapterLoading } = useStudentChapter(selectedChapterId)
  const { data: progressData } = useStudentProgress()
  const markChapter = useMarkChapter()
  const askQuestion = useStudentAskQuestion()

  const chapters = data?.chapters || []
  const selectedChapterSummary = chapters.find((chapter) => chapter.id === selectedChapterId)
  const selectedChapter = selectedChapterData?.chapter || selectedChapterSummary
  const slideLectures = selectedChapter?.slide_lectures || []
  const coursewareSlides = useMemo(
    () => attachRenderedPagesToSlides(selectedChapter?.ppt_slides || [], selectedChapter?.rendered_pages),
    [selectedChapter?.ppt_slides, selectedChapter?.rendered_pages],
  )
  const isCoursewareChapter = Boolean(coursewareSlides.length)
  const selectedContent = selectedChapter?.lecture_content || selectedChapter?.content || ""
  const selectedProgress = selectedChapter ? progressData?.progress?.chapters?.[selectedChapter.id] : undefined
  const playback = useLecturePlayback({
    segmentCount: isCoursewareChapter ? coursewareSlides.length : selectedContent ? 1 : 0,
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
      return selectedContent
    },
    getSegmentSpeechCues: (segment) => {
      if (!isCoursewareChapter) return undefined
      const slide = coursewareSlides[segment]
      return slideLectures.find((item) => ((slide?.slide_id && item.slide_id === slide.slide_id) || item.index === slide?.index) && item.lecture?.trim())?.speech_cues
    },
  })
  const currentSlide = playback.currentSegment
  const currentCoursewareSlide = coursewareSlides[currentSlide]
  const currentSlideLecture = useMemo(() => {
    if (!isCoursewareChapter || !slideLectures.length || !currentCoursewareSlide) return undefined
    return slideLectures.find((item) => ((currentCoursewareSlide.slide_id && item.slide_id === currentCoursewareSlide.slide_id) || item.index === currentCoursewareSlide.index) && item.lecture?.trim())
  }, [currentCoursewareSlide, isCoursewareChapter, slideLectures])
  const questionContext = isCoursewareChapter
    ? `章节标题：${selectedChapter?.title || ""}\n\n当前页：第 ${currentCoursewareSlide?.index || currentSlide + 1} 页 ${currentCoursewareSlide?.title || ""}\n\n页面内容：\n${currentCoursewareSlide?.content || currentCoursewareSlide?.raw_text || ""}\n\n页面讲稿：\n${currentSlideLecture?.lecture || ""}`
    : `章节标题：${selectedChapter?.title || ""}\n\n章节内容：\n${selectedContent}`

  const handleStatus = async (status: ChapterProgressStatus) => {
    if (!selectedChapter) return
    await markChapter.mutateAsync({ chapter_id: selectedChapter.id, status })
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["student-progress"] }),
      queryClient.invalidateQueries({ queryKey: ["student-review"] }),
    ])
  }

  const handleAsk = async () => {
    if (!selectedChapter || !question.trim()) return
    const result = await askQuestion.mutateAsync({
      chapter_id: selectedChapter.id,
      context: questionContext,
      question: question.trim(),
    })
    setAnswer(result)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">学习模式</h1>
        <p className="text-muted-foreground">选择章节，阅读课程内容</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1 space-y-4">
          <div className="bg-card border rounded-xl p-4">
            <h2 className="font-semibold mb-3 flex items-center gap-2">
              <BookOpen size={18} />
              章节选择
            </h2>
            {isLoading ? (
              <LoadingSpinner size={20} text="加载中..." />
            ) : chapters.length === 0 ? (
              <EmptyState title="暂无章节" description="没有可用的学习章节" />
            ) : (
              <div className="space-y-2">
                {chapters.map((chapter) => (
                  <button
                    key={chapter.id}
                    onClick={() => {
                      setSelectedChapterId(chapter.id)
                      playback.reset(0)
                      setQuestion("")
                      setAnswer(null)
                    }}
                    className={cn(
                      "w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors",
                      selectedChapterId === chapter.id
                        ? "bg-primary/10 text-primary font-medium"
                        : "hover:bg-muted text-muted-foreground"
                    )}
                  >
                    {chapter.title}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="lg:col-span-2">
          {selectedChapter ? (
            <div className="space-y-4">
              <div className="bg-card border rounded-xl">
              <div className="flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-center sm:justify-between">
                <h2 className="min-w-0 font-semibold">{selectedChapter.title}</h2>
                <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
                  <button
                    onClick={playback.toggle}
                    disabled={!playback.hasSegments}
                    className={cn(
                      "inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50",
                      playback.isPlaying ? "bg-amber-100 text-amber-700 hover:bg-amber-200" : "bg-primary/10 text-primary hover:bg-primary/20"
                    )}
                    title={playback.providerLabel}
                  >
                    {playback.isPlaying || playback.isLoadingAudio ? <Pause size={14} /> : <Play size={14} />}
                    {playback.isPlaying || playback.isLoadingAudio ? "暂停" : "播放"}
                  </button>
                  <button
                    onClick={() => playback.replay(currentSlide)}
                    disabled={!playback.hasSegments}
                    className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border hover:bg-accent disabled:opacity-50 transition-colors"
                    title={playback.providerLabel}
                  >
                    <RotateCcw size={14} />
                    重播
                  </button>
                  <button
                    onClick={() => handleStatus("learned")}
                    disabled={markChapter.isPending}
                    className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-green-100 text-green-700 hover:bg-green-200 transition-colors"
                  >
                    <CheckCircle size={14} />
                    标记已学
                  </button>
                  <button
                    onClick={() => handleStatus("forgotten")}
                    disabled={markChapter.isPending}
                    className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-red-100 text-red-700 hover:bg-red-200 transition-colors"
                  >
                    <Undo2 size={14} />
                    我忘了
                  </button>
                  <button
                    onClick={() => handleStatus("reviewing")}
                    disabled={markChapter.isPending}
                    className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-blue-100 text-blue-700 hover:bg-blue-200 transition-colors"
                  >
                    <RotateCcw size={14} />
                    重新学习
                  </button>
                </div>
              </div>
              {selectedProgress && (
                <div className="border-b px-4 py-2 text-sm text-muted-foreground">
                  当前状态：{statusLabel(selectedProgress.status)} · 正确 {selectedProgress.correct_count || 0} · 错误 {selectedProgress.wrong_count || 0}
                </div>
              )}
              <PlaybackProgress
                progress={playback.progress}
                statusText={playback.statusText}
                audioPosition={playback.audioPosition}
                onSeek={playback.seekAudio}
              />
              <div className="p-4">
                {isCoursewareChapter && currentCoursewareSlide ? (
                  <div className="space-y-5">
                    <PptSlideStudyView slide={currentCoursewareSlide} />
                    <section>
                      <div className="mb-2 text-xs font-medium uppercase text-muted-foreground">对应讲稿</div>
                      {currentSlideLecture?.lecture ? (
                        <RichTextContent content={currentSlideLecture.lecture} />
                      ) : (
                        <EmptyState title="暂无本页讲稿" description="该页没有保存逐页讲稿。" />
                      )}
                    </section>
                  </div>
                ) : selectedContent ? (
                  <RichTextContent content={selectedContent} />
                ) : (
                  <EmptyState title="暂无内容" description="该章节暂无课程内容" />
                )}
              </div>
              {isCoursewareChapter && coursewareSlides.length ? (
                <StudyPager
                  current={currentSlide}
                  total={coursewareSlides.length}
                  onPrev={() => playback.setCurrentSegment((prev) => prev - 1)}
                  onNext={() => playback.setCurrentSegment((prev) => prev + 1)}
                />
              ) : null}
            </div>
              <section className="bg-card border rounded-xl">
                <div className="border-b p-4">
                  <h2 className="flex items-center gap-2 font-semibold">
                    <Sparkles size={18} />
                    当前章节问答
                  </h2>
                </div>
                <div className="space-y-4 p-4">
                  <textarea
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    placeholder={`围绕《${selectedChapter.title}》提问，例如：这个公式中的变量分别是什么意思？`}
                    className="min-h-[92px] w-full resize-y rounded-lg border bg-background px-3 py-2.5 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                  />
                  <button
                    onClick={handleAsk}
                    disabled={!question.trim() || askQuestion.isPending}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 sm:w-auto"
                  >
                    {askQuestion.isPending ? <LoadingSpinner size={16} /> : <Send size={16} />}
                    {askQuestion.isPending ? "回答中..." : "提问"}
                  </button>

                  {answer?.warning && (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                      {answer.warning}
                    </div>
                  )}
                  {answer?.answer && (
                    <div className="rounded-lg border bg-background p-4">
                      <RichTextContent content={answer.answer} />
                    </div>
                  )}
                  {(answer?.learning_plan || answer?.sources?.length || answer?.retrieval_context) && (
                    <EvidenceSummary
                      learningPlan={answer.learning_plan}
                      sources={answer.sources}
                      retrievalContext={answer.retrieval_context}
                      warning={answer.warning}
                    />
                  )}
                  {answer?.consistency_report && (
                    <ConsistencyPanel report={answer.consistency_report} title="问答实体指标" />
                  )}
                </div>
              </section>
            </div>
          ) : (
            <div className="bg-card border rounded-xl p-8">
              <EmptyState
                title="选择章节"
                description="请从左侧选择一个章节开始学习"
                icon={<BookOpen size={48} className="text-muted-foreground" />}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = {
    learned: "已学完",
    reviewing: "需要复习",
    forgotten: "已忘记",
    unlearned: "未学习",
  }
  return labels[status || "unlearned"] || "未学习"
}

function attachRenderedPagesToSlides(
  slides: PptSlideDetail[],
  renderedPages: PptSlideDetail["rendered_page"][] | undefined,
): PptSlideDetail[] {
  if (!renderedPages?.length) return slides
  return slides.map((slide, index) => ({
    ...slide,
    rendered_page:
      slide.rendered_page ||
      renderedPages.find((page) => slide.slide_id && page?.slide_id === slide.slide_id) ||
      renderedPages.find((page) => page?.page_index === (slide.rendered_page_index ?? slide.index - 1)) ||
      renderedPages[index],
  }))
}

function StudyPager({ current, total, onPrev, onNext }: { current: number; total: number; onPrev: () => void; onNext: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t p-4">
      <button
        onClick={onPrev}
        disabled={current === 0}
        className="inline-flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-sm hover:bg-muted disabled:opacity-50"
      >
        <ChevronLeft size={16} />
        上一页
      </button>
      <span className="text-sm text-muted-foreground">
        {current + 1} / {total}
      </span>
      <button
        onClick={onNext}
        disabled={current === total - 1}
        className="inline-flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-sm hover:bg-muted disabled:opacity-50"
      >
        下一页
        <ChevronRight size={16} />
      </button>
    </div>
  )
}

function PptSlideStudyView({ slide }: { slide: PptSlideDetail }) {
  if (slide.rendered_page?.image) {
    return <RenderedSlideStudyView slide={slide} />
  }

  return (
    <section className="space-y-4">
      <div>
        <div className="text-xs font-medium uppercase text-muted-foreground">页面 {slide.index}</div>
        <h3 className="mt-1 text-lg font-semibold">{slide.title || "无标题"}</h3>
      </div>
      <div className="rounded-lg bg-muted p-4 text-sm leading-relaxed">
        <RichTextContent content={slide.content || slide.raw_text || "无正文文本"} />
      </div>
      {slide.notes ? (
        <div className="rounded-lg border bg-background p-3 text-sm">
          <div className="mb-2 text-xs font-medium uppercase text-muted-foreground">备注</div>
          <RichTextContent content={slide.notes} />
        </div>
      ) : null}
      {!!slide.tables?.length ? (
        <div className="space-y-2">
          <div className="text-xs font-medium uppercase text-muted-foreground">表格</div>
          {slide.tables.map((table, index) => (
            <div key={index} className="overflow-x-auto rounded-lg border">
              {table.rows.map((row, rowIndex) => (
                <div key={rowIndex} className="grid grid-flow-col auto-cols-fr border-b last:border-b-0">
                  {row.map((cell, cellIndex) => (
                    <div key={cellIndex} className="min-w-0 border-r px-2 py-1 text-sm last:border-r-0">
                      <RichTextContent content={cell} inline />
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : null}
      {!!slide.images?.length ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {slide.images.map((image, index) => (
            <figure key={`${image.source_path || index}-${index}`} className="rounded-lg border bg-background p-2">
              {image.data_uri ? (
                <img
                  src={image.data_uri}
                  alt={image.source_path || `课件图片 ${index + 1}`}
                  className="max-h-72 w-full rounded object-contain"
                />
              ) : (
                <div className="flex min-h-32 items-center justify-center rounded bg-muted px-3 text-center text-xs text-muted-foreground">
                  {image.oversized ? "图片过大，未内嵌预览" : "图片无法预览"}
                </div>
              )}
            </figure>
          ))}
        </div>
      ) : null}
    </section>
  )
}

function RenderedSlideStudyView({ slide }: { slide: PptSlideDetail }) {
  const page = slide.rendered_page
  return (
    <section className="space-y-4">
      <div>
        <div className="text-xs font-medium uppercase text-muted-foreground">页面 {slide.index}</div>
        <h3 className="mt-1 text-lg font-semibold">{slide.title || "无标题"}</h3>
      </div>
      <div
        className="mx-auto w-full overflow-hidden rounded-lg border bg-white shadow-sm"
        style={{ aspectRatio: renderedPageAspectRatio(page) }}
      >
        <img
          src={page?.image || ""}
          alt={slide.title || `PDF rendered slide ${slide.index}`}
          className="h-full w-full object-contain"
          draggable={false}
        />
      </div>
    </section>
  )
}

function renderedPageAspectRatio(page: PptSlideDetail["rendered_page"]) {
  const width = Number(page?.width)
  const height = Number(page?.height)
  return width > 0 && height > 0 ? `${width} / ${height}` : "16 / 9"
}
