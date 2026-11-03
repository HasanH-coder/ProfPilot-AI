"""Course sources are prepared for AI work without the professor asking: opening Sources brings older
files up to date (a lecture PDF's slides' words kept, its lecture knowledge built), once per file."""

import asyncio

from app.api import assessments as assessments_api
from app.core.errors import AIServiceError
from app.services.assessment_context import AssessmentContextService
from app.services.document_ingestion import DocumentIngestionService
from app.services.document_parsers import PAGE_MARKER
from app.services.lecture_knowledge import LectureKnowledgeService
from tests.helpers import auth
from tests.model import segment_handler
from tests.seed import seed_assessment, seed_document
from tests.test_figure_reading import DOCKER, lecture, read_source, text_of


def run(coroutine):
    return asyncio.run(coroutine)


def knowledge_of(db, document_id):
    return run(LectureKnowledgeService(db, None).get(document_id))  # type: ignore[arg-type]


def prepare(api, token="token-a"):
    """What the Sources page does when it opens (the background work finishes before the reply here)."""
    reply = api.post("/api/sources/preparation", headers=auth(token))
    assert reply.status_code == 202, reply.text
    return reply.json()["preparing"]


def slide_numbers(db, document_id):
    rows = run(db.select("document_pages", filters=[("document_id", "eq", document_id)], order=[("page_number", "asc")]))
    return [row["page_number"] for row in rows]


def older_lecture(supabase, db, ai, monkeypatch, *, pages=DOCKER, knowledge=True):
    """A lecture read before its slides' words were kept page by page (and, without `knowledge`, before
    its lecture knowledge was built)."""
    processed = read_source(supabase, db, ai, lecture(pages))
    run(db.delete("document_pages", filters=[("document_id", "eq", processed.id)]))
    if not knowledge:
        run(db.update("documents", {"knowledge": None}, filters=[("id", "eq", processed.id)]))
    return processed


def test_opening_sources_prepares_an_older_lecture_once(api, supabase, db_a, ai, monkeypatch):
    processed = older_lecture(supabase, db_a, ai, monkeypatch, knowledge=False)
    builds = len(ai.calls_for("synthesize_lecture"))

    assert prepare(api) == [processed.id]
    # Its slides' words are kept (from the file: no AI), then its lecture knowledge is built.
    assert slide_numbers(db_a, processed.id) == [1, 2, 3, 4, 5, 6, 7]
    assert len(ai.calls_for("synthesize_lecture")) == builds + 1
    assert ai.calls_for("read_figures") == []  # a PDF's pictures are seen as the exam is written
    shown = api.get(f"/api/documents/{processed.id}/knowledge", headers=auth()).json()
    assert shown["state"] == "ready" and shown["preparation"] is None

    # Prepared: the next visit finds nothing to do, even after a restart, and makes no model call.
    monkeypatch.setattr(assessments_api, "_prepared", set())
    calls = len(ai.calls)
    assert prepare(api) == [] and len(ai.calls) == calls


def test_a_lecture_read_before_page_markers_is_read_again_and_rebuilt(api, supabase, db_a, ai, monkeypatch):
    processed = older_lecture(supabase, db_a, ai, monkeypatch)
    old = run(db_a.select("document_chunks", filters=[("document_id", "eq", processed.id)]))
    run(db_a.delete("document_chunks", filters=[("document_id", "eq", processed.id)]))
    run(db_a.insert("document_chunks", [{**c, "content": PAGE_MARKER.sub("", c["content"]).strip()} for c in old]))
    knowledge = knowledge_of(db_a, processed.id)
    run(
        db_a.update(
            "documents", {"knowledge": {**knowledge, "coverage": {"page_detail": False}}}, filters=[("id", "eq", processed.id)]
        )
    )

    assert prepare(api) == [processed.id]
    assert PAGE_MARKER.search(text_of(db_a, processed.id))
    assert knowledge_of(db_a, processed.id)["coverage"]["page_detail"] is True


def test_a_failed_preparation_isnt_retried_on_its_own_but_retry_works(api, supabase, db_a, ai, monkeypatch):
    processed = older_lecture(supabase, db_a, ai, monkeypatch, knowledge=False)
    ai.on("analyze_lecture_segment", lambda _call: AIServiceError("The AI service is busy."))
    assert prepare(api) == [processed.id]
    assert knowledge_of(db_a, processed.id)["status"] == "failed"
    calls = len(ai.calls)
    assert prepare(api) == [] and len(ai.calls) == calls  # never keeps spending on a failing file

    ai.on("analyze_lecture_segment", segment_handler)
    assert api.post(f"/api/documents/{processed.id}/knowledge", headers=auth()).status_code == 202
    assert knowledge_of(db_a, processed.id)["status"] == "ready"


def test_only_the_professors_own_course_sources_are_prepared_and_never_while_busy(api, supabase, db_a, db_b, ai, monkeypatch):
    mine = older_lecture(supabase, db_a, ai, monkeypatch, knowledge=False)
    being_read = older_lecture(supabase, db_a, ai, monkeypatch, pages=DOCKER[:4], knowledge=False)
    monkeypatch.setattr(assessments_api, "_reading", {being_read.id})
    # An assessment's own upload isn't a course source; another professor's sources aren't theirs.
    assessment = run(seed_assessment(db_a))
    upload = run(seed_document(supabase, db_a, assessment["id"], name="Upload.pdf", data=lecture(DOCKER[:3])))
    run(DocumentIngestionService(db_a, ai).process(run(AssessmentContextService(db_a).get_document(upload["id"]))))
    run(db_a.update("documents", {"knowledge": None}, filters=[("id", "eq", upload["id"])]))
    older_lecture(supabase, db_b, ai, monkeypatch, pages=DOCKER[1:], knowledge=False)

    assert prepare(api) == [mine.id]


def test_a_source_just_read_shows_its_knowledge_as_on_its_way(api, supabase, db_a, ai, monkeypatch):
    processed = older_lecture(supabase, db_a, ai, monkeypatch, knowledge=False)
    monkeypatch.setattr(assessments_api, "_reading", {processed.id})
    # Read, and about to be analysed: never "No lecture knowledge yet", which invites a second paid build.
    assert api.get(f"/api/documents/{processed.id}/knowledge", headers=auth()).json()["state"] == "building"
