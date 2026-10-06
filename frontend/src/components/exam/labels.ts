import type { QuestionDifficulty, QuestionType, Revision } from "@/lib/api/types";

export const TYPE_LABELS: Record<QuestionType, string> = {
  mcq: "Multiple choice",
  short_answer: "Short answer",
  long_answer: "Long answer",
  problem: "Problem",
};

export const DIFFICULTY_LABELS: Record<QuestionDifficulty, string> = {
  easy: "Easy",
  medium: "Medium",
  hard: "Hard",
};

export const REVISION_SOURCES: Record<Revision["source"], string> = {
  ai_revision: "Changed with AI",
  manual_edit: "Edited by you",
  ai_review_fix: "Fixed by the quality check",
  solution_regenerated: "Answer key regenerated",
  restore: "Earlier version restored",
};

export function marks(points: number) {
  return `${points} ${points === 1 ? "mark" : "marks"}`;
}

/** Quick AI actions for one question, in menu order. */
export const QUICK_ACTIONS = [
  { preset: "harder", label: "Make harder" },
  { preset: "easier", label: "Make easier" },
  { preset: "application", label: "More application-based" },
  { preset: "clearer", label: "Make clearer" },
  { preset: "shorter", label: "Shorten" },
  { preset: "alternative", label: "Generate alternative" },
  { preset: "replace", label: "Replace question" },
] as const;

export type QuickAction = (typeof QUICK_ACTIONS)[number]["preset"];

const REVISION_STATUS: Record<string, (question: string) => string> = {
  harder: (question) => `Making ${question} harder…`,
  easier: (question) => `Making ${question} easier…`,
  application: (question) => `Making ${question} more application-based…`,
  clearer: (question) => `Making ${question} clearer…`,
  shorter: (question) => `Shortening ${question}…`,
  alternative: (question) => `Writing an alternative to ${question}…`,
  replace: (question) => `Replacing ${question}…`,
};

/**
 * What Build with AI shows while one of its tools runs, e.g. "Generating the
 * next question…" or "Making Question 4 harder…". Null for tools that are
 * instant (reading the exam state).
 */
export function builderToolStatus(name: string, args: Record<string, unknown>): string | null {
  const number = typeof args.question_number === "number" ? args.question_number : null;
  const question = number ? `Question ${number}` : "the question";
  switch (name) {
    case "generate_next_question":
      return "Generating the next question…";
    case "revise_question": {
      const preset = typeof args.quick_action === "string" ? args.quick_action : "";
      return REVISION_STATUS[preset]?.(question) ?? `Revising ${question}…`;
    }
    case "regenerate_solution":
      return number ? `Updating the answer key for Question ${number}…` : "Updating the answer key…";
    case "review_question":
      return `Reviewing ${question}…`;
    case "approve_question":
      return `Approving ${question}…`;
    case "unlock_question":
      return `Unlocking ${question}…`;
    case "delete_question":
      return `Deleting ${question}…`;
    case "move_question":
      return `Moving ${question}…`;
    case "finish_exam":
      return "Finishing the exam…";
    default:
      return null;
  }
}

/** The same, once the tool has failed: what couldn't be done. */
export function builderToolFailure(name: string): string {
  switch (name) {
    case "generate_next_question":
      return "The next question couldn't be generated.";
    case "revise_question":
      return "The question couldn't be changed.";
    case "regenerate_solution":
      return "The answer key couldn't be updated.";
    case "review_question":
      return "The question couldn't be reviewed.";
    default:
      return "That couldn't be done.";
  }
}
