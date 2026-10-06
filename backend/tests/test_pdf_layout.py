"""Preservation and pagination of printed mathematical questions and tables."""

import io

from pypdf import PdfReader
from reportlab.platypus import Paragraph, Table

from app.services.exam_store import FullExam
from app.services.pdf_export import ExportHeader, PdfExportService, rich_text
from app.services.pdf_math import Equation, is_equation, math_source


def test_equations_have_fraction_rules_and_keep_prose_as_prose():
    for text in (
        "Recall = TP/(TP + FN)",
        "g(z) = 1/(1 + e^(−z))",
        "Gⱼ = (1/2) Σ (pᵢ − yᵢ)φᵢⱼ",
    ):
        assert is_equation(text)
        equation = Equation(text)
        assert equation.layout.rects  # Real fraction bars, not slash text.
        assert equation.wrap(100, 500)[0] == 100
        assert equation.layout.glyphs
    assert not is_equation("Use recall = TP/(TP + FN) to select the threshold.")


def test_fraction_typesetting_preserves_powers_functions_signs_and_grouping():
    assert math_source("J = x^2/y") == r"J =\frac{x^2}{y}"
    assert math_source("J = exp(x)/2") == r"J =\frac{\exp (x)}{2}"
    assert math_source("J = a-x/y") == r"J = a-\frac{x}{y}"
    assert math_source("J = x/(-y)") == r"J =\frac{x}{-y}"
    for text in ("J = (x+1)^2/y", "J = x/(y/z)", "J = 1/(1 + exp(-s))"):
        assert Equation(text).layout.rects


def test_pipe_and_legacy_numeric_tables_preserve_cells_and_not_probability_prose():
    for text in (
        "Probability p | Actual disease present | Actual disease absent\n0.82 | 3 | 0\n0.56 | 1 | 5",
        "Feature Minimum Maximum Mean Standard deviation\nx1 0 2000 1000 250\nx2 0 2 1 0.25",
        "Category Desired p(y = 1)\nRed g(−1) ≈ 0.269\nBlue g(1) ≈ 0.731\nGreen g(−1) ≈ 0.269",
        "| Class | W | X | Y | Z |\n| --- | --- | --- | --- | --- |\n| Score | 0.73 | 0.61 | 0.79 | 0.66 |",
    ):
        assert any(isinstance(item, Table) for item in rich_text(text))
    probability = "A model outputs P(y = 1 | x).\nChoose the correct interpretation."
    assert all(isinstance(item, Paragraph) for item in rich_text(probability))


def test_long_table_repeats_header_and_export_keeps_all_questions_and_cells():
    rows = "\n".join(f"{i} | {i + 100}" for i in range(80))
    questions = [
        dict(
            id="q1",
            version_id="v",
            position=0,
            type="problem",
            points=5,
            prompt="Use the observations below.\n\nObservation | Measurement\n" + rows,
            choices=None,
            subparts=None,
        ),
        dict(
            id="q2",
            version_id="v",
            position=1,
            type="problem",
            points=5,
            prompt="Calculate the score.\n\nRecall = TP/(TP + FN)\n\nExplain your method.",
            choices=None,
            subparts=None,
        ),
    ]
    full = FullExam(exam={}, versions=[dict(id="v", label="A")], sections=[], questions=questions)
    data = PdfExportService().build(
        full, ExportHeader("CMPS 261 - Machine Learning", "Midterm", 60), "A", answer_key=False, figures={}
    )
    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() for page in reader.pages]
    assert len(pages) >= 3
    assert sum("Observation" in text for text in pages) >= 2
    combined = "\n".join(pages)
    assert "179" in combined and "2) Calculate the score" in combined and "Explain your method" in combined
    assert "Measurement" in combined and "|" not in combined
