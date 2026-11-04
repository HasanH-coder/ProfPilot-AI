"""The question plan the professor reviews before the exam is written.

The planner lists every question (its topic, type, difficulty, what students do). Here it runs early,
as soon as the improved prompt is on screen, so it is usually ready by the time the professor confirms
it; they can then change any question or give their own. It is kept next to the spec in the assessment's
envelope, so a new interpretation or an edited brief drops it (and stops one still being made), and exam
creation copies it to the exam, where generation uses it instead of planning again.
"""

from typing import Any

from app.ai.openai_service import OpenAIService, Usage
from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.core.timing import timed
from app.db.supabase import Database
from app.schemas.ai import ExamPlan
from app.services.assessment_context import AssessmentContextService, AssessmentRecord
from app.services.exam_generation import ExamPlanningService, load_planning_context
from app.services.jobs import RunContext, job_runner
from app.services.prompt_interpreter import QUESTION_PLAN_KEY

# Tells planning apart from an interpretation, the kind of job it runs as: it must not overlap one, but
# the professor reads the prompt while it runs, and whatever replaces the prompt stops it.
PLANNING_STAGE = "planning"


async def stop_planning(db: Database, assessment_id: str) -> None:
    await job_runner.stop(db, assessment_id=assessment_id, kind="interpretation", stage=PLANNING_STAGE)


def _serialize(plan: ExamPlan | None) -> dict[str, Any]:
    """The plan as the professor sees it. The planner's warnings are notes for itself, so they stay out."""
    if plan is None:
        return {"status": "none", "questions": [], "rationale": None}
    return {
        "status": "ready",
        "questions": [question.model_dump() for question in plan.questions],
        "rationale": plan.rationale,
    }


def _stored(record: AssessmentRecord) -> ExamPlan | None:
    stored = (record.exam_spec or {}).get(QUESTION_PLAN_KEY)
    return ExamPlan.model_validate(stored) if stored else None


class QuestionPlanService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage | None = None) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage or Usage()
        self.context = AssessmentContextService(db)

    async def _save(self, record: AssessmentRecord, plan: ExamPlan) -> None:
        await self.db.update(
            "exam_projects",
            {"exam_spec": {**(record.exam_spec or {}), QUESTION_PLAN_KEY: plan.model_dump()}},
            filters=[("id", "eq", record.id), ("professor_id", "eq", self.db.professor_id)],
            columns="id",
        )

    async def get(self, assessment_id: str) -> dict[str, Any]:
        return _serialize(_stored(await self.context.get_assessment(assessment_id)))

    async def create(self, assessment_id: str, run: RunContext) -> None:
        ctx = await load_planning_context(self.db, assessment_id, require_approved=False)
        async with timed("question_plan", self.usage):
            plan = await ExamPlanningService(self.db, self.ai, self.usage).plan(ctx)
        for question in plan.questions:
            question.professor_question = ""
        latest = await self.context.get_assessment(assessment_id)
        if latest.spec_status not in ("draft", "approved") or latest.enhanced_prompt != ctx.enhanced_prompt:
            raise ConflictError("Your prompt changed during planning. Confirm it again.", code="spec_stale")
        await self._save(latest, plan)

    async def edit(
        self, assessment_id: str, number: int, *, topic: str, assessment_target: str, professor_question: str
    ) -> dict[str, Any]:
        record = await self.context.get_assessment(assessment_id)
        plan = _stored(record)
        slot = next((question for question in plan.questions if question.number == number), None) if plan else None
        if plan is None or slot is None:
            raise NotFoundError("This planned question doesn't exist.")
        topic, assessment_target, professor_question = topic.strip(), assessment_target.strip(), professor_question.strip()
        if not topic:
            raise InvalidInputError("Say what this question is about.")
        # A new subject no longer rests on the slides planned for the old one: the writer finds its own.
        if topic != slot.topic or (professor_question and professor_question != slot.professor_question):
            slot.source_pages, slot.knowledge_refs, slot.source_hint = [], [], None
        if assessment_target != slot.assessment_target:
            slot.learning_objective = assessment_target or topic
        slot.topic, slot.assessment_target, slot.professor_question = topic, assessment_target, professor_question
        await self._save(record, plan)
        return _serialize(plan)
