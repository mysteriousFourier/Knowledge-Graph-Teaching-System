import type { EditableSlideModel, PptPreviewResponse, PptSlideDetail, PptSlideLecture } from "../types/education"

export function findSlideLecture(slides: PptSlideLecture[], slide: PptSlideDetail) {
  return (slide.slide_id ? slides.find((item) => item.slide_id === slide.slide_id) : undefined)
    || slides.find((item) => Number(item.index) === Number(slide.index) && (!slide.slide_id || !item.slide_id))
}

export function removeCoursewarePage(
  preview: PptPreviewResponse,
  model: EditableSlideModel | null,
  lectures: PptSlideLecture[],
  deletedIndex: number,
  drafts: Record<number, string>,
) {
  if (preview.slides.length <= 1) throw new Error("至少保留一页课件")
  const target = preview.slides.find((slide) => slide.index === deletedIndex)
  if (!target) throw new Error("待删除页面已不存在，请重新选择")
  if ((drafts[deletedIndex] ?? findSlideLecture(lectures, target)?.lecture ?? "").trim()) {
    throw new Error("请先清空待删除页的讲稿，或将内容移至其他页")
  }

  const indexMap = new Map<number, number>()
  const remaining = preview.slides.filter((slide) => slide.index !== deletedIndex)
  const slides = remaining.map((slide, position) => {
    const index = position + 1
    indexMap.set(slide.index, index)
    const pages = preview.rendered_pages || []
    const page = slide.rendered_page
      || (slide.slide_id ? pages.find((item) => item.slide_id === slide.slide_id) : undefined)
      || pages.find((item) => item.page_index === (slide.rendered_page_index ?? slide.index - 1))
    return {
      ...slide,
      index,
      rendered_page_index: page ? position : slide.rendered_page_index,
      rendered_page: page ? { ...page, page_index: position, slide_id: slide.slide_id } : undefined,
    }
  })
  const nextLectures = remaining.flatMap((slide, position) => {
    const previous = findSlideLecture(lectures, slide)
    const lecture = drafts[slide.index] ?? previous?.lecture ?? ""
    if (!previous && !lecture.trim()) return []
    const changed = lecture !== (previous?.lecture || "")
    return [{
      ...previous,
      index: position + 1,
      slide_id: slide.slide_id,
      title: slide.title || previous?.title,
      lecture,
      skipped: lecture.trim() ? false : previous?.skipped ?? false,
      ...(changed ? {
        estimated_chars: lecture.length,
        estimated_duration_seconds: Math.round(lecture.length / 250 * 60),
        speech_cues: undefined,
        error: undefined,
      } : {}),
    }]
  })
  const nextModel = model ? {
    ...model,
    slide_count: slides.length,
    slides: model.slides.flatMap((slide) => {
      const index = indexMap.get(slide.index)
      return index === undefined ? [] : [{ ...slide, index }]
    }),
  } : null
  return {
    indexMap,
    lectures: nextLectures,
    model: nextModel,
    preview: {
      ...preview,
      slides,
      slide_count: slides.length,
      full_text: slides.map((slide) => [slide.title, slide.content, slide.notes, slide.raw_text].filter(Boolean).join("\n")).join("\n\n"),
      rendered_pages: slides.flatMap((slide) => slide.rendered_page ? [slide.rendered_page] : []),
      editable_model: nextModel || undefined,
    },
  }
}

export function reindexPageDrafts(drafts: Record<number, string>, indexMap: Map<number, number>) {
  return Object.fromEntries(Object.entries(drafts).flatMap(([key, value]) => {
    const index = indexMap.get(Number(key))
    return index === undefined ? [] : [[index, value]]
  }))
}
