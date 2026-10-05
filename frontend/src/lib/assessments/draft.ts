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

/** The split a difficulty distribution starts with when the professor switches it on. */
export const DEFAULT_DIFFICULTY_DISTRIBUTION: Record<Difficulty, number> = {
  easy: 30,
  medium: 40,
  hard: 30,
};

export const LIMITS = {
  examName: 120,
  durationMinutes: 1440, // 24 hours
  versions: 20,
  additionalNotes: 2000,
  professorPrompt: 5000,
};

/** Shown in place of a missing assessment name. Never saved: an unnamed assessment's exam_name is null. */
export const UNTITLED_ASSESSMENT = "Untitled assessment";

/** The name to show for an assessment, which is optional. */
export function assessmentTitle(examName: string | null) {
  return examName?.trim() || UNTITLED_ASSESSMENT;
}

/**
 * An assessment draft, shaped like the exam_projects columns it is saved to.
 * Every setting is optional except the number of versions.
 */
export type AssessmentDraft = {
  /** null when no course is chosen. */
  courseId: string | null;
  /** As typed. A blank name is saved as null. */
  examName: string;
  /** null when no duration is specified. */
  durationMinutes: number | null;
  /** Both null when the distribution is not specified; otherwise they add up to 100. */
  mcqPercentage: number | null;
  subjectivePercentage: number | null;
  numberOfVersions: number;
  /** All three null when the difficulty distribution is not specified; otherwise they add up to 100. */
  easyPercentage: number | null;
  mediumPercentage: number | null;
  hardPercentage: number | null;
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
    | "difficultyDistribution"
    | "additionalNotes"
    | "professorPrompt",
    string
  >
>;

/** Returns an error message for each invalid field; an empty object means the draft is valid. */
export function validateAssessmentDraft(draft: AssessmentDraft): AssessmentDraftErrors {
  const errors: AssessmentDraftErrors = {};

  if (draft.examName.trim().length > LIMITS.examName) {
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

  const difficultyError = checkDifficultyDistribution(draft);
  if (difficultyError) errors.difficultyDistribution = difficultyError;

  if (draft.additionalNotes.length > LIMITS.additionalNotes) {
    errors.additionalNotes = `Use ${LIMITS.additionalNotes.toLocaleString("en")} characters or fewer.`;
  }
  if (draft.professorPrompt.length > LIMITS.professorPrompt) {
    errors.professorPrompt = `Use ${LIMITS.professorPrompt.toLocaleString("en")} characters or fewer.`;
  }

  return errors;
}

export type DifficultyPercentages = Pick<
  AssessmentDraft,
  "easyPercentage" | "mediumPercentage" | "hardPercentage"
>;

/**
 * What's wrong with a difficulty distribution, if anything. Valid means not
 * specified at all (all three null), or three whole numbers from 0 to 100 that
 * add up to 100. The database enforces the same rule.
 */
export function checkDifficultyDistribution(draft: DifficultyPercentages): string | undefined {
  const percentages = [draft.easyPercentage, draft.mediumPercentage, draft.hardPercentage];
  if (percentages.every((percentage) => percentage === null)) return undefined;
  if (!percentages.every((percentage) => isWholeNumberBetween(percentage, 0, 100))) {
    return "Enter a whole number from 0 to 100 for each difficulty.";
  }
  if (sum(percentages) !== 100) return "Difficulty percentages must total 100%.";
  return undefined;
}

/**
 * Readable text for a draft's settings, as summaries show them. A setting is
 * undefined when it isn't specified (or isn't a valid number yet while typing).
 */
export function describeSettings(draft: AssessmentDraft) {
  const { durationMinutes: minutes, mcqPercentage: mcq, subjectivePercentage: subjective } = draft;
  const { easyPercentage: easy, mediumPercentage: medium, hardPercentage: hard } = draft;
  const versions = draft.numberOfVersions;
  return {
    duration: isWholeNumberBetween(minutes, 1, Infinity)
      ? `${minutes} ${minutes === 1 ? "minute" : "minutes"}`
      : undefined,
    distribution:
      mcq !== null && subjective !== null ? `${mcq}% MCQ / ${subjective}% Subjective` : undefined,
    versions: isWholeNumberBetween(versions, 1, Infinity) ? `${versions}` : undefined,
    difficulty: [easy, medium, hard].every((percentage) => isWholeNumberBetween(percentage, 0, 100))
      ? `${easy}% Easy / ${medium}% Medium / ${hard}% Hard`
      : undefined,
  };
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
    isNumberOrNull(value.easyPercentage) &&
    isNumberOrNull(value.mediumPercentage) &&
    isNumberOrNull(value.hardPercentage) &&
    typeof value.additionalNotes === "string" &&
    typeof value.professorPrompt === "string"
  );
}

function isWholeNumberBetween(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= min && value <= max;
}

function sum(numbers: (number | null)[]) {
  return numbers.reduce<number>((total, number) => total + (number ?? 0), 0);
}
