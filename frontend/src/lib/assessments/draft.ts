// The rules for an assessment draft. They are shared by the Create assessment
// form (for instant feedback) and by the Server Action that saves the draft
// (the check that actually counts, because the browser can't be trusted).

export const DURATION_PRESETS = [60, 90, 120] as const;
export const VERSION_PRESETS = [1, 2, 3, 4] as const;
export const DIFFICULTIES = ["easy", "medium", "hard"] as const;

export type Difficulty = (typeof DIFFICULTIES)[number];

export const DIFFICULTY_LABELS: Record<Difficulty, string> = {
  easy: "Easy",
  medium: "Medium",
  hard: "Hard",
};

export const LIMITS = {
  examName: 120,
  durationMinutes: 1440, // 24 hours
  versions: 20,
  additionalNotes: 2000,
  professorPrompt: 5000,
};

/** An assessment draft, shaped like the exam_projects columns it is saved to. */
export type AssessmentDraft = {
  courseId: string | null;
  examName: string;
  /** null when no duration is specified. */
  durationMinutes: number | null;
  /** Both null when the distribution is not specified; otherwise they add up to 100. */
  mcqPercentage: number | null;
  subjectivePercentage: number | null;
  numberOfVersions: number;
  /** null when no difficulty is specified. */
  difficulty: Difficulty | null;
  additionalNotes: string;
  professorPrompt: string;
};

export type AssessmentDraftErrors = Partial<
  Record<
    | "courseId"
    | "examName"
    | "durationMinutes"
    | "distribution"
    | "numberOfVersions"
    | "difficulty"
    | "additionalNotes"
    | "professorPrompt",
    string
  >
>;

/** Returns an error message for each invalid field; an empty object means the draft is valid. */
export function validateAssessmentDraft(draft: AssessmentDraft): AssessmentDraftErrors {
  const errors: AssessmentDraftErrors = {};

  if (!draft.courseId) errors.courseId = "Choose a course.";

  const examName = draft.examName.trim();
  if (!examName) errors.examName = "Enter an assessment name.";
  else if (examName.length > LIMITS.examName) {
    errors.examName = `Use ${LIMITS.examName} characters or fewer.`;
  }

  if (
    draft.durationMinutes !== null &&
    !isWholeNumberBetween(draft.durationMinutes, 1, LIMITS.durationMinutes)
  ) {
    errors.durationMinutes = `Enter a whole number of minutes, from 1 to ${LIMITS.durationMinutes.toLocaleString("en")}.`;
  }

  const { mcqPercentage: mcq, subjectivePercentage: subjective } = draft;
  const notSpecified = mcq === null && subjective === null;
  const addsUpTo100 =
    isWholeNumberBetween(mcq, 0, 100) &&
    isWholeNumberBetween(subjective, 0, 100) &&
    mcq + subjective === 100;
  if (!notSpecified && !addsUpTo100) {
    errors.distribution = "MCQ and subjective percentages must add up to 100%.";
  }

  if (!isWholeNumberBetween(draft.numberOfVersions, 1, LIMITS.versions)) {
    errors.numberOfVersions = `Enter a whole number of versions, from 1 to ${LIMITS.versions}.`;
  }

  if (draft.difficulty !== null && !DIFFICULTIES.includes(draft.difficulty)) {
    errors.difficulty = "Choose a difficulty, or leave it not specified.";
  }

  if (draft.additionalNotes.length > LIMITS.additionalNotes) {
    errors.additionalNotes = `Use ${LIMITS.additionalNotes.toLocaleString("en")} characters or fewer.`;
  }
  if (draft.professorPrompt.length > LIMITS.professorPrompt) {
    errors.professorPrompt = `Use ${LIMITS.professorPrompt.toLocaleString("en")} characters or fewer.`;
  }

  return errors;
}

/** Checks that untrusted input has the AssessmentDraft shape (TypeScript types don't exist at runtime). */
export function isAssessmentDraft(input: unknown): input is AssessmentDraft {
  if (typeof input !== "object" || input === null) return false;
  const value = input as Record<string, unknown>;
  const isNumberOrNull = (field: unknown) => field === null || typeof field === "number";
  const isStringOrNull = (field: unknown) => field === null || typeof field === "string";

  return (
    isStringOrNull(value.courseId) &&
    typeof value.examName === "string" &&
    isNumberOrNull(value.durationMinutes) &&
    isNumberOrNull(value.mcqPercentage) &&
    isNumberOrNull(value.subjectivePercentage) &&
    typeof value.numberOfVersions === "number" &&
    isStringOrNull(value.difficulty) &&
    typeof value.additionalNotes === "string" &&
    typeof value.professorPrompt === "string"
  );
}

function isWholeNumberBetween(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= min && value <= max;
}
