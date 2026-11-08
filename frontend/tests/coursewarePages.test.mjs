import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import ts from "typescript"

const source = readFileSync(new URL("../src/lib/coursewarePages.ts", import.meta.url), "utf8")
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } })
const { findSlideLecture, removeCoursewarePage, reindexPageDrafts } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`)

function deck(withIds = true) {
  const slides = [1, 2, 3, 4].map((index) => ({ index, slide_id: withIds ? `s${index}` : undefined,
    title: `Title ${index}`, content: `Body ${index}`, rendered_page_index: index - 1,
    rendered_page: { page_index: index - 1, image: `image-${index}`, width: 1000, height: 562 },
  }))
  return { slides, slide_count: 4, full_text: "old", rendered_pages: slides.map((s) => s.rendered_page), render_source: "pdf" }
}

test("PDF deletion removes its rendered image and survives JSON reload and repeated deletion", () => {
  const original = deck()
  const first = removeCoursewarePage(original, null, [], 2, {})
  assert.equal(first.preview.slide_count, 3)
  assert.deepEqual(first.preview.rendered_pages.map((p) => p.image), ["image-1", "image-3", "image-4"])
  assert.deepEqual(first.preview.slides.map((s) => s.rendered_page_index), [0, 1, 2])
  assert.ok(!first.preview.full_text.includes("Body 2"))
  const second = removeCoursewarePage(JSON.parse(JSON.stringify(first.preview)), null, [], 2, {})
  assert.deepEqual(second.preview.rendered_pages.map((p) => p.image), ["image-1", "image-4"])
  assert.equal(original.slides.length, 4)
})

test("sparse historical lectures without IDs remain attached to their page", () => {
  const lectures = [{ index: 4, lecture: "page four", skipped: false }]
  const result = removeCoursewarePage(deck(false), null, lectures, 2, {})
  assert.deepEqual(result.lectures.map((l) => [l.index, l.lecture]), [[3, "page four"]])
  assert.equal(findSlideLecture(lectures, { index: 2 }), undefined)
})

test("stable IDs take priority over stale indices and reject conflicting IDs", () => {
  const lectures = [{ index: 2, slide_id: "s1", lecture: "one" }, { index: 9, slide_id: "s2", lecture: "two" }]
  assert.equal(findSlideLecture(lectures, { index: 2, slide_id: "s2" }).lecture, "two")
  assert.equal(findSlideLecture(lectures, { index: 2, slide_id: "other" }), undefined)
})

test("lecture must be cleared; moving it to another page preserves edits and resets speech cues", () => {
  const lectures = [{ index: 2, slide_id: "s2", lecture: "move this", skipped: false },
    { index: 3, slide_id: "s3", lecture: "existing", skipped: false, speech_cues: [{ text: "old" }] }]
  assert.throws(() => removeCoursewarePage(deck(), null, lectures, 2, {}), /清空/)
  const result = removeCoursewarePage(deck(), null, lectures, 2, { 2: " \n", 3: "existing\nmove this" })
  assert.equal(result.lectures[0].index, 2)
  assert.equal(result.lectures[0].slide_id, "s3")
  assert.equal(result.lectures[0].lecture, "existing\nmove this")
  assert.equal(result.lectures[0].speech_cues, undefined)
  assert.equal(result.lectures[0].estimated_chars, "existing\nmove this".length)
})

test("model and drafts follow the actual surviving page indices", () => {
  const preview = deck()
  const model = { version: 1, slide_count: 4, assets: { keep: {} }, slides: preview.slides.map((s) => ({ ...s, items: [] })) }
  const result = removeCoursewarePage(preview, model, [], 1, {})
  assert.equal(result.model.slide_count, 3)
  assert.deepEqual(result.model.slides.map((s) => s.index), [1, 2, 3])
  assert.equal(result.model.assets, model.assets)
  assert.deepEqual(reindexPageDrafts({ 1: "remove", 3: "keep" }, result.indexMap), { 2: "keep" })
})

test("last page and missing targets cannot be deleted", () => {
  assert.throws(() => removeCoursewarePage({ ...deck(), slides: [deck().slides[0]] }, null, [], 1, {}), /至少保留/)
  assert.throws(() => removeCoursewarePage(deck(), null, [], 99, {}), /不存在/)
})
