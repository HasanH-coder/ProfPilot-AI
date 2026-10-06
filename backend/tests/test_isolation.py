"""Scenario J: professor B can't reach anything of professor A's by changing ids.

Every attempt answers 404 "not found" (never 403), so B can't even learn that
A's assessment exists. The real database enforces the same rule with Row Level
Security; see docs/assessment-agent.md for those checks.
"""

import asyncio

import pytest

from tests.helpers import auth, wait_for_run
from tests.seed import seed_assessment, seed_document
from tests.test_workflow import A_SETTINGS, generate, interpret_and_approve


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def a_world(api, supabase, db_a):
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    document = run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    exam = generate(api, assessment["id"])
    question = exam["questions"][0]
    revision = api.post(f"/api/questions/{question['id']}/revision", json={"preset": "clearer"}, headers=auth())
    assert revision.status_code == 200
    revisions = api.get(f"/api/questions/{question['id']}/revisions", headers=auth()).json()
    runs = api.get(f"/api/assessments/{assessment['id']}/ai-status", headers=auth()).json()["runs"]
    return {
        "assessment": assessment["id"],
        "document": document["id"],
        "exam": exam["id"],
        "version": exam["versions"][0]["id"],
        "question": question["id"],
        "revision": revisions[0]["id"],
        "run": runs[0]["id"],
    }


def attempts(ids):
    a, e, q = ids["assessment"], ids["exam"], ids["question"]
    return [
        ("GET", f"/api/assessments/{a}/ai-status", None),
        ("POST", f"/api/assessments/{a}/documents/processing", {"retryFailed": True}),
        ("POST", f"/api/assessments/{a}/documents/processing", {"documentId": ids["document"]}),
        ("POST", f"/api/assessments/{a}/interpretation", None),
        ("GET", f"/api/assessments/{a}/interpretation", None),
        ("PATCH", f"/api/assessments/{a}/interpretation", {"enhancedPrompt": "hijacked"}),
        ("POST", f"/api/assessments/{a}/interpretation/approval", None),
        ("POST", f"/api/assessments/{a}/exam", {"mode": "full", "replace": True}),
        ("GET", f"/api/assessments/{a}/exam", None),
        ("GET", f"/api/runs/{ids['run']}", None),
        ("GET", f"/api/exams/{e}", None),
        ("POST", f"/api/exams/{e}/review", None),
        ("GET", f"/api/exams/{e}/readiness", None),
        ("POST", f"/api/exams/{e}/finalization", None),
        ("GET", f"/api/exams/{e}/export?kind=both&version=all&confirm=true", None),
        ("PUT", f"/api/exams/{e}/versions/{ids['version']}/order", {"questionIds": [q]}),
        ("PATCH", f"/api/questions/{q}", {"prompt": "hijacked"}),
        ("POST", f"/api/questions/{q}/revision", {"preset": "harder"}),
        ("POST", f"/api/questions/{q}/solution", None),
        ("PUT", f"/api/questions/{q}/approval", {"approved": True}),
        ("DELETE", f"/api/questions/{q}", None),
        ("GET", f"/api/questions/{q}/revisions", None),
        ("POST", f"/api/questions/{q}/revisions/{ids['revision']}/restore", None),
        ("POST", f"/api/questions/{q}/variants", None),
        ("GET", f"/api/exams/{e}/builder/messages", None),
        ("POST", f"/api/exams/{e}/builder/messages", {"content": "hi"}),
        ("POST", f"/api/exams/{e}/builder/tools/get_exam_state", {"arguments": {}}),
        ("POST", f"/api/exams/{e}/builder/transcript", {"role": "professor", "content": "hi"}),
        ("POST", "/api/realtime/client-secrets", {"purpose": "builder", "examId": e}),
        ("POST", "/api/realtime/client-secrets", {"purpose": "setup", "assessmentId": a}),
    ]


def test_professor_b_gets_not_found_everywhere(api, a_world):
    for method, path, body in attempts(a_world):
        response = api.request(method, path, json=body, headers=auth("token-b"))
        assert response.status_code == 404, (method, path, response.status_code, response.text)
        assert response.json()["error"]["code"] == "not_found"


def test_professor_as_data_is_untouched_after_b_tries(api, a_world):
    before = api.get(f"/api/exams/{a_world['exam']}", headers=auth()).json()
    for method, path, body in attempts(a_world):
        api.request(method, path, json=body, headers=auth("token-b"))
    after = api.get(f"/api/exams/{a_world['exam']}", headers=auth()).json()
    assert after["questions"] == before["questions"]
    interpretation = api.get(f"/api/assessments/{a_world['assessment']}/interpretation", headers=auth()).json()
    assert interpretation["enhancedPrompt"] != "hijacked" and interpretation["status"] == "approved"


def test_b_cannot_read_a_files_from_storage(supabase, db_a, db_b):
    assessment = run(seed_assessment(db_a))
    document = run(seed_document(supabase, db_a, assessment["id"]))
    with pytest.raises(Exception, match="couldn't be found"):
        run(db_b.download("assessment-files", document["storage_path"]))


def test_unauthenticated_requests_are_rejected(api, a_world):
    for method, path, body in attempts(a_world):
        assert api.request(method, path, json=body).status_code == 401, path


def test_b_can_use_their_own_assessments_normally(api, supabase, db_b):
    assessment = run(seed_assessment(db_b, professor_prompt="Quiz on probability"))
    started = api.post(f"/api/assessments/{assessment['id']}/interpretation", headers=auth("token-b"))
    assert wait_for_run(api, started.json()["run"]["id"], "token-b")["status"] == "succeeded"
