"use server";

import { redirect } from "next/navigation";

import {
  isAssessmentDraft,
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

/** Saves the Create assessment form as a draft exam project, then opens the Assessments page. */
export async function saveAssessmentDraft(draft: AssessmentDraft): Promise<SaveDraftResult> {
  await getCurrentProfessor();

  // The browser can send anything, so check the shape and the rules again here.
  if (!isAssessmentDraft(draft)) {
    return { error: "Something went wrong. Please reload the page and try again." };
  }
  const fieldErrors = validateAssessmentDraft(draft);
  if (Object.keys(fieldErrors).length > 0) return { fieldErrors };

  const supabase = await createClient();
  // professor_id defaults to the signed-in professor and status to 'draft'.
  const { error } = await supabase.from("exam_projects").insert({
    course_id: draft.courseId,
    exam_name: draft.examName.trim(),
    duration_minutes: draft.durationMinutes,
    mcq_percentage: draft.mcqPercentage,
    subjective_percentage: draft.subjectivePercentage,
    number_of_versions: draft.numberOfVersions,
    difficulty: draft.difficulty,
    additional_notes: textOrNull(draft.additionalNotes),
    professor_prompt: textOrNull(draft.professorPrompt),
  });

  if (error) {
    // Row Level Security (42501) or the foreign key (23503) rejects a course
    // that doesn't exist or belongs to another professor.
    if (error.code === "42501" || error.code === "23503") {
      return { fieldErrors: { courseId: "Choose one of your courses." } };
    }
    console.error("Could not save assessment draft:", error);
    return { error: "Your draft couldn't be saved. Please try again." };
  }

  redirect("/workspace/assessments?saved=1");
}

/** Keeps the text exactly as the professor wrote it, or stores nothing if it is blank. */
function textOrNull(text: string) {
  return text.trim() ? text : null;
}
