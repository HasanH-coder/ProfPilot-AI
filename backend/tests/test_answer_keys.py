"""Questions first, answer keys on request: the professor edits the questions by hand, then asks for one
question's key or all the missing ones (written from the saved wording), and downloads the questions, the
key or both. A key written before its question changed is marked, never silently kept as current."""

import asyncio
import io

from pypdf import PdfReader

from app.ai.prompts import DataFramer, answer_key_instructions, generator_instructions
from tests.helpers import auth
from tests.model import well_behaved_keys
from tests.test_revisions import ready_exam, version_a, write_all_keys


def run(coroutine):
    return asyncio.run(coroutine)


def pdf_text(response) -> str:
    assert response.status_code == 200, response.text
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)


def export(api, exam_id: str, kind: str, *, confirm: bool = False):
    return api.get(f"/api/exams/{exam_id}/export?kind={kind}&version=A{'&confirm=true' if confirm else ''}", headers=auth())


def test_a_question_and_one_of_its_choices_are_edited_by_hand_before_any_key(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a, keys=False, number_of_versions=1)
    mcq = next(q for q in version_a(exam) if q["type"] == "mcq")
    assert not mcq["hasAnswerKey"] and mcq["correctChoice"] is None
    choices = [{**choice} for choice in mcq["choices"]] + [{"id": "E", "text": "A fifth option"}]
    choices[2]["text"] = "The professor's own wording of choice C"

    edited = api.patch(
        f"/api/questions/{mcq['id']}", json={"prompt": "Which statement is true?", "choices": choices}, headers=auth()
    )
    assert edited.status_code == 200, edited.text
    question = edited.json()["question"]
    assert question["prompt"] == "Which statement is true?" and question["choices"][2]["text"].startswith("The professor's")
    # No key yet, so nothing is out of date; no AI was asked for anything.
    assert not question["hasAnswerKey"] and not question["needsSolutionReview"]

    # The questions download as they now read, without any key and without asking.
    text = pdf_text(export(api, exam["id"], "student"))
    assert "Which statement is true?" in text and "The professor's own wording of choice C" in text
    assert "Answer key" not in text


def test_one_key_is_written_from_the_saved_wording_and_marked_when_the_question_changes(api, supabase, db_a, ai):
    exam = ready_exam(api, supabase, db_a, keys=False, number_of_versions=1)
    mcq = next(q for q in version_a(exam) if q["type"] == "mcq")
    choices = [{**choice} for choice in mcq["choices"]]
    choices[1]["text"] = "Edited choice B, typed by the professor"
    api.patch(f"/api/questions/{mcq['id']}", json={"choices": choices}, headers=auth())

    keyed = api.post(f"/api/questions/{mcq['id']}/solution", headers=auth())
    assert keyed.status_code == 200, keyed.text
    question = keyed.json()["question"]
    assert question["hasAnswerKey"] and question["correctChoice"] == "B" and not question["answerKeyOutdated"]
    # The key writer was given the question exactly as saved, without any old key.
    sent = ai.calls_for("write_answer_keys")[0]["input"]
    assert "Edited choice B, typed by the professor" in sent and "Correct choice:" not in sent

    # Changing one choice afterwards marks the key out of date, until it is written again.
    choices[3]["text"] = "Another edit"
    changed = api.patch(f"/api/questions/{mcq['id']}", json={"choices": choices}, headers=auth()).json()["question"]
    assert changed["answerKeyOutdated"] and changed["correctChoice"] == "B"
    key_text = pdf_text(export(api, exam["id"], "answer_key", confirm=True))
    assert "This key was written before the question last changed: check it." in key_text
    rewritten = api.post(f"/api/questions/{mcq['id']}/solution", headers=auth()).json()["question"]
    assert not rewritten["answerKeyOutdated"] and "Another edit" in ai.calls_for("write_answer_keys")[-1]["input"]
    history = [r["source"] for r in api.get(f"/api/questions/{mcq['id']}/revisions", headers=auth()).json()]
    assert history[0] == "solution_regenerated"


def test_all_keys_are_written_on_request_keeping_the_ones_already_written(api, supabase, db_a, ai):
    exam = ready_exam(api, supabase, db_a, keys=False)
    questions = version_a(exam)
    first = questions[0]
    kept = api.post(f"/api/questions/{first['id']}/solution", headers=auth()).json()["question"]
    calls_before = len(ai.calls_for("write_answer_keys"))

    exam = write_all_keys(api, exam["id"])
    # Every question of both versions now has its key; the one written before is untouched.
    assert all(q["hasAnswerKey"] and not q["answerKeyOutdated"] for q in exam["questions"])
    again = next(q for q in exam["questions"] if q["id"] == first["id"])
    assert (again["answer"], again["updatedAt"]) == (kept["answer"], kept["updatedAt"])
    written = [call["input"] for call in ai.calls_for("write_answer_keys")[calls_before:]]
    assert not any(first["prompt"] in text for text in written)
    assert sum(text.count("\nQ") + text.startswith("Q") for text in written) >= 19

    # Now the key downloads without asking, with every answer.
    key_text = pdf_text(export(api, exam["id"], "answer_key"))
    assert "Correct answer: B" in key_text and "Answer key not generated yet." not in key_text


def test_downloading_the_key_before_the_keys_exist_asks_and_says_which_are_missing(api, supabase, db_a, ai):
    exam = ready_exam(api, supabase, db_a, keys=False, number_of_versions=1)
    asked = export(api, exam["id"], "answer_key")
    assert asked.status_code == 409 and "have no answer key yet" in asked.json()["error"]["message"]
    both = api.get(f"/api/exams/{exam['id']}/export?kind=both&version=A", headers=auth())
    assert both.status_code == 409
    text = pdf_text(export(api, exam["id"], "answer_key", confirm=True))
    assert "Answer key not generated yet." in text
    # Asking for nothing but the questions never writes a key.
    assert not ai.calls_for("write_answer_keys")


def test_an_ai_revision_writes_the_question_only_and_marks_the_key_it_had(api, supabase, db_a, ai):
    exam = ready_exam(api, supabase, db_a, number_of_versions=1)
    question = next(q for q in version_a(exam) if q["type"] != "mcq")
    revised = api.post(f"/api/questions/{question['id']}/revision", json={"preset": "harder"}, headers=auth())
    assert revised.status_code == 200, revised.text
    after = revised.json()["question"]
    assert after["prompt"] != question["prompt"] and after["answerKeyOutdated"]
    assert after["answer"] == question["answer"]  # the old key stays, marked, until it is written again
    assert "Write the revised question only" in ai.calls_for("revise_question")[0]["instructions"]
    # A question without a key stays without one.
    fresh = ready_exam(api, supabase, db_a, keys=False, number_of_versions=1)
    keyless = version_a(fresh)[1]
    result = api.post(f"/api/questions/{keyless['id']}/revision", json={"preset": "clearer"}, headers=auth()).json()
    assert not result["question"]["hasAnswerKey"] and not result["question"]["needsSolutionReview"]


def test_a_key_is_not_saved_for_a_question_that_changed_while_it_was_written(api, supabase, db_a, ai):
    exam = ready_exam(api, supabase, db_a, keys=False, number_of_versions=1)
    target = version_a(exam)[0]

    def edited_meanwhile(call):
        row = next(r for r in supabase.tables["exam_questions"] if r["id"] == target["id"])
        row["prompt"], row["updated_at"] = "Changed while the key was written.", "2099-01-01T00:00:00+00:00"
        return well_behaved_keys(call)

    ai.on("write_answer_keys", edited_meanwhile)
    refused = api.post(f"/api/questions/{target['id']}/solution", headers=auth())
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "question_changed"
    saved = api.get(f"/api/exams/{exam['id']}", headers=auth()).json()
    assert not next(q for q in saved["questions"] if q["id"] == target["id"])["hasAnswerKey"]


def test_an_approved_question_gets_a_missing_key_but_keeps_an_up_to_date_one(api, supabase, db_a):
    exam = ready_exam(api, supabase, db_a, keys=False, number_of_versions=1)
    question = version_a(exam)[0]
    api.put(f"/api/questions/{question['id']}/approval", json={"approved": True}, headers=auth())
    keyed = api.post(f"/api/questions/{question['id']}/solution", headers=auth())
    assert keyed.status_code == 200 and keyed.json()["question"]["hasAnswerKey"]
    again = api.post(f"/api/questions/{question['id']}/solution", headers=auth())
    assert again.status_code == 409 and again.json()["error"]["code"] == "question_locked"


def test_the_key_writer_and_the_question_writer_are_told_their_jobs():
    keys = answer_key_instructions(DataFramer())
    assert "exactly as it now reads" in keys and "Solve each question yourself" in keys
    assert "its own rubric adding up to the part's points" in keys
    writer = generator_instructions(DataFramer(), keys=False)
    assert "QUESTIONS ONLY" in writer and "nothing of the answer key" in writer
    assert "(correct_choice)" not in writer and "(correct_choice)" in generator_instructions(DataFramer())
