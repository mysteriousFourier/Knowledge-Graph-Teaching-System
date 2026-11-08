import { useEffect, useRef, useState } from "react"
import { FileUp, Trash2 } from "lucide-react"
import { uploadLectureMaterials } from "@/api/education"
import { LoadingSpinner } from "./LoadingSpinner"
import type { LectureMaterial } from "@/types/lectureAlignment"

const MATERIAL_ACCEPT = ".txt,.md,.srt,.vtt,.docx,.pdf,.tex,.zip"
const buttonClass = "rounded-lg border px-3 py-2 text-sm hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50"

export function LectureAlignmentDialog({ pageCount, busy, progress, coursewareAccept, onClose, onSubmit }: {
  pageCount: number
  busy: boolean
  progress: string
  coursewareAccept: string
  onClose: () => void
  onSubmit: (materials: LectureMaterial[], courseware: File | null) => Promise<void>
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [references, setReferences] = useState<File[]>([])
  const [courseware, setCourseware] = useState<File | null>(null)
  const [pastedText, setPastedText] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const pending = busy || submitting

  useEffect(() => {
    const dialog = dialogRef.current
    dialog?.showModal()
    return () => dialog?.close()
  }, [])

  const addFiles = (incoming: File[], role: "transcript" | "reference") => {
    const update = role === "transcript" ? setFiles : setReferences
    update((previous) => [...previous, ...incoming.filter((file) => !previous.some((p) => p.name === file.name && p.size === file.size))])
    setError("")
  }

  const submit = async () => {
    setSubmitting(true)
    setError("")
    try {
      const materials: LectureMaterial[] = []
      if (files.length) materials.push(...(await uploadLectureMaterials(files, "transcript")).materials)
      if (references.length) materials.push(...(await uploadLectureMaterials(references, "reference")).materials)
      if (pastedText.trim()) materials.push({ name: "粘贴的本次原稿", role: "transcript", text: pastedText.trim() })
      if (materials.length > 40 || materials.reduce((sum, material) => sum + material.text.length, 0) > 200000) {
        throw new Error("材料最多 40 份、合计 20 万字符，请按课节处理")
      }
      await onSubmit(materials, courseware)
    } catch (cause) {
      const response = (cause as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(response || (cause instanceof Error ? cause.message : "处理失败，请重试"))
    } finally {
      setSubmitting(false)
    }
  }

  const fileSection = (role: "transcript" | "reference", title: string, selected: File[]) => (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">{title}</h3>
      <label
        className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed bg-muted/20 px-4 py-5 text-center text-sm hover:bg-accent focus-within:ring-2 focus-within:ring-primary"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => { event.preventDefault(); if (!pending) addFiles(Array.from(event.dataTransfer.files), role) }}
      >
        <FileUp size={20} className="text-muted-foreground" />
        <span>点击选择或拖入文件，可多选</span>
        <span className="text-xs text-muted-foreground">TXT / MD / SRT / VTT / DOCX / 文字 PDF / TEX / ZIP</span>
        <input aria-label={title} type="file" multiple accept={MATERIAL_ACCEPT} disabled={pending} className="sr-only"
          onChange={(event) => { addFiles(Array.from(event.target.files || []), role); event.target.value = "" }} />
      </label>
      {selected.length > 0 && <ul className="space-y-1 text-sm">
        {selected.map((file, index) => <li key={`${file.name}-${index}`} className="flex min-w-0 items-center justify-between gap-2 rounded border px-3 py-1.5">
          <span className="min-w-0 break-all">{file.name}</span>
          <button type="button" aria-label={`移除 ${file.name}`} disabled={pending} className="shrink-0 rounded p-1 hover:bg-accent"
            onClick={() => (role === "transcript" ? setFiles : setReferences)((items) => items.filter((_, i) => i !== index))}><Trash2 size={14} /></button>
        </li>)}
      </ul>}
    </section>
  )

  return <dialog ref={dialogRef} aria-labelledby="lecture-alignment-title" aria-describedby="lecture-alignment-description"
    onCancel={(event) => { event.preventDefault(); if (!pending) onClose() }}
    className="m-auto w-[min(760px,calc(100%-2rem))] max-w-none overflow-hidden rounded-xl border bg-card p-0 text-foreground shadow-xl backdrop:bg-black/50">
    <div className="flex max-h-[90dvh] flex-col">
      <div className="border-b p-4 sm:px-6">
        <h2 id="lecture-alignment-title" className="text-lg font-semibold">已有文案拆分补全</h2>
        <p id="lecture-alignment-description" className="mt-2 text-sm leading-relaxed text-muted-foreground">
          按课件每一页匹配原稿，保留原有讲法，缺失内容参照教师风格补讲。补写来源会记入对齐记录。
        </p>
      </div>
      <div className="min-h-0 space-y-5 overflow-y-auto p-4 sm:px-6">
        <section className="space-y-2">
          <label htmlFor="alignment-courseware" className="block text-sm font-medium">课件{pageCount ? "（可选更换）" : "（必需）"}</label>
          <p className="text-xs text-muted-foreground">{pageCount ? `默认使用当前 ${pageCount} 页课件，含动画逐帧页面。` : "先放入课件，页面按实际渲染顺序排列。"}</p>
          <input id="alignment-courseware" type="file" accept={coursewareAccept} disabled={pending}
            onChange={(event) => setCourseware(event.target.files?.[0] || null)} className="block w-full min-w-0 rounded-lg border p-2 text-sm" />
        </section>
        {fileSection("transcript", "本次原稿 / 课堂逐字稿（必需，可粘贴代替）", files)}
        <label className="block text-sm">
          或粘贴本次原稿
          <textarea value={pastedText} maxLength={200000} disabled={pending} onChange={(event) => setPastedText(event.target.value)} rows={4}
            placeholder="可粘贴未分页文案或带时间戳的逐字稿。"
            className="mt-2 w-full resize-y rounded-lg border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20" />
        </label>
        {fileSection("reference", "参考讲稿 / 术语与公式资料（可选，仅用于讲法和核对）", references)}
        <p className="text-xs leading-relaxed text-muted-foreground">录音或视频请先转写为文本。材料合计最多 20 万字符，单个文件不超过 32 MB。完成后可下载逐页讲稿、对齐记录和核验报告，并在备课页保存。</p>
        <p className="text-xs text-muted-foreground">成功后会替换当前逐页文案，原有课件与文案在处理成功前保留。</p>
      </div>
      <div className="space-y-3 border-t p-4 sm:px-6">
        {pending && <div role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><LoadingSpinner size={16} />{progress || "正在读取材料…"}</div>}
        {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={pending} className={buttonClass}>取消</button>
          <button type="button" onClick={submit} disabled={pending || (!pageCount && !courseware) || (!files.length && !pastedText.trim())}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50">
            {pending && <LoadingSpinner size={16} />}开始拆分补全
          </button>
        </div>
      </div>
    </div>
  </dialog>
}
