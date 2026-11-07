"""Cost: routine work runs on the light model, exam content on the reasoning model, and
reasoning effort is only as high as the work needs."""

import pytest

from app.ai.openai_service import Usage
from app.core.config import settings
from app.schemas.ai import ExamSpec
from app.services.exam_generation import fit_points, plan_effort
from tests.test_latency import ready_exam
from tests.test_openai_service import response, service
from tests.test_planning import grouped_plan

# Purposes that write or check exam content: always the reasoning model.
EXAM_CONTENT = {"interpret_assessment", "plan_exam", "replan_exam", "generate_questions", "write_variants", "review_exam"}
ROUTINE = {"summarize_document", "analyze_previous_exams", "analyze_lecture_segment", "synthesize_lecture"}
ROUTINE |= {"read_image", "transcribe_pdf", "read_figures", "setup_chat", "builder_chat"}


def test_routine_work_uses_the_light_model_and_exam_content_the_reasoning_model(api, supabase, db_a, ai):
    ready_exam(api, supabase, db_a)
    seen = {call["purpose"] for call in ai.calls}
    assert seen & EXAM_CONTENT and seen & ROUTINE
    for call in ai.calls:
        if call["purpose"] in EXAM_CONTENT:
            assert call.get("model") is None, call["purpose"]  # the default: the reasoning model
        elif call["purpose"] in ROUTINE:
            assert call.get("model") == settings.light_model, call["purpose"]


@pytest.mark.anyio
async def test_the_tool_loop_can_run_on_another_model():
    ai, responses = service(response(None))

    async def execute(name, arguments):
        return {}

    await ai.run_tool_loop(
        purpose="t", instructions="i", messages=[], tools=[], execute=execute, usage=Usage(), model="gpt-6-luna"
    )
    assert responses.requests[0]["model"] == "gpt-6-luna"


def spec(**values) -> ExamSpec:
    return ExamSpec.model_construct(**{"question_count_min": None, "question_count_max": None, **values})


def test_small_exams_are_planned_with_medium_effort():
    assert plan_effort(spec(question_count_max=10)) == "medium"
    assert plan_effort(spec(question_count_min=8)) == "medium"
    assert plan_effort(spec(question_count_max=11)) == "high"
    assert plan_effort(spec()) == "high"  # unknown size: no shortcuts


def test_points_are_fitted_to_the_total_in_code():
    plan = grouped_plan()  # nine questions of 10 points: 90
    fitted = fit_points(plan, 100)
    points = [q.points for q in fitted.questions]
    assert sum(points) == 100 and all(p.is_integer() and p >= 1 for p in points)
    assert max(points) - min(points) <= 1  # equal questions stay (nearly) equal
    # Already right, or impossible to fit in whole or half points: unchanged.
    assert fit_points(plan, 90) is plan and fit_points(plan, None) is plan
    assert fit_points(plan, 4) is plan


def test_fitting_points_keeps_their_proportions():
    plan = grouped_plan()
    weights = [5, 5, 5, 10, 10, 10, 20, 20, 15]  # 100
    plan = plan.model_copy(
        update={"questions": [q.model_copy(update={"points": w}) for q, w in zip(plan.questions, weights, strict=True)]}
    )
    fitted = fit_points(plan, 50)
    assert sum(q.points for q in fitted.questions) == 50
    assert all(abs(q.points - w / 2) <= 1 for q, w in zip(fitted.questions, weights, strict=True))
