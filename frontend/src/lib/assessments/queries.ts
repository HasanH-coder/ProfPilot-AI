import "server-only";

import { cache } from "react";

import type { AssessmentDraft, Difficulty } from "@/lib/assessments/draft";
import type { Course } from "@/lib/courses/queries";
import type { DocumentCategory, DocumentFile } from "@/lib/documents/files";
import { createClient } from "@/lib/supabase/server";

/** An assessment as shown in the Assessments list. */
export type AssessmentListItem = {
  id: string;
  examName: string;
  status: string;
  updatedAt: string;
  courseCode: string | null;
  durationMinutes: number | null;
  numberOfVersions: number;
  difficulty: Difficulty | null;
  /** How many files the professor uploaded for the assessment. */
  fileCount: number;
};

/**
 * The signed-in professor's assessments, most recently updated first, or just
 * the first `limit` of them. Row Level Security returns only their own.
 */
export async function getAssessments({ limit }: { limit?: number } = {}): Promise<AssessmentListItem[]> {
  const supabase = await createClient();
  let query = supabase
    .from("exam_projects")
    .select(
      "id, exam_name, status, updated_at, duration_minutes, number_of_versions, difficulty, course:courses(code), documents(count)",
    )
    .order("updated_at", { ascending: false });
  if (limit) query = query.limit(limit);
  const { data, error } = await query;
  if (error) throw new Error(`Could not load assessments: ${error.message}`);

  return data.map((row) => ({
    id: row.id,
    examName: row.exam_name,
    status: row.status,
    updatedAt: row.updated_at,
    courseCode: row.course?.code ?? null,
    durationMinutes: row.duration_minutes,
    numberOfVersions: row.number_of_versions,
    difficulty: row.difficulty as Difficulty | null,
    fileCount: row.documents[0]?.count ?? 0,
  }));
}

/** A saved assessment, with everything its overview and edit pages show. */
export type Assessment = {
  id: string;
  status: string;
  updatedAt: string;
  /** null when no course is linked, for example after the course was deleted. */
  course: Course | null;
  /** The saved settings, in the shape the Create assessment form uses. */
  draft: AssessmentDraft;
  files: DocumentFile[];
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * One of the signed-in professor's assessments, or null if there is none with
 * this id. Another professor's assessment is null too: Row Level Security
 * hides it. Cached per request, so a page and its metadata share one query.
 */
export const getAssessment = cache(async (id: string): Promise<Assessment | null> => {
  // Anything that isn't an id (from a mistyped address) can't match.
  if (!UUID.test(id)) return null;

  const supabase = await createClient();
  const { data, error } = await supabase
    .from("exam_projects")
    .select(
      "id, status, updated_at, course_id, exam_name, duration_minutes, mcq_percentage, subjective_percentage, number_of_versions, difficulty, additional_notes, professor_prompt, course:courses(id, code, name), documents(id, category, original_name, size_bytes)",
    )
    .eq("id", id)
    .order("created_at", { referencedTable: "documents" })
    .maybeSingle();
  if (error) throw new Error(`Could not load the assessment: ${error.message}`);
  if (!data) return null;

  return {
    id: data.id,
    status: data.status,
    updatedAt: data.updated_at,
    course: data.course,
    draft: {
      courseId: data.course_id,
      examName: data.exam_name,
      durationMinutes: data.duration_minutes,
      mcqPercentage: data.mcq_percentage,
      subjectivePercentage: data.subjective_percentage,
      numberOfVersions: data.number_of_versions,
      difficulty: data.difficulty as Difficulty | null,
      additionalNotes: data.additional_notes ?? "",
      professorPrompt: data.professor_prompt ?? "",
    },
    files: data.documents.map((document) => ({
      id: document.id,
      // The database only allows the three categories.
      category: document.category as DocumentCategory,
      name: document.original_name,
      size: document.size_bytes,
    })),
  };
});
