"""The question plan: the professor reads it, changes it, writes questions one at a time, and finishes
the exam from the same page."""

import asyncio

from app.ai.prompts import DataFramer, generator_instructions, planner_instructions
from app.schemas.ai import ExamPlan
from app.services.jobs import job_runner
from app.services.question_plan import changed_numbers, stop_planning
from tests.helpers import auth, wait_for_run
from tests.seed import seed_assessment, seed_document
from tests.test_workflow import interpret_and_approve


def run(coroutine):
    return asyncio.run(coroutine)


def interpreted(api, supabase, db_a, **settings) -> str:
    """An improved prompt, on screen and not confirmed yet."""
    assessment = run(seed_assessment(db_a, **{"duration_minutes": 90, **settings}))
    run(seed_document(supabase, db_a, assessment["id"]))
    started = api.post(f"/api/assessments/{assessment['id']}/interpretation", headers=auth("token-a"))
    assert wait_for_run(api, started.json()["run"]["id"], "token-a")["status"] == "succeeded"
    return assessment["id"]


def approved(api, supabase, db_a) -> str:
    assessment = run(seed_assessment(db_a, duration_minutes=90))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    return assessment["id"]


def make_plan(api, assessment_id: str, token: str = "token-a") -> dict:
    started = api.post(f"/api/assessments/{assessment_id}/question-plan", headers=auth(token))
    assert started.status_code == 202, started.text
    assert wait_for_run(api, started.json()["run"]["id"], token)["status"] == "succeeded"
    return get_plan(api, assessment_id, token)


def get_plan(api, assessment_id: str, token: str = "token-a") -> dict:
    return api.get(f"/api/assessments/{assessment_id}/question-plan", headers=auth(token)).json()


def write(api, assessment_id: str, number: int, **body) -> dict:
    started = api.post(f"/api/assessments/{assessment_id}/question-plan/{number}/question", json=body, headers=auth())
    assert started.status_code == 202, started.text
    finished = wait_for_run(api, started.json()["run"]["id"])
    assert finished["status"] == "succeeded", finished
    return get_plan(api, assessment_id)


def finish(api, assessment_id: str, **body) -> dict:
    started = api.post(f"/api/assessments/{assessment_id}/question-plan/exam", json=body, headers=auth())
    assert started.status_code == 202, started.text
    finished = wait_for_run(api, started.json()["run"]["id"])
    assert finished["status"] == "succeeded", finished
    return api.get(f"/api/assessments/{assessment_id}/exam", headers=auth()).json()


def edit_body(question: dict, **changes) -> dict:
    return {
        "type": question["type"],
        "difficulty": question["difficulty"],
        "cognitiveLevel": question["cognitiveLevel"],
        "points": question["points"],
        "topic": question["topic"],
        "assessmentTarget": question["assessmentTarget"],
        "professorQuestion": question["professorQuestion"],
        **changes,
    }


def edit(api, assessment_id: str, number: int, **changes):
    question = next(q for q in get_plan(api, assessment_id)["questions"] if q["number"] == number)
    return api.patch(
        f"/api/assessments/{assessment_id}/question-plan/{number}", json=edit_body(question, **changes), headers=auth()
    )


# -- Making the plan ---------------------------------------------------------------------------


def test_there_is_no_plan_until_one_is_asked_for(api, supabase, db_a):
    plan = get_plan(api, approved(api, supabase, db_a))
    assert plan["status"] == "none" and plan["questions"] == [] and plan["exam"] is None


def test_planning_needs_an_improved_prompt(api, supabase, db_a):
    assessment = run(seed_assessment(db_a))
    started = api.post(f"/api/assessments/{assessment['id']}/question-plan", headers=auth("token-a"))
    assert started.status_code == 409 and started.json()["error"]["code"] == "spec_missing"


def test_planning_starts_while_the_prompt_is_only_on_screen(api, supabase, db_a):
    assessment_id = interpreted(api, supabase, db_a)
    assert make_plan(api, assessment_id)["status"] == "ready"
    # Reading the prompt confirms nothing, and nothing is written before it's confirmed.
    assert api.get(f"/api/assessments/{assessment_id}/interpretation", headers=auth()).json()["status"] == "draft"
    refused = api.post(f"/api/assessments/{assessment_id}/question-plan/1/question", json={}, headers=auth())
    assert refused.status_code == 409
    # Confirming it (Next) keeps the plan.
    api.post(f"/api/assessments/{assessment_id}/interpretation/approval", headers=auth())
    assert get_plan(api, assessment_id)["status"] == "ready"


def test_a_new_prompt_stops_the_plan_being_made_from_the_old_one(api, supabase, db_a):
    assessment_id = interpreted(api, supabase, db_a)

    async def stopped_while_running() -> dict:
        began = asyncio.Event()
        finished = False

        async def work(_run) -> None:
            nonlocal finished
            began.set()
            await asyncio.sleep(30)
            finished = True

        row = await job_runner.start(db_a, assessment_id=assessment_id, kind="interpretation", work=work, stage="planning")
        await began.wait()
        await stop_planning(db_a, assessment_id)
        assert not finished
        return row

    row = run(stopped_while_running())
    assert next(item for item in supabase.tables["ai_runs"] if item["id"] == row["id"])["status"] == "failed"
    # Nothing is left waiting: planning can start again at once.
    assert make_plan(api, assessment_id)["status"] == "ready"


def test_an_edited_prompt_or_new_interpretation_drops_the_plan(api, supabase, db_a):
    assessment_id = approved(api, supabase, db_a)
    make_plan(api, assessment_id)
    edited = api.patch(
        f"/api/assessments/{assessment_id}/interpretation",
        json={"enhancedPrompt": "A different brief for this assessment, long enough to guide an author."},
        headers=auth(),
    )
    assert edited.status_code == 200, edited.text
    assert get_plan(api, assessment_id)["status"] == "none"

    make_plan(api, assessment_id)
    again = api.post(f"/api/assessments/{assessment_id}/interpretation", headers=auth())
    assert wait_for_run(api, again.json()["run"]["id"])["status"] == "succeeded"
    assert get_plan(api, assessment_id)["status"] == "none"


def test_the_plan_shows_each_question_and_the_targets(api, supabase, db_a):
    plan = make_plan(api, approved(api, supabase, db_a))
    first = plan["questions"][0]
    assert first["number"] == 1 and first["professorQuestion"] == "" and first["written"] is None
    assert {"type", "difficulty", "cognitiveLevel", "points", "estimatedMinutes", "topic", "assessmentTarget"} <= set(first)
    assert plan["summary"]["questionCount"] == len(plan["questions"]) and plan["summary"]["totalPoints"] > 0
    assert isinstance(plan["distributionWarnings"], list)


# -- Changing the plan ---------------------------------------------------------------------------


def test_the_professor_edits_a_planned_question(api, supabase, db_a):
    assessment_id = approved(api, supabase, db_a)
    make_plan(api, assessment_id)
    saved = edit(
        api, assessment_id, 2, topic="Virtual memory", assessmentTarget="Trace a page fault", difficulty="hard", points=15
    )
    assert saved.status_code == 200, saved.text
    second = saved.json()["questions"][1]
    assert (second["topic"], second["assessmentTarget"], second["difficulty"], second["points"]) == (
        "Virtual memory",
        "Trace a page fault",
        "hard",
        15,
    )
    # The targets follow at once (computed in code): the total is now 105 of 100.
    assert saved.json()["summary"]["totalPoints"] == sum(q["points"] for q in saved.json()["questions"])


def test_impossible_edits_are_refused(api, supabase, db_a):
    assessment_id = approved(api, supabase, db_a)
    make_plan(api, assessment_id)
    assert edit(api, assessment_id, 1, topic="   ").status_code in (400, 422)
    assert edit(api, assessment_id, 1, type="mcq", cognitiveLevel="create").status_code == 422
    assert edit(api, assessment_id, 1, points=2.3).status_code == 422
    body = edit_body(get_plan(api, assessment_id)["questions"][0])
    assert api.patch(f"/api/assessments/{assessment_id}/question-plan/99", json=body, headers=auth()).status_code == 404


def test_the_ai_changes_only_what_the_professor_asks(api, supabase, db_a, ai):
    assessment_id = approved(api, supabase, db_a)
    before = make_plan(api, assessment_id)
    started = api.post(
        f"/api/assessments/{assessment_id}/question-plan/changes", json={"instruction": "Linked lists"}, headers=auth()
    )
    assert started.status_code == 202, started.text
    assert wait_for_run(api, started.json()["run"]["id"])["status"] == "succeeded"
    after = get_plan(api, assessment_id)
    assert after["questions"][1]["topic"] == "Linked lists"
    assert [q["topic"] for q in after["questions"] if q["number"] != 2] == [
        q["topic"] for q in before["questions"] if q["number"] != 2
    ]
    # The model saw the current plan and the professor's words.
    call = ai.calls_for("change_plan")[0]
    assert "Linked lists" in call["input"] and before["questions"][0]["topic"] in call["input"]


def test_changed_numbers_compares_what_a_question_asks():
    def plan(*topics: str) -> ExamPlan:
        return ExamPlan.model_validate(
            {
                "sections": [{"title": "A", "instructions": None}],
                "questions": [
                    {
                        "number": number,
                        "section_index": 0,
                        "type": "short_answer",
                        "difficulty": "easy",
                        "points": 5,
                        "topic": topic,
                        "learning_objective": "x",
                        "cognitive_level": "remember",
                        "source_hint": None,
                        "estimated_minutes": 3,
                        "figure_document_id": None,
                    }
                    for number, topic in enumerate(topics, start=1)
                ],
                "rationale": "",
                "coverage_notes": "",
                "warnings": [],
            }
        )

    assert changed_numbers(plan("a", "b", "c"), plan("a", "B", "c")) == {2}
    assert changed_numbers(plan("a", "b", "c"), plan("a", "b")) == {3}


# -- Writing questions one at a time, and finishing -------------------------------------------------------


def test_one_question_is_written_from_its_plan_and_shown_under_it(api, supabase, db_a, ai):
    assessment_id = approved(api, supabase, db_a)
    make_plan(api, assessment_id)
    plan = write(api, assessment_id, 3)
    third = plan["questions"][2]
    assert third["written"] is not None and third["written"]["number"] == 3
    # The question only: its answer key is written when the professor asks.
    assert not third["written"]["hasAnswerKey"] and third["written"]["answer"] is None
    assert "QUESTIONS ONLY" in ai.calls_for("generate_questions")[0]["instructions"]
    assert all(q["written"] is None for q in plan["questions"] if q["number"] != 3)
    assert plan["exam"]["status"] == "building"
    # Only that question: no other question, no other version, no key, no quality check yet.
    assert len(ai.calls_for("generate_questions")) == 1
    assert not ai.calls_for("review_exam") and not ai.calls_for("write_variants") and not ai.calls_for("write_answer_keys")

    # Its key, on request, from the question as saved.
    keyed = api.post(f"/api/questions/{third['written']['id']}/solution", headers=auth())
    assert keyed.status_code == 200, keyed.text
    assert keyed.json()["question"]["hasAnswerKey"] and not keyed.json()["question"]["answerKeyOutdated"]
    assert len(ai.calls_for("write_answer_keys")) == 1


def test_the_professors_own_question_is_what_gets_written(api, supabase, db_a, ai):
    assessment_id = approved(api, supabase, db_a)
    make_plan(api, assessment_id)
    edit(api, assessment_id, 1, professorQuestion="What does a\nTLB cache?")
    write(api, assessment_id, 1)
    assert "the professor's own question: What does a TLB cache?" in ai.calls_for("generate_questions")[0]["input"]
    assert "THE PROFESSOR'S OWN QUESTION" in generator_instructions(DataFramer())
    assert "Leave professor_question empty" in planner_instructions(DataFramer())


def test_changing_a_written_questions_plan_marks_it_until_it_is_rewritten(api, supabase, db_a):
    assessment_id = approved(api, supabase, db_a)
    make_plan(api, assessment_id)
    first_id = write(api, assessment_id, 1)["questions"][0]["written"]["id"]
    plan = edit(api, assessment_id, 1, topic="Something else").json()
    # Nothing is rewritten by itself: the question stays, marked.
    assert plan["questions"][0]["outdated"] is True and plan["questions"][0]["written"]["id"] == first_id
    # An unwritten question is never "outdated".
    assert edit(api, assessment_id, 2, topic="Another").json()["questions"][1]["outdated"] is False

    rewritten = write(api, assessment_id, 1)["questions"][0]
    assert rewritten["outdated"] is False and rewritten["written"]["id"] != first_id
    exam = api.get(f"/api/assessments/{assessment_id}/exam", headers=auth()).json()
    assert len(exam["questions"]) == 1


def test_finishing_writes_the_rest_and_rewrites_outdated_ones_without_keys(api, supabase, db_a, ai):
    assessment_id = approved(api, supabase, db_a)
    planned = make_plan(api, assessment_id)
    plans_made = len(ai.calls_for("plan_exam"))
    kept = write(api, assessment_id, 1)["questions"][0]["written"]["id"]
    outdated = write(api, assessment_id, 2)["questions"][1]["written"]["id"]
    edit(api, assessment_id, 2, topic="Something else")

    exam = finish(api, assessment_id)
    assert exam["status"] == "ready" and not ai.calls_for("review_exam") and not ai.calls_for("write_answer_keys")
    assert not any(question["hasAnswerKey"] for question in exam["questions"])
    ids = {q["position"]: q["id"] for q in exam["questions"]}
    assert len(ids) == len(planned["questions"])
    assert ids[1] == kept and ids[2] != outdated
    # The plan the professor worked on is the one written: never planned again.
    assert len(ai.calls_for("plan_exam")) == plans_made
    assert get_plan(api, assessment_id)["exam"]["status"] == "ready"


def test_an_exam_made_another_way_is_replaced_only_when_the_professor_confirms(api, supabase, db_a):
    assessment_id = approved(api, supabase, db_a)
    started = api.post(f"/api/assessments/{assessment_id}/exam", json={"mode": "full"}, headers=auth())
    assert wait_for_run(api, started.json()["run"]["id"])["status"] == "succeeded"
    make_plan(api, assessment_id)

    refused = api.post(f"/api/assessments/{assessment_id}/question-plan/1/question", json={}, headers=auth())
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "exam_exists"
    plan = write(api, assessment_id, 1, replace=True)
    exam = api.get(f"/api/assessments/{assessment_id}/exam", headers=auth()).json()
    assert exam["id"] == plan["exam"]["id"] and len(exam["questions"]) == 1


def test_the_plan_cant_change_while_a_question_is_being_written(api, supabase, db_a):
    assessment_id = approved(api, supabase, db_a)
    make_plan(api, assessment_id)
    run(db_a.insert("ai_runs", {"exam_project_id": assessment_id, "kind": "generation", "stage": "generating_questions"}))
    assert edit(api, assessment_id, 1, topic="Other").status_code == 409
    change = api.post(f"/api/assessments/{assessment_id}/question-plan/changes", json={"instruction": "x"}, headers=auth())
    assert change.status_code == 409


def test_another_professor_cant_reach_the_plan(api, supabase, db_a):
    assessment_id = approved(api, supabase, db_a)
    make_plan(api, assessment_id)
    body = edit_body(get_plan(api, assessment_id)["questions"][0])
    other = auth("token-b")
    assert api.get(f"/api/assessments/{assessment_id}/question-plan", headers=other).status_code == 404
    assert api.patch(f"/api/assessments/{assessment_id}/question-plan/1", json=body, headers=other).status_code == 404
    assert api.post(f"/api/assessments/{assessment_id}/question-plan/1/question", json={}, headers=other).status_code == 404
    assert api.post(f"/api/assessments/{assessment_id}/question-plan/exam", json={}, headers=other).status_code == 404


# -- The whole flow, as the professor goes through it ---------------------------------------------------


def test_prompt_then_plan_then_some_questions_then_the_rest(api, supabase, db_a, ai):
    """Improve with AI → the plan is prepared while the prompt is read (never twice) → an edited prompt is planned
    again → Next → a topic is edited → two questions are generated → Generate full exam writes only the rest."""
    assessment_id = interpreted(api, supabase, db_a)

    # Prepared in the background while the prompt is read; a second request while it runs is refused.
    run(db_a.insert("ai_runs", {"exam_project_id": assessment_id, "kind": "interpretation", "stage": "planning"}))
    again = api.post(f"/api/assessments/{assessment_id}/question-plan", headers=auth())
    assert again.status_code == 409 and again.json()["error"]["code"] == "job_running"
    supabase.tables["ai_runs"][-1]["status"] = "failed"
    make_plan(api, assessment_id)

    # Editing the prompt drops that plan; the next one is made from the edited prompt.
    edited = "Write a midterm on gradient descent and regularization, with plain wording."
    api.patch(f"/api/assessments/{assessment_id}/interpretation", json={"enhancedPrompt": edited}, headers=auth())
    assert get_plan(api, assessment_id)["status"] == "none"
    plan = make_plan(api, assessment_id)
    assert edited in ai.calls_for("plan_exam")[-1]["input"]

    # Next confirms the prompt and keeps the plan.
    api.post(f"/api/assessments/{assessment_id}/interpretation/approval", headers=auth())
    planned = [question["number"] for question in plan["questions"]]
    assert edit(api, assessment_id, 2, topic="Learning rate schedules").status_code == 200

    first = write(api, assessment_id, 1)["questions"][0]["written"]
    third = write(api, assessment_id, 3)["questions"][2]["written"]
    exam = finish(api, assessment_id)

    first_version = exam["versions"][0]["id"]
    by_position = {q["position"]: q for q in exam["questions"] if q["versionId"] == first_version}
    assert sorted(by_position) == planned  # every planned question, at its planned number
    assert by_position[1]["id"] == first["id"] and by_position[3]["id"] == third["id"]  # kept, not rewritten
    written_after = [call["input"] for call in ai.calls_for("generate_questions")[2:]]
    assert any("Learning rate schedules" in text for text in written_after)  # the edited topic was used
    targets = [text.split("WRITE THESE QUESTIONS NOW", 1)[1] for text in written_after]
    assert not any(line.startswith(("1.", "3.")) for text in targets for line in text.splitlines()[1:])
    assert exam["status"] == "ready"
