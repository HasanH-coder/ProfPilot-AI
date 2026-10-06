// Shapes the AI API returns (see backend/app/schemas/api.py).

import type { AssessmentDraft } from "@/lib/assessments/draft";

export type RunStatus = "running" | "succeeded" | "failed";

export type Run = {
  id: string;
  assessmentId: string;
  examId: string | null;
  kind: "document_processing" | "interpretation" | "generation" | "review" | "versioning";
  status: RunStatus;
  stage: string | null;
  errorMessage: string | null;
  startedAt: string | null;
  finishedAt: string | null;
};

export type StartedRun = { run: Run; examId?: string | null };

export type SpecStatus = "none" | "draft" | "approved" | "stale";
export type ProcessingStatus = "pending" | "processing" | "ready" | "failed";

export type AiStatus = {
  assessmentId: string;
  spec: { status: SpecStatus; generatedAt: string | null; approvedAt: string | null; staleReasons: string[] };
  documents: {
    id: string;
    name: string;
    category: string;
    status: ProcessingStatus;
    error: string | null;
    pageCount: number | null;
    title: string | null;
  }[];
  styleProfile: { examsAnalyzed: number; summary: string } | null;
  exam: {
    id: string;
    mode: "full" | "interactive";
    status: ExamStatus;
    reviewStatus: ReviewStatus;
    questionCount: number;
    finalizedAt: string | null;
  } | null;
  runs: Run[];
};

// -- ExamSpec (snake_case: it is the model's structured output, stored as is) --

export type Provenance = "professor" | "ai_inferred" | "ai_assumption";
export type SpecItem = { text: string; source: Provenance };

export type ExamSpec = {
  assessment_title: string | null;
  course: string | null;
  assessment_type: string | null;
  duration_minutes: number | null;
  duration_source: Provenance | null;
  versions: number;
  total_points: number | null;
  total_points_source: Provenance | null;
  question_count_min: number | null;
  question_count_max: number | null;
  question_count_source: Provenance | null;
  question_format: { mcq_percent: number; subjective_percent: number; source: Provenance } | null;
  difficulty: { easy_percent: number; medium_percent: number; hard_percent: number; source: Provenance } | null;
  sections: { title: string; question_types: string[]; weight_percent: number | null; notes: string | null }[];
  coverage: { topic: string; emphasis: "high" | "normal" | "low"; source_documents: string[]; source: Provenance }[];
  learning_emphasis: SpecItem[];
  question_style: SpecItem[];
  professor_notes: SpecItem[];
  professor_preferences_applied: SpecItem[];
  source_material_guidance: string | null;
  previous_exam_style_guidance: string | null;
  constraints: SpecItem[];
  assumptions: string[];
  warnings: string[];
  generation_instructions: string[];
};

export type Interpretation = {
  status: SpecStatus;
  staleReasons: string[];
  enhancedPrompt: string | null;
  spec: ExamSpec | null;
  inputs: {
    professor_prompt?: string;
    additional_notes?: string;
    course?: string | null;
    documents?: { id: string; name: string; category: string }[];
    failed_documents?: string[];
    style_profile_used?: boolean;
    preferences_used?: boolean;
    excerpts?: { ref: string; label: string }[];
  } | null;
  generatedAt: string | null;
  approvedAt: string | null;
  model: string | null;
};

// -- Exams ------------------------------------------------------------------------

export type ExamStatus = "generating" | "building" | "ready" | "failed";
export type ReviewStatus = "not_run" | "running" | "passed" | "needs_attention" | "failed";
export type QuestionType = "mcq" | "short_answer" | "long_answer" | "problem";
export type QuestionDifficulty = "easy" | "medium" | "hard";

export type Choice = { id: string; text: string };
export type RubricItem = { criterion: string; points: number };
export type Subpart = { label: string; prompt: string; points: number; answer: string; rubric: RubricItem[] };

export type Question = {
  id: string;
  examId: string;
  versionId: string;
  sectionId: string | null;
  slotId: string;
  number: number;
  position: number;
  type: QuestionType;
  difficulty: QuestionDifficulty;
  points: number;
  prompt: string;
  choices: Choice[] | null;
  correctChoice: string | null;
  subparts: Subpart[] | null;
  answer: string | null;
  solution: string | null;
  rubric: RubricItem[] | null;
  explanation: string | null;
  concepts: string[];
  learningObjectives: string[];
  sources: string[];
  figureDocumentId: string | null;
  estimatedMinutes: number | null;
  status: "draft" | "approved";
  needsSolutionReview: boolean;
  revisionCount: number;
  updatedAt: string;
};

export type DistributionSummary = {
  questionCount: number;
  totalPoints: number;
  estimatedMinutes: number;
  mcqPercent: number;
  subjectivePercent: number;
  easyPercent: number;
  mediumPercent: number;
  hardPercent: number;
};

export type DistributionWarning = {
  dimension: "format" | "difficulty" | "duration";
  label: string;
  actual: number;
  target: number;
  message: string;
};

export type ExamVersion = {
  id: string;
  label: string;
  position: number;
  summary: DistributionSummary;
  warnings: DistributionWarning[];
};

export type ReviewIssue = {
  id: string;
  severity: "info" | "warning" | "critical";
  category: string;
  message: string;
  questionId: string | null;
  questionNumber: number | null;
  versionLabel: string | null;
  source: "ai" | "check";
  fixApplied: boolean;
};

export type ExamReview = {
  summary: string;
  checkedAt: string;
  issues: ReviewIssue[];
  fixesApplied: number;
  estimatedTotalMinutes: number | null;
};

export type Exam = {
  id: string;
  assessmentId: string;
  mode: "full" | "interactive";
  status: ExamStatus;
  errorMessage: string | null;
  reviewStatus: ReviewStatus;
  review: ExamReview | null;
  reviewedAt: string | null;
  finalizedAt: string | null;
  createdAt: string;
  updatedAt: string;
  spec: ExamSpec | null;
  plannedQuestionCount: number;
  versions: ExamVersion[];
  sections: { id: string; position: number; title: string; instructions: string | null }[];
  questions: Question[];
};

export type QuestionResult = {
  question: Question;
  changeSummary?: string | null;
  notes?: string | null;
  distribution?: DistributionSummary | null;
  distributionWarnings: DistributionWarning[];
};

export type Revision = {
  id: string;
  revisionNumber: number;
  source: "ai_revision" | "manual_edit" | "ai_review_fix" | "solution_regenerated" | "restore";
  instruction: string | null;
  createdAt: string;
  snapshot: Partial<Record<string, unknown>> & { prompt?: string };
};

export type ReadinessCheck = {
  id: string;
  status: "ok" | "info" | "warning" | "serious" | "blocked";
  label: string;
  detail: string | null;
};

export type Readiness = {
  canExport: boolean;
  requiresConfirmation: boolean;
  checks: ReadinessCheck[];
  versions: { label: string; summary: DistributionSummary; warnings: DistributionWarning[] }[];
  durationMinutes: number | null;
};

// -- Conversations --------------------------------------------------------------------

export type BuilderMessage = {
  id: string;
  role: "professor" | "assistant";
  channel: "text" | "voice";
  content: string;
  createdAt: string;
};

export type BuilderChatReply = { reply: string; changedQuestionIds: string[]; runId: string | null };

export type ToolResult = Record<string, unknown> & {
  ok?: boolean;
  error?: string;
  runId?: string;
  changedQuestionIds?: string[];
};

export type SetupToolResult = { setup: AssessmentDraft; result: ToolResult };
export type SetupChatReply = { reply: string; setup: AssessmentDraft; changedFields: string[]; finished: boolean };

export type RealtimeSecret = { clientSecret: string; expiresAt: number; model: string };

export type Preferences = {
  explicitNotes: string;
  learningEnabled: boolean;
  learned: {
    based_on?: number;
    accepted_revisions?: number;
    summary?: string[];
    tendencies?: string[];
  };
  updatedAt: string | null;
};
