"""Exam planning and generation: from an approved ExamSpec to structured questions.

The full pipeline is controlled, not one giant prompt:

1. plan every question (type, difficulty, points, topic, time) against the spec;
2. check the plan in code (splits by marks, time, structure) and repair it once;
3. write the questions with complete answer keys, in parallel batches, each
   batch with the course-material excerpts relevant to its own topics;
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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.ai.openai_service import Effort, OpenAIService, Usage, gather_limited, get_openai_service
from app.ai.prompts import DataFramer, generator_instructions, planner_instructions, variant_instructions
from app.core.config import settings
from app.core.errors import AppError, ConflictError
from app.core.timing import timed
from app.db.supabase import Database
from app.domain import distribution
from app.schemas.ai import (
    QUESTION_TYPES,
    ExamPlan,
    ExamSpec,
    GeneratedQuestion,
    PlannedQuestion,
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
    normalize_content,
    question_problems,
    render_question,
    similar,
)
from app.services.jobs import RunContext
from app.services.prompt_interpreter import PromptInterpreterService

logger = logging.getLogger("profpilot.generation")

_BATCH_SIZE = 4
_PLAN_TOLERANCE = 6.0  # percentage points


@dataclass
class GenerationContext:
    """Everything generation needs about the assessment, loaded once per job."""

    record: AssessmentRecord
    spec: ExamSpec
    enhanced_prompt: str
    documents: list[DocumentRecord]
    style: StyleProfile | None
    preferences: PreferenceProfile

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
            render_style_profile(self.style, framer),
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
    return GenerationContext(
        record=record,
        spec=spec,
        enhanced_prompt=record.enhanced_prompt or "",
        documents=documents,
        style=style[0] if style else None,
        preferences=preferences,
    )


def excerpt_ref_map(excerpts: list[Excerpt]) -> dict[str, dict[str, Any]]:
    return {e.ref: {"chunk_id": e.chunk_id, "document_id": e.document_id, "label": e.label} for e in excerpts}


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


def interleave_plan(plan: ExamPlan, document_names: list[str]) -> ExamPlan:
    """The plan with difficulties and topics mixed inside each section, if they were grouped.

    Only the order changes: every question keeps its type, difficulty, points
    and topic, so the format and difficulty splits stay exactly as planned.
    Sections stay in order with their questions together, and a section whose
    questions are grouped by type keeps that grouping. Questions are renumbered
    1, 2, 3… in the new order.
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
                mixed = _interleave(unit, sources)
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


def plan_problems(plan: ExamPlan, spec: ExamSpec, figure_ids: set[str]) -> tuple[list[str], list[str]]:
    """(structural problems, softer target misses) for a plan."""
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
        if q.section_index < previous_section:
            hard.append("Keep each section's questions together, in section order.")
            break
        previous_section = q.section_index
        if q.figure_document_id and q.figure_document_id not in figure_ids:
            hard.append(f"Question {q.number} uses figure id {q.figure_document_id}, which isn't an image attachment.")

    summary = distribution.summarize(
        [
            {"type": q.type, "difficulty": q.difficulty, "points": q.points, "estimated_minutes": q.estimated_minutes}
            for q in questions
        ]
    )
    for warning in distribution.compare(summary, spec, tolerance=_PLAN_TOLERANCE):
        soft.append(warning["message"] + " Adjust question types, difficulties or points.")
    if spec.duration_minutes and summary["estimatedMinutes"] > spec.duration_minutes:
        soft.append(
            f"The questions need about {summary['estimatedMinutes']:g} minutes, more than the "
            f"{spec.duration_minutes}-minute duration. Remove or shorten questions."
        )
    if spec.question_count_min and len(questions) < spec.question_count_min:
        soft.append(f"Plan at least {spec.question_count_min} questions.")
    if spec.question_count_max and len(questions) > spec.question_count_max:
        soft.append(f"Plan at most {spec.question_count_max} questions.")
    if spec.total_points and abs(summary["totalPoints"] - spec.total_points) > 0.01:
        soft.append(f"The points add up to {summary['totalPoints']:g}; make them add up to {spec.total_points}.")
    return hard, soft


class ExamPlanningService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage

    async def plan(self, ctx: GenerationContext) -> ExamPlan:
        framer = DataFramer()
        retrieval = DocumentRetrievalService(self.db, self.ai, self.usage)
        course_ids = [doc.id for doc in ctx.documents if doc.category == "course_material" and doc.processing_status == "ready"]
        spread = assign_refs(
            await retrieval.spread(course_ids, document_names=ctx.document_names, per_document=3, max_chars=18_000)
        )
        sections = [
            *ctx.common_sections(framer),
            render_material_overview(ctx.documents, framer),
            render_excerpts(spread, framer, title="SAMPLE OF THE COURSE MATERIAL"),
            "Plan the exam now.",
        ]
        base_input = "\n\n".join(sections)
        figure_ids = ctx.figure_ids

        plan = await self.ai.parse(
            purpose="plan_exam",
            instructions=planner_instructions(framer),
            input=base_input,
            schema=ExamPlan,
            effort="high",
            max_output_tokens=24_000,
            validate=lambda result: plan_problems(result, ctx.spec, figure_ids)[0],
            usage=self.usage,
        )
        _, soft = plan_problems(plan, ctx.spec, figure_ids)
        if soft:
            # One focused repair for targets the plan misses.
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
                                    + "\n- ".join(soft),
                                }
                            ],
                        },
                    ],
                    schema=ExamPlan,
                    effort="high",
                    max_output_tokens=24_000,
                    validate=lambda result: plan_problems(result, ctx.spec, figure_ids)[0],
                    usage=self.usage,
                )
                remaining = plan_problems(repaired, ctx.spec, figure_ids)[1]
                if len(remaining) <= len(soft):
                    plan, soft = repaired, remaining
            except AppError:
                logger.info("Plan repair failed; keeping the first plan with warnings")
        for problem in soft:
            plan.warnings.append(problem)
        # Mix difficulties and topics in code, without another model call, unless
        # the professor asked for an order (then the planner's order is theirs).
        if not plan.ordering_request and not professor_requested_order(ctx):
            plan = interleave_plan(plan, [doc.original_name for doc in ctx.documents if doc.category == "course_material"])
        return plan


# =============================================================================
# Generation
# =============================================================================


def batch_problems(batch: QuestionBatch, planned: list[PlannedQuestion], others: list[str]) -> list[str]:
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
        if abs(content.points - slot.points) > 0.01:
            problems.append(f"{label} must be worth {slot.points:g} points, as planned.")
        if (content.figure_document_id or None) != (slot.figure_document_id or None):
            problems.append(f"{label} must use figure_document_id {slot.figure_document_id!r}, as planned.")
        problems.extend(question_problems(content, label=label))
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
        if abs(content.points - float(source["points"])) > 0.01:
            problems.append(f"{label} must keep {float(source['points']):g} points.")
        if similar(content.prompt, source["prompt"]) > 0.95:
            problems.append(f"{label} is identical to the original; change its values, scenario or wording.")
        problems.extend(question_problems(content, label=label))
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
        ctx = await load_generation_context(self.db, assessment_id)
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
        primary = full.primary_version
        existing = {q["position"] for q in full.version_questions(primary["id"])}
        missing = [q for q in plan.questions if q.number not in existing]
        batches = _batches(missing, settings.max_concurrent_generation_calls)
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
                        **content_to_row(content, excerpt_refs=refs, allowed_figures=ctx.figure_ids),
                        "position": slot.number,
                        "section_id": section["id"],
                    },
                )
                written.append(row["prompt"])
            await run.heartbeat()

        # Independent batches run concurrently (bounded); each question keeps its
        # planned number as its position, so the order never depends on timing.
        async with timed("full_generation.questions", self.usage):
            await gather_limited(settings.max_concurrent_generation_calls, [generate(batch) for batch in batches])

        if len(full.versions) > 1:
            await run.stage("creating_versions")
            async with timed("full_generation.versions", self.usage):
                await self.create_missing_variants(ctx, exam_id)

        await run.stage("reviewing")
        from app.services.exam_review import ExamReviewService  # avoids an import cycle

        # The full quality check runs once, after every question is written.
        async with timed("full_generation.review", self.usage):
            await ExamReviewService(self.db, self.ai, self.usage).review(exam_id, ctx=ctx)

        await run.stage("finalizing")
        await self.store.update_exam(exam_id, {"status": "ready", "error_message": None})

    async def _write_batch(
        self, ctx: GenerationContext, plan: ExamPlan, batch: list[PlannedQuestion], written: list[str]
    ) -> tuple[list[GeneratedQuestion], list[Excerpt]]:
        framer = DataFramer()
        retrieval = DocumentRetrievalService(self.db, self.ai, self.usage)
        queries = [f"{slot.topic}. {slot.learning_objective}. {slot.source_hint or ''}" for slot in batch]
        excerpts = assign_refs(
            await retrieval.search(
                ctx.record.id, queries, document_names=ctx.document_names, per_query=5, max_excerpts=10, max_chars=18_000
            )
        )
        plan_lines = [
            f"{q.number}. [{plan.sections[q.section_index].title if q.section_index < len(plan.sections) else ''}] "
            f"{q.type}, {q.difficulty}, {q.points:g} pts, ~{q.estimated_minutes:g} min — {q.topic}: {q.learning_objective}"
            f" (level: {q.cognitive_level}; source: {q.source_hint or 'any'}; figure: {q.figure_document_id or 'none'})"
            for q in plan.questions
        ]
        targets = [line for line in plan_lines if int(line.split(".", 1)[0]) in {slot.number for slot in batch}]
        others = list(written)
        sections = [
            *ctx.common_sections(framer),
            "FULL EXAM PLAN (for context)\n" + "\n".join(plan_lines),
            "QUESTIONS ALREADY WRITTEN (don't repeat them)\n"
            + ("\n".join(f"- {text[:240]}" for text in others[-40:]) if others else "(none yet)"),
            render_excerpts(excerpts, framer),
            (
                ""
                if ctx.has_course_material
                else "NOTE: no course material was uploaded. Use general knowledge appropriate to the course and "
                "add a warning that the questions are not grounded in uploaded material."
            ),
            "WRITE THESE QUESTIONS NOW (exactly as planned)\n" + "\n".join(targets),
        ]
        batch_result = await self.ai.parse(
            purpose="generate_questions",
            instructions=generator_instructions(framer),
            input="\n\n".join(part for part in sections if part),
            schema=QuestionBatch,
            effort=batch_effort(batch),
            max_output_tokens=48_000,
            validate=lambda result: batch_problems(result, batch, others),
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
        listing = "\n\n".join(render_question(q, number) for number, q in sources.items())
        result = await self.ai.parse(
            purpose="write_variants",
            instructions=variant_instructions(framer),
            input="\n\n".join(
                [
                    *ctx.common_sections(framer),
                    f"Write the Version {label} equivalent of each of these Version {source_label} questions.",
                    f"VERSION {source_label} QUESTIONS (application data)\n"
                    + framer.block("questions", f"version {source_label}", listing),
                ]
            ),
            schema=VariantBatch,
            effort="medium",
            max_output_tokens=40_000,
            validate=lambda batch: variant_problems(batch, sources),
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
            cognitive_level="application",
            source_hint=None,
            estimated_minutes=max(1.0, slot_points * 1.5),
            figure_document_id=None,
        )
        others = [q["prompt"] for q in existing]
        request = [
            f"Write question {number}: type {slot_type}, difficulty {slot_difficulty}, {slot_points:g} points.",
            f"Topic: {topic}" if topic else "Topic: choose the most useful topic not yet covered, following the coverage list.",
            f"The professor's request: {instructions}" if instructions else "",
            "Concepts already covered: " + (", ".join(dict.fromkeys(covered)) if covered else "none yet"),
            "Choose a realistic estimated_minutes; figure_document_id must be null unless the professor asked for an attachment.",
        ]
        result = await self.ai.parse(
            purpose="generate_question",
            instructions=generator_instructions(framer),
            input="\n\n".join(
                [
                    *ctx.common_sections(framer),
                    "QUESTIONS ALREADY IN THE EXAM\n" + ("\n".join(f"- {t[:240]}" for t in others) if others else "(none yet)"),
                    render_excerpts(excerpts, framer),
                    "\n".join(part for part in request if part),
                ]
            ),
            schema=QuestionBatch,
            effort=question_effort(slot_type, slot_difficulty),
            max_output_tokens=24_000,
            validate=lambda batch: _single_problems(batch, slot, others, ctx.figure_ids),
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


def _single_problems(batch: QuestionBatch, slot: PlannedQuestion, others: list[str], figure_ids: set[str]) -> list[str]:
    if len(batch.questions) != 1:
        return ["Write exactly one question."]
    question = normalize_content(batch.questions[0])
    problems = []
    if question.type != slot.type or question.difficulty != slot.difficulty:
        problems.append(f"The question must be type {slot.type} and difficulty {slot.difficulty}.")
    if abs(question.points - slot.points) > 0.01:
        problems.append(f"The question must be worth {slot.points:g} points.")
    if question.figure_document_id and question.figure_document_id not in figure_ids:
        problems.append("figure_document_id must be null or one of the listed image attachments.")
    problems.extend(question_problems(question))
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
