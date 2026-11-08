import { useRef, useState } from "react"

interface Props {
  current: number
  total: number
  onJump: (page: number) => void
  label: string
  disabled?: boolean
  className?: string
}

// Keep pointer scrubbing local: a long course should fetch only the page where
// the pointer is released. Keyboard changes still navigate immediately.
export function CoursewarePageSlider({ current, total, onJump, label, disabled, className }: Props) {
  const [draft, setDraft] = useState<number | null>(null)
  const dragging = useRef(false)
  const commit = (value: string) => {
    if (!dragging.current) return
    dragging.current = false
    setDraft(null)
    const page = Number(value) - 1
    if (page !== current) onJump(page)
  }

  return (
    <input
      type="range"
      min={1}
      max={Math.max(total, 1)}
      step={1}
      value={draft ?? current + 1}
      aria-label={label}
      aria-valuetext={`第 ${draft ?? current + 1} 页，共 ${total} 页`}
      disabled={disabled}
      className={className}
      onPointerDown={() => { dragging.current = true }}
      onChange={(event) => {
        const value = Number(event.target.value)
        if (dragging.current) setDraft(value)
        else onJump(value - 1)
      }}
      onPointerUp={(event) => commit(event.currentTarget.value)}
      onLostPointerCapture={(event) => commit(event.currentTarget.value)}
      onBlur={(event) => commit(event.currentTarget.value)}
      onPointerCancel={() => { dragging.current = false; setDraft(null) }}
    />
  )
}
