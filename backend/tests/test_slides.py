"""A lecture PDF is used the way a chat assistant uses a PDF: every slide's words are kept as it's read
(no AI writes anything about the slides), the planner gets every word of every slide, and each writer
and the reviewer see the slides themselves, as images, with their words.

The fake model records exactly what each request showed it, so these tests check what reaches the
model and what is kept, not how well a real model reads a slide.
"""

import asyncio
import io
import re
from types import SimpleNamespace

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.core.config import settings
from app.services.assessment_context import AssessmentContextService, DocumentRecord
from app.services.document_ingestion import DocumentIngestionService, TranscribedPage, Transcription
from app.services.document_parsers import PDF, parse_document
from app.services.document_retrieval import DocumentRetrievalService
from app.services.slides import SlideIndex, slide_problems
from tests.helpers import auth, wait_for_run
from tests.seed import pdf_bytes, seed_assessment, seed_document
from tests.test_figure_reading import DOCKER, lecture, read_source
from tests.test_workflow import A_SETTINGS, generate, interpret_and_approve


def run(coroutine):
    return asyncio.run(coroutine)


def slides_of(db, document_id):
    rows = run(db.select("document_pages", filters=[("document_id", "eq", document_id)], order=[("page_number", "asc")]))
    return {row["page_number"]: row for row in rows}


def shown(call) -> list[str]:
    """The slides a request showed as images, by id, in order (each image follows a line naming it)."""
    parts = call["files"]
    return [
        re.match(r"Slide (F\d+:p\d+) ", part["text"]).group(1)
        for part, following in zip(parts, parts[1:], strict=False)
        if part["type"] == "input_text" and part["text"].startswith("Slide ") and following["type"] == "input_image"
    ]


def planned_slides(call) -> list[str]:
    """The slides of the questions a writer was asked to write (from its plan lines)."""
    targets = call["input"].split("WRITE THESE QUESTIONS NOW (exactly as planned)\n", 1)[1]
    return list(dict.fromkeys(ref for refs in re.findall(r"; pages: ([^;)]+)", targets) for ref in refs.split(", ")))


KUBERNETES = [f"Kubernetes topic {n}: pods group containers that share a network and storage, part {n}." for n in range(1, 5)]


# -- Reading a lecture keeps its words, and nothing else is written ------------------------------------


def test_every_slides_words_are_kept_as_a_lecture_is_read_with_no_ai_reading(supabase, db_a, ai):
    texts = [f"Topic {n}: virtual machines share a host through a hypervisor, lesson {n}." for n in range(1, 7)]
    processed = read_source(supabase, db_a, ai, pdf_bytes(texts), name="Virtualization.pdf")
    assert processed.processing_status == "ready"
    slides = slides_of(db_a, processed.id)
    assert [slides[n]["text_layer"] for n in range(1, 7)] == texts
    assert {row["status"] for row in slides.values()} == {"analysed"} and slides[3]["search_text"] == texts[2]
    # Nothing is written about the slides: only the lecture knowledge is built, from their words.
    assert {call["purpose"] for call in ai.calls} <= {"analyze_lecture_segment", "synthesize_lecture"}


def test_a_lectures_pictures_are_counted_for_the_planner_not_read(supabase, db_a, ai):
    processed = read_source(supabase, db_a, ai, lecture(DOCKER))
    slides = slides_of(db_a, processed.id)
    assert {number: row["analysis"]["pictures"] for number, row in slides.items() if row["analysis"]} == {2: 1, 4: 1, 6: 1}
    assert len(slides) == 7 and ai.calls_for("read_figures") == []


def test_a_scanned_lectures_words_are_transcribed(supabase, db_a, ai):
    scanned = io.BytesIO()
    pdf = canvas.Canvas(scanned)
    for colour in ("white", "lightgrey"):
        picture = io.BytesIO()
        Image.new("RGB", (60, 80), colour).save(picture, format="PNG")
        picture.seek(0)
        pdf.drawImage(ImageReader(picture), 0, 0, 595, 842)  # a scanned page: one picture, no text layer
        pdf.showPage()
    pdf.save()
    ai.on(
        "transcribe_pdf",
        lambda _call: Transcription(
            pages=[
                TranscribedPage(page_number=1, text="Handwritten notes: a process moves from ready to running."),
                TranscribedPage(page_number=2, text="A blocked process waits for I/O."),
            ]
        ),
    )
    processed = read_source(supabase, db_a, ai, scanned.getvalue(), name="Notes.pdf")
    slides = slides_of(db_a, processed.id)
    assert slides[1]["text_layer"].startswith("Handwritten notes")
    assert slides[2]["text_layer"] == "A blocked process waits for I/O."


def test_an_identical_file_gets_the_same_slides_without_reading_them_again(supabase, db_a, ai):
    data = pdf_bytes(["Load balancers spread requests across healthy servers.", "Health checks remove failing servers."])
    first = read_source(supabase, db_a, ai, data, name="Balancing.pdf")
    calls = len(ai.calls)
    copy = read_source(supabase, db_a, ai, data, name="Balancing copy.pdf")
    assert len(ai.calls) == calls
    words = [(row["page_number"], row["text_layer"]) for row in slides_of(db_a, first.id).values()]
    assert [(row["page_number"], row["text_layer"]) for row in slides_of(db_a, copy.id).values()] == words


def test_only_course_material_pdfs_keep_slides(supabase, db_a, ai):
    assessment = run(seed_assessment(db_a))
    exam = run(seed_document(supabase, db_a, assessment["id"], name="Old exam.pdf", category="previous_exam"))
    run(DocumentIngestionService(db_a, ai).process(run(AssessmentContextService(db_a).get_document(exam["id"]))))
    assert slides_of(db_a, exam["id"]) == {}


def test_files_are_read_a_few_at_a_time(supabase, db_a, ai, monkeypatch):
    reading, most = 0, 0

    async def read(self, document):
        nonlocal reading, most
        reading += 1
        most = max(most, reading)
        await asyncio.sleep(0.01)
        reading -= 1
        return document

    monkeypatch.setattr(DocumentIngestionService, "_process", read)
    monkeypatch.setattr(settings, "max_files_read_at_once", 2)
    assessment = run(seed_assessment(db_a))
    for name in ("Week 1.pdf", "Week 2.pdf", "Week 3.pdf"):
        run(seed_document(supabase, db_a, assessment["id"], name=name))
    documents = run(AssessmentContextService(db_a).list_documents(assessment["id"]))
    assert [doc.original_name for doc in run(DocumentIngestionService(db_a, ai).process_pending(documents))] == [
        doc.original_name for doc in documents
    ]
    assert most == 2


def test_parsing_keeps_every_pages_own_text():
    parsed = parse_document(pdf_bytes(["Long enough text for a page of its own.", "", "Tiny"]), PDF)
    assert parsed.page_texts == {1: "Long enough text for a page of its own.", 2: "", 3: "Tiny"}


# -- Writing the exam: every word to the planner, the slides themselves to each writer and the reviewer --


def test_the_exam_is_planned_from_every_word_and_written_and_checked_looking_at_its_slides(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    run(seed_document(supabase, db_a, assessment["id"], name="Week 1.pdf", data=lecture(DOCKER)))
    run(seed_document(supabase, db_a, assessment["id"], name="Week 2.pdf", data=pdf_bytes(KUBERNETES)))
    interpret_and_approve(api, assessment["id"])
    exam = generate(api, assessment["id"])

    # The planner was given every word of every slide of both lectures, and which slides show pictures.
    planned = ai.calls_for("plan_exam")[-1]["input"].split("ALL SLIDES (", 1)[1]
    assert all(text in planned for text in KUBERNETES)
    assert re.search(r"F\d+:p2 \[shows 1 picture\(s\) its words don't describe\]: The container lifecycle", planned)

    # Each writer saw exactly its questions' slides, as images, with every word on them.
    writers = ai.calls_for("generate_questions")
    assert writers and all(shown(call) == planned_slides(call) for call in writers)
    images = [part for call in writers for part in call["files"] if part["type"] == "input_image"]
    assert images and all(part["image_url"].startswith("data:image/jpeg;base64,") for part in images)
    assert all("THE SLIDES OF THESE QUESTIONS" in call["input"] for call in writers)

    # The reviewer checked every answer key against the same slides.
    review = ai.calls_for("review_exam")[-1]
    assert "SOURCE SLIDES OF EACH QUESTION" in review["input"]
    assert set(shown(review)) == {ref for call in writers for ref in shown(call)}

    # Every question names the slides it was written from.
    assert all(any(re.search(r"Week \d\.pdf, Page \d+", label) for label in q["sources"]) for q in exam["questions"])


def test_the_plan_is_written_seeing_every_slide_and_every_lectures_knowledge(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    week1 = run(seed_document(supabase, db_a, assessment["id"], name="Week 1.pdf", data=lecture(DOCKER)))
    week2 = run(seed_document(supabase, db_a, assessment["id"], name="Week 2.pdf", data=pdf_bytes(KUBERNETES)))
    for document in (week1, week2):
        run(DocumentIngestionService(db_a, ai).process(run(AssessmentContextService(db_a).get_document(document["id"]))))
    # Week 2 is read, but its lecture knowledge isn't built (as when another job is still building it).
    run(db_a.update("documents", {"knowledge": None}, filters=[("id", "eq", week2["id"])]))
    builds = len(ai.calls_for("synthesize_lecture"))
    interpret_and_approve(api, assessment["id"])
    assert len(ai.calls_for("synthesize_lecture")) == builds + 1  # built before the plan was written
    written = ai.calls_for("interpret_assessment")[-1]
    assert all(text in written["input"].split("ALL SLIDES (", 1)[1] for text in KUBERNETES)
    assert "EVERY SLIDE IS AVAILABLE" in written["instructions"]


def test_lectures_read_before_their_slides_were_kept_get_them_when_an_exam_is_written(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    document = run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    run(db_a.delete("document_pages", filters=[("document_id", "eq", document["id"])]))
    generate(api, assessment["id"])
    assert sorted(slides_of(db_a, document["id"])) == [1, 2]
    assert "Gradient descent updates parameters" in ai.calls_for("plan_exam")[-1]["input"].split("ALL SLIDES (", 1)[1]


def test_a_slide_that_cant_be_shown_still_goes_by_its_words(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    document = run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    del supabase.storage[document["storage_path"]]  # the PDF can't be opened any more
    exam = generate(api, assessment["id"])
    assert exam["status"] == "ready"
    writers = ai.calls_for("generate_questions")
    assert all(call["files"] == [] and "Gradient descent updates parameters" in call["input"] for call in writers)


def test_a_plan_must_cite_real_slides_and_cover_the_whole_lecture():
    doc = DocumentRecord.from_row({"id": "doc-1", "original_name": "Scheduling.pdf", "category": "course_material"})
    words = "Slide {n} teaches scheduling policy number {n} in detail for students in this course"
    index = SlideIndex.build([doc], {"doc-1": {n: {"words": words.format(n=n), "pictures": 0} for n in range(1, 21)}})
    question = lambda number, pages: SimpleNamespace(number=number, source_pages=pages)  # noqa: E731

    hard, _ = slide_problems([question(1, ["F1:p99"]), question(2, [])], index)
    assert hard == [
        "Question 1 cites slides that aren't in ALL SLIDES: F1:p99.",
        "Question 2 cites no slides: set source_pages from ALL SLIDES.",
    ]
    hard, soft = slide_problems([question(n, [f"F1:p{n}"]) for n in range(1, 5)], index)
    assert hard == [] and "spread them across the whole lecture" in soft[0]
    assert slide_problems([question(n, [f"F1:p{n * 5}"]) for n in range(1, 5)], index) == ([], [])


def test_searching_finds_a_slide_by_its_words(supabase, db_a, ai):
    assessment = run(seed_assessment(db_a))
    texts = [
        "Caches keep recently used data close to the processor.",
        "Hyperthreading runs two hardware threads on one core.",
    ]
    document = run(seed_document(supabase, db_a, assessment["id"], data=pdf_bytes(texts)))
    run(DocumentIngestionService(db_a, ai).process(run(AssessmentContextService(db_a).get_document(document["id"]))))
    supabase.rpc_calls.clear()
    excerpts = run(
        DocumentRetrievalService(db_a, ai).search(assessment["id"], ["hyperthreading"], document_names={document["id"]: "L.pdf"})
    )
    assert "search_document_pages" in supabase.rpc_calls
    assert any("Hyperthreading runs two hardware threads" in excerpt.content for excerpt in excerpts)


def test_no_exam_starts_while_a_lecture_is_still_being_read(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    run(seed_document(supabase, db_a, assessment["id"], name="Week 1.pdf"))
    second = run(seed_document(supabase, db_a, assessment["id"], name="Week 2.pdf"))
    interpret_and_approve(api, assessment["id"])
    # Its reading was cut short (say, by a restart) and hasn't finished.
    run(db_a.update("documents", {"processing_status": "processing"}, filters=[("id", "eq", second["id"])]))
    refused = api.post(f"/api/assessments/{assessment['id']}/exam", json={"mode": "full"}, headers=auth())
    assert refused.status_code == 409 and '"Week 2.pdf" is still being read' in refused.json()["error"]["message"]
    runs = api.get(f"/api/assessments/{assessment['id']}/ai-status", headers=auth()).json()["runs"]
    wait_for_run(api, next(r for r in runs if r["kind"] == "document_processing")["id"])
    status = api.get(f"/api/assessments/{assessment['id']}/ai-status", headers=auth()).json()
    assert {doc["name"]: doc["status"] for doc in status["documents"]} == {"Week 1.pdf": "ready", "Week 2.pdf": "ready"}
    started = api.post(f"/api/assessments/{assessment['id']}/exam", json={"mode": "full"}, headers=auth())
    assert started.status_code == 202, started.text
    assert wait_for_run(api, started.json()["run"]["id"])["status"] == "succeeded"
