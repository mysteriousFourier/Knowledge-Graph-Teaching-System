import { Download } from "lucide-react"
import type { PptSlideDetail, PptSlideLecture } from "@/types/education"

function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement("a")
  link.href = url
  link.download = name
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function LectureAlignmentSummary({ title, slides, lectures }: { title: string; slides: PptSlideDetail[]; lectures: PptSlideLecture[] }) {
  if (!lectures.some((lecture) => lecture.alignment)) return null
  const countChars = (text: string) => text.replace(/\s/g, "").length
  const name = title.replace(/[<>:"/\\|?*]/g, "_") || "逐页讲稿"
  const simulated = lectures.filter((lecture) => lecture.alignment?.source === "simulated").map((lecture) => lecture.index)
  const missing = slides.filter((slide) => !lectures.some((lecture) => lecture.index === slide.index && lecture.lecture?.trim())).map((slide) => slide.index)
  const csvCell = (value: string) => `"${value.replace(/"/g, '""')}"`
  const coverage = ["page,slide_title,visible_increment,transcript_source,time_start,time_end,origin,confidence,needs_review,notes,source,source_segments", ...lectures.map((lecture) => [String(lecture.index), lecture.title || "", "",
    lecture.alignment?.source_files.join(" | ") || (lecture.alignment?.source === "retained" ? "原有讲稿" : ""), lecture.alignment?.timestamps.join(" | ") || "", "",
    lecture.alignment?.source === "retained" ? "adapted" : lecture.alignment?.source || "unknown", "", "true", lecture.alignment?.notes || "", lecture.alignment?.source || "unknown", lecture.alignment?.source_ids.join(" | ") || ""].map(csvCell).join(","))].join("\r\n")
  const chars = lectures.reduce((sum, lecture) => sum + countChars(lecture.lecture || ""), 0)
  const retainedChars = lectures.reduce((sum, lecture) => sum + (lecture.alignment?.retained_chars || 0), 0)
  const formulas = lectures.filter((lecture) => /\$[^\n$]+\$|\\(?:frac|sum|sigma|begin|sqrt|alpha|beta)\b/.test(lecture.lecture)).map((lecture) => lecture.index)
  const report = `# ${title} · 核验报告\n\n课件页数：${slides.length}\n\n当前讲稿字数（不含空白）：${chars}\n\n重排时保留的原文字数：${retainedChars || "不适用"}\n\n缺少正文的页面：${missing.join("、") || "无"}\n\n模拟补讲页面：${simulated.join("、") || "无"}\n\n数学源码提示页：${formulas.join("、") || "无"}\n\n模拟补讲是创作，不能冒称录音原话。此报告反映当前编辑稿；机械检查不能证明语义对齐。请结合课件抽查首、中、尾页及所有补讲页。\n`
  return <section aria-label="文案对齐结果" className="mt-3 space-y-2 rounded-lg border bg-muted/20 p-3 text-sm">
    <p>已对齐 {lectures.filter((lecture) => lecture.lecture?.trim()).length} / {slides.length} 页，共 {chars} 字；{simulated.length} 页为模拟补讲。{missing.length > 0 && `尚有 ${missing.length} 页缺少正文。`}</p>
    <div className="flex flex-wrap gap-2">
      {[
        ["逐页讲稿", "md", lectures.map((lecture) => `## 第${lecture.index}页\n${lecture.lecture}`).join("\n\n"), "text/markdown;charset=utf-8"],
        ["对齐记录", "csv", "\uFEFF" + coverage, "text/csv;charset=utf-8"],
        ["核验报告", "md", report, "text/markdown;charset=utf-8"],
      ].map(([label, extension, content, type]) => <button key={label} type="button" onClick={() => download(`${name}_${label}.${extension}`, content, type)}
        className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"><Download size={13} />下载{label}</button>)}
    </div>
  </section>
}
