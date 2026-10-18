"""Structured shapes the reasoning model returns.

These models are sent to the OpenAI Responses API as Structured Output schemas,
so the model's reply always has exactly this shape. Strict schemas need every
field to be present, so "optional" fields are nullable (``X | None``) rather
than given defaults. Business rules the schema can't express (percentages that
add up to 100, an answer key that matches a choice, points that add up) are
checked in code before anything is saved.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field

QuestionType = Literal["mcq", "short_answer", "long_answer", "problem"]
Difficulty = Literal["easy", "medium", "hard"]
# Where a setting came from, so the professor can see what they decided and what
# the AI added.
Provenance = Literal["professor", "ai_inferred", "ai_assumption"]
# Bloom's taxonomy: the thinking a question asks for. Independent of difficulty
# (an Analyze question can be medium; an Apply question can be hard).
CognitiveLevel = Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]
# How the professor chose the cognitive mix: ProfPilot recommends it (automatic),
# a preset, or their own percentages (custom). See app/domain/setup.py.
LearningFocus = Literal["automatic", "balanced", "practical", "critical", "custom"]

QUESTION_TYPES: tuple[QuestionType, ...] = ("mcq", "short_answer", "long_answer", "problem")
DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "medium", "hard")
COGNITIVE_LEVELS: tuple[CognitiveLevel, ...] = ("remember", "understand", "apply", "analyze", "evaluate", "create")

# Plans saved before Bloom's own names were used have these labels.
_LEGACY_LEVELS = {
    "recall": "remember",
    "understanding": "understand",
    "application": "apply",
    "analysis": "analyze",
    "evaluation": "evaluate",
    "creation": "create",
}
PlannedLevel = Annotated[CognitiveLevel, BeforeValidator(lambda value: _LEGACY_LEVELS.get(value, value))]
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


class BloomSpec(BaseModel):
    """Share of the assessment's marks at each Bloom's level, and the learning focus it follows."""

    focus: LearningFocus
    remember_percent: int = Field(ge=0, le=100)
    understand_percent: int = Field(ge=0, le=100)
    apply_percent: int = Field(ge=0, le=100)
    analyze_percent: int = Field(ge=0, le=100)
    evaluate_percent: int = Field(ge=0, le=100)
    create_percent: int = Field(ge=0, le=100)
    source: Provenance

    def shares(self) -> dict[str, int]:
        return {level: getattr(self, f"{level}_percent") for level in COGNITIVE_LEVELS}


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
    # The cognitive mix by marks. (The default keeps specs saved before this
    # field existed valid; the API still requires the field.)
    bloom: BloomSpec | None = None
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


# =============================================================================
# Lecture Knowledge Profile (built once per course-material document)
# =============================================================================
#
# The model analyses a lecture one section at a time (SegmentKnowledge), then
# relates the merged topics (LectureSynthesis). Everything is assembled, checked
# and stored by app/services/lecture_knowledge.py. `locations` are always the
# labels the section was given ("Slide 12", "Pages 3–4"): never invented.

# Where an item comes from: the lecture itself, or ProfPilot's own pedagogical suggestion.
KnowledgeOrigin = Literal["lecture", "ai_suggestion"]


class KnowledgeConcept(BaseModel):
    name: str
    kind: Literal[
        "definition", "term", "method", "algorithm", "principle", "theorem", "property",
        "classification", "assumption", "limitation", "exception", "observation", "other",
    ]  # fmt: skip
    # Faithful to the lecture, including the conditions under which it holds.
    description: str
    locations: list[str]


class KnowledgeFormula(BaseModel):
    name: str
    # As written in the lecture (LaTeX where possible); never "corrected".
    expression: str
    # e.g. "η: learning rate"
    variables: list[str]
    conditions: str | None
    locations: list[str]
    # True when the extracted text may have garbled it (e.g. a broken PDF text layer).
    uncertain: bool
    # For an uncertain formula: its standard form (LaTeX) only when the lecture's intent is
    # unambiguous; null otherwise. Never replaces `expression`, the evidence, and stays
    # unverified until the professor confirms it. (Profiles built before this field lack it.)
    normalized: str | None = None


class KnowledgeExample(BaseModel):
    title: str
    kind: Literal["worked_example", "application", "case_study", "calculation", "comparison", "demonstration", "dataset"]
    description: str
    locations: list[str]


class KnowledgeVisual(BaseModel):
    """A figure, table or diagram the text refers to or contains."""

    description: str
    locations: list[str]
    # False when the text only hints at it (its content needs the original page).
    interpretable: bool


class KnowledgeMisconception(BaseModel):
    description: str
    origin: KnowledgeOrigin
    locations: list[str]


class BloomOpportunity(BaseModel):
    """What a question at this level could ask, supported by the lecture. Not a quota."""

    level: CognitiveLevel
    idea: str


class KnowledgeTopic(BaseModel):
    name: str
    section: str | None
    summary: str
    locations: list[str]
    concepts: list[KnowledgeConcept]
    formulas: list[KnowledgeFormula]
    examples: list[KnowledgeExample]
    visuals: list[KnowledgeVisual]
    misconceptions: list[KnowledgeMisconception]
    bloom_opportunities: list[BloomOpportunity]


class SegmentKnowledge(BaseModel):
    """One section of a lecture, analysed in full."""

    topics: list[KnowledgeTopic]
    warnings: list[str]


class KnowledgeRelation(BaseModel):
    source_topic: str
    target_topic: str
    kind: Literal["prerequisite", "builds_on", "contrasts_with", "generalizes", "causes", "trade_off", "applies_to", "related"]
    note: str


class LectureSynthesis(BaseModel):
    """The whole lecture, from its merged topic inventory."""

    title: str
    overview: str
    sections: list[str]
    learning_objectives: list[str]
    relations: list[KnowledgeRelation]


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
    cognitive_level: PlannedLevel
    # Which uploaded material it should draw on (file name and pages/slides).
    source_hint: str | None
    estimated_minutes: float = Field(gt=0, le=600)
    # id of an uploaded image attachment the question should show, if any.
    figure_document_id: str | None
    # Lecture-knowledge topic ids it draws on (e.g. "L2.t5"), from the LECTURE KNOWLEDGE
    # inventory. (The default keeps plans saved before this field existed valid.)
    knowledge_refs: list[str] = Field(default_factory=list)


class ExamPlan(BaseModel):
    sections: list[PlannedSection]
    questions: list[PlannedQuestion]
    rationale: str
    coverage_notes: str
    warnings: list[str]
    # The order the professor explicitly asked for ("easy questions first",
    # "lecture by lecture"), in brief; null when they didn't ask for one, and the
    # questions are then mixed by difficulty and topic. (The default keeps plans
    # saved before this field existed valid; the API still requires the field.)
    ordering_request: str | None = None


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
    # The Bloom's level the question genuinely assesses (null only for questions
    # stored before levels were recorded).
    cognitive_level: CognitiveLevel | None
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
    "cognitive_level",
    "other",
]


class QuestionFix(BaseModel):
    """A small, safe repair the reviewer proposes. Applied only when it passes validation."""

    kind: Literal["correct_answer_key", "clarify_wording", "fix_solution", "fix_rubric", "relabel_cognitive_level"]
    # relabel_cognitive_level: the level the question actually assesses.
    cognitive_level: CognitiveLevel | None
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
