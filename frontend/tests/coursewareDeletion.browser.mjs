import assert from "node:assert/strict"

// Uses an existing Playwright installation; no real service or user data is changed.
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE)
const baseUrl = process.env.QA_BASE_URL || "http://127.0.0.1:3017"
const browser = await chromium.launch({ headless: true, channel: "chromium" })
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = []
  page.on("pageerror", (error) => errors.push(error.message))
  page.on("console", (message) => { if (message.type() === "error") console.log("Browser:", message.text()) })
  page.on("requestfailed", (request) => console.log("Request failed:", request.url(), request.failure()?.errorText))
  await page.addInitScript(() => {
    localStorage.setItem("kgts_token", "test-only")
    localStorage.setItem("kgts_user", JSON.stringify({ role: "teacher", username: "test" }))
  })
  const image = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="562"><rect width="1000" height="562" fill="white"/></svg>')
  let project = {
    id: "cw_deletion_test", title: "PDF deletion test", render_source: "pdf",
    slides: [1, 2, 3, 4].map((index) => ({ index, title: `Page ${index}`, rendered_page_index: index - 1 })),
    rendered_pages: [0, 1, 2, 3].map((page_index) => ({ page_index, image, width: 1000, height: 562 })),
    slide_lectures: [{ index: 1, lecture: "first page", skipped: false }, { index: 3, lecture: "move me", skipped: false }],
  }
  let writes = 0
  let failSave = false
  await page.route("**/env-config.js", (route) => route.fulfill({ contentType: "application/javascript", body: "window.__APP_CONFIG__ = {}" }))
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith("/api/")) return route.continue()
    let result = { success: true }
    if (url.pathname === "/api/education/courseware/projects" && route.request().method() === "POST") {
      writes++
      if (failSave) return route.fulfill({ status: 500, json: { detail: "test save failure" } })
      project = { ...project, ...route.request().postDataJSON() }
      result = { success: true, project_id: project.id, project }
    } else if (url.pathname.endsWith("/courseware/projects")) result = { success: true, projects: [project] }
    else if (url.pathname.includes("/courseware/projects/")) result = { success: true, project }
    else if (url.pathname.includes("latest/by-chapter")) result = { success: true, job: null }
    return route.fulfill({ json: result })
  })
  const open = async () => {
    await page.goto(`${baseUrl}/teacher/prepare?chapterId=cw_deletion_test`)
    await page.getByTitle("第 1 页：Page 1", { exact: true }).waitFor({ timeout: 15000 }).catch(async (error) => {
      console.log((await page.locator("body").innerText()).slice(0, 2500))
      console.log(errors)
      await page.screenshot({ path: process.env.QA_OUTPUT + "/delete-slide-load-error.png" })
      throw error
    })
  }
  await open()
  await page.getByTitle("第 3 页：Page 3", { exact: true }).click()
  await page.getByRole("button", { name: "删除当前页", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await dialog.waitFor()
  assert.equal(await dialog.locator("textarea").count(), 4)
  assert.equal(await page.evaluate(() => document.activeElement.id), "delete-lecture-3")
  assert.equal(await dialog.getByRole("button", { name: "确定删除", exact: true }).isDisabled(), true)
  await page.screenshot({ path: process.env.QA_OUTPUT + "/delete-slide-desktop.png" })
  await dialog.getByLabel("第 4 页：Page 4").fill("move me")
  await dialog.getByLabel("第 3 页：Page 3", { exact: false }).fill("")
  failSave = true
  await dialog.getByRole("button", { name: "确定删除", exact: true }).click()
  await dialog.getByRole("alert").waitFor()
  assert.equal(project.slides.length, 4)
  assert.equal(await dialog.getByLabel("第 4 页：Page 4").inputValue(), "move me")
  failSave = false
  await dialog.getByRole("button", { name: "确定删除", exact: true }).click()
  await dialog.waitFor({ state: "hidden" })
  assert.equal(project.slides.length, 3)
  assert.deepEqual(project.slide_lectures.map((l) => [l.index, l.lecture]), [[1, "first page"], [3, "move me"]])
  assert.equal(project.rendered_pages.length, 3)
  await open()
  await page.getByTitle("第 2 页：Page 2", { exact: true }).click()
  await page.getByRole("button", { name: "删除当前页", exact: true }).click()
  assert.equal(await dialog.getByLabel("第 2 页：Page 2", { exact: false }).inputValue(), "")
  await dialog.getByRole("button", { name: "取消", exact: true }).click()
  assert.equal(project.slides.length, 3)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole("button", { name: "删除当前页", exact: true }).click()
  await page.screenshot({ path: process.env.QA_OUTPUT + "/delete-slide-mobile.png" })
  const box = await dialog.boundingBox()
  assert.ok(box.width <= 390 && box.height <= 844)
  await dialog.getByRole("button", { name: "确定删除", exact: true }).click()
  await dialog.waitFor({ state: "hidden" })
  assert.equal(project.slides.length, 2)
  assert.deepEqual(project.slide_lectures.map((l) => [l.index, l.lecture]), [[1, "first page"], [2, "move me"]])
  // The same dialog also persists a teacher chapter through save-lecture.
  let chapterWrite
  await page.route("**/api/education/get-chapter?*", (route) => route.fulfill({ json: {
    success: true, chapter: { ...project, id: "ppt_deletion_test", ppt_slides: project.slides },
  } }))
  await page.route("**/api/education/save-lecture", async (route) => {
    chapterWrite = route.request().postDataJSON()
    return route.fulfill({ json: { success: true } })
  })
  await page.goto(`${baseUrl}/teacher/prepare?chapterId=ppt_deletion_test`)
  await page.getByTitle("第 1 页：Page 1", { exact: true }).click()
  await page.getByRole("button", { name: "删除当前页", exact: true }).click()
  await dialog.getByLabel("第 1 页：Page 1", { exact: false }).fill("")
  await dialog.getByRole("button", { name: "确定删除", exact: true }).click()
  await dialog.waitFor({ state: "hidden" })
  assert.equal(chapterWrite.ppt_slides.length, 1)
  assert.equal(chapterWrite.slide_lectures[0].lecture, "move me")
  assert.equal(chapterWrite.rendered_pages.length, 1)
  assert.deepEqual(errors, [])
  console.log(`PASS: PDF deletion, target focus, sparse lectures, cancel, transfer, save failure and retry, reload, mobile, teacher chapter; ${writes + 1} intercepted writes.`)
} finally {
  await browser.close()
}
