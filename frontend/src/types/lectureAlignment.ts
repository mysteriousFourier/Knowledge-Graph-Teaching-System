export interface LectureMaterial {
  name: string
  role: "transcript" | "reference"
  text: string
}

export interface LectureAlignmentProvenance {
  source: "adapted" | "simulated" | "retained"
  source_files: string[]
  source_ids: string[]
  timestamps: string[]
  notes: string
  retained_chars?: number
}

export interface LectureAlignmentAudit {
  page_count: number
  chars: number
  original_chars: number
  source_counts: Record<string, number>
  empty_pages: number[]
  warnings: string[]
  passed: boolean
  coverage_csv: string
  report_markdown: string
}
