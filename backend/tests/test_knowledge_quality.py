"""Lecture knowledge quality: formula fidelity, honest page classes, structured examples, Evaluate grounding,
format-only warnings and wording (the gaps found in the Logistic Regression evaluation)."""

import asyncio

from app.ai.prompts import DataFramer, lecture_segment_instructions
from app.schemas.ai import KnowledgeFormula
from app.services.assessment_context import AssessmentContextService, DocumentRecord
from app.services.document_ingestion import DocumentIngestionService
from app.services.exam_store import question_problems
from app.services.lecture_knowledge import (
    KNOWLEDGE_VERSION,
    LectureKnowledgeService,
    classify_pages,
    coverage_warnings,
    is_unverified,
    material_warnings,
    overview,
    page_list,
    render_formula,
    render_inventory,
    unverified_expressions,
    unverified_refs,
)
from tests.model import make_content, segment_handler
from tests.seed import pdf_bytes, seed_course, seed_course_source

PDF_LETTERS = "𝒛=𝒘.𝒙+𝒃"  # how the PDF's text layer gives 'z = w·x + b'


def run(coroutine):
    return asyncio.run(coroutine)


def formula(expression: str, *, uncertain: bool = False, normalized: str | None = None) -> dict:
    return {
        "name": "Linear score", "expression": expression, "normalized": normalized, "variables": ["z: score"],
        "conditions": None, "locations": ["Page 11"], "uncertain": uncertain,
    }  # fmt: skip


def lecture(formulas: list[dict], warnings: list[str] | None = None) -> tuple[list[DocumentRecord], dict]:
    doc = DocumentRecord.from_row(
        {
            "id": "d1", "exam_project_id": None, "course_id": "c", "category": "course_material",
            "original_name": "Logistic Regression.pdf", "mime_type": "application/pdf", "size_bytes": 1, "storage_path": "x",
            "processing_status": "ready", "processing_error": None, "processed_at": None, "content_sha256": "h",
            "page_count": 39, "extracted_characters": 13458, "summary": None, "created_at": "2026",
        }
    )  # fmt: skip
    topic = {
        "id": "t1", "name": "Sigmoid hypothesis", "section": None, "summary": "s", "locations": ["Page 11"],
        "concepts": [], "formulas": formulas, "examples": [], "visuals": [], "misconceptions": [],
        "bloom_opportunities": [{"level": "apply", "idea": "compute z"}],
    }  # fmt: skip
    profile = {
        "version": KNOWLEDGE_VERSION, "status": "ready", "content_sha256": "h", "title": "LR", "overview": "o",
        "sections": [], "topics": [topic], "relations": [], "warnings": warnings or [],
        "coverage": {"page_detail": True},
    }  # fmt: skip
    return [doc], {doc.id: profile}


# -- 1. Formula fidelity -------------------------------------------------------------------------------------


def test_a_formula_still_in_pdf_letters_is_unverified_even_when_the_analysis_called_it_verified():
    assert is_unverified(formula(PDF_LETTERS, uncertain=False))
    # Correct notation stays verified: LaTeX, plain text and ordinary Greek letters.
    for good in (r"\(z = w \cdot x + b\)", "z = w.x + b", "w = w - α dJ/dw"):
        assert not is_unverified(formula(good))

    line = render_formula(formula(PDF_LETTERS))
    assert line.startswith("- formula UNVERIFIED (extracted from the PDF, possibly garbled; never quote it)")
    assert "no reliable standard form: don't base a question on this formula's exact form" in line
    assert line.endswith(f"Extracted evidence, for reference only: {PDF_LETTERS}")
    assert (
        render_formula(formula(r"\(z = w \cdot x + b\)")) == r"- formula: Linear score: \(z = w \cdot x + b\); z: score (Page 11)"
    )


def test_such_a_formula_is_guarded_everywhere_a_question_could_use_it():
    documents, knowledge = lecture([formula(PDF_LETTERS)])
    assert unverified_expressions(documents, knowledge) == [PDF_LETTERS]  # questions quoting it are sent back
    assert unverified_refs(["L1.t1"], documents, knowledge) == ["Sigmoid hypothesis (Page 11)"]  # the review flags it
    assert "(1 formula, 1 unverified formula)" in render_inventory(documents, knowledge, DataFramer())  # the planner sees it
    copied = make_content(1, "short_answer", "medium", 5)
    copied.prompt = f"Given {PDF_LETTERS} with w = 2, x = 1 and b = 0.5, compute z."
    assert any("copied from a PDF's extracted text" in problem for problem in question_problems(copied))


def test_new_profiles_store_pdf_letter_formulas_as_unverified_and_drop_remarks_about_the_answer_format(supabase, db_a, ai):
    def verified_by_mistake(call):
        result = segment_handler(call)
        result.topics[0].formulas = [
            KnowledgeFormula(
                name="Linear score", expression=PDF_LETTERS, normalized=None, variables=[], conditions=None,
                locations=result.topics[0].locations, uncertain=False,
            )
        ]  # fmt: skip
        result.warnings = ["The response schema has no examples field; examples are recorded as concepts.", "Page 2 is blurred."]
        return result

    ai.on("analyze_lecture_segment", verified_by_mistake)
    course = run(seed_course(db_a))
    source = run(
        seed_course_source(supabase, db_a, course["id"], data=pdf_bytes(["The linear score feeds the sigmoid function."]))
    )
    run(DocumentIngestionService(db_a, ai).process(run(AssessmentContextService(db_a).get_document(source["id"]))))
    knowledge = run(LectureKnowledgeService(db_a, None).get(source["id"]))  # type: ignore[arg-type]
    stored = knowledge["topics"][0]["formulas"][0]
    assert stored["uncertain"] is True and stored["expression"] == PDF_LETTERS and stored["normalized"] is None
    assert "Page 2 is blurred." in knowledge["warnings"]
    assert not any("schema" in warning for warning in knowledge["warnings"])


# -- 3. Structured examples and warnings ----------------------------------------------------------------------


def test_remarks_about_the_answer_format_are_hidden_but_lecture_warnings_are_kept():
    warnings = [
        "The response schema has no examples field; worked examples are recorded in concept descriptions.",
        "Page 4's database schema diagram is unreadable.",
        "The table on Page 5 has no ID field, so rows can't be matched.",
        "Pages 13 and 16 use 1/(2m) while Page 17 uses 1/m.",
    ]
    assert material_warnings(warnings) == warnings[1:]
    # Saved profiles benefit at once: the Sources panel no longer shows the false remark.
    documents, knowledge = lecture([], warnings)
    assert overview(knowledge["d1"], documents[0])["warnings"] == warnings[1:]


def test_the_analysis_is_told_where_examples_go_and_when_evaluate_is_grounded():
    text = lecture_segment_instructions(DataFramer())
    assert "goes in examples, with its kind and its full details" in text
    assert "Evaluate fits when the material gives students grounds for a judgement" in text
    assert "Never add a level just to cover it" in text
    assert "never about this answer's format" in text
    assert "Text in mathematical letters such as '𝒛' or '𝒘' is always uncertain" in text


# -- 4. Coverage -----------------------------------------------------------------------------------------------------


FOOTER = "© American University of Beirut 2022."
FIGURE = "[Figure on page {n}: 1 picture(s) or chart(s) whose content is not in the extracted text]"


def test_slides_with_only_a_title_a_credit_or_axis_ticks_are_mostly_figures_not_substantive():
    credit = "Adapted from Andrew Ng's slides, Deep Learning Specialization."
    pages = {
        # Title, an image credit (run together, as extracted) and an unread figure: the logarithm refresher, page 14.
        1: f"REFRESHER: LOGARITHMIC FUNCTION\n{credit.replace(' ', '')}\n{FIGURE.format(n=1)}\n{FOOTER}",
        # A plot drawn as vector graphics: only tick labels and a credit survive (page 20).
        2: f"VISUALIZING THE DECISION BOUNDARY\n1\n2\n3\n1 2 3{credit}\n{FOOTER}",
        # A picture whose only text is its source link (page 3).
        3: f"CLASSIFICATION\nAdapted from https://example.org/img/problem.jpg\n{FIGURE.format(n=3)}\n{FOOTER}",
        # Real content keeps counting, credit or not.
        4: f"A decision boundary is a line or surface that separates the classes.\nSource: notes\n{FIGURE.format(n=4)}\n{FOOTER}",
        5: f"Gradient descent repeats the update until the cost stops decreasing.\n{FOOTER}",
        6: f"PART 2: TRAINING\n{FOOTER}",
    }
    classes = classify_pages(pages, page_count=6)
    assert classes["figure_dominated"] == [1, 2, 3]
    assert classes["text_with_figures"] == [4] and classes["text"] == [5] and classes["divider"] == [6]


# -- 6. Wording --------------------------------------------------------------------------------------------------------


def test_page_lists_are_grammatical_and_compact():
    assert page_list("Page", [29]) == "Page 29"
    assert page_list("Page", [39, 1]) == "Pages 1, 39"
    assert page_list("Page", [7, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 21, 24, 27]) == "Pages 7–13, 15–19, 21, 24, 27"
    warnings = coverage_warnings(
        {
            "unit": "page", "units_without_text": [39], "page_detail": True, "total_units": 39, "truncated": False,
            "pages": {"figure_dominated": [28], "text_with_figures": [3, 6], "uncertain_math": [7, 8, 9]},
        }
    )  # fmt: skip
    assert warnings == [
        "Page 39 has no extractable text (usually a picture or scan): its content isn't in the profile, and questions "
        "about it need the original.",
        "Page 28 is mostly figures or images, with only titles, captions or labels as text: what they show isn't in "
        "the profile, and questions about it need the original.",
        "Pages 3, 6 also have figures or images that aren't in the text: the text was analysed, but questions about "
        "the visuals need the original.",
        "Pages 7–9: the mathematics was extracted with garbled symbols, so their formulas are unverified until "
        "checked against the original.",
    ]


def test_formula_notes_read_as_notes():
    note = {**formula(PDF_LETTERS, uncertain=True, normalized=r"\(b \leftarrow b - \alpha \frac{dJ}{db}\)")}
    note["conditions"] = "The denominator is printed without d."
    line = render_formula(note)
    assert "; note: The denominator is printed without d." in line and "holds when" not in line
