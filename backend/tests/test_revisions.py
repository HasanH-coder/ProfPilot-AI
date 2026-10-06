"""Scenario F (one-question revision), manual edits, history, locking and the quality check's safe fixes."""

import asyncio

from app.core.errors import AIServiceError
from app.schemas.ai import QuestionFix, ReviewIssue, ReviewOutput
from tests.helpers import auth, wait_for_run
from tests.model import well_behaved_batch
from tests.seed import seed_assessment, seed_document
from tests.test_workflow import A_SETTINGS, generate, interpret_and_approve


def run(coroutine):
    return asyncio.run(coroutine)


def ready_exam(api, supabase, db_a, **settings) -> dict:
    assessment = run(seed_assessment(db_a, **(settings or A_SETTINGS)))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    return generate(api, assessment["id"])


def version_a(exam: dict) -> list[dict]:
    first = exam["versions"][0]["id"]
    return sorted((q for q in exam["questions"] if q["versionId"] == first), key=lambda q: q["number"])


def test_scenario_f_revising_one_question_changes_only_that_question(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    before = version_a(exam)
    q4 = before[3]
    revised = api.post(
        f"/api/questions/{q4['id']}/revision", json={"preset": "harder", "instruction": "Keep the same topic."}, headers=auth()
    )
    assert revised.status_code == 200, revised.text
    body = revised.json()
    question = body["question"]
    assert question["id"] == q4["id"] and question["number"] == 4
    assert question["prompt"] != q4["prompt"] and question["prompt"].startswith("Revised:")
    expected = {"easy": "medium", "medium": "hard", "hard": "hard"}[q4["difficulty"]]
    assert question["difficulty"] == expected and question["points"] == q4["points"]
    if question["type"] != "mcq":
        assert sum(item["points"] for item in question["rubric"]) == question["points"]  # key kept in sync
    assert body["changeSummary"]

    after = version_a(api.get(f"/api/exams/{exam['id']}", headers=auth()).json())
    for old, new in zip(before, after, strict=True):
        if old["id"] != q4["id"]:
            assert (new["prompt"], new["answer"], new["updatedAt"]) == (old["prompt"], old["answer"], old["updatedAt"])

    revisions = api.get(f"/api/questions/{q4['id']}/revisions", headers=auth()).json()
    assert len(revisions) == 1 and revisions[0]["source"] == "ai_revision"
    assert revisions[0]["snapshot"]["prompt"] == q4["prompt"]
    restored = api.post(f"/api/questions/{q4['id']}/revisions/{revisions[0]['id']}/restore", headers=auth()).json()
    assert restored["question"]["prompt"] == q4["prompt"]


def test_approved_questions_are_locked(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    q1 = version_a(exam)[0]
    assert (
        api.put(f"/api/questions/{q1['id']}/approval", json={"approved": True}, headers=auth()).json()["question"]["status"]
        == "approved"
    )
    for response in (
        api.post(f"/api/questions/{q1['id']}/revision", json={"preset": "easier"}, headers=auth()),
        api.patch(f"/api/questions/{q1['id']}", json={"prompt": "Changed"}, headers=auth()),
        api.delete(f"/api/questions/{q1['id']}", headers=auth()),
    ):
        assert response.status_code == 409 and response.json()["error"]["code"] == "question_locked"
    assert api.put(f"/api/questions/{q1['id']}/approval", json={"approved": False}, headers=auth()).status_code == 200


def test_manual_edits_flag_the_answer_key_and_regeneration_fixes_it(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    subjective = next(q for q in version_a(exam) if q["type"] != "mcq")
    edited = api.patch(
        f"/api/questions/{subjective['id']}", json={"prompt": "A rewritten question about entropy."}, headers=auth()
    )
    assert edited.status_code == 200 and edited.json()["question"]["needsSolutionReview"] is True
    readiness = api.get(f"/api/exams/{exam['id']}/readiness", headers=auth()).json()
    assert any(check["id"] == "solution_review" for check in readiness["checks"])
    fixed = api.post(f"/api/questions/{subjective['id']}/solution", headers=auth()).json()["question"]
    assert fixed["needsSolutionReview"] is False and fixed["answer"] == "New answer"
    sources = [r["source"] for r in api.get(f"/api/questions/{subjective['id']}/revisions", headers=auth()).json()]
    assert sources == ["solution_regenerated", "manual_edit"]


def test_mcq_edits_must_keep_a_valid_correct_choice(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    mcq = next(q for q in version_a(exam) if q["type"] == "mcq")
    bad = api.patch(f"/api/questions/{mcq['id']}", json={"correctChoice": "F"}, headers=auth())
    assert bad.status_code == 422
    choices = [{"id": "A", "text": "One"}, {"id": "B", "text": "Two"}, {"id": "C", "text": "Three"}]
    good = api.patch(f"/api/questions/{mcq['id']}", json={"choices": choices, "correctChoice": "C"}, headers=auth())
    assert good.status_code == 200
    assert good.json()["question"]["correctChoice"] == "C" and good.json()["question"]["needsSolutionReview"] is False


def test_changing_difficulty_reports_distribution_warnings(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    easy = [q for q in version_a(exam) if q["difficulty"] == "easy"]
    result = None
    for question in easy:
        result = api.patch(f"/api/questions/{question['id']}", json={"difficulty": "hard"}, headers=auth()).json()
    assert result is not None
    messages = [warning["message"] for warning in result["distributionWarnings"]]
    assert any("Hard questions are 60% of the marks" in message for message in messages)


def test_deleting_a_question_removes_it_from_every_version(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    q2 = version_a(exam)[1]
    assert api.delete(f"/api/questions/{q2['id']}", headers=auth()).status_code == 204
    after = api.get(f"/api/exams/{exam['id']}", headers=auth()).json()
    assert all(q["slotId"] != q2["slotId"] for q in after["questions"])
    assert [v["summary"]["questionCount"] for v in after["versions"]] == [9, 9]


def test_variants_are_resynced_after_an_edit(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    q1 = version_a(exam)[0]
    synced = api.post(f"/api/questions/{q1['id']}/variants", headers=auth()).json()
    assert len(synced["questions"]) == 1 and synced["questions"][0]["slotId"] == q1["slotId"]
    assert synced["skippedVersions"] == []


def test_reordering_questions(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    questions = version_a(exam)
    order = [q["id"] for q in reversed(questions)]
    reordered = api.put(
        f"/api/exams/{exam['id']}/versions/{exam['versions'][0]['id']}/order", json={"questionIds": order}, headers=auth()
    ).json()
    assert [q["id"] for q in version_a(reordered)] == order
    incomplete = api.put(
        f"/api/exams/{exam['id']}/versions/{exam['versions'][0]['id']}/order", json={"questionIds": order[:3]}, headers=auth()
    )
    assert incomplete.status_code == 422


def test_quality_check_applies_only_safe_fixes(api, supabase, db_a, ai):
    exam = ready_exam(api, supabase, db_a)
    questions = version_a(exam)
    mcqs = [q for q in questions if q["type"] == "mcq"]
    wrong_key, locked = mcqs[0], mcqs[1]
    new_choice = next(c["id"] for c in wrong_key["choices"] if c["id"] != wrong_key["correctChoice"])
    api.put(f"/api/questions/{locked['id']}/approval", json={"approved": True}, headers=auth())
    subjective = next(q for q in questions if q["type"] != "mcq")

    def review(_call):
        return ReviewOutput(
            summary="Two problems found.",
            estimated_total_minutes=80,
            issues=[
                ReviewIssue(
                    question_number=wrong_key["number"],
                    version_label="A",
                    severity="critical",
                    category="answer_key",
                    message="The marked answer is wrong.",
                    fix=QuestionFix(
                        kind="correct_answer_key", correct_choice=new_choice, prompt=None, answer=None, solution=None, rubric=None
                    ),
                ),
                ReviewIssue(
                    question_number=locked["number"],
                    version_label="A",
                    severity="critical",
                    category="answer_key",
                    message="Locked question key looks wrong.",
                    fix=QuestionFix(
                        kind="correct_answer_key", correct_choice=new_choice, prompt=None, answer=None, solution=None, rubric=None
                    ),
                ),
                ReviewIssue(
                    question_number=subjective["number"],
                    version_label="A",
                    severity="warning",
                    category="clarity",
                    message="Rewrite entirely.",
                    fix=QuestionFix(
                        kind="clarify_wording",
                        prompt="A completely different question about poetry?",
                        correct_choice=None,
                        answer=None,
                        solution=None,
                        rubric=None,
                    ),
                ),
            ],
        )

    ai.on("review_exam", review)
    started = api.post(f"/api/exams/{exam['id']}/review", headers=auth())
    assert wait_for_run(api, started.json()["run"]["id"])["status"] == "succeeded"
    reviewed = api.get(f"/api/exams/{exam['id']}", headers=auth()).json()
    by_id = {q["id"]: q for q in reviewed["questions"]}
    assert by_id[wrong_key["id"]]["correctChoice"] == new_choice  # safe fix applied
    assert by_id[locked["id"]]["correctChoice"] == locked["correctChoice"]  # locked: reported only
    assert by_id[subjective["id"]]["prompt"] == subjective["prompt"]  # a different question: never applied
    issues = reviewed["review"]["issues"]
    assert [issue["fixApplied"] for issue in issues if issue["source"] == "ai"] == [True, False, False]
    assert reviewed["reviewStatus"] == "needs_attention"
    revisions = api.get(f"/api/questions/{wrong_key['id']}/revisions", headers=auth()).json()
    assert revisions[0]["source"] == "ai_review_fix"


def test_generation_that_stops_halfway_keeps_saved_questions_and_resumes(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, **{**A_SETTINGS, "number_of_versions": 1}))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    calls = {"n": 0}

    def flaky(call):
        calls["n"] += 1
        if "WRITE THESE QUESTIONS NOW (exactly as planned)\n5." in call["input"]:
            return AIServiceError("ProfPilot's AI service couldn't complete this. Please try again.")
        return well_behaved_batch(call)

    ai.on("generate_questions", flaky)
    started = api.post(f"/api/assessments/{assessment['id']}/exam", json={"mode": "full"}, headers=auth())
    failed = wait_for_run(api, started.json()["run"]["id"])
    assert failed["status"] == "failed" and "try again" in failed["errorMessage"]
    exam = api.get(f"/api/assessments/{assessment['id']}/exam", headers=auth()).json()
    assert exam["status"] == "failed" and "were saved and kept" in exam["errorMessage"]
    kept = len(exam["questions"])
    assert 0 < kept < 10

    ai.on("generate_questions", well_behaved_batch)
    resumed = generate(api, assessment["id"], resume=True)
    assert resumed["id"] == exam["id"] and resumed["status"] == "ready"
    assert sorted(q["position"] for q in resumed["questions"]) == list(range(1, 11))
