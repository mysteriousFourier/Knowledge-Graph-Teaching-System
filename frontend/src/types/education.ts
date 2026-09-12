export interface GenerateLectureRequest {
  chapter_id?: string
  course_id?: string
  teacher_profile_id?: string
  chapter_content?: string
  chapter_title?: string
  style?: string
  length?: string
  source_node_id?: string
  source_node_ids?: string[]
  graph_scope?: "subtree" | string
  teacher_guidance?: string
}

export interface GraphSourceScope {
  mode?: string
  root_count?: number
  selected_count?: number
  referenced_count?: number
  max_nodes?: number
  truncated?: boolean
  [key: string]: unknown
}

export interface GenerateLectureResponse {
  success: boolean
  content?: string
  lecture_content?: string
  chapter_id?: string
  chapter_title?: string
  consistency_report?: ConsistencyReport
  learning_plan?: unknown
  source_node_id?: string
  source_node_ids?: string[]
  source_scope?: GraphSourceScope
  teacher_profile_id?: string | null
  error?: string
}

export interface TtsStatusResponse {
  success: boolean
  enabled: boolean
  provider: "disabled" | "genie" | "genie_server" | "azure_speech" | "gpt_sovits_local" | "gpt_sovits_server" | string
  available: boolean
  model_loaded?: boolean
  runtime_root?: string
  model_id?: string
  cache_enabled?: boolean
  cache_files?: number
  cache_size_mb?: number
  last_error?: string | null
  character_name?: string
  detail?: string
  output_dir?: string
  max_chars?: number
}

export interface SpeechCue {
  type: "repeat" | "key_point" | string
  target_text: string
  style?: string
  priority?: number
}

export interface TtsSynthesizeRequest {
  text: string
  character_name?: string
  split_sentence?: boolean
  language?: string
  speed_factor?: number
  chapter_id?: string
  segment_id?: string
  content_hash?: string
  force?: boolean
  speech_cues?: SpeechCue[]
}

export interface TtsSynthesizeResponse {
  success: boolean
  provider: string
  audio_url?: string
  cache_hit?: boolean
  cache_key?: string | null
  normalized_text_length?: number
  text_lang?: string
  text_length?: number
  error?: string
  detail?: string
}

export interface TtsSegmentRequest {
  text: string
  language?: string
  max_chars?: number
  speech_cues?: SpeechCue[]
}

export interface TtsSegmentItem {
  index: number
  text: string
  length: number
}

export interface TtsSegmentsResponse {
  success: boolean
  segments: TtsSegmentItem[]
  segment_count: number
  normalized_text_length: number
  text_lang: string
  max_chars: number
  error?: string
  detail?: string
}

export interface TtsCourseJobSlide {
  slide_index: number
  position: number
  text: string
  speech_cues?: SpeechCue[]
}

export interface TtsCourseJobRequest {
  chapter_id: string
  slides: TtsCourseJobSlide[]
  max_chars?: number
  language?: string
}

export interface TtsCourseJobResponse {
  success: boolean
  job_id: string
  chapter_id?: string
  status: "queued" | "running" | "stopping" | "completed" | "failed" | "cancelled" | string
  stage?: string
  message?: string
  created_at?: string
  updated_at?: string
  started_at?: string | null
  slide_count: number
  current_slide: number
  current_slide_index?: number | null
  current_chunk?: number
  ready_chunks: number
  total_chunks: number
  cache_hits: number
  max_chars?: number
  elapsed_seconds?: number
  error?: string
}

export interface TtsLatestCourseJobResponse {
  success: boolean
  job: TtsCourseJobResponse | null
}

export interface GraphContextTreeNode {
  id: string
  label: string
  type: string
  children?: GraphContextTreeNode[]
}

export interface GraphNodeContextResponse {
  success: boolean
  node_id?: string
  node_ids?: string[]
  root?: unknown
  roots?: unknown[]
  tree?: GraphContextTreeNode
  nodes?: unknown[]
  relations?: unknown[]
  chapter_title?: string
  chapter_content?: string
  scope?: GraphSourceScope
  evidence?: unknown[]
  error?: string
}

export interface ConsistencyEntity {
  id?: string
  name: string
  type?: string
  source_index?: number
  count?: number
}

export interface ConsistencyReport {
  knowledge_support_ratio?: number
  unsupported_concept_rate?: number
  entity_recall?: number
  entity_hallucination_rate?: number
  expected_entities?: ConsistencyEntity[]
  mentioned_entities?: ConsistencyEntity[]
  missing_entities?: ConsistencyEntity[]
  extracted_entities?: ConsistencyEntity[]
  unsupported_entities?: ConsistencyEntity[]
  learning_goal_alignment?: number
  difficulty_match?: string
  hint_policy_violated?: boolean
  is_safe_to_show?: boolean
  warnings?: string[]
}

export interface UploadGraphResponse {
  success: boolean
  file_name?: string
  graph_type?: string
  chapter_hint?: {
    title?: string
    content?: string
  }
  parsed?: {
    nodes: number
    relations: number
  }
  result?: unknown
  message?: string
  imported_at?: string
  error?: string
}

export interface AskQuestionRequest {
  question: string
  chapter_id?: string
  context?: string
  student_id?: string
}

export interface AskQuestionResponse {
  success: boolean
  answer?: string
  sources?: unknown[]
  retrieval_context?: string
  learning_plan?: unknown
  consistency_report?: ConsistencyReport
  warning?: string
  error?: string
}

export interface StudentQuestionRequest {
  question: string
  chapter_id?: string
  context?: string
  student_id?: string
}

export interface StudentQuestionResponse {
  success: boolean
  answer?: string
  sources?: unknown[]
  retrieval_context?: string
  learning_plan?: unknown
  consistency_report?: ConsistencyReport
  warning?: string
  error?: string
}

export interface GenerateReviewRequest {
  chapter_id: string
  count?: number
}

export interface GenerateReviewResponse {
  success: boolean
  chapter_id?: string
  review_content?: string
  exercise_bank?: unknown[]
  learning_plan?: unknown
  consistency_report?: ConsistencyReport
  generated_at?: string
  warning?: string
  error?: string
}

export interface LearningPlanRequest {
  query?: string
  chapter_id?: string
  task?: string
  learning_level?: string
  student_id?: string
}

export interface LearningPlanResponse {
  success: boolean
  learning_plan?: {
    nodes?: string[]
    path?: string[]
  }
  plan?: {
    nodes: string[]
    path: string[]
  }
  error?: string
}

export interface PptImageInfo {
  data_uri?: string | null
  width_emu: number
  height_emu: number
  left_emu: number
  top_emu: number
  source_path?: string
  tex_options?: string
  tex_ref?: string
  width_ratio?: number
  height_ratio?: number
  oversized?: boolean
  missing?: boolean
}

export interface CoursewareAsset {
  id: string
  name?: string
  source_path?: string
  tex_ref?: string
  mime_type?: string
  data_uri?: string | null
  aliases?: string[]
  slide_indices?: number[]
  figure_refs?: string[]
  oversized?: boolean
}

export interface EditableSlideBBox {
  x: number
  y: number
  width: number
  height: number
}

export interface EditableSlideObject {
  id: string
  type: "title" | "richText" | "textbox" | "equation" | "table" | "image" | "placeholder" | "callout" | string
  bbox: EditableSlideBBox
  z?: number
  locked?: boolean
  style?: Record<string, unknown>
  text?: string
  rich_html?: string
  latex?: string
  rows?: string[][]
  asset_id?: string
  source_path?: string
  tex_ref?: string
  width_ratio?: number
  label?: string
  title?: string
  role?: string
}

export interface EditableSlide {
  id: string
  index: number
  title?: string
  items: EditableSlideObject[]
  objects?: EditableSlideObject[]
  layout?: PptSlideDetail["layout"]
  source_tex?: string
  source_body_tex?: string
  source_start?: number | null
  source_end?: number | null
  notes?: string
}

export interface EditableSlideModel {
  version: number
  title?: string
  slide_count?: number
  canvas?: {
    width: number
    height: number
    unit?: string
  }
  layout?: {
    canvas?: {
      width: number
      height: number
    }
    [key: string]: unknown
  }
  source_tex?: string
  source_tex_file?: string
  assets?: Record<string, CoursewareAsset>
  slides: EditableSlide[]
  updated_at?: string
}

export interface CoursewareProject {
  course_id?: string
  id: string
  title: string
  editable_model?: EditableSlideModel
  asset_map?: Record<string, CoursewareAsset>
  slides?: PptSlideDetail[]
  tex_content?: string
  rendered_pages?: RenderedCoursewarePage[]
  render_source?: string
  render_error?: string
  ppt_artifact?: PptArtifact
  source_node_ids?: string[]
  lecture_target_duration_minutes?: number
  lecture_speech_rate_cpm?: number
  lecture_pacing?: SlideLecturePacingSummary
  slide_count?: number
  created_at?: string
  updated_at?: string
}

export interface PptTable {
  rows: string[][]
}

export interface PptSlideDetail {
  index: number
  slide_id?: string
  parent_slide_index?: number
  overlay_index?: number
  overlay_count?: number
  rendered_page_index?: number
  title?: string
  content?: string
  notes?: string
  rendered_page?: RenderedCoursewarePage
  has_images?: boolean
  image_count?: number
  images?: PptImageInfo[]
  tables?: PptTable[]
  body_texts?: string[]
  raw_text?: string
  source_tex?: string
  source_body_tex?: string
  source_start?: number | null
  source_end?: number | null
  source_node_ids?: string[]
  missing_image_refs?: string[]
  layout?: {
    mode?: "text" | "title" | "columns" | "image_only" | "image_text" | "text_image" | string
    has_columns?: boolean
    column_count?: number
    columns?: Array<{
      width_ratio?: number | null
      content?: string
      images?: PptImageInfo[]
      image_count?: number
      image_first?: boolean
      align?: "left" | "center" | string
      max_image_width?: number | null
      source_tex?: string
      [key: string]: unknown
    }>
    outside_content?: string
    align?: "left" | "center" | string
    image_first?: boolean
    image_count?: number
    max_image_width?: number | null
    canvas?: {
      items?: Array<{
        id?: string
        type?: "title" | "content" | "image" | string
        ref?: string
        x?: number
        y?: number
        width?: number
        height?: number
        [key: string]: unknown
      }>
      [key: string]: unknown
    }
    [key: string]: unknown
  }
}

export interface RenderedCoursewarePage {
  page_index: number
  slide_id?: string
  image: string
  width: number
  height: number
}

export interface PptArtifact {
  kind?: string
  pptx_path?: string
  tex_path?: string
  pptx_url?: string
  tex_url?: string
  tex_content_hash?: string
  slide_count?: number
  source_node_ids?: string[]
  generated_at?: string
  [key: string]: unknown
}

export interface PptSlideLecture {
  index: number
  slide_id?: string
  parent_slide_index?: number
  overlay_index?: number
  overlay_count?: number
  title?: string
  lecture: string
  skipped: boolean
  target_chars?: number
  target_duration_seconds?: number
  budget_source?: string
  estimated_chars?: number
  estimated_duration_seconds?: number
  source_node_ids?: string[]
  sources?: unknown[]
  graph_paths?: unknown[]
  formula_context?: unknown[]
  speech_cues?: SpeechCue[]
  learning_plan?: unknown
  consistency_report?: ConsistencyReport
  error?: string
  warning?: string
  generation_model?: string
  generation_status?: string
  generation_attempts?: number
  completion_model?: string
  completion_added_chars?: number
  completion_error?: string
}

export interface PlanSlideSpeechRequest {
  chapter_title?: string
  slide: PptSlideDetail
  lecture: string
  max_cues?: number
  teacher_guidance?: string
}

export interface PlanSlideSpeechResponse {
  success: boolean
  speech_cues: SpeechCue[]
  speech_cue_count?: number
  estimated_chars?: number
  error?: string
  detail?: string
}

export interface SlideLecturePacingSummary {
  target_duration_minutes?: number
  speech_rate_cpm?: number
  total_target_chars?: number
  estimated_chars?: number
  estimated_duration_seconds?: number
}

export interface CoursewareStyleColor {
  name?: string
  model?: string
  value?: string
}

export interface CoursewareStyleProfile {
  document_class?: string
  document_options?: string[]
  themes?: string[]
  packages?: string[]
  colors?: CoursewareStyleColor[]
  newcommands?: string[]
  beamertemplates?: string[]
  layout_summary?: {
    frame_count?: number
    columns_frame_count?: number
    image_frame_count?: number
    formula_frame_count?: number
    tikz_frame_count?: number
    itemize_frame_count?: number
    [key: string]: unknown
  }
  style_signals?: string[]
  sample_frame_titles?: string[]
  source_file?: string
  archive_file_count?: number
  archive_image_count?: number
  archive_image_paths?: string[]
  truncated?: boolean
  [key: string]: unknown
}

export interface CoursewareStyleReference {
  source_filename?: string
  profile?: CoursewareStyleProfile
  guidance?: string
  warning?: string
  [key: string]: unknown
}

export interface CoursewareStyleReferenceResponse extends CoursewareStyleReference {
  success: boolean
  error?: string
}

export interface PptTexGenerateRequest {
  chapter_title?: string
  content?: string
  allow_no_node?: boolean
  course_id?: string
  teacher_profile_id?: string
  style?: string
  source_node_id?: string
  source_node_ids?: string[]
  graph_scope?: "subtree" | string
  teacher_guidance?: string
  style_reference?: CoursewareStyleReference | null
  max_slides?: number
}

export interface PptPreviewResponse {
  success: boolean
  chapter_title: string
  slide_count: number
  slides: PptSlideDetail[]
  full_text: string
  tex_content?: string
  tex_source_file?: string
  editable_model?: EditableSlideModel
  asset_map?: Record<string, CoursewareAsset>
  layout?: EditableSlideModel["layout"]
  source_tex?: string
  rendered_pages?: RenderedCoursewarePage[]
  render_job_id?: string
  render_status?: "queued" | "running" | "completed" | "failed" | string
  render_source?: string
  render_error?: string
  warning?: string
  missing_image_refs?: string[]
  error?: string
}

export interface CoursewareRenderJobResponse {
  success: boolean
  job_id: string
  status: "queued" | "running" | "completed" | "failed" | string
  rendered_pages?: RenderedCoursewarePage[]
  rendered_page_count?: number
  render_error?: string
  created_at?: string
  started_at?: string
  finished_at?: string
}

export interface PptTexGenerateResponse extends PptPreviewResponse {
  tex_content?: string
  ppt_artifact?: PptArtifact | null
  learning_plan?: unknown
  graph_paths?: unknown[]
  formula_context?: unknown[]
  source_node_id?: string | null
  source_node_ids?: string[]
  source_scope?: GraphSourceScope | null
  auto_selected_nodes?: boolean
  teacher_profile_id?: string | null
  style?: string
  model?: string
  generated_at?: string
  message?: string
}

export interface GenerateSlideLecturesRequest {
  chapter_title?: string
  course_id?: string
  teacher_profile_id?: string
  allow_no_node?: boolean
  slides: PptSlideDetail[]
  tex_content?: string
  style?: string
  target_duration_minutes?: number
  speech_rate_cpm?: number
  source_node_id?: string
  source_node_ids?: string[]
  graph_scope?: "subtree" | string
  teacher_guidance?: string
  slide_feedback?: Record<number, string>
  style_reference?: CoursewareStyleReference | null
  target_slide_indices?: number[]
  existing_slide_lectures?: PptSlideLecture[]
  ppt_source_node_ids?: string[]
  ppt_source_scope?: GraphSourceScope | null
}

export interface SourceDriftReport {
  status?: "aligned" | "changed" | "unknown" | string
  changed?: boolean
  added_node_ids?: string[]
  removed_node_ids?: string[]
  ppt_source_node_ids?: string[]
  lecture_source_node_ids?: string[]
  warning?: string
}

export interface PptUploadResponse extends PptTexGenerateResponse {
  lecture_content: string
  slide_lectures: PptSlideLecture[]
  lecture_pacing?: SlideLecturePacingSummary
  consistency_report?: ConsistencyReport
  drift_report?: SourceDriftReport
  ppt_source_node_ids?: string[]
  lecture_source_node_ids?: string[]
}

export interface NaturalSupplementRequest {
  original_text: string
  supplement: string
  insert_position?: string
  save_draft_if_fail?: boolean
}

export interface NaturalSupplementResponse {
  success: boolean
  result?: string
  model?: string
  retrieval_context?: string
  sources?: unknown[]
  learning_plan?: unknown
  consistency_report?: ConsistencyReport
  warning?: string
  error?: string
}
