"""A scripted, well-behaved "model" for tests.

Each handler reads what the request actually asked for (the professor's
settings, the planned questions, the questions to vary…) and returns a valid
structured answer, so the services can be exercised end to end without OpenAI.
Tests that need a misbehaving model register their own handlers.
"""

import json
import re
from typing import Any

from app.schemas.ai import (
    ChoiceOut,
    CoverageTopic,
    DifficultySpec,
    DocumentSummary,
    DocumentTopic,
    ExamPlan,
    ExamSpec,
    FormatSpec,
    GeneratedQuestion,
    InterpretationOutput,
    PlannedQuestion,
    PlannedSection,
    QuestionBatch,
    QuestionContent,
    ReviewOutput,
    RevisedQuestion,
    RubricItem,
    SolutionKey,
    SpecItem,
    StyleProfile,
    SubpartOut,
    VariantBatch,
    VersionVariant,
)
from tests.fakes import FakeAI

STEMS = [
    "A logistics company records delivery times across {topic}; explain which measure best summarizes them",
    "Derive the update rule used in {topic} and state the condition under which it converges",
    "Given a small dataset about hospital admissions, apply {topic} and interpret the result",
    "Compare two competing approaches to {topic} and justify which one a startup should adopt",
    "Identify the flaw in a student's argument about {topic} and correct it",
    "Design an experiment that tests a claim related to {topic}, naming the variables you control",
    "A sensor network produces noisy readings; use {topic} to decide whether the anomaly is real",
    "Explain to a first-year student why {topic} matters, using one concrete numerical example",
    "Critique this proposed policy for applying {topic} to loan approvals",
    "Estimate the computational cost of {topic} for a million records and suggest one optimization",
    "Reconstruct the missing step in the proof sketch for {topic}",
    "Classify the following five cases according to {topic} and explain the borderline one",
]


# Deliberately unlike STEMS, so a revised question never looks like a duplicate.
REVISION_STEMS = [
    "Revised: a regional airline must reschedule {topic} after a storm; propose and defend a plan",
    "Revised: two clinics report conflicting statistics on {topic}; reconcile them quantitatively",
    "Revised: a robotics team's controller misbehaves because of {topic}; diagnose the cause step by step",
    "Revised: an archive of handwritten letters is digitized using {topic}; evaluate the error trade-offs",
]


def _setting(text: str, label: str) -> str | None:
    match = re.search(rf"- {re.escape(label)}: (.+)", text)
    value = match.group(1).strip() if match else None
    return None if value in (None, "not specified") else value


def well_behaved_interpretation(call: dict[str, Any]) -> InterpretationOutput:
    text = call["input"]
    versions = int(_setting(text, "Number of versions") or 1)
    duration = _setting(text, "Duration")
    fmt = _setting(text, "Question format (share of marks)")
    difficulty = _setting(text, "Difficulty (share of marks)")
    question_format = None
    if fmt:
        mcq, subjective = (int(n) for n in re.findall(r"(\d+)%", fmt)[:2])
        question_format = FormatSpec(mcq_percent=mcq, subjective_percent=subjective, source="professor")
    difficulty_spec = None
    if difficulty:
        easy, medium, hard = (int(n) for n in re.findall(r"(\d+)%", difficulty)[:3])
        difficulty_spec = DifficultySpec(easy_percent=easy, medium_percent=medium, hard_percent=hard, source="professor")
    files = re.findall(r'\d+\. "([^"]+)"', text)
    return InterpretationOutput(
        enhanced_prompt=(
            "Write an exam grounded in the uploaded course material that favours application over memorization. "
            "Respect the professor's settings exactly and cover the listed topics in proportion to their emphasis."
        ),
        exam_spec=ExamSpec(
            assessment_title=None,
            course=None,
            assessment_type="Midterm exam" if "midterm" in text.lower() else None,
            duration_minutes=int(duration.split()[0]) if duration else None,
            duration_source="professor" if duration else None,
            versions=versions,
            total_points=100,
            total_points_source="ai_assumption",
            question_count_min=8,
            question_count_max=12,
            question_count_source="ai_assumption",
            question_format=question_format,
            difficulty=difficulty_spec,
            sections=[],
            coverage=[CoverageTopic(topic="gradient descent", emphasis="high", source_documents=files[:1], source="ai_inferred")],
            learning_emphasis=[SpecItem(text="Application over memorization", source="professor")],
            question_style=[],
            professor_notes=[],
            professor_preferences_applied=[],
            source_material_guidance="Use the uploaded lectures.",
            previous_exam_style_guidance=None,
            constraints=[],
            assumptions=["A total of 100 points."],
            warnings=[],
            generation_instructions=["Each hard question combines two concepts."],
        ),
    )


def _spec_from(text: str) -> dict[str, Any]:
    match = re.search(r"APPROVED EXAM SPEC \(approved by the professor; binding\)\n(\{.*?\n\})\n", text, re.S)
    return json.loads(match.group(1)) if match else {}


def well_behaved_plan(call: dict[str, Any]) -> ExamPlan:
    """Ten 10-point questions that hit the spec's splits exactly (for splits in tens)."""
    spec = _spec_from(call["input"])
    fmt = spec.get("question_format") or {"mcq_percent": 40}
    difficulty = spec.get("difficulty") or {"easy_percent": 30, "medium_percent": 40, "hard_percent": 30}
    mcq_count = fmt["mcq_percent"] // 10
    levels = (
        ["easy"] * (difficulty["easy_percent"] // 10)
        + ["medium"] * (difficulty["medium_percent"] // 10)
        + ["hard"] * (difficulty["hard_percent"] // 10)
    )
    minutes = (spec.get("duration_minutes") or 100) * 0.8 / 10
    questions = []
    for number in range(1, 11):
        is_mcq = number <= mcq_count
        questions.append(
            PlannedQuestion(
                number=number,
                section_index=0 if is_mcq else 1,
                type="mcq" if is_mcq else "problem",
                difficulty=levels[number - 1],
                points=10,
                topic=f"topic {number}",
                learning_objective=f"apply concept {number}",
                cognitive_level="application",
                source_hint="Lecture 1",
                estimated_minutes=minutes,
                figure_document_id=None,
            )
        )
    sections = [
        PlannedSection(title="Section A: Multiple choice", instructions=None),
        PlannedSection(title="Section B: Problems", instructions="Show your work."),
    ]
    if mcq_count == 0:
        questions = [q.model_copy(update={"section_index": 0}) for q in questions]
        sections = sections[1:]
    elif mcq_count == 10:
        sections = sections[:1]
    return ExamPlan(sections=sections, questions=questions, rationale="Balanced plan.", coverage_notes="", warnings=[])


def make_content(
    number: int,
    question_type: str,
    difficulty: str,
    points: float,
    *,
    stem_offset: int = 0,
    label: str = "",
    refs: list[str] | None = None,
    stems: list[str] = STEMS,
) -> QuestionContent:
    stem = stems[(number - 1 + stem_offset) % len(stems)].format(topic=f"concept {number}")
    prompt = f"{label}{stem}."
    if question_type == "mcq":
        correct = "ABCD"[(number + stem_offset) % 4]
        return QuestionContent(
            type="mcq",
            difficulty=difficulty,
            points=points,
            prompt=prompt,
            choices=[ChoiceOut(id=letter, text=f"Option {letter} for item {number}{label}") for letter in "ABCD"],
            correct_choice=correct,
            subparts=None,
            answer=f"{correct} is correct.",
            solution=f"Because {correct}.",
            rubric=None,
            explanation="The other options misapply the concept.",
            concepts=[f"concept {number}"],
            learning_objectives=[f"apply concept {number}"],
            source_excerpt_ids=refs or [],
            estimated_minutes=2,
            figure_document_id=None,
        )
    half = points / 2
    return QuestionContent(
        type=question_type,
        difficulty=difficulty,
        points=points,
        prompt=prompt,
        choices=None,
        correct_choice=None,
        subparts=None,
        answer=f"Model answer {number}{label}.",
        solution=f"Worked solution {number}{label}.",
        rubric=[RubricItem(criterion="Method", points=half), RubricItem(criterion="Result", points=points - half)],
        explanation=None,
        concepts=[f"concept {number}"],
        learning_objectives=[f"apply concept {number}"],
        source_excerpt_ids=refs or [],
        estimated_minutes=8,
        figure_document_id=None,
    )


_PLAN_LINE = re.compile(r"^(\d+)\. \[[^\]]*\] (\w+), (\w+), ([\d.]+) pts", re.M)


def well_behaved_batch(call: dict[str, Any]) -> QuestionBatch:
    text = call["input"]
    targets = text.split("WRITE THESE QUESTIONS NOW (exactly as planned)\n", 1)[1]
    refs = re.findall(r"id (S\d+)", text)[:2]
    questions = []
    for number, question_type, difficulty, points in _PLAN_LINE.findall(targets):
        content = make_content(int(number), question_type, difficulty, float(points), refs=refs)
        questions.append(GeneratedQuestion(number=int(number), **content.model_dump()))
    return QuestionBatch(questions=questions, warnings=[])


def well_behaved_single(call: dict[str, Any]) -> QuestionBatch:
    text = call["input"]
    match = re.search(r"Write question (\d+): type (\w+), difficulty (\w+), ([\d.]+) points", text)
    number, question_type, difficulty, points = int(match.group(1)), match.group(2), match.group(3), float(match.group(4))
    offset = 5 if "practical" in text.lower() else 0
    content = make_content(number, question_type, difficulty, points, stem_offset=offset)
    return QuestionBatch(questions=[GeneratedQuestion(number=number, **content.model_dump())], warnings=[])


_QUESTION_HEADER = re.compile(r"^Q(\d+) \[(\w+), (\w+), ([\d.]+) pts\]", re.M)


def well_behaved_variants(call: dict[str, Any]) -> VariantBatch:
    label = re.search(r"Write the Version (\w) equivalent", call["input"]).group(1)
    variants = []
    for number, question_type, difficulty, points in _QUESTION_HEADER.findall(call["input"]):
        content = make_content(int(number), question_type, difficulty, float(points), stem_offset=7, label=f"[{label}] ")
        variants.append(VersionVariant(source_number=int(number), question=content, variation="new scenario"))
    return VariantBatch(variants=variants)


def well_behaved_revision(call: dict[str, Any]) -> RevisedQuestion:
    text = call["input"]
    number, question_type, difficulty, points = _QUESTION_HEADER.search(text).groups()
    target = re.search(r"Its difficulty becomes (\w+)", text)
    new_difficulty = target.group(1) if target else difficulty
    content = make_content(int(number), question_type, new_difficulty, float(points), stems=REVISION_STEMS)
    return RevisedQuestion(question=content, change_summary="Raised the cognitive demand.")


def well_behaved_solution(call: dict[str, Any]) -> SolutionKey:
    text = call["input"]
    header = _QUESTION_HEADER.search(text)
    question_type, points = header.group(2), float(header.group(4))
    subparts = len(re.findall(r"^\s+\([a-z]\) \[", text, re.M))
    if question_type == "mcq":
        return SolutionKey(
            correct_choice="B",
            subpart_answers=None,
            answer="B",
            solution="B follows.",
            rubric=None,
            explanation="Others fail.",
            notes=None,
        )
    return SolutionKey(
        correct_choice=None,
        subpart_answers=[f"answer {i}" for i in range(subparts)] or None,
        answer="New answer",
        solution="New worked solution",
        rubric=None if subparts else [RubricItem(criterion="Correct", points=points)],
        explanation=None,
        notes=None,
    )


def clean_review(_call: dict[str, Any]) -> ReviewOutput:
    return ReviewOutput(summary="The exam is consistent with the spec.", issues=[], estimated_total_minutes=80)


def summary_handler(call: dict[str, Any]) -> DocumentSummary:
    return DocumentSummary(
        title="Lecture notes",
        summary="Covers gradient descent and regression.",
        topics=[DocumentTopic(name="Gradient descent", location="Page 1")],
        low_content=False,
    )


def style_handler(_call: dict[str, Any]) -> StyleProfile:
    return StyleProfile(
        exams_analyzed=1,
        common_question_types=["problem"],
        typical_question_length="short",
        difficulty_characteristics="mostly medium",
        conceptual_vs_computational="balanced",
        recall_vs_application="application-heavy",
        wording_style="direct",
        use_of_scenarios="frequent",
        use_of_subquestions="common",
        section_structure="two sections",
        grading_patterns="10 points per problem",
        recurring_preferences=[],
        limitations=[],
        summary="Applied, scenario-based problems.",
    )


def install_well_behaved(ai: FakeAI) -> FakeAI:
    ai.on("interpret_assessment", well_behaved_interpretation)
    ai.on("plan_exam", well_behaved_plan)
    ai.on("replan_exam", well_behaved_plan)
    ai.on("generate_questions", well_behaved_batch)
    ai.on("generate_question", well_behaved_single)
    ai.on("write_variants", well_behaved_variants)
    ai.on("review_exam", clean_review)
    ai.on("revise_question", well_behaved_revision)
    ai.on("regenerate_solution", well_behaved_solution)
    ai.on("summarize_document", summary_handler)
    ai.on("analyze_previous_exams", style_handler)
    return ai


def subpart(label: str, points: float) -> SubpartOut:
    return SubpartOut(
        label=label,
        prompt=f"Part {label}",
        points=points,
        answer=f"Answer {label}",
        rubric=[RubricItem(criterion="Correct", points=points)],
    )
