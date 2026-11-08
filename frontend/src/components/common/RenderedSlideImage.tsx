import { useEffect, useRef, useState } from "react"
import { Loader2, RefreshCw } from "lucide-react"

export function RenderedSlideImage({ src, title }: { src: string; title: string }) {
  // The keyed child prevents an earlier image's load/error event from changing
  // the state of a later page after a jump.
  return <PageImage key={src} src={src} title={title} />
}

function PageImage({ src, title }: { src: string; title: string }) {
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading")
  const imageRef = useRef<HTMLImageElement>(null)
  const retry = () => { setStatus("loading"); setAttempt((value) => value + 1) }
  const imageSrc = attempt ? `${src}${src.includes("?") ? "&" : "?"}retry=${attempt}` : src

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      if (!imageRef.current?.naturalWidth) setStatus("error")
    }, 15000)
    return () => window.clearTimeout(timeout)
  }, [imageSrc])

  useEffect(() => {
    if (status !== "error" || attempt !== 0) return
    const timeout = window.setTimeout(retry, 400)
    return () => window.clearTimeout(timeout)
  }, [status, attempt])

  return (
    <div className="relative h-full w-full bg-white">
      <img
        key={imageSrc}
        ref={imageRef}
        src={imageSrc}
        alt={title}
        className="h-full w-full object-contain"
        draggable={false}
        onLoad={() => setStatus("ready")}
        onError={() => setStatus("error")}
      />
      {status !== "ready" && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-white text-sm text-slate-600" role="status">
          {status === "loading" ? <><Loader2 size={22} className="animate-spin" />正在加载课件页面…</> : (
            <>
              <span>课件页面加载失败</span>
              <button type="button" onClick={(event) => { event.stopPropagation(); retry() }} className="inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-slate-800">
                <RefreshCw size={16} />重新加载本页
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
