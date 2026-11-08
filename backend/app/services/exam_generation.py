"""Exam planning and generation: from an approved ExamSpec to structured questions.

The full pipeline is controlled, not one giant prompt:

1. plan every question (type, difficulty, points, topic, time) against the spec;
2. check the plan in code (splits by marks, time, structure, the slides each
   question cites) and repair it once;
3. write the questions with complete answer keys, in parallel batches, each
   batch shown its questions' slides (as images, with their words) and the
   course-material excerpts relevant to its own topics;
4. create the other versions as equivalent variants;
5. run the independent quality review (exam_review.py);
6. mark the exam ready.

Questions are saved as soon as their batch finishes, so if generation stops
halfway the finished questions are kept and generation can resume.
"""

import asyncio
import json
import logging
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Any

import pydantic

from app.ai.openai_service import Effort, OpenAIService, Usage, gather_limited, get_openai_service
from app.ai.prompts import DataFramer, choice_ids, generator_instructions, planner_instructions, variant_instructions
from app.core.config import settings
from app.core.errors import AppError, ConflictError, InvalidInputError
from app.core.timing import timed
from app.db.supabase import Database
from app.domain import distribution
from app.domain.exam_style import StyleNumbers, length_problems, mix_problems, style_numbers
from app.domain.setup import BLOOM_PRESETS
from app.schemas.ai import (
    COGNITIVE_LEVELS,
    QUESTION_TYPES,
    ExamPlan,
    ExamSpec,
    GeneratedQuestion,
    PlannedQuestion,
    PlannedSection,
    QuestionBatch,
    QuestionContent,
    StyleProfile,
    VariantBatch,
)
from app.schemas.common import parse_id
from app.services.assessment_context import (
    AssessmentContextService,
    AssessmentRecord,
    DocumentRecord,
    PreferenceProfile,
    render_material_overview,
    render_preferences,
    render_style_profile,
)
from app.services.document_retrieval import DocumentRetrievalService, Excerpt, assign_refs, render_excerpts
from app.services.exam_store import (
    ExamStore,
    FullExam,
    content_to_row,
    leak_messages,
    normalize_content,
    question_problems,
    render_question,
    row_to_content,
    similar,
)
from app.services.jobs import RunContext
from app.services.lecture_knowledge import (
    lecture_refs,
    mapped_ids,
    matching_refs,
    render_inventory,
    render_topics,
    topic_index,
    topic_sources,
    unverified_expressions,
    unverified_formula_problems,
)
from app.services.prompt_interpreter import PromptInterpreterService
from app.services.slides import (
    WRITER_IMAGES,
    SlideIndex,
    ensure_page_words,
    first_line,
    has_slides,
    image_parts,
    render_all_slides,
    render_slides,
    slide_images,
    slide_problems,
)

logger = logging.getLogger("profpilot.generation")

_BATCH_SIZE = 4
_PLAN_TOLERANCE = 6.0  # percentage points
# A section's title and instructions are printed for students. These show they were written for someone
# else: question numbers or marks (each question shows its own, and the order can change), or notes about
# the writer or the app. Kept narrow, so a case about a loan application or quartiles (Q1) still passes.
_SECTION_NUMBERING = re.compile(
    r"\bquestions?\s+\d+\b|\bworth\s+\d+(?:\.\d+)?\s*(?:points?|marks?)\b|\(\s*\d+(?:\.\d+)?\s*(?:points?|marks?)\s*\)",
    re.IGNORECASE,
)
# A case that joins unrelated situations ("Separately, …"): each case is one situation, as in the
# professor's previous exams. ("A separate final test set" within one situation is fine.)
_JOINED_CASE = re.compile(
    r"(?:^|[.;:]\s+)(?:separately\b|in a separate (?:audit|analysis|study|diagnostic|review|survey|sample|project)\b"
    r"|another (?:analysis|audit|study|team|project|diagnostic)\b"
    r"|a separate (?:audit|analysis|study|diagnostic|review|survey|sample|project)\b)",
    re.IGNORECASE | re.MULTILINE,
)
_SECTION_NOTES = re.compile(
    r"\bprofpilot\b|\b(?:exam|question)\s+(?:author|writer)s?\b|\beventual exam\b|\bapplication's\b"
    r"|\b(?:mcq|multiple-choice) format\b",
    re.IGNORECASE,
)


@dataclass
class GenerationContext:
    """Everything generation needs about the assessment, loaded once per job."""

    record: AssessmentRecord
    spec: ExamSpec
    enhanced_prompt: str
    documents: list[DocumentRecord]
    style: StyleProfile | None
    preferences: PreferenceProfile
    # Lecture knowledge profiles of the course material, by document id (see lecture_knowledge.py).
    knowledge: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Every slide of the lecture PDFs: its words and how many pictures it shows, by document and page
    # (see slides.py).
    pages: dict[str, dict[int, dict[str, Any]]] = field(default_factory=dict)
    # The lecture PDFs downloaded for showing their slides, once per job (see slides.slide_images).
    files: dict[str, "asyncio.Future[bytes]"] = field(default_factory=dict)

    @property
    def slides(self) -> SlideIndex:
        """Every slide, under the ids the planner cites ('F1:p8')."""
        return SlideIndex.build(self.documents, self.pages)

    @property
    def knowledge_refs(self) -> set[str]:
        """Every topic id the planner may cite (e.g. 'L2.t5')."""
        return set(topic_index(lecture_refs(self.documents, self.knowledge)))

    def formula_problems(self, questions: Iterable[tuple[str, QuestionContent]]) -> list[str]:
        """(label, question) pairs that quote an unverified lecture formula's garbled extracted text."""
        expressions = unverified_expressions(self.documents, self.knowledge)
        if not expressions:
            return []
        return [problem for label, content in questions for problem in unverified_formula_problems(content, expressions, label)]

    @property
    def document_names(self) -> dict[str, str]:
        return {doc.id: doc.original_name for doc in self.documents}

    @property
    def attachments(self) -> list[DocumentRecord]:
        return [doc for doc in self.documents if doc.category == "additional_attachment" and doc.processing_status == "ready"]

    @property
    def figure_ids(self) -> set[str]:
        return {doc.id for doc in self.attachments if doc.is_image}

    @property
    def style_numbers(self) -> StyleNumbers | None:
        """The previous exams in numbers (the mix of tasks, lengths, cases), when they were analysed."""
        return style_numbers(self.style)

    @property
    def mcq_choices(self) -> int:
        """Choices in every multiple-choice question: the number the professor asked for, else 4."""
        return self.spec.mcq_choices or 4

    @property
    def has_course_material(self) -> bool:
        return any(doc.category == "course_material" and doc.processing_status == "ready" for doc in self.documents)

    def common_sections(self, framer: DataFramer) -> list[str]:
        attachments = [
            f'- id {doc.id}: "{doc.original_name}"' + (f" — {(doc.summary or {}).get('summary', '')}" if doc.summary else "")
            for doc in self.attachments
            if doc.is_image
        ]
        return [
            "APPROVED EXAM SPEC (approved by the professor; binding)\n" + json.dumps(self.spec.model_dump(), indent=1),
            "APPROVED BRIEF (approved by the professor)\n" + self.enhanced_prompt,
            render_preferences(self.preferences),
            render_style_profile(self.style, framer, planned=self.spec.question_count_max or self.spec.question_count_min),
            (
                "IMAGE ATTACHMENTS (a question may show one by setting figure_document_id)\n"
                + framer.block("attachments", "image attachments", "\n".join(attachments))
                if attachments
                else "IMAGE ATTACHMENTS: none."
            ),
        ]


async def load_generation_context(db: Database, assessment_id: str, *, require_approved: bool = True) -> GenerationContext:
    context = AssessmentContextService(db)
    assessment_id = parse_id(assessment_id, what="assessment")
    # Four independent reads as the professor, at once. Nothing is analysed
    # again here: file summaries and the style profile are already stored.
    record, documents, style, preferences = await asyncio.gather(
        context.get_assessment(assessment_id),
        context.list_documents(assessment_id),
        context.get_style_profile(assessment_id),
        context.get_preferences(),
    )
    if require_approved:
        spec = await PromptInterpreterService(db, get_openai_service()).approved_spec(record, documents)
    else:
        if not record.exam_spec:
            raise ConflictError("Improve with AI and approve the plan first.", code="spec_missing")
        spec = ExamSpec.model_validate(record.exam_spec["spec"])
    knowledge, pages = await asyncio.gather(context.get_knowledge(documents), context.get_pages(documents))
    return GenerationContext(
        record=record,
        spec=spec,
        enhanced_prompt=record.enhanced_prompt or "",
        documents=documents,
        style=style[0] if style else None,
        preferences=preferences,
        knowledge=knowledge,
        pages=pages,
    )


async def load_planning_context(db: Database, assessment_id: str, *, require_approved: bool = True) -> GenerationContext:
    """The context, with every lecture's slide words, which the planner is given."""
    ctx = await load_generation_context(db, assessment_id, require_approved=require_approved)
    # Lectures read before their slides' words were kept get them now (from their PDFs, no AI).
    if any(doc.id not in ctx.pages for doc in ctx.documents if has_slides(doc) and doc.processing_status == "ready"):
        await ensure_page_words(db, ctx.documents)
        ctx.pages = await AssessmentContextService(db).get_pages(ctx.documents)
    return ctx


def excerpt_ref_map(excerpts: list[Excerpt]) -> dict[str, dict[str, Any]]:
    return {e.ref: {"chunk_id": e.chunk_id, "document_id": e.document_id, "label": e.label} for e in excerpts}


def excerpt_id_problems(questions: Iterable[tuple[str, QuestionContent]], excerpts: list[Excerpt]) -> list[str]:
    """Questions citing excerpt ids that this request didn't give: sent back, never silently dropped
    (excerpt ids are numbered per request, so an id from the brief or plan means another excerpt)."""
    given = [e.ref for e in excerpts]
    problems = []
    for label, content in questions:
        unknown = [ref for ref in dict.fromkeys(content.source_excerpt_ids) if ref not in given]
        if unknown:
            problems.append(
                f"{label} cites {', '.join(unknown)} in source_excerpt_ids, but this request's excerpts are "
                f"{', '.join(given) or 'none'}. Cite only the excerpts given here that you used (or none)."
            )
    return problems


def question_effort(question_type: str, difficulty: str) -> Effort:
    """Reasoning effort for writing (or revising) one question and its answer key.

    High effort is kept for the questions where extra reasoning materially
    improves a correct, fair answer key: hard questions, and multi-step
    problems above easy. Everything else is written with medium effort.
    """
    if difficulty == "hard" or (question_type == "problem" and difficulty != "easy"):
        return "high"
    return "medium"


def batch_effort(slots: Iterable[PlannedQuestion]) -> Effort:
    return "high" if any(question_effort(slot.type, slot.difficulty) == "high" for slot in slots) else "medium"


# =============================================================================
# Question order: difficulty and topics are mixed unless the professor asked otherwise
# =============================================================================

# Phrases in the professor's own words that ask for a particular order.
_ORDER_REQUESTS = [
    r"\b(easy|easier|easiest|simple|simpler|hard|harder|hardest|difficult)\s+(questions?|ones|items|problems)\b[^.\n]{0,30}"
    r"\b(first|last|before|at the (start|beginning|end))\b",
    r"\b(start|begin|open)\w*\b[^.\n]{0,30}\b(easy|easier|easiest|simple|simpler|warm[- ]?up)\b",
    r"\b(increasing|ascending|progressive|rising)\b[^.\n]{0,20}\bdifficult",
    r"\bfrom\s+easy\s+(to|through)\s+(medium|hard|difficult)",
    r"\b(order|sort|arrange|group|organi[sz]e)\w*\b[^.\n]{0,30}\bby\s+(difficulty|lecture|topic|chapter|week|module|unit)",
    r"\b(lecture|chapter|topic|week|module|unit)[- ]by[- ](lecture|chapter|topic|week|module|unit)\b",
    r"\b(one|a|each)\s+section\s+(per|for each|for every)\s+(lecture|chapter|topic|week|module|unit)\b",
    r"\b(finish|cover|complete|do)\s+(lecture|chapter|topic|week|module|unit)\s+\w+\s+first\b",
    r"\bin\s+(lecture|chapter|topic|syllabus)\s+order\b",
]


def professor_requested_order(ctx: GenerationContext) -> bool:
    """Whether the professor's own words ask for a particular question order."""
    spec = ctx.spec
    texts = [ctx.record.setup.professor_prompt, ctx.record.setup.additional_notes]
    for items in (spec.constraints, spec.question_style, spec.professor_notes):
        texts.extend(item.text for item in items if item.source == "professor")
    text = "\n".join(texts).lower()
    return any(re.search(pattern, text) for pattern in _ORDER_REQUESTS)


def _runs(sequence: Sequence[str]) -> list[int]:
    lengths: list[int] = []
    for index, value in enumerate(sequence):
        if index and value == sequence[index - 1]:
            lengths[-1] += 1
        else:
            lengths.append(1)
    return lengths


def is_clustered(sequence: Sequence[str]) -> bool:
    """True when the labels are grouped where they could be mixed.

    Either every label forms one block (easy, easy, medium, medium, hard), or a
    run of three or more repeats although the other labels could break it up.
    Too few questions, or a single label, is never clustered.
    """
    if len(sequence) < 4:
        return False
    counts = Counter(sequence)
    if len(counts) < 2:
        return False
    runs = _runs(sequence)
    if len(runs) == len(counts) and max(counts.values()) >= 2:
        return True
    index = 0
    for length in runs:
        label = sequence[index]
        others = len(sequence) - counts[label]
        if length >= 3 and math.ceil(counts[label] / (others + 1)) < length:
            return True
        index += length
    return False


def _source_key(question: PlannedQuestion, document_names: list[str]) -> str:
    """The file (lecture) a planned question draws on, from its source hint."""
    hint = (question.source_hint or "").lower()
    for name in document_names:
        stem = name.lower().rsplit(".", 1)[0]
        if name.lower() in hint or (stem and stem in hint):
            return name.lower()
    hint = re.split(r"[,;(]|\bslides?\b|\bpages?\b|\bp\.", hint, maxsplit=1)[0].strip()
    return hint or question.topic.lower()


def _interleave(items: list[PlannedQuestion], sources: dict[int, str]) -> list[PlannedQuestion]:
    """Spreads difficulties and sources evenly through `items`, deterministically.

    At each position, the question whose difficulty and source are furthest
    behind an even spread goes next; repeating the previous question's
    difficulty, source or topic counts against it, and ties keep the planner's
    order.
    """
    total = len(items)
    expected = {"difficulty": Counter(q.difficulty for q in items), "source": Counter(sources[q.number] for q in items)}
    placed: dict[str, Counter[str]] = {"difficulty": Counter(), "source": Counter()}
    remaining = list(items)
    ordered: list[PlannedQuestion] = []
    for position in range(1, total + 1):
        previous = ordered[-1] if ordered else None
        scores = [_interleave_score(question, previous, sources, position / total, expected, placed) for question in remaining]
        # Highest score first; ties keep the planner's order.
        best = remaining[max(range(len(remaining)), key=lambda index: (scores[index], -index))]
        remaining.remove(best)
        ordered.append(best)
        placed["difficulty"][best.difficulty] += 1
        placed["source"][sources[best.number]] += 1
    return ordered


def _interleave_score(
    question: PlannedQuestion,
    previous: PlannedQuestion | None,
    sources: dict[int, str],
    progress: float,
    expected: dict[str, Counter[str]],
    placed: dict[str, Counter[str]],
) -> float:
    """How far behind an even spread this question's difficulty and source are (higher goes first)."""
    source = sources[question.number]
    lag = progress * expected["difficulty"][question.difficulty] - placed["difficulty"][question.difficulty]
    lag += progress * expected["source"][source] - placed["source"][source]
    if previous is not None:
        lag -= 0.25 * (question.difficulty == previous.difficulty)
        lag -= 0.25 * (source == sources[previous.number])
        lag -= 0.5 * (question.topic.strip().lower() == previous.topic.strip().lower())
    return lag


def interleave_plan(plan: ExamPlan, document_names: list[str], fixed: AbstractSet[int] = frozenset()) -> ExamPlan:
    """The plan with difficulties and topics mixed inside each section, if they were grouped.

    Only the order changes: every question keeps its type, difficulty, points
    and topic, so the format and difficulty splits stay exactly as planned.
    Sections stay in order with their questions together, and a section whose
    questions are grouped by type keeps that grouping. Questions are renumbered
    1, 2, 3… in the new order. `fixed`: numbers that stay where they are (questions the
    professor changed or wrote while the plan was made); the others move around them.
    """
    sources = {q.number: _source_key(q, document_names) for q in plan.questions}
    ordered: list[PlannedQuestion] = []
    changed = False
    for section_index in sorted({q.section_index for q in plan.questions}):
        section = [q for q in plan.questions if q.section_index == section_index]
        types = [q.type for q in section]
        # Keep a deliberate grouping by type (e.g. short answers, then problems).
        if len(_runs(types)) == len(set(types)):
            units = [[q for q in section if q.type == question_type] for question_type in dict.fromkeys(types)]
        else:
            units = [section]
        for unit in units:
            clustered = is_clustered([q.difficulty for q in unit]) or is_clustered([sources[q.number] for q in unit])
            if clustered:
                free = iter(q for q in _interleave(unit, sources) if q.number not in fixed)
                mixed = [q if q.number in fixed else next(free) for q in unit]
                changed = changed or mixed != unit
                ordered.extend(mixed)
            else:
                ordered.extend(unit)
    if not changed:
        return plan
    renumbered = [question.model_copy(update={"number": number}) for number, question in enumerate(ordered, start=1)]
    return plan.model_copy(update={"questions": renumbered})


# =============================================================================
# Planning
# =============================================================================


def plan_problems(
    plan: ExamPlan,
    spec: ExamSpec,
    figure_ids: set[str],
    knowledge_refs: set[str] | None = None,
    slides: SlideIndex | None = None,
    *,
    style: StyleNumbers | None = None,
) -> tuple[list[str], list[str]]:
    """(structural problems, softer target misses) for a plan. `knowledge_refs`: the topic ids it may
    cite; `slides`: the analysed pages each question must cite (every question traced to its pages);
    `style`: the previous exams in numbers, whose mix of tasks the plan should match."""
    hard: list[str] = []
    soft: list[str] = []
    questions = plan.questions
    if not plan.sections:
        hard.append("The plan needs at least one section.")
    if not questions:
        hard.append("The plan has no questions.")
        return hard, soft
    numbers = [q.number for q in questions]
    if numbers != list(range(1, len(questions) + 1)):
        hard.append("Number the questions 1, 2, 3… in exam order, with no gaps or repeats.")
    previous_section = 0
    for q in questions:
        if q.section_index >= len(plan.sections):
            hard.append(f"Question {q.number} points to a section that doesn't exist.")
        if q.type == "mcq" and q.cognitive_level == "create":
            hard.append(f"Question {q.number} is multiple choice, which can't assess Create; change its type or level.")
        if q.section_index < previous_section:
            hard.append("Keep each section's questions together, in section order.")
            break
        previous_section = q.section_index
        if q.figure_document_id and q.figure_document_id not in figure_ids:
            hard.append(f"Question {q.number} uses figure id {q.figure_document_id}, which isn't an image attachment.")
        unknown = [ref for ref in q.knowledge_refs if ref not in (knowledge_refs or set())]
        if unknown:
            hard.append(
                f"Question {q.number} cites {', '.join(unknown)}, which aren't topic ids from the LECTURE KNOWLEDGE "
                "inventory; use ids exactly as listed (or none if there is no inventory)."
            )

    for index, section in enumerate(plan.sections, start=1):
        text = f"{section.title}\n{section.instructions or ''}"
        if _SECTION_NUMBERING.search(text):
            hard.append(
                f"Section {index}'s title or instructions name question numbers or the marks of particular "
                "questions. They are printed for students, who see each question's own number and marks: remove them."
            )
        if _JOINED_CASE.search(section.instructions or ""):
            soft.append(
                f"Section {index} ({section.title}) joins unrelated situations in one case. Make each case one "
                "situation on one theme, and give a topic that doesn't fit a short case of its own."
            )
        if _SECTION_NOTES.search(text):
            hard.append(
                f"Section {index}'s instructions hold notes that aren't for students (about the writer, ProfPilot or "
                "the app). Write only what students need to answer."
            )

    if slides:
        pages_hard, pages_soft = slide_problems(questions, slides)
        hard += pages_hard
        soft += pages_soft

    summary = distribution.summarize(questions)
    for warning in distribution.compare(summary, spec, tolerance=_PLAN_TOLERANCE):
        soft.append(warning["message"] + " Adjust question types, difficulties, Bloom's levels or points.")
    if spec.duration_minutes and summary["estimatedMinutes"] > spec.duration_minutes:
        soft.append(
            f"The questions need about {summary['estimatedMinutes']:g} minutes, more than the "
            f"{spec.duration_minutes}-minute duration. Remove or shorten questions."
        )
    soft.extend(mix_problems(questions, style))
    if spec.question_count_min and len(questions) < spec.question_count_min:
        soft.append(f"Plan at least {spec.question_count_min} questions.")
    if spec.question_count_max and len(questions) > spec.question_count_max:
        soft.append(f"Plan at most {spec.question_count_max} questions.")
    if spec.total_points and abs(summary["totalPoints"] - spec.total_points) > 0.01:
        soft.append(f"The points add up to {summary['totalPoints']:g}; make them add up to {spec.total_points}.")
    # The same task again with only its numbers changed ('… for f = 0.64', '… for f = 0.88'). Wording
    # alone can't tell 'the mean' from 'the median' apart reliably, so nothing looser is reported.
    targets = [(q.number, re.sub(r"\d+(?:\.\d+)?", "#", " ".join(q.assessment_target.lower().split()))) for q in questions]
    for index, (number, target) in enumerate(targets):
        twin = next((other for other, text in targets[index + 1 :] if target and text == target), None)
        if twin is not None:
            soft.append(
                f"Questions {number} and {twin} have nearly the same assessment target. Give each its own task or "
                "case; a concept may return only with different thinking on a different case."
            )
    return hard, soft


# Exams of at most this many questions are planned with medium reasoning; bigger ones with high.
_SMALL_EXAM_QUESTIONS = 10


def plan_effort(spec: ExamSpec) -> Effort:
    count = spec.question_count_max or spec.question_count_min
    return "medium" if count is not None and count <= _SMALL_EXAM_QUESTIONS else "high"


def fit_points(plan: ExamPlan, total: float | None, fixed: AbstractSet[int] = frozenset()) -> ExamPlan:
    """Scales the planned points to add up to the exam's total, in code, so a points total alone never
    costs another model call. The shares by type, difficulty and level stay as planned (points scale
    together). Whole points when the plan used whole points, else halves. Unchanged when it can't fit.
    `fixed`: numbers whose points stay as they are (the professor set them); the others make up the rest."""
    if fixed and total:
        kept = sum(q.points for q in plan.questions if q.number in fixed)
        others = plan.model_copy(update={"questions": [q for q in plan.questions if q.number not in fixed]})
        if total - kept <= 0:
            return plan
        fitted = {q.number: q for q in fit_points(others, total - kept).questions}
        return plan.model_copy(update={"questions": [fitted.get(q.number, q) for q in plan.questions]})
    questions = plan.questions
    current = sum(q.points for q in questions)
    if not total or not questions or current <= 0 or abs(current - total) <= 0.01:
        return plan
    unit = 1.0 if all(float(q.points).is_integer() for q in questions) else 0.5
    if unit * len(questions) > total:
        return plan
    scaled = [max(unit, round(q.points * total / current / unit) * unit) for q in questions]
    # Rounding leaves a few units over or under: settle them on the biggest questions first.
    steps = round((total - sum(scaled)) / unit)
    biggest_first = sorted(range(len(questions)), key=lambda i: -scaled[i])
    for _ in range(4 * len(questions)):
        if steps == 0:
            break
        for i in biggest_first:
            if steps == 0:
                break
            if steps > 0:
                scaled[i] += unit
                steps -= 1
            elif scaled[i] - unit >= unit:
                scaled[i] -= unit
                steps += 1
    if steps != 0:
        return plan
    return plan.model_copy(
        update={"questions": [q.model_copy(update={"points": points}) for q, points in zip(questions, scaled, strict=True)]}
    )


def change_request(instruction: str) -> str:
    return (
        "THE PROFESSOR ASKS FOR THESE CHANGES TO THE PLAN ABOVE (their own words):\n"
        + instruction.strip()
        + "\n\nReturn the complete revised plan. Change only what the request needs and keep every other "
        "question exactly as it is: the same type, difficulty, points, Bloom's level, topic, assessment target, "
        "source pages and professor_question. Number the questions 1, 2, 3… in exam order. If the request "
        "breaks a target of the spec (a split, the total points, the time), still do what the professor asks, "
        "and add a warning naming the problem and a concrete fix."
    )


# Called with the plan written so far: its sections (complete) and its complete questions.
PlanProgress = Callable[[list[PlannedSection], list[PlannedQuestion]], Awaitable[None]]

_SECTIONS_START = re.compile(r'\s*\{\s*"sections"\s*:\s*\[')
_QUESTIONS_START = re.compile(r'\s*,\s*"questions"\s*:\s*\[')


def _array_items(text: str, start: int) -> tuple[list[Any], int | None]:
    """The complete JSON values of the array opened just before `start`, and the index after its closing
    bracket (None while it is still open)."""
    decoder = json.JSONDecoder()
    items: list[Any] = []
    index = start
    while True:
        while index < len(text) and text[index] in " \t\r\n,":
            index += 1
        if index >= len(text):
            return items, None
        if text[index] == "]":
            return items, index + 1
        try:
            item, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            return items, None
        items.append(item)


def partial_plan(text: str) -> tuple[list[PlannedSection], list[PlannedQuestion]] | None:
    """The sections and the complete questions in a plan still being written (a JSON prefix), or None
    until every section is complete (each case is shown before the questions that build on it)."""
    opened = _SECTIONS_START.match(text)
    if not opened:
        return None
    raw_sections, after = _array_items(text, opened.end())
    if after is None:
        return None
    try:
        sections = [PlannedSection.model_validate(item) for item in raw_sections]
    except pydantic.ValidationError:
        return None
    questions: list[PlannedQuestion] = []
    started = _QUESTIONS_START.match(text, after)
    if started:
        raw_questions, _ = _array_items(text, started.end())
        for item in raw_questions:
            try:
                question = PlannedQuestion.model_validate(item)
            except pydantic.ValidationError:
                break
            # Shown in order only: a question out of sequence waits for the finished plan.
            if question.number != len(questions) + 1:
                break
            questions.append(question)
    return sections, questions


class PlanStream:
    """Turns the streamed text of a plan into progress: the sections once they are complete, then each
    new complete question. The text is read again only when a JSON value may have closed."""

    def __init__(self, on_progress: PlanProgress) -> None:
        self.on_progress = on_progress
        self.length = 0
        self.shown: tuple[int, int] | None = None

    async def feed(self, text: str) -> None:
        added = text[self.length :] if len(text) >= self.length else text
        self.length = len(text)
        if "}" not in added and "]" not in added:
            return
        found = partial_plan(text)
        if found is None:
            return
        sections, questions = found
        if self.shown == (len(sections), len(questions)):
            return
        self.shown = (len(sections), len(questions))
        await self.on_progress(sections, questions)


def keep_questions(plan: ExamPlan, kept: dict[int, PlannedQuestion], base: ExamPlan | None = None) -> ExamPlan:
    """The plan with the professor's questions (changed or written while it was made) put back at their
    numbers, and, from `base`, the text of the sections they sit in (the case they were written on)."""
    if not kept:
        return plan
    questions = []
    for question in plan.questions:
        own = kept.get(question.number)
        # Under the section the plan has there, so the sections stay in order.
        questions.append(own.model_copy(update={"section_index": question.section_index}) if own else question)
    sections = list(plan.sections)
    if base is not None:
        for own in kept.values():
            index = own.section_index
            if index < len(sections) and index < len(base.sections):
                sections[index] = base.sections[index]
    return plan.model_copy(update={"questions": questions, "sections": sections})


def fits_kept(plan: ExamPlan, kept: dict[int, PlannedQuestion], base: ExamPlan) -> bool:
    """Whether a revised plan still has each kept question's place: its number, in the same section."""
    by_number = {question.number: question for question in plan.questions}
    return all(
        number in by_number and by_number[number].section_index == own.section_index and own.section_index < len(base.sections)
        for number, own in kept.items()
    )


class ExamPlanningService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage

    async def _planning_input(self, ctx: GenerationContext, framer: DataFramer) -> str:
        retrieval = DocumentRetrievalService(self.db, self.ai, self.usage)
        slides = ctx.slides
        # Lectures whose every slide is listed below need no sample of their wording.
        course_ids = [
            doc.id
            for doc in ctx.documents
            if doc.category == "course_material" and doc.processing_status == "ready" and doc.id not in ctx.pages
        ]
        mapped = mapped_ids(ctx.documents, ctx.knowledge)
        # Lectures in the LECTURE KNOWLEDGE map need only a taste of their wording here: the map gives
        # their full coverage, and each question's writer retrieves its own excerpts.
        per_document = 1 if course_ids and set(course_ids) <= mapped else 3
        spread = assign_refs(
            await retrieval.spread(course_ids, document_names=ctx.document_names, per_document=per_document, max_chars=18_000)
            if course_ids
            else []
        )
        sections = [
            *ctx.common_sections(framer),
            render_material_overview(ctx.documents, framer, mapped=mapped_ids(ctx.documents, ctx.knowledge)),
            render_inventory(ctx.documents, ctx.knowledge, framer),
            render_all_slides(slides, framer),
            render_excerpts(spread, framer, title="SAMPLE OF THE COURSE MATERIAL") if spread else "",
            "Plan the exam now.",
        ]
        return "\n\n".join(part for part in sections if part)

    def problems(self, plan: ExamPlan, ctx: GenerationContext) -> tuple[list[str], list[str]]:
        return plan_problems(plan, ctx.spec, ctx.figure_ids, ctx.knowledge_refs, ctx.slides, style=ctx.style_numbers)

    async def plan(
        self,
        ctx: GenerationContext,
        *,
        on_progress: PlanProgress | None = None,
        touched: Callable[[], Awaitable[dict[int, PlannedQuestion]]] | None = None,
    ) -> ExamPlan:
        """The plan: one planning call, then one repair for the targets it misses.

        `on_progress` gets the plan as it is written: the sections (cases) first, complete, then each
        question as soon as it is complete. `touched` returns the questions the professor changed or
        wrote meanwhile, by number: they are kept exactly as they are, and the repair plans around them.
        The order is mixed separately (see order()), so the caller can keep questions touched later."""
        framer = DataFramer()
        base_input = await self._planning_input(ctx, framer)
        stream = PlanStream(on_progress) if on_progress else None

        plan = await self.ai.parse(
            purpose="plan_exam",
            instructions=planner_instructions(framer),
            input=base_input,
            schema=ExamPlan,
            effort=plan_effort(ctx.spec),
            max_output_tokens=24_000,
            validate=lambda result: self.problems(result, ctx)[0],
            usage=self.usage,
            on_text=stream.feed if stream else None,
        )
        kept = await touched() if touched else {}
        plan = fit_points(keep_questions(plan, kept), ctx.spec.total_points, set(kept))
        _, soft = self.problems(plan, ctx)
        if soft:
            # One focused repair for targets the plan misses.
            keep_note = (
                "\nKeep questions "
                + ", ".join(map(str, sorted(kept)))
                + " exactly as they are, at the same numbers and in the same sections, with their sections' text "
                "unchanged: the professor has already changed or written them."
                if kept
                else ""
            )
            try:
                repaired = await self.ai.parse(
                    purpose="replan_exam",
                    instructions=planner_instructions(framer),
                    input=[
                        {"role": "user", "content": [{"type": "input_text", "text": base_input}]},
                        {"role": "assistant", "content": [{"type": "output_text", "text": plan.model_dump_json()}]},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Revise the plan so it meets these targets, keeping everything else:\n- "
                                    + "\n- ".join(soft)
                                    + keep_note,
                                }
                            ],
                        },
                    ],
                    schema=ExamPlan,
                    effort=plan_effort(ctx.spec),
                    max_output_tokens=24_000,
                    validate=lambda result: self.problems(result, ctx)[0],
                    usage=self.usage,
                )
                kept = await touched() if touched else {}
                if fits_kept(repaired, kept, plan):
                    repaired = fit_points(keep_questions(repaired, kept, plan), ctx.spec.total_points, set(kept))
                    remaining = self.problems(repaired, ctx)[1]
                    if len(remaining) <= len(soft):
                        plan, soft = repaired, remaining
                else:
                    logger.info("Plan repair moved questions the professor kept; keeping the first plan")
            except AppError:
                logger.info("Plan repair failed; keeping the first plan with warnings")
        for problem in soft:
            plan.warnings.append(problem)
        if touched is None:
            plan = self.order(plan, ctx)
        return plan

    def order(self, plan: ExamPlan, ctx: GenerationContext, fixed: AbstractSet[int] = frozenset()) -> ExamPlan:
        """Mixes difficulties and topics in code, without another model call, unless the professor asked
        for an order (then the planner's order is theirs). `fixed` questions stay at their numbers."""
        if plan.ordering_request or professor_requested_order(ctx):
            return plan
        return interleave_plan(plan, [doc.original_name for doc in ctx.documents if doc.category == "course_material"], fixed)

    async def change(self, ctx: GenerationContext, current: ExamPlan, instruction: str) -> ExamPlan:
        """The plan with the professor's requested changes, and everything else as it was.

        Points aren't refitted to the total here: what the professor asked for wins, and a total that
        no longer matches shows on the plan page with the other targets."""
        framer = DataFramer()
        base_input = await self._planning_input(ctx, framer)
        changed = await self.ai.parse(
            purpose="change_plan",
            instructions=planner_instructions(framer),
            input=[
                {"role": "user", "content": [{"type": "input_text", "text": base_input}]},
                {"role": "assistant", "content": [{"type": "output_text", "text": current.model_dump_json()}]},
                {"role": "user", "content": [{"type": "input_text", "text": change_request(instruction)}]},
            ],
            schema=ExamPlan,
            effort=plan_effort(ctx.spec),
            max_output_tokens=24_000,
            validate=lambda result: self.problems(result, ctx)[0],
            usage=self.usage,
        )
        # The professor's own questions stay with their slots unless the request moved them.
        before = {q.number: q for q in current.questions}
        for question in changed.questions:
            old = before.get(question.number)
            if old and old.professor_question and not question.professor_question and old.topic == question.topic:
                question.professor_question = old.professor_question
        _, soft = self.problems(changed, ctx)
        changed.warnings = soft
        return changed


# =============================================================================
# Generation
# =============================================================================


def render_sections(sections: list[tuple[str, str | None]], framer: DataFramer) -> str:
    """What students read under each section heading (a case's scenario, or what to do), for the writers.
    Empty when no section has any text."""
    if not any((instructions or "").strip() for _, instructions in sections):
        return ""
    texts = [f"[{title}]\n{(instructions or '').strip() or '(no text)'}" for title, instructions in sections]
    return "THE SECTIONS (each title, and the text students read under it before its questions)\n" + framer.block(
        "sections", "section texts", "\n\n".join(texts)
    )


def choice_count_problems(content: QuestionContent, label: str, expected: int) -> list[str]:
    """An MCQ with another number of choices than the exam uses (the professor's number, else 4)."""
    if content.type != "mcq" or len(content.choices or []) == expected:
        return []
    return [f"{label} must have exactly {expected} choices, with ids {choice_ids(expected)}."]


def batch_problems(
    batch: QuestionBatch,
    planned: list[PlannedQuestion],
    others: list[str],
    *,
    choices: int = 4,
    style: StyleNumbers | None = None,
) -> list[str]:
    problems: list[str] = []
    by_number = {q.number: q for q in batch.questions}
    if sorted(by_number) != sorted(p.number for p in planned) or len(batch.questions) != len(planned):
        problems.append("Write exactly one question for each planned number: " + ", ".join(str(p.number) for p in planned) + ".")
    for slot in planned:
        question = by_number.get(slot.number)
        if question is None:
            continue
        label = f"Question {slot.number}"
        content = normalize_content(question)
        if content.type != slot.type or content.difficulty != slot.difficulty:
            problems.append(f"{label} must be type {slot.type} and difficulty {slot.difficulty}, as planned.")
        if content.cognitive_level != slot.cognitive_level:
            problems.append(f"{label} must assess the {slot.cognitive_level} level, as planned.")
        if abs(content.points - slot.points) > 0.01:
            problems.append(f"{label} must be worth {slot.points:g} points, as planned.")
        if (content.figure_document_id or None) != (slot.figure_document_id or None):
            problems.append(f"{label} must use figure_document_id {slot.figure_document_id!r}, as planned.")
        problems.extend(question_problems(content, label=label))
        problems.extend(choice_count_problems(content, label, choices))
        # The professor's own question keeps their wording, however long.
        if not slot.professor_question:
            problems.extend(length_problems(content, label, style))
        for other in others:
            if similar(content.prompt, other) > 0.85:
                problems.append(f"{label} is almost the same as another question in the exam; make it test something different.")
                break
    return problems


def variant_problems(batch: VariantBatch, sources: dict[int, dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    numbers = [variant.source_number for variant in batch.variants]
    if sorted(numbers) != sorted(sources):
        problems.append("Write exactly one variant for each given question number: " + ", ".join(map(str, sorted(sources))) + ".")
    for variant in batch.variants:
        source = sources.get(variant.source_number)
        if source is None:
            continue
        label = f"The variant of question {variant.source_number}"
        content = normalize_content(variant.question)
        if content.type != source["type"] or content.difficulty != source["difficulty"]:
            problems.append(f"{label} must keep type {source['type']} and difficulty {source['difficulty']}.")
        if source.get("cognitive_level") and content.cognitive_level != source["cognitive_level"]:
            problems.append(f"{label} must assess the same Bloom's level ({source['cognitive_level']}).")
        if abs(content.points - float(source["points"])) > 0.01:
            problems.append(f"{label} must keep {float(source['points']):g} points.")
        if similar(content.prompt, source["prompt"]) > 0.95:
            problems.append(f"{label} is identical to the original; change its values, scenario or wording.")
        problems.extend(question_problems(content, label=label))
        if source["type"] == "mcq" and source.get("choices"):
            problems.extend(choice_count_problems(content, label, len(source["choices"])))
    return problems


class ExamGenerationService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage
        self.store = ExamStore(db)

    # -- Full generation -----------------------------------------------------------

    async def run_full(self, assessment_id: str, exam_id: str, run: RunContext) -> None:
        """The whole pipeline for an exam row that already exists (status generating)."""
        try:
            async with timed("full_generation", self.usage):
                await self._run_full(assessment_id, exam_id, run)
        except AppError as error:
            await self._fail(exam_id, error.message)
            raise
        except Exception:
            await self._fail(exam_id, "Generation stopped because of an unexpected problem.")
            raise

    async def _run_full(self, assessment_id: str, exam_id: str, run: RunContext) -> None:
        ctx = await load_planning_context(self.db, assessment_id)
        full = await self.store.load(exam_id)

        await run.stage("planning")
        if full.exam.get("plan"):
            plan = ExamPlan.model_validate(full.exam["plan"])
        else:
            # The plan is made once; a resumed generation reuses it.
            async with timed("full_generation.plan", self.usage):
                plan = await ExamPlanningService(self.db, self.ai, self.usage).plan(ctx)
            await self.store.update_exam(exam_id, {"plan": plan.model_dump()})
        if not full.versions:
            await self.store.create_versions(exam_id, ctx.spec.versions)
        if not full.sections:
            await self.store.create_sections(exam_id, [(s.title, s.instructions) for s in plan.sections])
        full = await self.store.load(exam_id)

        await run.stage("generating_questions")
        existing = {q["position"] for q in full.version_questions(full.primary_version["id"])}
        missing = [q for q in plan.questions if q.number not in existing]
        await self.write_slots(ctx, exam_id, plan, missing, run=run)

        # The professor reads the exam as soon as its questions are written. The other versions and the
        # quality check (once, after every question is written) follow in the background, as in Build
        # with AI; the review keeps a professor's edits and approvals made meanwhile.
        await run.stage("finalizing")
        await self.store.update_exam(exam_id, {"status": "ready", "error_message": None, "review_status": "running"})
        from app.services.exam_builder import start_finish_job  # avoids an import cycle

        try:
            await start_finish_job(self.db, self.ai, full.exam)
        except ConflictError:
            # Already creating the versions and checking this exam.
            logger.info("Versions and quality check already running for exam %s", exam_id)

    async def write_slots(
        self,
        ctx: GenerationContext,
        exam_id: str,
        plan: ExamPlan,
        slots: list[PlannedQuestion],
        *,
        run: RunContext | None = None,
    ) -> None:
        """Writes these planned questions into the exam's first version, each at its planned number."""
        if not slots:
            return
        full = await self.store.load(exam_id)
        slides = ctx.slides
        primary = full.primary_version
        batches = _batches(slots, settings.max_concurrent_generation_calls)
        written = [q["prompt"] for q in full.version_questions(primary["id"])]
        sections = sorted(full.sections, key=lambda s: s["position"])

        async def generate(batch: list[PlannedQuestion]) -> None:
            questions, excerpts = await self._write_batch(ctx, plan, batch, written)
            refs = excerpt_ref_map(excerpts)
            for slot in batch:
                content = normalize_content(next(q for q in questions if q.number == slot.number))
                section = sections[slot.section_index] if slot.section_index < len(sections) else sections[-1]
                row = await self.store.insert_question(
                    exam_id,
                    primary["id"],
                    {
                        **content_to_row(
                            content,
                            excerpt_refs=refs,
                            allowed_figures=ctx.figure_ids,
                            topic_refs=slides.sources(slot.source_pages)
                            + topic_sources(slot.knowledge_refs, ctx.documents, ctx.knowledge),
                        ),
                        "position": slot.number,
                        "section_id": section["id"],
                    },
                )
                written.append(row["prompt"])
            if run is not None:
                await run.heartbeat()

        # Independent batches run concurrently (bounded); each question keeps its
        # planned number as its position, so the order never depends on timing.
        async with timed("full_generation.questions", self.usage):
            await gather_limited(settings.max_concurrent_generation_calls, [generate(batch) for batch in batches])

    async def _write_batch(
        self, ctx: GenerationContext, plan: ExamPlan, batch: list[PlannedQuestion], written: list[str]
    ) -> tuple[list[GeneratedQuestion], list[Excerpt]]:
        framer = DataFramer()
        retrieval = DocumentRetrievalService(self.db, self.ai, self.usage)
        refs = [ref for slot in batch for ref in slot.knowledge_refs]
        pages = [ref for slot in batch for ref in slot.source_pages]
        topics = topic_index(lecture_refs(ctx.documents, ctx.knowledge))
        slides = ctx.slides
        titles = {ref: first_line(slides.words(doc.id, page)) for ref, (doc, page) in slides.refs.items()}
        queries = [
            f"{slot.topic}. {slot.learning_objective}. {slot.professor_question} {slot.source_hint or ''} "
            + " ".join(topics[ref][1]["name"] for ref in slot.knowledge_refs if ref in topics)
            + " ".join(titles.get(ref, "") for ref in slot.source_pages)
            for slot in batch
        ]
        # The questions' slides, as the writer will see them, and related material, at the same time.
        found, images = await asyncio.gather(
            retrieval.search(
                ctx.record.id, queries, document_names=ctx.document_names, per_query=5, max_excerpts=10, max_chars=18_000
            ),
            slide_images(self.db, slides, pages, limit=WRITER_IMAGES, files=ctx.files),
        )
        excerpts = assign_refs(found)
        plan_lines = [
            f"{q.number}. [{plan.sections[q.section_index].title if q.section_index < len(plan.sections) else ''}] "
            f"{q.type}, {q.difficulty}, {q.points:g} pts, ~{q.estimated_minutes:g} min — {q.topic}: {q.learning_objective}"
            f" (Bloom's level: {q.cognitive_level}; task: {q.task or 'as the target says'}; source: {q.source_hint or 'any'}; "
            f"figure: {q.figure_document_id or 'none'}"
            + (f"; pages: {', '.join(q.source_pages)}" if q.source_pages else "")
            + (f"; lecture knowledge: {', '.join(q.knowledge_refs)}" if q.knowledge_refs else "")
            + (f"; target: {q.assessment_target}" if q.assessment_target else "")
            + (f"; the professor's own question: {' '.join(q.professor_question.split())}" if q.professor_question else "")
            + ")"
            for q in plan.questions
        ]
        targets = [line for line in plan_lines if int(line.split(".", 1)[0]) in {slot.number for slot in batch}]
        others = list(written)
        sections = [
            *ctx.common_sections(framer),
            render_sections([(section.title, section.instructions) for section in plan.sections], framer),
            "FULL EXAM PLAN (for context)\n" + "\n".join(plan_lines),
            "QUESTIONS ALREADY WRITTEN (don't repeat them)\n"
            + ("\n".join(f"- {text[:240]}" for text in others[-40:]) if others else "(none yet)"),
            render_slides(pages, slides, framer),
            render_excerpts(excerpts, framer),
            render_topics(refs, ctx.documents, ctx.knowledge, framer),
            (
                ""
                if ctx.has_course_material
                else "NOTE: no course material was uploaded. Use general knowledge appropriate to the course and "
                "add a warning that the questions are not grounded in uploaded material."
            ),
            "WRITE THESE QUESTIONS NOW (exactly as planned)\n" + "\n".join(targets),
        ]
        batch_result = await self.ai.parse_with_files(
            purpose="generate_questions",
            instructions=generator_instructions(framer, choices=ctx.mcq_choices),
            text="\n\n".join(part for part in sections if part),
            files=image_parts(images, slides),
            schema=QuestionBatch,
            effort=batch_effort(batch),
            max_output_tokens=48_000,
            validate=lambda result: (
                batch_problems(result, batch, others, choices=ctx.mcq_choices, style=ctx.style_numbers)
                + ctx.formula_problems((f"Question {q.number}", normalize_content(q)) for q in result.questions)
                + excerpt_id_problems(((f"Question {q.number}", q) for q in result.questions), excerpts)
                + leak_messages([normalize_content(q) for q in result.questions], [q.number for q in result.questions])
            ),
            usage=self.usage,
        )
        return batch_result.questions, excerpts

    async def create_missing_variants(self, ctx: GenerationContext, exam_id: str) -> None:
        """Gives every other version an equivalent of each question it is missing."""
        full = await self.store.load(exam_id)
        primary = full.primary_version
        sources = full.version_questions(primary["id"])

        async def make(group: list[dict[str, Any]], version: dict[str, Any]) -> None:
            variants = await self.write_variants(ctx, full, group, version["label"])
            for source, content in variants:
                await self.store.insert_question(
                    exam_id,
                    version["id"],
                    {
                        **content_to_row(content, allowed_figures=ctx.figure_ids),
                        "source_refs": source.get("source_refs") or [],
                        "position": source["position"],
                        "section_id": source.get("section_id"),
                        "slot_id": source["slot_id"],
                    },
                )

        # Every version is written from Version A, so all of them can be written at once.
        work = []
        for version in full.versions[1:]:
            have = {q["slot_id"] for q in full.version_questions(version["id"])}
            todo = [q for q in sources if q["slot_id"] not in have]
            work.extend(make(todo[i : i + _BATCH_SIZE], version) for i in range(0, len(todo), _BATCH_SIZE))
        await gather_limited(settings.max_concurrent_generation_calls, work)

    async def write_variants(
        self,
        ctx: GenerationContext,
        full: FullExam,
        group: list[dict[str, Any]],
        label: str,
        *,
        source_label: str = "A",
    ) -> list[tuple[dict[str, Any], QuestionContent]]:
        framer = DataFramer()
        source_version = full.version_by_label(source_label) or full.primary_version
        numbers = {q["id"]: n for n, q in enumerate(full.version_questions(source_version["id"]), start=1)}
        sources = {numbers.get(q["id"], i + 1): q for i, q in enumerate(group)}
        ordered_sections = sorted(full.sections, key=lambda section: section["position"])
        titles = {section["id"]: section["title"] for section in ordered_sections}
        listing = "\n\n".join(
            (f"[{titles[q['section_id']]}]\n" if q.get("section_id") in titles else "") + render_question(q, number)
            for number, q in sources.items()
        )
        result = await self.ai.parse(
            purpose="write_variants",
            instructions=variant_instructions(framer),
            input="\n\n".join(
                [
                    *ctx.common_sections(framer),
                    render_sections([(section["title"], section.get("instructions")) for section in ordered_sections], framer),
                    f"Write the Version {label} equivalent of each of these Version {source_label} questions.",
                    f"VERSION {source_label} QUESTIONS (application data)\n"
                    + framer.block("questions", f"version {source_label}", listing),
                ]
            ),
            schema=VariantBatch,
            effort="medium",
            max_output_tokens=40_000,
            validate=lambda batch: (
                variant_problems(batch, sources)
                + ctx.formula_problems(
                    (f"The variant of question {v.source_number}", normalize_content(v.question)) for v in batch.variants
                )
            ),
            usage=self.usage,
        )
        return [(sources[v.source_number], normalize_content(v.question)) for v in result.variants]

    async def _fail(self, exam_id: str, message: str) -> None:
        try:
            full = await self.store.load(exam_id)
            done = len(full.version_questions(full.primary_version["id"])) if full.versions else 0
            planned = len((full.exam.get("plan") or {}).get("questions") or [])
            detail = f" {done} of {planned} questions were saved and kept." if planned else ""
            await self.store.update_exam(exam_id, {"status": "failed", "error_message": (message + detail)[:500]})
        except Exception:
            logger.warning("Could not mark exam %s as failed", exam_id)

    # -- One question at a time ("Build with AI") ------------------------------------

    async def generate_one(
        self,
        ctx: GenerationContext,
        exam_id: str,
        *,
        instructions: str | None,
        question_type: str | None,
        difficulty: str | None,
        topic: str | None,
        points: float | None,
        cognitive_level: str | None = None,
        full: FullExam | None = None,
    ) -> dict[str, Any]:
        """Writes the next Build with AI question. Only what one question needs is
        used: nothing is re-planned, re-analysed or reviewed, and no other question changes."""
        full = full or await self.store.load(exam_id)
        primary = full.primary_version
        existing = full.version_questions(primary["id"])
        number = len(existing) + 1
        suggestion = suggest_next(ctx.spec, existing)
        # What the professor asked for (passed on by the assistant) always wins over the suggestion.
        slot_type = question_type if question_type in QUESTION_TYPES else suggestion["type"]
        slot_difficulty = difficulty if difficulty in ("easy", "medium", "hard") else suggestion["difficulty"]
        slot_points = points if points and points > 0 else suggestion["points"]
        slot_level = cognitive_level if cognitive_level in COGNITIVE_LEVELS else suggest_level(ctx.spec, existing, slot_type)
        if slot_type == "mcq" and slot_level == "create":
            if question_type == "mcq":
                raise InvalidInputError("A multiple-choice question can't assess the Create level. Choose another type or level.")
            slot_type = "short_answer"
            slot_points = points if points and points > 0 else _POINTS[slot_type]
        # The suggested topic only when the professor named none (their request may imply one).
        topic = topic or (None if instructions else suggestion["topic"])
        framer = DataFramer()
        retrieval = DocumentRetrievalService(self.db, self.ai, self.usage)
        query = " ".join(part for part in [topic or "", instructions or ""] if part) or ctx.enhanced_prompt[:800]
        covered = [concept for q in existing for concept in (q.get("concepts") or [])]
        excerpts = assign_refs(
            await retrieval.search(
                ctx.record.id, [query], document_names=ctx.document_names, per_query=8, max_excerpts=8, max_chars=14_000
            )
        )
        slot = PlannedQuestion(
            number=number,
            section_index=0,
            type=slot_type,  # type: ignore[arg-type]
            difficulty=slot_difficulty,  # type: ignore[arg-type]
            points=slot_points,
            topic=topic or "the next most useful topic from the coverage list",
            learning_objective="as the professor asks, consistent with the spec",
            cognitive_level=slot_level,  # type: ignore[arg-type]
            source_hint=None,
            estimated_minutes=max(1.0, slot_points * 1.5),
            figure_document_id=None,
        )
        others = [q["prompt"] for q in existing]
        existing_content = [row_to_content(q) for q in existing]
        request = [
            f"Write question {number}: type {slot_type}, difficulty {slot_difficulty}, Bloom's level {slot_level}, "
            f"{slot_points:g} points.",
            f"Topic: {topic}" if topic else "Topic: choose the most useful topic not yet covered, following the coverage list.",
            f"The professor's request: {instructions}" if instructions else "",
            "Concepts already covered: " + (", ".join(dict.fromkeys(covered)) if covered else "none yet"),
            "Choose a realistic estimated_minutes; figure_document_id must be null unless the professor asked for an attachment.",
        ]
        result = await self.ai.parse(
            purpose="generate_question",
            instructions=generator_instructions(framer, choices=ctx.mcq_choices),
            input="\n\n".join(
                [
                    *ctx.common_sections(framer),
                    "QUESTIONS ALREADY IN THE EXAM\n" + ("\n".join(f"- {t[:240]}" for t in others) if others else "(none yet)"),
                    render_excerpts(excerpts, framer),
                    # The lecture topics closest to what was asked, from the stored profiles (no extra call).
                    render_topics(matching_refs(query, ctx.documents, ctx.knowledge), ctx.documents, ctx.knowledge, framer),
                    "\n".join(part for part in request if part),
                ]
            ),
            schema=QuestionBatch,
            effort=question_effort(slot_type, slot_difficulty),
            max_output_tokens=24_000,
            validate=lambda batch: (
                _single_problems(batch, slot, others, ctx.figure_ids, choices=ctx.mcq_choices, style=ctx.style_numbers)
                + ctx.formula_problems(("The question", normalize_content(q)) for q in batch.questions)
                + excerpt_id_problems((("The question", q) for q in batch.questions), excerpts)
                + leak_messages(
                    [*existing_content, *(normalize_content(q) for q in batch.questions[:1])],
                    [*range(1, len(existing_content) + 1), number],
                    only={number},
                )
            ),
            usage=self.usage,
        )
        content = normalize_content(result.questions[0])
        section = _section_for(full, content.type, ctx.spec)
        return await self.store.insert_question(
            exam_id,
            primary["id"],
            {
                **content_to_row(content, excerpt_refs=excerpt_ref_map(excerpts), allowed_figures=ctx.figure_ids),
                "position": await self.store.next_position(primary["id"]),
                "section_id": section["id"] if section else None,
            },
        )


def _single_problems(
    batch: QuestionBatch,
    slot: PlannedQuestion,
    others: list[str],
    figure_ids: set[str],
    *,
    choices: int = 4,
    style: StyleNumbers | None = None,
) -> list[str]:
    if len(batch.questions) != 1:
        return ["Write exactly one question."]
    question = normalize_content(batch.questions[0])
    problems = []
    if question.type != slot.type or question.difficulty != slot.difficulty:
        problems.append(f"The question must be type {slot.type} and difficulty {slot.difficulty}.")
    if question.cognitive_level != slot.cognitive_level:
        problems.append(f"The question must assess the {slot.cognitive_level} level.")
    if abs(question.points - slot.points) > 0.01:
        problems.append(f"The question must be worth {slot.points:g} points.")
    if question.figure_document_id and question.figure_document_id not in figure_ids:
        problems.append("figure_document_id must be null or one of the listed image attachments.")
    problems.extend(question_problems(question))
    problems.extend(choice_count_problems(question, "The question", choices))
    if not slot.professor_question:
        problems.extend(length_problems(question, "The question", style))
    if any(similar(question.prompt, other) > 0.85 for other in others):
        problems.append("The question is almost the same as one already in the exam; make it different.")
    return problems


_POINTS = {"mcq": 2.0, "short_answer": 5.0, "long_answer": 10.0, "problem": 10.0}
# Without a difficulty split in the plan, Build with AI still mixes difficulties,
# around the split the setup form starts with.
_DEFAULT_DIFFICULTY = {"easy": 30, "medium": 40, "hard": 30}
_EMPHASIS_WEIGHT = {"high": 3, "normal": 2, "low": 1}
_STOPWORDS = {
    "about",
    "apply",
    "applying",
    "based",
    "between",
    "concept",
    "concepts",
    "explain",
    "from",
    "given",
    "into",
    "lecture",
    "question",
    "student",
    "students",
    "that",
    "their",
    "these",
    "this",
    "using",
    "what",
    "when",
    "which",
    "with",
}


def suggest_next(spec: ExamSpec, existing: list[dict[str, Any]]) -> dict[str, Any]:
    """The type, difficulty, points and topic for the next Build with AI question.

    It moves the exam toward the plan's targets by marks while mixing them as it
    goes: no difficulty (and no lecture) is finished before the others start,
    and the previous question's difficulty and topic aren't repeated while
    another is about as far behind. The professor's explicit requests override it.
    """
    summary = distribution.summarize(existing)
    if spec.question_format:
        # Whichever side of the split is furthest below its target comes next.
        mcq_deficit = spec.question_format.mcq_percent - summary["mcqPercent"]
        question_type = "mcq" if mcq_deficit > 0 else "short_answer"
    else:
        question_type = "short_answer"
    targets = (
        {"easy": spec.difficulty.easy_percent, "medium": spec.difficulty.medium_percent, "hard": spec.difficulty.hard_percent}
        if spec.difficulty
        else _DEFAULT_DIFFICULTY
    )
    deficits = {level: targets[level] - summary[f"{level}Percent"] for level in ("easy", "medium", "hard")}
    # Furthest behind first; ties keep the order easy, medium, hard.
    ranked = sorted(deficits, key=lambda level: deficits[level], reverse=True)
    difficulty = ranked[0]
    previous = existing[-1]["difficulty"] if existing else None
    # Rather than repeat the previous difficulty, take the runner-up while it is
    # still about on target (marks shift with every question, so allow a few points).
    if difficulty == previous and deficits[ranked[1]] > -5 and deficits[difficulty] - deficits[ranked[1]] <= 15:
        difficulty = ranked[1]
    return {
        "type": question_type,
        "difficulty": difficulty,
        "points": _POINTS[question_type],
        "topic": _next_topic(spec, existing),
    }


def suggest_level(spec: ExamSpec, existing: list[dict[str, Any]], question_type: str) -> str:
    """The Bloom's level furthest behind the plan's split by marks (the Balanced preset
    when the plan has none), avoiding the previous question's level while another
    is about as far behind. Multiple choice never gets Create."""
    targets = spec.bloom.shares() if spec.bloom else BLOOM_PRESETS["balanced"]
    shares = distribution.summarize(existing)["bloom"] or dict.fromkeys(COGNITIVE_LEVELS, 0.0)
    levels = [level for level in COGNITIVE_LEVELS if targets[level] > 0 and not (question_type == "mcq" and level == "create")]
    if not levels:
        return "apply"
    deficits = {level: targets[level] - shares[level] for level in levels}
    ranked = sorted(levels, key=lambda level: deficits[level], reverse=True)
    previous = existing[-1].get("cognitive_level") if existing else None
    if ranked[0] == previous and len(ranked) > 1 and deficits[ranked[0]] - deficits[ranked[1]] <= 10:
        return ranked[1]
    return ranked[0]


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 3 and word not in _STOPWORDS}


def _covered_topic(question: dict[str, Any], topics: list[Any]) -> int | None:
    """Which coverage topic an existing question is about, judged by its concepts and text."""
    words = _words(
        " ".join([*(question.get("concepts") or []), *(question.get("learning_objectives") or []), question["prompt"][:400]])
    )
    best, best_share = None, 0.0
    for index, topic in enumerate(topics):
        topic_words = _words(topic.topic)
        if topic_words:
            share = len(words & topic_words) / len(topic_words)
            if share > best_share:
                best, best_share = index, share
    return best if best_share >= 0.5 else None


def _next_topic(spec: ExamSpec, existing: list[dict[str, Any]]) -> str | None:
    """The coverage topic furthest behind its share (by emphasis), avoiding the previous
    question's topic and, where possible, the same lecture."""
    topics = spec.coverage
    if not topics:
        return None
    covered = [_covered_topic(question, topics) for question in existing]
    counts = Counter(index for index in covered if index is not None)
    weights = [_EMPHASIS_WEIGHT.get(topic.emphasis, 2) for topic in topics]
    upcoming = len(existing) + 1
    previous = covered[-1] if covered else None
    previous_files = set(topics[previous].source_documents) if previous is not None else set()

    def score(index: int) -> float:
        lag = upcoming * weights[index] / sum(weights) - counts[index]
        if index == previous:
            lag -= 1.0
        if previous_files & set(topics[index].source_documents):
            lag -= 0.25
        return lag

    return topics[max(range(len(topics)), key=lambda index: (score(index), -index))].topic


def _section_for(full: FullExam, question_type: str, spec: ExamSpec) -> dict[str, Any] | None:
    sections = sorted(full.sections, key=lambda s: s["position"])
    if not sections:
        return None
    for index, section_spec in enumerate(spec.sections):
        if question_type in section_spec.question_types and index < len(sections):
            return sections[index]
    return sections[0]


# Roughly how much longer a question takes to write with high reasoning effort than
# with medium, used only to balance batches so that they finish at about the same time.
_HIGH_EFFORT_COST = 2.5


def _batches(missing: list[PlannedQuestion], parallel: int) -> list[list[PlannedQuestion]]:
    """Splits the questions still to write into independent batches of at most _BATCH_SIZE.

    There are enough batches to keep `parallel` calls busy in as few rounds as
    possible. Questions that need high reasoning effort are batched apart from
    the others (which are then written with medium effort), and the batches are
    shared out so the slowest one is as quick as possible; high-effort batches
    go first. Each question keeps its planned number.
    """
    if not missing:
        return []
    total = len(missing)
    slots = max(1, parallel)
    needed = math.ceil(total / _BATCH_SIZE)
    count = min(total, max(needed, math.ceil(needed / slots) * slots))
    high = [q for q in missing if question_effort(q.type, q.difficulty) == "high"]
    medium = [q for q in missing if question_effort(q.type, q.difficulty) != "high"]
    splits = [
        (for_high, count - for_high)
        for for_high in range(1, count)
        if for_high <= len(high)
        and count - for_high <= len(medium)
        and math.ceil(len(high) / for_high) <= _BATCH_SIZE
        and math.ceil(len(medium) / (count - for_high)) <= _BATCH_SIZE
    ]
    if high and medium and splits:
        for_high, for_medium = min(
            splits,
            key=lambda split: max(_HIGH_EFFORT_COST * math.ceil(len(high) / split[0]), math.ceil(len(medium) / split[1])),
        )
        return _split(high, for_high) + _split(medium, for_medium)
    return _split(high + medium, count)


def _split(items: list[PlannedQuestion], count: int) -> list[list[PlannedQuestion]]:
    """`items` in `count` consecutive parts of nearly equal size."""
    size, extra = divmod(len(items), count)
    parts: list[list[PlannedQuestion]] = []
    start = 0
    for index in range(count):
        end = start + size + (1 if index < extra else 0)
        parts.append(items[start:end])
        start = end
    return parts
