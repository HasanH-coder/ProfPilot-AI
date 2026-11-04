"""The question plan: the professor reviews and changes it before the exam is written."""

import asyncio
import re

from app.ai.prompts import DataFramer, generator_instructions, planner_instructions, reviser_instructions
from app.core.errors import AIServiceError
from app.domain import distribution
from app.schemas.ai import BloomSpec, ExamSpec, FormatSpec
from app.services.assessment_context import spread_text
from app.services.jobs import job_runner
from app.services.question_plan import stop_planning
from tests.helpers import auth, wait_for_run
from tests.seed import pdf_bytes, seed_assessment, seed_document
from tests.test_workflow import interpret_and_approve

OLD_EXAM = ["Question 1. What does CPU stand for? A. Central Processing Unit. B. Core Power Unit. " * 6]


def run(coroutine):
    return asyncio.run(coroutine)


def approved_assessment(api, supabase, db_a, **settings) -> str:
    assessment = run(seed_assessment(db_a, duration_minutes=90, **settings))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    return assessment["id"]


def approved_assessment_of(api, supabase, db_a, **settings) -> str:
    assessment = run(seed_assessment(db_a, **settings))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    return assessment["id"]


def finish_quality_check(api, supabase) -> None:
    """Waits for the quality check a mini quiz's generation started in the background."""
    wait_for_run(api, next(row["id"] for row in supabase.tables["ai_runs"] if row["kind"] == "review"))


def make_plan(api, assessment_id: str, token: str = "token-a") -> dict:
    started = api.post(f"/api/assessments/{assessment_id}/question-plan", headers=auth(token))
    assert started.status_code == 202, started.text
    assert wait_for_run(api, started.json()["run"]["id"], token)["status"] == "succeeded"
    return api.get(f"/api/assessments/{assessment_id}/question-plan", headers=auth(token)).json()


def test_there_is_no_plan_until_one_is_asked_for(api, supabase, db_a):
    assessment_id = approved_assessment(api, supabase, db_a)
    plan = api.get(f"/api/assessments/{assessment_id}/question-plan", headers=auth("token-a")).json()
    assert plan == {"status": "none", "questions": [], "rationale": None}


def test_planning_needs_an_improved_prompt(api, supabase, db_a):
    assessment = run(seed_assessment(db_a))
    started = api.post(f"/api/assessments/{assessment['id']}/question-plan", headers=auth("token-a"))
    assert started.status_code == 409
    assert started.json()["error"]["code"] == "spec_missing"


def interpreted(api, supabase, db_a, **settings) -> str:
    """An improved prompt, on screen and not confirmed yet."""
    assessment = run(seed_assessment(db_a, **settings))
    run(seed_document(supabase, db_a, assessment["id"]))
    started = api.post(f"/api/assessments/{assessment['id']}/interpretation", headers=auth("token-a"))
    assert wait_for_run(api, started.json()["run"]["id"], "token-a")["status"] == "succeeded"
    return assessment["id"]


def test_planning_starts_while_the_prompt_is_only_on_screen(api, supabase, db_a):
    assessment_id = interpreted(api, supabase, db_a, duration_minutes=10)
    assert make_plan(api, assessment_id)["status"] == "ready"
    # Reading the prompt confirms nothing.
    interpretation = api.get(f"/api/assessments/{assessment_id}/interpretation", headers=auth("token-a")).json()
    assert interpretation["status"] == "draft"
    # Confirming it keeps the plan, and generating without confirming is still refused.
    refused = api.post(f"/api/assessments/{assessment_id}/exam", json={"mode": "full"}, headers=auth("token-a"))
    assert refused.status_code == 409
    api.post(f"/api/assessments/{assessment_id}/interpretation/approval", headers=auth("token-a"))
    assert api.get(f"/api/assessments/{assessment_id}/question-plan", headers=auth("token-a")).json()["status"] == "ready"


def test_a_new_prompt_stops_the_plan_being_made_from_the_old_one(api, supabase, db_a):
    assessment_id = interpreted(api, supabase, db_a, duration_minutes=10)

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
    # The lock is free again: planning can start at once.
    assert make_plan(api, assessment_id)["status"] == "ready"


def test_editing_the_prompt_or_regenerating_is_never_blocked_by_planning(api, supabase, db_a):
    assessment_id = interpreted(api, supabase, db_a, duration_minutes=10)
    db_row = {"exam_project_id": assessment_id, "kind": "interpretation", "stage": "planning"}
    run(db_a.insert("ai_runs", db_row, columns="id"))

    edited = api.patch(
        f"/api/assessments/{assessment_id}/interpretation",
        json={"enhancedPrompt": "A different short prompt for this quiz, long enough to guide an author."},
        headers=auth("token-a"),
    )
    assert edited.status_code == 200, edited.text

    run(db_a.insert("ai_runs", db_row, columns="id"))
    regenerated = api.post(f"/api/assessments/{assessment_id}/interpretation", headers=auth("token-a"))
    assert regenerated.status_code == 202, regenerated.text
    assert wait_for_run(api, regenerated.json()["run"]["id"], "token-a")["status"] == "succeeded"


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


def test_a_mini_quiz_is_simple_by_default(api, supabase, db_a):
    assessment_id = interpreted(api, supabase, db_a, duration_minutes=10)
    spec = api.get(f"/api/assessments/{assessment_id}/interpretation", headers=auth("token-a")).json()["spec"]
    assert (spec["difficulty"]["easy_percent"], spec["difficulty"]["hard_percent"]) == (60, 0)
    assert spec["difficulty"]["source"] == "ai_assumption"
    assert (spec["bloom"]["remember_percent"], spec["bloom"]["analyze_percent"]) == (40, 0)


def test_a_learning_focus_the_professor_chose_stays_theirs(api, supabase, db_a):
    assessment_id = interpreted(api, supabase, db_a, duration_minutes=10, learning_focus="critical")
    spec = api.get(f"/api/assessments/{assessment_id}/interpretation", headers=auth("token-a")).json()["spec"]
    assert spec["bloom"]["focus"] == "critical"
    assert spec["bloom"]["source"] == "professor"


def test_a_mini_quiz_is_written_to_be_simple_and_in_the_previous_exams_style(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, duration_minutes=10))
    run(seed_document(supabase, db_a, assessment["id"]))
    run(seed_document(supabase, db_a, assessment["id"], name="Old exam.pdf", category="previous_exam", data=pdf_bytes(OLD_EXAM)))
    interpret_and_approve(api, assessment["id"])
    make_plan(api, assessment["id"])
    started = api.post(f"/api/assessments/{assessment['id']}/exam", json={"mode": "full"}, headers=auth("token-a"))
    wait_for_run(api, started.json()["run"]["id"], "token-a")
    finish_quality_check(api, supabase)

    for purpose in ("plan_exam", "generate_questions"):
        call = ai.calls_for(purpose)[-1]
        assert "This overrules the guidance above" in call["instructions"], purpose
        assert "What does CPU stand for?" in call["input"], purpose
    assert "never make a question longer" in ai.calls_for("review_exam")[-1]["instructions"]


def test_a_mini_quiz_is_asked_for_full_sentences_and_equal_marks():
    writing = generator_instructions(DataFramer(), mini_quiz=True)
    assert "complete, natural sentences" in writing
    # These made its questions read as notes, with no setting and choices of a few words.
    assert "a few words" not in writing and "No invented scenarios" not in writing
    assert "same marks" in planner_instructions(DataFramer(), mini_quiz=True)
    assert "complete, natural sentences" in reviser_instructions(DataFramer(), mini_quiz=True)
    assert "complete, natural sentences" not in reviser_instructions(DataFramer())


def test_a_revision_does_what_the_professor_asks_students_to_do():
    assert "WHAT THE PROFESSOR ASKS STUDENTS TO DO IS BINDING" in reviser_instructions(DataFramer())


def test_editing_the_prompt_updates_what_the_exam_is_written_from(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, duration_minutes=10))
    run(seed_document(supabase, db_a, assessment["id"]))
    before = interpret_and_approve(api, assessment["id"])["spec"]
    assert before["question_count_max"] == 5

    def six_questions(call):
        assert "Write six questions." in call["input"]
        return ExamSpec.model_validate(
            {
                **before,
                "question_count_min": 6,
                "question_count_max": 6,
                "question_count_source": "professor",
                "warnings": [],
            }
        )

    ai.on("apply_prompt_edit", six_questions)
    url = f"/api/assessments/{assessment['id']}/interpretation"
    saved = api.patch(url, json={"enhancedPrompt": "Write six questions."}, headers=auth("token-a"))
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["enhancedPrompt"] == "Write six questions."
    assert body["status"] == "draft"
    # The number the professor wrote is kept, not put back to a mini quiz's five.
    assert body["spec"]["question_count_min"] == body["spec"]["question_count_max"] == 6


def test_a_prompt_edit_that_cant_reach_the_spec_isnt_half_saved(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, duration_minutes=10))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    url = f"/api/assessments/{assessment['id']}/interpretation"
    original = api.get(url, headers=auth("token-a")).json()["enhancedPrompt"]

    ai.on("apply_prompt_edit", lambda _call: AIServiceError())
    assert api.patch(url, json={"enhancedPrompt": "Write six questions."}, headers=auth("token-a")).status_code == 502
    assert api.get(url, headers=auth("token-a")).json()["enhancedPrompt"] == original


def test_a_mini_quiz_doesnt_warn_about_small_bloom_shares():
    class Spec:
        duration_minutes = 10
        question_count_max = 5
        question_format = FormatSpec(mcq_percent=100, subjective_percent=0, source="professor")
        bloom = BloomSpec(
            focus="custom", remember_percent=5, understand_percent=15, apply_percent=40,
            analyze_percent=25, evaluate_percent=10, create_percent=5, source="professor",
        )  # fmt: skip

    assert distribution.bloom_conflicts(Spec()) == []  # type: ignore[arg-type]
    # More than one question's share at Create still needs written questions.
    Spec.bloom = Spec.bloom.model_copy(update={"create_percent": 30, "apply_percent": 15})
    assert any("Create level" in conflict for conflict in distribution.bloom_conflicts(Spec()))  # type: ignore[arg-type]


def test_a_full_exam_gets_none_of_the_mini_quiz_rules(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, duration_minutes=90))
    run(seed_document(supabase, db_a, assessment["id"]))
    run(seed_document(supabase, db_a, assessment["id"], name="Old exam.pdf", category="previous_exam", data=pdf_bytes(OLD_EXAM)))
    interpret_and_approve(api, assessment["id"])
    make_plan(api, assessment["id"])

    call = ai.calls_for("plan_exam")[-1]
    assert "MINI QUIZ. This overrules" not in call["instructions"]
    assert "QUESTIONS FROM THE PROFESSOR'S PREVIOUS EXAMS" not in call["input"]


def test_a_difficulty_the_professor_chose_stays_theirs(api, supabase, db_a):
    assessment_id = interpreted(
        api, supabase, db_a, duration_minutes=10, easy_percentage=0, medium_percentage=0, hard_percentage=100
    )
    spec = api.get(f"/api/assessments/{assessment_id}/interpretation", headers=auth("token-a")).json()["spec"]
    assert spec["difficulty"]["hard_percent"] == 100
    assert spec["difficulty"]["source"] == "professor"


def test_a_mini_quiz_asks_the_ai_for_less_and_a_shorter_prompt(api, supabase, db_a, ai):
    assessment_id = approved_assessment_of(api, supabase, db_a, duration_minutes=10)
    make_plan(api, assessment_id)
    started = api.post(f"/api/assessments/{assessment_id}/exam", json={"mode": "full"}, headers=auth("token-a"))
    wait_for_run(api, started.json()["run"]["id"], "token-a")
    finish_quality_check(api, supabase)

    interpret = ai.calls_for("interpret_assessment")[-1]
    assert interpret["effort"] == "low"
    assert "MINI QUIZ BRIEF" in interpret["instructions"]
    assert ai.calls_for("plan_exam")[-1]["effort"] == "medium"
    assert {call["effort"] for call in ai.calls_for("generate_questions")} == {"medium"}
    assert ai.calls_for("review_exam")[-1]["effort"] == "medium"


def test_a_full_exam_keeps_its_full_reasoning(api, supabase, db_a, ai):
    assessment_id = approved_assessment(api, supabase, db_a)
    make_plan(api, assessment_id)
    interpret = ai.calls_for("interpret_assessment")[-1]
    assert "MINI QUIZ BRIEF" not in interpret["instructions"]
    assert ai.calls_for("plan_exam")[-1]["effort"] == "high"


def test_a_mini_quiz_appears_before_its_quality_check_finishes(api, supabase, db_a, ai):
    from tests.model import clean_review

    assessment_id = approved_assessment_of(api, supabase, db_a, duration_minutes=10)
    make_plan(api, assessment_id)
    seen: list[str] = []

    def review_seeing_the_exam(call):
        seen.append(next(row for row in supabase.tables["exams"])["status"])
        return clean_review(call)

    ai.on("review_exam", review_seeing_the_exam)
    started = api.post(f"/api/assessments/{assessment_id}/exam", json={"mode": "full"}, headers=auth("token-a"))
    assert wait_for_run(api, started.json()["run"]["id"], "token-a")["status"] == "succeeded"
    finish_quality_check(api, supabase)

    assert seen == ["ready"]
    exam = api.get(f"/api/assessments/{assessment_id}/exam", headers=auth("token-a")).json()
    assert exam["reviewStatus"] != "running"


def test_a_split_a_mini_quiz_can_only_roughly_meet_is_not_reported(api, supabase, db_a):
    assessment_id = interpreted(api, supabase, db_a, duration_minutes=10)
    spec = ExamSpec.model_validate(
        api.get(f"/api/assessments/{assessment_id}/interpretation", headers=auth("token-a")).json()["spec"]
    )
    summary = {
        "questionCount": 5,
        "mcqPercent": 100,
        "easyPercent": 60,
        "mediumPercent": 40,
        "hardPercent": 0,
        "estimatedMinutes": 9,
    }
    assert [item for item in distribution.compare(summary, spec) if item["dimension"] == "difficulty"] == []
    # A format the professor set is still exact.
    spec.question_format = FormatSpec(mcq_percent=100, subjective_percent=0, source="professor")
    missed = distribution.compare({**summary, "mcqPercent": 80}, spec)
    assert [item["dimension"] for item in missed] == ["format"]


def _marked_pages(prefix: str, pages: int, words: int = 25) -> bytes:
    filler = " ".join(f"Question about records number {index}." for index in range(words))
    return pdf_bytes([f"{prefix}{page:03d} {filler}" for page in range(1, pages + 1)])


def _pages_seen(prefix: str, calls: list[dict]) -> set[str]:
    return {found for call in calls for found in re.findall(prefix + r"\d{3}", call["input"])}


def test_a_mini_quiz_can_use_every_word_of_the_lecture_and_previous_exam(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, duration_minutes=10))
    run(seed_document(supabase, db_a, assessment["id"], data=_marked_pages("LECTURE", 60)))
    run(
        seed_document(
            supabase, db_a, assessment["id"], name="Old exam.pdf", category="previous_exam", data=_marked_pages("OLDEXAM", 40)
        )
    )
    interpret_and_approve(api, assessment["id"])
    make_plan(api, assessment["id"])
    started = api.post(f"/api/assessments/{assessment['id']}/exam", json={"mode": "full"}, headers=auth("token-a"))
    assert wait_for_run(api, started.json()["run"]["id"], "token-a")["status"] == "succeeded"
    finish_quality_check(api, supabase)

    for purpose in ("interpret_assessment", "plan_exam", "generate_questions"):
        calls = ai.calls_for(purpose)
        # Every single writing request has all of it, whichever slides it was planned on.
        for call in calls:
            assert len(_pages_seen("LECTURE", [call])) == 60, purpose
            assert len(_pages_seen("OLDEXAM", [call])) == 40, purpose
    assert len(_pages_seen("LECTURE", ai.calls_for("review_exam"))) == 60
    assert "STAY CLOSE TO THE FILES" in ai.calls_for("generate_questions")[0]["instructions"]


def test_a_mini_quiz_gets_the_whole_text_of_a_word_lecture(api, supabase, db_a, ai):
    from app.services.document_parsers import TXT

    assessment = run(seed_assessment(db_a, duration_minutes=10))
    text = "\n\n".join(f"NOTESPART{part:03d} The notes explain records and values in detail." * 20 for part in range(1, 41))
    run(seed_document(supabase, db_a, assessment["id"], name="Notes.txt", mime=TXT, data=text.encode()))
    interpret_and_approve(api, assessment["id"])
    make_plan(api, assessment["id"])
    assert len(_pages_seen("NOTESPART", ai.calls_for("interpret_assessment"))) == 40
    assert len(_pages_seen("NOTESPART", ai.calls_for("plan_exam"))) == 40


def test_a_very_long_file_is_sampled_from_start_to_end():
    text = "".join(f"{index:06d}" for index in range(100_000))
    kept = spread_text(text, 6_000)
    assert len(kept) < 6_200
    assert kept.startswith("000000") and kept.rstrip().endswith("099999")
    assert spread_text("short", 6_000) == "short"


def test_a_full_exam_still_gets_only_what_it_did(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, duration_minutes=90))
    run(seed_document(supabase, db_a, assessment["id"], data=_marked_pages("LECTURE", 30)))
    old_exam = _marked_pages("OLDEXAM", 6)
    run(seed_document(supabase, db_a, assessment["id"], name="Old.pdf", category="previous_exam", data=old_exam))
    interpret_and_approve(api, assessment["id"])
    make_plan(api, assessment["id"])
    assert not _pages_seen("OLDEXAM", ai.calls_for("interpret_assessment") + ai.calls_for("plan_exam"))
    assert "STAY CLOSE TO THE FILES" not in ai.calls_for("plan_exam")[0]["instructions"]


def test_stray_spaces_in_settings_are_ignored():
    from app.core.config import Settings

    pasted = Settings(supabase_publishable_key=" key-value \n", supabase_url="https://x.supabase.co\n", openai_api_key="sk-x\n")
    assert pasted.supabase_publishable_key == "key-value"
    assert pasted.supabase_url == "https://x.supabase.co"
    assert pasted.openai_api_key.get_secret_value() == "sk-x"
