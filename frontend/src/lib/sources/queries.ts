import "server-only";

import { createClient } from "@/lib/supabase/server";
import type { Tables } from "@/lib/supabase/database.types";
import type { CourseSource, CourseWithSources, SourceStatus } from "@/lib/sources/types";

export const SOURCE_COLUMNS =
  "id, course_id, category, original_name, size_bytes, processing_status, processing_error, page_count, summary, created_at";

type SourceRow = Pick<
  Tables<"documents">,
  | "id"
  | "course_id"
  | "original_name"
  | "size_bytes"
  | "processing_status"
  | "processing_error"
  | "page_count"
  | "summary"
  | "created_at"
>;

export function toCourseSource(row: SourceRow): CourseSource {
  const summary = row.summary && typeof row.summary === "object" && !Array.isArray(row.summary) ? row.summary : null;
  return {
    id: row.id,
    courseId: row.course_id ?? "",
    category: "course_material",
    name: row.original_name,
    size: row.size_bytes,
    status: row.processing_status as SourceStatus,
    error: row.processing_error,
    pageCount: row.page_count,
    title: summary && typeof summary.title === "string" ? summary.title : null,
    createdAt: row.created_at,
  };
}

/** A course's reusable sources: course material not owned by any one assessment. */
function sourcesQuery(supabase: Awaited<ReturnType<typeof createClient>>) {
  return supabase
    .from("documents")
    .select(SOURCE_COLUMNS)
    .is("exam_project_id", null)
    .eq("category", "course_material")
    .not("course_id", "is", null)
    .order("created_at", { ascending: false });
}

export async function getCoursesWithSources(): Promise<CourseWithSources[]> {
  const supabase = await createClient();
  const [{ data: courses, error: coursesError }, { data: documents, error: documentsError }] = await Promise.all([
    supabase.from("courses").select("id, code, name").order("code"),
    sourcesQuery(supabase),
  ]);
  if (coursesError) throw new Error(`Could not load courses: ${coursesError.message}`);
  if (documentsError) throw new Error(`Could not load course sources: ${documentsError.message}`);

  const byCourse = new Map<string, CourseSource[]>();
  for (const row of documents) {
    const source = toCourseSource(row);
    byCourse.set(source.courseId, [...(byCourse.get(source.courseId) ?? []), source]);
  }
  return courses.map((course) => ({ ...course, sources: byCourse.get(course.id) ?? [] }));
}

/** Data needed by the reusable selector when an agent workflow chooses a course. */
export async function getCourseSources(courseId: string): Promise<CourseSource[]> {
  const supabase = await createClient();
  const { data, error } = await sourcesQuery(supabase).eq("course_id", courseId);
  if (error) throw new Error(`Could not load course sources: ${error.message}`);
  return data.map(toCourseSource);
}

export async function getAssessmentSourceDocumentIds(assessmentId: string): Promise<string[]> {
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("assessment_documents")
    .select("document_id")
    .eq("exam_project_id", assessmentId)
    .order("created_at");
  if (error) throw new Error(`Could not load selected sources: ${error.message}`);
  return data.map((row) => row.document_id);
}
