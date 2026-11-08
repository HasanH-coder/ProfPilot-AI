"""The plan appears as it is made, each case before its questions; the professor works on planned questions
meanwhile; and the exam follows the professor's previous exams in its mix of tasks and its length, with
harder questions harder through their thinking, not their length."""

import asyncio
import threading

from app.ai.prompts import DataFramer, generator_instructions, planner_instructions, style_instructions
from app.domain.exam_style import length_problems, mix_problems, question_words, render_style_numbers, style_numbers
from app.schemas.ai import (
    ExamPlan,
    GeneratedQuestion,
    PlannedQuestion,
    PlannedSection,
    PreviousExamQuestion,
    QuestionBatch,
    StyleProfile,
)
from app.services.exam_generation import batch_problems, fit_points, interleave_plan, partial_plan
from tests.helpers import auth, wait_for_run
from tests.model import make_content, well_behaved_batch, well_behaved_plan
from tests.test_question_plan import approved, edit, get_plan, make_plan, run, write

CASE = "A clinic screens 1,000 patients. The test flags 120 of them, and 90 of those are ill."


# -- The plan as it is written ----------------------------------------------------------------------


def _plan(count: int = 4) -> ExamPlan:
    return ExamPlan(
        sections=[PlannedSection(title="Case Study 1: Screening", instructions=CASE)],
        questions=[
            PlannedQuestion(
                number=number,
                section_index=0,
                type="mcq",
                difficulty="medium",
                points=5,
                topic=f"topic {number}",
                learning_objective=f"objective {number}",
                cognitive_level="apply",
                task="conceptual",
                source_hint=None,
                estimated_minutes=2,
                figure_document_id=None,
            )
            for number in range(1, count + 1)
        ],
        rationale="",
        coverage_notes="",
        warnings=[],
    )


def test_a_plan_being_written_shows_its_cases_first_then_each_finished_question():
    text = _plan().model_dump_json()
    case_ends = text.index('"questions"')
    # Nothing until every section (each case, complete) is written.
    assert partial_plan(text[: case_ends - 20]) is None
    sections, questions = partial_plan(text[: case_ends + 15])
    assert sections[0].instructions == CASE and questions == []
    # Then each question once it is complete, never half of one.
    second_ends = text.index('{"number":3')
    _, questions = partial_plan(text[: second_ends - 1])
    assert [q.number for q in questions] == [1, 2]
    _, questions = partial_plan(text[: second_ends + 30])
    assert [q.number for q in questions] == [1, 2]
    assert [q.number for q in partial_plan(text)[1]] == [1, 2, 3, 4]


def test_the_professors_questions_keep_their_place_and_points():
    plan = _plan(6)
    clustered = plan.model_copy(
        update={
            "questions": [
                q.model_copy(update={"difficulty": level})
                for q, level in zip(plan.questions, ["easy", "easy", "easy", "hard", "hard", "hard"], strict=True)
            ]
        }
    )
    mixed = interleave_plan(clustered, [], fixed={2, 5})
    assert [q.number for q in mixed.questions] == [1, 2, 3, 4, 5, 6]
    assert mixed.questions[1].topic == "topic 2" and mixed.questions[4].topic == "topic 5"
    assert [q.difficulty for q in mixed.questions] != [q.difficulty for q in clustered.questions]
    # Points fit the total around the professor's own.
    fitted = fit_points(
        plan.model_copy(
            update={"questions": [q.model_copy(update={"points": 7}) if q.number == 2 else q for q in plan.questions]}
        ),
        60,
        {2},
    )
    assert fitted.questions[1].points == 7 and sum(q.points for q in fitted.questions) == 60


def test_the_plan_appears_as_it_is_made_and_keeps_what_the_professor_did_meanwhile(api, supabase, db_a, ai):
    assessment_id = approved(api, supabase, db_a)
    release, reached = threading.Event(), threading.Event()

    async def pause(purpose: str, text: str) -> None:
        found = partial_plan(text) if purpose == "plan_exam" else None
        if found and len(found[1]) >= 3 and not release.is_set():
            reached.set()
            await asyncio.to_thread(release.wait, 20)

    ai.stream_chunk, ai.stream_pause = 60, pause
    started = api.post(f"/api/assessments/{assessment_id}/question-plan", headers=auth())
    planning_run = started.json()["run"]["id"]
    assert reached.wait(10)

    partial = get_plan(api, assessment_id)
    assert partial["status"] == "planning"
    # The sections (cases) are all there before the questions that build on them.
    assert [s["title"] for s in partial["sections"]] == ["Section A: Multiple choice", "Section B: Problems"]
    assert [q["number"] for q in partial["questions"]] == [1, 2, 3]
    assert partial["expectedQuestions"] is None or partial["expectedQuestions"] >= 3
    # The whole plan is needed for the full exam; the plan can't be changed by the AI yet.
    refused = api.post(f"/api/assessments/{assessment_id}/question-plan/exam", json={}, headers=auth())
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "plan_incomplete"
    not_yet = api.post(f"/api/assessments/{assessment_id}/question-plan/9/question", json={}, headers=auth())
    assert not_yet.status_code == 404

    # A planned question is changed and another generated while the rest is planned.
    changed = edit(api, assessment_id, 1, topic="Load balancing", points=12)
    assert changed.status_code == 200, changed.text
    written = write(api, assessment_id, 2)
    assert written["status"] == "planning"
    assert written["questions"][1]["written"] is not None
    assert written["questions"][0]["topic"] == "Load balancing"

    release.set()
    assert wait_for_run(api, planning_run)["status"] == "succeeded"
    final = get_plan(api, assessment_id)
    assert final["status"] == "ready" and len(final["questions"]) == 10
    first, second = final["questions"][0], final["questions"][1]
    # Kept exactly as the professor left them, at the same numbers.
    assert first["topic"] == "Load balancing" and first["points"] == 12
    assert second["written"] is not None and not second["outdated"]
    assert second["topic"] == "topic 2"
    # The other questions make up the rest of the total.
    assert sum(q["points"] for q in final["questions"]) == 100

    # Now the full exam can be generated, keeping question 2.
    finished = api.post(f"/api/assessments/{assessment_id}/question-plan/exam", json={}, headers=auth())
    assert finished.status_code == 202
    assert wait_for_run(api, finished.json()["run"]["id"])["status"] == "succeeded"
    exam = api.get(f"/api/assessments/{assessment_id}/exam", headers=auth()).json()
    assert exam["questions"][1]["id"] == second["written"]["id"]


def test_a_plan_that_stopped_part_way_keeps_what_was_planned(api, supabase, db_a, ai):
    assessment_id = approved(api, supabase, db_a)

    def broken(call):
        plan = well_behaved_plan(call)
        last = plan.questions[-1].model_copy(update={"type": "mcq", "cognitive_level": "create"})
        return [plan.model_copy(update={"questions": [*plan.questions[:-1], last]})] * 2

    ai.on("plan_exam", broken)
    started = api.post(f"/api/assessments/{assessment_id}/question-plan", headers=auth())
    assert wait_for_run(api, started.json()["run"]["id"])["status"] == "failed"
    plan = get_plan(api, assessment_id)
    assert plan["status"] == "stopped" and len(plan["questions"]) == 10
    refused = api.post(f"/api/assessments/{assessment_id}/question-plan/exam", json={}, headers=auth())
    assert refused.status_code == 409 and "Make a new plan" in refused.json()["error"]["message"]


# -- The previous exams' overall style -------------------------------------------------------------------


def previous_style(calculations: int = 1) -> StyleProfile:
    """One previous exam: 30 multiple-choice questions of about 25 words, two cases, few calculations."""
    tasks = ["calculation"] * calculations + ["application"] * 11 + ["conceptual"] * (19 - calculations)
    return StyleProfile(
        exams_analyzed=1,
        common_question_types=["mcq"],
        typical_question_length="short",
        difficulty_characteristics=None,
        conceptual_vs_computational="mostly conceptual",
        recall_vs_application=None,
        wording_style="direct",
        use_of_scenarios="two cases",
        use_of_subquestions=None,
        section_structure=None,
        grading_patterns=None,
        recurring_preferences=[],
        limitations=[],
        summary="Short conceptual multiple-choice questions.",
        questions=[
            PreviousExamQuestion(
                exam=1,
                label=str(number),
                type="mcq",
                task=task,
                difficulty=("easy", "medium", "hard")[number % 3],
                marks=4,
                words=22 + number % 7,
                case=("Fraud detection" if number <= 6 else "Churn" if number <= 11 else None),
                topic="Precision and recall" if number % 4 == 0 else "Bias and variance",
            )
            for number, task in enumerate(tasks, start=1)
        ],
    )


def with_tasks(plan: ExamPlan, calculations: int) -> ExamPlan:
    tasks = ["calculation"] * calculations + ["application", "conceptual"] * len(plan.questions)
    return plan.model_copy(
        update={"questions": [q.model_copy(update={"task": task}) for q, task in zip(plan.questions, tasks, strict=False)]}
    )


def test_the_previous_exams_are_counted_in_code_and_scaled_to_this_exam():
    numbers = style_numbers(previous_style())
    assert numbers.tasks == {"conceptual": 18, "application": 11, "calculation": 1}
    assert numbers.cases == [6, 5] and numbers.words["mcq"] in range(22, 29)
    text = render_style_numbers(numbers, planned=30)
    assert "1 calculation (3%)" in text and "about 1 calculation question(s), never more than 2" in text
    assert "2 shared case(s), with 6, 5 questions; 19 questions stand alone" in text
    # Six or seven calculation questions don't match one in thirty; one or two do.
    plan = _plan(30)
    assert "plan at most 2 calculation" in mix_problems(with_tasks(plan, 7).questions, numbers)[0]
    assert not [p for p in mix_problems(with_tasks(plan, 2).questions, numbers) if "make students calculate" in p]
    # Profiles analysed before questions were listed simply have no numbers.
    assert style_numbers(previous_style().model_copy(update={"questions": None})) is None


def test_planning_and_writing_are_told_to_match_the_previous_exams_style():
    planner = planner_instructions(DataFramer())
    writer = generator_instructions(DataFramer())
    for text in (planner, writer):
        assert "MATCHING THE PROFESSOR'S PREVIOUS EXAMS" in text
        assert "never six or seven" in text
        assert "DIFFICULTY IS NOT LENGTH" in text
    assert "task (what students do" in planner and "Write the sections first" in planner
    assert "Only a 'calculation' question has students compute anything" in writer
    assert "task: 'calculation' when students must compute any number" in style_instructions(DataFramer())


def test_a_plan_with_far_more_calculation_than_the_previous_exams_is_replanned(api, supabase, db_a, ai):
    assessment_id = approved(api, supabase, db_a)
    run(
        db_a.insert(
            "assessment_style_profiles",
            {
                "exam_project_id": assessment_id,
                "source_hash": "test",
                "source_document_ids": [],
                "profile": previous_style().model_dump(),
                "model": "test",
            },
        )
    )
    ai.on("plan_exam", lambda call: with_tasks(well_behaved_plan(call), 6))
    ai.on("replan_exam", lambda call: with_tasks(well_behaved_plan(call), 1))

    plan = make_plan(api, assessment_id)
    assert plan["tasks"]["calculation"] == 1
    assert plan["previousTasks"] == {"conceptual": 18, "application": 11, "calculation": 1}
    planner_input = ai.calls_for("plan_exam")[0]["input"]
    assert "THE PREVIOUS EXAMS IN NUMBERS" in planner_input and "1 calculation (3%)" in planner_input
    repair = ai.calls_for("replan_exam")[0]["input"]
    assert "6 of the 10 questions make students calculate" in repair and "plan at most 1 calculation" in repair


def _hard(number: int, extra_words: int) -> GeneratedQuestion:
    content = make_content(number, "mcq", "hard", 5.0, cognitive_level="analyze")
    prompt = content.prompt + " " + " ".join(["context"] * extra_words)
    return GeneratedQuestion(number=number, **content.model_copy(update={"prompt": prompt}).model_dump())


def test_a_harder_question_is_not_a_longer_one():
    numbers = style_numbers(previous_style())
    slot = _plan(1).questions[0].model_copy(update={"difficulty": "hard", "cognitive_level": "analyze"})
    long = QuestionBatch(questions=[_hard(1, 120)], warnings=[])
    short = QuestionBatch(questions=[_hard(1, 0)], warnings=[])
    problems = batch_problems(long, [slot], [], style=numbers)
    assert any("harder through its thinking, never through more words" in problem for problem in problems)
    assert not [p for p in batch_problems(short, [slot], [], style=numbers) if "words" in p]
    # The professor's own question keeps their wording, however long.
    own = slot.model_copy(update={"professor_question": "A long question the professor wrote. " * 20})
    assert not [p for p in batch_problems(long, [own], [], style=numbers) if "words" in p]
    # A table, code or the shared case aren't words of the question.
    table = _hard(1, 0).model_copy(update={"prompt": "Which model is better?\n\n| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * 60})
    assert not length_problems(table, "Question 1", numbers)
    assert question_words(table) < 40


def test_the_writer_shortens_a_hard_question_that_ran_long(api, supabase, db_a, ai):
    assessment_id = approved(api, supabase, db_a)
    run(
        db_a.insert(
            "assessment_style_profiles",
            {
                "exam_project_id": assessment_id,
                "source_hash": "test",
                "source_document_ids": [],
                "profile": previous_style().model_dump(),
                "model": "test",
            },
        )
    )
    make_plan(api, assessment_id)

    def long_then_short(call):
        good = well_behaved_batch(call)
        padded = [q.model_copy(update={"prompt": q.prompt + " " + "context " * 150}) for q in good.questions]
        return [good.model_copy(update={"questions": padded}), good]

    ai.on("generate_questions", long_then_short)
    plan = write(api, assessment_id, 1)
    assert plan["questions"][0]["written"] is not None
    first_try = ai.calls_for("generate_questions")[-1]["problems"][0]
    assert any("runs to" in problem and "never through more words" in problem for problem in first_try)
    assert "context context" not in plan["questions"][0]["written"]["prompt"]
