import { useMutation, useQuery } from "@tanstack/react-query"
import { educationClient } from "./client"
import { unpackCourseware } from "./coursewareTransport"
import type { ApiResponse, LoginApiResponse } from "@/types/api"
import type { Chapter, Exercise, SaveChapterRequest, SaveLectureRequest } from "@/types/chapter"

export interface GenerateExercisesPayload extends ApiResponse<{ exercises: Exercise[] }> {
  exercise?: Exercise
  exercise_bank?: Exercise[]
  approved_exercise_bank?: Exercise[]
  chapter?: Chapter
  cached?: boolean
  review_pending?: boolean
  warning?: string
}

export interface RegenerateOptionPayload extends ApiResponse<unknown> {
  chapter_id?: string
  scope?: "option" | string
  option_key?: string
  old_option?: string
  replacement_option?: string
  replacement_source?: string
  exercise_bank?: Exercise[]
  approved_exercise_bank?: Exercise[]
  feedback_summary?: Record<string, number>
}

export interface ExerciseFeedbackPayload extends ApiResponse<unknown> {
  chapter_id?: string
  feedback_key?: string
  scope?: "exercise" | "option" | string
  teacher_rating?: string
  exercise_bank?: Exercise[]
  approved_exercise_bank?: Exercise[]
  feedback_summary?: Record<string, number>
}

export const useTeacherLogin = () => {
  return useMutation({
    mutationFn: (credentials: { username: string; password: string }) =>
      educationClient
        .post<LoginApiResponse>("/api/teacher/login", credentials)
        .then((r) => r.data),
  })
}

export const useTeacherChapters = (courseId?: string) => {
  const normalizedCourseId = courseId?.trim() || ""
  return useQuery({
    queryKey: normalizedCourseId ? ["teacher-chapters", normalizedCourseId] : ["teacher-chapters"],
    queryFn: () => {
      const query = normalizedCourseId ? "?course_id=" + encodeURIComponent(normalizedCourseId) : ""
      return educationClient
        .get<{ success: boolean; chapters: Chapter[] }>("/api/education/list-chapters" + query)
        .then((r) => r.data)
    },
  })
}

export const useTeacherChapter = (chapterId: string, includeAssets = false) => {
  return useQuery({
    queryKey: ["teacher-chapter", chapterId, includeAssets ? "assets" : "compact"],
    queryFn: ({ signal }) =>
      educationClient
        .get<{ success: boolean; chapter?: Chapter; error?: string }>(
          `/api/education/get-chapter?chapter_id=${encodeURIComponent(chapterId)}${includeAssets ? "&include_assets=1&compact_strings=1" : ""}`,
          { signal },
        )
        .then((r) => unpackCourseware(r.data)),
    enabled: Boolean(chapterId),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    retry: 1,
  })
}

export const useSaveChapter = () => {
  return useMutation({
    mutationFn: (data: SaveChapterRequest) =>
      educationClient.post<{ success: boolean; chapter_id: string; chapter: Chapter }>("/api/education/save-chapter", data).then((r) => r.data),
  })
}

export const useSaveLecture = () => {
  return useMutation({
    mutationFn: (data: SaveLectureRequest) =>
      educationClient.post<ApiResponse<unknown>>("/api/education/save-lecture", data).then((r) => r.data),
  })
}

export const useDeleteChapter = () => {
  return useMutation({
    mutationFn: (chapterId: string) =>
      educationClient
        .delete<{ success: boolean; chapter_id?: string; deleted_ids?: string[]; error?: string }>(
          `/api/education/delete-chapter?chapter_id=${encodeURIComponent(chapterId)}`,
        )
        .then((r) => r.data),
  })
}

export const useGenerateExercises = () => {
  return useMutation({
    mutationFn: (data: {
      chapter_id: string
      chapter_title?: string
      chapter_content?: string
      count?: number
      types?: string[]
      force_regenerate?: boolean
    }) =>
      educationClient
        .post<GenerateExercisesPayload>("/api/education/teacher/regenerate-exercises", data)
        .then((r) => r.data),
  })
}

export const useFeedbackExercise = () => {
  return useMutation({
    mutationFn: (data: {
      exercise_id: string
      chapter_id: string
      feedback?: "like" | "dislike"
      rating?: "up" | "down" | "clear"
      option_index?: number
      question?: string
      options?: string[]
      correct_answer?: string
    }) =>
      educationClient.post<ExerciseFeedbackPayload>("/api/education/teacher/exercise-feedback", data).then((r) => r.data),
  })
}

export const useRegenerateExerciseOption = () => {
  return useMutation({
    mutationFn: (data: {
      chapter_id: string
      exercise_id: string
      rating?: "down" | "clear"
      question?: string
      option_key: string
      option_text?: string
      options?: string[]
      correct_answer?: string
      feedback_key?: string
      option_feedback_key?: string
      note?: string
    }) =>
      educationClient
        .post<RegenerateOptionPayload>("/api/education/teacher/regenerate-option", data)
        .then((r) => r.data),
  })
}
