"""The question plan: the page where the professor builds the exam, question by question.

The planner lists every question (its topic, type, difficulty, Bloom's level, points and what students do).
It runs early, as soon as the improved prompt is on screen, so it is usually ready by the time the professor
presses Next. On the plan page the professor changes any planned question (by hand, by giving their own
question, or by asking the AI), writes questions one at a time or all at once, and finishes the exam there.

The plan is kept in the assessment's envelope (exam_spec[QUESTION_PLAN_KEY]), so a new interpretation or
an edited prompt drops it (and stops one still being made). Next to it are the exam written from it and the
numbers of the questions written before their plan changed ("outdated": rewritten only when the professor
asks, or when they finish the exam). A written question sits at its planned number (its position) in the
exam's first version; the other versions are written when the exam is finished.
"""

from dataclasses import dataclass, field
from typing import Any

from app.ai.openai_service import OpenAIService, Usage
from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.core.timing import timed
from app.db.supabase import Database
from app.domain import distribution
from app.schemas.ai import COGNITIVE_LEVELS, QUESTION_TYPES, ExamPlan, ExamSpec, PlannedQuestion
from app.services.assessment_context import AssessmentContextService, AssessmentRecord
from app.services.exam_generation import (
    ExamGenerationService,
    ExamPlanningService,
    load_planning_context,
)
from app.services.exam_store import ExamStore, serialize_question
from app.services.jobs import RUN_COLUMNS, RunContext, is_stale, job_runner
from app.services.prompt_interpreter import QUESTION_PLAN_KEY

# Planning and plan changes run as interpretation jobs: they continue the same review, must not overlap a
# new interpretation, and whatever replaces the prompt stops them. The stage tells them apart.
PLANNING_STAGE = "planning"
CHANGING_STAGE = "changing_plan"
_PLAN_STAGES = (PLANNING_STAGE, CHANGING_STAGE)

# A slot's question is outdated when any of these change after it was written.
_CONTENT_FIELDS = (
    "type",
    "difficulty",
    "points",
    "cognitive_level",
    "topic",
    "assessment_target",
    "professor_question",
    "source_pages",
    "knowledge_refs",
)


@dataclass
class PlanState:
    plan: ExamPlan
    # The exam written from this plan, once the first question is written.
    exam_id: str | None = None
    # Planned numbers whose written question no longer matches its plan.
    outdated: list[int] = field(default_factory=list)

    @classmethod
    def of(cls, record: AssessmentRecord) -> "PlanState | None":
        stored = (record.exam_spec or {}).get(QUESTION_PLAN_KEY)
        if not stored:
            return None
        return cls(
            plan=ExamPlan.model_validate(stored["plan"]),
            exam_id=stored.get("exam_id"),
            outdated=sorted(set(stored.get("outdated") or [])),
        )

    def dump(self) -> dict[str, Any]:
        return {"plan": self.plan.model_dump(), "exam_id": self.exam_id, "outdated": sorted(set(self.outdated))}

    def slot(self, number: int) -> PlannedQuestion:
        found = next((question for question in self.plan.questions if question.number == number), None)
        if found is None:
            raise NotFoundError("This planned question doesn't exist.")
        return found


async def stop_planning(db: Database, assessment_id: str) -> None:
    """Stops a plan still being made or changed: whatever replaced the prompt makes it pointless."""
    for stage in _PLAN_STAGES:
        await job_runner.stop(db, assessment_id=assessment_id, kind="interpretation", stage=stage)


async def require_idle(db: Database, assessment_id: str, *, kinds: tuple[str, ...], doing: str) -> None:
    """Refuses while the plan is being made or changed, or a question is being written."""
    running = await db.select(
        "ai_runs",
        columns=RUN_COLUMNS,
        filters=[("exam_project_id", "eq", assessment_id), ("status", "eq", "running"), ("kind", "in", list(kinds))],
    )
    for run in running:
        if is_stale(run):
            continue
        if run["kind"] == "interpretation" and run.get("stage") not in _PLAN_STAGES:
            raise ConflictError("ProfPilot is rewriting your prompt. Wait until it's done.", code="job_running")
        if run["kind"] == "interpretation":
            raise ConflictError(f"ProfPilot is still working on the plan. Wait until it's done to {doing}.", code="job_running")
        raise ConflictError(f"ProfPilot is still writing. Wait until it's done to {doing}.", code="job_running")


def changed_numbers(before: ExamPlan, after: ExamPlan) -> set[int]:
    """The planned numbers whose question content differs between two plans (or exists in only one)."""
    old = {question.number: question for question in before.questions}
    new = {question.number: question for question in after.questions}
    changed = set(old) ^ set(new)
    for number in set(old) & set(new):
        if any(getattr(old[number], name) != getattr(new[number], name) for name in _CONTENT_FIELDS):
            changed.add(number)
    return changed


class QuestionPlanService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage | None = None) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage or Usage()
        self.context = AssessmentContextService(db)
        self.store = ExamStore(db)

    # -- Reading -----------------------------------------------------------------------

    async def _state(self, assessment_id: str) -> tuple[AssessmentRecord, PlanState]:
        record = await self.context.get_assessment(assessment_id)
        state = PlanState.of(record)
        if state is None:
            raise ConflictError("ProfPilot hasn't planned the questions yet.", code="plan_missing")
        return record, state

    async def _save(self, assessment_id: str, state: PlanState) -> None:
        # Saved into the latest envelope, so nothing written meanwhile (an approval) is lost.
        latest = await self.context.get_assessment(assessment_id)
        await self.db.update(
            "exam_projects",
            {"exam_spec": {**(latest.exam_spec or {}), QUESTION_PLAN_KEY: state.dump()}},
            filters=[("id", "eq", latest.id), ("professor_id", "eq", self.db.professor_id)],
            columns="id",
        )

    async def _exam_of(self, assessment_id: str, state: PlanState) -> dict[str, Any] | None:
        """The exam written from this plan, if it still exists."""
        if not state.exam_id:
            return None
        exam = await self.store.exam_for_assessment(assessment_id)
        return exam if exam and exam["id"] == state.exam_id else None

    async def get(self, assessment_id: str) -> dict[str, Any]:
        record = await self.context.get_assessment(assessment_id)
        state = PlanState.of(record)
        if state is None:
            return {"status": "none", "questions": [], "rationale": None, "warnings": [], "exam": None}
        exam = await self._exam_of(record.id, state)
        written: dict[int, dict[str, Any]] = {}
        if exam is not None:
            full = await self.store.load(exam["id"])
            for row in full.version_questions(full.primary_version["id"]):
                written[row["position"]] = serialize_question(row, row["position"], full.revision_counts.get(row["id"], 0))
        spec = record.exam_spec.get("spec") if record.exam_spec else None
        summary = distribution.summarize(state.plan.questions)
        return {
            "status": "ready",
            "rationale": state.plan.rationale,
            "warnings": state.plan.warnings,
            "summary": summary,
            "distributionWarnings": distribution.compare(summary, ExamSpec.model_validate(spec) if spec else None),
            "exam": (
                {"id": exam["id"], "status": exam["status"], "errorMessage": exam.get("error_message")}
                if exam is not None
                else None
            ),
            "questions": [
                {
                    **question.model_dump(),
                    "outdated": question.number in state.outdated and question.number in written,
                    "written": written.get(question.number),
                }
                for question in state.plan.questions
            ],
        }

    # -- Making and changing the plan ---------------------------------------------------

    async def create(self, assessment_id: str, run: RunContext) -> None:
        ctx = await load_planning_context(self.db, assessment_id, require_approved=False)
        async with timed("question_plan", self.usage):
            plan = await ExamPlanningService(self.db, self.ai, self.usage).plan(ctx)
        for question in plan.questions:
            question.professor_question = ""
        latest = await self.context.get_assessment(assessment_id)
        if latest.spec_status not in ("draft", "approved") or latest.enhanced_prompt != ctx.enhanced_prompt:
            raise ConflictError("Your prompt changed during planning. Confirm it again.", code="spec_stale")
        await self._save(assessment_id, PlanState(plan=plan))

    async def change(self, assessment_id: str, instruction: str, run: RunContext) -> None:
        """Applies the professor's request to the plan, keeping everything else as it was."""
        _, state = await self._state(assessment_id)
        ctx = await load_planning_context(self.db, assessment_id, require_approved=False)
        async with timed("question_plan.change", self.usage):
            changed = await ExamPlanningService(self.db, self.ai, self.usage).change(ctx, state.plan, instruction)
        latest = await self.context.get_assessment(assessment_id)
        if latest.enhanced_prompt != ctx.enhanced_prompt:
            raise ConflictError("Your prompt changed while the plan was being changed. Try again.", code="spec_stale")
        await self._apply(assessment_id, state, changed)

    async def edit(self, assessment_id: str, number: int, values: dict[str, Any]) -> dict[str, Any]:
        """The professor changes a planned question by hand, or gives their own question."""
        record, state = await self._state(assessment_id)
        await require_idle(self.db, record.id, kinds=("interpretation", "generation"), doing="change the plan")
        slot = state.slot(number)
        topic = str(values.get("topic") or "").strip()
        if not topic:
            raise InvalidInputError("Say what this question is about.")
        question_type = values.get("type") or slot.type
        level = values.get("cognitive_level") or slot.cognitive_level
        if question_type not in QUESTION_TYPES or level not in COGNITIVE_LEVELS:
            raise InvalidInputError("Choose a question type and a Bloom's level from the lists.")
        if question_type == "mcq" and level == "create":
            raise InvalidInputError("A multiple-choice question can't assess the Create level. Choose another type or level.")
        difficulty = values.get("difficulty") or slot.difficulty
        if difficulty not in ("easy", "medium", "hard"):
            raise InvalidInputError("Choose easy, medium or hard.")
        points = float(values.get("points") or slot.points)
        if not 0 < points <= 1000 or points * 2 != int(points * 2):
            raise InvalidInputError("Use whole or half points, more than 0.")
        target = str(values.get("assessment_target") or "").strip()
        own = str(values.get("professor_question") or "").strip()

        updated = slot.model_copy(deep=True)
        # A new subject no longer rests on the slides planned for the old one: the writer finds its own.
        if topic != slot.topic or (own and own != slot.professor_question):
            updated.source_pages, updated.knowledge_refs, updated.source_hint = [], [], None
        if target != slot.assessment_target:
            updated.learning_objective = target or topic
        updated.topic, updated.assessment_target, updated.professor_question = topic, target, own
        updated.type, updated.cognitive_level, updated.difficulty, updated.points = question_type, level, difficulty, points
        plan = state.plan.model_copy(
            update={"questions": [updated if question.number == number else question for question in state.plan.questions]}
        )
        await self._apply(record.id, state, plan)
        return await self.get(record.id)

    async def _apply(self, assessment_id: str, state: PlanState, plan: ExamPlan) -> None:
        """Saves a changed plan. A written question whose plan changed becomes outdated; one whose
        planned question is gone is deleted (it has no place in the exam any more)."""
        changed = changed_numbers(state.plan, plan)
        exam = await self._exam_of(assessment_id, state)
        if exam is not None:
            full = await self.store.load(exam["id"])
            planned = {question.number for question in plan.questions}
            for row in full.questions:
                if row["position"] not in planned:
                    await self.store.delete_question(row["id"])
            written = {row["position"] for row in full.version_questions(full.primary_version["id"])}
            state.outdated = [number for number in {*state.outdated, *changed} if number in written and number in planned]
        state.plan = plan
        await self._save(assessment_id, state)

    # -- Writing the exam ---------------------------------------------------------------

    async def prepare_exam(self, assessment_id: str, *, replace: bool) -> dict[str, Any]:
        """The exam this plan is written into, created on first use (a previous exam is replaced only
        when the professor confirms it)."""
        record, state = await self._state(assessment_id)
        exam = await self._exam_of(record.id, state)
        if exam is not None:
            return exam
        existing = await self.store.exam_for_assessment(record.id)
        if existing:
            if not replace:
                raise ConflictError("This assessment already has an exam. Replace it to start again.", code="exam_exists")
            await require_idle(self.db, record.id, kinds=("generation", "review", "versioning"), doing="replace the exam")
            await self.store.delete_exam(existing["id"])
        envelope = {key: value for key, value in (record.exam_spec or {}).items() if key != QUESTION_PLAN_KEY}
        exam = await self.store.create_exam(
            record.id, mode="full", spec_envelope=envelope, enhanced_prompt=record.enhanced_prompt, status="building"
        )
        spec_versions = int((envelope.get("spec") or {}).get("versions") or 1)
        await self.store.create_versions(exam["id"], spec_versions)
        await self.store.create_sections(exam["id"], [(section.title, section.instructions) for section in state.plan.sections])
        await self.store.update_exam(exam["id"], {"plan": state.plan.model_dump()})
        await self.db.update(
            "exam_projects",
            {"generation_mode": "full"},
            filters=[("id", "eq", record.id), ("professor_id", "eq", self.db.professor_id)],
            columns="id",
        )
        state.exam_id = exam["id"]
        await self._save(record.id, state)
        return exam

    async def _clear(self, exam_id: str, numbers: set[int]) -> None:
        """Deletes the written questions at these planned numbers, in every version."""
        full = await self.store.load(exam_id)
        for row in full.questions:
            if row["position"] in numbers:
                await self.store.delete_question(row["id"])

    async def write(self, assessment_id: str, number: int, run: RunContext) -> None:
        """Writes (or rewrites) one planned question. The quality check runs when the exam is finished."""
        ctx = await load_planning_context(self.db, assessment_id)
        _, state = await self._state(assessment_id)
        slot = state.slot(number)
        exam = await self._exam_of(assessment_id, state)
        if exam is None:
            raise ConflictError("This plan's exam no longer exists. Write the question again.", code="exam_missing")
        await self._clear(exam["id"], {number})
        await self.store.update_exam(exam["id"], {"plan": state.plan.model_dump(), "status": "building", "error_message": None})
        async with timed("question_plan.write", self.usage):
            await ExamGenerationService(self.db, self.ai, self.usage).write_slots(ctx, exam["id"], state.plan, [slot], run=run)
        _, latest = await self._state(assessment_id)
        latest.outdated = [item for item in latest.outdated if item != number]
        await self._save(assessment_id, latest)

    async def finish(self, assessment_id: str, run: RunContext) -> None:
        """Writes every question still missing (and rewrites outdated ones), then the other versions and
        the quality check: the same steps as a full generation, from this plan."""
        _, state = await self._state(assessment_id)
        exam = await self._exam_of(assessment_id, state)
        if exam is None:
            raise ConflictError("This plan's exam no longer exists. Generate it again.", code="exam_missing")
        await self._clear(exam["id"], set(state.outdated))
        await self.store.update_exam(exam["id"], {"plan": state.plan.model_dump(), "status": "generating", "error_message": None})
        state.outdated = []
        await self._save(assessment_id, state)
        await ExamGenerationService(self.db, self.ai, self.usage).run_full(assessment_id, exam["id"], run)
