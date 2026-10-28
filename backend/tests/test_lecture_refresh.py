"""Refreshing a lecture read before page markers existed: a free re-read, then a rebuild.

The document, its file, course and assessment links are kept; the working profile stays in use
until its replacement is saved and survives any failure; interruptions, retries, concurrent
requests and identical copies are handled. (Sources runs both steps on its own: see
test_source_preparation.py.)
"""

import asyncio

import pytest

from app.core.errors import AIServiceError
from app.services import lecture_knowledge
from app.services.assessment_context import AssessmentContextService
from app.services.document_ingestion import DocumentIngestionService, TranscribedPage, Transcription
from app.services.document_parsers import PAGE_MARKER
from app.services.lecture_knowledge import LectureKnowledgeService, extraction_state
from tests.helpers import auth
from tests.model import segment_handler
from tests.seed import pdf_bytes, seed_assessment, seed_course, seed_course_source, seed_document

PAGES = [
    "Logistic regression models the log-odds of the positive class as a linear function.",
    "The sigmoid maps a real score to a probability; the learning rate scales each update.",
    "Cross-entropy is the cost; gradient descent minimises it over the training set.",
]


def run(coroutine):
    return asyncio.run(coroutine)


def chunks_of(db, document_id):
    return run(
        db.select(
            "document_chunks",
            columns="chunk_index, content, location_label, embedding",
            filters=[("document_id", "eq", document_id)],
            order=[("chunk_index", "asc")],
        )
    )


def knowledge_of(db, document_id):
    return run(LectureKnowledgeService(db, None).get(document_id))  # type: ignore[arg-type]


def legacy_lecture(supabase, db, ai, *, data=None, course=None, name="Logistic Regression.pdf"):
    """A lecture read and profiled before page markers existed (as on the hosted database)."""
    course = course or run(seed_course(db))
    source = run(seed_course_source(supabase, db, course["id"], name=name, data=data or pdf_bytes(PAGES)))
    run(DocumentIngestionService(db, ai).process(run(AssessmentContextService(db).get_document(source["id"]))))
    old = chunks_of(db, source["id"])
    run(db.delete("document_chunks", filters=[("document_id", "eq", source["id"])]))
    run(
        db.insert(
            "document_chunks",
            [{**c, "document_id": source["id"], "content": PAGE_MARKER.sub("", c["content"]).strip()} for c in old],
        )
    )
    knowledge = knowledge_of(db, source["id"])
    coverage = {k: v for k, v in knowledge["coverage"].items() if k not in ("substantive_units", "page_detail", "pages")}
    run(db.update("documents", {"knowledge": {**knowledge, "coverage": coverage}}, filters=[("id", "eq", source["id"])]))
    return source, course


def state(api, document_id, token="token-a"):
    return api.get(f"/api/documents/{document_id}/knowledge", headers=auth(token)).json()


# -- Step 1: the free re-read --------------------------------------------------------------------


def test_a_reread_adds_page_markers_and_keeps_everything_else(api, supabase, db_a, ai):
    source, course = legacy_lecture(supabase, db_a, ai)
    assessment = run(seed_assessment(db_a, course_id=course["id"]))
    run(db_a.insert("assessment_documents", {"exam_project_id": assessment["id"], "document_id": source["id"]}))
    before = run(AssessmentContextService(db_a).get_document(source["id"]))
    profile = knowledge_of(db_a, source["id"])
    shown = state(api, source["id"])
    assert shown["legacy"] is True and shown["extraction"] == {"state": "legacy", "rereadable": True}
    assert shown["rereadEstimate"]["embeddingTokens"] > 0
    calls, embeddings = len(ai.calls), ai.embedding_calls

    reply = api.post(f"/api/documents/{source['id']}/extraction", headers=auth())
    assert reply.status_code == 200, reply.text
    assert reply.json()["extraction"] == "current"

    # No language-model call; one embedding request, for the new chunks only.
    assert len(ai.calls) == calls and ai.embedding_calls == embeddings + 1
    chunks = chunks_of(db_a, source["id"])
    assert chunks and all(PAGE_MARKER.match(chunk["content"]) for chunk in chunks)
    assert all(chunk["embedding"] for chunk in chunks)
    # Same document, file, digest, course, summary, assessment link and profile.
    after = run(AssessmentContextService(db_a).get_document(source["id"]))
    assert (after.id, after.storage_path, after.content_sha256, after.course_id, after.summary) == (
        before.id, before.storage_path, before.content_sha256, before.course_id, before.summary,
    )  # fmt: skip
    links = run(db_a.select("assessment_documents", columns="document_id", filters=[("exam_project_id", "eq", assessment["id"])]))
    assert [link["document_id"] for link in links] == [source["id"]]
    assert knowledge_of(db_a, source["id"]) == profile
    # The profile is still in use, still marked as legacy, and nothing was rebuilt.
    shown = state(api, source["id"])
    assert shown["state"] == "ready" and shown["legacy"] is True and shown["extraction"]["state"] == "current"
    assert run(AssessmentContextService(db_a).get_knowledge([after]))[source["id"]] == profile


def test_a_failed_reread_leaves_the_stored_text_as_it_was(supabase, db_a, ai, monkeypatch):
    source, _ = legacy_lecture(supabase, db_a, ai)
    before = chunks_of(db_a, source["id"])
    insert = db_a.insert

    async def failing_insert(table, rows, **kwargs):
        if table == "document_chunks":
            await insert(table, rows, **kwargs)  # some rows saved, then the connection drops
            raise AIServiceError("The connection was lost.")
        return await insert(table, rows, **kwargs)

    monkeypatch.setattr(db_a, "insert", failing_insert)
    record = run(AssessmentContextService(db_a).get_document(source["id"]))
    with pytest.raises(AIServiceError):
        run(DocumentIngestionService(db_a, ai).refresh_extraction(record))
    assert chunks_of(db_a, source["id"]) == before


def test_an_interrupted_reread_is_detected_never_built_on_and_finished_by_the_next(api, supabase, db_a, ai):
    source, _ = legacy_lecture(supabase, db_a, ai)
    old = chunks_of(db_a, source["id"])
    # A crash after saving a new chunk but before removing the old ones.
    run(
        db_a.insert(
            "document_chunks",
            {**old[0], "document_id": source["id"], "chunk_index": 50, "content": "[Page 1]\n" + old[0]["content"]},
        )
    )
    assert state(api, source["id"])["extraction"]["state"] == "interrupted"

    calls = len(ai.calls)
    api.post(f"/api/documents/{source['id']}/knowledge", headers=auth())
    shown = state(api, source["id"])
    assert len(ai.calls) == calls  # refused before any model call…
    assert shown["state"] == "ready" and shown["rebuild"]["state"] == "failed" and "interrupted" in shown["rebuild"]["error"]
    assert shown["topics"]  # …and the working profile is still there

    assert api.post(f"/api/documents/{source['id']}/extraction", headers=auth()).status_code == 200
    chunks = chunks_of(db_a, source["id"])
    assert extraction_state(chunks) == "current" and all(chunk["chunk_index"] > 50 for chunk in chunks)
    assert len({chunk["location_label"] for chunk in chunks}) == len(chunks)  # no duplicates left


def test_a_file_that_needed_ai_transcription_is_never_reread(api, supabase, db_a, ai):
    def transcribe(call):
        return Transcription(pages=[TranscribedPage(page_number=1, text="Handwritten notes on entropy and information gain.")])

    ai.on("transcribe_pdf", transcribe)
    source, _ = legacy_lecture(supabase, db_a, ai, data=pdf_bytes(["Decision trees split on information gain.", "", ""]))
    before = chunks_of(db_a, source["id"])
    calls = len(ai.calls)
    reply = api.post(f"/api/documents/{source['id']}/extraction", headers=auth())
    assert reply.status_code == 400 and "transcription" in reply.json()["error"]["message"]
    assert len(ai.calls) == calls and chunks_of(db_a, source["id"]) == before


def test_a_file_whose_stored_bytes_changed_is_not_reread(api, supabase, db_a, ai):
    source, _ = legacy_lecture(supabase, db_a, ai)
    supabase.storage[source["storage_path"]] = pdf_bytes(["Something else entirely, never read before."])
    before = chunks_of(db_a, source["id"])
    assert api.post(f"/api/documents/{source['id']}/extraction", headers=auth()).status_code == 400
    assert chunks_of(db_a, source["id"]) == before


def test_rereading_is_refused_while_the_file_is_busy_and_building_waits_for_a_reread(api, supabase, db_a, ai, monkeypatch):
    source, _ = legacy_lecture(supabase, db_a, ai)
    monkeypatch.setattr(lecture_knowledge, "_building", {source["id"]})
    assert api.post(f"/api/documents/{source['id']}/extraction", headers=auth()).json()["error"]["code"] == "busy"
    monkeypatch.undo()
    with lecture_knowledge.extracting(source["id"]):
        assert api.post(f"/api/documents/{source['id']}/knowledge", headers=auth()).status_code == 409
        assert state(api, source["id"])["extraction"]["state"] == "processing"


def test_only_the_owner_can_reread_and_only_read_pdf_or_powerpoint_files(api, supabase, db_a, ai):
    source, _ = legacy_lecture(supabase, db_a, ai)
    assert api.post(f"/api/documents/{source['id']}/extraction", headers=auth("token-b")).status_code == 404
    assessment = run(seed_assessment(db_a))
    notes = run(
        seed_document(supabase, db_a, assessment["id"], name="notes.txt", mime="text/plain", data=b"Plain notes about trees.")
    )
    reply = api.post(f"/api/documents/{notes['id']}/extraction", headers=auth())
    assert reply.status_code == 409 and reply.json()["error"]["code"] == "not_rereadable"


# -- Step 2: the separate, paid rebuild ---------------------------------------------------------------


def test_the_working_profile_survives_a_failed_rebuild_and_is_replaced_only_on_success(api, supabase, db_a, ai):
    source, _ = legacy_lecture(supabase, db_a, ai)
    api.post(f"/api/documents/{source['id']}/extraction", headers=auth())
    previous = knowledge_of(db_a, source["id"])

    ai.on("analyze_lecture_segment", lambda _call: AIServiceError("The AI service is busy."))
    api.post(f"/api/documents/{source['id']}/knowledge", headers=auth())
    shown = state(api, source["id"])
    assert shown["state"] == "ready" and shown["legacy"] is True
    assert shown["rebuild"] == {"state": "failed", "error": "The AI service is busy."}
    kept = knowledge_of(db_a, source["id"])
    assert {k: v for k, v in kept.items() if k != "rebuild"} == previous
    document = run(AssessmentContextService(db_a).get_document(source["id"]))
    assert run(AssessmentContextService(db_a).get_knowledge([document]))[source["id"]]["topics"] == previous["topics"]

    ai.on("analyze_lecture_segment", segment_handler)
    api.post(f"/api/documents/{source['id']}/knowledge", headers=auth())
    shown = state(api, source["id"])
    assert shown["state"] == "ready" and shown["legacy"] is False and shown["rebuild"] is None
    rebuilt = knowledge_of(db_a, source["id"])
    assert rebuilt["coverage"]["page_detail"] is True and rebuilt["coverage"]["substantive_units"] == 3


def test_an_interrupted_rebuild_reads_as_failed_and_keeps_the_profile(api, supabase, db_a, ai):
    source, _ = legacy_lecture(supabase, db_a, ai)
    previous = knowledge_of(db_a, source["id"])
    run(
        db_a.update(
            "documents", {"knowledge": {**previous, "rebuild": {"status": "building"}}}, filters=[("id", "eq", source["id"])]
        )
    )
    shown = state(api, source["id"])
    assert shown["state"] == "ready" and shown["topics"] and shown["rebuild"]["state"] == "failed"


def test_nothing_is_reread_or_rebuilt_on_its_own(api, supabase, db_a, ai):
    source, course = legacy_lecture(supabase, db_a, ai)
    other, _ = legacy_lecture(
        supabase,
        db_a,
        ai,
        course=course,
        name="Other.pdf",
        data=pdf_bytes(["Support vector machines maximise the margin between classes."]),
    )
    calls, embeddings = len(ai.calls), ai.embedding_calls
    assessment = run(seed_assessment(db_a, course_id=course["id"]))
    for document_id in (source["id"], other["id"]):
        run(db_a.insert("assessment_documents", {"exam_project_id": assessment["id"], "document_id": document_id}))
    documents = run(AssessmentContextService(db_a).list_documents(assessment["id"]))
    run(DocumentIngestionService(db_a, ai).process_pending(documents))  # e.g. opening an assessment
    state(api, source["id"])
    api.post(f"/api/documents/{source['id']}/extraction", headers=auth())  # one lecture, on request
    assert len(ai.calls) == calls and ai.embedding_calls == embeddings + 1
    assert (
        extraction_state(chunks_of(db_a, other["id"])) == "legacy"
        and knowledge_of(db_a, other["id"])["coverage"].get("page_detail") is None
    )


# -- Identical copies -------------------------------------------------------------------------------------


@pytest.mark.parametrize("twin_has_page_detail", [True, False])
def test_a_rebuild_copies_a_better_twin_profile_but_never_swaps_one_legacy_profile_for_another(
    api, supabase, db_a, ai, monkeypatch, twin_has_page_detail
):
    data = pdf_bytes(PAGES)  # the same bytes (each pdf_bytes call differs)
    source, _ = legacy_lecture(supabase, db_a, ai, data=data)
    calls = len(ai.calls)
    with monkeypatch.context() as patch:  # a copy made before files were prepared on reading
        patch.setattr(DocumentIngestionService, "prepare", _not_prepared)
        twin, _ = legacy_lecture(supabase, db_a, ai, data=data, course=run(seed_course(db_a, code="CMPS 298")))
    assert len(ai.calls) == calls  # the copy was read for free, with the source's profile
    if twin_has_page_detail:
        api.post(f"/api/documents/{twin['id']}/extraction", headers=auth())
        run(LectureKnowledgeService(db_a, ai).build(run(AssessmentContextService(db_a).get_document(twin["id"]))))
    api.post(f"/api/documents/{source['id']}/extraction", headers=auth())
    calls = len(ai.calls)

    api.post(f"/api/documents/{source['id']}/knowledge", headers=auth())
    rebuilt = knowledge_of(db_a, source["id"])
    assert rebuilt["coverage"]["page_detail"] is True
    if twin_has_page_detail:
        assert len(ai.calls) == calls and rebuilt == knowledge_of(db_a, twin["id"])  # copied, nothing paid
    else:
        assert len(ai.calls) > calls  # analysed: the twin's legacy profile is no improvement


async def _not_prepared(self, document, **_) -> None:
    return None
