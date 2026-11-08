import assert from "node:assert/strict"
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "../../.tmp/browser-check/node_modules/playwright/index.mjs")

const browser = await chromium.launch({ headless: true, executablePath: process.env.QA_BROWSER || "C:/Users/csc20/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe" })
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = []
  page.on("pageerror", (error) => errors.push(error.message))
  await page.addInitScript(() => {
    localStorage.setItem("kgts_token", "test-only")
    localStorage.setItem("kgts_user", JSON.stringify({ role: "teacher", username: "test" }))
  })
  const courseId = "course_order_test"
  let chapters = [1, 2, 10].map((i) => ({ id: `chapter::lesson_${i}`, title: `AT-${i}`, course_id: courseId, has_lecture_content: true }))
  let fail = false
  let writes = 0
  let publishPayload
  const project = { id: "cw_identity_test", title: "Identity test", render_source: "pdf", slides: [{ index: 1, title: "Page 1" }], slide_lectures: [{ index: 1, lecture: "A saved lecture with content." }] }
  await page.route("**/env-config.js", (route) => route.fulfill({ contentType: "application/javascript", body: "window.__APP_CONFIG__ = {}" }))
  await page.route("**/api/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    let result = { success: true }
    if (url.pathname === "/api/education/courses") result = { success: true, courses: [{ id: courseId, title: "The Advanced evolutionary theory of polygenic trait" }] }
    else if (url.pathname.endsWith("/lesson-order")) {
      writes++
      if (fail) return route.fulfill({ status: 409, json: { detail: "课节列表已变更，请刷新后重新排序" } })
      const { chapter_ids: ids } = request.postDataJSON()
      chapters = ids.map((id) => chapters.find((chapter) => chapter.id === id))
      result = { success: true, chapters }
    } else if (url.pathname.endsWith("/list-chapters")) result = { success: true, chapters }
    else if (url.pathname.endsWith("/courseware/projects")) result = { success: true, projects: [{ id: "cw_long_title", title: "Fundamental Population and Quantitative Genetics on Evolution" }] }
    else if (url.pathname.endsWith("/courseware/projects/cw_identity_test")) result = { success: true, project }
    else if (url.pathname.endsWith("/save-chapter")) {
      publishPayload = request.postDataJSON()
      result = { success: true, chapter_id: "chapter::existing", chapter: { id: "chapter::existing" } }
    } else if (url.pathname.includes("latest/by-chapter")) result = { success: true, job: null }
    return route.fulfill({ json: result })
  })
  await page.goto("http://127.0.0.1:3017/teacher")
  const card = page.locator("article").filter({ hasText: "The Advanced evolutionary theory of polygenic trait" })
  const titles = () => card.locator("p.font-medium").allTextContents()
  await card.getByRole("button", { name: "调整顺序", exact: true }).click()
  assert.equal(await card.getByRole("button", { name: "上移 AT-1", exact: true }).isDisabled(), true)
  await card.getByRole("button", { name: "上移 AT-10", exact: true }).click()
  assert.deepEqual(await titles(), ["AT-1", "AT-10", "AT-2"])
  fail = true
  await card.getByRole("button", { name: "保存顺序", exact: true }).click()
  await card.getByRole("alert").waitFor()
  assert.deepEqual(chapters.map((c) => c.title), ["AT-1", "AT-2", "AT-10"])
  assert.deepEqual(await titles(), ["AT-1", "AT-10", "AT-2"])
  fail = false
  await card.getByRole("button", { name: "保存顺序", exact: true }).click()
  await card.getByRole("button", { name: "调整顺序", exact: true }).waitFor()
  assert.deepEqual(chapters.map((c) => c.title), ["AT-1", "AT-10", "AT-2"])
  await page.reload()
  await card.getByRole("button", { name: "调整顺序", exact: true }).click()
  assert.deepEqual(await titles(), ["AT-1", "AT-10", "AT-2"])
  await card.getByRole("button", { name: "按名称排序", exact: true }).click()
  assert.deepEqual(await titles(), ["AT-1", "AT-2", "AT-10"])
  await page.screenshot({ path: "D:/unlessPaper/lesson-deploy-20261025/sorting-desktop.png", fullPage: true })
  await card.getByRole("button", { name: "取消", exact: true }).click()
  assert.deepEqual(await titles(), ["AT-1", "AT-10", "AT-2"])
  await page.setViewportSize({ width: 390, height: 844 })
  await card.getByRole("button", { name: "调整顺序", exact: true }).click()
  await page.screenshot({ path: "D:/unlessPaper/lesson-deploy-20261025/sorting-mobile.png", fullPage: true })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
  await card.getByRole("button", { name: "下移 AT-1", exact: true }).click()
  await card.getByRole("button", { name: "保存顺序", exact: true }).click()
  await card.getByRole("button", { name: "调整顺序", exact: true }).waitFor()
  assert.equal(writes, 3)
  await page.goto(`http://127.0.0.1:3017/teacher/prepare?chapterId=cw_identity_test&courseId=${courseId}`)
  await page.getByRole("button", { name: "保存为课程", exact: true }).click()
  await page.waitForURL("**/teacher/prepare?chapterId=chapter%3A%3Aexisting**")
  assert.equal(publishPayload.source_project_id, "cw_identity_test")
  assert.equal(publishPayload.course_id, courseId)
  assert.deepEqual(errors, [])
  console.log("PASS: sorting, persistence, cancel, failure/retry, mobile layout, and project publish identity")
} finally {
  await browser.close()
}
