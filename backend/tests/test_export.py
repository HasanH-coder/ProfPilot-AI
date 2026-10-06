"""The final review gate before export."""

import asyncio

from tests.helpers import auth
from tests.test_revisions import ready_exam


def run(coroutine):
    return asyncio.run(coroutine)


def test_export_is_blocked_while_generating_and_needs_confirmation_when_incomplete(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    url = f"/api/exams/{exam['id']}/export?kind=student&version=A"

    run(db_a.update("exams", {"status": "generating"}, filters=[("id", "eq", exam["id"])]))
    blocked = api.get(url, headers=auth())
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "export_blocked"

    run(db_a.update("exams", {"status": "failed", "error_message": "Stopped."}, filters=[("id", "eq", exam["id"])]))
    readiness = api.get(f"/api/exams/{exam['id']}/readiness", headers=auth()).json()
    assert readiness["canExport"] and readiness["requiresConfirmation"]
    unconfirmed = api.get(url, headers=auth())
    assert unconfirmed.status_code == 409 and unconfirmed.json()["error"]["code"] == "export_needs_confirmation"
    assert api.get(url + "&confirm=true", headers=auth()).status_code == 200


def test_harmless_warnings_never_block_export(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    question = next(q for q in exam["questions"] if q["type"] != "mcq")
    api.patch(f"/api/questions/{question['id']}", json={"prompt": "A reworded question on entropy."}, headers=auth())
    readiness = api.get(f"/api/exams/{exam['id']}/readiness", headers=auth()).json()
    assert not readiness["requiresConfirmation"]
    statuses = {check["id"]: check["status"] for check in readiness["checks"]}
    assert statuses["solution_review"] == "warning" and statuses["approval"] == "info"
    assert api.get(f"/api/exams/{exam['id']}/export?kind=answer_key&version=B", headers=auth()).status_code == 200


def test_unknown_version_is_rejected(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a)
    assert api.get(f"/api/exams/{exam['id']}/export?version=Z", headers=auth()).status_code == 422
