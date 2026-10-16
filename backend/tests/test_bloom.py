"""Exam Learning Focus (Bloom's taxonomy): setup rules, the spec, planning, generation and review."""

import asyncio
import hashlib
import json

import pytest

from app.core.errors import InvalidInputError
from app.domain import distribution
from app.domain.setup import BLOOM_PRESETS, AssessmentSetup, check_bloom_distribution
from app.schemas.ai import (
    COGNITIVE_LEVELS,
    BloomSpec,
    ExamPlan,
    ExamSpec,
    FormatSpec,
    GeneratedQuestion,
    PlannedQuestion,
    PlannedSection,
    QuestionBatch,
    VariantBatch,
    VersionVariant,
)
from app.services.exam_generation import batch_problems, plan_problems, suggest_level, variant_problems
from tests.helpers import auth
from tests.model import AUTOMATIC_BLOOM, make_content, well_behaved_interpretation, well_behaved_revision
from tests.seed import seed_assessment, seed_document
from tests.test_workflow import A_SETTINGS, generate, interpret_and_approve

CUSTOM = {"remember": 10, "understand": 15, "apply": 30, "analyze": 25, "evaluate": 15, "create": 5}


def run(coroutine):
    return asyncio.run(coroutine)


def spec_with(bloom: dict[str, int] | None, *, mcq: int | None = None) -> ExamSpec:
    """The scripted interpreter's spec, with a chosen Bloom's split and format."""
    spec = well_behaved_interpretation({"input": "- Number of versions: 1"}).exam_spec
    return spec.model_copy(
        update={
            "bloom": BloomSpec(focus="custom", **{f"{k}_percent": v for k, v in bloom.items()}, source="professor")
            if bloom
            else None,
            "question_format": FormatSpec(mcq_percent=mcq, subjective_percent=100 - mcq, source="professor")
            if mcq is not None
            else None,
            "difficulty": None,
            "total_points": None,
            "question_count_min": None,
            "question_count_max": None,
            "duration_minutes": None,
        }
    )


def slot(number: int, level: str, *, kind: str = "short_answer", points: float = 10) -> PlannedQuestion:
    return PlannedQuestion(
        number=number,
        section_index=0,
        type=kind,
        difficulty="medium",
        points=points,
        topic=f"topic {number}",
        learning_objective=f"objective {number}",
        cognitive_level=level,
        source_hint=None,
        estimated_minutes=5,
        figure_document_id=None,
    )


# -----------------------------------------------------------------------------
# Setup rules (shared by the form, the assistants and the database)
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("focus", "split", "valid"),
    [
        ("automatic", None, True),
        ("balanced", None, True),
        ("practical", None, True),
        ("critical", None, True),
        ("custom", CUSTOM, True),
        ("custom", {**CUSTOM, "create": 10}, False),  # adds up to 105
        ("custom", {**CUSTOM, "create": -5, "apply": 40}, False),  # negative
        ("custom", {**CUSTOM, "create": 101, "remember": -91}, False),  # out of range
        ("custom", {k: v for k, v in CUSTOM.items() if k != "create"}, False),  # a level missing
        ("custom", {**CUSTOM, "synthesis": 0}, False),  # an unknown level
        ("custom", None, False),  # custom without percentages
        ("balanced", CUSTOM, False),  # a preset never stores percentages
        ("bespoke", None, False),  # not a focus
    ],
)
def test_learning_focus_rules(focus, split, valid):
    assert (check_bloom_distribution(focus, split) is None) is valid
    setup = AssessmentSetup(learning_focus=focus, bloom_distribution=split)
    assert ("bloomDistribution" not in setup.errors()) is valid


def test_presets_are_complete_splits():
    for split in BLOOM_PRESETS.values():
        assert set(split) == set(COGNITIVE_LEVELS) and sum(split.values()) == 100


def test_the_database_rejects_an_invalid_focus(db_a):
    with pytest.raises(InvalidInputError):
        run(seed_assessment(db_a, learning_focus="custom", bloom_distribution={**CUSTOM, "create": 50}))
    with pytest.raises(InvalidInputError):
        run(seed_assessment(db_a, learning_focus="practical", bloom_distribution=CUSTOM))


def test_new_and_historical_assessments_default_to_automatic():
    old_row = {"number_of_versions": 1}  # saved before the columns existed
    setup = AssessmentSetup.from_row(old_row)
    assert setup.learning_focus == "automatic" and setup.bloom_distribution is None and setup.errors() == {}
    # An unchanged old assessment keeps its fingerprint, so its approved plan stays valid.
    legacy = {k: v for k, v in setup.to_row().items() if k not in ("learning_focus", "bloom_distribution")}
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert setup.fingerprint() == expected
    # Choosing a focus changes it.
    assert AssessmentSetup(learning_focus="critical").fingerprint() != expected


def test_specs_and_plans_saved_before_bloom_still_load():
    spec = well_behaved_interpretation({"input": "- Number of versions: 1"}).exam_spec.model_dump()
    del spec["bloom"]
    assert ExamSpec.model_validate(spec).bloom is None
    old = slot(1, "apply").model_dump() | {"cognitive_level": "analysis"}  # the label plans used before
    assert PlannedQuestion.model_validate(old).cognitive_level == "analyze"


# -----------------------------------------------------------------------------
# The ExamSpec keeps who decided what
# -----------------------------------------------------------------------------


def test_custom_split_is_binding_and_marked_as_the_professors(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, **A_SETTINGS, learning_focus="custom", bloom_distribution=CUSTOM))
    run(seed_document(supabase, db_a, assessment["id"]))
    approved = interpret_and_approve(api, assessment["id"])
    seen = ai.calls_for("interpret_assessment")[0]["input"]
    assert "Learning focus (Bloom's levels, share of marks): Custom, the professor's own split" in seen
    bloom = approved["spec"]["bloom"]
    assert bloom["focus"] == "custom" and bloom["source"] == "professor"
    assert {level: bloom[f"{level}_percent"] for level in COGNITIVE_LEVELS} == CUSTOM


def test_a_preset_records_the_choice_with_its_split(api, supabase, db_a, ai):
    def ignores_the_preset(call):
        return well_behaved_interpretation({**call, "input": call["input"].replace("% create", "% making")})

    ai.on("interpret_assessment", ignores_the_preset)  # the model's own split is replaced in code
    assessment = run(seed_assessment(db_a, **A_SETTINGS, learning_focus="practical"))
    bloom = interpret_and_approve(api, assessment["id"])["spec"]["bloom"]
    assert bloom["focus"] == "practical" and bloom["source"] == "professor"
    assert {level: bloom[f"{level}_percent"] for level in COGNITIVE_LEVELS} == BLOOM_PRESETS["practical"]


def test_automatic_is_recommended_by_the_ai_not_the_professor(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    bloom = interpret_and_approve(api, assessment["id"])["spec"]["bloom"]
    assert bloom["focus"] == "automatic" and bloom["source"] == "ai_inferred"
    assert [bloom[f"{level}_percent"] for level in COGNITIVE_LEVELS] == AUTOMATIC_BLOOM


def test_a_split_that_needs_written_answers_is_flagged_not_changed(api, supabase, db_a):
    settings = {**A_SETTINGS, "mcq_percentage": 100, "subjective_percentage": 0}
    assessment = run(seed_assessment(db_a, **settings, learning_focus="custom", bloom_distribution=CUSTOM))
    spec = interpret_and_approve(api, assessment["id"])["spec"]
    assert spec["bloom"]["create_percent"] == 5  # kept as the professor set it
    assert any("Create level" in warning and "subjective" in warning for warning in spec["warnings"])
    assert distribution.bloom_conflicts(spec_with(CUSTOM, mcq=95)) == []


def test_changing_the_learning_focus_makes_the_plan_stale(api, supabase, db_a):
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    interpret_and_approve(api, assessment["id"])
    run(db_a.update("exam_projects", {"learning_focus": "critical"}, filters=[("id", "eq", assessment["id"])]))
    interpretation = api.get(f"/api/assessments/{assessment['id']}/interpretation", headers=auth()).json()
    assert interpretation["status"] == "stale"


# -----------------------------------------------------------------------------
# Planning: the split is by marks, checked in code
# -----------------------------------------------------------------------------


def test_bloom_split_is_by_marks_not_question_count():
    questions = [slot(1, "analyze", points=20), *(slot(n, "remember", kind="mcq", points=2) for n in range(2, 7))]
    shares = distribution.summarize(questions)["bloom"]
    assert shares["analyze"] == 66.7 and shares["remember"] == 33.3
    # Older questions without a level don't produce a (wrong) split.
    assert distribution.summarize([{"points": 5, "cognitive_level": None}])["bloom"] is None


def test_plan_checks_the_bloom_split_and_impossible_slots():
    spec = spec_with({"remember": 0, "understand": 0, "apply": 50, "analyze": 50, "evaluate": 0, "create": 0})
    sections = [PlannedSection(title="Questions", instructions=None)]

    def plan(*levels):
        return ExamPlan(
            sections=sections,
            questions=[slot(n, lvl) for n, lvl in enumerate(levels, 1)],
            rationale="",
            coverage_notes="",
            warnings=[],
        )

    assert plan_problems(plan("apply", "analyze"), spec, set()) == ([], [])
    hard, soft = plan_problems(plan("apply", "apply"), spec, set())
    assert not hard and any("Analyze-level questions are 0% of the marks" in problem for problem in soft)
    mcq_create = plan("apply", "analyze").model_copy(update={"questions": [slot(1, "apply"), slot(2, "create", kind="mcq")]})
    hard, _ = plan_problems(mcq_create, spec, set())
    assert any("can't assess Create" in problem for problem in hard)


def test_generation_gets_and_keeps_each_questions_level(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, **A_SETTINGS, learning_focus="custom", bloom_distribution=CUSTOM))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    exam = generate(api, assessment["id"])
    # The writer is told each question's planned level and what the levels mean.
    call = ai.calls_for("generate_questions")[0]
    assert "Bloom's level: " in call["input"] and "BLOOM'S LEVELS" in call["instructions"]
    assert "BLOOM'S PLANNING" in ai.calls_for("plan_exam")[0]["instructions"]
    # Every question records a level, and its equivalent in Version B has the same one.
    by_slot: dict[str, set[str]] = {}
    for question in exam["questions"]:
        assert question["cognitiveLevel"] in COGNITIVE_LEVELS
        by_slot.setdefault(question["slotId"], set()).add(question["cognitiveLevel"])
    assert all(len(levels) == 1 for levels in by_slot.values())
    assert all(not (q["type"] == "mcq" and q["cognitiveLevel"] == "create") for q in exam["questions"])
    assert "BLOOM'S LEVELS" in ai.calls_for("review_exam")[0]["instructions"]


def test_a_question_at_the_wrong_level_is_sent_back():
    content = make_content(1, "short_answer", "medium", 10, cognitive_level="remember")
    batch = QuestionBatch(questions=[GeneratedQuestion(number=1, **content.model_dump())], warnings=[])
    assert any("must assess the analyze level" in problem for problem in batch_problems(batch, [slot(1, "analyze")], []))
    variant = make_content(1, "short_answer", "medium", 10, cognitive_level="apply", label="[B] ")
    variants = VariantBatch(variants=[VersionVariant(source_number=1, question=variant, variation="new values")])
    source = {"type": "short_answer", "difficulty": "medium", "points": 10, "prompt": "Original", "cognitive_level": "analyze"}
    assert any("same Bloom's level" in problem for problem in variant_problems(variants, {1: source}))


def test_build_with_ai_suggests_the_level_furthest_behind():
    spec = spec_with({"remember": 20, "understand": 0, "apply": 40, "analyze": 0, "evaluate": 0, "create": 40})
    assert suggest_level(spec, [], "short_answer") in ("apply", "create")
    done = [{"points": 10, "cognitive_level": "apply", "type": "short_answer"}]
    assert suggest_level(spec, done, "short_answer") == "create"
    # Multiple choice never gets Create.
    assert suggest_level(spec, done, "mcq") != "create"
    # Without a split, Build with AI still mixes levels (around the Balanced preset).
    assert suggest_level(spec_with(None), [], "mcq") in COGNITIVE_LEVELS


def test_build_with_ai_follows_a_requested_level(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    interpret_and_approve(api, assessment["id"])
    exam = api.post(f"/api/assessments/{assessment['id']}/exam", json={"mode": "interactive"}, headers=auth()).json()["exam"]
    tool = f"/api/exams/{exam['id']}/builder/tools/generate_next_question"
    result = api.post(tool, json={"arguments": {"cognitive_level": "evaluate", "question_type": "long_answer"}}, headers=auth())
    assert result.json()["question"]["cognitiveLevel"] == "evaluate"
    refused = api.post(tool, json={"arguments": {"cognitive_level": "create", "question_type": "mcq"}}, headers=auth()).json()
    assert refused["ok"] is False and "Create" in refused["error"]


# -----------------------------------------------------------------------------
# Editing keeps the level consistent
# -----------------------------------------------------------------------------


def test_revisions_and_edits_keep_the_level_consistent(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a, **{**A_SETTINGS, "number_of_versions": 1}))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    question = next(q for q in generate(api, assessment["id"])["questions"] if q["type"] != "mcq")

    # "Make clearer" doesn't change what the question tests, so its level must stay.
    def drifts(call):
        revised = well_behaved_revision(call)
        other = "remember" if question["cognitiveLevel"] != "remember" else "apply"
        return revised.model_copy(update={"question": revised.question.model_copy(update={"cognitive_level": other})})

    ai.on("revise_question", drifts)
    kept = api.post(f"/api/questions/{question['id']}/revision", json={"preset": "clearer"}, headers=auth())
    # The drifting answer failed validation (and its one repair): nothing is saved.
    assert kept.json()["error"]["code"] == "ai_invalid_output"
    after = api.get(f"/api/exams/{question['examId']}", headers=auth()).json()
    assert next(q for q in after["questions"] if q["id"] == question["id"])["cognitiveLevel"] == question["cognitiveLevel"]

    # The professor can relabel a question by hand; the change is in its history.
    edited = api.patch(f"/api/questions/{question['id']}", json={"cognitiveLevel": "evaluate"}, headers=auth()).json()
    assert edited["question"]["cognitiveLevel"] == "evaluate"
    revisions = api.get(f"/api/questions/{question['id']}/revisions", headers=auth()).json()
    assert revisions[0]["snapshot"]["cognitive_level"] == question["cognitiveLevel"]
    invalid = api.patch(f"/api/questions/{question['id']}", json={"cognitiveLevel": "synthesis"}, headers=auth())
    assert invalid.status_code == 422
