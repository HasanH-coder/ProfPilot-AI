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

The plan is shown as it is made: its sections (each case, complete) first, then each question as soon as
the planner has written it (status "planning"). A planned question can be changed or written at once; the
planner keeps those ("touched") exactly as the professor left them, and plans and orders the rest around
them. Every change to the stored plan happens under one lock per assessment, so the planner's progress
and the professor's changes never overwrite each other.
"""

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.ai.openai_service import OpenAIService, Usage
from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.core.timing import timed
from app.db.supabase import Database
from app.domain import distribution
from app.domain.exam_style import TASKS, style_numbers
from app.schemas.ai import COGNITIVE_LEVELS, QUESTION_TYPES, ExamPlan, ExamSpec, PlannedQuestion, PlannedSection
from app.services.assessment_context import AssessmentContextService, AssessmentRecord
from app.services.exam_generation import (
    ExamGenerationService,
    ExamPlanningService,
    GenerationContext,
    fit_points,
    keep_questions,
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


def empty_plan(sections: list[PlannedSection] | None = None, questions: list[PlannedQuestion] | None = None) -> ExamPlan:
    """A plan still being made: what is planned so far."""
    return ExamPlan(sections=sections or [], questions=questions or [], rationale="", coverage_notes="", warnings=[])


@dataclass
class PlanState:
    plan: ExamPlan
    # The exam written from this plan, once the first question is written.
    exam_id: str | None = None
    # Planned numbers whose written question no longer matches its plan.
    outdated: list[int] = field(default_factory=list)
    # "planning" while the planner is still adding questions; "ready" once it finished.
    status: str = "ready"
    # The planning job that made this plan.
    run_id: str | None = None
    # Numbers the professor changed or wrote while it was being planned: the planner keeps them as they are.
    touched: list[int] = field(default_factory=list)

    @classmethod
    def of(cls, record: AssessmentRecord) -> "PlanState | None":
        stored = (record.exam_spec or {}).get(QUESTION_PLAN_KEY)
        if not stored:
            return None
        return cls(
            plan=ExamPlan.model_validate(stored["plan"]),
            exam_id=stored.get("exam_id"),
            outdated=sorted(set(stored.get("outdated") or [])),
            status=stored.get("status") or "ready",
            run_id=stored.get("run_id"),
            touched=sorted(set(stored.get("touched") or [])),
        )

    def dump(self) -> dict[str, Any]:
        return {
            "plan": self.plan.model_dump(),
            "exam_id": self.exam_id,
            "outdated": sorted(set(self.outdated)),
            "status": self.status,
            "run_id": self.run_id,
            "touched": sorted(set(self.touched)),
        }

    def kept(self) -> dict[int, PlannedQuestion]:
        """The questions the professor changed or wrote while the plan was being made, by number."""
        return {question.number: question for question in self.plan.questions if question.number in self.touched}

    def slot(self, number: int) -> PlannedQuestion:
        found = next((question for question in self.plan.questions if question.number == number), None)
        if found is None:
            raise NotFoundError("This planned question doesn't exist.")
        return found


# One change to an assessment's stored plan at a time (in this server process, where the jobs run too).
_locks: dict[tuple[int, str], asyncio.Lock] = {}


def plan_lock(assessment_id: str) -> asyncio.Lock:
    """Held while a plan is read, changed and saved, so no change overwrites another made meanwhile."""
    key = (id(asyncio.get_running_loop()), assessment_id)
    return _locks.setdefault(key, asyncio.Lock())


async def stop_planning(db: Database, assessment_id: str) -> None:
    """Stops a plan still being made or changed: whatever replaced the prompt makes it pointless."""
    for stage in _PLAN_STAGES:
        await job_runner.stop(db, assessment_id=assessment_id, kind="interpretation", stage=stage)


async def require_idle(
    db: Database, assessment_id: str, *, kinds: tuple[str, ...], doing: str, while_planning: bool = False
) -> None:
    """Refuses while the plan is being made or changed, or a question is being written. `while_planning`:
    a plan still being made doesn't stop this (its planned questions can be changed and written)."""
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
        if while_planning and run["kind"] == "interpretation" and run.get("stage") == PLANNING_STAGE:
            continue
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

    async def _planning(self, state: PlanState) -> bool:
        """Whether the job making this plan is still running (it may have failed or been interrupted)."""
        if state.status != "planning" or not state.run_id:
            return False
        run = await self.db.select_one("ai_runs", columns=RUN_COLUMNS, filters=[("id", "eq", state.run_id)])
        return run is not None and run["status"] == "running" and not is_stale(run)

    async def get(self, assessment_id: str) -> dict[str, Any]:
        record = await self.context.get_assessment(assessment_id)
        state = PlanState.of(record)
        if state is None:
            return {"status": "none", "sections": [], "questions": [], "rationale": None, "warnings": [], "exam": None}
        status = state.status
        if status == "planning" and not await self._planning(state):
            status = "stopped"
        exam = await self._exam_of(record.id, state)
        written: dict[int, dict[str, Any]] = {}
        if exam is not None:
            full = await self.store.load(exam["id"])
            for row in full.version_questions(full.primary_version["id"]):
                written[row["position"]] = serialize_question(row, row["position"], full.revision_counts.get(row["id"], 0))
        spec = ExamSpec.model_validate(record.exam_spec["spec"]) if record.exam_spec and record.exam_spec.get("spec") else None
        summary = distribution.summarize(state.plan.questions)
        style = await self.context.get_style_profile(record.id)
        numbers = style_numbers(style[0] if style else None)
        planned_tasks = [question.task for question in state.plan.questions if question.task]
        return {
            "status": status,
            "sections": [section.model_dump() for section in state.plan.sections],
            "rationale": state.plan.rationale or None,
            "warnings": state.plan.warnings,
            "summary": summary,
            # Targets are compared once every question is planned.
            "distributionWarnings": distribution.compare(summary, spec) if status == "ready" else [],
            "expectedQuestions": (spec.question_count_min or spec.question_count_max) if spec and status != "ready" else None,
            "tasks": {task: Counter(planned_tasks)[task] for task in TASKS} if planned_tasks else None,
            "previousTasks": numbers.tasks if numbers else None,
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

    async def _current(self, assessment_id: str, ctx: GenerationContext) -> AssessmentRecord:
        """The assessment, while its prompt is still the one this plan is made from."""
        latest = await self.context.get_assessment(assessment_id)
        if latest.spec_status not in ("draft", "approved") or latest.enhanced_prompt != ctx.enhanced_prompt:
            raise ConflictError("Your prompt changed during planning. Confirm it again.", code="spec_stale")
        return latest

    async def create(self, assessment_id: str, run: RunContext) -> None:
        """Plans every question. The page shows the plan as it is made (the sections first, then each
        question once it is planned), and the professor's changes made meanwhile are kept."""
        ctx = await load_planning_context(self.db, assessment_id, require_approved=False)
        planner = ExamPlanningService(self.db, self.ai, self.usage)

        def mine(record: AssessmentRecord) -> PlanState | None:
            state = PlanState.of(record)
            return state if state is not None and state.run_id == run.run_id else None

        async def progress(sections: list[PlannedSection], questions: list[PlannedQuestion]) -> None:
            async with plan_lock(assessment_id):
                state = mine(await self._current(assessment_id, ctx))
                if state is None:
                    raise ConflictError("A newer plan replaced this one.", code="spec_stale")
                kept = state.kept()
                shown = [
                    kept.get(question.number) or question.model_copy(update={"professor_question": ""}) for question in questions
                ]
                # A question the professor kept stays even if this text doesn't reach it (a restarted stream).
                shown += [kept[number] for number in sorted(kept) if number > len(shown)]
                state.plan = empty_plan(sections, shown)
                await self._save(assessment_id, state)
            await run.heartbeat()

        async def touched() -> dict[int, PlannedQuestion]:
            state = mine(await self.context.get_assessment(assessment_id))
            return state.kept() if state else {}

        # The plan being made replaces any earlier one at once: the page shows its progress from the start.
        async with plan_lock(assessment_id):
            await self._current(assessment_id, ctx)
            await self._save(assessment_id, PlanState(plan=empty_plan(), status="planning", run_id=run.run_id))
        async with timed("question_plan", self.usage):
            plan = await planner.plan(ctx, on_progress=progress, touched=touched)
        await self._settle(assessment_id, ctx, planner, plan, mine)

    async def _settle(
        self,
        assessment_id: str,
        ctx: GenerationContext,
        planner: ExamPlanningService,
        plan: ExamPlan,
        mine: Callable[[AssessmentRecord], PlanState | None],
    ) -> None:
        """Saves the finished plan with the professor's questions as they left them: the points of the others
        fit the total and their order is mixed around the kept ones. A written question whose plan changed
        becomes outdated, and the exam's sections follow the plan's."""
        async with plan_lock(assessment_id):
            latest = await self._current(assessment_id, ctx)
            state = mine(latest)
            if state is None:
                raise ConflictError("A newer plan replaced this one.", code="spec_stale")
            kept = state.kept()
            for question in plan.questions:
                if question.number not in kept:
                    question.professor_question = ""
            final = fit_points(keep_questions(plan, kept, state.plan), ctx.spec.total_points, set(kept))
            final = planner.order(final, ctx, set(kept))
            state.status, state.touched = "ready", []
            await self._apply(assessment_id, state, final)
            exam = await self._exam_of(assessment_id, state)
            if exam is not None:
                await self.store.sync_sections(exam["id"], [(section.title, section.instructions) for section in final.sections])
                await self.store.update_exam(exam["id"], {"plan": final.model_dump()})

    async def change(self, assessment_id: str, instruction: str, run: RunContext) -> None:
        """Applies the professor's request to the plan, keeping everything else as it was."""
        _, state = await self._state(assessment_id)
        if state.status != "ready":
            raise ConflictError("Wait until every question is planned to change the plan.", code="plan_incomplete")
        ctx = await load_planning_context(self.db, assessment_id, require_approved=False)
        async with timed("question_plan.change", self.usage):
            changed = await ExamPlanningService(self.db, self.ai, self.usage).change(ctx, state.plan, instruction)
        async with plan_lock(assessment_id):
            latest, current = await self._state(assessment_id)
            if latest.enhanced_prompt != ctx.enhanced_prompt:
                raise ConflictError("Your prompt changed while the plan was being changed. Try again.", code="spec_stale")
            await self._apply(assessment_id, current, changed)

    async def edit(self, assessment_id: str, number: int, values: dict[str, Any]) -> dict[str, Any]:
        """The professor changes a planned question by hand, or gives their own question (also while the rest
        of the plan is still being made: the planner keeps it as they left it)."""
        async with plan_lock(assessment_id):
            record, state = await self._state(assessment_id)
            await require_idle(
                self.db, record.id, kinds=("interpretation", "generation"), doing="change the plan", while_planning=True
            )
            slot = state.slot(number)
            plan = state.plan.model_copy(
                update={
                    "questions": [
                        edited(slot, values) if question.number == number else question for question in state.plan.questions
                    ]
                }
            )
            if state.status == "planning":
                state.touched = [*state.touched, number]
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

    async def require_complete(self, assessment_id: str) -> None:
        """Refuses until every question is planned (the full exam is written from the whole plan)."""
        _, state = await self._state(assessment_id)
        if state.status != "ready":
            raise ConflictError(
                "ProfPilot is still planning the questions. Generate the full exam once every question is planned."
                if await self._planning(state)
                else "Planning stopped before every question was planned. Make a new plan first.",
                code="plan_incomplete",
            )

    async def touch(self, assessment_id: str, number: int) -> None:
        """Before one planned question is written: it must exist, and while the plan is still being made the
        planner keeps it (and the question written from it) as it is."""
        async with plan_lock(assessment_id):
            _, state = await self._state(assessment_id)
            state.slot(number)
            if state.status == "planning" and number not in state.touched:
                state.touched = [*state.touched, number]
                await self._save(assessment_id, state)

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
        # A plan still being made already has every section (each case is planned before its questions).
        await self.store.create_sections(exam["id"], [(section.title, section.instructions) for section in state.plan.sections])
        await self.store.update_exam(exam["id"], {"plan": state.plan.model_dump()})
        await self.db.update(
            "exam_projects",
            {"generation_mode": "full"},
            filters=[("id", "eq", record.id), ("professor_id", "eq", self.db.professor_id)],
            columns="id",
        )
        async with plan_lock(record.id):
            _, latest = await self._state(record.id)
            latest.exam_id = exam["id"]
            await self._save(record.id, latest)
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
        async with plan_lock(assessment_id):
            _, latest = await self._state(assessment_id)
            latest.outdated = [item for item in latest.outdated if item != number]
            await self._save(assessment_id, latest)

    async def finish(self, assessment_id: str, run: RunContext) -> None:
        """Writes every question still missing (and rewrites outdated ones), then the other versions and
        the quality check: the same steps as a full generation, from this plan."""
        await self.require_complete(assessment_id)
        _, state = await self._state(assessment_id)
        exam = await self._exam_of(assessment_id, state)
        if exam is None:
            raise ConflictError("This plan's exam no longer exists. Generate it again.", code="exam_missing")
        await self._clear(exam["id"], set(state.outdated))
        await self.store.update_exam(exam["id"], {"plan": state.plan.model_dump(), "status": "generating", "error_message": None})
        async with plan_lock(assessment_id):
            _, latest = await self._state(assessment_id)
            latest.outdated = []
            await self._save(assessment_id, latest)
        await ExamGenerationService(self.db, self.ai, self.usage).run_full(assessment_id, exam["id"], run)


def edited(slot: PlannedQuestion, values: dict[str, Any]) -> PlannedQuestion:
    """A planned question as the professor changed it by hand (or with their own question)."""
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
    return updated
