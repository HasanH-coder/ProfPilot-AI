// Shapes the AI API returns (see backend/app/schemas/api.py).

import type { AssessmentDraft, BloomDistribution, BloomLevel, LearningFocus } from "@/lib/assessments/draft";

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
  /** Missing in plans made before the learning focus existed. */
  bloom?: ({ focus: LearningFocus; source: Provenance } & Record<`${BloomLevel}_percent`, number>) | null;
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
  /** null for questions written before levels were recorded. */
  cognitiveLevel: BloomLevel | null;
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
  /** Share of marks per Bloom's level; null while any question has no level. */
  bloom: BloomDistribution | null;
};

export type DistributionWarning = {
  dimension: "format" | "difficulty" | "bloom" | "duration";
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

/** One line of a streamed Build with AI chat reply. */
export type BuilderChatEvent =
  | { event: "tool"; name: string; arguments: Record<string, unknown> }
  | ({ event: "done" } & BuilderChatReply)
  | { event: "error"; code: string; message: string };

export type ToolResult = Record<string, unknown> & {
  ok?: boolean;
  error?: string;
  runId?: string;
  changedQuestionIds?: string[];
};

export type SetupToolResult = { setup: AssessmentDraft; result: ToolResult };
export type SetupChatReply = {
  reply: string;
  setup: AssessmentDraft;
  changedFields: string[];
  finished: boolean;
  /** Settings answered (set or skipped) in this conversation. */
  addressed: string[];
  /** The course sources selected after the turn ("sourceIds" is in changedFields when it changed). */
  sourceIds: string[];
};

/** A lecture's reusable knowledge profile, in brief (GET /api/documents/{id}/knowledge). */
export type LectureKnowledge = {
  state: "none" | "building" | "ready" | "outdated" | "failed";
  error: string | null;
  title: string | null;
  overview: string | null;
  sections: string[];
  topics: { id: string; name: string; locations: string[] }[];
  coverage: {
    unit: string | null;
    totalUnits: number | null;
    /** Pages or slides with any extracted text (titles and footers included). */
    analysedUnits: number | null;
    /** Pages with real content; null for files read before page-level detail existed. */
    substantiveUnits?: number | null;
    pageDetail?: boolean;
    /** Page numbers by class: text, textWithFigures, figureDominated, divider, empty, uncertainMath. */
    pages?: Partial<Record<"text" | "textWithFigures" | "figureDominated" | "divider" | "empty" | "uncertainMath", number[]>>;
    unitsWithoutText: number[];
    /** Slides whose pictures or charts aren't in the text (their text was analysed). */
    unitsWithFigures: number[];
    /** Slides whose pictures ProfPilot read when the profile was built (what they show is in the text). */
    unitsWithReadFigures?: number[];
    truncated: boolean;
  } | null;
  warnings: string[];
  usage: { inputTokens: number; outputTokens: number; modelCalls: number } | null;
  /** A rough estimate of a (re)build, shown before it starts. */
  estimate: { modelCalls: number; inputTokens: number; outputTokens: number };
  /** Built from text without page markers: usable, without page-level coverage. */
  legacy?: boolean;
  /** A rebuild running or failed beside the working profile, which stays in use meanwhile. */
  rebuild?: { state: "building" | "failed"; error: string | null } | null;
  /** Whether the stored text has page markers, and whether a (free) re-read can add them. */
  extraction?: { state: "legacy" | "current" | "interrupted" | "not_applicable" | "processing"; rereadable: boolean };
  /** A re-read makes no AI analysis call: only its new text is embedded for search. */
  rereadEstimate?: { embeddingTokens: number };
  /** The pictures on its pages or slides (diagrams, charts, tables, code): which ProfPilot has read. */
  pictures?: {
    state: "reading" | "idle";
    /** A PowerPoint lecture that has been read, so its pictures can be (a PDF's slides are seen as the exam is written). */
    readable: boolean;
    unread: number[];
    read: number[];
    /** Some were read after the lecture knowledge was built: a rebuild includes them. */
    afterBuild: boolean;
    /** Why the last reading failed, if it did. */
    error: string | null;
  };
  /** Being brought up to date by the Sources page: waiting its turn, or under way. */
  preparation?: "queued" | "running" | null;
};

export type RealtimeSecret ={ clientSecret: string; expiresAt: number; model: string };

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
