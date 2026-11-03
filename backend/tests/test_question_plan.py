"""The question plan: the professor reviews and changes it before the exam is written."""

import asyncio

from tests.helpers import auth, wait_for_run
from tests.seed import seed_assessment, seed_document
from tests.test_workflow import interpret_and_approve


def run(coroutine):
    return asyncio.run(coroutine)


def approved_assessment(api, supabase, db_a, **settings) -> str:
    assessment = run(seed_assessment(db_a, duration_minutes=90, **settings))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    return assessment["id"]


def make_plan(api, assessment_id: str, token: str = "token-a") -> dict:
    started = api.post(f"/api/assessments/{assessment_id}/question-plan", headers=auth(token))
    assert started.status_code == 202, started.text
    assert wait_for_run(api, started.json()["run"]["id"], token)["status"] == "succeeded"
    return api.get(f"/api/assessments/{assessment_id}/question-plan", headers=auth(token)).json()


def test_there_is_no_plan_until_one_is_asked_for(api, supabase, db_a):
    assessment_id = approved_assessment(api, supabase, db_a)
    plan = api.get(f"/api/assessments/{assessment_id}/question-plan", headers=auth("token-a")).json()
    assert plan == {"status": "none", "questions": [], "rationale": None}


def test_planning_needs_an_approved_brief(api, supabase, db_a):
    assessment = run(seed_assessment(db_a))
    started = api.post(f"/api/assessments/{assessment['id']}/question-plan", headers=auth("token-a"))
    assert started.status_code == 409
    assert started.json()["error"]["code"] == "spec_not_approved"


def test_the_plan_lists_each_question_for_the_professor(api, supabase, db_a):
    plan = make_plan(api, approved_assessment(api, supabase, db_a))
    assert plan["status"] == "ready"
    assert plan["rationale"]
    first = plan["questions"][0]
    assert first["number"] == 1
    assert {"type", "difficulty", "cognitiveLevel", "points", "estimatedMinutes", "topic", "assessmentTarget"} <= set(first)
    assert first["professorQuestion"] == ""


def test_generation_writes_the_reviewed_plan_with_the_professors_changes(api, supabase, db_a, ai):
    assessment_id = approved_assessment(api, supabase, db_a)
    make_plan(api, assessment_id)
    planned_calls = len(ai.calls_for("plan_exam"))

    edited = api.patch(
        f"/api/assessments/{assessment_id}/question-plan/2",
        json={"topic": "Virtual memory", "assessmentTarget": "Trace a page fault", "professorQuestion": ""},
        headers=auth("token-a"),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["questions"][1]["topic"] == "Virtual memory"
    own = api.patch(
        f"/api/assessments/{assessment_id}/question-plan/3",
        json={"topic": "topic 3", "assessmentTarget": "", "professorQuestion": "What does a\nTLB cache?"},
        headers=auth("token-a"),
    )
    assert own.json()["questions"][2]["professorQuestion"] == "What does a\nTLB cache?"

    started = api.post(f"/api/assessments/{assessment_id}/exam", json={"mode": "full"}, headers=auth("token-a"))
    assert started.status_code == 202, started.text
    assert wait_for_run(api, started.json()["run"]["id"], "token-a")["status"] == "succeeded"

    # Not planned a second time, and the writer is told what the professor decided.
    assert len(ai.calls_for("plan_exam")) == planned_calls
    written = "\n".join(str(call["input"]) for call in ai.calls_for("generate_questions"))
    assert "Virtual memory" in written and "Trace a page fault" in written
    assert "the professor's own question: What does a TLB cache?" in written

    exam = supabase.tables["exams"][0]
    assert exam["plan"]["questions"][1]["topic"] == "Virtual memory"
    assert "question_plan" not in exam["spec_snapshot"]


def test_changing_the_subject_drops_the_slides_planned_for_the_old_one(api, supabase, db_a):
    assessment_id = approved_assessment(api, supabase, db_a)
    make_plan(api, assessment_id)
    row = next(item for item in supabase.tables["exam_projects"] if item["id"] == assessment_id)
    planned = row["exam_spec"]["question_plan"]["questions"]
    planned[0]["source_pages"] = ["F1:p2"]
    planned[1]["source_pages"] = ["F1:p3"]
    first = planned[0]

    api.patch(
        f"/api/assessments/{assessment_id}/question-plan/1",
        json={"topic": first["topic"], "assessmentTarget": "A sharper task", "professorQuestion": ""},
        headers=auth("token-a"),
    )
    api.patch(
        f"/api/assessments/{assessment_id}/question-plan/2",
        json={"topic": "Something else", "assessmentTarget": "", "professorQuestion": ""},
        headers=auth("token-a"),
    )
    questions = row["exam_spec"]["question_plan"]["questions"]
    assert questions[0]["source_pages"] == ["F1:p2"]
    assert questions[1]["source_pages"] == []


def test_an_edited_brief_or_new_interpretation_drops_the_plan(api, supabase, db_a):
    assessment_id = approved_assessment(api, supabase, db_a)
    make_plan(api, assessment_id)
    edited = api.patch(
        f"/api/assessments/{assessment_id}/interpretation",
        json={"enhancedPrompt": "A different brief for this assessment, long enough to guide an author."},
        headers=auth("token-a"),
    )
    assert edited.status_code == 200, edited.text
    plan = api.get(f"/api/assessments/{assessment_id}/question-plan", headers=auth("token-a")).json()
    assert plan["status"] == "none"
    # Still a complete interpretation, to approve again.
    assert edited.json()["spec"] is not None


def test_a_missing_question_or_blank_topic_is_refused(api, supabase, db_a):
    assessment_id = approved_assessment(api, supabase, db_a)
    make_plan(api, assessment_id)
    body = {"topic": "x", "assessmentTarget": "", "professorQuestion": ""}
    assert api.patch(f"/api/assessments/{assessment_id}/question-plan/99", json=body, headers=auth("token-a")).status_code == 404
    blank = api.patch(f"/api/assessments/{assessment_id}/question-plan/1", json={**body, "topic": "   "}, headers=auth("token-a"))
    assert blank.status_code in (400, 422)


def test_another_professor_cant_reach_the_plan(api, supabase, db_a):
    assessment_id = approved_assessment(api, supabase, db_a)
    make_plan(api, assessment_id)
    body = {"topic": "x", "assessmentTarget": "", "professorQuestion": ""}
    assert api.get(f"/api/assessments/{assessment_id}/question-plan", headers=auth("token-b")).status_code == 404
    assert api.patch(f"/api/assessments/{assessment_id}/question-plan/1", json=body, headers=auth("token-b")).status_code == 404
    assert api.post(f"/api/assessments/{assessment_id}/question-plan", headers=auth("token-b")).status_code == 404


def test_a_ten_minute_assessment_is_a_mini_quiz_of_five_questions(api, supabase, db_a):
    assessment = run(seed_assessment(db_a, duration_minutes=10))
    run(seed_document(supabase, db_a, assessment["id"]))
    spec = interpret_and_approve(api, assessment["id"])["spec"]
    assert spec["assessment_type"] == "Mini quiz"
    assert (spec["question_count_min"], spec["question_count_max"]) == (5, 5)
    assert spec["question_count_source"] == "ai_assumption"


def test_a_number_the_professor_asked_for_stays_theirs(api, supabase, db_a, ai):
    from tests.model import well_behaved_interpretation

    def asks_for_seven(call):
        output = well_behaved_interpretation(call)
        output.exam_spec.question_count_min = output.exam_spec.question_count_max = 7
        output.exam_spec.question_count_source = "professor"
        return output

    ai.on("interpret_assessment", asks_for_seven)
    assessment = run(seed_assessment(db_a, duration_minutes=10, professor_prompt="Write seven questions."))
    spec = interpret_and_approve(api, assessment["id"])["spec"]
    assert (spec["question_count_min"], spec["question_count_max"]) == (7, 7)
    assert spec["question_count_source"] == "professor"
