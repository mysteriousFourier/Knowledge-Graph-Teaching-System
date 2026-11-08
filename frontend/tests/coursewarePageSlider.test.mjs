import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import ts from "typescript"

// Run the component's real event handlers with a small hook host; no browser or
// network is required to check which page navigations it emits while scrubbing.
const source = readFileSync(new URL("../src/components/common/CoursewarePageSlider.tsx", import.meta.url), "utf8")
  .replace('import { useRef, useState } from "react"', 'const { useRef, useState } = globalThis.sliderTestHooks; const makeElement = (_type, props) => props')
const slots = []
let cursor = 0
globalThis.sliderTestHooks = {
  useState(initial) {
    const index = cursor++
    if (!(index in slots)) slots[index] = initial
    return [slots[index], (next) => { slots[index] = typeof next === "function" ? next(slots[index]) : next }]
  },
  useRef(initial) {
    const index = cursor++
    return slots[index] ??= { current: initial }
  },
}
const { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React, jsxFactory: "makeElement",
} })
const { CoursewarePageSlider } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`)
function setup() {
  slots.length = 0
  const jumps = []
  const render = () => {
    cursor = 0
    return CoursewarePageSlider({ current: 0, total: 149, label: "课件页面", onJump: (index) => jumps.push(index) })
  }
  return { jumps, render }
}
const event = (value) => ({ target: { value: String(value) }, currentTarget: { value: String(value) } })

test("dragging across 149 pages loads only the final destination", () => {
  const { jumps, render } = setup()
  render().onPointerDown()
  for (let page = 2; page <= 149; page++) render().onChange(event(page))
  assert.equal(render().value, 149)
  assert.deepEqual(jumps, [])
  render().onPointerUp(event(149))
  render().onLostPointerCapture(event(149))
  assert.deepEqual(jumps, [148])
})

test("keyboard page changes remain immediate", () => {
  const { jumps, render } = setup()
  for (const page of [100, 120, 149]) render().onChange(event(page))
  assert.deepEqual(jumps, [99, 119, 148])
})

test("cancelled drags do not navigate; blur commits a pending destination once", () => {
  const { jumps, render } = setup()
  render().onPointerDown()
  render().onChange(event(120))
  render().onPointerCancel()
  render().onLostPointerCapture(event(120))
  assert.equal(render().value, 1)
  assert.deepEqual(jumps, [])
  render().onPointerDown()
  render().onChange(event(100))
  render().onBlur(event(100))
  render().onPointerUp(event(100))
  assert.deepEqual(jumps, [99])
})
