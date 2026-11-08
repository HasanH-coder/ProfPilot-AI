"""The exam's format follows the professor: their number of choices, cases like their previous exams, and
section text written for students, printed and shown properly."""

import asyncio
import io

from pypdf import PdfReader

from app.ai.prompts import DataFramer, generator_instructions, planner_instructions, variant_instructions
from app.schemas.ai import ExamPlan, PlannedQuestion, PlannedSection, QuestionBatch, VariantBatch, VersionVariant
from app.services.exam_generation import batch_problems, plan_problems, variant_problems
from app.services.exam_store import FullExam
from app.services.pdf_export import ExportHeader, PdfExportService, _column_widths
from tests.helpers import auth, wait_for_run
from tests.model import make_content, well_behaved_interpretation, well_behaved_plan
from tests.seed import seed_assessment, seed_document
from tests.test_workflow import interpret_and_approve

CASE = (
    "A Lebanese bank tests a fraud model on 5,000 transactions. Fraud is rare.\n\n"
    "| Outcome | Count |\n| --- | --- |\n| True positives | 120 |\n| False negatives | 30 |"
)


def run(coroutine):
    return asyncio.run(coroutine)


def slot(number: int, section_index: int = 0, question_type: str = "mcq") -> PlannedQuestion:
    return PlannedQuestion(
        number=number,
        section_index=section_index,
        type=question_type,
        difficulty="easy",
        points=4,
        topic=f"Topic {number}",
        learning_objective="x",
        cognitive_level="remember",
        source_hint=None,
        estimated_minutes=2,
        figure_document_id=None,
    )


def plan_with(sections: list[PlannedSection]) -> ExamPlan:
    return ExamPlan(
        sections=sections,
        questions=[slot(n, n - 1) for n in range(1, len(sections) + 1)],
        rationale="",
        coverage_notes="",
        warnings=[],
    )


# -- 1. The number of choices --------------------------------------------------------------------


def test_the_writer_is_told_the_number_of_choices():
    assert "exactly 5 choices with ids A, B, C, D, E" in generator_instructions(DataFramer(), choices=5)
    assert "exactly 4 choices with ids A, B, C, D" in generator_instructions(DataFramer())
    assert "spec.mcq_choices" in planner_instructions(DataFramer())
    assert "same number of choices" in variant_instructions(DataFramer())


def test_a_question_with_another_number_of_choices_is_sent_back():
    four = make_content(1, "mcq", "easy", 4, cognitive_level="remember")
    batch = QuestionBatch.model_validate({"questions": [{"number": 1, **four.model_dump()}], "warnings": []})
    assert any("exactly 5 choices" in problem for problem in batch_problems(batch, [slot(1)], [], choices=5))
    assert not any("choices" in problem for problem in batch_problems(batch, [slot(1)], []))


def test_a_variant_keeps_its_originals_number_of_choices():
    source = {"type": "mcq", "difficulty": "easy", "cognitive_level": "remember", "points": 4, "prompt": "Original?"}
    source["choices"] = [{"id": letter, "text": letter} for letter in "ABCDE"]
    variant = make_content(1, "mcq", "easy", 4, cognitive_level="remember", stem_offset=3)
    batch = VariantBatch(variants=[VersionVariant(source_number=1, question=variant, variation="new values")])
    assert any("exactly 5 choices" in problem for problem in variant_problems(batch, {1: source}))


def test_five_choices_asked_for_are_written_in_every_version(api, supabase, db_a, ai):
    def five_choices(call):
        output = well_behaved_interpretation(call)
        output.exam_spec.mcq_choices = 5
        return output

    ai.on("interpret_assessment", five_choices)
    assessment = run(seed_assessment(db_a, duration_minutes=60, number_of_versions=2))
    run(seed_document(supabase, db_a, assessment["id"]))
    spec = interpret_and_approve(api, assessment["id"])["spec"]
    assert spec["mcq_choices"] == 5
    started = api.post(f"/api/assessments/{assessment['id']}/exam", json={"mode": "full"}, headers=auth())
    assert wait_for_run(api, started.json()["run"]["id"])["status"] == "succeeded"

    exam = api.get(f"/api/assessments/{assessment['id']}/exam", headers=auth()).json()
    mcqs = [question for question in exam["questions"] if question["type"] == "mcq"]
    assert mcqs and {tuple(choice["id"] for choice in q["choices"]) for q in mcqs} == {("A", "B", "C", "D", "E")}
    assert len({question["versionId"] for question in mcqs}) == 2  # the second version keeps five too
    assert "exactly 5 choices" in ai.calls_for("generate_questions")[0]["instructions"]


# -- 2 and 3. Section text is for students: no notes, no question numbers or marks -------------------


def test_section_text_with_notes_or_question_marks_is_sent_back():
    leaked = [
        "Question 19 is worth 6 points, and all other questions are worth 4 points.",
        "Under the application's current MCQ format, each question has four choices.",
        "Include all needed case details in the eventual exam.",
        "A note for the question writer: keep the cases short.",
    ]
    for text in leaked:
        hard, _ = plan_problems(plan_with([PlannedSection(title="Questions", instructions=text)]), _spec(), set())
        assert any("Section 1" in problem for problem in hard), text
    # A case about quartiles or a loan application, and plain directions, are fine.
    fine = "A delivery company reports Q1 = 18 and Q3 = 34 minutes for its loan application process.\n\nChoose one answer."
    hard, _ = plan_problems(plan_with([PlannedSection(title="Case Study 1: Deliveries", instructions=fine)]), _spec(), set())
    assert not any("Section 1" in problem for problem in hard)


def test_the_planner_writes_section_text_for_students_and_cases_like_the_previous_exams():
    planner = planner_instructions(DataFramer())
    assert "printed on the student paper" in planner and "question numbers" in planner
    assert "make every case its own section" in planner
    assert "THE SECTIONS" in generator_instructions(DataFramer())


def _spec():
    from tests.test_cost import spec

    return spec(duration_minutes=None, total_points=None, question_format=None, difficulty=None, bloom=None)


# -- 4. Cases: written on the case, checked with it, printed and shown once ------------------------


def test_the_writer_and_the_quality_check_see_each_case(api, supabase, db_a, ai):
    def cases(call):
        plan = well_behaved_plan(call)
        plan.sections = [PlannedSection(title="Case Study 1: Credit Card Fraud Detection", instructions=CASE)]
        plan.questions = [question.model_copy(update={"section_index": 0}) for question in plan.questions]
        return plan

    ai.on("plan_exam", cases)
    ai.on("replan_exam", cases)
    assessment = run(seed_assessment(db_a, duration_minutes=60))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    started = api.post(f"/api/assessments/{assessment['id']}/exam", json={"mode": "full"}, headers=auth())
    assert wait_for_run(api, started.json()["run"]["id"])["status"] == "succeeded"

    writing = ai.calls_for("generate_questions")[0]["input"]
    assert "THE SECTIONS" in writing and "5,000 transactions" in writing
    review = ai.calls_for("review_exam")[-1]["input"]
    assert "SECTION: Case Study 1: Credit Card Fraud Detection" in review and "5,000 transactions" in review

    # A professor's AI revision of one question keeps its case in view too.
    exam = api.get(f"/api/assessments/{assessment['id']}/exam", headers=auth()).json()
    first = exam["questions"][0]
    revised = api.post(f"/api/questions/{first['id']}/revision", json={"preset": "easier"}, headers=auth())
    assert revised.status_code == 200, revised.text
    assert "THE CASE THIS QUESTION BUILDS ON" in ai.calls_for("revise_question")[-1]["input"]


def test_a_case_prints_once_as_normal_text_with_its_table():
    questions = [
        dict(id=f"q{n}", version_id="v", position=n, type="mcq", points=4, section_id="s1", prompt=f"Question on the case {n}?")
        | {"choices": [{"id": letter, "text": f"Choice {letter}{n}"} for letter in "ABCDE"], "subparts": None}
        for n in (1, 2)
    ]
    sections = [dict(id="s1", position=1, title="Case Study 1: Credit Card Fraud Detection", instructions=CASE)]
    full = FullExam(exam={}, versions=[dict(id="v", label="A")], sections=sections, questions=questions)
    data = PdfExportService().build(full, ExportHeader("CMPS 262", "Midterm", 60), "A", answer_key=False, figures={})
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
    assert text.count("5,000 transactions") == 1
    assert "True positives" in text and "|" not in text
    assert "e) Choice E1" in text


# -- 7. The PDF: tables inside choices, and columns that don't break words -------------------------


def test_a_table_inside_a_choice_prints_as_a_table():
    table = "| Channel | Web indicator | Kiosk indicator |\n| --- | --- | --- |\n| Phone | 0 | 0 |\n| Web | 1 | 0 |"
    question = dict(
        id="q1",
        version_id="v",
        position=1,
        type="mcq",
        points=4,
        prompt="Which table applies reference coding?",
        choices=[{"id": letter, "text": table.replace("Phone", f"Phone {letter}")} for letter in "ABCD"],
        subparts=None,
    )
    full = FullExam(exam={}, versions=[dict(id="v", label="A")], sections=[], questions=[question])
    data = PdfExportService().build(full, ExportHeader("CMPS 262", "Midterm", 60), "A", answer_key=False, figures={})
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
    assert "| --- |" not in text and "---" not in text
    assert "a)" in text and "Kiosk indicator" in text and "Phone A" in text


def test_table_columns_fit_their_longest_word():
    from reportlab.pdfbase import pdfmetrics

    rows = [
        ["Policy", "Accuracy", "Recall", "False-positive rate", "Alarms per day", "Missed faults per day"],
        ["Medium threshold", "97.8%", "90%", "2%", "376", "20"],
    ]
    width = 400.0
    widths = _column_widths(rows, 11, width)
    assert abs(sum(widths) - width) < 0.01
    for column, column_width in enumerate(widths):
        longest = max(
            pdfmetrics.stringWidth(word, "ExamBold" if r == 0 else "ExamRoman", 11)
            for r, row in enumerate(rows)
            for word in row[column].split()
        )
        assert column_width >= longest + 16, rows[0][column]


# -- 5 and 6. Plain wording, honest difficulty and reading time ---------------------------------------


def test_the_writer_is_told_to_word_plainly_and_keep_easy_questions_easy():
    writing = generator_instructions(DataFramer())
    assert "never chain clauses with semicolons" in writing
    assert "Put the words every choice would repeat into the stem" in writing
    assert "an easy question must be easy for these students" in writing
    assert "counting reading time" in planner_instructions(DataFramer())
    assert "never a table" in writing
