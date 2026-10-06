"""Request and response bodies of the AI API (JSON in camelCase)."""

from typing import Any, Literal

from pydantic import Field

from app.domain.setup import AssessmentSetup
from app.schemas.common import ApiModel

# -----------------------------------------------------------------------------
# Jobs
# -----------------------------------------------------------------------------


class RunOut(ApiModel):
    id: str
    assessment_id: str
    exam_id: str | None
    kind: str
    status: Literal["running", "succeeded", "failed"]
    stage: str | None
    error_message: str | None
    started_at: str | None
    finished_at: str | None


class StartedRunOut(ApiModel):
    run: RunOut
    exam_id: str | None = None


# -----------------------------------------------------------------------------
# Assessment AI status and documents
# -----------------------------------------------------------------------------


class DocumentStatusOut(ApiModel):
    id: str
    name: str
    category: str
    status: Literal["pending", "processing", "ready", "failed"]
    error: str | None
    page_count: int | None
    title: str | None


class StyleStatusOut(ApiModel):
    exams_analyzed: int
    summary: str


class ExamStatusOut(ApiModel):
    id: str
    mode: str
    status: str
    review_status: str
    question_count: int
    finalized_at: str | None


class SpecStatusOut(ApiModel):
    status: Literal["none", "draft", "approved", "stale"]
    generated_at: str | None
    approved_at: str | None
    stale_reasons: list[str]


class AiStatusOut(ApiModel):
    assessment_id: str
    spec: SpecStatusOut
    documents: list[DocumentStatusOut]
    style_profile: StyleStatusOut | None
    exam: ExamStatusOut | None
    runs: list[RunOut]


class ProcessDocumentsIn(ApiModel):
    retry_failed: bool = False
    document_id: str | None = None


# -----------------------------------------------------------------------------
# Interpretation (ExamSpec)
# -----------------------------------------------------------------------------


class InterpretationOut(ApiModel):
    status: Literal["none", "draft", "approved", "stale"]
    stale_reasons: list[str]
    enhanced_prompt: str | None
    spec: dict[str, Any] | None
    inputs: dict[str, Any] | None
    generated_at: str | None
    approved_at: str | None
    model: str | None


class InterpretationEditIn(ApiModel):
    enhanced_prompt: str = Field(min_length=1, max_length=8000)


# -----------------------------------------------------------------------------
# Exams and questions
# -----------------------------------------------------------------------------


class CreateExamIn(ApiModel):
    mode: Literal["full", "interactive"]
    # Replace an existing exam (its questions are deleted).
    replace: bool = False
    # Continue a full generation that stopped halfway.
    resume: bool = False


class QuestionOut(ApiModel):
    id: str
    exam_id: str
    version_id: str
    section_id: str | None
    slot_id: str
    number: int
    position: int
    type: str
    difficulty: str
    points: float
    prompt: str
    choices: list[dict[str, Any]] | None
    correct_choice: str | None
    subparts: list[dict[str, Any]] | None
    answer: str | None
    solution: str | None
    rubric: list[dict[str, Any]] | None
    explanation: str | None
    concepts: list[str]
    learning_objectives: list[str]
    sources: list[str]
    figure_document_id: str | None
    estimated_minutes: float | None
    status: str
    needs_solution_review: bool
    revision_count: int
    updated_at: str


class VersionOut(ApiModel):
    id: str
    label: str
    position: int
    summary: dict[str, Any]
    warnings: list[dict[str, Any]]


class SectionOut(ApiModel):
    id: str
    position: int
    title: str
    instructions: str | None


class ExamOut(ApiModel):
    id: str
    assessment_id: str
    mode: str
    status: str
    error_message: str | None
    review_status: str
    review: dict[str, Any] | None
    reviewed_at: str | None
    finalized_at: str | None
    created_at: str
    updated_at: str
    spec: dict[str, Any] | None
    planned_question_count: int
    versions: list[VersionOut]
    sections: list[SectionOut]
    questions: list[QuestionOut]


class CreateExamOut(ApiModel):
    exam: ExamOut | None = None
    run: RunOut | None = None


class QuestionResultOut(ApiModel):
    question: QuestionOut
    change_summary: str | None = None
    notes: str | None = None
    distribution: dict[str, Any] | None = None
    distribution_warnings: list[dict[str, Any]] = []


class ReviseQuestionIn(ApiModel):
    instruction: str | None = Field(default=None, max_length=2000)
    preset: Literal["harder", "easier", "application", "clearer", "shorter", "alternative", "replace"] | None = None


class ApprovalIn(ApiModel):
    approved: bool


class RevisionOut(ApiModel):
    id: str
    revision_number: int
    source: str
    instruction: str | None
    created_at: str
    snapshot: dict[str, Any]


class VariantsOut(ApiModel):
    questions: list[QuestionOut]
    skipped_versions: list[str]


class ReorderIn(ApiModel):
    question_ids: list[str] = Field(min_length=1, max_length=300)


class ReadinessCheckOut(ApiModel):
    id: str
    status: Literal["ok", "info", "warning", "serious", "blocked"]
    label: str
    detail: str | None


class ReadinessOut(ApiModel):
    can_export: bool
    requires_confirmation: bool
    checks: list[ReadinessCheckOut]
    versions: list[dict[str, Any]]
    duration_minutes: int | None


class FinalizeOut(ApiModel):
    finalized_at: str


# -----------------------------------------------------------------------------
# Conversations and voice
# -----------------------------------------------------------------------------


class MessageOut(ApiModel):
    id: str
    role: Literal["professor", "assistant"]
    channel: Literal["text", "voice"]
    content: str
    created_at: str


class BuilderMessageIn(ApiModel):
    content: str = Field(min_length=1, max_length=4000)


class BuilderChatOut(ApiModel):
    reply: str
    changed_question_ids: list[str]
    run_id: str | None


class TranscriptIn(ApiModel):
    role: Literal["professor", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ToolCallIn(ApiModel):
    arguments: dict[str, Any] = {}


class RealtimeSecretIn(ApiModel):
    purpose: Literal["setup", "builder"]
    assessment_id: str | None = None
    exam_id: str | None = None
    setup: AssessmentSetup | None = None
    # Setup settings already answered (set or skipped) in this conversation.
    addressed: list[str] = Field(default=[], max_length=20)


class RealtimeSecretOut(ApiModel):
    client_secret: str
    expires_at: int
    model: str


class SetupToolIn(ApiModel):
    setup: AssessmentSetup
    arguments: dict[str, Any] = {}
    # Settings already answered (set or skipped) in this conversation.
    addressed: list[str] = Field(default=[], max_length=20)


class SetupToolOut(ApiModel):
    setup: AssessmentSetup
    result: dict[str, Any]


class ChatMessageIn(ApiModel):
    role: Literal["professor", "assistant"]
    content: str = Field(max_length=4000)


class SetupChatIn(ApiModel):
    setup: AssessmentSetup
    messages: list[ChatMessageIn] = Field(min_length=1, max_length=40)
    addressed: list[str] = Field(default=[], max_length=20)


class SetupChatOut(ApiModel):
    reply: str
    setup: AssessmentSetup
    changed_fields: list[str]
    finished: bool
    addressed: list[str]


# -----------------------------------------------------------------------------
# Preferences
# -----------------------------------------------------------------------------


class PreferencesOut(ApiModel):
    explicit_notes: str
    learning_enabled: bool
    learned: dict[str, Any]
    updated_at: str | None


class PreferencesIn(ApiModel):
    explicit_notes: str | None = Field(default=None, max_length=2000)
    learning_enabled: bool | None = None
