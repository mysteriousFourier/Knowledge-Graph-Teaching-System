import assert from "node:assert/strict"
import { mkdir, readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "../../.tmp/browser-check/node_modules/playwright/index.mjs")
const output = new URL("../../.tmp/lecture-alignment/", import.meta.url)
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true, executablePath: process.env.QA_BROWSER || "C:/Users/csc20/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe" })
const source = "老师原来的问法和例子全部保留。"
const pageSlides = (count) => Array.from({ length: count }, (_, i) => ({ index: i + 1, slide_id: `page-${i + 1}`, title: `遗传漂变 ${i + 1}`, raw_text: "随机抽样导致频率改变。" }))
const original = [1, 2].map((index) => ({ index, slide_id: `page-${index}`, lecture: source + index, skipped: false }))
const chars = (text) => text.replace(/\s/g, "").length
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, acceptDownloads: true })
  const errors = []
  const warnings = []
  page.on("pageerror", (error) => errors.push(error.message))
  page.on("console", (message) => { if (message.type() === "warning") warnings.push(message.text()); if (message.type() === "error") errors.push(message.text()) })
  await page.addInitScript(() => {
    localStorage.setItem("kgts_token", "test-only")
    localStorage.setItem("kgts_user", JSON.stringify({ role: "teacher", username: "test" }))
  })
  let previewCount = 3
  let fail = false
  let jobs = 0
  let lastRequest
  let result
  const project = { id: "cw_alignment_test", title: "原课件", slides: pageSlides(2), slide_count: 2, slide_lectures: original }
  await page.route("**/env-config.js", (route) => route.fulfill({ contentType: "application/javascript", body: "window.__APP_CONFIG__ = {}" }))
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith("/api/")) return route.continue()
    let json = { success: true }
    if (path.endsWith("/courseware/projects/cw_alignment_test")) json = { success: true, project }
    else if (path.endsWith("/courseware/projects")) json = { success: true, projects: [project] }
    else if (path.endsWith("/lecture-materials")) json = { success: true, materials: [{ name: "参考讲稿.md", role: "reference", text: "保留老师的设问方式。" }] }
    else if (path.endsWith("/upload-ppt-preview")) json = { success: true, chapter_title: "新课件", slides: pageSlides(previewCount), slide_count: previewCount, full_text: "遗传漂变" }
    else if (path.endsWith("/generate-slide-lectures/jobs")) {
      jobs++
      lastRequest = route.request().postDataJSON()
      const lectures = lastRequest.lecture_workflow === "align"
        ? lastRequest.slides.map((slide) => ({ index: slide.index, slide_id: slide.slide_id, lecture: source + slide.index, skipped: false,
          alignment: { source: slide.index === 1 ? "adapted" : "simulated", source_files: slide.index === 1 ? ["粘贴的本次原稿"] : [], source_ids: [], timestamps: [], notes: "测试对齐记录" } }))
        : lastRequest.slides.map((slide, i) => ({ index: slide.index, slide_id: slide.slide_id, lecture: i === 0 ? lastRequest.existing_slide_lectures.map((l) => l.lecture).join("\n") : "新增页补讲。", skipped: false,
          alignment: { source: i === 0 ? "retained" : "simulated", source_files: [], source_ids: [], timestamps: [], notes: "完整保留原稿" } }))
      result = { success: true, chapter_title: "测试", slide_count: lectures.length, slides: lastRequest.slides, slide_lectures: lectures,
        lecture_content: lectures.map((l) => l.lecture).join("\n"), message: "拆分完成", lecture_alignment_audit: { chars: chars(lectures.map((l) => l.lecture).join("")), original_chars: chars(lastRequest.existing_slide_lectures?.map((l) => l.lecture).join("") || "") } }
      json = { success: true, job_id: `test-${jobs}`, status: "queued" }
    } else if (path.includes("/generate-slide-lectures/jobs/")) json = fail ? { success: true, status: "failed", error: "模型暂时不可用" } : { success: true, status: "completed", result }
    else if (path.includes("latest/by-chapter")) json = { success: true, job: null }
    else if (path.endsWith("/graph/scope-tree")) json = { success: true, data: { nodes: [], relationships: [] } }
    return route.fulfill({ json })
  })
  await page.goto(`${process.env.QA_BASE_URL || "http://127.0.0.1:3018"}/teacher/prepare?chapterId=cw_alignment_test`)
  await page.getByRole("button", { name: "更改课件", exact: true }).waitFor().catch(async (error) => {
    console.error(JSON.stringify({ url: page.url(), errors, warnings, body: (await page.locator("body").innerText()).slice(0, 3500) }))
    throw error
  })
  assert.equal(await page.getByText("载入逐字稿", { exact: true }).count(), 0)
  await page.getByRole("button", { name: "已有文案拆分补全", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: "已有文案拆分补全" })
  await dialog.getByLabel("或粘贴本次原稿").fill(source)
  await dialog.getByLabel("参考讲稿 / 术语与公式资料（可选，仅用于讲法和核对）").setInputFiles({ name: "参考讲稿.md", mimeType: "text/markdown", buffer: Buffer.from("保留老师的设问方式。") })
  await page.screenshot({ path: fileURLToPath(new URL("dialog-desktop.png", output)), fullPage: true })
  await dialog.getByRole("button", { name: "开始拆分补全", exact: true }).click()
  await dialog.waitFor({ state: "hidden" })
  assert.equal(lastRequest.lecture_workflow, "align")
  assert.deepEqual(lastRequest.lecture_materials.map((m) => m.role), ["reference", "transcript"])
  const downloadEvent = page.waitForEvent("download")
  await page.getByRole("button", { name: "下载逐页讲稿", exact: true }).click()
  const download = await downloadEvent
  const content = await readFile(await download.path(), "utf8")
  assert.match(content, /## 第1页/)
  assert.match(content, /## 第2页/)
  assert.equal(content.includes("## 第1 页："), false)

  const replace = async (name) => {
    const chooserPromise = page.waitForEvent("filechooser")
    await page.getByRole("button", { name: "更改课件", exact: true }).click()
    await (await chooserPromise).setFiles({ name, mimeType: "application/pdf", buffer: Buffer.from("mocked file") })
  }
  await replace("三页新课件.pdf")
  await page.getByText(/课件由 2 页改为 3 页/).waitFor()
  assert.equal(lastRequest.lecture_workflow, "reflow")
  assert.equal(lastRequest.existing_slide_lectures.length, 2)
  assert.equal(result.slide_lectures[0].lecture.replace(/\s/g, ""), original.map((l) => l.lecture).join(""))
  assert.ok(result.lecture_alignment_audit.chars >= result.lecture_alignment_audit.original_chars)
  const beforeJobs = jobs
  await replace("同样三页课件.pdf")
  await page.getByText(/课件已更改，页数相同，已保留 3 页原文案/).waitFor()
  assert.equal(jobs, beforeJobs)
  fail = true
  previewCount = 1
  await replace("合并为一页.pdf")
  await page.getByText(/更改课件失败，原课件与文稿已保留/).waitFor()
  assert.equal(await page.getByLabel("课程名称", { exact: true }).inputValue(), "同样三页课件")
  assert.equal(lastRequest.existing_slide_lectures.length, 3)
  await page.screenshot({ path: fileURLToPath(new URL("replacement-desktop.png", output)), fullPage: true })
  fail = false
  await replace("合并为一页.pdf")
  await page.getByText(/课件由 3 页改为 1 页/).waitFor()
  assert.equal(result.slide_lectures.length, 1)
  assert.ok(result.lecture_alignment_audit.chars >= result.lecture_alignment_audit.original_chars)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole("button", { name: "已有文案拆分补全", exact: true }).click()
  await dialog.waitFor()
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
  assert.ok(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth))
  await page.screenshot({ path: fileURLToPath(new URL("dialog-mobile.png", output)) })
  assert.deepEqual(errors, [])
  assert.deepEqual(warnings, [])
  console.log("PASS: material upload, alignment, downloads, page split/merge, same-page replacement, atomic failure, mobile layout; no browser errors or warnings")
} finally {
  await browser.close()
}
