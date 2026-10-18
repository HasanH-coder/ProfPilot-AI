"""Mathematical fidelity, page-level provenance and honest coverage for lecture knowledge.

A formula whose extracted text is garbled is never treated as the lecture's wording: it is
shown as unverified evidence, questions may not quote it, and questions built on it are
flagged for the professor. Pages are classified honestly, and identical copies share one profile.
"""

import asyncio
import io

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.ai.prompts import DataFramer, lecture_segment_instructions
from app.services.assessment_context import AssessmentContextService
from app.services.document_ingestion import DocumentIngestionService
from app.services.document_parsers import TextUnit, chunk_units, parse_pdf
from app.services.exam_store import question_problems
from app.services.lecture_knowledge import (
    LectureKnowledgeService,
    classify_pages,
    coverage_of,
    coverage_warnings,
    page_texts,
    unverified_formula_problems,
)
from tests.helpers import auth
from tests.model import make_content, segment_handler, well_behaved_batch, well_behaved_plan, well_behaved_revision
from tests.seed import pdf_bytes, seed_assessment, seed_course, seed_course_source, seed_document
from tests.test_workflow import A_SETTINGS, generate, interpret_and_approve

GARBLED = "𝑱 𝒘 = − 𝟏 𝒎 ∑ 𝒚 𝐥𝐨𝐠 𝒉 𝒙"


def run(coroutine):
    return asyncio.run(coroutine)


def knowledge_of(db, document_id):
    return run(LectureKnowledgeService(db, None).get(document_id))  # type: ignore[arg-type]


def with_garbled_formulas(call):
    """The lecture's formulas came out of the PDF garbled, and the analysis says so."""
    result = segment_handler(call)
    for topic in result.topics:
        for formula in topic.formulas:
            formula.expression = GARBLED
            formula.uncertain = True
    return result


def plan_on_first_topic(call):
    plan = well_behaved_plan(call)
    for question in plan.questions:
        question.knowledge_refs = ["L1.t1"]
    return plan


# -- Questions never quote unverified mathematics --------------------------------------------------


def test_ai_questions_with_characters_copied_from_extracted_pdf_text_are_rejected():
    content = make_content(1, "short_answer", "medium", 5)
    content.solution = "Differentiate 𝒇(𝒙) = 𝒆^𝒙 once."
    assert any("copied from a PDF's extracted text" in problem for problem in question_problems(content))
    # A professor's own edit is theirs to write.
    assert not any("copied from a PDF" in problem for problem in question_problems(content, manual=True))


def test_quoting_an_unverified_formula_is_found_in_every_part_of_a_question():
    content = make_content(1, "mcq", "medium", 2)
    assert unverified_formula_problems(content, [GARBLED]) == []
    content.choices[2].text = f"Use {GARBLED} directly"
    assert "quotes an unverified, garbled formula" in unverified_formula_problems(content, [GARBLED], "Question 1")[0]
    # Spacing differences don't hide a copy; short fragments (a lone "= 1") never count.
    content.choices[2].text = GARBLED.replace(" ", "")
    assert unverified_formula_problems(content, [GARBLED])
    assert unverified_formula_problems(make_content(2, "mcq", "easy", 2), ["= 1"]) == []


def test_generation_rejects_garbled_formulas_and_the_review_asks_the_professor_to_confirm_the_maths(api, supabase, db_a, ai):
    def quotes_then_corrects(call):
        good = well_behaved_batch(call)
        good.questions[0].prompt += " Compute \\(\\sigma(0) = \\frac{1}{1+e^{0}}\\)."
        bad = good.model_copy(deep=True)
        bad.questions[0].prompt += f" Recall that {GARBLED}."
        return [bad, good]

    ai.on("analyze_lecture_segment", with_garbled_formulas)
    ai.on("plan_exam", plan_on_first_topic)
    ai.on("generate_questions", quotes_then_corrects)
    assessment = run(seed_assessment(db_a, **{**A_SETTINGS, "number_of_versions": 1}))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    exam = generate(api, assessment["id"])

    call = ai.calls_for("generate_questions")[0]
    first_attempt = " ".join(call["problems"][0])
    assert "quotes an unverified, garbled formula" in first_attempt and "copied from a PDF" in first_attempt
    assert call["problems"][1] == []
    # The question writer saw the formula as unverified evidence, never as the lecture's wording.
    assert "formula UNVERIFIED" in call["input"] and "Extracted evidence, for reference only" in call["input"]
    assert all(GARBLED not in question["prompt"] for question in exam["questions"])
    # The question with mathematics on that topic is flagged for the professor to confirm.
    flagged = [issue for issue in exam["review"]["issues"] if "weren't reliably extracted" in issue["message"]]
    assert flagged and flagged[0]["category"] == "answer_key" and flagged[0]["severity"] == "warning"


def test_a_revision_may_not_quote_an_unverified_formula_and_sees_it_marked(api, supabase, db_a, ai):
    ai.on("analyze_lecture_segment", with_garbled_formulas)
    ai.on("plan_exam", plan_on_first_topic)
    assessment = run(seed_assessment(db_a, **{**A_SETTINGS, "number_of_versions": 1}))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    exam = generate(api, assessment["id"])

    def quotes_then_corrects(call):
        good = well_behaved_revision(call)
        bad = good.model_copy(deep=True)
        bad.question.answer = f"{bad.question.answer} Since {GARBLED}."
        return [bad, good]

    ai.on("revise_question", quotes_then_corrects)
    question = next(q for q in exam["questions"] if q["number"] == 1)
    reply = api.post(f"/api/questions/{question['id']}/revision", json={"preset": "harder"}, headers=auth())
    assert reply.status_code == 200, reply.text
    call = ai.calls_for("revise_question")[0]
    assert "quotes an unverified, garbled formula" in " ".join(call["problems"][0]) and call["problems"][1] == []
    assert GARBLED not in reply.json()["question"]["answer"]


def test_variants_may_not_quote_an_unverified_formula(api, supabase, db_a, ai):
    from tests.model import well_behaved_variants

    def quotes_then_corrects(call):
        good = well_behaved_variants(call)
        bad = good.model_copy(deep=True)
        bad.variants[0].question.solution += f" Using {GARBLED}."
        return [bad, good]

    ai.on("analyze_lecture_segment", with_garbled_formulas)
    ai.on("plan_exam", plan_on_first_topic)
    ai.on("write_variants", quotes_then_corrects)
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    generate(api, assessment["id"])
    call = ai.calls_for("write_variants")[0]
    assert "quotes an unverified, garbled formula" in " ".join(call["problems"][0]) and call["problems"][1] == []


def test_the_analysis_is_told_to_keep_evidence_and_standard_form_apart_and_to_separate_limitations():
    text = lecture_segment_instructions(DataFramer())
    for expected in ("normalized", "[Page 14]", "limitation", "create"):
        assert expected.lower() in text.lower(), expected


# -- Page-level provenance and honest coverage ------------------------------------------------------


def test_every_page_inside_a_combined_chunk_keeps_its_own_marker():
    units = [TextUnit(text=f"Text of page {n} about regularization.", page_start=n, page_end=n, kind="Page") for n in (3, 4, 5)]
    chunks = chunk_units(units)
    assert len(chunks) == 1 and chunks[0].location_label == "Pages 3–5"
    assert [line for line in chunks[0].text.splitlines() if line.startswith("[Page")] == ["[Page 3]", "[Page 4]", "[Page 5]"]
    pages = page_texts([{"content": chunks[0].text}])
    assert sorted(pages) == [3, 4, 5] and "page 4 about" in pages[4]
    # A long page split into pieces: every piece still names its page.
    long = chunk_units([TextUnit(text="A sentence about bias. " * 400, page_start=7, page_end=7, kind="Page")])
    assert len(long) > 1 and all(chunk.text.startswith("[Page 7]\n") for chunk in long)


def _png(colour: str) -> ImageReader:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def test_a_pdf_figure_beyond_the_slide_template_is_marked_on_its_page():
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for number in range(1, 4):
        pdf.drawImage(_png("red"), 500, 780, 20, 20)  # the template's logo, on every page
        if number == 2:
            pdf.drawImage(_png("blue"), 100, 300, 200, 200)  # a real figure
        pdf.drawString(40, 800, f"Page {number} explains the decision boundary in detail.")
        pdf.showPage()
    pdf.save()
    parsed = parse_pdf(buffer.getvalue())
    marked = [unit.page_start for unit in parsed.units if "[Figure on page" in unit.text]
    assert marked == [2]


def test_pages_are_classified_by_what_they_really_contain():
    footer = "CMPS 297U · Machine Learning · Fall 2026"
    figure = "picture(s) or chart(s) whose content is not in the extracted text]"
    pages = {
        1: f"INTRODUCTION TO LOGISTIC REGRESSION\n{footer}",
        2: f"Logistic regression models the log-odds of the positive class as a linear function of x.\n{footer}",
        3: f"Decision boundary\n[Figure on page 3: 1 {figure}\n0.5 1.0\n{footer}",
        4: f"The sigmoid maps any real score to a probability between zero and one.\n[Figure on page 4: 1 {figure}\n{footer}",
        5: f"PART 2: TRAINING\n{footer}",
        6: f"The cost is the average cross-entropy over the training set:\n{GARBLED}\n{footer}",
    }
    classes = classify_pages(pages, page_count=7)
    assert classes["text"] == [2, 6] and classes["text_with_figures"] == [4]
    assert classes["figure_dominated"] == [3] and classes["divider"] == [1, 5] and classes["empty"] == [7]
    assert classes["uncertain_math"] == [6]


def test_coverage_counts_only_substantive_pages_and_warns_about_the_rest():
    chunks = [
        {"content": "[Page 1]\nLOGISTIC REGRESSION", "location_label": "Page 1"},
        {
            "content": "[Page 2]\nLogistic regression models the log-odds as a linear function of the inputs.\n\n"
            f"[Page 3]\nThe cost function averages the cross-entropy over all examples.\n{GARBLED}",
            "location_label": "Pages 2–3",
        },
    ]
    coverage = coverage_of(chunks, page_count=4, extracted_characters=300, segments=1)
    assert coverage["page_detail"] is True and coverage["unit"] == "page"
    assert coverage["analysed_units"] == 3 and coverage["substantive_units"] == 2
    assert coverage["units_without_text"] == [4] and coverage["pages"]["divider"] == [1]
    warnings = coverage_warnings(coverage)
    assert any("Pages 4 had no extractable text" in warning for warning in warnings)
    assert any("Pages 3: the mathematics wasn't reliably extracted" in warning for warning in warnings)
    assert not any("Page-level coverage isn't available" in warning for warning in warnings)


# -- One profile for identical copies -----------------------------------------------------------------


def test_an_identical_copy_reuses_the_profile_instead_of_paying_again(api, supabase, db_a, ai):
    course = run(seed_course(db_a))
    data = pdf_bytes(["Support vector machines maximise the margin between classes."])
    first = run(seed_course_source(supabase, db_a, course["id"], name="SVM.pdf", data=data))
    # A copy read before the original had a profile (so nothing was copied then).
    second = run(seed_course_source(supabase, db_a, course["id"], name="SVM copy.pdf", data=data))
    context = AssessmentContextService(db_a)
    for source in (first, second):
        run(DocumentIngestionService(db_a, ai).process(run(context.get_document(source["id"]))))
    run(db_a.update("documents", {"knowledge": None}, filters=[("id", "eq", second["id"])]))
    calls = len(ai.calls)

    # Assessments use the twin's profile even before anything is copied…
    documents = [run(context.get_document(second["id"]))]
    assert run(context.get_knowledge(documents))[second["id"]]["topics"] == knowledge_of(db_a, first["id"])["topics"]
    # …and building it copies the profile with no model call.
    assert api.post(f"/api/documents/{second['id']}/knowledge", headers=auth()).status_code == 202
    assert api.get(f"/api/documents/{second['id']}/knowledge", headers=auth()).json()["state"] == "ready"
    assert len(ai.calls) == calls


def test_another_professors_identical_copy_is_never_used(api, supabase, db_a, db_b, ai):
    data = pdf_bytes(["Support vector machines maximise the margin between classes."])
    course_a = run(seed_course(db_a))
    original = run(seed_course_source(supabase, db_a, course_a["id"], data=data))
    run(DocumentIngestionService(db_a, ai).process(run(AssessmentContextService(db_a).get_document(original["id"]))))
    assert knowledge_of(db_a, original["id"])["status"] == "ready"

    course_b = run(seed_course(db_b))
    copy = run(seed_course_source(supabase, db_b, course_b["id"], data=data))
    context_b = AssessmentContextService(db_b)
    run(DocumentIngestionService(db_b, ai).process(run(context_b.get_document(copy["id"]))))
    run(db_b.update("documents", {"knowledge": None}, filters=[("id", "eq", copy["id"])]))
    assert run(context_b.get_knowledge([run(context_b.get_document(copy["id"]))])) == {}
