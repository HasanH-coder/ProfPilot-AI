"""End-to-end scenarios through the real API routes (OpenAI and Supabase faked).

Scenario A: manual configuration → Improve with AI → approve → full exam.
Scenario B: minimal input (no course, no name, no settings).
Scenario D: prompt improvement uses settings, material, notes, previous exams, preferences.
Scenario E: full generation (structured questions, answer keys, splits by marks).
Scenario H: multiple versions are different but equivalent.
Scenario I: the student PDF has no answers; the answer key has them.
"""

import asyncio
import io
import re
import zipfile

from pypdf import PdfReader

from tests.helpers import auth, wait_for_run
from tests.seed import seed_assessment, seed_course, seed_document

A_SETTINGS = {
    "duration_minutes": 90,
    "mcq_percentage": 40,
    "subjective_percentage": 60,
    "easy_percentage": 30,
    "medium_percentage": 40,
    "hard_percentage": 30,
    "number_of_versions": 2,
    "professor_prompt": "Focus on application rather than memorization.",
}


def run(coroutine):
    return asyncio.run(coroutine)


def interpret_and_approve(api, assessment_id: str, token: str = "token-a") -> dict:
    started = api.post(f"/api/assessments/{assessment_id}/interpretation", headers=auth(token))
    assert started.status_code == 202, started.text
    finished = wait_for_run(api, started.json()["run"]["id"], token)
    assert finished["status"] == "succeeded", finished
    interpretation = api.get(f"/api/assessments/{assessment_id}/interpretation", headers=auth(token)).json()
    assert interpretation["status"] == "draft"
    approved = api.post(f"/api/assessments/{assessment_id}/interpretation/approval", headers=auth(token))
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    return approved.json()


def generate(api, assessment_id: str, token: str = "token-a", **body) -> dict:
    started = api.post(f"/api/assessments/{assessment_id}/exam", json={"mode": "full", **body}, headers=auth(token))
    assert started.status_code == 202, started.text
    finished = wait_for_run(api, started.json()["run"]["id"], token)
    assert finished["status"] == "succeeded", finished
    return api.get(f"/api/assessments/{assessment_id}/exam", headers=auth(token)).json()


def test_scenario_a_manual_configuration_to_full_exam(api, supabase, db_a, ai):
    course = run(seed_course(db_a))
    assessment = run(seed_assessment(db_a, course_id=course["id"], exam_name="Midterm", **A_SETTINGS))
    run(seed_document(supabase, db_a, assessment["id"]))

    approved = interpret_and_approve(api, assessment["id"])

    # The interpreter saw ALL the configuration, the prompt and the course material.
    seen = ai.calls_for("interpret_assessment")[0]["input"]
    for expected in (
        "Duration: 90 minutes",
        "40% MCQ / 60% subjective",
        "30% easy / 40% medium / 30% hard",
        "Number of versions: 2",
        "Focus on application rather than memorization.",
        "CMPS 297U · Machine Learning",
        "Assessment name: Midterm",
        "Lecture 1.pdf",
        "Gradient descent updates parameters",
    ):
        assert expected in seen, expected
    # The ExamSpec matches the settings exactly, marked as the professor's.
    spec = approved["spec"]
    assert spec["duration_minutes"] == 90 and spec["duration_source"] == "professor"
    assert spec["question_format"] == {"mcq_percent": 40, "subjective_percent": 60, "source": "professor"}
    assert spec["difficulty"] == {"easy_percent": 30, "medium_percent": 40, "hard_percent": 30, "source": "professor"}
    assert spec["versions"] == 2 and spec["assessment_title"] == "Midterm"
    assert approved["enhancedPrompt"] and approved["inputs"]["professor_prompt"] == A_SETTINGS["professor_prompt"]

    exam = generate(api, assessment["id"])

    # Scenario E: structured questions with answer keys, following the spec by marks.
    assert exam["status"] == "ready" and exam["reviewStatus"] == "passed"
    assert [v["label"] for v in exam["versions"]] == ["A", "B"]
    for version in exam["versions"]:
        summary = version["summary"]
        assert summary["questionCount"] == 10 and summary["totalPoints"] == 100
        assert (summary["mcqPercent"], summary["easyPercent"], summary["mediumPercent"], summary["hardPercent"]) == (
            40,
            30,
            40,
            30,
        )
        assert version["warnings"] == []
    for question in exam["questions"]:
        if question["type"] == "mcq":
            assert question["correctChoice"] in [choice["id"] for choice in question["choices"]]
        else:
            assert question["answer"] and question["solution"]
            assert sum(item["points"] for item in question["rubric"]) == question["points"]
    sources = [q["sources"] for q in exam["questions"] if q["sources"]]
    assert sources and "Lecture 1.pdf" in sources[0][0]  # grounded in the uploaded lecture

    # Scenario H: versions differ but are equivalent, each with its own key.
    version_a, version_b = exam["versions"]
    a = {q["slotId"]: q for q in exam["questions"] if q["versionId"] == version_a["id"]}
    b = {q["slotId"]: q for q in exam["questions"] if q["versionId"] == version_b["id"]}
    assert a.keys() == b.keys()
    for slot, question in a.items():
        twin = b[slot]
        assert (twin["type"], twin["difficulty"], twin["points"]) == (
            question["type"],
            question["difficulty"],
            question["points"],
        )
        assert twin["prompt"] != question["prompt"]
        if twin["type"] == "mcq":
            assert twin["correctChoice"] in [choice["id"] for choice in twin["choices"]]

    # Scenario I: PDFs.
    exam_id = exam["id"]
    student = api.get(f"/api/exams/{exam_id}/export?kind=student&version=A", headers=auth())
    assert student.status_code == 200 and student.headers["content-type"] == "application/pdf"
    assert "attachment" in student.headers["content-disposition"]
    student_text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(student.content)).pages)
    assert "Midterm" in student_text and "Name:" in student_text
    for leaked in ("Model answer", "Worked solution", "Correct answer", "Marking rubric", "is correct.", "Lecture 1.pdf"):
        assert leaked not in student_text, leaked
    key = api.get(f"/api/exams/{exam_id}/export?kind=answer_key&version=A", headers=auth())
    key_text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(key.content)).pages)
    assert "Answer key" in key_text and "Model answer" in key_text and "Marking rubric" in key_text
    both = api.get(f"/api/exams/{exam_id}/export?kind=both&version=all", headers=auth())
    assert both.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(both.content)).namelist()
    assert sorted(names) == sorted(
        [
            "midterm-version-A-exam.pdf",
            "midterm-version-A-answer-key.pdf",
            "midterm-version-B-exam.pdf",
            "midterm-version-B-answer-key.pdf",
        ]
    )


def test_scenario_b_minimal_input_has_no_dead_end(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, professor_prompt="Create a midterm based on this."))
    run(seed_document(supabase, db_a, assessment["id"]))
    approved = interpret_and_approve(api, assessment["id"])
    seen = ai.calls_for("interpret_assessment")[0]["input"]
    assert "Course: not specified" in seen and "Assessment name: not specified" in seen
    assert approved["spec"]["versions"] == 1
    assert approved["spec"]["question_format"] is None  # not invented
    exam = generate(api, assessment["id"])
    assert exam["status"] == "ready" and len(exam["versions"]) == 1 and exam["questions"]
    pdf = api.get(f"/api/exams/{exam['id']}/export?kind=student&version=A", headers=auth())
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "None" not in text and "Untitled" not in text  # no ugly placeholders


def test_scenario_d_prompt_improvement_uses_everything(api, supabase, db_a, ai):
    run(db_a.insert("professor_preferences", {"explicit_notes": "I like scenario-based questions."}))
    assessment = run(
        seed_assessment(
            db_a,
            duration_minutes=60,
            easy_percentage=20,
            medium_percentage=40,
            hard_percentage=40,
            additional_notes="Avoid long proofs.",
            professor_prompt="make it difficult and practical",
        )
    )
    run(seed_document(supabase, db_a, assessment["id"]))
    run(seed_document(supabase, db_a, assessment["id"], name="Midterm 2025.pdf", category="previous_exam"))

    interpret_and_approve(api, assessment["id"])
    seen = ai.calls_for("interpret_assessment")[0]["input"]
    for expected in (
        "make it difficult and practical",
        "Avoid long proofs.",
        "Duration: 60 minutes",
        "20% easy / 40% medium / 40% hard",
        "PREVIOUS-EXAM STYLE",
        "Applied, scenario-based problems.",
        "I like scenario-based questions.",
        "Midterm 2025.pdf",
    ):
        assert expected in seen, expected
    # Uploaded text is framed as untrusted data, never as instructions.
    instructions = ai.calls_for("interpret_assessment")[0]["instructions"]
    assert "UNTRUSTED DATA" in instructions
    assert re.search(r"<<<DATA \w+ \| excerpt \| id S1", seen)

    # Interpreting again reuses the processed files and the style analysis (no new cost).
    embedded = len(ai.embedded_texts)
    started = api.post(f"/api/assessments/{assessment['id']}/interpretation", headers=auth())
    wait_for_run(api, started.json()["run"]["id"])
    assert len(ai.calls_for("analyze_previous_exams")) == 1
    assert len(ai.calls_for("summarize_document")) == 2
    new_texts = ai.embedded_texts[embedded:]
    assert all("Gradient descent updates parameters" not in text for text in new_texts)  # only queries embedded


def test_a_changed_setup_makes_the_approved_spec_stale(api, supabase, db_a):
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    interpret_and_approve(api, assessment["id"])
    # The professor changes the duration in the form after approving.
    run(db_a.update("exam_projects", {"duration_minutes": 120}, filters=[("id", "eq", assessment["id"])]))
    interpretation = api.get(f"/api/assessments/{assessment['id']}/interpretation", headers=auth()).json()
    assert interpretation["status"] == "stale" and interpretation["staleReasons"]
    blocked = api.post(f"/api/assessments/{assessment['id']}/exam", json={"mode": "full"}, headers=auth())
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] in ("spec_not_approved", "spec_stale")
    # Approving the stale interpretation is refused too; it must be regenerated.
    assert api.post(f"/api/assessments/{assessment['id']}/interpretation/approval", headers=auth()).status_code == 409


def test_uploading_a_file_after_approval_makes_the_spec_stale(api, supabase, db_a):
    assessment = run(seed_assessment(db_a, professor_prompt="Quiz on regression"))
    interpret_and_approve(api, assessment["id"])
    run(seed_document(supabase, db_a, assessment["id"], name="Lecture 2.pdf"))
    status = api.get(f"/api/assessments/{assessment['id']}/ai-status", headers=auth()).json()
    assert status["spec"]["status"] == "stale"


def test_editing_the_enhanced_prompt_needs_approval_again(api, supabase, db_a):
    assessment = run(seed_assessment(db_a, professor_prompt="Quiz on regression"))
    interpret_and_approve(api, assessment["id"])
    edited = api.patch(
        f"/api/assessments/{assessment['id']}/interpretation",
        json={"enhancedPrompt": "Write a short quiz on regression with one scenario question."},
        headers=auth(),
    ).json()
    assert edited["status"] == "draft" and edited["enhancedPrompt"].startswith("Write a short quiz")


def test_an_existing_exam_is_only_replaced_on_request(api, supabase, db_a):
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    interpret_and_approve(api, assessment["id"])
    first = generate(api, assessment["id"])
    again = api.post(f"/api/assessments/{assessment['id']}/exam", json={"mode": "full"}, headers=auth())
    assert again.status_code == 409 and again.json()["error"]["code"] == "exam_exists"
    second = generate(api, assessment["id"], replace=True)
    assert second["id"] != first["id"]
