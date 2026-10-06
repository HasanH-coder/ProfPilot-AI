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
