"use server";

import type { PostgrestError } from "@supabase/supabase-js";
import { redirect } from "next/navigation";

import {
  isAssessmentDraft,
  LIMITS,
  validateAssessmentDraft,
  type AssessmentDraft,
  type AssessmentDraftErrors,
} from "@/lib/assessments/draft";
import { getCurrentProfessor } from "@/lib/auth/current-professor";
import { createClient } from "@/lib/supabase/server";

export type SaveDraftResult = {
  /** A problem with the whole form, such as the database being unreachable. */
  error?: string;
  /** Problems with single fields, shown beside them. */
  fieldErrors?: AssessmentDraftErrors;
};

/** The draft that uploaded files belong to. */
export type UploadDraft = {
  examProjectId: string;
  /** The draft's folder in Storage: {professor_id}/{exam_project_id}. */
  folder: string;
};

/**
 * Creates the draft exam project that uploaded files are attached to. The form
 * calls this when the first file is added and reuses the draft afterwards.
 */
export async function createDraftForUploads(input: {
  courseId: string | null;
  examName: string;
}): Promise<{ draft?: UploadDraft; error?: string }> {
  const professor = await getCurrentProfessor();
  const courseId = typeof input?.courseId === "string" ? input.courseId : null;
  const examName = typeof input?.examName === "string" ? input.examName.trim() : "";
  if (!courseId || !examName || examName.length > LIMITS.examName) {
    return { error: "Choose a course and enter the assessment name before adding files." };
  }

  const supabase = await createClient();
  const { data, error } = await supabase
    .from("exam_projects")
    .insert({ course_id: courseId, exam_name: examName })
    .select("id")
    .single();
  if (error) {
    if (isCourseError(error)) return { error: "Choose one of your courses before adding files." };
    console.error("Could not create a draft for uploads:", error);
    return { error: "Your files couldn't be added. Please try again." };
  }

  return { draft: { examProjectId: data.id, folder: `${professor.id}/${data.id}` } };
}

/**
 * Saves the Create assessment form as a draft, then opens the Assessments page.
 * When files were uploaded, the draft already exists, so it is updated instead.
 */
export async function saveAssessmentDraft(
  draft: AssessmentDraft,
  examProjectId: string | null,
): Promise<SaveDraftResult> {
  await getCurrentProfessor();

  // The browser can send anything, so check the shape and the rules again here.
  if (!isAssessmentDraft(draft) || !(examProjectId === null || typeof examProjectId === "string")) {
    return { error: "Something went wrong. Please reload the page and try again." };
  }
  const fieldErrors = validateAssessmentDraft(draft);
  if (Object.keys(fieldErrors).length > 0) return { fieldErrors };

  const values = {
    course_id: draft.courseId,
    exam_name: draft.examName.trim(),
    duration_minutes: draft.durationMinutes,
    mcq_percentage: draft.mcqPercentage,
    subjective_percentage: draft.subjectivePercentage,
    number_of_versions: draft.numberOfVersions,
    difficulty: draft.difficulty,
    additional_notes: textOrNull(draft.additionalNotes),
    professor_prompt: textOrNull(draft.professorPrompt),
  };

  const supabase = await createClient();
  if (examProjectId) {
    // Row Level Security only lets the professor update their own draft.
    const { data, error } = await supabase
      .from("exam_projects")
      .update(values)
      .eq("id", examProjectId)
      .eq("status", "draft")
      .select("id")
      .maybeSingle();
    if (error) return saveFailure(error);
    if (!data) return { error: "This draft no longer exists. Please reload the page." };

    // Keep the uploaded files linked to the draft's course, in case it changed.
    const { error: documentsError } = await supabase
      .from("documents")
      .update({ course_id: values.course_id })
      .eq("exam_project_id", examProjectId);
    if (documentsError) return saveFailure(documentsError);
  } else {
    // professor_id defaults to the signed-in professor and status to 'draft'.
    const { error } = await supabase.from("exam_projects").insert(values);
    if (error) return saveFailure(error);
  }

  redirect("/workspace/assessments?saved=1");
}

function saveFailure(error: PostgrestError): SaveDraftResult {
  if (isCourseError(error)) return { fieldErrors: { courseId: "Choose one of your courses." } };
  console.error("Could not save assessment draft:", error);
  return { error: "Your draft couldn't be saved. Please try again." };
}

// Row Level Security (42501) or the foreign key (23503) rejects a course that
// doesn't exist or belongs to another professor.
function isCourseError(error: PostgrestError) {
  return error.code === "42501" || error.code === "23503";
}

/** Keeps the text exactly as the professor wrote it, or stores nothing if it is blank. */
function textOrNull(text: string) {
  return text.trim() ? text : null;
}
