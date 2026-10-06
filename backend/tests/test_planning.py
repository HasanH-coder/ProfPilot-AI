"""Question order: plans and Build with AI mix difficulties and topics, unless the professor asks otherwise."""

import asyncio

import pytest

from app.ai.openai_service import Usage
from app.ai.prompts import DataFramer, planner_instructions
from app.domain import distribution
from app.schemas.ai import CoverageTopic, DifficultySpec, ExamPlan, FormatSpec, PlannedQuestion, PlannedSection
from app.services.exam_generation import (
    ExamPlanningService,
    interleave_plan,
    is_clustered,
    load_generation_context,
    suggest_next,
)
from tests.helpers import auth
from tests.model import well_behaved_interpretation
from tests.seed import seed_assessment, seed_document
from tests.test_workflow import A_SETTINGS, generate, interpret_and_approve

LECTURES = ["Lecture 1.pdf", "Lecture 2.pdf", "Lecture 3.pdf"]


def run(coroutine):
    return asyncio.run(coroutine)


def planned(number: int, difficulty: str, lecture: int, *, section: int = 0, kind: str = "short_answer") -> PlannedQuestion:
    return PlannedQuestion(
        number=number,
        section_index=section,
        type=kind,
        difficulty=difficulty,
        points=10,
        topic=f"lecture {lecture} topic {number}",
        learning_objective=f"apply idea {number}",
        cognitive_level="application",
        source_hint=f"{LECTURES[lecture - 1]}, slides {number}–{number + 2}",
        estimated_minutes=8,
        figure_document_id=None,
    )


def grouped_plan() -> ExamPlan:
    """Nine questions sorted by difficulty and by lecture: the pattern to avoid."""
    rows = [("easy", 1), ("easy", 1), ("easy", 1), ("medium", 2), ("medium", 2), ("medium", 2), ("hard", 3), ("hard", 3)]
    rows.append(("hard", 3))
    questions = [planned(number, difficulty, lecture) for number, (difficulty, lecture) in enumerate(rows, start=1)]
    return ExamPlan(
        sections=[PlannedSection(title="Questions", instructions=None)],
        questions=questions,
        rationale="",
        coverage_notes="",
        warnings=[],
    )


def lecture_of(question: PlannedQuestion) -> str:
    return question.source_hint.split(",")[0]


def splits(plan: ExamPlan) -> dict:
    return distribution.summarize(
        [{"type": q.type, "difficulty": q.difficulty, "points": q.points, "estimated_minutes": 8} for q in plan.questions]
    )


@pytest.mark.parametrize(
    ("sequence", "clustered"),
    [
        (["easy", "easy", "easy", "medium", "medium", "hard", "hard"], True),
        (["medium", "easy", "hard", "medium", "easy", "medium", "hard"], False),
        (["easy", "medium", "medium", "hard"], True),  # sorted by difficulty
        (["medium", "easy", "easy", "easy", "hard", "medium", "hard"], True),  # a long run that could be broken up
        (["medium", "medium", "easy", "medium", "medium", "hard", "medium", "medium"], False),  # runs can't be shorter
        (["medium", "medium", "medium", "easy", "medium", "medium", "medium", "hard"], True),  # but these could be
        (["easy", "easy", "hard"], False),  # too few questions to insist
        (["medium"] * 6, False),  # one level only
    ],
)
def test_clustering_is_detected_only_where_mixing_is_possible(sequence, clustered):
    assert is_clustered(sequence) is clustered


def test_a_grouped_plan_is_interleaved_without_changing_the_splits():
    plan = grouped_plan()
    mixed = interleave_plan(plan, LECTURES)
    difficulties = [q.difficulty for q in mixed.questions]
    lectures = [lecture_of(q) for q in mixed.questions]
    assert not is_clustered(difficulties) and not is_clustered(lectures)
    assert difficulties[:3] != ["easy", "easy", "easy"] and len(set(difficulties[:3])) == 3
    # Only the order changed: same questions, numbered 1, 2, 3… in the new order.
    assert [q.number for q in mixed.questions] == list(range(1, 10))
    assert sorted(q.topic for q in mixed.questions) == sorted(q.topic for q in plan.questions)
    assert splits(mixed) == splits(plan)
    # Deterministic: the same plan always gives the same order.
    assert [q.topic for q in interleave_plan(grouped_plan(), LECTURES).questions] == [q.topic for q in mixed.questions]


def test_sections_and_their_type_grouping_are_kept():
    mcq = [
        planned(n, d, lecture, section=0, kind="mcq") for n, (d, lecture) in enumerate([("easy", 1)] * 3 + [("hard", 2)] * 2, 1)
    ]
    written = [
        planned(5 + n, d, lecture, section=1, kind=kind)
        for n, (d, lecture, kind) in enumerate(
            [("easy", 1, "short_answer"), ("easy", 2, "short_answer"), ("hard", 3, "short_answer"), ("medium", 1, "problem")],
            1,
        )
    ]
    plan = ExamPlan(
        sections=[PlannedSection(title="A", instructions=None), PlannedSection(title="B", instructions=None)],
        questions=[*mcq, *written],
        rationale="",
        coverage_notes="",
        warnings=[],
    )
    mixed = interleave_plan(plan, LECTURES)
    assert [q.section_index for q in mixed.questions] == [0] * 5 + [1] * 4
    assert [q.type for q in mixed.questions][5:] == ["short_answer"] * 3 + ["problem"]
    assert [q.difficulty for q in mixed.questions][:5] != ["easy", "easy", "easy", "hard", "hard"]


def test_an_already_mixed_plan_is_left_as_planned():
    rows = [("medium", 1), ("easy", 2), ("hard", 3), ("medium", 2), ("easy", 3), ("hard", 1)]
    plan = grouped_plan().model_copy(update={"questions": [planned(n, d, lec) for n, (d, lec) in enumerate(rows, 1)]})
    assert interleave_plan(plan, LECTURES) is plan


def use_plan(ai, plan: ExamPlan) -> None:
    """The planner (and its one repair attempt) return this plan."""
    ai.on("plan_exam", lambda _call: plan)
    ai.on("replan_exam", lambda _call: plan)


def seeded_context(supabase, db_a, ai, **settings):
    assessment = run(seed_assessment(db_a, **{**A_SETTINGS, **settings}))
    for name in LECTURES:
        run(seed_document(supabase, db_a, assessment["id"], name=name))
    return assessment


def test_planning_mixes_the_planners_grouped_order(api, supabase, db_a, ai):
    assessment = seeded_context(supabase, db_a, ai)
    interpret_and_approve(api, assessment["id"])
    use_plan(ai, grouped_plan())
    ctx = run(load_generation_context(db_a, assessment["id"]))
    plan = run(ExamPlanningService(db_a, ai, Usage()).plan(ctx))
    assert not is_clustered([q.difficulty for q in plan.questions])
    assert not is_clustered([lecture_of(q) for q in plan.questions])
    # The order is fixed in code: no extra model call for it.
    assert len(ai.calls_for("plan_exam")) == 1 and len(ai.calls_for("replan_exam")) <= 1


@pytest.mark.parametrize(
    "request_text",
    ["Put easy questions first.", "Organize the exam lecture by lecture.", "Start with easy warm-up questions."],
)
def test_an_explicit_order_from_the_professor_is_kept(api, supabase, db_a, ai, request_text):
    assessment = seeded_context(supabase, db_a, ai, professor_prompt=request_text)
    interpret_and_approve(api, assessment["id"])
    use_plan(ai, grouped_plan())
    ctx = run(load_generation_context(db_a, assessment["id"]))
    plan = run(ExamPlanningService(db_a, ai, Usage()).plan(ctx))
    assert [q.difficulty for q in plan.questions] == [q.difficulty for q in grouped_plan().questions]


def test_an_order_the_planner_reports_as_requested_is_kept(api, supabase, db_a, ai):
    assessment = seeded_context(supabase, db_a, ai)
    interpret_and_approve(api, assessment["id"])
    use_plan(ai, grouped_plan().model_copy(update={"ordering_request": "Easy questions first."}))
    ctx = run(load_generation_context(db_a, assessment["id"]))
    plan = run(ExamPlanningService(db_a, ai, Usage()).plan(ctx))
    assert [q.number for q in plan.questions] == list(range(1, 10))
    assert [q.difficulty for q in plan.questions] == [q.difficulty for q in grouped_plan().questions]


def test_the_planner_is_told_to_mix_unless_the_professor_asks_for_an_order():
    text = planner_instructions(DataFramer())
    for rule in (
        "Unless the professor explicitly asks for a particular order",
        "never all the easy questions first, then medium, then hard",
        "interleave topics and lectures",
        "Sections may still group question types",
        "follow it exactly and summarise their request in ordering_request",
    ):
        assert rule in text, rule


def test_a_full_exam_is_written_in_the_mixed_order(api, supabase, db_a, ai):
    assessment = seeded_context(supabase, db_a, ai)
    interpret_and_approve(api, assessment["id"])
    exam = generate(api, assessment["id"])
    first = exam["versions"][0]["id"]
    questions = sorted((q for q in exam["questions"] if q["versionId"] == first), key=lambda q: q["number"])
    # The fake planner groups by difficulty (easy, easy, easy, medium…); the exam doesn't.
    assert [q["difficulty"] for q in questions][:3] != ["easy", "easy", "easy"]
    assert [q["number"] for q in questions] == list(range(1, len(questions) + 1))
    # Versions stay aligned question by question.
    second = exam["versions"][1]["id"]
    twins = {q["slotId"]: q for q in exam["questions"] if q["versionId"] == second}
    assert all(
        (twins[q["slotId"]]["difficulty"], twins[q["slotId"]]["number"]) == (q["difficulty"], q["number"]) for q in questions
    )


# -- Build with AI: the next question keeps the mix ----------------------------------------


def spec_with(**changes):
    spec = well_behaved_interpretation({"input": ""}).exam_spec
    return spec.model_copy(
        update={
            "difficulty": DifficultySpec(easy_percent=30, medium_percent=40, hard_percent=30, source="professor"),
            "question_format": FormatSpec(mcq_percent=40, subjective_percent=60, source="professor"),
            **changes,
        }
    )


def build_sequence(spec, count: int) -> list[dict]:
    """Generates `count` questions the way Build with AI does when the professor just says 'next'."""
    questions: list[dict] = []
    for number in range(1, count + 1):
        suggestion = suggest_next(spec, questions)
        questions.append(
            {
                "type": suggestion["type"],
                "difficulty": suggestion["difficulty"],
                "points": suggestion["points"],
                "estimated_minutes": 5,
                "prompt": f"Question {number} about {suggestion['topic'] or 'the course'}",
                "concepts": [suggestion["topic"]] if suggestion["topic"] else [],
                "learning_objectives": [],
                "topic": suggestion["topic"],
            }
        )
    return questions


def test_next_questions_mix_difficulties_while_converging_to_the_target():
    questions = build_sequence(spec_with(), 12)
    difficulties = [q["difficulty"] for q in questions]
    assert not is_clustered(difficulties)
    assert set(difficulties[:3]) == {"easy", "medium", "hard"}  # not all the easy ones first
    assert all(len(set(difficulties[i : i + 3])) > 1 for i in range(len(difficulties) - 2))  # never three in a row
    summary = distribution.summarize(questions)
    assert abs(summary["easyPercent"] - 30) <= 10 and abs(summary["mediumPercent"] - 40) <= 10
    assert abs(summary["hardPercent"] - 30) <= 10


def test_next_questions_interleave_topics_and_lectures():
    coverage = [
        CoverageTopic(topic=topic, emphasis="normal", source_documents=[lecture], source="ai_inferred")
        for topic, lecture in [
            ("Gradient descent", "Lecture 1.pdf"),
            ("Backpropagation", "Lecture 2.pdf"),
            ("Regularization", "Lecture 3.pdf"),
        ]
    ]
    topics = [q["topic"] for q in build_sequence(spec_with(coverage=coverage), 9)]
    assert set(topics[:3]) == {"Gradient descent", "Backpropagation", "Regularization"}
    assert all(topics[i] != topics[i + 1] for i in range(len(topics) - 1))
    assert not is_clustered(topics)


def test_the_professors_explicit_difficulty_wins(api, supabase, db_a):
    from tests.test_builder import NULL_QUESTION, start_builder

    _, exam = start_builder(api, supabase, db_a)
    for _ in range(3):
        response = api.post(
            f"/api/exams/{exam['id']}/builder/tools/generate_next_question",
            json={"arguments": {**NULL_QUESTION, "difficulty": "easy", "instructions": "Give me three easy questions first."}},
            headers=auth(),
        ).json()
        assert response["ok"] is True
    questions = api.get(f"/api/exams/{exam['id']}", headers=auth()).json()["questions"]
    first = sorted(q["number"] for q in questions if q["difficulty"] == "easy")
    assert first[:3] == [1, 2, 3]
