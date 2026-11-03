"""Assessment quality: the gaps found in the first real evaluation (an 85/100 Logistic Regression quiz).

The reviewer sees every rubric and the attached sources; excerpt ids never leak between requests or
into question text; unknown ids are sent back, not dropped; every planned question keeps the exact
pages of its lecture topics; subpart tasks appear once; the writing rules target understanding,
overlap and distractors.
"""

import asyncio
import re

from app.ai.prompts import DataFramer, generator_instructions, planner_instructions, reviewer_instructions
from app.services.document_retrieval import Excerpt
from app.services.exam_generation import excerpt_id_problems
from app.services.exam_store import question_problems, render_question, source_labels
from tests.helpers import auth
from tests.model import make_content, subpart, well_behaved_batch, well_behaved_plan, well_behaved_revision
from tests.seed import seed_assessment, seed_document
from tests.test_workflow import A_SETTINGS, generate, interpret_and_approve


def run(coroutine):
    return asyncio.run(coroutine)


def excerpt(ref: str, location: str = "Pages 15–21") -> Excerpt:
    return Excerpt(
        chunk_id=f"c-{ref}", document_id="d1", document_name="LR.pdf", location=location, content="x", similarity=1, ref=ref
    )


def plan_on_first_topic(call):
    plan = well_behaved_plan(call)
    refs = re.findall(r"\[(L\d+\.t\d+)\]", call["input"])
    for question in plan.questions:
        question.knowledge_refs = refs[:1]
    return plan


def generated_exam(api, supabase, db_a, ai, **handlers):
    ai.on("plan_exam", plan_on_first_topic)
    ai.on("replan_exam", plan_on_first_topic)
    for purpose, handler in handlers.items():
        ai.on(purpose, handler)
    assessment = run(seed_assessment(db_a, **{**A_SETTINGS, "number_of_versions": 1}))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    return generate(api, assessment["id"])


# -- The reviewer sees the whole marking scheme ----------------------------------------------------


def test_the_reviewer_sees_every_subpart_rubric_and_the_attached_sources():
    content = make_content(4, "problem", "easy", 6)
    content.subparts = [subpart("a", 2), subpart("b", 2), subpart("c", 2)]
    row = {
        **content.model_dump(),
        "source_refs": [
            {"document_id": "d1", "label": "LR.pdf, Pages 15, 19", "topic": "Class prediction"},
            {"document_id": "d1", "chunk_id": "c1", "label": "LR.pdf, Pages 15–21"},
        ],
    }
    text = render_question(row, 4)
    assert text.count("     Rubric: ") == 3  # one per subpart (they were missing, so the review asked for rubrics)
    assert "Sources (attached by ProfPilot): LR.pdf, Pages 15, 19" in text
    # Students' copies never include the key or the sources.
    assert "Rubric" not in render_question(row, 4, include_key=False) and "Sources" not in render_question(
        row, 4, include_key=False
    )


# -- Source references: stable, exact, never silently dropped ---------------------------------------


def test_an_excerpt_id_this_request_didnt_give_is_sent_back_not_dropped():
    content = make_content(5, "problem", "medium", 6, refs=["S6"])
    problems = excerpt_id_problems([("Question 5", content)], [excerpt("S1"), excerpt("S2")])
    assert problems == [
        "Question 5 cites S6 in source_excerpt_ids, but this request's excerpts are S1, S2. "
        "Cite only the excerpts given here that you used (or none)."
    ]
    assert excerpt_id_problems([("Question 5", make_content(5, "problem", "medium", 6, refs=["S2"]))], [excerpt("S2")]) == []


def test_generation_repairs_unknown_ids_and_keeps_the_exact_pages_of_the_planned_topics(api, supabase, db_a, ai):
    def cites_an_id_from_the_brief_then_corrects(call):
        good = well_behaved_batch(call)
        bad = good.model_copy(deep=True)
        bad.questions[0].source_excerpt_ids = ["S99"]
        return [bad, good]

    exam = generated_exam(api, supabase, db_a, ai, generate_questions=cites_an_id_from_the_brief_then_corrects)
    call = ai.calls_for("generate_questions")[0]
    assert "cites S99 in source_excerpt_ids" in " ".join(call["problems"][0]) and call["problems"][1] == []
    # Every question carries its planned topic's exact pages, whatever its excerpts were.
    topic = re.search(r"\[L1\.t1\] [^(]+\(([^)]+)\)", ai.calls_for("plan_exam")[0]["input"]).group(1)
    assert all(f"Lecture 1.pdf, {topic}" in question["sources"] for question in exam["questions"])
    # …and its planned slide, first.
    assert all(re.fullmatch(r"Lecture 1\.pdf, Page \d+", question["sources"][0]) for question in exam["questions"])


def test_a_revision_keeps_the_exact_pages_and_may_not_cite_unknown_excerpts(api, supabase, db_a, ai):
    exam = generated_exam(api, supabase, db_a, ai)
    question = exam["questions"][0]

    def cites_unknown_then_corrects(call):
        good = well_behaved_revision(call)
        bad = good.model_copy(deep=True)
        bad.question.source_excerpt_ids = ["S42"]
        return [bad, good]

    ai.on("revise_question", cites_unknown_then_corrects)
    revised = api.post(f"/api/questions/{question['id']}/revision", json={"preset": "clearer"}, headers=auth()).json()
    assert "cites S42" in " ".join(ai.calls_for("revise_question")[0]["problems"][0])
    assert revised["question"]["sources"][0] == question["sources"][0]


def test_answer_keys_show_exact_pages_first_and_older_questions_keep_their_excerpt_labels():
    exact = {"document_id": "d1", "label": "LR.pdf, Page 38", "topic": "Accuracy and confusion-matrix evaluation"}
    chunk = {"document_id": "d1", "chunk_id": "c9", "label": "LR.pdf, Pages 33–38"}
    other = {"document_id": "d2", "chunk_id": "c2", "label": "Notes.pdf, Page 4"}
    assert source_labels({"source_refs": [exact, chunk, other]}) == ["LR.pdf, Page 38", "Notes.pdf, Page 4"]
    assert source_labels({"source_refs": [chunk]}) == ["LR.pdf, Pages 33–38"]  # questions saved before this change


def test_internal_ids_in_question_text_are_sent_back_but_ordinary_notation_is_not():
    content = make_content(1, "mcq", "easy", 2)
    content.solution = "Recall the definition and select B.\n\nSource: Logistic Regression.pdf, page 6; L1.t1; S4."
    assert any("writes internal ids" in problem for problem in question_problems(content))
    content.solution = "The excerpt on page 6 supports B (see S4)."
    assert any("writes internal ids" in problem for problem in question_problems(content))
    # A state named S1 in an automaton, or a page citation, is ordinary text.
    content.solution = "From state S1 the machine reads 0 and moves to S2 (page 6)."
    assert not any("internal ids" in problem for problem in question_problems(content))
    # A professor's own edit is theirs to write.
    content.solution = "Source: page 6; S4."
    assert not any("internal ids" in problem for problem in question_problems(content, manual=True))


# -- Subpart tasks appear once (the Q4 duplication) -------------------------------------------------


def test_a_main_prompt_that_restates_its_subparts_is_sent_back():
    content = make_content(4, "problem", "easy", 6)
    content.subparts = [subpart("a", 2), subpart("b", 2), subpart("c", 2)]
    for part, task in zip(
        content.subparts,
        ("Compute the score for w = 2, x = 3 and b = -7.", "Classify f = 0.5.", "Compute the loss."),
        strict=True,
    ):
        part.prompt = task
    shared = "Use the linear score \\(z = wx + b\\). Predict class 1 when \\(z \\geq 0\\)."
    content.prompt = shared
    assert not any("repeats its subpart" in problem for problem in question_problems(content))

    content.prompt = f"{shared}\n\n(a) A model has w = 2, x = 3 and b = -7. Compute its score. (2 marks)\n\n(b) Classify f = 0.5."
    problems = [p for p in question_problems(content) if "repeats its subpart" in p]
    assert problems and "(a, b)" in problems[0]
    # The same task written into the prompt without a label is caught too.
    content.subparts[0].prompt = "Compute the linear score for w = 2, x = 3 and b = -7, then state the predicted class."
    content.prompt = f"{shared} Compute the linear score for w = 2, x = 3 and b = -7, then state the predicted class."
    assert any("repeats its subpart" in problem for problem in question_problems(content))
    assert not any("repeats its subpart" in problem for problem in question_problems(content, manual=True))


# -- Writing rules for understanding, overlap, distractors and provenance --------------------------


def test_the_writing_rules_target_the_weaknesses_found_in_the_evaluation():
    writer = generator_instructions(DataFramer())
    for rule in (
        "Never list in the stem the points a full answer must contain",  # Q3 read as a checklist
        "never because of length",  # hard means harder thinking
        "Don't assess the same fact, case or calculation twice",  # Q3 and Q4(c) overlapped
        "never a different kind of thing that is obviously wrong",  # Q1's weak distractors
        "each task appears once, in its own subpart's prompt",  # Q4 was printed twice
        "ids mentioned in the brief or the plan belong to other requests",  # Q5 lost its source
        "Never write excerpt ids, topic ids or 'Source:' lines",  # 'S4' in the answer keys
    ):
        assert rule in writer, rule
    assert "never excerpt ids (S1, S2…)" in planner_instructions(DataFramer())
    reviewer = reviewer_instructions(DataFramer())
    assert "marked by its subparts' rubrics" in reviewer and "don't report a missing source reference" in reviewer
    # The shared grounding rule reaches the interpreter, planner, writer and reviewer alike.
    assert "excerpt ids (S1, S2…) are numbered for one request only" in reviewer and "numbered for one request only" in writer
