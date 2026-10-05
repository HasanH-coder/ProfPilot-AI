"use server";

import type { PostgrestError } from "@supabase/supabase-js";
import { refresh } from "next/cache";
import { redirect } from "next/navigation";

import {
  isAssessmentDraft,
  LIMITS,
  validateAssessmentDraft,
  type AssessmentDraft,
  type AssessmentDraftErrors,
} from "@/lib/assessments/draft";
import { getCurrentProfessor } from "@/lib/auth/current-professor";
import { ASSESSMENT_FILES_BUCKET, DOCUMENT_CATEGORIES } from "@/lib/documents/files";
import { createClient } from "@/lib/supabase/server";

/** Where a draft lives: its exam project, and its folder in Storage. */
export type DraftLocation = {
  examProjectId: string;
  /** {professor_id}/{exam_project_id} */
  folder: string;
};

export type CreateDraftResult = { draft?: DraftLocation; error?: string };

export type SaveDraftResult = {
  /** A problem with the whole form, such as the database being unreachable. */
  error?: string;
  /** Problems with single fields, shown beside them. */
  fieldErrors?: AssessmentDraftErrors;
};

// Every action checks the session itself: Server Actions can be called
// directly, not only from our pages. Row Level Security then makes sure a
// professor can only ever read, change or delete their own assessments.

/**
 * Creates the draft for a new assessment, with its course and name if the
 * professor has given them (both are optional). The form calls this once, on
 * the first upload or the first save. Everything after that updates this
 * draft, so one assessment never becomes two.
 */
export async function createAssessmentDraft(input: {
  courseId: string | null;
  examName: string;
}): Promise<CreateDraftResult> {
  const professor = await getCurrentProfessor();
  const courseId = typeof input?.courseId === "string" ? input.courseId : null;
  const examName = typeof input?.examName === "string" ? input.examName.trim() : "";
  if (examName.length > LIMITS.examName) {
    return { error: `Use an assessment name of ${LIMITS.examName} characters or fewer.` };
  }

  const supabase = await createClient();
  // professor_id defaults to the signed-in professor and status to 'draft'.
  const { data, error } = await supabase
    .from("exam_projects")
    .insert({ course_id: courseId, exam_name: examName || null })
    .select("id")
    .single();
  if (error) {
    if (isCourseError(error)) return { error: "Choose one of your courses." };
    console.error("Could not create assessment draft:", error);
    return { error: "Your assessment couldn't be saved. Please try again." };
  }

  // Other pages, such as the Assessments list, now include the new draft.
  refresh();
  return { draft: { examProjectId: data.id, folder: `${professor.id}/${data.id}` } };
}

/** Saves the form to the professor's draft. An empty result means it was saved. */
export async function saveAssessmentDraft(
  examProjectId: string,
  draft: AssessmentDraft,
): Promise<SaveDraftResult> {
  await getCurrentProfessor();

  // The browser can send anything, so check the shape and the rules again here.
  if (typeof examProjectId !== "string" || !isAssessmentDraft(draft)) {
    return { error: "Something went wrong. Please reload the page and try again." };
  }
  const fieldErrors = validateAssessmentDraft(draft);
  if (Object.keys(fieldErrors).length > 0) return { fieldErrors };

  const supabase = await createClient();
  // Row Level Security only lets the professor update their own drafts.
  const { data, error } = await supabase
    .from("exam_projects")
    .update({
      course_id: draft.courseId,
      // An unnamed assessment is stored as null, never as a placeholder name.
      exam_name: draft.examName.trim() || null,
      duration_minutes: draft.durationMinutes,
      mcq_percentage: draft.mcqPercentage,
      subjective_percentage: draft.subjectivePercentage,
      number_of_versions: draft.numberOfVersions,
      // The deprecated difficulty column is left as it is: these replace it.
      easy_percentage: draft.easyPercentage,
      medium_percentage: draft.mediumPercentage,
      hard_percentage: draft.hardPercentage,
      additional_notes: textOrNull(draft.additionalNotes),
      professor_prompt: textOrNull(draft.professorPrompt),
    })
    .eq("id", examProjectId)
    .eq("status", "draft")
    .select("id")
    .maybeSingle();
  if (error) return saveFailure(error);
  if (!data) return { error: "This draft no longer exists. It may have been deleted." };

  // Keep the uploaded files linked to the draft's course, in case it changed.
  const { error: documentsError } = await supabase
    .from("documents")
    .update({ course_id: draft.courseId })
    .eq("exam_project_id", examProjectId);
  if (documentsError) return saveFailure(documentsError);

  // Pages that show this draft, including ones in the browser's history, show the change.
  refresh();
  return {};
}

/**
 * Deletes a draft and all its files, then opens the Assessments page. The
 * files in Storage go first, so a failure never leaves files without their
 * assessment. Deleting the exam project then deletes its documents rows too
 * (on delete cascade).
 */
export async function deleteAssessment(examProjectId: string): Promise<{ error?: string }> {
  const professor = await getCurrentProfessor();
  const supabase = await createClient();

  // Row Level Security only finds the professor's own drafts.
  const { data: assessment, error: findError } = await supabase
    .from("exam_projects")
    .select("id, documents(storage_path)")
    .eq("id", examProjectId)
    .eq("status", "draft")
    .maybeSingle();
  if (findError) return deleteFailure(findError);
  if (!assessment) return { error: "This assessment no longer exists." };

  // Every recorded file, plus anything an interrupted upload left in the draft's folder.
  const storage = supabase.storage.from(ASSESSMENT_FILES_BUCKET);
  const paths = new Set(assessment.documents.map((document) => document.storage_path));
  for (const category of DOCUMENT_CATEGORIES) {
    const folder = `${professor.id}/${assessment.id}/${category}`;
    const { data: files } = await storage.list(folder, { limit: 1000 });
    for (const file of files ?? []) paths.add(`${folder}/${file.name}`);
  }
  if (paths.size > 0) {
    const { error } = await storage.remove([...paths]);
    if (error) return deleteFailure(error);
  }

  const { data: deleted, error } = await supabase
    .from("exam_projects")
    .delete()
    .eq("id", assessment.id)
    .select("id");
  if (error) return deleteFailure(error);
  if (deleted.length === 0) return { error: "This assessment no longer exists." };

  // Make sure no page, including ones in the browser's history, still shows it.
  refresh();
  redirect("/workspace/assessments?deleted=1");
}

function saveFailure(error: PostgrestError): SaveDraftResult {
  if (isCourseError(error)) return { fieldErrors: { courseId: "Choose one of your courses." } };
  console.error("Could not save assessment draft:", error);
  return { error: "Your draft couldn't be saved. Please try again." };
}

function deleteFailure(error: { message: string }) {
  console.error("Could not delete assessment:", error);
  return { error: "The assessment couldn't be deleted. Please try again." };
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
