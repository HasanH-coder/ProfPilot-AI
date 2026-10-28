"""Lecture Knowledge Profiles: complete coverage, faithful sources, reuse, failure, isolation and use in generation."""

import asyncio
import re

import pytest

from app.ai.prompts import DataFramer
from app.core.errors import AIServiceError
from app.domain.distribution import bloom_conflicts
from app.schemas.ai import BloomSpec, ExamPlan, SegmentKnowledge
from app.services import lecture_knowledge
from app.services.assessment_context import AssessmentContextService
from app.services.document_ingestion import DocumentIngestionService
from app.services.exam_generation import plan_problems
from app.services.lecture_knowledge import (
    LectureKnowledgeService,
    render_inventory,
    render_topics,
    split_segments,
)
from tests.helpers import auth
from tests.model import segment_handler, well_behaved_plan
from tests.seed import pdf_bytes, seed_assessment, seed_course, seed_course_source, seed_document
from tests.test_workflow import A_SETTINGS, generate, interpret_and_approve


def run(coroutine):
    return asyncio.run(coroutine)


def long_page(number: int) -> str:
    """About 3,000 characters of distinct lecture text."""
    words = f"topic{number}alpha topic{number}beta regularization learning rate validation curve bias variance".split()
    return "\n".join(" ".join(words[(line + i) % len(words)] for i in range(14)) for line in range(30))


def process(db, ai, document_id):
    record = run(AssessmentContextService(db).get_document(document_id))
    return run(DocumentIngestionService(db, ai).process(record))


def knowledge_of(db, document_id):
    return run(LectureKnowledgeService(db, None).get(document_id))  # type: ignore[arg-type]


# -- Building the profile --------------------------------------------------------------------


def test_a_long_lecture_is_analysed_section_by_section_with_every_page_accounted_for(supabase, db_a, ai):
    course = run(seed_course(db_a))
    source = run(seed_course_source(supabase, db_a, course["id"], data=pdf_bytes([long_page(n) for n in range(1, 13)])))
    processed = process(db_a, ai, source["id"])

    knowledge = knowledge_of(db_a, source["id"])
    assert knowledge["status"] == "ready" and knowledge["content_sha256"] == processed.content_sha256
    segments = ai.calls_for("analyze_lecture_segment")
    assert len(segments) >= 2 and knowledge["coverage"]["segments"] == len(segments)
    # Every page was analysed: none missing, nothing truncated.
    assert knowledge["coverage"]["total_units"] == 12 and knowledge["coverage"]["analysed_units"] == 12
    assert knowledge["coverage"]["units_without_text"] == [] and not knowledge["coverage"]["truncated"]
    # The later pages reached the model too (no silent truncation).
    assert "topic12alpha" in segments[-1]["input"]
    # Topics have stable ids and real locations; the short summary comes from the profile.
    assert [topic["id"] for topic in knowledge["topics"]][:2] == ["t1", "t2"]
    assert all(re.fullmatch(r"Pages? [\d–-]+", loc) for t in knowledge["topics"] for loc in t["locations"])
    assert processed.summary["title"] == "Lecture notes"
    # Token usage is recorded with the profile.
    assert knowledge["usage"]["model_calls"] == len(segments) + 1 and knowledge["usage"]["input_tokens"] > 0


def test_split_segments_keeps_order_and_respects_the_budget():
    chunks = [{"content": "x" * 1000, "chunk_index": i} for i in range(10)]
    segments = split_segments(chunks, budget=3000)
    assert [len(s) for s in segments] == [3, 3, 3, 1]
    assert [c["chunk_index"] for s in segments for c in s] == list(range(10))


def test_pages_without_text_are_reported_not_silently_skipped(supabase, db_a, ai):
    course = run(seed_course(db_a))
    # A mostly-text deck with one figure-only page (too few to be sent for transcription).
    pages = [
        "Gradient descent minimises the loss step by step.",
        "",
        "Learning rate controls the step size of each update.",
        "Momentum averages past gradients to smooth the updates.",
        "Early stopping halts training when validation loss rises.",
    ]
    source = run(seed_course_source(supabase, db_a, course["id"], data=pdf_bytes(pages)))
    process(db_a, ai, source["id"])
    knowledge = knowledge_of(db_a, source["id"])
    assert knowledge["coverage"]["units_without_text"] == [2]
    assert any("Page 2 has no extractable text" in warning for warning in knowledge["warnings"])


def test_invented_locations_are_sent_back_for_repair(supabase, db_a, ai):
    def invents_then_corrects(call):
        good = segment_handler(call)
        bad = good.model_copy(deep=True)
        bad.topics[0].locations = ["Slide 99"]
        return [bad, good]

    ai.on("analyze_lecture_segment", invents_then_corrects)
    course = run(seed_course(db_a))
    source = run(seed_course_source(supabase, db_a, course["id"]))
    process(db_a, ai, source["id"])
    call = ai.calls_for("analyze_lecture_segment")[0]
    assert "Slide 99" in call["problems"][0][0] and call["problems"][1] == []
    assert knowledge_of(db_a, source["id"])["status"] == "ready"


def test_formulas_are_kept_as_written_and_uncertain_ones_are_flagged(supabase, db_a, ai):
    def uncertain(call):
        result = segment_handler(call)
        for topic in result.topics:
            for formula in topic.formulas:
                formula.uncertain = True
        return result

    ai.on("analyze_lecture_segment", uncertain)
    course = run(seed_course(db_a))
    source = run(seed_course_source(supabase, db_a, course["id"], data=pdf_bytes(["The learning rate eta scales the gradient."])))
    record = process(db_a, ai, source["id"])
    knowledge = {source["id"]: knowledge_of(db_a, source["id"])}
    rendered = render_topics(["L1.t1"], [record], knowledge, DataFramer())
    # Marked unverified, with the extracted text kept only as labelled evidence, after the warning.
    assert "formula UNVERIFIED (extracted from the PDF, possibly garbled; never quote it): update rule" in rendered
    assert "no reliable standard form: don't base a question on this formula's exact form" in rendered
    assert rendered.index("UNVERIFIED") < rendered.index("Extracted evidence, for reference only: w = w - \\eta \\nabla L")


def test_a_proposed_standard_form_is_kept_apart_from_the_evidence_and_never_presented_as_the_lecture():
    from app.services.lecture_knowledge import render_formula

    formula = {
        "name": "sigmoid", "expression": "𝒈 𝒛 = 𝟏 𝟏+𝒆−𝒛", "normalized": "g(z) = 1 / (1 + e^{-z})",
        "variables": [], "conditions": None, "locations": ["Page 9"], "uncertain": True,
    }  # fmt: skip
    line = render_formula(formula)
    assert "proposed standard form (ProfPilot's reading, not confirmed by the lecture): g(z) = 1 / (1 + e^{-z})" in line
    assert line.endswith("Extracted evidence, for reference only: 𝒈 𝒛 = 𝟏 𝟏+𝒆−𝒛") and "(Page 9)" in line
    # A verified formula is shown exactly as written; any proposed form is ignored.
    verified = render_formula({**formula, "expression": "g(z) = 1/(1+e^{-z})", "uncertain": False})
    assert verified == "- formula: sigmoid: g(z) = 1/(1+e^{-z}) (Page 9)"


# -- Reuse, versions, failure and duplicates ---------------------------------------------------


def test_a_profile_is_built_once_and_reused_by_an_identical_file(supabase, db_a, ai):
    course = run(seed_course(db_a))
    data = pdf_bytes(["Bias variance trade-off and regularization."])
    first = run(seed_course_source(supabase, db_a, course["id"], data=data))
    process(db_a, ai, first["id"])
    calls = len(ai.calls)
    # Processing it again costs nothing.
    process(db_a, ai, first["id"])
    assert len(ai.calls) == calls
    # The same bytes elsewhere (an assessment upload) copies the chunks and the profile.
    assessment = run(seed_assessment(db_a, course_id=course["id"]))
    copy = run(seed_document(supabase, db_a, assessment["id"], data=data))
    process(db_a, ai, copy["id"])
    assert len(ai.calls) == calls
    assert knowledge_of(db_a, copy["id"])["topics"] == knowledge_of(db_a, first["id"])["topics"]


def test_a_copy_of_an_unprofiled_lecture_is_prepared_too(supabase, db_a, ai):
    # An existing lecture read before profiles existed, and the same file uploaded again elsewhere.
    course = run(seed_course(db_a))
    data = pdf_bytes(["Logistic regression models the log-odds as a linear function."])
    original = run(seed_course_source(supabase, db_a, course["id"], data=data))
    process(db_a, ai, original["id"])
    run(db_a.update("documents", {"knowledge": None}, filters=[("id", "eq", original["id"])]))
    embedded = ai.embedding_calls

    assessment = run(seed_assessment(db_a, course_id=course["id"]))
    copy = run(seed_document(supabase, db_a, assessment["id"], data=data))
    processed = process(db_a, ai, copy["id"])
    # Its text was copied (no new reading), and it was still left ready for AI work.
    assert processed.processing_status == "ready" and ai.embedding_calls == embedded
    assert knowledge_of(db_a, copy["id"])["status"] == "ready"


def test_an_outdated_profile_isnt_rebuilt_while_an_assessment_reads_its_files(api, supabase, db_a, ai, monkeypatch):
    course = run(seed_course(db_a))
    source = run(seed_course_source(supabase, db_a, course["id"]))
    process(db_a, ai, source["id"])
    monkeypatch.setattr(lecture_knowledge, "KNOWLEDGE_VERSION", lecture_knowledge.KNOWLEDGE_VERSION + 1)

    calls = len(ai.calls)
    assessment = run(seed_assessment(db_a, course_id=course["id"]))
    run(db_a.insert("assessment_documents", {"exam_project_id": assessment["id"], "document_id": source["id"]}))
    documents = run(AssessmentContextService(db_a).list_documents(assessment["id"]))
    run(DocumentIngestionService(db_a, ai).process_pending(documents))  # e.g. "Improve with AI"
    assert len(ai.calls) == calls

    state = api.get(f"/api/documents/{source['id']}/knowledge", headers=auth()).json()
    assert state["state"] == "outdated" and state["estimate"]["modelCalls"] >= 2
    assert api.post(f"/api/documents/{source['id']}/knowledge", headers=auth()).status_code == 202
    assert api.get(f"/api/documents/{source['id']}/knowledge", headers=auth()).json()["state"] == "ready"


def test_a_failed_profile_leaves_the_file_usable_and_can_be_retried(api, supabase, db_a, ai):
    ai.on("analyze_lecture_segment", lambda _call: AIServiceError("The AI service is busy."))
    course = run(seed_course(db_a))
    source = run(seed_course_source(supabase, db_a, course["id"]))
    processed = process(db_a, ai, source["id"])
    assert processed.processing_status == "ready"  # still searchable
    state = api.get(f"/api/documents/{source['id']}/knowledge", headers=auth()).json()
    assert state["state"] == "failed" and "busy" in state["error"]

    ai.on("analyze_lecture_segment", segment_handler)
    api.post(f"/api/documents/{source['id']}/knowledge", headers=auth())
    assert api.get(f"/api/documents/{source['id']}/knowledge", headers=auth()).json()["state"] == "ready"


def test_a_profile_being_built_is_not_started_twice(api, supabase, db_a, ai, monkeypatch):
    course = run(seed_course(db_a))
    source = run(seed_course_source(supabase, db_a, course["id"]))
    process(db_a, ai, source["id"])
    calls = len(ai.calls)
    monkeypatch.setattr(lecture_knowledge, "_building", {source["id"]})
    reply = api.post(f"/api/documents/{source['id']}/knowledge", headers=auth())
    assert reply.status_code == 202 and reply.json() == {"state": "building"} and len(ai.calls) == calls


def test_an_interrupted_build_reads_as_failed_so_it_can_be_rebuilt(api, supabase, db_a, ai):
    course = run(seed_course(db_a))
    source = run(seed_course_source(supabase, db_a, course["id"]))
    process(db_a, ai, source["id"])
    run(db_a.update("documents", {"knowledge": {"status": "building", "version": 1}}, filters=[("id", "eq", source["id"])]))
    state = api.get(f"/api/documents/{source['id']}/knowledge", headers=auth()).json()
    assert state["state"] == "failed" and "interrupted" in state["error"]


# -- Isolation ---------------------------------------------------------------------------------


def test_another_professor_can_neither_read_nor_build_a_profile(api, supabase, db_a, ai):
    course = run(seed_course(db_a))
    source = run(seed_course_source(supabase, db_a, course["id"]))
    process(db_a, ai, source["id"])
    assert api.get(f"/api/documents/{source['id']}/knowledge", headers=auth("token-b")).status_code == 404
    assert api.post(f"/api/documents/{source['id']}/knowledge", headers=auth("token-b")).status_code == 404


def test_profiles_are_built_only_for_course_material(api, supabase, db_a, ai):
    assessment = run(seed_assessment(db_a))
    exam = run(seed_document(supabase, db_a, assessment["id"], category="previous_exam"))
    process(db_a, ai, exam["id"])
    assert knowledge_of(db_a, exam["id"]) is None
    assert api.post(f"/api/documents/{exam['id']}/knowledge", headers=auth()).status_code == 409


# -- Use in assessments ------------------------------------------------------------------------


def test_the_inventory_lists_every_lecture_with_topic_ids_and_flags_lectures_without_a_profile(supabase, db_a, ai):
    course = run(seed_course(db_a))
    first = run(seed_course_source(supabase, db_a, course["id"], name="Lecture 1.pdf"))
    process(db_a, ai, first["id"])
    second = run(
        seed_course_source(
            supabase, db_a, course["id"], name="Lecture 2.pdf", data=pdf_bytes(["Decision trees split data by information gain."])
        )
    )
    ai.on("analyze_lecture_segment", lambda _call: AIServiceError("down"))
    process(db_a, ai, second["id"])
    context = AssessmentContextService(db_a)
    documents = [run(context.get_document(first["id"])), run(context.get_document(second["id"]))]
    text = render_inventory(documents, run(context.get_knowledge(documents)), DataFramer())
    assert "[L1.t1]" in text and " R Ap An" in text
    assert 'No profile yet for: "Lecture 2.pdf"' in text


def test_the_compact_inventory_lists_every_topic_of_a_large_course_and_counts_what_it_abbreviates():
    from app.services.assessment_context import DocumentRecord, render_material_overview

    docs, knowledge = [], {}
    for n in range(1, 7):
        doc = DocumentRecord.from_row(
            {
                "id": f"d{n}", "exam_project_id": None, "course_id": "c", "category": "course_material",
                "original_name": f"Lecture {n}.pdf", "mime_type": None, "size_bytes": 1, "storage_path": "x",
                "processing_status": "ready", "processing_error": None, "processed_at": None,
                "content_sha256": f"h{n}", "page_count": 60, "extracted_characters": 30000,
                "summary": {"title": f"Lecture {n}", "summary": "s", "topics": [{"name": "Repeated topic list"}]},
                "created_at": "2026",
            }
        )  # fmt: skip
        docs.append(doc)
        topics = [
            {
                "id": f"t{i}", "name": f"Topic {n}.{i}", "summary": "long " * 80,
                "locations": [f"Slide {i}", f"Slide {i + 1}"], "formulas": [{}, {}], "examples": [],
                "visuals": [], "bloom_opportunities": [{"level": "apply", "idea": "x"}],
            }
            for i in range(1, 41)
        ]  # fmt: skip
        knowledge[doc.id] = {
            "version": lecture_knowledge.KNOWLEDGE_VERSION, "status": "ready", "content_sha256": f"h{n}",
            "title": f"Lecture {n}", "overview": "o", "sections": [], "topics": topics,
            "relations": [{"source_topic": "a", "target_topic": "b", "kind": "builds_on"}] * 20, "warnings": [],
        }  # fmt: skip
    framer = DataFramer()
    text = render_inventory(docs, knowledge, framer)
    # Every topic of every lecture is there (nothing truncated), compactly; summaries live in the notes.
    assert all(
        f"[L{n}.t{i}] Topic {n}.{i} (Slides {i}–{i + 1}) Ap (2 formulas)" in text for n in range(1, 7) for i in range(1, 41)
    )
    assert "long long" not in text and "(+8 more)" in text
    assert len(text) // 4 < 8_000  # 240 topics in well under 8k tokens
    # The file overview doesn't repeat what the map already says.
    overview = render_material_overview(docs, framer, mapped={doc.id for doc in docs})
    assert "Repeated topic list" not in overview and "described in LECTURE KNOWLEDGE below" in overview


def test_compact_locations_merges_ranges():
    from app.services.lecture_knowledge import compact_locations

    assert compact_locations(["Slide 3", "Slides 4–5", "Slide 9", "Section: Intro"]) == "Slides 3–5, 9; Section: Intro"
    assert compact_locations(["Page 2"]) == "Page 2"


def test_slides_with_pictures_are_reported_in_the_coverage():
    from app.services.lecture_knowledge import coverage_of, coverage_warnings

    chunks = [
        {"content": "Linear classifier\n[Figure on slide 2: 1 picture(s) or chart(s) whose content is not in the extracted text]",
         "location_label": "Slides 1–2"},
        {"content": "Margins", "location_label": "Slide 3"},
    ]  # fmt: skip
    coverage = coverage_of(chunks, page_count=3, extracted_characters=100, segments=1)
    assert coverage["units_with_figures"] == [2] and coverage["units_without_text"] == []
    # Chunks without page markers (files read earlier) can't be classified page by page, and say so.
    assert coverage["page_detail"] is False and coverage["substantive_units"] is None
    warnings = coverage_warnings(coverage)
    assert any("Slide 2 also has figures or images that aren't in the text" in warning for warning in warnings)
    assert any("Page-level coverage isn't available" in warning for warning in warnings)


def test_legacy_profiles_stay_current_and_show_their_coverage_honestly(supabase, db_a, ai):
    course = run(seed_course(db_a))
    source = run(seed_course_source(supabase, db_a, course["id"]))
    record = process(db_a, ai, source["id"])
    knowledge = knowledge_of(db_a, source["id"])
    # A profile saved before page-level coverage existed: no page classes in its coverage.
    legacy = {
        **knowledge,
        "coverage": {
            key: value for key, value in knowledge["coverage"].items() if key not in ("substantive_units", "page_detail", "pages")
        },
    }
    assert lecture_knowledge.is_current(legacy, record.content_sha256)
    shown = lecture_knowledge.overview(legacy, record)["coverage"]
    assert shown["pageDetail"] is False and shown["substantiveUnits"] is None and shown["pages"] == {}


def test_plans_may_only_cite_listed_topic_ids():
    plan = ExamPlan.model_validate(well_behaved_plan({"input": ""}).model_dump())
    plan.questions[0].knowledge_refs = ["L1.t1"]
    plan.questions[1].knowledge_refs = ["L9.t9"]
    from app.schemas.ai import ExamSpec

    spec = ExamSpec.model_validate(
        {
            "assessment_title": None, "course": None, "assessment_type": None, "duration_minutes": None,
            "duration_source": None, "versions": 1, "total_points": None, "total_points_source": None,
            "question_count_min": None, "question_count_max": None, "question_count_source": None,
            "question_format": None, "difficulty": None, "sections": [], "coverage": [], "learning_emphasis": [],
            "question_style": [], "professor_notes": [], "professor_preferences_applied": [],
            "source_material_guidance": None, "previous_exam_style_guidance": None, "constraints": [],
            "assumptions": [], "warnings": [], "generation_instructions": [],
        }
    )  # fmt: skip
    hard, _ = plan_problems(plan, spec, set(), {"L1.t1"})
    assert any("L9.t9" in problem for problem in hard) and not any("L1.t1" in problem for problem in hard)


def test_generation_receives_the_cited_lecture_knowledge(api, supabase, db_a, ai):
    def plan_with_refs(call):
        plan = well_behaved_plan(call)
        refs = re.findall(r"\[(L\d+\.t\d+)\]", call["input"])
        assert refs, "the planner must see the inventory"
        for question in plan.questions:
            question.knowledge_refs = [refs[0]]
        return plan

    ai.on("plan_exam", plan_with_refs)
    ai.on("replan_exam", plan_with_refs)
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    assert "LECTURE KNOWLEDGE" in ai.calls_for("interpret_assessment")[0]["input"]
    generate(api, assessment["id"])
    batch = ai.calls_for("generate_questions")[0]["input"]
    assert "LECTURE KNOWLEDGE FOR THESE QUESTIONS" in batch and "lecture knowledge: L1.t1" in batch
    # The professor's Bloom's split stays with the assessment: the profile has no percentages.
    profile = knowledge_of(db_a, run(AssessmentContextService(db_a).list_documents(assessment["id"]))[0].id)
    assert "bloom" not in profile and "remember_percent" not in str(profile)


@pytest.mark.parametrize(("count_max", "flagged"), [(3, True), (6, False)])
def test_more_bloom_levels_than_questions_is_a_conflict(count_max, flagged):
    class Spec:
        question_format = None
        question_count_max = count_max
        bloom = BloomSpec(
            focus="balanced", remember_percent=15, understand_percent=20, apply_percent=25,
            analyze_percent=20, evaluate_percent=10, create_percent=10, source="professor",
        )  # fmt: skip

    conflicts = bloom_conflicts(Spec())  # type: ignore[arg-type]
    assert bool(conflicts) is flagged
    if flagged:
        assert "6 levels" in conflicts[0] and "at most 3 question(s)" in conflicts[0]


def test_segment_schema_has_no_place_for_an_assessment_split():
    assert not any("percent" in name for name in SegmentKnowledge.model_json_schema()["$defs"]["KnowledgeTopic"]["properties"])
