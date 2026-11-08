import { createFileRoute, Link } from "@tanstack/react-router"
import { ArrowDown, ArrowUp, BookOpen, FileText, FolderOpen, Pencil, Plus, Trash2 } from "lucide-react"
import { useState, type FormEvent } from "react"
import { useCourses, useCreateCourse, useUpdateCourse } from "@/api/courses"
import { useCoursewareProjects, useDeleteCoursewareProject } from "@/api/education"
import { useQueryClient } from "@tanstack/react-query"
import { useDeleteChapter, useReorderLessons, useTeacherChapters } from "@/api/teacher"
import { EmptyState } from "@/components/common/EmptyState"
import { LoadingSpinner } from "@/components/common/LoadingSpinner"
import type { Chapter } from "@/types/chapter"
import type { Course } from "@/types/course"

export const Route = createFileRoute("/teacher/")({
  component: TeacherPage,
})

function TeacherPage() {
  const [creating, setCreating] = useState(false)
  const { data, isLoading, isError } = useCourses()
  const courses = data?.courses || []

  return (
    <div className="space-y-7">
      <header className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <span className="sheet-label">Teacher Console</span>
          <h1 className="mt-2 text-3xl font-bold">课程选择</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            先进入一门课程，再选择具体课节进行备课或授课。
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCreating((value) => !value)}
          className="inline-flex min-h-10 items-center justify-center gap-2 border bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <Plus size={16} />
          新建课程
        </button>
      </header>

      {creating && <CourseEditor onClose={() => setCreating(false)} />}

      {isLoading ? (
        <div className="flex min-h-48 items-center justify-center border bg-card">
          <LoadingSpinner text="正在加载课程..." />
        </div>
      ) : isError ? (
        <div className="border border-destructive/40 bg-card p-6 text-sm text-destructive">
          课程列表加载失败，请刷新后重试。
        </div>
      ) : courses.length === 0 ? (
        <EmptyState
          title="还没有课程"
          description="先新建一门课程，之后保存的课件和课节会归档到这里。"
        />
      ) : (
        <section className="space-y-4" aria-label="课程列表">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">我的课程</h2>
            <span className="mono text-xs text-muted-foreground">{courses.length} 门课程</span>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {courses.map((course) => (
              <CourseCard key={course.id} course={course} />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

function CourseCard({ course }: { course: Course }) {
  const [editing, setEditing] = useState(false)
  const [lessonOrder, setLessonOrder] = useState<Chapter[] | null>(null)
  const [orderError, setOrderError] = useState("")
  const reorder = useReorderLessons()
  const { data, isLoading } = useTeacherChapters(course.id)
  const { data: projectsData } = useCoursewareProjects(course.id)
  const deleteProject = useDeleteCoursewareProject()
  const queryClient = useQueryClient()
  const chapters = data?.chapters || []
  const projects = projectsData?.projects || []
  const displayedChapters = lessonOrder ?? chapters
  const moveLesson = (index: number, direction: number) => {
    if (!lessonOrder || reorder.isPending) return
    const next = [...lessonOrder]
    const target = index + direction
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    setLessonOrder(next)
  }
  const saveOrder = async () => {
    if (!lessonOrder) return
    setOrderError("")
    try {
      const result = await reorder.mutateAsync({ courseId: course.id, chapterIds: lessonOrder.map((chapter) => chapter.id) })
      queryClient.setQueryData(["teacher-chapters", course.id], result)
      setLessonOrder(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["teacher-chapters"] }),
        queryClient.invalidateQueries({ queryKey: ["student-chapters"] }),
      ])
    } catch (cause) {
      setOrderError(cause instanceof Error ? cause.message : "排序保存失败，请重试")
    }
  }
  const handleDeleteProject = async (project: { id: string; title?: string }) => {
    if (!window.confirm(`删除课件“${project.title || project.id}”？`)) return
    try {
      await deleteProject.mutateAsync({ projectId: project.id, courseId: course.id })
      queryClient.removeQueries({ queryKey: ["courseware-project", project.id] })
      await queryClient.invalidateQueries({ queryKey: ["courseware-projects", course.id] })
      await queryClient.invalidateQueries({ queryKey: ["courses"] })
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "删除失败，请重试")
    }
  }

  return (
    <article className="flex min-h-64 min-w-0 flex-col border bg-card p-5 transition-colors hover:border-primary/50">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <span className="mono text-[11px] uppercase tracking-[0.12em] text-muted-foreground">Course</span>
          <h3 className="mt-1 truncate text-xl font-semibold">{course.title}</h3>
          <p className="mt-1 line-clamp-2 min-h-10 text-sm text-muted-foreground">
            {course.description || "暂无课程说明"}
          </p>
        </div>
        <button type="button" onClick={() => setEditing((value) => !value)} title="编辑课程" aria-label={`编辑课程 ${course.title}`} className="inline-flex h-9 w-9 shrink-0 items-center justify-center border hover:bg-accent"><Pencil size={15} /></button>
        <Link
          to="/teacher/prepare"
          search={{ chapterId: "", nodeId: "", courseId: course.id }}
          className="inline-flex h-9 shrink-0 items-center justify-center gap-1.5 border bg-secondary px-3 text-sm font-medium transition-colors hover:bg-accent"
          title={`进入课程 ${course.title}`}
        >
          <FolderOpen size={15} />
          进入课程
        </Link>
      </div>

      {editing && <CourseEditor course={course} onClose={() => setEditing(false)} />}

      <div className="mt-5 border-t pt-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-sm font-semibold">课节</h4>
          <div className="flex items-center gap-3"><span className="mono text-xs text-muted-foreground">
            {isLoading ? "读取中" : `${chapters.length} 节`}
          </span>
          {!lessonOrder && chapters.length > 1 && <button type="button" onClick={() => { setOrderError(""); setLessonOrder([...chapters]) }} className="border px-2.5 py-1.5 text-xs hover:bg-accent">调整顺序</button>}
          </div>
        </div>
        {lessonOrder && <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted-foreground">用箭头调整授课顺序</span>
          <button type="button" disabled={reorder.isPending} onClick={() => setLessonOrder([...lessonOrder].sort((a, b) => a.title.replace(/[_-]/g, " ").localeCompare(b.title.replace(/[_-]/g, " "), "zh-CN", { numeric: true })))} className="border px-2.5 py-1.5 hover:bg-accent disabled:opacity-50">按名称排序</button>
          <button type="button" disabled={reorder.isPending} onClick={() => void saveOrder()} className="border bg-primary px-2.5 py-1.5 text-primary-foreground disabled:opacity-50">{reorder.isPending ? "保存中..." : "保存顺序"}</button>
          <button type="button" disabled={reorder.isPending} onClick={() => { setLessonOrder(null); setOrderError("") }} className="border px-2.5 py-1.5 hover:bg-accent disabled:opacity-50">取消</button>
        </div>}
        {orderError && <p role="alert" className="mb-3 text-sm text-destructive">{orderError}</p>}
        {isLoading ? (
          <div className="flex min-h-16 items-center text-sm text-muted-foreground">
            <LoadingSpinner size={16} text="正在读取课节..." />
          </div>
        ) : chapters.length === 0 ? (
          <p className="border border-dashed px-3 py-4 text-sm text-muted-foreground">
            课程内还没有课节，进入备课后保存第一节课件。
          </p>
        ) : (
          <div className="space-y-2">
            {displayedChapters.map((chapter, index) => (
              <LessonRow key={chapter.id} chapter={chapter} index={index} courseId={course.id}
                sorting={Boolean(lessonOrder)} first={index === 0} last={index === displayedChapters.length - 1}
                orderPending={reorder.isPending} onMove={(direction) => moveLesson(index, direction)} />
            ))}
          </div>
        )}
        {projects.length > 0 ? (
          <div className="mt-4 space-y-2 border-t pt-3">
            <h4 className="text-sm font-semibold">已保存课件</h4>
            {projects.map((project) => (
              <div
                key={project.id}
                className="flex items-center justify-between border bg-background/40 px-3 py-2 text-sm hover:bg-accent"
              >
                <Link to="/teacher/prepare" search={{ chapterId: project.id, nodeId: "", courseId: course.id }} className="min-w-0 flex-1 truncate">{project.title}</Link>
                <span className="ml-3 flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                  编辑
                  <button type="button" onClick={() => void handleDeleteProject(project)} disabled={deleteProject.isPending} className="inline-flex h-8 items-center gap-1 border px-2 text-destructive hover:bg-destructive/10 disabled:opacity-50" aria-label={`删除课件 ${project.title || project.id}`}><Trash2 size={13} />删除</button>
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </article>
  )
}

function CourseEditor({ course, onClose }: { course?: Course; onClose: () => void }) {
  const [title, setTitle] = useState(course?.title || "")
  const [description, setDescription] = useState(course?.description || "")
  const [error, setError] = useState("")
  const create = useCreateCourse()
  const update = useUpdateCourse()
  const queryClient = useQueryClient()
  const save = async (event: FormEvent) => {
    event.preventDefault()
    try {
      if (course) await update.mutateAsync({ courseId: course.id, title: title.trim(), description: description.trim() })
      else await create.mutateAsync({ title: title.trim(), description: description.trim() })
      await queryClient.invalidateQueries({ queryKey: ["courses"] })
      onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败")
    }
  }
  return <form onSubmit={save} className="mt-4 space-y-3 border-y py-4">
    <label className="block text-sm">课程名称<input required maxLength={160} value={title} onChange={(event) => setTitle(event.target.value)} className="mt-1" /></label>
    <label className="block text-sm">课程说明<textarea maxLength={1000} value={description} onChange={(event) => setDescription(event.target.value)} className="mt-1" /></label>
    <div className="flex gap-2"><button disabled={!title.trim() || create.isPending || update.isPending} className="border bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50">保存</button><button type="button" onClick={onClose} className="border px-3 py-2 text-sm">取消</button></div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </form>
}

function LessonRow({ chapter, index, courseId, sorting, first, last, orderPending, onMove }: { chapter: Chapter; index: number; courseId: string; sorting: boolean; first: boolean; last: boolean; orderPending: boolean; onMove: (direction: number) => void }) {
  const hasLecture = Boolean(chapter.has_lecture_content || chapter.lecture_content)
  const deleteChapter = useDeleteChapter()
  const queryClient = useQueryClient()
  const handleDelete = async () => {
    if (!window.confirm(`删除课件“${chapter.title}”及其讲稿和练习？`)) return
    try {
      const result = await deleteChapter.mutateAsync(chapter.id)
      if (!result.success) throw new Error(result.error || "删除失败")
      queryClient.removeQueries({ queryKey: ["teacher-chapter", chapter.id] })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["teacher-chapters"] }),
        queryClient.invalidateQueries({ queryKey: ["student-chapters"] }),
        queryClient.invalidateQueries({ queryKey: ["courses"] }),
      ])
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "删除失败，请重试")
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3 border bg-background/40 px-3 py-2.5">
      <span className="mono w-6 shrink-0 text-xs text-muted-foreground">{String(index + 1).padStart(2, "0")}</span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{chapter.title}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {hasLecture ? "已有授课文案" : "尚未生成授课文案"}
        </p>
      </div>
      <div className="ml-auto flex shrink-0 flex-wrap items-center gap-1.5">
        {sorting ? <>
          <button type="button" disabled={first || orderPending} onClick={() => onMove(-1)} aria-label={`上移 ${chapter.title}`} className="inline-flex h-11 w-11 items-center justify-center border hover:bg-accent disabled:opacity-40"><ArrowUp size={16} /></button>
          <button type="button" disabled={last || orderPending} onClick={() => onMove(1)} aria-label={`下移 ${chapter.title}`} className="inline-flex h-11 w-11 items-center justify-center border hover:bg-accent disabled:opacity-40"><ArrowDown size={16} /></button>
        </> : <>
        <Link
          to="/teacher/prepare"
          search={{ chapterId: chapter.id, nodeId: "", courseId }}
          className="inline-flex h-8 items-center justify-center gap-1 border px-2.5 text-xs font-medium transition-colors hover:bg-accent"
          title={`备课：${chapter.title}`}
        >
          <BookOpen size={14} />
          备课
        </Link>
        <Link
          to="/teacher/lecture"
          search={{ chapterId: chapter.id, courseId }}
          className="inline-flex h-8 items-center justify-center gap-1 border bg-primary/10 px-2.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
          title={`授课：${chapter.title}`}
        >
          <FileText size={14} />
          授课
        </Link>
        <button type="button" onClick={handleDelete} disabled={deleteChapter.isPending} aria-label={`删除课件 ${chapter.title}`} className="inline-flex h-8 items-center gap-1 border px-2.5 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50">
          <Trash2 size={14} />{deleteChapter.isPending ? "删除中..." : "删除"}
        </button>
        </>}
      </div>
    </div>
  )
}
