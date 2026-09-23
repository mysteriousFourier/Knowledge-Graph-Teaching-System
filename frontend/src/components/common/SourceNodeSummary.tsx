import { Network } from "lucide-react"

export function SourceNodeSummary({ nodeIds, className = "" }: { nodeIds?: unknown; className?: string }) {
  const ids = Array.isArray(nodeIds)
    ? Array.from(new Set(nodeIds.map((value) => String(value || "").trim()).filter(Boolean)))
    : []
  if (!ids.length) return null

  return (
    <div className={`flex flex-wrap items-center gap-2 text-xs text-muted-foreground ${className}`}>
      <span className="inline-flex items-center gap-1 font-medium text-foreground">
        <Network size={14} />
        使用节点
      </span>
      {ids.map((id) => (
        <span key={id} className="rounded border bg-muted/35 px-2 py-1" title={id}>
          {id}
        </span>
      ))}
    </div>
  )
}
