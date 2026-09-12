from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

class CourseCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=160, description="Course title")
    description: str = Field("", max_length=1000, description="Course description")
    course_id: Optional[str] = Field(None, max_length=100, description="Optional stable course ID")


class CourseUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=160, description="Course title")
    description: Optional[str] = Field(None, max_length=1000, description="Course description")


class GenerateLectureRequest(BaseModel):
    chapter_id: str = Field(..., description="Chapter ID")
    course_id: Optional[str] = Field(None, description="Course ID used to resolve a teacher pedagogy profile")
    teacher_profile_id: Optional[str] = Field(None, description="Optional teacher pedagogy profile ID")
    chapter_title: str = Field("", description="Chapter title")
    chapter_content: str = Field("", description="Chapter content")
    style: str = Field("guided", description="Teaching style")
    source_node_id: Optional[str] = Field(None, description="Graph node ID used as the lecture source")
    source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs used as lecture sources")
    graph_scope: Optional[str] = Field(None, description="Graph source scope, currently subtree")
    teacher_guidance: Optional[str] = Field(None, description="Optional teacher guidance for emphasis, selection, and pacing")
    api_key: Optional[str] = Field(None, description="DeepSeek API key")
    model: Optional[str] = Field(None, description="DeepSeek model name")


class GeneratePptTexRequest(BaseModel):
    chapter_title: str = Field("", description="Optional title override")
    content: str = Field("", description="Optional free-form courseware content when no graph node is selected")
    allow_no_node: bool = Field(False, description="Generate from content without requiring a graph node")
    course_id: Optional[str] = Field(None, description="Course ID used to resolve a teacher pedagogy profile")
    teacher_profile_id: Optional[str] = Field(None, description="Optional teacher pedagogy profile ID")
    style: str = Field("引导式教学", description="Teaching style")
    source_node_id: Optional[str] = Field(None, description="Graph node ID used as the PPT/TeX source")
    source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs used as PPT/TeX sources")
    graph_scope: Optional[str] = Field("subtree", description="Graph source scope, currently subtree")
    teacher_guidance: Optional[str] = Field(None, description="Optional teacher guidance for emphasis, selection, and pacing")
    style_reference: Optional[Dict[str, Any]] = Field(None, description="Compact courseware style reference profile/guidance")
    max_slides: int = Field(12, description="Maximum slide count")
    api_key: Optional[str] = Field(None, description="DeepSeek API key")
    model: Optional[str] = Field(None, description="DeepSeek model name")


class PreviewTexRequest(BaseModel):
    tex_content: str = Field(..., description="Editable TeX source to parse into slide previews")
    filename: str = Field("edited.tex", description="Virtual filename used for parser hints")
    asset_map: Optional[Dict[str, Any]] = Field(None, description="Uploaded assets referenced by the TeX source")


class CoursewareProjectSaveRequest(BaseModel):
    project_id: Optional[str] = Field(None, description="Existing project ID to update")
    course_id: Optional[str] = Field(None, description="Course that owns this courseware project")
    title: str = Field("未命名课件", description="Project title")
    editable_model: Dict[str, Any] = Field(default_factory=dict, description="Structured editable slide model")
    asset_map: Optional[Dict[str, Any]] = Field(None, description="Courseware asset map")
    slides: Optional[List[Dict[str, Any]]] = Field(None, description="Legacy preview slide payload")
    tex_content: Optional[str] = Field(None, description="Serialized TeX source")
    rendered_pages: Optional[List[Dict[str, Any]]] = Field(None, description="Compiled PDF page render payloads")
    render_source: Optional[str] = Field(None, description="Render source marker, e.g. latex")
    render_error: Optional[str] = Field(None, description="Last LaTeX render error")
    ppt_artifact: Optional[Dict[str, Any]] = Field(None, description="Export artifact metadata")
    source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs associated with this project")
    lecture_target_duration_minutes: Optional[float] = Field(None, ge=0.1, le=180, description="Persisted target duration for slide lectures")
    lecture_speech_rate_cpm: Optional[int] = Field(None, ge=80, le=800, description="Persisted speech rate for slide lectures")
    lecture_pacing: Optional[Dict[str, Any]] = Field(None, description="Persisted lecture pacing summary")


class CoursewareExportPptxRequest(BaseModel):
    title: str = Field("未命名课件", description="Export title")
    editable_model: Dict[str, Any] = Field(default_factory=dict, description="Structured editable slide model")
    source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs used as sources")


class GenerateSlideLecturesRequest(BaseModel):
    chapter_title: str = Field("", description="Generated PPT/TeX title")
    course_id: Optional[str] = Field(None, description="Course ID used to resolve a teacher pedagogy profile")
    teacher_profile_id: Optional[str] = Field(None, description="Optional teacher pedagogy profile ID")
    allow_no_node: bool = Field(False, description="Allow generation without graph nodes")
    slides: List[Dict[str, Any]] = Field(default_factory=list, description="Generated slide/page details")
    tex_content: Optional[str] = Field(None, description="Generated TeX source")
    style: str = Field("引导式教学", description="Teaching style")
    target_duration_minutes: Optional[float] = Field(135, ge=0.1, le=180, description="Target lecture duration for the whole courseware")
    speech_rate_cpm: Optional[int] = Field(250, ge=80, le=800, description="Estimated Chinese characters per minute")
    source_node_id: Optional[str] = Field(None, description="Graph node ID used as the lecture source")
    source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs used as lecture sources")
    graph_scope: Optional[str] = Field("subtree", description="Graph source scope, currently subtree")
    teacher_guidance: Optional[str] = Field(None, description="Optional teacher guidance for emphasis, selection, and pacing")
    slide_feedback: Optional[Dict[str, str]] = Field(None, description="Optional per-slide regeneration feedback keyed by slide ID")
    style_reference: Optional[Dict[str, Any]] = Field(None, description="Compact courseware style reference profile/guidance")
    target_slide_indices: Optional[List[int]] = Field(None, description="Slide indices to regenerate; empty means all slides")
    target_slide_ids: Optional[List[str]] = Field(None, description="Stable slide IDs to regenerate")
    existing_slide_lectures: Optional[List[Dict[str, Any]]] = Field(None, description="Existing slide lectures to preserve when regenerating selected slides")
    ppt_source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs used to generate the PPT/TeX")
    ppt_source_scope: Optional[Dict[str, Any]] = Field(None, description="Graph source scope used to generate the PPT/TeX")
    api_key: Optional[str] = Field(None, description="DeepSeek API key")
    model: Optional[str] = Field(None, description="DeepSeek model name")


class PlanSlideSpeechRequest(BaseModel):
    chapter_title: str = Field("", description="Courseware or chapter title")
    slide: Dict[str, Any] = Field(default_factory=dict, description="Current slide/page details")
    lecture: str = Field(..., min_length=1, description="Visible lecture script text")
    max_cues: int = Field(1, ge=0, le=3, description="Maximum speech cues to return")
    teacher_guidance: Optional[str] = Field(None, description="Optional teacher guidance for emphasis")
    api_key: Optional[str] = Field(None, description="DeepSeek API key")
    model: Optional[str] = Field(None, description="DeepSeek model name")


class AskQuestionRequest(BaseModel):
    question: str = Field(..., description="Question")
    api_key: Optional[str] = Field(None, description="DeepSeek API key")


class LearningPlanRequest(BaseModel):
    query: str = Field(..., description="Learner question or teacher task")
    chapter_id: Optional[str] = Field(None, description="Chapter ID")
    task: str = Field("qa", description="Task type: qa, lecture, exercise, feedback")
    learning_level: str = Field("beginner", description="Learner level")


class NaturalSupplementRequest(BaseModel):
    original_text: str = Field(..., description="Original text")
    supplement: str = Field(..., description="Supplemental text")
    insert_position: Optional[str] = Field(None, description="Insert position")
    save_draft_if_fail: bool = Field(False, description="Save draft if merge fails")
    api_key: Optional[str] = Field(None, description="DeepSeek API key")


class ChapterRequest(BaseModel):
    chapter_id: str = Field(..., description="Chapter ID")


class MarkChapterRequest(BaseModel):
    chapter_id: str = Field(..., description="Chapter ID")
    student_id: Optional[str] = Field(None, description="Student ID")
    status: Optional[str] = Field("learned", description="learned, reviewing, forgotten, or reset")


class ResetProgressRequest(BaseModel):
    chapter_id: Optional[str] = Field(None, description="Chapter ID")
    student_id: Optional[str] = Field(None, description="Student ID")


class ExerciseRequest(BaseModel):
    chapter_id: str = Field(..., description="Chapter ID")


class CheckAnswerRequest(BaseModel):
    exercise_id: str = Field(..., description="Exercise ID")
    question: str = Field(..., description="Question")
    answer: str = Field(..., description="User answer")
    chapter_id: str = Field(..., description="Chapter ID")
    correct_answer: Optional[str] = Field(None, description="Correct answer")
    explanation: Optional[str] = Field(None, description="Explanation")


class QuestionRequest(BaseModel):
    question: str = Field(..., description="Question")
    student_id: Optional[str] = Field(None, description="Student ID")
    chapter_id: Optional[str] = Field(None, description="Chapter ID")
    context: Optional[str] = Field(None, description="Chapter context")
    api_key: Optional[str] = Field(None, description="DeepSeek API key")


class GenerateReviewRequest(BaseModel):
    chapter_id: str = Field(..., description="Chapter ID")
    count: int = Field(5, description="Review exercise count")
    api_key: Optional[str] = Field(None, description="DeepSeek API key")
    model: Optional[str] = Field(None, description="DeepSeek model name")


class SaveChapterRequest(BaseModel):
    chapter_id: Optional[str] = Field(None, description="Chapter ID")
    course_id: Optional[str] = Field(None, description="Course that owns this chapter")
    title: str = Field(..., description="Chapter title")
    content: Optional[str] = Field(None, description="Chapter content")
    graph_data: Optional[Dict[str, Any]] = Field(None, description="Knowledge graph data")
    source_type: Optional[str] = Field(None, description="Chapter source type")
    source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs used as chapter sources")
    source_scope: Optional[Dict[str, Any]] = Field(None, description="Graph source scope metadata")
    ppt_slides: Optional[List[Dict[str, Any]]] = Field(None, description="PPT slide parse result")
    slide_lectures: Optional[List[Dict[str, Any]]] = Field(None, description="PPT slide lectures")
    tex_content: Optional[str] = Field(None, description="Generated TeX source")
    editable_model: Optional[Dict[str, Any]] = Field(None, description="Structured editable courseware model")
    asset_map: Optional[Dict[str, Any]] = Field(None, description="Courseware asset map")
    rendered_pages: Optional[List[Dict[str, Any]]] = Field(None, description="Compiled PDF page render payloads")
    render_source: Optional[str] = Field(None, description="Render source marker, e.g. latex")
    render_error: Optional[str] = Field(None, description="Last LaTeX render error")
    ppt_artifact: Optional[Dict[str, Any]] = Field(None, description="Generated PPT/TeX artifact metadata")
    ppt_source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs used to generate PPT/TeX")
    lecture_source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs used to generate slide lectures")
    lecture_target_duration_minutes: Optional[float] = Field(None, ge=0.1, le=180, description="Persisted target duration for slide lectures")
    lecture_speech_rate_cpm: Optional[int] = Field(None, ge=80, le=800, description="Persisted speech rate for slide lectures")
    lecture_pacing: Optional[Dict[str, Any]] = Field(None, description="Persisted lecture pacing summary")


class SaveLectureRequest(BaseModel):
    chapter_id: str = Field(..., description="Chapter ID")
    course_id: Optional[str] = Field(None, description="Course that owns this chapter")
    lecture_content: str = Field(..., description="Lecture content")
    graph_data: Optional[Dict[str, Any]] = Field(None, description="Knowledge graph data")
    source_type: Optional[str] = Field(None, description="Chapter source type")
    source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs used as lecture sources")
    source_scope: Optional[Dict[str, Any]] = Field(None, description="Graph source scope metadata")
    ppt_slides: Optional[List[Dict[str, Any]]] = Field(None, description="PPT slide parse result")
    slide_lectures: Optional[List[Dict[str, Any]]] = Field(None, description="PPT slide lectures")
    learning_plan: Optional[Dict[str, Any]] = Field(None, description="Lecture grounding plan")
    consistency_report: Optional[Dict[str, Any]] = Field(None, description="Lecture consistency report")
    tex_content: Optional[str] = Field(None, description="Generated TeX source")
    editable_model: Optional[Dict[str, Any]] = Field(None, description="Structured editable courseware model")
    asset_map: Optional[Dict[str, Any]] = Field(None, description="Courseware asset map")
    rendered_pages: Optional[List[Dict[str, Any]]] = Field(None, description="Compiled PDF page render payloads")
    render_source: Optional[str] = Field(None, description="Render source marker, e.g. latex")
    render_error: Optional[str] = Field(None, description="Last LaTeX render error")
    ppt_artifact: Optional[Dict[str, Any]] = Field(None, description="Generated PPT/TeX artifact metadata")
    ppt_source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs used to generate PPT/TeX")
    lecture_source_node_ids: Optional[List[str]] = Field(None, description="Graph node IDs used to generate slide lectures")
    lecture_target_duration_minutes: Optional[float] = Field(None, ge=0.1, le=180, description="Persisted target duration for slide lectures")
    lecture_speech_rate_cpm: Optional[int] = Field(None, ge=80, le=800, description="Persisted speech rate for slide lectures")
    lecture_pacing: Optional[Dict[str, Any]] = Field(None, description="Persisted lecture pacing summary")


class GenerateExercisesRequest(BaseModel):
    chapter_id: str = Field(..., description="Chapter ID")
    chapter_title: str = Field("", description="Chapter title")
    chapter_content: str = Field("", description="Chapter content")
    count: int = Field(5, description="Exercise count")
    types: Optional[List[str]] = Field(None, description="Requested exercise types")
    api_key: Optional[str] = Field(None, description="DeepSeek API key")
    model: Optional[str] = Field(None, description="DeepSeek model name")
    force_regenerate: bool = Field(False, description="Force exercise regeneration")


class TeacherExerciseFeedbackRequest(BaseModel):
    chapter_id: str = Field(..., description="Chapter ID")
    exercise_id: str = Field(..., description="Exercise ID")
    rating: str = Field("", description="up, down, or clear")
    feedback: Optional[str] = Field(None, description="Legacy frontend feedback value: like or dislike")
    question: Optional[str] = Field(None, description="Question text snapshot")
    note: Optional[str] = Field(None, description="Optional teacher note")
    scope: Optional[str] = Field("exercise", description="exercise or option")
    feedback_key: Optional[str] = Field(None, description="Stable exercise feedback key")
    option_key: Optional[str] = Field(None, description="Option letter for option feedback")
    option_text: Optional[str] = Field(None, description="Option text snapshot")
    option_feedback_key: Optional[str] = Field(None, description="Stable option feedback key")
    options: Optional[List[Any]] = Field(None, description="Exercise options snapshot")
    correct_answer: Optional[str] = Field(None, description="Correct answer snapshot")


class TeacherRegenerateExercisesRequest(BaseModel):
    chapter_id: str = Field(..., description="Chapter ID")
    chapter_title: Optional[str] = Field(None, description="Chapter title snapshot")
    chapter_content: Optional[str] = Field(None, description="Chapter content snapshot")
    count: int = Field(5, description="Question count")
    api_key: Optional[str] = Field(None, description="DeepSeek API key")
    model: Optional[str] = Field(None, description="DeepSeek model name")
    force_regenerate: bool = Field(True, description="Force rebuild")


class TeacherRegenerateOptionRequest(TeacherExerciseFeedbackRequest):
    api_key: Optional[str] = Field(None, description="DeepSeek API key")
    model: Optional[str] = Field(None, description="DeepSeek model name")
