"""Personalization learns only from accepted work; jobs report interruptions honestly."""

import asyncio
from datetime import UTC, datetime, timedelta

from app.services.personalization import build_learned_profile
from tests.helpers import auth
from tests.seed import seed_assessment, seed_document
from tests.test_revisions import ready_exam, version_a
from tests.test_workflow import A_SETTINGS


def run(coroutine):
    return asyncio.run(coroutine)


def test_finalizing_an_exam_teaches_typical_splits(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    assert api.post(f"/api/exams/{exam['id']}/finalization", headers=auth()).status_code == 200
    preferences = api.get("/api/preferences", headers=auth()).json()
    learned = preferences["learned"]
    assert learned["based_on"] == 1
    assert learned["typical_split"] == {"mcqPercent": 40, "easyPercent": 30, "mediumPercent": 40, "hardPercent": 30}
    assert any("90 minutes" in line for line in learned["summary"])
    # Finalizing again replaces the signal instead of counting the exam twice.
    api.post(f"/api/exams/{exam['id']}/finalization", headers=auth())
    assert api.get("/api/preferences", headers=auth()).json()["learned"]["based_on"] == 1


def test_only_accepted_ai_revisions_count(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    questions = version_a(exam)
    for question in questions[:2]:
        api.post(f"/api/questions/{question['id']}/revision", json={"preset": "application"}, headers=auth())
        api.put(f"/api/questions/{question['id']}/approval", json={"approved": True}, headers=auth())
    # A revision the professor undid (restored) and then approved is not a preference.
    rejected = questions[2]
    api.post(f"/api/questions/{rejected['id']}/revision", json={"preset": "harder"}, headers=auth())
    revision = api.get(f"/api/questions/{rejected['id']}/revisions", headers=auth()).json()[0]
    api.post(f"/api/questions/{rejected['id']}/revisions/{revision['id']}/restore", headers=auth())
    api.put(f"/api/questions/{rejected['id']}/approval", json={"approved": True}, headers=auth())

    learned = api.get("/api/preferences", headers=auth()).json()["learned"]
    assert learned["accepted_revisions"] == 2
    assert learned["tendencies"] == ["Prefers application-based questions over recall (accepted 2 times)"]


def test_learning_can_be_turned_off_and_reset(api, supabase, db_a):
    api.put("/api/preferences", json={"explicitNotes": "Use real-world datasets.", "learningEnabled": False}, headers=auth())
    exam = ready_exam(api, supabase, db_a)
    api.post(f"/api/exams/{exam['id']}/finalization", headers=auth())
    preferences = api.get("/api/preferences", headers=auth()).json()
    assert preferences["explicitNotes"] == "Use real-world datasets."
    assert preferences["learned"] == {} and preferences["learningEnabled"] is False
    api.put("/api/preferences", json={"learningEnabled": True}, headers=auth())
    api.post(f"/api/exams/{exam['id']}/finalization", headers=auth())
    assert api.get("/api/preferences", headers=auth()).json()["learned"]["based_on"] == 1
    reset = api.delete("/api/preferences/learned", headers=auth()).json()
    assert reset["learned"] == {} and reset["explicitNotes"] == "Use real-world datasets."


def test_explicit_settings_override_learned_preferences_in_prompts(api, supabase, db_a, ai):
    run(
        db_a.insert(
            "professor_preferences",
            {
                "learned": build_learned_profile(
                    [
                        {
                            "mcqPercent": 80,
                            "easyPercent": 50,
                            "mediumPercent": 30,
                            "hardPercent": 20,
                            "durationMinutes": 60,
                            "typeCounts": {"mcq": 8},
                            "sectionTitles": [],
                        }
                    ],
                    [],
                )
            },
        )
    )
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    run(seed_document(supabase, db_a, assessment["id"]))
    from tests.test_workflow import interpret_and_approve

    approved = interpret_and_approve(api, assessment["id"])
    seen = ai.calls_for("interpret_assessment")[0]["input"]
    assert "current settings and instructions always override them" in seen
    assert "about 80% of marks multiple choice" in seen
    assert approved["spec"]["question_format"]["mcq_percent"] == 40  # the professor's setting wins


def test_a_job_that_stops_reporting_is_shown_as_interrupted(api, db_a):
    assessment = run(seed_assessment(db_a))
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    [stuck] = run(
        db_a.insert(
            "ai_runs", {"exam_project_id": assessment["id"], "kind": "interpretation", "started_at": old, "heartbeat_at": old}
        )
    )
    shown = api.get(f"/api/runs/{stuck['id']}", headers=auth()).json()
    assert shown["status"] == "failed" and "interrupted" in shown["errorMessage"]
    # Starting the job again works: the stuck run is closed first.
    assert api.post(f"/api/assessments/{assessment['id']}/interpretation", headers=auth()).status_code == 202


def test_a_running_job_blocks_a_duplicate(api, db_a):
    assessment = run(seed_assessment(db_a))
    run(db_a.insert("ai_runs", {"exam_project_id": assessment["id"], "kind": "interpretation"}))
    response = api.post(f"/api/assessments/{assessment['id']}/interpretation", headers=auth())
    assert response.status_code == 409 and response.json()["error"]["code"] == "job_running"
