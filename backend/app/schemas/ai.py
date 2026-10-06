"""Structured shapes the reasoning model returns.

These models are sent to the OpenAI Responses API as Structured Output schemas,
so the model's reply always has exactly this shape. Strict schemas need every
field to be present, so "optional" fields are nullable (``X | None``) rather
than given defaults. Business rules the schema can't express (percentages that
add up to 100, an answer key that matches a choice, points that add up) are
checked in code before anything is saved.
"""

from typing import Literal

from pydantic import BaseModel, Field

QuestionType = Literal["mcq", "short_answer", "long_answer", "problem"]
Difficulty = Literal["easy", "medium", "hard"]
# Where a setting came from, so the professor can see what they decided and what
# the AI added.
Provenance = Literal["professor", "ai_inferred", "ai_assumption"]
CognitiveLevel = Literal["recall", "understanding", "application", "analysis", "evaluation", "creation"]

QUESTION_TYPES: tuple[QuestionType, ...] = ("mcq", "short_answer", "long_answer", "problem")
DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "medium", "hard")
QUESTION_TYPE_LABELS: dict[str, str] = {
    "mcq": "Multiple choice",
    "short_answer": "Short answer",
    "long_answer": "Long answer",
    "problem": "Problem",
}


# =============================================================================
# ExamSpec (the Prompt Interpreter's output)
# =============================================================================


class SpecItem(BaseModel):
    """One instruction, preference or constraint, and where it came from."""

    text: str
    source: Provenance


class FormatSpec(BaseModel):
    """Share of the assessment's marks in multiple-choice vs subjective questions."""

    mcq_percent: int = Field(ge=0, le=100)
    subjective_percent: int = Field(ge=0, le=100)
    source: Provenance


class DifficultySpec(BaseModel):
    """Share of the assessment's marks at each difficulty."""

    easy_percent: int = Field(ge=0, le=100)
    medium_percent: int = Field(ge=0, le=100)
    hard_percent: int = Field(ge=0, le=100)
    source: Provenance


class CoverageTopic(BaseModel):
    topic: str
    emphasis: Literal["high", "normal", "low"]
    # Names of the uploaded files (and pages or slides) this topic comes from.
    source_documents: list[str]
    source: Provenance


class SectionSpec(BaseModel):
    title: str
    question_types: list[QuestionType]
    # Share of the total marks, when it matters.
    weight_percent: int | None = Field(ge=0, le=100)
    notes: str | None


class ExamSpec(BaseModel):
    # Identity. Null when the professor didn't give it and it can't be inferred.
    assessment_title: str | None
    course: str | None
    assessment_type: str | None  # e.g. "Midterm exam", "Quiz"
    # Format
    duration_minutes: int | None = Field(ge=1, le=1440)
    duration_source: Provenance | None
    versions: int = Field(ge=1, le=20)
    total_points: int | None = Field(ge=1, le=1000)
    total_points_source: Provenance | None
    question_count_min: int | None = Field(ge=1, le=200)
    question_count_max: int | None = Field(ge=1, le=200)
    question_count_source: Provenance | None
    question_format: FormatSpec | None
    difficulty: DifficultySpec | None
    sections: list[SectionSpec]
    # Content
    coverage: list[CoverageTopic]
    learning_emphasis: list[SpecItem]
    question_style: list[SpecItem]
    professor_notes: list[SpecItem]
    professor_preferences_applied: list[SpecItem]
    source_material_guidance: str | None
    previous_exam_style_guidance: str | None
    constraints: list[SpecItem]
    # Transparency
    assumptions: list[str]
    warnings: list[str]
    generation_instructions: list[str]


class InterpretationOutput(BaseModel):
    """What the Prompt Interpreter returns."""

    enhanced_prompt: str
    exam_spec: ExamSpec


# =============================================================================
# Documents and previous-exam style
# =============================================================================


class DocumentTopic(BaseModel):
    name: str
    # e.g. "Slides 4–9" or "Pages 2–3"; null if unclear.
    location: str | None


class DocumentSummary(BaseModel):
    """A short overview of one uploaded file, made once when it is processed."""

    title: str
    summary: str
    topics: list[DocumentTopic]
    # True when the file has little real content (e.g. a cover page only).
    low_content: bool


class StyleTrait(BaseModel):
    trait: str
    # Short evidence from the previous exams (e.g. "Q3 and Q5 use scenarios").
    evidence: str


class StyleProfile(BaseModel):
    """How the professor's previous exams look. Only traits the exams support."""

    exams_analyzed: int
    common_question_types: list[str]
    typical_question_length: str | None
    difficulty_characteristics: str | None
    conceptual_vs_computational: str | None
    recall_vs_application: str | None
    wording_style: str | None
    use_of_scenarios: str | None
    use_of_subquestions: str | None
    section_structure: str | None
    grading_patterns: str | None
    recurring_preferences: list[StyleTrait]
    limitations: list[str]
    summary: str


# =============================================================================
# Exam plan
# =============================================================================


class PlannedSection(BaseModel):
    title: str
    instructions: str | None


class PlannedQuestion(BaseModel):
    number: int = Field(ge=1)
    section_index: int = Field(ge=0)
    type: QuestionType
    difficulty: Difficulty
    points: float = Field(gt=0, le=1000)
    topic: str
    learning_objective: str
    cognitive_level: CognitiveLevel
    # Which uploaded material it should draw on (file name and pages/slides).
    source_hint: str | None
    estimated_minutes: float = Field(gt=0, le=600)
    # id of an uploaded image attachment the question should show, if any.
    figure_document_id: str | None


class ExamPlan(BaseModel):
    sections: list[PlannedSection]
    questions: list[PlannedQuestion]
    rationale: str
    coverage_notes: str
    warnings: list[str]


# =============================================================================
# Questions
# =============================================================================


class ChoiceOut(BaseModel):
    id: str = Field(pattern="^[A-H]$")
    text: str


class RubricItem(BaseModel):
    criterion: str
    points: float = Field(ge=0, le=1000)


class SubpartOut(BaseModel):
    label: str
    prompt: str
    points: float = Field(gt=0, le=1000)
    answer: str
    rubric: list[RubricItem]


class QuestionContent(BaseModel):
    """A complete question with its answer key, as the model writes it."""

    type: QuestionType
    difficulty: Difficulty
    points: float = Field(gt=0, le=1000)
    prompt: str
    choices: list[ChoiceOut] | None
    correct_choice: str | None
    subparts: list[SubpartOut] | None
    # The final answer: for multiple choice, the correct option and why.
    answer: str
    # A worked solution or model answer a marker can follow.
    solution: str
    rubric: list[RubricItem] | None
    # For multiple choice: why each distractor is wrong.
    explanation: str | None
    concepts: list[str]
    learning_objectives: list[str]
    # ids of the course-material excerpts it is based on (from the prompt).
    source_excerpt_ids: list[str]
    estimated_minutes: float = Field(gt=0, le=600)
    figure_document_id: str | None


class GeneratedQuestion(QuestionContent):
    number: int = Field(ge=1)


class QuestionBatch(BaseModel):
    questions: list[GeneratedQuestion]
    warnings: list[str]


class RevisedQuestion(BaseModel):
    question: QuestionContent
    change_summary: str


class SolutionKey(BaseModel):
    """A regenerated answer key for a question whose text the professor changed."""

    correct_choice: str | None
    subpart_answers: list[str] | None
    answer: str
    solution: str
    rubric: list[RubricItem] | None
    explanation: str | None
    notes: str | None


class VersionVariant(BaseModel):
    """An equivalent question for another version of the exam."""

    source_number: int = Field(ge=1)
    question: QuestionContent
    variation: str


class VariantBatch(BaseModel):
    variants: list[VersionVariant]


# =============================================================================
# Quality review
# =============================================================================

IssueCategory = Literal[
    "coverage",
    "duplication",
    "ambiguity",
    "answer_key",
    "impossible",
    "missing_information",
    "answerability",
    "difficulty",
    "format",
    "duration",
    "clarity",
    "version_consistency",
    "solution",
    "rubric",
    "other",
]


class QuestionFix(BaseModel):
    """A small, safe repair the reviewer proposes. Applied only when it passes validation."""

    kind: Literal["correct_answer_key", "clarify_wording", "fix_solution", "fix_rubric"]
    correct_choice: str | None
    prompt: str | None
    answer: str | None
    solution: str | None
    rubric: list[RubricItem] | None


class ReviewIssue(BaseModel):
    question_number: int | None
    version_label: str | None
    severity: Literal["info", "warning", "critical"]
    category: IssueCategory
    message: str
    fix: QuestionFix | None


class ReviewOutput(BaseModel):
    summary: str
    issues: list[ReviewIssue]
    estimated_total_minutes: float | None
