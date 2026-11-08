import { useEffect, useRef, useState } from "react"
import type { PptSlideDetail, PptSlideLecture } from "@/types/education"
import { findSlideLecture } from "@/lib/coursewarePages"
import { cn } from "@/lib/utils"

export function DeleteSlideDialog({ slides, lectures, targetIndex, initialDraft, onCancel, onConfirm }: {
  slides: PptSlideDetail[]
  lectures: PptSlideLecture[]
  targetIndex: number
  initialDraft?: string
  onCancel: () => void
  onConfirm: (drafts: Record<number, string>) => Promise<void>
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const scrollerRef = useRef<HTMLDivElement>(null)
  const targetRef = useRef<HTMLElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const [drafts, setDrafts] = useState<Record<number, string>>(() => Object.fromEntries(
    slides.map((slide) => [slide.index, slide.index === targetIndex && initialDraft !== undefined
      ? initialDraft : findSlideLecture(lectures, slide)?.lecture || ""]),
  ))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const hasContent = Boolean(drafts[targetIndex]?.trim())

  useEffect(() => {
    const dialog = dialogRef.current
    dialog?.showModal()
    if (scrollerRef.current && targetRef.current) {
      scrollerRef.current.scrollTop = targetRef.current.offsetTop - 16
    }
    inputRef.current?.focus({ preventScroll: true })
    return () => dialog?.close()
  }, [])

  const confirm = async () => {
    if (hasContent || saving) return
    setSaving(true)
    setError("")
    try {
      await onConfirm(drafts)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除失败，请重试")
      setSaving(false)
    }
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="delete-slide-title"
      aria-describedby="delete-slide-description"
      onCancel={(event) => { event.preventDefault(); if (!saving) onCancel() }}
      className="m-auto w-[min(960px,calc(100%-2rem))] max-w-none overflow-hidden rounded-xl border bg-card p-0 text-foreground shadow-xl backdrop:bg-black/50"
    >
      <div className="flex max-h-[90dvh] flex-col">
        <div className="border-b p-4 sm:px-6">
          <h2 id="delete-slide-title" className="text-lg font-semibold">删除第 {targetIndex} 页 · 全篇讲稿</h2>
          <p id="delete-slide-description" className="mt-2 text-sm text-muted-foreground">
            已定位到待删除页。可在下方编辑全篇讲稿，将本页内容移至其他页或清空；本页讲稿为空后，点击确定删除页面。
          </p>
        </div>
        <div ref={scrollerRef} className="relative min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:px-6">
          {slides.map((slide) => {
            const isTarget = slide.index === targetIndex
            return (
              <section key={slide.slide_id || slide.index} ref={isTarget ? targetRef : undefined}
                className={cn("rounded-lg border p-3", isTarget && "border-destructive/50 bg-destructive/5")}>
                <label htmlFor={`delete-lecture-${slide.index}`} className="mb-2 block text-sm font-medium">
                  第 {slide.index} 页：{slide.title || "未命名页"}
                  {isTarget && <span className="ml-2 text-destructive">待删除</span>}
                </label>
                <textarea
                  id={`delete-lecture-${slide.index}`}
                  ref={isTarget ? inputRef : undefined}
                  value={drafts[slide.index] || ""}
                  onChange={(event) => setDrafts((previous) => ({ ...previous, [slide.index]: event.target.value }))}
                  disabled={saving}
                  rows={isTarget ? 8 : 5}
                  placeholder="本页暂无讲稿"
                  className="w-full resize-y rounded-lg border bg-background px-3 py-2 font-mono text-sm leading-relaxed focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50"
                />
              </section>
            )
          })}
        </div>
        <div className="border-t p-4 sm:px-6">
          <p aria-live="polite" className={cn("mb-3 text-sm", hasContent ? "text-destructive" : "text-muted-foreground")}>
            {hasContent ? "待删除页仍有讲稿，请先移走或清空本页内容。" : "待删除页讲稿为空，可以确定删除。其他页的讲稿修改会一并保存。"}
          </p>
          {error && <p role="alert" className="mb-3 text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onCancel} disabled={saving} className="rounded-lg border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50">取消</button>
            <button type="button" onClick={confirm} disabled={hasContent || saving}
              className="rounded-lg bg-destructive px-4 py-2 text-sm text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50">
              {saving ? "正在删除…" : "确定删除"}
            </button>
          </div>
        </div>
      </div>
    </dialog>
  )
}
