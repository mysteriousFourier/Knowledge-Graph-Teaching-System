export interface Chapter {
  course_id?: string
  source_project_id?: string
  course_order?: number
  id: string
  title: string
  content?: string
  lecture_content?: string
  lecture_learning_plan?: unknown
  lecture_consistency_report?: import("./education").ConsistencyReport
  source_type?: string
  source_node_ids?: string[]
  source_scope?: import("./education").GraphSourceScope
  ppt_slides?: import("./education").PptSlideDetail[]
  slide_lectures?: import("./education").PptSlideLecture[]
  tex_content?: string
  editable_model?: import("./education").EditableSlideModel
  asset_map?: Record<string, import("./education").CoursewareAsset>
  rendered_pages?: import("./education").RenderedCoursewarePage[]
  render_source?: string
  render_error?: string
  has_content?: boolean
  has_lecture_content?: boolean
  has_tex_content?: boolean
  has_rendered_pages?: boolean
  rendered_page_count?: number
  ppt_slide_count?: number
  slide_lecture_count?: number
  exercise_count?: number
  content_preview?: string
  ppt_artifact?: import("./education").PptArtifact
  ppt_source_node_ids?: string[]
  lecture_source_node_ids?: string[]
  lecture_target_duration_minutes?: number
  lecture_speech_rate_cpm?: number
  lecture_pacing?: import("./education").SlideLecturePacingSummary
  created_at?: string | number
  updated_at?: string | number
  source?: string
  exercise_bank?: Exercise[]
  approved_exercise_bank?: Exercise[]
}

export interface Exercise {
  id: string
  type: "填空题" | "选择题" | "简答题" | string
  question: string
  options?: string[]
  answer?: string
  correct_answer?: string
  explanation?: string
  difficulty?: number
}

export interface SaveChapterRequest {
  course_id?: string
  source_project_id?: string
  chapter_id: string
  title: string
  content?: string
  graph_data?: Record<string, unknown>
  source_type?: string
  source_node_ids?: string[]
  source_scope?: import("./education").GraphSourceScope
  ppt_slides?: import("./education").PptSlideDetail[]
  slide_lectures?: import("./education").PptSlideLecture[]
  tex_content?: string
  editable_model?: import("./education").EditableSlideModel
  asset_map?: Record<string, import("./education").CoursewareAsset>
  rendered_pages?: import("./education").RenderedCoursewarePage[]
  render_source?: string
  render_error?: string
  ppt_artifact?: import("./education").PptArtifact
  ppt_source_node_ids?: string[]
  lecture_source_node_ids?: string[]
  lecture_target_duration_minutes?: number
  lecture_speech_rate_cpm?: number
  lecture_pacing?: import("./education").SlideLecturePacingSummary
}

export interface SaveLectureRequest {
  course_id?: string
  chapter_id: string
  lecture_content: string
  graph_data?: Record<string, unknown>
  learning_plan?: unknown
  consistency_report?: import("./education").ConsistencyReport
  source_type?: string
  source_node_ids?: string[]
  source_scope?: import("./education").GraphSourceScope
  ppt_slides?: import("./education").PptSlideDetail[]
  slide_lectures?: import("./education").PptSlideLecture[]
  tex_content?: string
  editable_model?: import("./education").EditableSlideModel
  asset_map?: Record<string, import("./education").CoursewareAsset>
  rendered_pages?: import("./education").RenderedCoursewarePage[]
  render_source?: string
  render_error?: string
  ppt_artifact?: import("./education").PptArtifact
  ppt_source_node_ids?: string[]
  lecture_source_node_ids?: string[]
  lecture_target_duration_minutes?: number
  lecture_speech_rate_cpm?: number
  lecture_pacing?: import("./education").SlideLecturePacingSummary
}
